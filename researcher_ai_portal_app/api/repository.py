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

import copy
import re
from typing import Any
from uuid import UUID

from asgiref.sync import sync_to_async


# ---------------------------------------------------------------------------
# Path-based mutation helpers (Phase 2a PATCH support)
# ---------------------------------------------------------------------------

# Segment pattern: either a bare key ("software_version") or a key with an
# integer index ("assays[1]", "steps[0]").
_SEG_RE = re.compile(r'^([A-Za-z_][A-Za-z0-9_]*)(?:\[(\d+)\])?$')

# ---------------------------------------------------------------------------
# Per-step whitelist of patchable JSON Pointer paths.
# Keys are allowed path *prefixes* (after stripping the leading step key).
# A request path must match one of these strings exactly OR start with one
# of them followed by a '.' or '['.
# ---------------------------------------------------------------------------
_PATCH_WHITELIST: dict[str, set[str]] = {
    "datasets": {
        "accession",
        "source",
        "title",
        "organism",
        "summary",
        "experiment_type",
    },
    "software": {
        "name",
        "version",
        "source_url",
        "language",
        "description",
        "license_type",
        "github_repo",
        "bioconda_package",
        "cran_package",
        "pypi_package",
    },
    "method": {
        "assay_graph",
        "assay_graph.assays",
    },
    "pipeline": {
        "steps",
        "config",
        "name",
        "description",
    },
    "paper": {
        "title",
        "abstract",
        "authors",
        "year",
        "doi",
    },
}


def _parse_path(path: str) -> list[str | int]:
    """Parse a dot-bracket path string into a list of keys / indices.

    Supports two syntaxes:

    - Dot-key notation with optional trailing index::

        "assay_graph.assays[1].steps[0].software_version"
        → ["assay_graph", "assays", 1, "steps", 0, "software_version"]

    - Leading bare index (for list-rooted payloads)::

        "[2].accession"
        → [2, "accession"]

    Raises ValueError if any segment is malformed.
    """
    tokens: list[str | int] = []

    # Handle optional leading bare array index, e.g. "[2].foo.bar"
    remainder = path
    lead_m = re.match(r'^\[(\d+)\](?:\.(.+))?$', path)
    if lead_m:
        tokens.append(int(lead_m.group(1)))
        remainder = lead_m.group(2) or ""

    if remainder:
        for raw in remainder.split("."):
            if not raw:
                raise ValueError(f"Empty segment in path: {path!r}")
            m = _SEG_RE.match(raw)
            if not m:
                raise ValueError(f"Invalid path segment: {raw!r}")
            tokens.append(m.group(1))
            if m.group(2) is not None:
                tokens.append(int(m.group(2)))

    if not tokens:
        raise ValueError(f"Empty path: {path!r}")
    return tokens


# List-based steps whose top-level payload is an array; paths start with [n].
_LIST_STEPS = frozenset({"datasets", "software", "figures"})


def _path_allowed(step: str, path: str) -> bool:
    """Return True if *path* is whitelisted for *step*.

    For list-based steps (datasets, software, figures) the path begins with an
    array index like ``[2].accession``.  We strip the leading ``[n].`` before
    matching against the whitelist so the whitelist can list bare field names.
    """
    allowed = _PATCH_WHITELIST.get(step, set())
    effective = path
    if step in _LIST_STEPS:
        # Strip leading "[n]." or "[n]" prefix
        m = re.match(r'^\[\d+\](?:\.(.+))?$', path)
        if not m:
            return False
        effective = m.group(1) or ""
    for prefix in allowed:
        if effective == prefix or effective.startswith(prefix + ".") or effective.startswith(prefix + "["):
            return True
    return False


def apply_patch(payload: Any, path: str, value: Any) -> Any:
    """Return a deep-copy of *payload* with *value* set at *path*.

    Raises ValueError on bad path syntax or out-of-range indices.
    The original *payload* is never mutated.
    """
    result = copy.deepcopy(payload)
    tokens = _parse_path(path)
    node: Any = result
    for tok in tokens[:-1]:
        if isinstance(tok, int):
            if not isinstance(node, list) or tok >= len(node):
                raise ValueError(f"Index {tok} out of range (length {len(node) if isinstance(node, list) else 'N/A'})")
            node = node[tok]
        else:
            if not isinstance(node, dict):
                raise ValueError(f"Expected dict at key {tok!r}, got {type(node).__name__}")
            if tok not in node:
                node[tok] = {}
            node = node[tok]
    last = tokens[-1]
    if isinstance(last, int):
        if not isinstance(node, list) or last >= len(node):
            raise ValueError(f"Index {last} out of range")
        node[last] = value
    else:
        if not isinstance(node, dict):
            raise ValueError(f"Expected dict at final key {last!r}, got {type(node).__name__}")
        node[last] = value  # type: ignore[index]
    return result


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


# ---------------------------------------------------------------------------
# Phase 2a — Component PATCH
# ---------------------------------------------------------------------------


async def get_confidence_for_job(
    job_id: str | UUID, user_id: int
) -> dict[str, Any] | None:
    """Return computed confidence + actionable_items for a job.

    Returns None if the job doesn't exist or doesn't belong to user_id.
    Recomputes confidence from the current ComponentSnapshot rows — no cache.
    """
    @sync_to_async
    def _fetch() -> dict[str, Any] | None:
        from researcher_ai_portal_app.models import ComponentSnapshot, WorkflowJob
        from researcher_ai_portal_app.confidence import (
            compute_confidence,
            compute_actionable_items,
        )

        try:
            job = WorkflowJob.objects.only("id").get(id=job_id, user_id=user_id)
        except WorkflowJob.DoesNotExist:
            return None

        components = {s.step: s.payload for s in ComponentSnapshot.objects.filter(job=job)}
        confidence = compute_confidence(components)
        actionable_items = compute_actionable_items(components, confidence)
        return {
            "job_id": str(job_id),
            "overall": confidence.get("overall", 0.0),
            "assay_confidences": confidence.get("assay_confidences", {}),
            "validation_passed": confidence.get("validation_passed", True),
            "actionable_items": actionable_items,
        }

    return await _fetch()


async def patch_component_snapshot(
    job_id: str | UUID,
    user_id: int,
    step: str,
    path: str,
    value: Any,
) -> dict[str, Any] | None:
    """Apply a path-based patch to a ComponentSnapshot payload.

    Returns a dict with keys ``payload``, ``confidence``, and
    ``actionable_items`` on success, or None if the job / snapshot is not
    found.  Raises ValueError for disallowed paths or validation failures.
    """
    if not _path_allowed(step, path):
        raise ValueError(
            f"Path {path!r} is not patchable for step {step!r}. "
            "Check the allowed path whitelist."
        )

    @sync_to_async
    def _fetch_and_patch() -> dict[str, Any] | None:
        from researcher_ai_portal_app.models import ComponentSnapshot, WorkflowJob
        from researcher_ai_portal_app.confidence import (
            compute_confidence,
            compute_actionable_items,
        )

        # Ownership check
        try:
            job = WorkflowJob.objects.only("id").get(id=job_id, user_id=user_id)
        except WorkflowJob.DoesNotExist:
            return None

        try:
            snap = ComponentSnapshot.objects.get(job=job, step=step)
        except ComponentSnapshot.DoesNotExist:
            return None

        # Apply path mutation
        new_payload = apply_patch(snap.payload, path, value)

        # Re-validate via researcher_ai Pydantic models (same gate as views.py)
        try:
            from researcher_ai_portal_app.views import (
                _import_runtime_modules,
                _validate_component_json,
            )
            mods = _import_runtime_modules()
            validated_payload = _validate_component_json(step, new_payload, mods)
        except Exception as exc:
            raise ValueError(f"Validation failed after patch: {exc}") from exc

        snap.payload = validated_payload
        snap.save(update_fields=["payload", "payload_hash"])

        # Rebuild confidence from all components for this job
        all_snaps = ComponentSnapshot.objects.filter(job=job)
        components = {s.step: s.payload for s in all_snaps}
        confidence = compute_confidence(components)
        actionable_items = compute_actionable_items(components, confidence)

        return {
            "step": step,
            "payload": validated_payload,
            "confidence": confidence,
            "actionable_items": actionable_items,
        }

    return await _fetch_and_patch()
