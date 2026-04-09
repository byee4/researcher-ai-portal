from __future__ import annotations

import json
import time
from pathlib import Path

from django.contrib.auth import get_user_model
from django.test import RequestFactory

from researcher_ai_portal_app import views
from researcher_ai_portal_app.api.schemas import JobStatusResponse
from researcher_ai_portal_app.job_store import create_job
from researcher_ai_portal_app.models import WorkflowJob


def test_parse_vision_fallback_warning_tolerates_whitespace_and_newlines():
    parsed = views._parse_vision_fallback_warning(
        "paper_rag_vision_fallback:\n  count=2   latency_seconds=3.250   "
    )
    assert parsed == {
        "vision_fallback_count": 2,
        "vision_fallback_latency_seconds": 3.25,
    }


def test_parse_vision_fallback_warning_malformed_is_non_fatal():
    assert views._parse_vision_fallback_warning("paper_rag_vision_fallback: latency_seconds=abc") is None


def test_extract_method_diagnostics_derives_review_summary_when_blocked_warning_present():
    payload = {
        "parse_warnings": [
            "paper_rag_vision_fallback: count=1 latency_seconds=1.75",
            "bioworkflow_blocked: ungrounded_fields=3 mode=on",
        ]
    }

    diagnostics = views._extract_method_diagnostics(payload)

    assert diagnostics["vision_fallback_count"] == 1
    assert diagnostics["vision_fallback_latency_seconds"] == 1.75
    assert diagnostics["human_review_required"] is True
    assert diagnostics["human_review_summary"]["ungrounded_count"] == 3


def test_workflowjob_can_be_queried_by_needs_human_review_status(db):
    user = get_user_model().objects.create_user("review_state_user", password="pw")
    create_job(
        input_type="pmid",
        input_value="123",
        source="123",
        source_type="pmid",
        llm_model="gpt-5.4",
        user=user,
        status="needs_human_review",
        stage="needs_human_review",
        job_metadata={"human_review_required": True},
    )
    create_job(
        input_type="pmid",
        input_value="456",
        source="456",
        source_type="pmid",
        llm_model="gpt-5.4",
        user=user,
        status="completed",
        stage="completed",
    )

    assert WorkflowJob.objects.filter(status="needs_human_review").count() == 1


def test_job_status_endpoint_surfaces_review_and_diagnostic_metadata(db):
    user = get_user_model().objects.create_user("review_meta_user", password="pw")
    job_id = create_job(
        input_type="pmid",
        input_value="123",
        source="123",
        source_type="pmid",
        llm_model="gpt-5.4",
        user=user,
        status="needs_human_review",
        stage="needs_human_review",
        job_metadata={
            "human_review_required": True,
            "human_review_summary": {"ungrounded_count": 2},
            "vision_fallback_count": 4,
            "vision_fallback_latency_seconds": 8.9,
        },
    )

    request = RequestFactory().get("/status")
    request.user = user
    response = views.job_status(request, job_id)

    payload = json.loads(response.content.decode("utf-8"))
    assert payload["status"] == "needs_human_review"
    assert payload["review_required"] is True
    assert payload["review_summary"] == {"ungrounded_count": 2}
    assert payload["vision_fallback_count"] == 4
    assert payload["vision_fallback_latency_seconds"] == 8.9


def test_job_status_schema_remains_backward_compatible_for_legacy_payload():
    payload = {
        "job_id": "abc123",
        "status": "completed",
        "progress": 100,
        "stage": "Workflow complete",
        "current_step": "pipeline",
        "error": "",
        "parse_logs": [],
        "figure_parse_total": 0,
        "figure_parse_current": 0,
    }
    model = JobStatusResponse(**payload)
    assert model.review_required is None
    assert model.review_summary is None
    assert model.vision_fallback_count is None
    assert model.vision_fallback_latency_seconds is None


def test_job_status_schema_accepts_extended_review_fields():
    payload = {
        "job_id": "abc123",
        "status": "needs_human_review",
        "progress": 100,
        "stage": "needs_human_review",
        "current_step": "pipeline",
        "error": "",
        "parse_logs": [],
        "figure_parse_total": 0,
        "figure_parse_current": 0,
        "review_required": True,
        "review_summary": {"ungrounded_count": 3},
        "vision_fallback_count": 1,
        "vision_fallback_latency_seconds": 2.2,
    }
    model = JobStatusResponse(**payload)
    assert model.review_required is True
    assert model.review_summary == {"ungrounded_count": 3}
    assert model.vision_fallback_count == 1
    assert model.vision_fallback_latency_seconds == 2.2


def test_job_status_handles_large_diagnostics_payload_under_latency_budget(db):
    user = get_user_model().objects.create_user("large_meta_user", password="pw")
    large_errors = [f"error-{i}-" + ("x" * 400) for i in range(200)]
    job_id = create_job(
        input_type="pmid",
        input_value="123",
        source="123",
        source_type="pmid",
        llm_model="gpt-5.4",
        user=user,
        status="needs_human_review",
        stage="needs_human_review",
        job_metadata={
            "human_review_required": True,
            "human_review_summary": {"ungrounded_count": 2},
            "dataset_parse_errors": large_errors,
            "workflow_graph_validation_issues": [{"message": "m" * 2000}] * 200,
        },
    )
    request = RequestFactory().get("/status")
    request.user = user
    started = time.perf_counter()
    response = views.job_status(request, job_id)
    elapsed = time.perf_counter() - started
    assert response.status_code == 200
    assert elapsed < 0.5


def test_progress_template_handles_needs_human_review_terminal_state():
    template_path = (
        Path(__file__).resolve().parents[1]
        / "templates"
        / "researcher_ai_portal"
        / "progress.html"
    )
    text = template_path.read_text(encoding="utf-8")
    assert "showNeedsHumanReview" in text
    assert 'data.status === "needs_human_review"' in text


def test_workflow_step_template_stops_running_state_for_needs_human_review():
    template_path = (
        Path(__file__).resolve().parents[1]
        / "templates"
        / "researcher_ai_portal"
        / "workflow_step.html"
    )
    text = template_path.read_text(encoding="utf-8")
    assert 'status === "needs_human_review"' in text
    assert "Human review required. Reloading…" in text
