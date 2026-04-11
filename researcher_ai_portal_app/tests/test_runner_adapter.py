from __future__ import annotations

import importlib.metadata
import sys
import time
import types
from contextlib import nullcontext
from datetime import timedelta

import pytest
from django.contrib.auth import get_user_model
from django.test import RequestFactory
from django.utils import timezone
from pydantic import BaseModel

from researcher_ai_portal_app import views
from researcher_ai_portal_app.api import routes as api_routes
from researcher_ai_portal_app.job_store import create_job, get_job
from researcher_ai_portal_app.models import WorkflowJob


def test_runner_mode_defaults_to_orchestrator(monkeypatch):
    monkeypatch.delenv("RESEARCHER_AI_PORTAL_RUNNER_MODE", raising=False)
    assert views._runner_mode() == "orchestrator"


def test_runner_mode_invalid_falls_back_to_legacy(monkeypatch):
    monkeypatch.setenv("RESEARCHER_AI_PORTAL_RUNNER_MODE", "invalid")
    assert views._runner_mode() == "legacy"


def test_version_drift_logs_warning(monkeypatch):
    captured: list[str] = []

    def _capture(job_id: str, message: str, *, step: str = "", level: str = "info") -> None:
        captured.append(f"{level}:{message}")

    monkeypatch.setenv("RESEARCHER_AI_EXPECTED_VERSION", "9.9.9")
    monkeypatch.setattr(views, "_log_job_event", _capture)
    views._report_version_drift("job1", "3.0.0")
    assert any("version drift detected" in item for item in captured)


def test_version_drift_logs_info_when_check_disabled(monkeypatch):
    captured: list[str] = []

    def _capture(job_id: str, message: str, *, step: str = "", level: str = "info") -> None:
        captured.append(f"{level}:{message}")

    monkeypatch.delenv("RESEARCHER_AI_EXPECTED_VERSION", raising=False)
    monkeypatch.setattr(views, "_log_job_event", _capture)
    views._report_version_drift("job1", "3.0.0")
    assert any("drift check disabled" in item for item in captured)


def test_runtime_researcher_ai_version_prefers_distribution_metadata(monkeypatch):
    monkeypatch.setitem(sys.modules, "researcher_ai", types.SimpleNamespace(__version__="2.2.3"))
    monkeypatch.setattr(importlib.metadata, "version", lambda name: "2.3.0")
    assert views._runtime_researcher_ai_version() == "2.3.0"


def test_runtime_researcher_ai_version_falls_back_to_module_version(monkeypatch):
    monkeypatch.setitem(sys.modules, "researcher_ai", types.SimpleNamespace(__version__="2.2.3"))

    def _raise_not_found(name: str) -> str:
        raise importlib.metadata.PackageNotFoundError

    monkeypatch.setattr(importlib.metadata, "version", _raise_not_found)
    assert views._runtime_researcher_ai_version() == "2.2.3"


def test_validate_component_json_datasets_preserves_subtype_fields():
    class _DatasetModel(BaseModel):
        accession: str
        source: str

    payload = [
        {
            "accession": "GSE276986",
            "source": "geo",
            "pride_accession": "PXD055825",
            "custom_dataset_note": "retained",
        }
    ]
    result = views._validate_component_json("datasets", payload, mods={"Dataset": _DatasetModel})
    assert result[0]["accession"] == "GSE276986"
    assert result[0]["source"] == "geo"
    assert result[0]["pride_accession"] == "PXD055825"
    assert result[0]["custom_dataset_note"] == "retained"


def test_orchestrator_metadata_compaction_limits_strings_lists_and_depth():
    deep = {"a": {"b": {"c": {"d": {"e": {"f": {"g": "too deep"}}}}}}}
    state = {
        "dataset_parse_errors": ["x" * 5000] + [f"e{i}" for i in range(150)],
        "workflow_graph_validation_issues": [deep],
        "progress": 70,
        "stage": "parsed_datasets",
    }
    compacted = views._extract_orchestrator_diagnostics(state)
    errors = compacted["dataset_parse_errors"]
    assert len(errors) == 101
    assert errors[-1] == "...truncated"
    assert str(errors[0]).endswith("...truncated")
    nested = compacted["workflow_graph_validation_issues"][0]
    assert nested["a"]["b"]["c"]["d"]["e"] == "...truncated"


def test_normalize_orchestrator_components_rejects_bad_pipeline_shape(monkeypatch):
    monkeypatch.setattr(views, "_validate_component_json", lambda step, payload, mods: payload)
    state = {
        "method": {"assay_graph": {"assays": [], "dependencies": []}},
        "pipeline": {"config": {"steps": "not-a-list"}},
    }
    with pytest.raises(views._RunnerContractError):
        views._normalize_orchestrator_components(state, mods={})


def test_orchestrator_status_maps_to_human_review_metadata():
    method_payload = {
        "parse_warnings": [
            "paper_rag_vision_fallback: count=1 latency_seconds=2.0",
            "bioworkflow_blocked: ungrounded_fields=2 mode=on",
        ]
    }
    status, metadata = views._orchestrator_status_and_metadata(
        {"human_review_required": True, "human_review_summary": {"ungrounded_count": 2}},
        method_payload,
    )
    assert status == "needs_human_review"
    assert metadata["human_review_required"] is True
    assert metadata["human_review_summary"]["ungrounded_count"] == 2
    assert metadata["vision_fallback_count"] == 1


def test_run_all_steps_async_routes_to_orchestrator_runner(monkeypatch, db):
    user = get_user_model().objects.create_user("runner_mode_user", password="pw")
    job_id = create_job(
        input_type="pmid",
        input_value="123",
        source="123",
        source_type="pmid",
        llm_model="gpt-5.4",
        user=user,
        status="in_progress",
        stage="Queued",
    )
    called: dict[str, bool] = {"orchestrator": False}

    def _fake_orchestrator_run(
        job_id: str,
        *,
        llm_api_key: str,
        llm_model: str,
        hard_timeout_seconds: float | None = None,
    ) -> None:
        called["orchestrator"] = True
        views.update_job(
            job_id,
            status="completed",
            stage="All steps complete — ready for review",
            progress=100,
            current_step="pipeline",
        )

    monkeypatch.setattr(views, "_runner_mode", lambda: "orchestrator")
    monkeypatch.setattr(views, "_runtime_researcher_ai_version", lambda: "3.0.0")
    monkeypatch.setattr(views, "_report_version_drift", lambda job_id, version: None)
    monkeypatch.setattr(views, "_run_orchestrator_job", _fake_orchestrator_run)
    monkeypatch.setattr(views, "_runner_soft_timeout_seconds", lambda mode: 9999.0)
    monkeypatch.setattr(views, "_runner_timeout_seconds", lambda mode: 9999.0)

    views._run_all_steps_async(job_id, llm_api_key="sk-12345678901234567890", llm_model="gpt-5.4")
    assert called["orchestrator"] is True
    job = get_job(job_id, user=user)
    assert job is not None
    assert job.get("status") == "completed"


def test_run_orchestrator_job_updates_current_step_across_nodes(monkeypatch, db):
    user = get_user_model().objects.create_user("runner_stage_updates_user", password="pw")
    job_id = create_job(
        input_type="pmid",
        input_value="123",
        source="123",
        source_type="pmid",
        llm_model="gpt-5.4",
        user=user,
        status="in_progress",
        stage="Queued",
        current_step="paper",
        progress=0,
    )

    captured_steps: list[str] = []
    original_update_job = views.update_job

    def _spy_update_job(job_id: str, user=None, **fields):
        step = fields.get("current_step")
        if step is not None:
            captured_steps.append(str(step))
        return original_update_job(job_id, user=user, **fields)

    monkeypatch.setattr(views, "update_job", _spy_update_job)
    monkeypatch.setattr(views, "_llm_env", lambda job: nullcontext())
    monkeypatch.setattr(views, "_orchestrator_heartbeat_seconds", lambda: 0.01)
    monkeypatch.setattr(views, "_import_runtime_modules", lambda: {})
    monkeypatch.setattr(
        views,
        "_normalize_orchestrator_components",
        lambda state, mods: {
            "paper": {"title": "t"},
            "figures": [],
            "method": {"assay_graph": {"assays": [], "dependencies": []}},
            "datasets": [],
            "software": [],
            "pipeline": {"config": {"steps": []}, "validation_report": {"passed": True}},
        },
    )

    fake_root = types.ModuleType("researcher_ai")
    fake_models = types.ModuleType("researcher_ai.models")
    fake_paper = types.ModuleType("researcher_ai.models.paper")
    fake_pipeline = types.ModuleType("researcher_ai.pipeline")
    fake_orch = types.ModuleType("researcher_ai.pipeline.orchestrator")

    class _PaperSource:
        PMID = "pmid"
        PDF = "pdf"
        PMCID = "pmcid"
        DOI = "doi"
        URL = "url"

    class _FakeOrchestrator:
        max_build_attempts = 2
        bioworkflow_mode = "warn"

        def _node_parse_paper(self, state):
            return {"paper": {"title": "t"}, "progress": 15, "stage": "parsed_paper"}

        def _node_parse_figures(self, state):
            return {"figures": [], "progress": 35, "stage": "parsed_figures"}

        def _node_parse_methods(self, state):
            return {"method": {"assay_graph": {"assays": [], "dependencies": []}}, "progress": 55, "stage": "parsed_methods"}

        def _node_parse_datasets(self, state):
            return {"datasets": [], "progress": 70, "stage": "parsed_datasets"}

        def _node_parse_software(self, state):
            return {"software": [], "progress": 80, "stage": "parsed_software"}

        def _node_build_workflow_graph(self, state):
            return {"workflow_graph": {}, "progress": 86, "stage": "built_workflow_graph"}

        def _node_validate_method(self, state):
            return {"progress": 90, "stage": "validated_method"}

        def _node_build_pipeline(self, state):
            return {
                "pipeline": {"config": {"steps": []}, "validation_report": {"passed": True}},
                "build_attempts": 1,
                "progress": 100,
                "stage": "completed",
            }

        def _next_after_build_pipeline(self, state):
            return "end"

    fake_paper.PaperSource = _PaperSource
    fake_orch.WorkflowOrchestrator = _FakeOrchestrator

    monkeypatch.setitem(sys.modules, "researcher_ai", fake_root)
    monkeypatch.setitem(sys.modules, "researcher_ai.models", fake_models)
    monkeypatch.setitem(sys.modules, "researcher_ai.models.paper", fake_paper)
    monkeypatch.setitem(sys.modules, "researcher_ai.pipeline", fake_pipeline)
    monkeypatch.setitem(sys.modules, "researcher_ai.pipeline.orchestrator", fake_orch)

    views._run_orchestrator_job(
        job_id,
        llm_api_key="sk-12345678901234567890",
        llm_model="gpt-5.4",
        hard_timeout_seconds=30.0,
    )

    assert "paper" in captured_steps
    assert "figures" in captured_steps
    assert captured_steps.index("figures") > captured_steps.index("paper")
    job = get_job(job_id, user=user)
    assert job is not None
    assert job.get("status") == "completed"
    assert job.get("current_step") == "pipeline"


def test_api_run_full_pipeline_sync_completes_job(monkeypatch, db):
    user = get_user_model().objects.create_user("api_runner_user", password="pw")
    job_id = create_job(
        input_type="pmid",
        input_value="123",
        source="123",
        source_type="pmid",
        llm_model="gpt-5.4",
        user=user,
        status="queued",
        stage="Queued",
        progress=0,
        current_step="paper",
    )
    called_steps: list[str] = []

    monkeypatch.setattr(views, "STEP_ORDER", ["paper"])
    monkeypatch.setattr(views, "STEP_LABELS", {"paper": "Paper Parser"})
    monkeypatch.setattr(
        views,
        "_dispatch_workflow_step",
        lambda job_id, step, *, llm_api_key, llm_model, force_reparse: called_steps.append(step),
    )
    monkeypatch.setattr(api_routes, "_save_graph_after_completion", lambda job_id: None)

    api_routes._run_full_pipeline_sync(job_id, "sk-test-1234567890", "gpt-5.4", False)

    job = get_job(job_id, user=user)
    assert called_steps == ["paper"]
    assert job is not None
    assert job.get("status") == "completed"
    assert job.get("progress") == 100


def test_runner_timeout_marks_job_failed(monkeypatch, db):
    user = get_user_model().objects.create_user("runner_timeout_user", password="pw")
    job_id = create_job(
        input_type="pmid",
        input_value="123",
        source="123",
        source_type="pmid",
        llm_model="gpt-5.4",
        user=user,
        status="in_progress",
        stage="Queued",
    )

    def _slow_orchestrator_run(
        job_id: str,
        *,
        llm_api_key: str,
        llm_model: str,
        hard_timeout_seconds: float | None = None,
    ) -> None:
        time.sleep(0.02)
        views.update_job(job_id, status="completed", stage="Done", progress=100, current_step="pipeline")

    monkeypatch.setattr(views, "_runner_mode", lambda: "orchestrator")
    monkeypatch.setattr(views, "_runtime_researcher_ai_version", lambda: "3.0.0")
    monkeypatch.setattr(views, "_report_version_drift", lambda job_id, version: None)
    monkeypatch.setattr(views, "_run_orchestrator_job", _slow_orchestrator_run)
    monkeypatch.setattr(views, "_runner_soft_timeout_seconds", lambda mode: 0.001)
    monkeypatch.setattr(views, "_runner_timeout_seconds", lambda mode: 0.001)

    views._run_all_steps_async(job_id, llm_api_key="sk-12345678901234567890", llm_model="gpt-5.4")
    job = get_job(job_id, user=user)
    assert job is not None
    assert job.get("status") == "failed"
    assert "timed out" in str(job.get("stage") or "").lower()


def test_run_with_timeout_raises_timeout_error():
    def _slow() -> str:
        time.sleep(1.2)
        return "done"

    with pytest.raises(TimeoutError):
        views._run_with_timeout(_slow, timeout_seconds=0.01, label="unit-test")


def test_run_all_steps_passes_hard_timeout_to_orchestrator(monkeypatch, db):
    user = get_user_model().objects.create_user("runner_timeout_pass_user", password="pw")
    job_id = create_job(
        input_type="pmid",
        input_value="123",
        source="123",
        source_type="pmid",
        llm_model="gpt-5.4",
        user=user,
        status="in_progress",
        stage="Queued",
    )
    captured: dict[str, float | None] = {"hard_timeout_seconds": None}

    def _capture_timeout(
        job_id: str,
        *,
        llm_api_key: str,
        llm_model: str,
        hard_timeout_seconds: float | None = None,
    ) -> None:
        captured["hard_timeout_seconds"] = hard_timeout_seconds
        views.update_job(job_id, status="completed", stage="Done", progress=100, current_step="pipeline")

    monkeypatch.setattr(views, "_runner_mode", lambda: "orchestrator")
    monkeypatch.setattr(views, "_runtime_researcher_ai_version", lambda: "3.0.0")
    monkeypatch.setattr(views, "_report_version_drift", lambda job_id, version: None)
    monkeypatch.setattr(views, "_run_orchestrator_job", _capture_timeout)
    monkeypatch.setattr(views, "_runner_soft_timeout_seconds", lambda mode: 9999.0)
    monkeypatch.setattr(views, "_runner_timeout_seconds", lambda mode: 123.0)

    views._run_all_steps_async(job_id, llm_api_key="sk-12345678901234567890", llm_model="gpt-5.4")
    assert captured["hard_timeout_seconds"] == 123.0


def test_job_status_and_dashboard_context_accept_orchestrator_compatible_payload(monkeypatch, db):
    user = get_user_model().objects.create_user("runner_payload_user", password="pw")
    job_id = create_job(
        input_type="pmid",
        input_value="123",
        source="123",
        source_type="pmid",
        llm_model="gpt-5.4",
        user=user,
        status="needs_human_review",
        stage="needs_human_review",
        current_step="pipeline",
        progress=100,
        components={
            "paper": {"title": "Test", "paper_type": "computational"},
            "figures": [],
            "method": {
                "assay_graph": {"assays": [], "dependencies": []},
                "parse_warnings": ["bioworkflow_blocked: ungrounded_fields=1 mode=on"],
            },
            "datasets": [],
            "software": [],
            "pipeline": {"config": {"steps": []}},
        },
        component_meta={
            "paper": {"status": "found", "missing": [], "source": "parsed_orchestrator"},
            "figures": {"status": "found", "missing": [], "source": "parsed_orchestrator"},
            "method": {"status": "inferred", "missing": [], "source": "parsed_orchestrator"},
            "datasets": {"status": "found", "missing": [], "source": "parsed_orchestrator"},
            "software": {"status": "found", "missing": [], "source": "parsed_orchestrator"},
            "pipeline": {"status": "found", "missing": [], "source": "parsed_orchestrator"},
        },
        job_metadata={
            "human_review_required": True,
            "human_review_summary": {"ungrounded_count": 1},
        },
    )

    monkeypatch.setattr(views, "compute_confidence", lambda result: {"overall": 80.0, "assay_confidences": {}})
    monkeypatch.setattr(views, "compute_actionable_items", lambda result, confidence: [])

    request = RequestFactory().get("/status")
    request.user = user
    response = views.job_status(request, job_id)
    assert response.status_code == 200

    job = get_job(job_id, user=user)
    assert job is not None
    ctx = views._dashboard_context(job)
    assert ctx["review_required"] is True
    assert ctx["review_summary"] == {"ungrounded_count": 1}


def test_job_status_marks_stale_in_progress_job_as_failed(monkeypatch, db):
    user = get_user_model().objects.create_user("runner_stale_user", password="pw")
    job_id = create_job(
        input_type="pmid",
        input_value="123",
        source="123",
        source_type="pmid",
        llm_model="gpt-5.4",
        user=user,
        status="in_progress",
        stage="Running WorkflowOrchestrator",
        current_step="paper",
        progress=0,
    )
    stale_at = timezone.now() - timedelta(seconds=1200)
    WorkflowJob.objects.filter(id=job_id).update(updated_at=stale_at)
    monkeypatch.setenv("RESEARCHER_AI_PORTAL_STUCK_JOB_TIMEOUT_SECONDS", "60")

    request = RequestFactory().get("/status")
    request.user = user
    response = views.job_status(request, job_id)
    assert response.status_code == 200

    job = get_job(job_id, user=user)
    assert job is not None
    assert job.get("status") == "failed"
    assert "stalled" in str(job.get("stage") or "").lower()
    assert "retry" in str(job.get("error") or "").lower()
