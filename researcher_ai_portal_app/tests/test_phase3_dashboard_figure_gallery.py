from __future__ import annotations

from pathlib import Path


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_phase3_dashboard_view_includes_figure_media_rows():
    views_path = Path(__file__).resolve().parents[1] / "views.py"
    text = _read(views_path)
    assert "figure_media_rows = _figure_media_rows(" in text
    assert '"figure_media_rows": figure_media_rows' in text


def test_phase3_dashboard_template_renders_figure_gallery():
    template_path = Path(__file__).resolve().parents[1] / "templates" / "researcher_ai_portal" / "dashboard.html"
    text = _read(template_path)
    assert "Figure Gallery" in text
    assert "{% if figure_media_rows %}" in text
    assert "{{ entry.proxy_url }}" in text
