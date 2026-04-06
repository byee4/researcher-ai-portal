from __future__ import annotations

from pathlib import Path

from researcher_ai_portal_app.dag_app import build_dag_app


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_phase1_dag_app_has_cytoscape_layout_and_fallback():
    dag_path = Path(__file__).resolve().parents[1] / "dag_app.py"
    text = _read(dag_path)
    assert "import dash_cytoscape as cyto" in text
    assert "cyto.load_extra_layouts()" in text
    assert "dash-cytoscape is not installed" in text
    assert 'layout={"name": "dagre"' in text
    assert 'id="assay-dag"' in text


def test_phase1_dashboard_wires_dag_app_name_into_template_context():
    views_path = Path(__file__).resolve().parents[1] / "views.py"
    template_path = Path(__file__).resolve().parents[1] / "templates" / "researcher_ai_portal" / "dashboard.html"
    views_text = _read(views_path)
    template_text = _read(template_path)
    assert "build_dag_app(" in views_text
    assert '"dag_app_name": dag_app_name' in views_text
    assert "{% plotly_app name=dag_app_name" in template_text


def test_phase1_build_dag_app_reuses_cached_app_instance():
    method = {
        "assay_graph": {
            "assays": [{"name": "RNA-seq", "steps": [{"software": "STAR"}], "figures_produced": ["Figure 1"]}],
            "dependencies": [],
        }
    }
    name1 = build_dag_app("job-phase1-test", method, [], [], {}, confidence={})
    name2 = build_dag_app("job-phase1-test", method, [], [], {}, confidence={})
    assert name1 == "researcher_ai_dag_job-phase1-test"
    assert name1 == name2
