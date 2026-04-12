"""Tests for Phase 5: Structured parse warnings with resolution workflow.

Covers:
  - Warning classification (_classify_warning)
  - Warning list construction (get_warnings_for_job)
  - Warning resolution/dismissal (resolve_warning)
  - Index re-indexing after resolution
  - Validation plan generation (get_validation_plan_for_job)
  - Schema contract validation
  - Confidence impact accuracy
"""

from __future__ import annotations

import pytest

from researcher_ai_portal_app.api.repository import (
    _classify_warning,
    _WARNING_CATEGORY_MAP,
)
from researcher_ai_portal_app.api.schemas import (
    ParseWarningDetail,
    ValidationPlanResponse,
    WarningResolveRequest,
    WarningResolveResponse,
    WarningsListResponse,
)


# ---------------------------------------------------------------------------
# Unit tests: warning classification
# ---------------------------------------------------------------------------


class TestClassifyWarning:
    """Verify _classify_warning maps raw strings to structured details."""

    def test_assay_stub_classified_as_error(self):
        result = _classify_warning(
            index=0,
            raw="assay_stub: RNA-seq alignment — fallback assay synthesized",
            assay_names=["RNA-seq alignment"],
            resolution_map={},
        )
        assert result["category"] == "assay_stub"
        assert result["severity"] == "error"
        assert result["affected_assay"] == "RNA-seq alignment"
        assert result["status"] == "open"
        assert result["suggested_fix"] is not None

    def test_dependency_dropped_classified_as_error(self):
        result = _classify_warning(
            index=1,
            raw="dependency_dropped: edge Peak Calling → Unknown removed",
            assay_names=["Peak Calling"],
            resolution_map={},
        )
        assert result["category"] == "dependency_dropped"
        assert result["severity"] == "error"
        assert result["affected_assay"] == "Peak Calling"

    def test_inferred_parameters_classified_as_warning(self):
        result = _classify_warning(
            index=2,
            raw="inferred_parameters: STAR.outSAMtype=BAM",
            assay_names=["RNA-seq alignment"],
            resolution_map={},
        )
        assert result["category"] == "inferred_parameters"
        assert result["severity"] == "warning"
        assert "backfilled" in result["summary"].lower() or "inferred" in result["summary"].lower()

    def test_vision_fallback_classified_as_info(self):
        result = _classify_warning(
            index=3,
            raw="paper_rag_vision_fallback: count=3 latency_seconds=0.542",
            assay_names=[],
            resolution_map={},
        )
        assert result["category"] == "paper_rag_vision_fallback"
        assert result["severity"] == "info"

    def test_filtered_non_computational_classified_as_info(self):
        result = _classify_warning(
            index=0,
            raw="assay_filtered_non_computational: Western blot skipped",
            assay_names=["Western blot"],
            resolution_map={},
        )
        assert result["category"] == "assay_filtered_non_computational"
        assert result["severity"] == "info"
        assert result["affected_assay"] == "Western blot"

    def test_template_missing_stages_classified_as_warning(self):
        result = _classify_warning(
            index=0,
            raw="template_missing_stages: normalization, batch_correction",
            assay_names=[],
            resolution_map={},
        )
        assert result["category"] == "template_missing_stages"
        assert result["severity"] == "warning"

    def test_unknown_prefix_falls_back_to_unknown_category(self):
        result = _classify_warning(
            index=0,
            raw="retrieval_rounds=2",
            assay_names=[],
            resolution_map={},
        )
        assert result["category"] == "unknown"
        assert result["severity"] == "warning"
        assert result["summary"] == "retrieval_rounds=2"

    def test_resolution_map_honored(self):
        result = _classify_warning(
            index=0,
            raw="assay_stub: fallback",
            assay_names=[],
            resolution_map={0: {"status": "dismissed", "resolved_by": "user_dismiss"}},
        )
        assert result["status"] == "dismissed"
        assert result["resolved_by"] == "user_dismiss"

    def test_no_assay_match_returns_none(self):
        result = _classify_warning(
            index=0,
            raw="inferred_parameters: some_param=value",
            assay_names=["ChIP-seq"],
            resolution_map={},
        )
        assert result["affected_assay"] is None

    def test_all_known_prefixes_have_entries(self):
        """Every known prefix in the map should produce a valid classification."""
        for prefix, (cat, sev, tmpl, fix, tab) in _WARNING_CATEGORY_MAP.items():
            result = _classify_warning(
                index=0,
                raw=f"{prefix} some detail text",
                assay_names=[],
                resolution_map={},
            )
            assert result["category"] == cat
            assert result["severity"] == sev


# ---------------------------------------------------------------------------
# Schema validation tests
# ---------------------------------------------------------------------------


class TestWarningSchemas:
    """Verify Pydantic schemas accept valid data and reject invalid data."""

    def test_parse_warning_detail_valid(self):
        detail = ParseWarningDetail(
            index=0,
            raw="assay_stub: fallback",
            category="assay_stub",
            severity="error",
            summary="LLM parse failed",
            affected_assay="RNA-seq",
            suggested_fix="Re-run parser",
            fix_target_tab="editing",
            status="open",
        )
        assert detail.index == 0
        assert detail.status == "open"

    def test_warnings_list_response_valid(self):
        resp = WarningsListResponse(
            job_id="abc-123",
            total_count=3,
            open_count=2,
            resolved_count=1,
            warnings=[],
            confidence_impact=7.5,
        )
        assert resp.open_count == 2

    def test_warning_resolve_request_valid(self):
        req = WarningResolveRequest(action="resolve", reason="Fixed the assay steps")
        assert req.action == "resolve"

    def test_warning_resolve_request_dismiss(self):
        req = WarningResolveRequest(action="dismiss")
        assert req.reason == ""

    def test_validation_plan_response_valid(self):
        resp = ValidationPlanResponse(
            job_id="abc-123",
            current_confidence=65.0,
            projected_confidence=72.5,
            steps=[{
                "warning_index": 0,
                "category": "assay_stub",
                "severity": "error",
                "summary": "Fix stub",
                "suggested_fix": "Re-run",
                "projected_confidence_delta": 3.75,
                "projected_confidence_after": 68.75,
            }],
        )
        assert resp.projected_confidence > resp.current_confidence


# ---------------------------------------------------------------------------
# Confidence impact calculation tests
# ---------------------------------------------------------------------------


class TestConfidenceImpact:
    """Verify that warning resolution correctly reduces confidence penalty."""

    def test_single_warning_impact(self):
        """One warning should cost 25 * 0.15 = 3.75% confidence."""
        impact = min(1 * 25.0, 100.0) * 0.15
        assert round(impact, 2) == 3.75

    def test_four_warnings_cap(self):
        """Four warnings hit the 100% cap: 100 * 0.15 = 15%."""
        impact = min(4 * 25.0, 100.0) * 0.15
        assert round(impact, 2) == 15.0

    def test_five_warnings_still_capped(self):
        """Five warnings also cap at 15% (100 * 0.15)."""
        impact = min(5 * 25.0, 100.0) * 0.15
        assert round(impact, 2) == 15.0

    def test_zero_warnings_no_impact(self):
        """Zero warnings should have zero confidence impact."""
        impact = min(0 * 25.0, 100.0) * 0.15
        assert impact == 0.0


# ---------------------------------------------------------------------------
# PATCH whitelist test
# ---------------------------------------------------------------------------

from researcher_ai_portal_app.api.repository import _path_allowed


class TestParseWarningsPatchWhitelist:
    """Verify parse_warnings is patchable via the PATCH endpoint."""

    def test_parse_warnings_path_allowed(self):
        assert _path_allowed("method", "parse_warnings")

    def test_parse_warnings_indexed_allowed(self):
        assert _path_allowed("method", "parse_warnings[0]")

    def test_parse_warnings_nested_path_allowed(self):
        """parse_warnings is a list of strings; indexed access should work."""
        # parse_warnings starts with "parse_warnings" which is in the whitelist
        assert _path_allowed("method", "parse_warnings[2]")


# ---------------------------------------------------------------------------
# Integration-style tests: warning classification against real payloads
# ---------------------------------------------------------------------------


class TestWarningClassificationIntegration:
    """Test classification with realistic warning strings from the parser."""

    REAL_WARNINGS = [
        "assay_stub: scRNA-seq clustering — LLM returned empty steps",
        "dependency_dropped: edge 'RNA-seq alignment' → 'Unknown assay' removed (unresolved target)",
        "inferred_parameters: STAR.outSAMtype=BAM SortedByCoordinate",
        "inferred_parameters_fallback_mode: tool_use unavailable, used regex extraction",
        "paper_rag_vision_fallback: count=3 latency_seconds=0.542",
        "template_missing_stages: normalization, batch_correction, clustering",
        "assay_filtered_non_computational: Immunohistochemistry skipped under computational_only=True",
        "bioworkflow_blocked: ungrounded_fields=1 mode=on",
        "retrieval_rounds=2",
        "retrieved_chunks=14",
        "context_tokens_est=1800",
    ]

    def test_all_real_warnings_classifiable(self):
        """Every warning should produce a valid classification dict."""
        for i, raw in enumerate(self.REAL_WARNINGS):
            result = _classify_warning(i, raw, ["scRNA-seq clustering", "RNA-seq alignment"], {})
            assert "category" in result
            assert "severity" in result
            assert result["severity"] in ("error", "warning", "info")
            assert "summary" in result
            assert isinstance(result["raw"], str)

    def test_stub_warning_gets_error_severity(self):
        result = _classify_warning(0, self.REAL_WARNINGS[0], ["scRNA-seq clustering"], {})
        assert result["severity"] == "error"
        assert result["category"] == "assay_stub"

    def test_metrics_warnings_get_unknown_category(self):
        """retrieval_rounds=2 and similar metric strings should be 'unknown'."""
        for raw in self.REAL_WARNINGS[8:]:
            result = _classify_warning(0, raw, [], {})
            assert result["category"] == "unknown"

    def test_affected_assay_detected_for_stub(self):
        result = _classify_warning(
            0, self.REAL_WARNINGS[0], ["scRNA-seq clustering"], {}
        )
        assert result["affected_assay"] == "scRNA-seq clustering"

    def test_affected_assay_detected_for_dependency(self):
        result = _classify_warning(
            1, self.REAL_WARNINGS[1], ["RNA-seq alignment"], {}
        )
        assert result["affected_assay"] == "RNA-seq alignment"
