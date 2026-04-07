from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from .job_store import get_job, update_job


def _trim_logs(logs: list[dict[str, Any]], limit: int = 250) -> list[dict[str, Any]]:
    if len(logs) <= limit:
        return logs
    return logs[-limit:]


def append_job_log(
    job_id: str,
    message: str,
    *,
    level: str = "info",
    step: str = "",
    extra: dict[str, Any] | None = None,
) -> None:
    job = get_job(job_id) or {}
    logs = list(job.get("parse_logs") or [])
    entry: dict[str, Any] = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "level": str(level or "info"),
        "step": str(step or ""),
        "message": str(message or ""),
    }
    if extra:
        entry["extra"] = extra
    logs.append(entry)
    update_job(job_id, parse_logs=_trim_logs(logs))


def merge_logs(payload: dict[str, Any], job_id: str) -> dict[str, Any]:
    existing = get_job(job_id) or {}
    logs = list(existing.get("parse_logs") or [])
    if logs:
        payload = dict(payload)
        payload["logs"] = _trim_logs(logs)
    return payload
