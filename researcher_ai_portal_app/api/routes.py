"""FastAPI router — Phase 1 + Phase 2 + Phase 3 + Phase 4 endpoints.

All routes are prefixed with /api/v1/ by the ASGI router in asgi.py.

Phase 1 routes (smoke tests + ORM integration):
  GET  /api/v1/ping
  GET  /api/v1/jobs
  GET  /api/v1/jobs/{job_id}

Phase 2 routes (visual builder core):
  POST /api/v1/parse-publication        — submit a publication; returns 202
  GET  /api/v1/jobs/{job_id}/status     — lightweight status poll
  GET  /api/v1/graphs/{job_id}          — retrieve React Flow graph state
  PUT  /api/v1/graphs/{job_id}          — save React Flow graph state
  GET  /api/v1/graphs/{job_id}/nodes/{node_id}  — single-node detail
  PATCH /api/v1/jobs/{job_id}/components/{step}  — partial update + confidence refresh

Phase 3 routes (confidence correction loops):
  GET  /api/v1/jobs/{job_id}/confidence — lightweight confidence poll

Phase 4 routes (graph compilation + execution + paginated logs):
  POST /api/v1/graphs/{job_id}/compile  — DAG validation; returns ValidationIssue[]
  POST /api/v1/graphs/{job_id}/execute  — 202 pipeline dispatch
  GET  /api/v1/jobs/{job_id}/logs       — incremental log fetch (?since_ts&limit)
"""

from __future__ import annotations

import asyncio
import os
import threading
from typing import Annotated

from asgiref.sync import sync_to_async
from fastapi import APIRouter, Depends, HTTPException, status

from .dependencies import get_current_user
from .schemas import (
    ComponentPatchRequest,
    ComponentSaveResponse,
    CompileResponse,
    ConfidenceResponse,
    ErrorResponse,
    ExecuteRequest,
    ExecuteResponse,
    GraphNode,
    JobStatusResponse,
    JobSummary,
    JobsListResponse,
    LogsResponse,
    ParsePublicationRequest,
    ParsePublicationResponse,
    PingResponse,
    RagWorkflowResponse,
    WorkflowGraph,
)
from . import repository

router = APIRouter()

# ---------------------------------------------------------------------------
# Background task registry — prevents asyncio.Task objects from being
# garbage-collected before the pipeline finishes.
# ---------------------------------------------------------------------------
_background_tasks: set[asyncio.Task] = set()  # type: ignore[type-arg]


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
    """Return the most recent workflow jobs for the logged-in user."""
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


# ---------------------------------------------------------------------------
# Phase 2 — Status polling
# ---------------------------------------------------------------------------


@router.get(
    "/jobs/{job_id}/status",
    response_model=JobStatusResponse,
    summary="Lightweight job status poll",
    tags=["jobs"],
    responses={
        401: {"model": ErrorResponse},
        404: {"model": ErrorResponse},
    },
)
async def get_job_status(
    job_id: str,
    user: Annotated[object, Depends(get_current_user)],
) -> JobStatusResponse:
    """Return real-time status for a running or completed pipeline job.

    Reads from the Django cache layer first (written by tasks.py) so that
    frequent polling during active parsing avoids hitting the database on
    every request.  Falls back to the database if the cache entry is absent.
    """
    status_data = await repository.get_job_status(
        job_id=job_id, user_id=user.pk
    )
    if status_data is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Job '{job_id}' not found.",
        )
    return JobStatusResponse(**status_data)


# ---------------------------------------------------------------------------
# Phase 3 — Lightweight confidence poll
# ---------------------------------------------------------------------------


@router.get(
    "/jobs/{job_id}/confidence",
    response_model=ConfidenceResponse,
    summary="Lightweight confidence poll for the Command Center",
    tags=["jobs"],
    responses={
        401: {"model": ErrorResponse},
        404: {"model": ErrorResponse},
    },
)
async def get_job_confidence(
    job_id: str,
    user: Annotated[object, Depends(get_current_user)],
) -> ConfidenceResponse:
    """Return the current confidence score + actionable items for a job.

    Recomputes confidence from the live ComponentSnapshot rows so the Command
    Center always shows fresh data after any PATCH save.  This is cheaper than
    a full page reload and faster than waiting for the PATCH response cycle
    when the client wants to refresh independently (e.g. after switching tabs).

    Pair with the PATCH /components/{step} autosave: prefer the PATCH response
    for immediate field-save feedback; use this endpoint to re-sync the full
    Command Center after tab switches or bookmark loads.
    """
    data = await repository.get_confidence_for_job(
        job_id=job_id, user_id=user.pk
    )
    if data is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Job '{job_id}' not found.",
        )
    return ConfidenceResponse(**data)


@router.get(
    "/jobs/{job_id}/rag-workflow",
    response_model=RagWorkflowResponse,
    summary="Get normalized Methods-step RAG workflow telemetry",
    tags=["jobs"],
    responses={
        401: {"model": ErrorResponse},
        404: {"model": ErrorResponse},
    },
)
async def get_rag_workflow(
    job_id: str,
    user: Annotated[object, Depends(get_current_user)],
) -> RagWorkflowResponse:
    data = await repository.get_rag_workflow_for_user(
        job_id=job_id,
        user_id=user.pk,
    )
    if data is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Job '{job_id}' not found.",
        )
    return RagWorkflowResponse(**data)


# ---------------------------------------------------------------------------
# Phase 2 — Graph CRUD
# ---------------------------------------------------------------------------


@router.get(
    "/graphs/{job_id}",
    response_model=WorkflowGraph,
    summary="Retrieve the React Flow graph state for a job",
    tags=["graphs"],
    responses={
        401: {"model": ErrorResponse},
        404: {"model": ErrorResponse},
    },
)
async def get_graph(
    job_id: str,
    user: Annotated[object, Depends(get_current_user)],
) -> WorkflowGraph:
    """Return the stored React Flow graph (nodes, edges, viewport) for a job.

    State-persistence contract
    --------------------------
    This endpoint ALWAYS returns the previously persisted graph when one
    exists.  It never regenerates node positions from scratch, so any layout
    changes the user made (node drag, zoom, pan) survive a full page reload.

    The write side is ``PUT /api/v1/graphs/{job_id}``: the client sends the
    full ReactFlow state (including updated ``position`` values) after every
    user interaction, and that payload is stored verbatim in
    ``WorkflowJob.graph_data``.  Subsequent GET calls return that saved state.

    If the job exists but no graph has been generated or saved yet, an empty
    WorkflowGraph is returned (empty nodes and edges lists, default viewport).
    This lets the frontend distinguish "job not found" (404) from "job has no
    graph yet" (200 with empty lists) and fall back to client-side graph
    construction from the injected SOFTWARE_TOOLS / METHOD_ASSAYS data.
    """
    graph_data = await repository.get_graph_state(
        job_id=job_id, user_id=user.pk
    )
    if graph_data is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Job '{job_id}' not found.",
        )

    if not graph_data:
        # Job exists but graph hasn't been generated yet.
        return WorkflowGraph(nodes=[], edges=[])

    # Detect old step-format graphs (pre-redesign: nodes had data.step but no
    # data.name).  Return an empty graph so the frontend falls back to
    # building a fresh tool graph from the injected SOFTWARE_TOOLS data.
    stored_nodes = graph_data.get("nodes") or []
    is_tool_graph = any(
        isinstance(n.get("data"), dict) and n["data"].get("name")
        for n in stored_nodes
    )
    if stored_nodes and not is_tool_graph:
        return WorkflowGraph(nodes=[], edges=[])

    return WorkflowGraph.model_validate(graph_data)


# ---------------------------------------------------------------------------
# Phase 4 — Graph compilation + execution + paginated logs
# ---------------------------------------------------------------------------


@router.post(
    "/graphs/{job_id}/compile",
    response_model=CompileResponse,
    summary="Validate the visual pipeline graph (Dry Run)",
    tags=["graphs"],
    responses={
        401: {"model": ErrorResponse},
        404: {"model": ErrorResponse},
    },
)
async def compile_graph(
    job_id: str,
    user: Annotated[object, Depends(get_current_user)],
) -> CompileResponse:
    """Validate the stored React Flow graph as a directed acyclic graph.

    Performs two checks:

    1. **Topological sort** — detects directed cycles using Kahn's algorithm.
       Any node that is part of a cycle receives an ``error``-severity
       ``ValidationIssue``.  ``execution_order`` is empty when a cycle is
       found.

    2. **Field validation** — checks each node for missing name (error),
       missing version (warning), and missing I/O definition (warning).

    ``valid`` is ``True`` when no error-severity issues are present.  Warnings
    do not block execution but are surfaced in the UI as amber node outlines.

    The client should call this before ``POST /execute`` to confirm the graph
    is executable, and use the returned ``node_id`` fields to highlight broken
    nodes in the React Flow canvas.
    """
    result = await repository.compile_graph(job_id=job_id, user_id=user.pk)
    if result is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Job '{job_id}' not found.",
        )
    return CompileResponse(**result)


@router.post(
    "/graphs/{job_id}/execute",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=ExecuteResponse,
    summary="Execute the compiled pipeline (202 Accepted)",
    tags=["graphs"],
    responses={
        401: {"model": ErrorResponse},
        404: {"model": ErrorResponse},
        422: {"model": ErrorResponse},
    },
)
async def execute_pipeline(
    job_id: str,
    req: ExecuteRequest,
    user: Annotated[object, Depends(get_current_user)],
) -> ExecuteResponse:
    """Dispatch the full pipeline for an existing job.

    **Pre-flight compile check** — the graph is compiled before dispatch.
    If any error-severity ``ValidationIssue`` is present the endpoint returns
    422 with the compile result embedded in the ``extra`` field so the client
    can surface the issues without a separate compile call.

    On success (202) the pipeline runs in a background thread — the same
    ``_run_full_pipeline_sync`` path used by ``POST /parse-publication``.
    Poll ``GET /api/v1/jobs/{job_id}/status`` for progress and
    ``GET /api/v1/jobs/{job_id}/logs`` for streaming output.

    ``llm_api_key`` and ``llm_model`` default to environment variables if
    omitted.  ``force_reparse`` bypasses cached step outputs.
    """
    # Verify the job exists and belongs to the authenticated user.
    job = await repository.get_job_for_user(job_id=job_id, user_id=user.pk)
    if job is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Job '{job_id}' not found.",
        )

    # Compile check — abort if there are error-severity issues.
    compile_result = await repository.compile_graph(job_id=job_id, user_id=user.pk)
    if compile_result and not compile_result["valid"]:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Pipeline graph has validation errors. Run /compile first to see issues.",
        )

    # Resolve LLM configuration — fall back to env vars when not provided.
    from researcher_ai_portal_app.views import (
        _infer_provider,
        _validate_llm_api_key,
        _validate_llm_model,
    )

    raw_model = req.llm_model or job.llm_model or os.environ.get("LLM_MODEL", "gpt-5.4")
    try:
        llm_model = _validate_llm_model(raw_model)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        )

    raw_key = req.llm_api_key or os.environ.get("LLM_API_KEY", "")
    try:
        llm_api_key = _validate_llm_api_key(raw_key, _infer_provider(llm_model))
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        )

    # Reset job status so the frontend polling sees a fresh run.
    from researcher_ai_portal_app.job_store import get_job, update_job
    @sync_to_async
    def _reset() -> None:
        update_job(
            str(job_id),
            status="queued",
            progress=0,
            stage="Queued for re-execution",
            current_step="paper",
            error="",
            parse_logs=[],
        )

    await _reset()

    # Dispatch the pipeline in a background thread (identical to parse-publication).
    pipeline_coro = sync_to_async(
        _run_full_pipeline_sync, thread_sensitive=False
    )(str(job_id), llm_api_key, llm_model, req.force_reparse)

    task = asyncio.create_task(pipeline_coro)
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)

    return ExecuteResponse(job_id=str(job_id), status="queued")


@router.get(
    "/jobs/{job_id}/logs",
    response_model=LogsResponse,
    summary="Paginated incremental log fetch",
    tags=["jobs"],
    responses={
        401: {"model": ErrorResponse},
        404: {"model": ErrorResponse},
    },
)
async def get_job_logs(
    job_id: str,
    user: Annotated[object, Depends(get_current_user)],
    since_ts: str | None = None,
    limit: int = 50,
) -> LogsResponse:
    """Return log entries for a job, optionally filtered to those after *since_ts*.

    Supports incremental fetching to avoid downloading the full log on every
    poll.  Typical client loop::

        watermark = None
        while job_running:
            resp = GET /api/v1/jobs/{job_id}/logs?since_ts={watermark}&limit=50
            display(resp.entries)
            watermark = resp.next_since_ts
            if resp.has_more:
                continue   # fetch next page immediately
            else:
                sleep(3)   # wait before next poll

    ``since_ts`` is an ISO 8601 UTC timestamp string (as returned in
    ``next_since_ts``).  Entries are compared lexicographically, which is
    correct for the UTC timestamps produced by ``job_events.append_job_log``.

    ``limit`` is clamped to [1, 200].
    """
    if limit < 1:
        limit = 1
    if limit > 200:
        limit = 200

    result = await repository.get_paginated_logs(
        job_id=job_id, user_id=user.pk, since_ts=since_ts, limit=limit
    )
    if result is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Job '{job_id}' not found.",
        )
    return LogsResponse(**result)


# ---------------------------------------------------------------------------
# (existing) Phase 2 — Graph CRUD continued
# ---------------------------------------------------------------------------


@router.put(
    "/graphs/{job_id}",
    response_model=WorkflowGraph,
    summary="Save the React Flow graph state for a job",
    tags=["graphs"],
    responses={
        401: {"model": ErrorResponse},
        404: {"model": ErrorResponse},
    },
)
async def save_graph(
    job_id: str,
    graph: WorkflowGraph,
    user: Annotated[object, Depends(get_current_user)],
) -> WorkflowGraph:
    """Persist the React Flow graph state (after the user drags / connects nodes).

    Accepts the full WorkflowGraph payload and stores it in
    WorkflowJob.graph_data.  Returns the saved graph so the client can
    confirm the round-trip.
    """
    saved = await repository.save_graph_state(
        job_id=job_id,
        user_id=user.pk,
        graph_json=graph.model_dump(),
    )
    if not saved:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Job '{job_id}' not found.",
        )
    return graph


@router.get(
    "/graphs/{job_id}/nodes/{node_id}",
    response_model=GraphNode,
    summary="Get detailed metadata for a single pipeline node",
    tags=["graphs"],
    responses={
        401: {"model": ErrorResponse},
        404: {"model": ErrorResponse},
    },
)
async def get_graph_node(
    job_id: str,
    node_id: str,
    user: Annotated[object, Depends(get_current_user)],
) -> GraphNode:
    """Return the full ComponentSnapshot payload for a single pipeline step.

    ``node_id`` maps to a step name (e.g. "paper", "figures", "method").
    This endpoint exposes the raw parsed metadata so the React Flow node
    detail panel can display the complete structured output.
    """
    from .graph_layout import STEP_LABELS, NODE_TYPE_MAP

    snapshot = await repository.get_component_snapshot(
        job_id=job_id, user_id=user.pk, step=node_id
    )
    if snapshot is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Node '{node_id}' not found for job '{job_id}'.",
        )

    # Pull position from the saved graph if available; fall back to (0, 0).
    graph_data = await repository.get_graph_state(
        job_id=job_id, user_id=user.pk
    )
    position = {"x": 0.0, "y": 0.0}
    if graph_data:
        for node in graph_data.get("nodes", []):
            if node.get("id") == node_id:
                position = node.get("position", position)
                break

    return GraphNode(
        id=node_id,
        type=NODE_TYPE_MAP.get(node_id, node_id),
        position=position,
        data={
            "step": snapshot["step"],
            "label": STEP_LABELS.get(node_id, node_id),
            "status": snapshot["status"],
            "source": snapshot["source"],
            "missing_fields": snapshot["missing_fields"],
            "payload": snapshot["payload"],
        },
    )


# ---------------------------------------------------------------------------
# Phase 2a — Component PATCH  (partial update + confidence refresh)
# ---------------------------------------------------------------------------


@router.patch(
    "/jobs/{job_id}/components/{step}",
    response_model=ComponentSaveResponse,
    summary="Partially update a component snapshot (autosave)",
    tags=["components"],
    responses={
        401: {"model": ErrorResponse},
        404: {"model": ErrorResponse},
        422: {"model": ErrorResponse},
    },
)
async def patch_component(
    job_id: str,
    step: str,
    req: ComponentPatchRequest,
    user: Annotated[object, Depends(get_current_user)],
) -> ComponentSaveResponse:
    """Apply a path-based partial update to a component snapshot payload.

    Uses dot-bracket notation, e.g.::

        PATCH /api/v1/jobs/{job_id}/components/method
        { "path": "assay_graph.assays[0].steps[1].software_version",
          "value": "2.1.3" }

    The endpoint re-validates the full payload through the same Pydantic
    gate used by the Django form views, recomputes confidence, and returns
    the updated payload + confidence delta in a single response so the
    client can refresh the Command Center without a separate API call.

    Only paths in the per-step whitelist are accepted; unknown paths receive
    a 422 Unprocessable Entity response without touching the database.
    """
    valid_steps = {"paper", "figures", "method", "datasets", "software", "pipeline"}
    if step not in valid_steps:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Unknown step {step!r}. Must be one of: {', '.join(sorted(valid_steps))}.",
        )

    try:
        result = await repository.patch_component_snapshot(
            job_id=job_id,
            user_id=user.pk,
            step=step,
            path=req.path,
            value=req.value,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        )

    if result is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Component '{step}' not found for job '{job_id}'.",
        )

    return ComponentSaveResponse(**result)


# ---------------------------------------------------------------------------
# Phase 2 — Publication parsing  (202 Accepted + background pipeline)
# ---------------------------------------------------------------------------


def _run_full_pipeline_sync(
    job_id: str,
    llm_api_key: str,
    llm_model: str,
    force_reparse: bool,
) -> None:
    """Run all pipeline steps sequentially in a worker thread.

    This mirrors what the Django ``start_parse`` view does manually when the
    user clicks through each step.  For the FastAPI endpoint we run them all
    automatically so the client only needs to poll /status.

    Imports from views.py happen inside this function so that Django's app
    registry is fully loaded before the imports resolve.
    """
    from researcher_ai_portal_app.job_store import get_job as get_job_record
    from researcher_ai_portal_app.job_store import update_job
    from researcher_ai_portal_app.views import (
        STEP_LABELS,
        STEP_ORDER,
        _dispatch_workflow_step,
    )

    try:
        for step in STEP_ORDER:
            existing = get_job_record(job_id)
            if existing is not None and str(existing.get("status") or "") in {"failed", "needs_human_review"}:
                return
            update_job(
                job_id,
                status="in_progress",
                current_step=step,
                stage=f"Running {STEP_LABELS[step]}",
            )
            _dispatch_workflow_step(
                job_id,
                step,
                llm_api_key=llm_api_key,
                llm_model=llm_model,
                force_reparse=force_reparse,
            )
    except Exception:
        # _dispatch_workflow_step already set status="failed" on the job.
        return

    # All steps succeeded — persist the auto-generated graph layout.
    _save_graph_after_completion(job_id)

    final = get_job_record(job_id)
    if final is None:
        return
    if str(final.get("status") or "") == "needs_human_review":
        return
    update_job(job_id, status="completed", progress=100, stage="Workflow complete")


def _save_graph_after_completion(job_id: str) -> None:
    """Generate the tool-based React Flow graph and persist it to graph_data.

    Uses ``generate_tool_graph`` (one node per software tool) when the job
    has a non-empty software component; falls back to ``generate_default_graph``
    (the six fixed parse-step nodes) when the software list is empty.
    """
    from researcher_ai_portal_app.job_store import get_job
    from researcher_ai_portal_app.models import WorkflowJob
    from .graph_layout import generate_default_graph, generate_tool_graph

    job_dict = get_job(job_id)
    if job_dict is None:
        return

    components = job_dict.get("components") or {}
    software_list: list = components.get("software") or []
    pipeline_config: dict = components.get("pipeline") or {}

    if software_list:
        graph = generate_tool_graph(software_list, pipeline_config)
    else:
        # Fallback: no software parsed yet — show the step-level graph so the
        # canvas is never completely empty.
        component_meta = job_dict.get("component_meta") or {}
        graph = generate_default_graph(components, component_meta)

    # Use direct ORM update — update_job does not expose graph_data.
    try:
        WorkflowJob.objects.filter(id=job_id).update(graph_data=graph)
    except Exception:
        pass  # Non-critical; user can still retrieve an empty graph


@router.post(
    "/parse-publication",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=ParsePublicationResponse,
    summary="Submit a publication for parsing",
    tags=["pipeline"],
    responses={
        401: {"model": ErrorResponse},
        422: {"model": ErrorResponse},
    },
)
async def parse_publication(
    req: ParsePublicationRequest,
    user: Annotated[object, Depends(get_current_user)],
) -> ParsePublicationResponse:
    """Submit a publication and start the full parsing pipeline.

    Returns **202 Accepted** immediately.  The six-step pipeline (paper →
    figures → method → datasets → software → pipeline) runs in a background
    thread.  Poll ``GET /api/v1/jobs/{job_id}/status`` every 2–3 seconds
    until ``status`` is ``"completed"``, ``"failed"``, or ``"needs_human_review"``, then retrieve the
    generated graph with ``GET /api/v1/graphs/{job_id}``.

    ``llm_api_key`` is optional; if omitted the server falls back to the
    ``LLM_API_KEY`` environment variable.
    """
    from researcher_ai_portal_app.job_store import create_job
    from researcher_ai_portal_app.views import (
        _infer_provider,
        _validate_llm_api_key,
        _validate_llm_model,
    )

    # Validate the model string and API key using the existing Django helpers.
    try:
        llm_model = _validate_llm_model(req.llm_model)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        )

    raw_key = req.llm_api_key or os.environ.get("LLM_API_KEY", "")
    try:
        llm_api_key = _validate_llm_api_key(raw_key, _infer_provider(llm_model))
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        )

    # Create the job synchronously via the existing job store.
    @sync_to_async
    def _create() -> str:
        return create_job(
            req.source_type,
            req.source,
            user=user,
            source=req.source,
            source_type=req.source_type,
            llm_model=llm_model,
            status="queued",
            stage="Queued",
            progress=0,
            current_step="paper",
        )

    job_id = await _create()

    # Launch the full pipeline in a background thread.
    # sync_to_async(thread_sensitive=False) runs the callable in a fresh
    # thread-pool thread that is not bound to the main Django thread.
    pipeline_coro = sync_to_async(
        _run_full_pipeline_sync, thread_sensitive=False
    )(job_id, llm_api_key, llm_model, req.force_reparse)

    task = asyncio.create_task(pipeline_coro)
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)

    return ParsePublicationResponse(
        job_id=job_id,
        status="queued",
        nodes=[],
    )
