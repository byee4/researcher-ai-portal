from __future__ import annotations

import json

import pytest
from django.contrib.auth import get_user_model
from django.test import RequestFactory

from researcher_ai_portal_app import views
from researcher_ai_portal_app.job_store import create_job, get_job


_RATE_LIMIT_MSG = "Vision model rate limit reached. Please try again in 1 minute."


def test_humanize_step_error_for_rate_limit():
    err = RuntimeError("429 Too Many Requests from vision provider")
    assert views._humanize_step_error(err) == _RATE_LIMIT_MSG


def test_dispatch_workflow_step_persists_human_readable_rate_limit(monkeypatch):
    monkeypatch.setenv("USE_ASYNC_TASKS", "0")

    def boom(*args, **kwargs):
        raise RuntimeError("429 Too Many Requests from vision provider")

    monkeypatch.setattr(views, "_run_step", boom)

    job_id = create_job(
        input_type="pmid",
        input_value="123",
        source="123",
        source_type="pmid",
        llm_model="gpt-5.4",
        llm_api_key="sk-12345678901234567890",
    )

    with pytest.raises(RuntimeError):
        views._dispatch_workflow_step(job_id, "paper", llm_api_key="sk-12345678901234567890", llm_model="gpt-5.4")

    job = get_job(job_id)
    assert job is not None
    assert job.get("status") == "failed"
    assert job.get("error") == _RATE_LIMIT_MSG


def test_job_status_endpoint_surfaces_human_readable_error(db):
    user = get_user_model().objects.create_user("rate_limit_user", password="pw")
    job_id = create_job(
        input_type="pmid",
        input_value="123",
        source="123",
        source_type="pmid",
        llm_model="gpt-5.4",
        llm_api_key="sk-12345678901234567890",
        user=user,
        status="failed",
        error=_RATE_LIMIT_MSG,
    )

    request = RequestFactory().get("/status")
    request.user = user
    response = views.job_status(request, job_id)

    payload = json.loads(response.content.decode("utf-8"))
    assert payload["status"] == "failed"
    assert payload["error"] == _RATE_LIMIT_MSG
