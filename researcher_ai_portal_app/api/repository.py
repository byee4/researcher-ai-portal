"""Async repository layer — the single point of contact between FastAPI route
handlers and the Django ORM.

All ORM access from FastAPI routes MUST go through functions in this module.
This centralises the sync/async boundary, keeping route handlers clean and
making the ORM calls straightforward to test in isolation.

Django 5.2 native async ORM is used where supported (afilter, aget, acount,
acreate, aupdate).  Operations that still require sync_to_async (select_related,
prefetch_related, bulk queries) are wrapped explicitly here so callers never
need to think about it.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from asgiref.sync import sync_to_async


# ---------------------------------------------------------------------------
# Job queries
# ---------------------------------------------------------------------------


async def get_recent_jobs_for_user(user_id: int, limit: int = 20) -> list[Any]:
    """Return the most recent WorkflowJob rows for *user_id*.

    Uses sync_to_async because queryset slicing with ordering requires
    evaluation in a sync context in Django 5.x.
    """
    from researcher_ai_portal_app.models import WorkflowJob

    @sync_to_async
    def _fetch() -> list[Any]:
        return list(
            WorkflowJob.objects
            .filter(user_id=user_id)
            .order_by("-created_at")[:limit]
        )

    return await _fetch()


async def get_job_for_user(job_id: str | UUID, user_id: int) -> Any | None:
    """Return a single WorkflowJob owned by user_id, or None if not found."""
    from researcher_ai_portal_app.models import WorkflowJob

    try:
        return await WorkflowJob.objects.aget(id=job_id, user_id=user_id)
    except WorkflowJob.DoesNotExist:
        return None


async def get_job_with_components(
    job_id: str | UUID, user_id: int
) -> dict[str, Any] | None:
    """Return a job and all its ComponentSnapshot payloads keyed by step name.

    Returns None if the job doesn't exist or doesn't belong to user_id.
    select_related + prefetch_related require sync_to_async.
    """
    from researcher_ai_portal_app.models import WorkflowJob

    @sync_to_async
    def _fetch() -> dict[str, Any] | None:
        try:
            job = (
                WorkflowJob.objects
                .select_related("user")
                .prefetch_related("components")
                .get(id=job_id, user_id=user_id)
            )
        except WorkflowJob.DoesNotExist:
            return None
        return {
            "job": job,
            "components": {c.step: c for c in job.components.all()},
        }

    return await _fetch()


async def get_job_count_for_user(user_id: int) -> int:
    """Return the total number of jobs owned by user_id."""
    from researcher_ai_portal_app.models import WorkflowJob

    return await WorkflowJob.objects.filter(user_id=user_id).acount()


async def save_graph_state(
    job_id: str | UUID, user_id: int, graph_json: dict[str, Any]
) -> bool:
    """Persist a React Flow graph payload to WorkflowJob.graph_data.

    Returns True if the row was updated, False if the job wasn't found.

    Note: graph_data field is added in Phase 2 via a migration.  This function
    is defined here now so the repository API is stable; it will raise
    FieldError until the migration is applied.
    """
    from researcher_ai_portal_app.models import WorkflowJob

    count = await WorkflowJob.objects.filter(
        id=job_id, user_id=user_id
    ).aupdate(graph_data=graph_json)
    return count > 0


# ---------------------------------------------------------------------------
# Phase 2 — Graph state
# ---------------------------------------------------------------------------


async def get_graph_state(
    job_id: str | UUID, user_id: int
) -> dict[str, Any] | None:
    """Return the stored React Flow graph_data for a job, or None if not found.

    Falls back to an empty dict (falsy but not None) if the job exists but
    has no graph yet — callers can distinguish "job not found" from
    "job has no graph" by checking for None vs {}.
    """
    from researcher_ai_portal_app.models import WorkflowJob

    try:
        job = await WorkflowJob.objects.aget(id=job_id, user_id=user_id)
    except WorkflowJob.DoesNotExist:
        return None
    return job.graph_data or {}


async def get_component_snapshot(
    job_id: str | UUID, user_id: int, step: str
) -> dict[str, Any] | None:
    """Return the ComponentSnapshot payload for a single pipeline step.

    Returns None if the job doesn't exist or the snapshot for *step* has not
    been created yet.
    """
    from researcher_ai_portal_app.models import ComponentSnapshot, WorkflowJob

    @sync_to_async
    def _fetch() -> dict[str, Any] | None:
        try:
            # Ownership check via job
            job = WorkflowJob.objects.only("id").get(id=job_id, user_id=user_id)
        except WorkflowJob.DoesNotExist:
            return None
        try:
            snap = ComponentSnapshot.objects.get(job=job, step=step)
        except ComponentSnapshot.DoesNotExist:
            return None
        return {
            "step": snap.step,
            "status": snap.status,
            "source": snap.source,
            "missing_fields": snap.missing_fields,
            "payload": snap.payload,
            "created_at": snap.created_at.isoformat(),
        }

    return await _fetch()


async def get_job_status(
    job_id: str | UUID, user_id: int
) -> dict[str, Any] | None:
    """Return a lightweight status dict for polling, preferring the cache.

    Reads from Django's cache layer first (written by tasks.py) to avoid a
    database hit on every 2-second frontend poll.  Falls back to the DB row
    if the cache entry is absent or stale.

    Returns None if the job doesn't exist or doesn't belong to user_id.
    """
    from django.core.cache import cache

    from researcher_ai_portal_app.models import WorkflowJob

    cache_key = f"job_progress:{job_id}"

    @sync_to_async
    def _from_cache() -> dict[str, Any] | None:
        return cache.get(cache_key)

    @sync_to_async
    def _from_db() -> dict[str, Any] | None:
        try:
            job = WorkflowJob.objects.only(
                "id",
                "status",
                "progress",
                "stage",
                "current_step",
                "error",
                "parse_logs",
                "figure_parse_total",
                "figure_parse_current",
                "user_id",
            ).get(id=job_id, user_id=user_id)
        except WorkflowJob.DoesNotExist:
            return None
        return {
            "job_id": str(job.id),
            "status": job.status,
            "progress": job.progress,
            "stage": job.stage,
            "current_step": job.current_step,
            "error": job.error,
            "parse_logs": job.parse_logs,
            "figure_parse_total": job.figure_parse_total,
            "figure_parse_current": job.figure_parse_current,
        }

    cached = await _from_cache()
    if cached is not None:
        # The cache payload's user_id was written by tasks.py; verify ownership.
        if str(cached.get("user_id")) != str(user_id):
            return None
        return {
            "job_id": str(job_id),
            "status": cached.get("status", "unknown"),
            "progress": cached.get("progress", 0),
            "stage": cached.get("stage", ""),
            "current_step": cached.get("current_step", "paper"),
            "error": cached.get("error", ""),
            "parse_logs": cached.get("parse_logs", []),
            "figure_parse_total": cached.get("figure_parse_total", 0),
            "figure_parse_current": cached.get("figure_parse_current", 0),
        }

    return await _from_db()
