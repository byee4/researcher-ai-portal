# researcher-ai-portal

Django portal for running the researcher-ai parsing workflow with per-user jobs, durable ORM state, and serial step execution.

## MVP Status (Phase 6)

- Job state moved from in-memory dicts to Django ORM (`WorkflowJob`, `ComponentSnapshot`, `PaperCache`).
- Parse steps run serially in-process for easier live progress tracking.
- Progress and parser logs are read directly from durable DB snapshots.
- LLM API keys are stored in session and passed to parser calls (not persisted in DB).
- Dashboard now includes an assay DAG view (`dash-cytoscape`) rendered through `django-plotly-dash`.
- Confidence scoring is computed from parsed method/figure/dataset/pipeline components and surfaced in dashboard context.
- Dashboard now includes a Figure Gallery section with proxied image previews and source links.
- Structured assay-step editing is available in dashboard for non-JSON users, while raw JSON editing remains for power users.
- DAG node details now surface detected `nf-core` pipeline info and GitHub code links from method metadata.
- Dashboard now includes a DAG-aware rebuild trigger that invalidates and reruns only downstream steps.

## Run Locally (MVP)

Redis and Celery are not required in serial parser mode.

### Option A: One-command Docker Deploy (Recommended)

```bash
cd /Users/brianyee/Documents/work/01_active/researcher-ai-portal
chmod +x run_portal.sh
./run_portal.sh
```

This launches:
- `db` (Postgres)
- `web` (Django + Gunicorn, serial parser mode)

`run_portal.sh` vendors the local `researcher-ai` package and prebuilds a wheel,
then installs that exact wheel in Docker for faster, more deterministic builds.
Default source path:
`/Users/brianyee/Documents/work/01_active/researcher-ai/researcher-ai`

Override source path if needed:

```bash
RESEARCHER_AI_SRC=/absolute/path/to/researcher-ai ./run_portal.sh
```

Force image rebuild when dependencies change:

```bash
FORCE_BUILD=1 ./run_portal.sh
```

### Option B: Native (Conda) Run

Use the helper scripts:

```bash
cd /Users/brianyee/Documents/work/01_active/researcher-ai-portal
./scripts/setup_portal_local.sh
./scripts/run_portal_local.sh
```

`setup_portal_local.sh` installs portal requirements + local `researcher-ai`, writes `.env` defaults, and runs migrations.
`run_portal_local.sh` runs migrations and launches Django in serial parser mode.

Manual alternative:

```bash
cd /Users/brianyee/Documents/work/01_active/researcher-ai-portal
python -m pip install -r requirements.txt
# Install core package dependency (editable for local co-dev):
# python -m pip install -e /Users/brianyee/Documents/work/01_active/researcher-ai/researcher-ai
# OR install a pinned package release:
# python -m pip install researcher-ai==2.0.0

python manage.py migrate
python manage.py runserver 0.0.0.0:8000
```

Optional dependency for full DAG canvas:

```bash
python -m pip install dash-cytoscape
```

## v2 Runtime Environment

`researcher-ai v2.0.0` supports multiple LLM providers. The portal routes the single UI API key to provider-specific env vars by model prefix:

- `gpt-*`, `chatgpt-*`, `o1-*`, `o3-*`, `o4-*` -> `OPENAI_API_KEY`
- `claude-*` -> `ANTHROPIC_API_KEY`
- `gemini-*` -> `GEMINI_API_KEY`

Optional v2 runtime controls:

- `RESEARCHER_AI_VISION_MODEL` (override default figure multimodal model)
- `RESEARCHER_AI_RAG_MODE` (`per_job` default, `shared` opt-in)
- `RESEARCHER_AI_RAG_BASE_DIR` (base directory for RAG persistence)

## Test

```bash
cd /Users/brianyee/Documents/work/01_active/researcher-ai-portal
python -m pytest researcher_ai_portal_app/tests -q
```

## Tutorial

Full walkthrough: [`docs/TUTORIAL.md`](docs/TUTORIAL.md)
