from __future__ import annotations

from dash import dcc, html
from django_plotly_dash import DjangoDash
import plotly.graph_objects as go

_APPS: dict[str, DjangoDash] = {}


def build_dashboard_app(job_id: str, summary: dict, context: dict) -> str:
    """Create (or reuse) a per-job dash app showing parsed-paper components and gaps."""
    app_name = f"researcher_ai_portal_{job_id}"
    if app_name in _APPS:
        return app_name

    counts_fig = go.Figure(
        data=[
            go.Bar(
                x=["Figures", "Assays", "Datasets", "Software", "Pipeline Steps"],
                y=[
                    summary.get("figure_count", 0),
                    summary.get("assay_count", 0),
                    summary.get("dataset_count", 0),
                    summary.get("software_count", 0),
                    summary.get("pipeline_step_count", 0),
                ],
                marker_color=["#005ea8", "#8a6d3b", "#2c7a4b", "#7b3f7b", "#3b6a8a"],
            )
        ]
    )
    counts_fig.update_layout(
        title="Parsed Elements",
        paper_bgcolor="#f5f7fa",
        plot_bgcolor="#ffffff",
        margin=dict(l=30, r=20, t=50, b=30),
    )

    status_counts = summary.get("status_counts", {})
    status_fig = go.Figure(
        data=[
            go.Bar(
                x=["Found", "Inferred", "Missing"],
                y=[
                    status_counts.get("found", 0),
                    status_counts.get("inferred", 0),
                    status_counts.get("missing", 0),
                ],
                marker_color=["#2f7d31", "#b8741a", "#b42318"],
            )
        ]
    )
    status_fig.update_layout(
        title=f"Component Quality (Paper Type: {summary.get('paper_type', 'unknown')})",
        paper_bgcolor="#f5f7fa",
        plot_bgcolor="#ffffff",
        margin=dict(l=30, r=20, t=50, b=30),
    )

    pipeline_steps = ((context.get("pipeline") or {}).get("config") or {}).get("steps") or []
    if pipeline_steps:
        x = list(range(1, len(pipeline_steps) + 1))
        names = [s.get("step_id", f"step_{i}") for i, s in enumerate(pipeline_steps, start=1)]
        y = [1] * len(names)
        pipeline_fig = go.Figure(
            data=[
                go.Scatter(
                    x=x,
                    y=y,
                    mode="markers+text",
                    text=names,
                    textposition="top center",
                    marker=dict(size=18, color="#005ea8"),
                )
            ]
        )
        for i in range(1, len(x)):
            pipeline_fig.add_shape(
                type="line",
                x0=x[i - 1],
                y0=1,
                x1=x[i],
                y1=1,
                line=dict(color="#95a8bc", width=2),
            )
        pipeline_fig.update_layout(
            title="Pipeline Topology",
            showlegend=False,
            xaxis=dict(visible=False),
            yaxis=dict(visible=False),
            paper_bgcolor="#f5f7fa",
            plot_bgcolor="#ffffff",
            margin=dict(l=20, r=20, t=50, b=20),
        )
    else:
        pipeline_fig = go.Figure()
        pipeline_fig.update_layout(
            title="Pipeline Topology (missing)",
            xaxis=dict(visible=False),
            yaxis=dict(visible=False),
            paper_bgcolor="#f5f7fa",
            plot_bgcolor="#ffffff",
            margin=dict(l=20, r=20, t=50, b=20),
        )

    figures = context.get("figures", [])
    assays = ((context.get("method") or {}).get("assay_graph") or {}).get("assays") or []
    datasets = context.get("datasets", [])
    software = context.get("software", [])

    app = DjangoDash(app_name)
    app.layout = html.Div(
        style={"fontFamily": "Georgia, 'Times New Roman', serif", "padding": "16px"},
        children=[
            html.H2("researcher-ai-portal Dashboard", style={"color": "#17324d"}),
            html.P(context.get("paper", {}).get("title", "Untitled"), style={"fontSize": "1.1rem"}),
            dcc.Graph(figure=counts_fig),
            dcc.Graph(figure=status_fig),
            dcc.Graph(figure=pipeline_fig),
            html.H3("Figure IDs"),
            html.Ul([html.Li(f.get("figure_id", "")) for f in figures[:30]] or [html.Li("None")]),
            html.H3("Assays"),
            html.Ul([html.Li(a.get("name", "")) for a in assays[:30]] or [html.Li("None")]),
            html.H3("Datasets"),
            html.Ul([html.Li(d.get("accession", "")) for d in datasets[:30]] or [html.Li("None")]),
            html.H3("Software"),
            html.Ul([html.Li(s.get("name", "")) for s in software[:30]] or [html.Li("None")]),
        ],
    )
    _APPS[app_name] = app
    return app_name
