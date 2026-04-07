from __future__ import annotations

from pathlib import Path


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_phase4_dashboard_handles_structured_step_save_action():
    views_path = Path(__file__).resolve().parents[1] / "views.py"
    text = _read(views_path)
    assert 'if action == "save_structured_step":' in text
    assert 'error=f"Structured step edit failed:' in text
    assert "_persist_component(job_id, \"method\", validated, \"corrected_structured_dashboard\")" in text


def test_phase4_dashboard_template_has_structured_step_editor_ui():
    """Phase 2c: step editor uses PATCH autosave (no legacy form POST)."""
    template_path = Path(__file__).resolve().parents[1] / "templates" / "researcher_ai_portal" / "dashboard.html"
    text = _read(template_path)
    assert "Structured Step Editing" in text
    # Phase 2c: autosave via PATCH endpoint, not form POST
    assert "se-field" in text                    # autosave CSS class marker
    assert "data-patch-path" in text             # patch path attribute
    assert "patchComponent" in text              # shared PATCH helper
    # Parameters field still present (as textarea with autosave)
    assert "parameters" in text
