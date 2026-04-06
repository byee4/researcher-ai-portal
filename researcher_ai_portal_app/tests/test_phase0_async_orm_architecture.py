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


def test_phase0_views_use_cache_status_fallback_and_user_scoped_get_job():
    views_path = Path(__file__).resolve().parents[1] / "views.py"
    text = _read(views_path)
    assert "cache.get(_progress_cache_key(job_id))" in text
    assert "get_job(job_id, user=request.user)" in text


def test_phase0_start_parse_uses_session_keys_and_async_dispatch():
    views_path = Path(__file__).resolve().parents[1] / "views.py"
    text = _read(views_path)
    assert 'request.session[_SESSION_LLM_API_KEY_FIELD] = _encrypt_session_secret(llm_api_key)' in text
    assert 'request.session["llm_model"] = llm_model' in text
    assert "_dispatch_workflow_step(" in text


def test_phase0_tasks_define_async_workflow_and_rebuild_entrypoints():
    tasks_path = Path(__file__).resolve().parents[1] / "tasks.py"
    text = _read(tasks_path)
    assert "def run_workflow_step(" in text
    assert "def rebuild_from_step(" in text
    assert 'cache.set(_cache_key(job_id)' in text
    assert "if bind:" in text
    assert "return fn(None, *args, **kwargs)" in text


def test_phase0_settings_include_cache_and_celery_config():
    settings_path = Path(__file__).resolve().parents[2] / "researcher_ai_portal" / "settings.py"
    text = _read(settings_path)
    assert "CACHES = {" in text
    assert "CELERY_BROKER_URL" in text
    assert "CELERY_RESULT_BACKEND" in text
    assert "SESSION_ENGINE" in text
