"""FastAPI router — Phase 1 endpoints.

All routes are prefixed with /api/v1/ by the ASGI router in asgi.py.

Phase 1 routes:
  GET  /api/v1/ping            — unauthenticated smoke test
  GET  /api/v1/jobs            — list the current user's jobs (ORM smoke test)
  GET  /api/v1/jobs/{job_id}   — retrieve a single job summary

Phase 2+ routes (parse-publication, graph CRUD, WebSockets) are added in
their respective phases and imported here.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status

from .dependencies import get_current_user
from .schemas import (
    ErrorResponse,
    JobSummary,
    JobsListResponse,
    PingResponse,
)
from . import repository

router = APIRouter()


# ---------------------------------------------------------------------------
# Health / smoke test (unauthenticated)
# ---------------------------------------------------------------------------


@router.get(
    "/ping",
    response_model=PingResponse,
    summary="FastAPI smoke test",
    tags=["system"],
)
async def ping() -> PingResponse:
    """Unauthenticated liveness probe.

    Returns immediately without touching the database.  Use this to confirm
    that FastAPI is wired into the ASGI router correctly.
    """
    import fastapi

    return PingResponse(
        status="ok",
        framework="fastapi",
        version=fastapi.__version__,
    )


# ---------------------------------------------------------------------------
# Jobs (authenticated — ORM integration smoke test)
# ---------------------------------------------------------------------------


@router.get(
    "/jobs",
    response_model=JobsListResponse,
    summary="List the authenticated user's workflow jobs",
    tags=["jobs"],
    responses={401: {"model": ErrorResponse}},
)
async def list_jobs(
    user: Annotated[object, Depends(get_current_user)],
    limit: int = 20,
) -> JobsListResponse:
    """Return the most recent workflow jobs for the logged-in user.

    This endpoint primarily exists in Phase 1 as an ORM integration smoke
    test: it proves that FastAPI can query Django's WorkflowJob model via
    the async repository layer.  It will be expanded in Phase 2 with filtering,
    pagination, and richer payload.
    """
    if limit < 1 or limit > 100:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="limit must be between 1 and 100",
        )

    jobs = await repository.get_recent_jobs_for_user(
        user_id=user.pk, limit=limit
    )
    return JobsListResponse(
        count=len(jobs),
        jobs=[JobSummary.model_validate(j) for j in jobs],
    )


@router.get(
    "/jobs/{job_id}",
    response_model=JobSummary,
    summary="Retrieve a single workflow job",
    tags=["jobs"],
    responses={
        401: {"model": ErrorResponse},
        404: {"model": ErrorResponse},
    },
)
async def get_job(
    job_id: str,
    user: Annotated[object, Depends(get_current_user)],
) -> JobSummary:
    """Return a single workflow job owned by the authenticated user."""
    job = await repository.get_job_for_user(job_id=job_id, user_id=user.pk)
    if job is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Job '{job_id}' not found.",
        )
    return JobSummary.model_validate(job)
