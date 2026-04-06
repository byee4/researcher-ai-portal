# researcher-ai-portal Tutorial (MVP, Phase 6)

This document consolidates quickstart/tutorial/readme guidance for the Django portal and updates commands to match the current filesystem layout.

## Scope and Inputs

Merged source sets:
- `README_QUICKSTART.md`
- `README_django.md`
- `TUTORIAL.md`

## 1) Environment Setup

From portal repo root:

```bash
cd /Users/brianyee/Documents/work/01_active/researcher-ai-portal
python -m pip install -r requirements.txt
python -m pip install -e /Users/brianyee/Documents/work/01_active/researcher-ai/researcher-ai
python -m pip install dpd-static-support
```

Optional: define Django/env overrides in workspace `.env`.

## 2) Start Django Portal (Phase 0 MVP)

```bash
cd /Users/brianyee/Documents/work/01_active/researcher-ai-portal
python manage.py migrate
python manage.py runserver 0.0.0.0:8000
```

Open: <http://127.0.0.1:8000>

## 2.1) Start Async Worker (Recommended)

```bash
cd /Users/brianyee/Documents/work/01_active/researcher-ai-portal
celery -A researcher_ai_portal worker -l info
```

Notes:
- Redis-backed cache/broker are used when `REDIS_URL` / Celery env vars are configured.
- Local-memory cache fallback is enabled for lightweight local development.

## 3) Run a Parse Job in UI

1. Open home page.
2. Provide PubMed ID or upload PDF.
3. Choose LLM model and API key.
4. Run stepwise workflow (`Paper`, `Figures`, `Method`, `Datasets`, `Software`, `Pipeline`).
5. Optionally edit JSON or inject figure ground truth.
6. Open dashboard for summary charts and quality status.
7. Open the new **Workflow Graph** section in the dashboard to inspect assay dependencies.
8. Review the **End-to-End Pipeline Confidence** score to triage weak extraction areas.
9. Use the **Figure Gallery** panel in dashboard to inspect figure images and linked source URLs.
10. Use **Structured Step Editing** to modify method steps without editing full raw JSON.
11. Click DAG nodes to inspect detected `nf-core` and GitHub code links when available.
12. Use **Rebuild pipeline** to rerun only downstream invalidated steps after edits.

## 4) Useful Endpoints

- Health check: `/healthz/`
- Job status JSON: `/jobs/<job_id>/status/`
- Workflow step UI: `/jobs/<job_id>/workflow/<step>/`
- Dashboard UI: `/jobs/<job_id>/dashboard/`

## 5) Notes on Auth and Config

- Globus/social-auth integration is configured in settings.
- Local dev can still run with standard Django session flow if env values are not fully provisioned.
- `DATABASE_URL` enables Postgres; otherwise SQLite fallback uses `template.db`.

## 6) Test Portal Behavior

```bash
cd /Users/brianyee/Documents/work/01_active/researcher-ai-portal
python -m pytest researcher_ai_portal_app/tests
```

Optional install for interactive DAG canvas:

```bash
python -m pip install dash-cytoscape
```

## Accuracy Notes (MVP, Phase 6)

- Correct Django working directory is `researcher-ai-portal`.
- `manage.py` commands should not be run under `researcher_ai`.
- Portal now uses ORM-backed job storage and async task entrypoints (`run_workflow_step`, `rebuild_from_step`).
- Dashboard includes a DAG app endpoint (`researcher_ai_portal_app/dag_app.py`) and template embedding for dependency visualization.
- Confidence is computed via `researcher_ai_portal_app/confidence.py` and injected into dashboard context for UI display and DAG coloring inputs.
- Figure previews in dashboard reuse proxy-backed image resolution (`figure_image_proxy`) via `figure_media_rows`.
- Dashboard supports structured method-step edits via `action=save_structured_step` while keeping the raw JSON editor for advanced use.
- DAG details enrich assay nodes with `nf_core` and GitHub URLs parsed from pipeline/method metadata.
- Dashboard rebuild action (`action=rebuild_pipeline`) dispatches async DAG-aware rebuilds via `rebuild_from_step`.
