# FastAPI Integration Plan — Visual Pipeline Builder

## Architectural Decision: Integrated ASGI Monorepo

**Develop FastAPI within the existing Django project from the start.**

A separate FastAPI microservice would require duplicating the Django ORM models (`WorkflowJob`, `ComponentSnapshot`, `PaperCache`) as SQLAlchemy equivalents, building a synchronization layer between two databases, and bridging Globus OAuth across two origins. None of that complexity is justified. The integrated approach gives FastAPI direct access to Django's ORM, session auth, and the existing `researcher-ai` parser orchestration in `views.py` — all without duplicating a single model.

### Why Integrated Wins

1. **Single source of truth.** FastAPI imports Django models directly. No duplicate schema, no sync drift, no second database connection pool.
2. **Shared authentication.** FastAPI reads the Django session cookie to identify logged-in Globus users. No OAuth2/JWT bridge between two domains.
3. **Single deployment.** One ASGI process (Uvicorn) serves both frameworks. No inter-service networking, no second container, no service discovery.

---

## Integration Architecture: Django-Outer ASGI Mount

Django remains the top-level ASGI application. FastAPI is mounted as a sub-application under `/api/v1/`. This is the opposite of the "FastAPI-wraps-Django" pattern sometimes described online, and it's the right choice here for a critical reason: the existing middleware stack.

The current `settings.py` configures 9 middleware layers including WhiteNoise static serving, Django session management, CSRF protection, Globus auth exception handling, and `django-plotly-dash` base middleware. All of these expect Django to be the outermost ASGI app. Inverting the relationship (FastAPI outer, Django inner) would require re-implementing or carefully bypassing each middleware for Django-bound requests — a large surface area for subtle bugs.

### Routing Contract

```
/api/v1/*          → FastAPI  (visual builder, async endpoints, WebSockets)
/healthz/          → Django   (existing)
/parse/start/      → Django   (existing)
/jobs/*/status/    → Django   (existing)
/jobs/*/workflow/* → Django   (existing)
/*                 → Django   (everything else: home, auth, admin, Dash apps)
```

Existing Django endpoints are **never migrated** to FastAPI. New visual builder functionality lives exclusively under `/api/v1/`. If a future decision is made to migrate JSON-returning Django views (like `job_status`), that happens after FastAPI has been stable in production for months — not during initial integration.

### File: `researcher_ai_portal/asgi.py` (revised)

```python
import os
import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "researcher_ai_portal.settings")
django.setup()

from django.core.asgi import get_asgi_application
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

django_asgi = get_asgi_application()

# FastAPI sub-application for visual builder APIs
fastapi_app = FastAPI(
    title="Researcher AI Visual Builder",
    version="0.1.0",
    docs_url="/api/v1/docs",
    openapi_url="/api/v1/openapi.json",
)

# Import routers after django.setup()
from researcher_ai_portal_app.api.routes import router as builder_router
fastapi_app.include_router(builder_router, prefix="/api/v1")


async def application(scope, receive, send):
    """Top-level ASGI router: delegate /api/v1 to FastAPI, everything else to Django."""
    if scope["type"] in ("http", "websocket") and scope["path"].startswith("/api/v1"):
        await fastapi_app(scope, receive, send)
    else:
        await django_asgi(scope, receive, send)
```

This gives you a clean boundary: FastAPI handles only `/api/v1` traffic, Django handles everything else with its full middleware stack intact.

---

## Async ORM Repository Layer

The plan **must not** scatter `sync_to_async` calls throughout FastAPI route handlers. Instead, build a thin async repository module that wraps all Django ORM access. This centralizes the sync/async boundary, makes testing straightforward, and provides a single place to upgrade when Django's async ORM matures further.

Django 5.2 supports native async for basic querysets (`afirst`, `afilter`, `acreate`, `acount`, etc.), but `select_related`, `prefetch_related`, complex aggregations, and bulk operations still require `sync_to_async`. The repository layer hides this from route handlers.

### File: `researcher_ai_portal_app/api/repository.py`

```python
from asgiref.sync import sync_to_async
from researcher_ai_portal_app.models import WorkflowJob, ComponentSnapshot


async def get_job_with_components(job_id: str, user_id: int) -> dict | None:
    """Load a job and all its component snapshots. Returns None if not found."""

    @sync_to_async
    def _fetch():
        try:
            job = (
                WorkflowJob.objects
                .select_related("user")
                .prefetch_related("components")
                .get(id=job_id, user_id=user_id)
            )
        except WorkflowJob.DoesNotExist:
            return None
        return {
            "job": job,
            "components": {c.step: c for c in job.components.all()},
        }

    return await _fetch()


async def save_graph_state(job_id: str, user_id: int, graph_json: dict) -> bool:
    """Persist the React Flow graph state to the job's graph_data field."""
    count = await WorkflowJob.objects.filter(
        id=job_id, user_id=user_id
    ).aupdate(graph_data=graph_json)
    return count > 0
```

Every FastAPI route calls repository functions. No raw ORM in route handlers.

---

## Development Roadmap

### Phase 1: ASGI Co-habitation (Weeks 1–2)

**Goal:** Both frameworks running on a single Uvicorn process. Zero changes to existing Django behavior.

**Tasks:**

1. Add `fastapi`, `uvicorn[standard]`, and `pydantic>=2.0` to `requirements.txt`.
2. Create the package structure:
   ```
   researcher_ai_portal_app/
   └── api/
       ├── __init__.py
       ├── routes.py        # FastAPI router
       ├── schemas.py       # Pydantic models
       ├── repository.py    # Async ORM wrappers
       └── dependencies.py  # Auth + shared dependencies
   ```
3. Rewrite `researcher_ai_portal/asgi.py` with the path-based ASGI router shown above.
4. Add a smoke-test endpoint:
   ```python
   @router.get("/ping")
   async def ping():
       return {"status": "ok", "framework": "fastapi"}
   ```
5. Add an ORM integration test endpoint that queries `WorkflowJob` through the repository layer. Verify it returns real data when hit via `curl`.
6. Run the full existing test suite (`pytest researcher_ai_portal_app/tests -q`) and confirm zero regressions.
7. Update `run_portal.sh` and `docker-compose.yml` to use `uvicorn researcher_ai_portal.asgi:application` instead of Gunicorn's WSGI worker. (Gunicorn can still be used as a process manager with `uvicorn.workers.UvicornWorker`.)

**Exit criteria:** `http://localhost:8000/` serves the Django home page. `http://localhost:8000/api/v1/ping` returns FastAPI JSON. All existing tests pass.

---

### Phase 2: Core Visual Builder APIs (Weeks 3–4)

**Goal:** Backend API for the React Flow visual pipeline editor.

**Data Model Decision — Read This First**

The existing app has a fixed 6-step linear pipeline with hardcoded dependencies:

```python
STEP_ORDER = ["paper", "figures", "method", "datasets", "software", "pipeline"]
STEP_DEPENDENCIES = {
    "paper": [],
    "figures": ["paper"],
    "method": ["paper", "figures"],
    ...
}
```

The visual builder introduces a **user-composable graph** where researchers can drag blocks, wire connections, and customize execution order. This is a fundamentally different data model. The Pydantic schemas below represent the React Flow graph state, which is **separate from** the existing step dependency DAG. The graph is stored as a JSON blob on the job; execution still follows the validated dependency order.

**Tasks:**

1. **Add `graph_data` field to `WorkflowJob`:**
   ```python
   graph_data = models.JSONField(default=dict, blank=True)
   ```
   Create and run a Django migration. This field stores the full React Flow graph (nodes, edges, viewport).

2. **Define Pydantic schemas** (`api/schemas.py`):
   ```python
   class NodePort(BaseModel):
       id: str
       label: str
       type: Literal["input", "output"]

   class GraphNode(BaseModel):
       id: str
       type: str  # "paper_parser", "figure_parser", "method_parser", etc.
       position: dict  # {x: float, y: float}
       data: dict  # node-specific config + parsed metadata summary
       ports: list[NodePort] = []

   class GraphEdge(BaseModel):
       id: str
       source: str
       target: str
       source_handle: str | None = None
       target_handle: str | None = None

   class WorkflowGraph(BaseModel):
       nodes: list[GraphNode]
       edges: list[GraphEdge]
       viewport: dict = {"x": 0, "y": 0, "zoom": 1}

   class ParsePublicationRequest(BaseModel):
       source: str  # PMID, DOI, or "uploaded" for PDF
       source_type: Literal["pmid", "doi", "pdf"]
       llm_model: str

   class ParsePublicationResponse(BaseModel):
       job_id: str
       status: str
       nodes: list[GraphNode]  # auto-generated node layout from parsed metadata
   ```

3. **Build endpoints** (`api/routes.py`):

   | Method | Path | Purpose |
   |--------|------|---------|
   | `POST` | `/api/v1/parse-publication` | Submit a publication for parsing. Returns a job ID and auto-generated graph nodes from the parsed metadata. Long-running — returns 202 with job ID, client polls for completion. |
   | `GET` | `/api/v1/graphs/{job_id}` | Retrieve the current graph state for a job. |
   | `PUT` | `/api/v1/graphs/{job_id}` | Save the React Flow graph state (after user drags/connects nodes). |
   | `GET` | `/api/v1/graphs/{job_id}/nodes/{node_id}` | Get detailed metadata for a single node (parsed component payload from `ComponentSnapshot`). |
   | `GET` | `/api/v1/jobs/{job_id}/status` | Lightweight status poll (progress %, current step, errors). |

4. **Publication parsing integration:** The `POST /parse-publication` endpoint should reuse the existing `_run_step` orchestration from `views.py`, not rewrite it. Call it via `sync_to_async` through the repository layer, or dispatch through the existing Celery task (`run_workflow_step.delay()`). The endpoint returns `202 Accepted` immediately; the client polls `/jobs/{job_id}/status`.

5. **Auto-layout:** After parsing completes, generate a default graph layout from `STEP_ORDER` and `STEP_DEPENDENCIES` — each step becomes a `GraphNode`, dependency links become `GraphEdge`s. Store this in `graph_data`. The user can then rearrange nodes in the React Flow UI.

**Exit criteria:** A `curl` call to `POST /api/v1/parse-publication` creates a job, triggers parsing via the existing pipeline, and `GET /api/v1/graphs/{job_id}` returns a valid `WorkflowGraph` JSON.

---

### Phase 3: Frontend & Auth Wiring (Weeks 5–6)

**Goal:** Secure the API and connect the React Flow frontend.

**Tasks:**

1. **Auth dependency** (`api/dependencies.py`):
   Write a FastAPI dependency that reads the Django session cookie (`sessionid`), looks up the session in Django's session backend, and extracts the authenticated user. This reuses `django.contrib.sessions` directly — no JWT, no OAuth bridge.

   ```python
   from django.contrib.sessions.backends.db import SessionStore
   from django.contrib.auth import get_user_model

   async def get_current_user(request: Request) -> User:
       session_id = request.cookies.get("sessionid")
       if not session_id:
           raise HTTPException(401, "Not authenticated")
       session = await sync_to_async(SessionStore)(session_key=session_id)
       user_id = session.get("_auth_user_id")
       if not user_id:
           raise HTTPException(401, "Session expired")
       User = get_user_model()
       return await User.objects.aget(pk=user_id)
   ```

   **Note:** This dependency also needs access to `_decrypt_session_secret` for routes that trigger LLM calls (the API key is stored encrypted in the Django session). Import it from `views.py` or extract it to a shared utility module.

2. **CSRF handling:** FastAPI endpoints under `/api/v1/` are JSON APIs called via `fetch` with `credentials: "include"`. Either exempt them from Django's CSRF middleware (they bypass it already since FastAPI handles them directly) or implement a lightweight CSRF token header check in the FastAPI dependency.

3. **React Flow frontend:** Serve the React Flow app as a Django template (extending `base.html`) or as a standalone SPA with static files served by WhiteNoise. The choice depends on whether you want the visual builder to share Django's nav/chrome or be a full-screen app. All API calls go to `/api/v1/` with `credentials: "include"` to send the session cookie.

4. **Status polling:** The frontend polls `GET /api/v1/jobs/{job_id}/status` every 2–3 seconds during parsing. This is a thin FastAPI endpoint that reads from Django's cache (the same `job_progress:{job_id}` key used by `tasks.py`) for low-latency responses without hitting the database on every poll.

**Exit criteria:** A logged-in Globus user can open the visual builder, submit a publication, see nodes populate as parsing completes, drag/connect them, and save the graph — all through FastAPI endpoints authenticated via their Django session.

---

### Phase 4: Execution, WebSockets & Handoff (Weeks 7–8)

**Goal:** Compile the visual graph into an executable pipeline and stream real-time feedback.

**Tasks:**

1. **Graph validation:** When the user clicks "Compile & Run," FastAPI receives the `WorkflowGraph` JSON, validates it against `STEP_DEPENDENCIES` (all required upstream nodes must be connected), and rejects invalid topologies with descriptive errors.

2. **Graph compilation:** Pass the validated graph to the existing `_run_step` / `rebuild_from_step` pipeline. The graph's node ordering determines execution sequence. FastAPI dispatches via `run_workflow_step.delay()` (Celery) or falls back to synchronous execution in MVP mode — reusing the exact same code path Django currently uses.

3. **WebSocket support (optional, recommended):**
   FastAPI handles WebSocket connections natively. When a user starts pipeline execution, the frontend opens a WebSocket to `/api/v1/ws/jobs/{job_id}/logs`. The server reads from `parse_logs` (already stored as a JSON array on `WorkflowJob`) and streams new entries as they appear. This replaces aggressive HTTP polling during execution.

   **Infrastructure requirement:** WebSockets need a channel layer for multi-worker deployments. For single-worker MVP, in-process streaming is sufficient. For production, add Redis as a pub/sub layer (this pairs well with the optional Celery Redis broker already in the architecture).

   **If WebSockets are too heavy for the timeline**, skip them. The polling approach from Phase 3 works fine — `parse_logs` and the cache-based progress system already support it.

4. **Execution adapters:** FastAPI triggers the relevant adapter (Snakemake, Nextflow, TSCC/Slurm) via the existing `PipelineBuilder` output and updates the Django database with the active job PID or container ID.

**Exit criteria:** A user can visually build a pipeline, click "Run," and see real-time progress (via polling or WebSocket) as each step executes.

---

## What This Plan Does NOT Change

These elements of the existing Django app remain untouched throughout all phases:

- All 10 existing URL patterns in `urls.py`
- The Globus OAuth login/logout flow
- The `django-plotly-dash` dashboard integration
- The `django-svelte-jsoneditor` component editing UI
- The template rendering pipeline (`base.html`, `home.html`, `workflow_step.html`)
- The Celery task infrastructure and fallback dummy decorator
- The `PaperCache` model and caching logic
- The confidence scoring system
- The DAG visualization (Cytoscape) app

FastAPI is additive. If you deleted the entire `api/` package and reverted `asgi.py`, the Django app would work exactly as it does today.

---

## Risk Mitigation

| Risk | Mitigation |
|------|------------|
| `sync_to_async` overhead on hot paths | Repository layer centralizes all ORM calls; profile early in Phase 1. For read-heavy endpoints, use Django's cache layer (already configured as `LocMemCache`). |
| Session cookie auth fragility | The `get_current_user` dependency is ~10 lines and testable in isolation. Add integration tests in Phase 3 that verify auth works with both Globus-authenticated and local dev sessions. |
| React Flow state size (large graphs) | `graph_data` is a JSONField — PostgreSQL handles multi-MB JSON efficiently. Add a size limit in the Pydantic schema (e.g., max 500 nodes) to prevent abuse. |
| Middleware conflicts between frameworks | The ASGI path router ensures Django middleware never touches `/api/v1` requests and FastAPI never touches Django requests. Clean separation, no shared middleware stack. |
| WebSocket complexity blows the timeline | WebSockets are explicitly optional in Phase 4. Polling works. Ship without WebSockets if needed; add them in a follow-up sprint. |

---

## Dependencies to Add

```
# requirements.txt additions
fastapi>=0.115,<1.0
uvicorn[standard]>=0.30,<1.0
pydantic>=2.0,<3.0
```

Pydantic v2 is required for performance and compatibility with FastAPI's current release line. The existing `researcher-ai` package likely already uses Pydantic — verify version compatibility before upgrading.
