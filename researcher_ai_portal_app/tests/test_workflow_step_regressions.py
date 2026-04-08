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


def test_workflow_step_autopoll_only_when_stage_is_running():
    template_path = (
        Path(__file__).resolve().parents[1]
        / "templates"
        / "researcher_ai_portal"
        / "workflow_step.html"
    )
    text = template_path.read_text(encoding="utf-8")
    assert "const initialStage =" in text
    assert "/^Running\\b/.test(initialStage)" in text


def test_workflow_step_template_shows_live_heartbeat_feedback():
    template_path = (
        Path(__file__).resolve().parents[1]
        / "templates"
        / "researcher_ai_portal"
        / "workflow_step.html"
    )
    text = template_path.read_text(encoding="utf-8")
    assert 'id="step-heartbeat-text"' in text
    assert "Running. Temporary network issue; retrying status check…" in text
    assert "Still running (" in text


def test_figure_parser_updates_stage_before_each_figure_parse():
    views_path = Path(__file__).resolve().parents[1] / "views.py"
    text = views_path.read_text(encoding="utf-8")
    assert 'stage=f"Starting {fig_id} ({idx}/{total})"' in text


def test_workflow_step_template_includes_worker_log_sidebar():
    template_path = (
        Path(__file__).resolve().parents[1]
        / "templates"
        / "researcher_ai_portal"
        / "workflow_step.html"
    )
    text = template_path.read_text(encoding="utf-8")
    assert 'id="worker-log-list"' in text
    assert 'id="worker-log-meta"' in text
    assert "renderLogs(data.logs);" in text


def test_job_status_merges_cached_logs():
    views_path = Path(__file__).resolve().parents[1] / "views.py"
    text = views_path.read_text(encoding="utf-8")
    assert "payload = merge_logs(payload, job_id)" in text


def test_workflow_step_only_builds_figure_media_rows_on_figures_step():
    views_path = Path(__file__).resolve().parents[1] / "views.py"
    text = views_path.read_text(encoding="utf-8")
    assert 'if step == "figures":' in text
    assert "figure_media_rows = _figure_media_rows(figures_for_ui, paper_for_ui, job_id, validate_urls=False)" in text


def test_figure_proxy_writes_and_reads_disk_cache():
    views_path = Path(__file__).resolve().parents[1] / "views.py"
    text = views_path.read_text(encoding="utf-8")
    assert "def _read_cached_figure_proxy_image" in text
    assert "def _write_cached_figure_proxy_image" in text
    assert "cached_image = _read_cached_figure_proxy_image(url)" in text
