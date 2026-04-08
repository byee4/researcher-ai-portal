"""Utilities for generating the default React Flow graph layout.

Two graph generators are provided:

generate_default_graph(components, component_meta)
    Legacy: produces the 6 fixed-step parse-pipeline graph (paper →
    figures → method → datasets → software → pipeline).  Still used as a
    fallback when the software list is empty.

generate_tool_graph(software_list, pipeline_config)
    New: produces one node per software tool returned by the software
    parser, with edges derived from the pipeline config's ``depends_on``
    chains.  This is the graph shown in the visual Pipeline Builder tab.

Layout algorithm (shared)
--------------------------
1. Compute the *tier* (x-column) of each node: the maximum tier of any
   upstream dependency, plus one.  Root nodes get tier 0.
2. Within each tier, stack nodes vertically in discovery order.
3. x = tier × X_STRIDE, y = row_within_tier × Y_STRIDE.

Node types and ports
--------------------
Tool nodes use type ``"tool_node"`` so the React Flow renderer picks
the custom ToolNode component.  Every tool node gets both an input and
output handle so the user can connect them freely.
"""

from __future__ import annotations

import re
from typing import Any

# Mirror of the constants in views.py — kept here to avoid an import from the
# Django view layer inside a pure-utility module.
STEP_ORDER: list[str] = [
    "paper",
    "figures",
    "method",
    "datasets",
    "software",
    "pipeline",
]

STEP_DEPENDENCIES: dict[str, list[str]] = {
    "paper": [],
    "figures": ["paper"],
    "method": ["paper", "figures"],
    "datasets": ["paper", "method"],
    "software": ["method"],
    "pipeline": ["method", "datasets", "software", "figures"],
}

STEP_LABELS: dict[str, str] = {
    "paper": "Paper Parser",
    "figures": "Figure Parser",
    "method": "Methods Parser",
    "datasets": "Dataset Parsers",
    "software": "Software Parser",
    "pipeline": "Pipeline Builder",
}

NODE_TYPE_MAP: dict[str, str] = {
    "paper": "paper_parser",
    "figures": "figure_parser",
    "method": "method_parser",
    "datasets": "datasets_parser",
    "software": "software_parser",
    "pipeline": "pipeline_builder",
}

X_STRIDE = 280   # pixels between tiers
Y_STRIDE = 220   # pixels between nodes within the same tier
TOOL_Y_STRIDE = 260  # slightly more breathing room for tool nodes
GRID_COLS = 4    # columns used by the grid fallback (no edge data available)


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _compute_tiers() -> dict[str, int]:
    """Assign a tier (column index) to every step using Kahn-style BFS."""
    tiers: dict[str, int] = {}
    for step in STEP_ORDER:
        deps = STEP_DEPENDENCIES.get(step, [])
        if not deps:
            tiers[step] = 0
        else:
            tiers[step] = max(tiers.get(d, 0) for d in deps) + 1
    return tiers


def _has_dependents(step: str) -> bool:
    """Return True if any other step lists *step* as a dependency."""
    return any(step in deps for deps in STEP_DEPENDENCIES.values())


def _component_summary(payload: Any) -> dict[str, Any]:
    """Extract a minimal display summary from a parsed component payload."""
    if not payload or not isinstance(payload, dict):
        return {}
    summary: dict[str, Any] = {}
    for key in ("title", "paper_type", "pmid", "doi"):
        if key in payload:
            summary[key] = payload[key]
    if "figure_ids" in payload:
        summary["figure_count"] = len(payload["figure_ids"])
    if isinstance(payload, list):
        summary["count"] = len(payload)
    return summary


def _slugify(name: str, index: int) -> str:
    """Create a stable, URL-safe node ID from a software name."""
    slug = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")
    return slug if slug else f"tool_{index}"


# ---------------------------------------------------------------------------
# Legacy step-based graph (paper → figures → method → … → pipeline)
# ---------------------------------------------------------------------------

def generate_default_graph(
    components: dict[str, Any],
    component_meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a default React Flow WorkflowGraph dict from the parsed job data.

    Parameters
    ----------
    components:
        Dict mapping step name → parsed payload (from ComponentSnapshot).
    component_meta:
        Optional dict mapping step name → {"status": ..., "source": ..., ...}.
        Used to populate the ``status`` field on each node's data.

    Returns a plain dict that is JSON-serialisable and conforms to the
    WorkflowGraph Pydantic schema.
    """
    meta = component_meta or {}
    tiers = _compute_tiers()

    # Group steps by tier so we can assign row positions.
    tier_to_steps: dict[int, list[str]] = {}
    for step in STEP_ORDER:
        tier = tiers[step]
        tier_to_steps.setdefault(tier, []).append(step)

    nodes: list[dict[str, Any]] = []
    for step in STEP_ORDER:
        tier = tiers[step]
        row = tier_to_steps[tier].index(step)

        deps = STEP_DEPENDENCIES.get(step, [])
        ports: list[dict[str, Any]] = []
        if deps:
            ports.append({"id": f"{step}__in", "label": "input", "type": "input"})
        if _has_dependents(step):
            ports.append({"id": f"{step}__out", "label": "output", "type": "output"})

        step_meta = meta.get(step) or {}
        nodes.append(
            {
                "id": step,
                "type": NODE_TYPE_MAP.get(step, step),
                "position": {
                    "x": float(tier * X_STRIDE),
                    "y": float(row * Y_STRIDE),
                },
                "data": {
                    "step": step,
                    "label": STEP_LABELS.get(step, step),
                    "status": step_meta.get("status", "missing"),
                    "source": step_meta.get("source", "none"),
                    "summary": _component_summary(components.get(step)),
                },
                "ports": ports,
            }
        )

    edges: list[dict[str, Any]] = []
    for step, deps in STEP_DEPENDENCIES.items():
        for dep in deps:
            edges.append(
                {
                    "id": f"{dep}--{step}",
                    "source": dep,
                    "target": step,
                    "source_handle": f"{dep}__out",
                    "target_handle": f"{step}__in",
                }
            )

    return {
        "nodes": nodes,
        "edges": edges,
        "viewport": {"x": 0.0, "y": 0.0, "zoom": 1.0},
    }


# ---------------------------------------------------------------------------
# Tool-based graph (one node per software tool from the software parser)
# ---------------------------------------------------------------------------

def generate_tool_graph(
    software_list: list[dict[str, Any]],
    pipeline_config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a React Flow graph where each node is a parsed software tool.

    Parameters
    ----------
    software_list:
        List of software dicts from ComponentSnapshot (Software.model_dump).
        Each entry has at minimum a ``name`` key; ``commands``, ``version``,
        ``language``, etc. are used when present.
    pipeline_config:
        Optional parsed pipeline dict (Pipeline.model_dump).  When provided,
        ``config.steps[].depends_on`` is used to derive default edges between
        tool nodes.  The user can always add/remove edges manually.

    Returns a plain dict conforming to the WorkflowGraph Pydantic schema.
    If *software_list* is empty an empty graph is returned — the caller
    should fall back to ``generate_default_graph`` in that case.
    """
    if not software_list:
        return {"nodes": [], "edges": [], "viewport": {"x": 0.0, "y": 0.0, "zoom": 1.0}}

    # ------------------------------------------------------------------
    # 1. Build a stable ID for each software tool and a name→id lookup.
    #
    # node_ids  : lower(name) → node_id for the FIRST occurrence of that
    #             name.  Used by edge-building (depends_on resolution).
    # idx_to_id : index → unique node_id for every tool, including
    #             duplicates.  Used in step 5 to build node dicts.
    # used_ids  : tracks every allocated id so we can suffix-deduplicate
    #             without clobbering the first-occurrence entry in node_ids.
    # ------------------------------------------------------------------
    node_ids:  dict[str, str] = {}   # lower(name) → first-occurrence node_id
    idx_to_id: dict[int, str] = {}   # index       → unique node_id
    used_ids:  set[str]       = set()

    for i, sw in enumerate(software_list):
        name = (sw.get("name") or f"tool_{i}").strip()
        node_id = _slugify(name, i)
        # Deduplicate: if this slug is already taken, append a numeric suffix
        base = node_id
        suffix = 0
        while node_id in used_ids:
            suffix += 1
            node_id = f"{base}_{suffix}"
        used_ids.add(node_id)
        idx_to_id[i] = node_id
        # Only map the FIRST occurrence so edge-building resolves to a real node
        node_ids.setdefault(name.lower(), node_id)

    # ------------------------------------------------------------------
    # 2. Extract pipeline steps for default edges and per-tool metadata.
    # ------------------------------------------------------------------
    pipeline_cfg: dict[str, Any] = {}
    if pipeline_config:
        pipeline_cfg = pipeline_config.get("config") or {}

    steps: list[dict[str, Any]] = pipeline_cfg.get("steps") or []

    # Map step_id → step dict (for depends_on resolution)
    step_by_id: dict[str, dict[str, Any]] = {
        s["step_id"]: s for s in steps if s.get("step_id")
    }
    # Map lower(software name) → pipeline step (for metadata overlay)
    step_by_sw: dict[str, dict[str, Any]] = {}
    for s in steps:
        sw_name = (s.get("software") or "").lower()
        if sw_name:
            step_by_sw[sw_name] = s

    # ------------------------------------------------------------------
    # 3. Build edges from depends_on chains.
    # ------------------------------------------------------------------
    edges: list[dict[str, Any]] = []
    seen_edges: set[str] = set()

    for step in steps:
        target_sw = (step.get("software") or "").lower()
        target_id = node_ids.get(target_sw)
        if not target_id:
            continue

        for dep in (step.get("depends_on") or []):
            # depends_on entries are step_ids; resolve to software name → node id.
            dep_step = step_by_id.get(dep) or {}
            dep_sw = (dep_step.get("software") or dep).lower()
            source_id = node_ids.get(dep_sw)
            if not source_id or source_id == target_id:
                continue
            edge_key = f"{source_id}--{target_id}"
            if edge_key in seen_edges:
                continue
            seen_edges.add(edge_key)
            edges.append(
                {
                    "id": edge_key,
                    "source": source_id,
                    "target": target_id,
                    "source_handle": f"{source_id}__out",
                    "target_handle": f"{target_id}__in",
                }
            )

    # ------------------------------------------------------------------
    # 4. Compute tier (column) for each node using the edge DAG.
    #
    # When no edges were derived (empty pipeline_config / no depends_on
    # chains), every node ends up at tier 0 and would be stacked in a
    # single vertical column — unreadable for large tool lists.  In that
    # case we defer to the grid fallback in step 5 instead.
    # ------------------------------------------------------------------
    all_ids = list(idx_to_id.values())
    use_grid_layout = len(edges) == 0  # no dependency data → grid is clearer

    # Iterative tier resolution; handles cycles gracefully.
    tiers: dict[str, int] = {nid: 0 for nid in all_ids}
    if not use_grid_layout:
        for _ in range(len(all_ids)):      # max passes = n (handles longest chain)
            changed = False
            for edge in edges:
                src, tgt = edge["source"], edge["target"]
                if tiers.get(src, 0) + 1 > tiers.get(tgt, 0):
                    tiers[tgt] = tiers[src] + 1
                    changed = True
            if not changed:
                break

    # Group IDs by tier for row assignment (used only when edges exist).
    tier_rows: dict[int, list[str]] = {}
    for nid in all_ids:
        tier_rows.setdefault(tiers.get(nid, 0), []).append(nid)

    # ------------------------------------------------------------------
    # 5. Build node dicts.
    #
    # Position strategy:
    #   • Edge data available  → tier-based layout (x = tier * X_STRIDE,
    #                            y = row_within_tier * TOOL_Y_STRIDE).
    #   • No edge data (grid)  → wrap into GRID_COLS columns so nodes fill
    #                            the canvas horizontally instead of piling
    #                            up in a single vertical stripe.
    #
    # In both cases, positions are only used as *initial* values.  Once a
    # user drags nodes the client saves their chosen positions via
    # PUT /api/v1/graphs/{job_id}, and those positions are returned
    # verbatim by GET /api/v1/graphs/{job_id} on subsequent loads —
    # the backend never recalculates positions for a stored graph.
    # ------------------------------------------------------------------
    nodes: list[dict[str, Any]] = []
    for i, sw in enumerate(software_list):
        name = (sw.get("name") or f"tool_{i}").strip()
        node_id = idx_to_id[i]  # always unique, even for duplicate tool names

        if use_grid_layout:
            col = i % GRID_COLS
            row_g = i // GRID_COLS
            pos_x = float(col * X_STRIDE)
            pos_y = float(row_g * TOOL_Y_STRIDE)
        else:
            tier = tiers.get(node_id, 0)
            row_list = tier_rows.get(tier, [])
            row = row_list.index(node_id) if node_id in row_list else 0
            pos_x = float(tier * X_STRIDE)
            pos_y = float(row * TOOL_Y_STRIDE)

        commands: list[dict[str, Any]] = sw.get("commands") or []
        first_cmd: dict[str, Any] = commands[0] if commands else {}

        # Prefer values from the pipeline step over the raw software model.
        pip_step = step_by_sw.get(name.lower()) or {}

        nodes.append(
            {
                "id": node_id,
                "type": "tool_node",
                "position": {"x": pos_x, "y": pos_y},
                "data": {
                    # ── Identity ───────────────────────────────────────
                    "name": name,
                    "version": sw.get("version") or "",
                    "description": sw.get("description") or "",
                    "language": sw.get("language") or "",
                    "source_url": sw.get("source_url") or "",
                    # ── Commands (for command-selector dropdown) ────────
                    "commands": commands,
                    "selectedCommandIndex": 0,
                    # ── Editable pipeline metadata ──────────────────────
                    "inputs": (
                        pip_step.get("inputs")
                        or first_cmd.get("required_inputs")
                        or []
                    ),
                    "outputs": (
                        pip_step.get("outputs")
                        or first_cmd.get("outputs")
                        or []
                    ),
                    "parameters": (
                        pip_step.get("parameters")
                        or first_cmd.get("parameters")
                        or {}
                    ),
                    "container": (
                        pip_step.get("container")
                        or (sw.get("environment") or {}).get("docker_image")
                        or ""
                    ),
                    "step_id": pip_step.get("step_id") or node_id,
                },
                "ports": [
                    {"id": f"{node_id}__in",  "label": "input",  "type": "input"},
                    {"id": f"{node_id}__out", "label": "output", "type": "output"},
                ],
            }
        )

    return {
        "nodes": nodes,
        "edges": edges,
        "viewport": {"x": 0.0, "y": 0.0, "zoom": 1.0},
    }
