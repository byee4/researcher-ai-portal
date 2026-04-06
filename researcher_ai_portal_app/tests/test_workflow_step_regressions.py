from __future__ import annotations

from pathlib import Path


def test_workflow_step_template_uses_step_action_not_action_name_collision():
    template_path = (
        Path(__file__).resolve().parents[1]
        / "templates"
        / "researcher_ai_portal"
        / "workflow_step.html"
    )
    text = template_path.read_text(encoding="utf-8")
    assert 'name="step_action"' in text
    assert 'name="action" value="run"' not in text


def test_workflow_step_template_uses_safe_form_action_lookup():
    template_path = (
        Path(__file__).resolve().parents[1]
        / "templates"
        / "researcher_ai_portal"
        / "workflow_step.html"
    )
    text = template_path.read_text(encoding="utf-8")
    assert 'form.getAttribute("action")' in text
    assert "form.action ||" not in text


def test_workflow_view_accepts_step_action_fallback():
    views_path = Path(__file__).resolve().parents[1] / "views.py"
    text = views_path.read_text(encoding="utf-8")
    assert 'request.POST.get("step_action", "")' in text


def test_start_parse_dispatches_async_paper_parser_task():
    views_path = Path(__file__).resolve().parents[1] / "views.py"
    text = views_path.read_text(encoding="utf-8")
    assert "_dispatch_workflow_step(" in text
    assert '_run_step(job_id, "paper")' not in text


def test_pmc_figure_link_builder_uses_f_prefix():
    views_path = Path(__file__).resolve().parents[1] / "views.py"
    text = views_path.read_text(encoding="utf-8")
    assert "def _build_pmc_figure_url" in text
    assert "/figure/F{number}/" in text
    assert "def _candidate_pmc_figure_urls" in text
    assert "Figure{number}" in text
    assert "Fig{number}" in text


def test_supplementary_figures_are_marked_for_future_parser():
    views_path = Path(__file__).resolve().parents[1] / "views.py"
    template_path = (
        Path(__file__).resolve().parents[1]
        / "templates"
        / "researcher_ai_portal"
        / "workflow_step.html"
    )
    views_text = views_path.read_text(encoding="utf-8")
    template_text = template_path.read_text(encoding="utf-8")
    assert "def _split_primary_and_supplementary_figure_ids" in views_text
    assert "Supplemental Figure Parser" in views_text
    assert "supplementary_figure_ids" in template_text
    assert "Supplementary figures" in template_text


def test_preview_panel_is_collapsed_by_default_and_not_side_pane():
    template_path = (
        Path(__file__).resolve().parents[1]
        / "templates"
        / "researcher_ai_portal"
        / "workflow_step.html"
    )
    text = template_path.read_text(encoding="utf-8")
    assert "Figure Image Preview" in text
    assert "<details class=\"accordion\">" in text
    assert "figure-two-pane" not in text


def test_preview_panel_filters_to_expanded_figures_only():
    template_path = (
        Path(__file__).resolve().parents[1]
        / "templates"
        / "researcher_ai_portal"
        / "workflow_step.html"
    )
    text = template_path.read_text(encoding="utf-8")
    assert "data-figure-key" in text
    assert "row.entries" in text
    assert "entry.proxy_url" in text
    assert "data-figure-key" in text
