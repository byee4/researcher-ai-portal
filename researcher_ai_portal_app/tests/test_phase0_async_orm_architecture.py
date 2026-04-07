from __future__ import annotations

from pathlib import Path


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_phase0_models_exist_and_no_llm_key_storage():
    models_path = Path(__file__).resolve().parents[1] / "models.py"
    text = _read(models_path)
    assert "class WorkflowJob(models.Model):" in text
    assert "class ComponentSnapshot(models.Model):" in text
    assert "class PaperCache(models.Model):" in text
    assert "llm_api_key" not in text


def test_phase0_views_use_user_scoped_get_job_and_db_status_payload():
    views_path = Path(__file__).resolve().parents[1] / "views.py"
    text = _read(views_path)
    assert "get_job(job_id, user=request.user)" in text
    assert "payload = merge_logs(payload, job_id)" in text


def test_phase0_start_parse_uses_session_keys_and_dispatch_helper():
    views_path = Path(__file__).resolve().parents[1] / "views.py"
    text = _read(views_path)
    assert 'request.session[_SESSION_LLM_API_KEY_FIELD] = _encrypt_session_secret(llm_api_key)' in text
    assert 'request.session["llm_model"] = llm_model' in text
    assert "_dispatch_workflow_step(" in text


def test_phase0_dispatch_helper_runs_steps_serially():
    views_path = Path(__file__).resolve().parents[1] / "views.py"
    text = _read(views_path)
    assert "def _dispatch_workflow_step(" in text
    assert "_run_step(" in text
    assert 'update_job(job_id, status="in_progress", current_step=step, stage=f"Running {label}")' in text


def test_phase0_settings_include_local_cache_and_db_sessions():
    settings_path = Path(__file__).resolve().parents[2] / "researcher_ai_portal" / "settings.py"
    text = _read(settings_path)
    assert "CACHES = {" in text
    assert "LocMemCache" in text
    assert "SESSION_ENGINE" in text
    assert "django.contrib.sessions.backends.db" in text
