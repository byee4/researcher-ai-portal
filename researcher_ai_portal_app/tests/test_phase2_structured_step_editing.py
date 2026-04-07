"""Tests for Phase 2c structured step editor.

Phase 2 (2b–2d) of MERGED_UX_FASTAPI_PLAN.md introduces typed form editors
for datasets, software, and pipeline config with PATCH-based debounced
autosave.  Saving happens exclusively through the FastAPI endpoint:

    PATCH /api/v1/jobs/{job_id}/components/{step}
    Body: { "path": "...", "value": ... }

Django form POST handling for structured-step saves was intentionally removed
when Phase 2 landed.  These tests guard against accidental re-introduction of
the legacy POST path.
"""

from __future__ import annotations

from pathlib import Path


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_phase2_dashboard_template_has_structured_step_editor_ui():
    """Phase 2c: step editor uses PATCH autosave (no legacy form POST)."""
    template_path = (
        Path(__file__).resolve().parents[1]
        / "templates"
        / "researcher_ai_portal"
        / "dashboard.html"
    )
    text = _read(template_path)
    assert "Structured Step Editing" in text
    # Phase 2c: autosave via PATCH endpoint, not form POST
    assert "se-field" in text           # autosave CSS class marker
    assert "data-patch-path" in text    # patch path attribute
    assert "patchComponent" in text     # shared PATCH helper
    # Parameters field still present (as textarea with autosave)
    assert "parameters" in text


def test_phase2_legacy_save_structured_step_post_removed():
    """The legacy Django form POST handler for save_structured_step must not exist.

    All structured-step mutations are handled by:
        PATCH /api/v1/jobs/{job_id}/components/method

    The Django view should no longer contain the action == "save_structured_step"
    branch.  If this test fails, dead code has been re-introduced and should be
    removed — the PATCH endpoint is the sole mutation path per the App Island
    architecture in MERGED_UX_FASTAPI_PLAN.md.
    """
    views_path = Path(__file__).resolve().parents[1] / "views.py"
    text = _read(views_path)
    assert 'action == "save_structured_step"' not in text, (
        "Legacy Django form POST handler for save_structured_step found in views.py. "
        "Remove it — structured-step saves must go through the FastAPI PATCH endpoint."
    )
