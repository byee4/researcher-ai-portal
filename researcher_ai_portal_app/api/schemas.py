"""Pydantic schemas for the FastAPI visual builder API.

Phase 1 contains the foundational types used for smoke-testing and the ORM
integration endpoint.  Visual-builder graph schemas (WorkflowGraph, GraphNode,
GraphEdge, etc.) are added in Phase 2.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict


# ---------------------------------------------------------------------------
# Shared / utility
# ---------------------------------------------------------------------------


class PingResponse(BaseModel):
    status: str
    framework: str
    version: str


# ---------------------------------------------------------------------------
# Job summary (used by the ORM smoke test and future /jobs endpoints)
# ---------------------------------------------------------------------------


class JobSummary(BaseModel):
    """Lightweight job representation returned by the API.

    Derived from WorkflowJob without exposing internal DB fields.
    """

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    status: str
    progress: int
    stage: str
    current_step: str
    llm_model: str
    input_display: str
    source_type: str
    error: str
    created_at: datetime
    updated_at: datetime


class JobsListResponse(BaseModel):
    count: int
    jobs: list[JobSummary]


# ---------------------------------------------------------------------------
# Error envelope
# ---------------------------------------------------------------------------


class ErrorResponse(BaseModel):
    detail: str
    code: str | None = None
    extra: dict[str, Any] | None = None
