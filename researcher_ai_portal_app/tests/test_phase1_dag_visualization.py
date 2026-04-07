"""DAG integrity and schema tests for the React Flow graph layout generators.

Covers the concerns raised in the Phase 1 technical review:
  1. GraphNode.data is Dict[str, Any] — accepts arbitrary future fields.
  2. Edge source/target IDs always reference valid node IDs (React Flow
     silently breaks when an edge references a missing node).
  3. No duplicate node IDs within a generated graph.
  4. generate_tool_graph uses a grid layout (not a single column) when no
     pipeline_config / depends_on edges are available.
  5. WorkflowGraph Pydantic schema round-trips without validation errors.
"""
from __future__ import annotations

import pytest

from researcher_ai_portal_app.api.graph_layout import (
    GRID_COLS,
    generate_default_graph,
    generate_tool_graph,
)
from researcher_ai_portal_app.api.schemas import WorkflowGraph


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _assert_edge_integrity(graph: dict) -> None:
    """Every edge source and target must reference an existing node id.

    React Flow renders silently broken pipelines (missing handles, phantom
    connections) when this invariant is violated.
    """
    node_ids = {n["id"] for n in graph.get("nodes", [])}
    for edge in graph.get("edges", []):
        assert edge["source"] in node_ids, (
            f"Edge '{edge['id']}' source '{edge['source']}' "
            f"is not in node ids: {sorted(node_ids)}"
        )
        assert edge["target"] in node_ids, (
            f"Edge '{edge['id']}' target '{edge['target']}' "
            f"is not in node ids: {sorted(node_ids)}"
        )


def _assert_unique_ids(graph: dict) -> None:
    """Node IDs must be unique — React Flow keys its internal state on them."""
    node_ids = [n["id"] for n in graph.get("nodes", [])]
    duplicates = [nid for nid in set(node_ids) if node_ids.count(nid) > 1]
    assert not duplicates, f"Duplicate node IDs found: {duplicates}"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

SAMPLE_TOOLS = [
    {"name": "STAR",   "version": "2.7.10a", "language": "c++"},
    {"name": "HTSeq",  "version": "2.0.3",   "language": "python"},
    {"name": "DESeq2", "version": "1.38.0",  "language": "r"},
]

SAMPLE_PIPELINE = {
    "config": {
        "steps": [
            {"step_id": "align", "software": "STAR",   "depends_on": []},
            {"step_id": "count", "software": "HTSeq",  "depends_on": ["align"]},
            {"step_id": "de",    "software": "DESeq2", "depends_on": ["count"]},
        ]
    }
}


# ---------------------------------------------------------------------------
# generate_default_graph — 6-step fixed parse pipeline
# ---------------------------------------------------------------------------


class TestDefaultGraph:
    def test_edge_integrity(self):
        graph = generate_default_graph({})
        _assert_edge_integrity(graph)

    def test_unique_node_ids(self):
        graph = generate_default_graph({})
        _assert_unique_ids(graph)

    def test_six_fixed_nodes(self):
        graph = generate_default_graph({})
        assert len(graph["nodes"]) == 6

    def test_all_steps_present(self):
        graph = generate_default_graph({})
        ids = {n["id"] for n in graph["nodes"]}
        assert ids == {"paper", "figures", "method", "datasets", "software", "pipeline"}

    def test_schema_round_trip(self):
        graph = generate_default_graph({"paper": {"title": "Test paper"}})
        wg = WorkflowGraph.model_validate(graph)
        assert len(wg.nodes) == 6
        assert len(wg.edges) > 0

    def test_node_data_accepts_arbitrary_fields(self):
        """GraphNode.data is Dict[str, Any] — must not reject unknown keys."""
        graph = generate_default_graph({})
        node_dict = graph["nodes"][0]
        # Inject Phase 2+ UI fields that don't exist yet
        node_dict["data"]["react_flow_selected"] = True
        node_dict["data"]["confidence_score"] = 0.82
        node_dict["data"]["warnings"] = ["missing version"]
        # Must not raise a Pydantic ValidationError
        wg = WorkflowGraph.model_validate(
            {"nodes": [node_dict], "edges": [], "viewport": {"x": 0, "y": 0, "zoom": 1}}
        )
        assert wg.nodes[0].data["confidence_score"] == 0.82

    def test_positions_are_spread_across_tiers(self):
        """Tier-based layout must produce multiple distinct x positions."""
        graph = generate_default_graph({})
        xs = {n["position"]["x"] for n in graph["nodes"]}
        assert len(xs) > 1, "All default-graph nodes share the same x position"


# ---------------------------------------------------------------------------
# generate_tool_graph — dynamic DAG of software tools
# ---------------------------------------------------------------------------


class TestToolGraph:

    # -- Edge / node integrity -----------------------------------------------

    def test_edge_integrity_with_pipeline_config(self):
        graph = generate_tool_graph(SAMPLE_TOOLS, SAMPLE_PIPELINE)
        _assert_edge_integrity(graph)

    def test_edge_integrity_without_pipeline_config(self):
        """Without config there are no edges, so the invariant holds trivially."""
        graph = generate_tool_graph(SAMPLE_TOOLS, None)
        _assert_edge_integrity(graph)

    def test_unique_node_ids_linear_chain(self):
        graph = generate_tool_graph(SAMPLE_TOOLS, SAMPLE_PIPELINE)
        _assert_unique_ids(graph)

    def test_unique_node_ids_no_config(self):
        graph = generate_tool_graph(SAMPLE_TOOLS, None)
        _assert_unique_ids(graph)

    def test_duplicate_tool_names_get_unique_ids(self):
        tools = [{"name": "BWA"}, {"name": "BWA"}, {"name": "BWA"}]
        graph = generate_tool_graph(tools, None)
        _assert_unique_ids(graph)
        assert len(graph["nodes"]) == 3

    # -- Node / edge counts --------------------------------------------------

    def test_three_nodes_for_three_tools(self):
        graph = generate_tool_graph(SAMPLE_TOOLS, SAMPLE_PIPELINE)
        assert len(graph["nodes"]) == 3

    def test_two_edges_for_linear_chain(self):
        graph = generate_tool_graph(SAMPLE_TOOLS, SAMPLE_PIPELINE)
        # STAR → HTSeq → DESeq2 = 2 edges
        assert len(graph["edges"]) == 2

    def test_no_edges_without_pipeline_config(self):
        graph = generate_tool_graph(SAMPLE_TOOLS, None)
        assert graph["edges"] == []

    def test_empty_tools_returns_empty_graph(self):
        graph = generate_tool_graph([], None)
        assert graph["nodes"] == []
        assert graph["edges"] == []

    # -- Layout: grid vs tier ------------------------------------------------

    def test_grid_layout_when_no_edges(self):
        """Without pipeline_config, nodes must spread across multiple x columns.

        Previously all nodes ended up at x=0 (tier 0) and were stacked in a
        single unreadable column.  The grid fallback uses GRID_COLS columns.
        """
        tools = [{"name": f"Tool{i}"} for i in range(GRID_COLS + 1)]
        graph = generate_tool_graph(tools, None)
        xs = {n["position"]["x"] for n in graph["nodes"]}
        assert len(xs) > 1, (
            f"All {len(tools)} nodes are at the same x position — "
            "grid layout not applied when there are no edges"
        )

    def test_grid_wraps_at_grid_cols_boundary(self):
        """First GRID_COLS tools should occupy exactly GRID_COLS distinct x values."""
        tools = [{"name": f"Tool{i}"} for i in range(GRID_COLS)]
        graph = generate_tool_graph(tools, None)
        xs = sorted(n["position"]["x"] for n in graph["nodes"])
        assert len(set(xs)) == GRID_COLS

    def test_tier_layout_when_edges_present(self):
        """Linear depends_on chain → each tool in a distinct x tier."""
        graph = generate_tool_graph(SAMPLE_TOOLS, SAMPLE_PIPELINE)
        xs = sorted(n["position"]["x"] for n in graph["nodes"])
        # STAR (tier 0), HTSeq (tier 1), DESeq2 (tier 2) → 3 distinct x values
        assert len(set(xs)) == 3, (
            f"Expected 3 distinct x tiers for a linear chain, got: {set(xs)}"
        )

    # -- Pydantic schema round-trips -----------------------------------------

    def test_schema_round_trip_with_config(self):
        graph = generate_tool_graph(SAMPLE_TOOLS, SAMPLE_PIPELINE)
        wg = WorkflowGraph.model_validate(graph)
        assert len(wg.nodes) == 3
        assert len(wg.edges) == 2

    def test_schema_round_trip_without_config(self):
        graph = generate_tool_graph(SAMPLE_TOOLS, None)
        wg = WorkflowGraph.model_validate(graph)
        assert len(wg.nodes) == 3

    def test_data_accepts_phase2_fields(self):
        """GraphNode.data must accept confidence scores, warnings etc injected later."""
        graph = generate_tool_graph(SAMPLE_TOOLS, SAMPLE_PIPELINE)
        for node in graph["nodes"]:
            node["data"]["confidence_score"] = 0.75
            node["data"]["warnings"] = ["missing version"]
        wg = WorkflowGraph.model_validate(graph)
        assert all(n.data["confidence_score"] == 0.75 for n in wg.nodes)


# ---------------------------------------------------------------------------
# WorkflowGraph schema — edge cases
# ---------------------------------------------------------------------------


class TestWorkflowGraphSchema:

    def test_accepts_unknown_node_data_fields(self):
        """Dict[str, Any] on data must allow any keys without ValidationError."""
        graph = {
            "nodes": [{
                "id": "star",
                "type": "tool_node",
                "position": {"x": 0.0, "y": 0.0},
                "data": {
                    "name": "STAR",
                    "react_flow_internal": True,
                    "phase3_nested": {"key": [1, 2, 3]},
                },
            }],
            "edges": [],
            "viewport": {"x": 0.0, "y": 0.0, "zoom": 1.0},
        }
        wg = WorkflowGraph.model_validate(graph)
        assert wg.nodes[0].data["react_flow_internal"] is True
        assert wg.nodes[0].data["phase3_nested"] == {"key": [1, 2, 3]}

    def test_pydantic_does_not_enforce_referential_integrity(self):
        """Pydantic only validates field types — edge→node integrity is our job."""
        graph = {
            "nodes": [{"id": "n1", "type": "tool_node",
                        "position": {"x": 0, "y": 0}, "data": {}}],
            "edges": [{"id": "e1", "source": "GHOST", "target": "n1"}],
            "viewport": {"x": 0, "y": 0, "zoom": 1},
        }
        # Pydantic must not raise — our _assert_edge_integrity helper catches this
        wg = WorkflowGraph.model_validate(graph)
        assert wg.edges[0].source == "GHOST"

    def test_viewport_defaults_to_origin(self):
        graph = generate_default_graph({})
        wg = WorkflowGraph.model_validate(graph)
        assert wg.viewport == {"x": 0.0, "y": 0.0, "zoom": 1.0}
