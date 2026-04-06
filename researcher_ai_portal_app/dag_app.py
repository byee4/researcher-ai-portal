from __future__ import annotations

import re
from dash import Input, Output, dcc, html
from django_plotly_dash import DjangoDash

try:  # pragma: no cover - optional dependency
    import dash_cytoscape as cyto
except Exception:  # pragma: no cover - allow local MVP without cytoscape package
    cyto = None


_DAG_APPS: dict[str, DjangoDash] = {}
_URL_RE = re.compile(r"https?://[^\s<>'\"()]+", re.IGNORECASE)


def _extract_urls(text: str) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for match in _URL_RE.finditer(text or ""):
        url = match.group(0).rstrip(".,);")
        if url not in seen:
            seen.add(url)
            out.append(url)
    return out


def _build_elements(method_json: dict, pipeline_json: dict, confidence: dict) -> list[dict]:
    assay_graph = (method_json or {}).get("assay_graph") or {}
    assays = assay_graph.get("assays") or []
    dependencies = assay_graph.get("dependencies") or []
    assay_conf = (confidence or {}).get("assay_confidences") or {}
    pipeline_config = (pipeline_json or {}).get("config") or {}
    nf_core_name = str(pipeline_config.get("nf_core_pipeline") or "").strip()
    nf_core_version = str(pipeline_config.get("nf_core_version") or "").strip()
    code_urls = _extract_urls(str((method_json or {}).get("code_availability") or ""))
    github_urls = [url for url in code_urls if "github.com/" in url.lower()]

    elements: list[dict] = []
    for assay in assays:
        name = str(assay.get("name") or "Unknown Assay")
        overall = float(((assay_conf.get(name) or {}).get("overall")) or 50.0)
        color = "#16a34a" if overall >= 80 else "#ca8a04" if overall >= 50 else "#dc2626"
        elements.append(
            {
                "data": {
                    "id": name,
                    "label": name,
                    "confidence": round(overall, 1),
                    "software": ", ".join(
                        [
                            str(step.get("software") or "")
                            for step in (assay.get("steps") or [])
                            if str(step.get("software") or "").strip()
                        ][:3]
                    ),
                    "figures": ", ".join((assay.get("figures_produced") or [])[:4]),
                    "color": color,
                    "description": str(assay.get("description") or ""),
                    "category": str(assay.get("method_category") or "unknown"),
                    "step_count": len(assay.get("steps") or []),
                    "nf_core": f"{nf_core_name} {nf_core_version}".strip(),
                    "github_links": "\n".join(github_urls[:3]),
                }
            }
        )

    for dep in dependencies:
        up = str(dep.get("upstream_assay") or "")
        down = str(dep.get("downstream_assay") or "")
        if not up or not down:
            continue
        dep_type = str(dep.get("dependency_type") or "")
        elements.append(
            {
                "data": {
                    "source": up,
                    "target": down,
                    "label": dep_type,
                    "line_style": "dashed"
                    if dep_type in ("normalization_reference", "co-analysis")
                    else "solid",
                }
            }
        )

    return elements


def build_dag_app(
    job_id: str,
    method_json: dict,
    figures_json: list,  # noqa: ARG001 - reserved for phase 3 figure gallery
    datasets_json: list,  # noqa: ARG001 - reserved for phase 2 confidence enrichments
    pipeline_json: dict,
    confidence: dict,
) -> str:
    """Create (or reuse) a per-job DAG app for assay dependencies."""
    app_name = f"researcher_ai_dag_{job_id}"
    if app_name in _DAG_APPS:
        return app_name

    if cyto is not None:
        try:  # pragma: no cover - depends on optional dash-cytoscape runtime
            cyto.load_extra_layouts()
        except Exception:
            # Keep app functional with default layouts if extension loading fails.
            pass

    elements = _build_elements(method_json, pipeline_json, confidence)
    app = DjangoDash(app_name)

    if cyto is None:
        app.layout = html.Div(
            [
                html.H3("Workflow Graph"),
                html.P(
                    "dash-cytoscape is not installed in this environment. "
                    "Install it to enable the interactive DAG canvas."
                ),
                html.P(f"Assays detected: {len([e for e in elements if 'id' in e.get('data', {})])}"),
            ]
        )
        _DAG_APPS[app_name] = app
        return app_name

    stylesheet = [
        {
            "selector": "node",
            "style": {
                "label": "data(label)",
                "background-color": "data(color)",
                "color": "#1e293b",
                "text-valign": "top",
                "text-halign": "center",
                "font-size": "12px",
                "width": "180px",
                "height": "76px",
                "shape": "roundrectangle",
                "border-width": 2,
                "border-color": "#e2e8f0",
                "text-wrap": "wrap",
                "text-max-width": "160px",
            },
        },
        {
            "selector": "edge",
            "style": {
                "curve-style": "bezier",
                "target-arrow-shape": "triangle",
                "target-arrow-color": "#475569",
                "line-color": "#475569",
                "label": "data(label)",
                "font-size": "10px",
                "text-rotation": "autorotate",
            },
        },
        {
            "selector": "edge[line_style = 'dashed']",
            "style": {
                "line-style": "dashed",
                "line-color": "#94a3b8",
                "target-arrow-color": "#94a3b8",
            },
        },
    ]

    app.layout = html.Div(
        [
            cyto.Cytoscape(
                id="assay-dag",
                elements=elements,
                stylesheet=stylesheet,
                layout={"name": "dagre", "rankDir": "TB", "spacingFactor": 1.35},
                style={"width": "100%", "height": "500px", "border": "1px solid #dbe5ef", "borderRadius": "8px"},
                responsive=True,
            ),
            html.Div(
                id="node-detail",
                style={
                    "display": "none",
                    "marginTop": "12px",
                    "padding": "12px",
                    "border": "1px solid #dbe5ef",
                    "borderRadius": "8px",
                    "backgroundColor": "#fafcff",
                },
            ),
        ]
    )

    @app.callback(
        Output("node-detail", "children"),
        Output("node-detail", "style"),
        Input("assay-dag", "tapNodeData"),
    )
    def show_node_detail(node_data: dict | None):
        base_style = {
            "marginTop": "12px",
            "padding": "12px",
            "border": "1px solid #dbe5ef",
            "borderRadius": "8px",
            "backgroundColor": "#fafcff",
        }
        if not node_data:
            return [], {"display": "none"}
        return [
            html.H4(str(node_data.get("label") or "Unknown Assay")),
            html.Div(f"Category: {node_data.get('category', 'unknown')}"),
            html.Div(f"Confidence: {node_data.get('confidence', 50)}%"),
            html.Div(f"Steps: {node_data.get('step_count', 0)}"),
            html.Div(f"Software: {node_data.get('software', 'unknown')}"),
            html.Div(f"Linked Figures: {node_data.get('figures', 'none')}"),
            html.Div(f"nf-core: {node_data.get('nf_core', 'not detected')}"),
            html.Pre(str(node_data.get("github_links") or "GitHub links: none"), style={"whiteSpace": "pre-wrap"}),
            dcc.Markdown(str(node_data.get("description") or "")),
        ], {**base_style, "display": "block"}

    _DAG_APPS[app_name] = app
    return app_name
