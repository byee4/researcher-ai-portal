from __future__ import annotations

import time

import pytest
from django.contrib.auth import get_user_model
from django.test import RequestFactory

from researcher_ai_portal_app import views
from researcher_ai_portal_app.job_store import create_job, get_job


def test_runner_mode_defaults_to_legacy(monkeypatch):
    monkeypatch.delenv("RESEARCHER_AI_PORTAL_RUNNER_MODE", raising=False)
    assert views._runner_mode() == "legacy"


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

    def _fake_orchestrator_run(job_id: str, *, llm_api_key: str, llm_model: str) -> None:
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

    def _slow_orchestrator_run(job_id: str, *, llm_api_key: str, llm_model: str) -> None:
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
