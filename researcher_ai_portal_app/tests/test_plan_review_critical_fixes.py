from __future__ import annotations

from pathlib import Path

from researcher_ai_portal_app import views


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_critical_session_key_encryption_roundtrip():
    secret = "sk-test-secret-1234567890"
    enc = views._encrypt_session_secret(secret)
    assert enc and enc != secret
    dec = views._decrypt_session_secret(enc)
    assert dec == secret


def test_critical_llm_env_guarded_by_process_lock_and_no_plain_session_storage():
    views_path = Path(__file__).resolve().parents[1] / "views.py"
    text = _read(views_path)
    assert "_LLM_ENV_LOCK = threading.RLock()" in text
    assert "with _LLM_ENV_LOCK:" in text
    assert '_SESSION_LLM_API_KEY_FIELD = "llm_api_key_enc"' in text
    assert 'request.session[_SESSION_LLM_API_KEY_FIELD] = _encrypt_session_secret(llm_api_key)' in text
    assert 'request.session.pop("llm_api_key", None)' in text


def test_critical_paper_cache_is_read_and_written():
    views_path = Path(__file__).resolve().parents[1] / "views.py"
    text = _read(views_path)
    assert "PaperCache.objects.filter(canonical_id=canonical_id" in text
    assert "_persist_component(job_id, \"paper\", cached.paper_json, \"cached\")" in text
    assert "PaperCache.objects.update_or_create(" in text

