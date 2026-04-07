"""Pydantic schemas for the FastAPI visual builder API.

Phase 1 — foundational types for ORM smoke-testing and the /jobs endpoints.
Phase 2 — visual builder graph types: GraphNode, GraphEdge, WorkflowGraph,
           ParsePublicationRequest / Response, and JobStatusResponse.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


# ---------------------------------------------------------------------------
# Shared / utility
# ---------------------------------------------------------------------------


class PingResponse(BaseModel):
    status: str
    framework: str
    version: str


# ---------------------------------------------------------------------------
# Job summary (used by the ORM smoke test and /jobs endpoints)
# ---------------------------------------------------------------------------


class JobSummary(BaseModel):
    """Lightweight job representation returned by the API."""

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


# ---------------------------------------------------------------------------
# Phase 2 — Visual builder graph types
# ---------------------------------------------------------------------------


class NodePort(BaseModel):
    """A single connection point on a visual pipeline node."""

    id: str
    label: str
    type: Literal["input", "output"]


class GraphNode(BaseModel):
    """A single node in the React Flow visual pipeline graph.

    ``type`` maps to a React Flow custom node component name:
      paper_parser, figure_parser, method_parser, datasets_parser,
      software_parser, pipeline_builder.

    ``data`` carries the node-specific config plus a summary of the parsed
    metadata extracted from ComponentSnapshot.payload.
    """

    id: str
    type: str  # e.g. "paper_parser", "figure_parser"
    position: dict[str, float]  # {"x": 0.0, "y": 0.0}
    data: dict[str, Any]  # step, label, status, component summary
    ports: list[NodePort] = Field(default_factory=list)


class GraphEdge(BaseModel):
    """A directed edge connecting two nodes in the React Flow graph."""

    id: str
    source: str  # node id
    target: str  # node id
    source_handle: str | None = None
    target_handle: str | None = None


class WorkflowGraph(BaseModel):
    """Full React Flow graph state stored in WorkflowJob.graph_data."""

    nodes: list[GraphNode]
    edges: list[GraphEdge]
    viewport: dict[str, float] = Field(
        default_factory=lambda: {"x": 0.0, "y": 0.0, "zoom": 1.0}
    )


# ---------------------------------------------------------------------------
# Phase 2 — Publication parsing request / response
# ---------------------------------------------------------------------------


class ParsePublicationRequest(BaseModel):
    """Body payload for POST /api/v1/parse-publication.

    ``source``        — PubMed ID, DOI, or a path to a staged PDF.
    ``source_type``   — one of "pmid", "doi", "pdf".
    ``llm_model``     — the model string to use for all parsing steps.
    ``llm_api_key``   — optional; if omitted the server falls back to the
                        LLM_API_KEY environment variable.
    ``force_reparse`` — bypass the PaperCache and re-parse from scratch.
    """

    source: str
    source_type: Literal["pmid", "doi", "pdf"]
    llm_model: str
    llm_api_key: str = ""
    force_reparse: bool = False


class ParsePublicationResponse(BaseModel):
    """Immediate 202 response from POST /api/v1/parse-publication.

    The pipeline runs asynchronously.  Poll GET /api/v1/jobs/{job_id}/status
    for completion.  ``nodes`` is an empty list in the 202 response; it is
    populated once the job reaches 'completed' status and
    GET /api/v1/graphs/{job_id} is called.
    """

    job_id: str
    status: str
    nodes: list[GraphNode] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Phase 2 — Lightweight job status poll response
# ---------------------------------------------------------------------------


class JobStatusResponse(BaseModel):
    """Returned by GET /api/v1/jobs/{job_id}/status."""

    job_id: str
    status: str
    progress: int
    stage: str
    current_step: str
    error: str
    parse_logs: list[Any] = Field(default_factory=list)
    figure_parse_total: int = 0
    figure_parse_current: int = 0


# ---------------------------------------------------------------------------
# Phase 2a — Component PATCH (partial update with path-based mutation)
# ---------------------------------------------------------------------------


class ComponentPatchRequest(BaseModel):
    """Body payload for PATCH /api/v1/jobs/{job_id}/components/{step}.

    Uses dot-bracket notation to address a specific field inside the component
    payload, e.g.::

        path  = "assay_graph.assays[1].steps[0].software_version"
        value = "2.1.3"

    Only paths in the per-step whitelist are accepted; unknown paths receive
    a 422 response without touching the database.
    """

    path: str = Field(
        ...,
        description='Dot-bracket path into the payload, e.g. "assays[0].steps[1].software_version"',
        max_length=512,
    )
    value: Any = Field(..., description="New value to set at the given path")


class ComponentSaveResponse(BaseModel):
    """Response body for a successful PATCH to a component step.

    Returns the full (re-validated) payload plus refreshed confidence data
    so the client can update the Command Center without a separate poll.
    """

    step: str
    payload: Any
    confidence: dict[str, Any]
    actionable_items: list[dict[str, Any]]


# ---------------------------------------------------------------------------
# Phase 3 — Lightweight confidence poll response
# ---------------------------------------------------------------------------


class ConfidenceResponse(BaseModel):
    """Returned by GET /api/v1/jobs/{job_id}/confidence.

    Lightweight endpoint for the React dashboard to refresh Command Center
    data after PATCH saves without a full page reload.
    """

    job_id: str
    overall: float
    assay_confidences: dict[str, Any]
    validation_passed: bool
    actionable_items: list[dict[str, Any]]
