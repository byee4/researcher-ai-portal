# Architecture

This document describes the current architecture of `researcher-ai-portal`, including the Phase 1 FastAPI integration.

---

## System overview

`researcher-ai-portal` is a Django 5.2+ application that wraps the `researcher-ai` parsing library. It accepts a PubMed ID or PDF, runs a multi-step LLM-powered extraction pipeline, and stores the results in a PostgreSQL (or SQLite) database. A FastAPI sub-application mounted at `/api/v1/` provides the async JSON API layer for the upcoming visual pipeline builder.

### Request routing

All traffic enters through a single ASGI process. A path-based router in `researcher_ai_portal/asgi.py` dispatches requests:

```
/api/v1/*   →  FastAPI    (async JSON API, visual builder, Phase 1+)
/*          →  Django     (HTML views, Dash apps, Globus auth, static files)
```

Django is the outer ASGI application, so its full middleware stack — WhiteNoise static serving, session management, CSRF protection, Globus auth exception handling, `django-plotly-dash` — applies to all Django-bound requests without modification.

---

## Component inventory

### `researcher_ai_portal/` — Django project

| File | Role |
|------|------|
| `settings.py` | Environment-driven configuration: database, auth backends, middleware, static files, caching, Globus OAuth |
| `urls.py` | URL router for all Django views and third-party app URLs |
| `asgi.py` | Top-level ASGI entry point; routes `/api/v1/*` to FastAPI, everything else to Django |
| `wsgi.py` | Legacy WSGI entry point (retained for tooling compatibility; not used in production) |
| `celery.py` | Optional Celery application; falls back to a no-op dummy when Celery is not installed |

### `researcher_ai_portal_app/` — Main Django app

#### Models (`models.py`)

Three ORM models form the persistent state layer:

**`WorkflowJob`** — one record per parse submission.
- UUID primary key, foreign key to User (per-user isolation).
- Status lifecycle: `queued` → `in_progress` → `completed` / `failed`.
- Progress metadata: 0–100%, current step, stage description.
- LLM model name (not the key — keys are session-encrypted and never written to DB).
- Append-only parse log array (`parse_logs` JSONField).
- Figure parse counters (`figure_parse_total`, `figure_parse_current`).

**`ComponentSnapshot`** — one record per step per job.
- Stores the validated Pydantic payload for a step as a JSONField.
- Payload hash (SHA-256) for deduplication and cache invalidation.
- Status: `found` / `inferred` / `missing`.
- Source annotation: `parsed` vs. `user_edited`.

**`PaperCache`** — canonical paper cache keyed by PMID/DOI/URL.
- Caches Paper and Figures step outputs across jobs.
- Invalidated when the LLM model changes (tracked as a field).

#### Views (`views.py`)

`views.py` (~1,950 lines) contains the main application logic. Key functions:

| Function | Route | Description |
|----------|-------|-------------|
| `home` | `GET /` | Submission form; lists recent jobs |
| `start_parse` | `POST /parse/start/` | Validates input, creates `WorkflowJob`, dispatches first step |
| `workflow_step` | `GET/POST /jobs/<id>/workflow/<step>/` | Per-step editor with JSON and ground truth forms |
| `job_status` | `GET /jobs/<id>/status/` | JSON progress poll endpoint |
| `dashboard` | `GET /jobs/<id>/dashboard/` | Dash-powered summary dashboard |
| `figure_image_proxy` | `GET /jobs/<id>/figure-image/` | Proxies figure images from remote URLs |
| `_run_step` | internal | Core orchestrator; imports and runs `researcher-ai` parsers |
| `_dispatch_workflow_step` | internal | Dispatches via Celery if available, otherwise runs synchronously |
| `invalidated_steps` | internal | Returns downstream steps to re-run after a user edit |

#### Step dependency graph

```python
STEP_ORDER = ["paper", "figures", "method", "datasets", "software", "pipeline"]

STEP_DEPENDENCIES = {
    "paper":    [],
    "figures":  ["paper"],
    "method":   ["paper", "figures"],
    "datasets": ["paper", "method"],
    "software": ["method"],
    "pipeline": ["method", "datasets", "software", "figures"],
}
```

Editing any step invalidates all steps that depend on it. `invalidated_steps()` computes the affected set and `rebuild_from_step` re-runs them in topological order.

#### Supporting modules

| Module | Description |
|--------|-------------|
| `job_store.py` | ORM-backed job persistence with in-memory fallback for testing |
| `job_events.py` | Append-only `parse_logs` management; trims to the last 250 entries |
| `tasks.py` | Celery task wrappers with fallback dummy decorator |
| `confidence.py` | Weighted confidence scoring (0–100) across step quality, figure matching, dataset resolution, warnings |
| `dag_app.py` | `dash-cytoscape` assay DAG factory, one Dash app per job |
| `dashboards.py` | Plotly Dash summary dashboard factory |
| `forms.py` | Django forms: `ComponentJSONForm` (Svelte JSON editor), `FigureGroundTruthForm` |

#### FastAPI sub-package (`api/`)

Added in Phase 1. All files live under `researcher_ai_portal_app/api/`.

| File | Description |
|------|-------------|
| `__init__.py` | Package marker |
| `routes.py` | FastAPI `APIRouter` with Phase 1 endpoints (`/ping`, `/jobs`, `/jobs/{id}`) |
| `schemas.py` | Pydantic v2 request/response models (`JobSummary`, `JobsListResponse`, etc.) |
| `repository.py` | Async ORM layer — all Django model access from FastAPI goes through here |
| `dependencies.py` | `get_current_user` FastAPI dependency; resolves Django session cookie → User |

The async repository layer centralises the sync/async boundary. Route handlers call repository functions; they never use `sync_to_async` directly. This keeps route handlers clean and makes the ORM calls testable in isolation.

---

## Data flow

### Parse submission

```
Browser POST /parse/start/
  → start_parse() validates LLM model + API key format
  → Creates WorkflowJob (status: queued)
  → Encrypts API key into session (Fernet, never written to DB)
  → _dispatch_workflow_step("paper")
       → Celery: run_workflow_step.delay("paper", ...)
         OR sync: _run_step("paper", ...)
  → Redirect to /jobs/<id>/
```

### Step execution

```
_run_step(job_id, step, llm_api_key, llm_model)
  → Imports researcher_ai.<step> parser
  → Executes parser with paper/figures/method components as context
  → Receives Pydantic model payload
  → ComponentSnapshot.objects.update_or_create(job=job, step=step, payload=payload.dict())
  → Updates WorkflowJob.progress + WorkflowJob.parse_logs
```

### Status polling

```
Browser GET /jobs/<id>/status/   (every 2–3 seconds)
  → job_status() reads from Django's LocMemCache (key: "job_progress:<job_id>")
  → Falls back to WorkflowJob DB record if cache miss
  → Returns JSON: {status, progress, stage, parse_logs, error}
```

### User edit + rebuild

```
Browser POST /jobs/<id>/workflow/method/  (action=save_structured_step or save_json)
  → Validates edited JSON against researcher_ai Pydantic model
  → ComponentSnapshot.save(source="user_edited")
  → invalidated_steps(job, "method") → ["datasets", "software", "pipeline"]
  → rebuild_from_step.delay(job_id, "method", ...)
       → Runs each invalidated step in order
```

---

## Authentication

### Globus OAuth (production)

Login flow: `/login/globus/` → Globus authorization server → `/complete/globus/` callback → `social_django` creates/updates Django User → session established.

Access control: `GLOBUS_ADMIN_GROUP` restricts login to members of a specific Globus group. The portal uses `@login_required` on all job views.

### Local Django auth (development)

When `GLOBUS_CLIENT_ID` is not set, `social-auth-app-django` falls back to Django's `ModelBackend`. The app starts without Globus configured; create a superuser with `python manage.py createsuperuser`.

### FastAPI session auth

FastAPI endpoints are authenticated via the `get_current_user` dependency in `api/dependencies.py`. It reads the `sessionid` cookie, looks up the session in Django's session backend, and returns the authenticated Django User object. No JWT or separate auth system is needed.

---

## LLM provider routing

The portal supports three LLM providers. The model name prefix determines which environment variable supplies the API key. The user's session-stored key takes precedence; the env var acts as a server-side fallback.

| Model prefix | Provider | Env var fallback |
|-------------|----------|-----------------|
| `gpt-*`, `chatgpt-*`, `o1-*`, `o3-*`, `o4-*` | OpenAI | `OPENAI_API_KEY` |
| `claude-*` | Anthropic | `ANTHROPIC_API_KEY` |
| `gemini-*` | Google | `GEMINI_API_KEY` |

---

## Caching

| Layer | Backend | Key pattern | TTL |
|-------|---------|-------------|-----|
| Job progress (poll) | `LocMemCache` | `job_progress:<job_id>` | 1 hour |
| Paper parse cache | `PaperCache` DB table | PMID/DOI/URL + model | permanent (manual invalidation) |

In-process cache (`LocMemCache`) does not survive worker restarts and is not shared across Gunicorn workers. This is acceptable for single-worker deployments. A Redis-backed cache can be configured via `CACHE_URL` if cross-worker consistency is needed.

---

## Deployment topology

### Development

```
uvicorn (single process)
  └── ASGI router (asgi.py)
       ├── FastAPI → /api/v1/*
       └── Django  → /*
              └── SQLite (template.db)
```

### Production (Docker)

```
nginx (TLS termination, reverse proxy)
  └── Gunicorn (process manager, 2 workers)
       └── UvicornWorker × 2
            └── ASGI router (asgi.py)
                 ├── FastAPI → /api/v1/*
                 └── Django  → /*
                        └── PostgreSQL
```

### Optional async workers (Celery)

When `CELERY_BROKER_URL` is set, parse steps are dispatched to Celery workers instead of running in-process. The Celery worker runs the same `_run_step` code path. Without Celery, steps run synchronously in the Gunicorn/Uvicorn worker — this is the default "serial parser mode."

```
Gunicorn/Uvicorn  →  run_workflow_step.delay()  →  Redis broker
                                                         └── Celery worker
                                                              └── _run_step()
```

---

## FastAPI integration roadmap

The FastAPI layer is built in phases alongside the visual pipeline builder feature.

| Phase | Scope |
|-------|-------|
| **Phase 1** (complete) | ASGI co-habitation. Path-based router, `/ping` + `/jobs` endpoints, async repository layer, Django session auth dependency. |
| **Phase 2** | Core visual builder API. Pydantic graph schemas (`WorkflowGraph`, `GraphNode`, `GraphEdge`), `POST /parse-publication`, `GET/PUT /graphs/{job_id}`. |
| **Phase 3** | Frontend wiring. React Flow integration, auth hardening, CSRF handling, status-poll endpoint backed by the existing cache layer. |
| **Phase 4** | Execution and WebSockets. Graph validation, pipeline compilation, WebSocket log streaming, optional Snakemake/Slurm adapter. |

See [`../FASTAPI_INTEGRATION_PLAN.md`](../FASTAPI_INTEGRATION_PLAN.md) for the full plan with code examples.

---

## Key design decisions

**Django outer, FastAPI inner.** FastAPI is mounted inside the Django ASGI application, not the other way around. This preserves Django's middleware stack (9 layers including WhiteNoise, Globus auth, session, CSRF) without re-wiring. Inverting the relationship would require re-implementing or bypassing each middleware for Django-bound requests.

**Shared ORM, no SQLAlchemy.** FastAPI accesses Django models through the async repository layer in `api/repository.py`. There is no second database connection pool, no duplicate model definitions, and no synchronisation strategy. The repository layer wraps `select_related`/`prefetch_related` calls in `sync_to_async` and uses Django 5.2's native async querysets for simple operations.

**No JWT.** FastAPI endpoints authenticate using the same Django session cookie the browser already holds. This eliminates a parallel auth system and means a logged-in user can call the API from the browser's developer console or a script with their existing session.

**Additive, not migratory.** All existing Django URL patterns, views, forms, and templates are untouched. The FastAPI package (`api/`) can be deleted and `asgi.py` reverted to the original without breaking any existing functionality.
