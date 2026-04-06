# researcher-ai-portal

Django portal for running the researcher-ai parsing workflow with per-user jobs, durable ORM state, and async step execution.

## MVP Status (Phase 6)

- Job state moved from in-memory dicts to Django ORM (`WorkflowJob`, `ComponentSnapshot`, `PaperCache`).
- Parse steps run asynchronously via Celery task entrypoints.
- Progress polling reads cache first, then falls back to durable DB snapshots.
- LLM API keys are stored in session and passed to tasks (not persisted in DB).
- Dashboard now includes an assay DAG view (`dash-cytoscape`) rendered through `django-plotly-dash`.
- Confidence scoring is computed from parsed method/figure/dataset/pipeline components and surfaced in dashboard context.
- Dashboard now includes a Figure Gallery section with proxied image previews and source links.
- Structured assay-step editing is available in dashboard for non-JSON users, while raw JSON editing remains for power users.
- DAG node details now surface detected `nf-core` pipeline info and GitHub code links from method metadata.
- Dashboard now includes a DAG-aware rebuild trigger that invalidates and reruns only downstream steps.

## Run Locally (MVP)

```bash
cd /Users/brianyee/Documents/work/01_active/researcher-ai-portal
python -m pip install -r requirements.txt
# Install core package dependency (editable for local co-dev):
# python -m pip install -e /Users/brianyee/Documents/work/01_active/researcher-ai/researcher-ai
# OR install from package index/git source when available.

python manage.py migrate
python manage.py runserver 0.0.0.0:8000
```

Optional async worker (recommended for full Phase 0 behavior):

```bash
cd /Users/brianyee/Documents/work/01_active/researcher-ai-portal
celery -A researcher_ai_portal worker -l info
```

If Redis is unavailable, Django cache falls back to local memory for development.

Optional dependency for full DAG canvas:

```bash
python -m pip install dash-cytoscape
```

## Test

```bash
cd /Users/brianyee/Documents/work/01_active/researcher-ai-portal
python -m pytest researcher_ai_portal_app/tests -q
```

## Tutorial

Full walkthrough: [`docs/TUTORIAL.md`](docs/TUTORIAL.md)
