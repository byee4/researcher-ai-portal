from __future__ import annotations

from django.contrib.auth import get_user_model
from django.http import HttpResponse
from django.test import RequestFactory

from researcher_ai_portal_app import views
from researcher_ai_portal_app.job_store import create_job, get_job


def test_dashboard_get_during_parse_does_not_interrupt_job(monkeypatch, db):
    user = get_user_model().objects.create_user("dash_nav_user", password="pw")
    job_id = create_job(
        input_type="pmid",
        input_value="12345678",
        source="12345678",
        source_type="pmid",
        llm_model="gpt-5.4",
        user=user,
        status="in_progress",
        current_step="figures",
        stage="Running Figure Parser",
        progress=35,
    )

    # Keep the view lightweight for this regression check.
    monkeypatch.setattr(views, "build_dashboard_app", lambda *args, **kwargs: "dash")
    monkeypatch.setattr(views, "build_dag_app", lambda *args, **kwargs: "dag")
    monkeypatch.setattr(views, "render", lambda request, template, context: HttpResponse("ok"))

    request = RequestFactory().get(f"/jobs/{job_id}/dashboard/")
    request.user = user
    response = views.dashboard(request, job_id)
    assert response.status_code == 200

    job = get_job(job_id, user=user)
    assert job is not None
    assert job.get("status") == "in_progress"
    assert job.get("current_step") == "figures"
    assert job.get("stage") == "Running Figure Parser"
