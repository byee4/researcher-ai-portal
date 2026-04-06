from __future__ import annotations

from pathlib import Path

from researcher_ai_portal_app.views import invalidated_steps


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_phase6_invalidated_steps_keeps_upstream_nodes_clean():
    dirty = invalidated_steps({}, "method")
    assert dirty == ["datasets", "software", "pipeline"]
    dirty2 = invalidated_steps({}, "software")
    assert dirty2 == ["pipeline"]


def test_phase6_dashboard_rebuild_action_wired():
    views_path = Path(__file__).resolve().parents[1] / "views.py"
    template_path = Path(__file__).resolve().parents[1] / "templates" / "researcher_ai_portal" / "dashboard.html"
    views_text = _read(views_path)
    template_text = _read(template_path)
    assert 'if action == "rebuild_pipeline":' in views_text
    assert "_dispatch_rebuild(" in views_text
    assert '"rebuild_steps": invalidated_steps(' in views_text
    assert 'name="action" value="rebuild_pipeline"' in template_text
    assert "Rebuild pipeline" in template_text
