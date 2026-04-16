from __future__ import annotations

import pytest
from asgiref.sync import async_to_sync
from django.contrib.auth import get_user_model
from django.http import Http404, HttpResponse
from django.test import RequestFactory
from researcher_ai.models.paper import Paper, PaperSource

from researcher_ai_portal_app import views
from researcher_ai_portal_app.api import repository
from researcher_ai_portal_app.api.schemas import RagWorkflowResponse
from researcher_ai_portal_app.job_store import create_job, get_job


class _StubMethod:
    def __init__(self):
        self.assay_graph = type("AssayGraph", (), {"assays": [{"name": "A1"}]})()

    def model_dump(self, mode: str = "json"):
        return {
            "assay_graph": {"assays": [{"name": "A1"}], "dependencies": []},
            "parse_warnings": [
                "retrieval_rounds=2",
                "retrieved_chunks=14",
                "context_tokens_est=1800",
            ],
        }


def test_method_step_persists_rag_workflow_metadata(monkeypatch, tmp_path, db):
    user = get_user_model().objects.create_user("rag_meta_user", password="pw")
    paper = Paper(title="RAG test", source=PaperSource.PMID, source_path="123", pmid="123")
    job_id = create_job(
        input_type="pmid",
        input_value="123",
        source="123",
        source_type="pmid",
        llm_model="gpt-5.4",
        user=user,
        components={"paper": paper.model_dump(mode="json"), "figures": []},
    )

    class _StubMethodsParser:
        def __init__(self, llm_model: str = "", rag_persist_dir: str | None = None, **kwargs):
            self.rag_persist_dir = rag_persist_dir

        def parse(self, paper, figures=None, computational_only=True):
            return _StubMethod()

    original_import_runtime_modules = views._import_runtime_modules

    def patched_import_runtime_modules():
        mods = original_import_runtime_modules()
        mods["MethodsParser"] = _StubMethodsParser
        return mods

    monkeypatch.setattr(views, "_import_runtime_modules", patched_import_runtime_modules)
    monkeypatch.setenv("RESEARCHER_AI_RAG_MODE", "per_job")
    monkeypatch.setenv("RESEARCHER_AI_RAG_BASE_DIR", str(tmp_path))

    views._run_step(job_id, "method")

    job = get_job(job_id, user=user)
    assert job is not None
    rag = (job.get("job_metadata") or {}).get("rag_workflow") or {}
    assert rag.get("mode") == "per_job"
    assert rag.get("indexing", {}).get("section_count") == 0
    assert rag.get("retrieval", {}).get("rounds") == 2
    assert rag.get("retrieval", {}).get("retrieved_chunk_count") == 14
    assert rag.get("retrieval", {}).get("total_context_tokens_est") == 1800
    assert rag.get("result", {}).get("assay_count") == 1
    assert isinstance(rag.get("events"), list)
    assert rag.get("events")[0]["phase"] == "indexing"


def test_build_rag_workflow_payload_merges_parse_logs_when_no_structured_metadata():
    payload = views.build_rag_workflow_payload(
        {
            "llm_model": "gpt-5.4",
            "components": {"method": {"assay_graph": {"assays": [], "dependencies": []}}},
            "parse_logs": [
                {
                    "ts": "2026-04-09T01:00:00Z",
                    "step": "method",
                    "level": "info",
                    "message": "Indexing 10 paper sections + 3 figure captions into RAG store",
                }
            ],
            "job_metadata": {},
        }
    )
    assert payload["has_telemetry"] is False
    assert len(payload["timeline"]) == 1
    assert payload["timeline"][0]["phase"] == "indexing"


def test_build_rag_workflow_payload_extracts_retrieval_metrics_from_natural_language_logs():
    payload = views.build_rag_workflow_payload(
        {
            "llm_model": "gpt-5.4",
            "components": {"method": {"assay_graph": {"assays": [], "dependencies": []}}},
            "parse_logs": [
                {
                    "ts": "2026-04-09T01:00:00Z",
                    "step": "method",
                    "level": "info",
                    "message": "retrieval was 3 rounds; retrieved 21 chunks; context tokens est was 2450",
                }
            ],
            "job_metadata": {},
        }
    )
    assert payload["retrieval"]["rounds"] == 3
    assert payload["retrieval"]["retrieved_chunk_count"] == 21
    assert payload["retrieval"]["total_context_tokens_est"] == 2450


def test_rag_workflow_view_is_user_scoped(monkeypatch, db):
    user = get_user_model().objects.create_user("rag_owner", password="pw")
    other = get_user_model().objects.create_user("rag_other", password="pw")
    job_id = create_job(
        input_type="pmid",
        input_value="123",
        source="123",
        source_type="pmid",
        llm_model="gpt-5.4",
        user=user,
        job_metadata={"rag_workflow": {"mode": "per_job"}},
    )

    monkeypatch.setattr(views, "render", lambda request, template, context: HttpResponse("ok"))
    request = RequestFactory().get(f"/jobs/{job_id}/rag-workflow/")
    request.user = user
    response = views.rag_workflow(request, job_id)
    assert response.status_code == 200

    request_other = RequestFactory().get(f"/jobs/{job_id}/rag-workflow/")
    request_other.user = other
    with pytest.raises(Http404):
        views.rag_workflow(request_other, job_id)


def test_repository_rag_workflow_payload_matches_schema(db):
    user = get_user_model().objects.create_user("rag_api_user", password="pw")
    job_id = create_job(
        input_type="pmid",
        input_value="123",
        source="123",
        source_type="pmid",
        llm_model="gpt-5.4",
        user=user,
        components={
            "method": {
                "assay_graph": {"assays": [{"name": "A1"}], "dependencies": []},
                "parse_warnings": [],
            }
        },
        parse_logs=[],
        job_metadata={
            "rag_workflow": {
                "mode": "per_job",
                "indexing": {"section_count": 2, "figure_caption_count": 1},
                "retrieval": {"rounds": 1},
                "generation": {"model": "gpt-5.4"},
                "result": {"assay_count": 1, "parse_warning_count": 0, "review_required": False},
                "events": [],
            }
        },
    )

    data = async_to_sync(repository.get_rag_workflow_for_user)(job_id=job_id, user_id=user.pk)
    assert data is not None
    model = RagWorkflowResponse(**data)
    assert model.job_id == str(job_id)
    assert model.mode == "per_job"
    assert model.result.assay_count == 1

    missing = async_to_sync(repository.get_rag_workflow_for_user)(job_id=job_id, user_id=user.pk + 999)
    assert missing is None


def test_templates_include_rag_navigation_links():
    dashboard_text = (
        views.DJANGO_ROOT
        / "researcher_ai_portal_app"
        / "templates"
        / "researcher_ai_portal"
        / "dashboard.html"
    ).read_text(encoding="utf-8")
    home_text = (
        views.DJANGO_ROOT
        / "researcher_ai_portal_app"
        / "templates"
        / "researcher_ai_portal"
        / "home.html"
    ).read_text(encoding="utf-8")
    assert "RAG Workflow" in dashboard_text
    assert "RAG View" in home_text
