"""Tests for Phase 2a: PATCH /api/v1/jobs/{job_id}/components/{step}

Covers the path-parsing / whitelist / apply_patch utilities in repository.py
and verifies the API schema contracts in schemas.py.  No ORM access needed.
"""

from __future__ import annotations

import pytest
from pathlib import Path


# ---------------------------------------------------------------------------
# repository-layer helpers
# ---------------------------------------------------------------------------

from researcher_ai_portal_app.api.repository import (
    _parse_path,
    _path_allowed,
    apply_patch,
)


class TestParsePath:
    def test_simple_key(self):
        assert _parse_path("software_version") == ["software_version"]

    def test_dot_notation(self):
        assert _parse_path("assay_graph.assays") == ["assay_graph", "assays"]

    def test_bracket_index(self):
        assert _parse_path("assays[2].steps[0].software") == [
            "assays", 2, "steps", 0, "software"
        ]

    def test_full_method_path(self):
        tokens = _parse_path("assay_graph.assays[1].steps[0].software_version")
        assert tokens == ["assay_graph", "assays", 1, "steps", 0, "software_version"]

    def test_leading_bare_index(self):
        assert _parse_path("[2].accession") == [2, "accession"]

    def test_leading_index_only_key(self):
        assert _parse_path("[0].version") == [0, "version"]

    def test_invalid_segment_raises(self):
        with pytest.raises(ValueError):
            _parse_path("foo..bar")

    def test_empty_path_raises(self):
        with pytest.raises(ValueError):
            _parse_path("")


class TestPathAllowed:
    def test_method_step_path_allowed(self):
        assert _path_allowed("method", "assay_graph.assays[0].steps[1].software_version")

    def test_method_assay_graph_prefix_allowed(self):
        assert _path_allowed("method", "assay_graph")

    def test_datasets_accession_allowed(self):
        assert _path_allowed("datasets", "[0].accession")

    def test_datasets_organism_allowed(self):
        assert _path_allowed("datasets", "[3].organism")

    def test_datasets_append_row_allowed(self):
        assert _path_allowed("datasets", "[+]")

    def test_datasets_raw_metadata_blocked(self):
        assert not _path_allowed("datasets", "[0].raw_metadata")

    def test_software_version_allowed(self):
        assert _path_allowed("software", "[1].version")

    def test_software_environment_blocked(self):
        assert not _path_allowed("software", "[0].environment")

    def test_pipeline_steps_allowed(self):
        assert _path_allowed("pipeline", "steps[0].name")

    def test_unknown_step_blocked(self):
        assert not _path_allowed("unknown_step", "anything")

    def test_paper_title_allowed(self):
        assert _path_allowed("paper", "title")


class TestApplyPatch:
    def test_patch_dict_field(self):
        payload = {"title": "old", "abstract": "x"}
        result = apply_patch(payload, "title", "new")
        assert result["title"] == "new"
        assert result["abstract"] == "x"  # untouched
        assert payload["title"] == "old"  # original not mutated

    def test_patch_nested_field(self):
        payload = {"assay_graph": {"assays": [{"steps": [{"software": "STAR"}]}]}}
        result = apply_patch(payload, "assay_graph.assays[0].steps[0].software", "HISAT2")
        assert result["assay_graph"]["assays"][0]["steps"][0]["software"] == "HISAT2"

    def test_patch_list_element(self):
        payload = [{"accession": "GSE123"}, {"accession": "GSE456"}]
        result = apply_patch(payload, "[1].accession", "GSE999")
        assert result[1]["accession"] == "GSE999"
        assert result[0]["accession"] == "GSE123"

    def test_patch_creates_missing_key(self):
        payload = {"a": {}}
        result = apply_patch(payload, "a.b", 42)
        assert result["a"]["b"] == 42

    def test_patch_index_out_of_range_raises(self):
        payload = [{"x": 1}]
        with pytest.raises(ValueError, match="out of range"):
            apply_patch(payload, "[5].x", "bad")

    def test_original_not_mutated(self):
        payload = {"name": "original"}
        result = apply_patch(payload, "name", "changed")
        assert payload["name"] == "original"
        assert result["name"] == "changed"


# ---------------------------------------------------------------------------
# schema contracts
# ---------------------------------------------------------------------------

from researcher_ai_portal_app.api.schemas import (
    ComponentPatchRequest,
    ComponentSaveResponse,
)


class TestComponentPatchSchema:
    def test_request_valid(self):
        req = ComponentPatchRequest(
            path="assay_graph.assays[0].steps[0].software_version",
            value="2.1.3",
        )
        assert req.path == "assay_graph.assays[0].steps[0].software_version"
        assert req.value == "2.1.3"

    def test_response_has_required_fields(self):
        resp = ComponentSaveResponse(
            step="method",
            payload={"assay_graph": {}},
            confidence={"overall": 72.5},
            actionable_items=[],
        )
        assert resp.step == "method"
        assert resp.confidence["overall"] == 72.5


# ---------------------------------------------------------------------------
# route registration sanity check
# ---------------------------------------------------------------------------

from pathlib import Path


def test_patch_route_registered():
    routes_path = Path(__file__).resolve().parents[1] / "api" / "routes.py"
    text = routes_path.read_text()
    assert 'router.patch' in text
    assert '"/jobs/{job_id}/components/{step}"' in text
    assert 'ComponentPatchRequest' in text
    assert 'ComponentSaveResponse' in text
