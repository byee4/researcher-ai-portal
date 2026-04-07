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
