"""Shared FastAPI dependency functions.

Phase 1: auth dependency that validates the Django session cookie and returns
the authenticated Django user.  All protected endpoints declare this as a
dependency via `Depends(get_current_user)`.

The auth flow:
  1. Browser sends the Django `sessionid` cookie with every request (because
     the React Flow frontend calls `/api/v1/` with `credentials: "include"`).
  2. We look up the session in Django's session backend.
  3. We extract `_auth_user_id` from the session and fetch the User row.
  4. If any step fails we raise HTTP 401.

This deliberately reuses Django's existing session infrastructure — no JWT,
no duplicate auth system.

Note on encrypted LLM API keys:
  The Django session also stores an encrypted LLM API key (written by
  `_encrypt_session_secret` in views.py). Endpoints that trigger parsing need
  to call `_decrypt_session_secret(session["llm_api_key_enc"])` from
  views.py — or, once views.py is refactored, from a shared utility module.
  This is handled explicitly in each route that needs it rather than being
  bundled into the auth dependency.
"""

from __future__ import annotations

from typing import Any

from asgiref.sync import sync_to_async
from fastapi import Cookie, Depends, HTTPException, status


async def _load_session(sessionid: str) -> dict[str, Any]:
    """Load a Django session dict from the session backend."""
    from django.contrib.sessions.backends.db import SessionStore

    @sync_to_async
    def _fetch() -> dict[str, Any] | None:
        store = SessionStore(session_key=sessionid)
        # _session_cache triggers a DB read; raises if key is invalid/expired.
        try:
            return dict(store._session)  # noqa: SLF001
        except Exception:
            return None

    result = await _fetch()
    if not result:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Session not found or expired. Please log in again.",
        )
    return result


async def get_current_user(
    sessionid: str | None = Cookie(default=None),
) -> Any:
    """FastAPI dependency — resolves the Django session cookie to a User object.

    Usage in route handlers::

        @router.get("/protected")
        async def protected(user=Depends(get_current_user)):
            return {"user": user.username}

    Raises HTTP 401 if the cookie is absent, the session is expired, or the
    user account no longer exists.
    """
    if not sessionid:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated. Please log in.",
        )

    session = await _load_session(sessionid)

    user_id = session.get("_auth_user_id")
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Session exists but contains no authenticated user.",
        )

    from django.contrib.auth import get_user_model

    User = get_user_model()
    try:
        return await User.objects.aget(pk=user_id)
    except User.DoesNotExist:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authenticated user account not found.",
        )
