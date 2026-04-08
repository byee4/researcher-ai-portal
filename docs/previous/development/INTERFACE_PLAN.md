# researcher-ai Interface Plan (Revised)

## Assessment: Modify the Existing Django Portal

**The portal exists and has solid bones.** After reviewing `researcher-ai-portal/` in full, the verdict is to **build on what's there** rather than start from scratch. Here's the inventory of what already works and what needs to change.

### What Already Works

| Feature | Status | Location |
|---------|--------|----------|
| Globus OAuth2 login | Fully configured | `settings.py` — social_django + globus_portal_framework |
| django-plotly-dash | Integrated, with middleware + static finders | `settings.py`, `dashboards.py` |
| Step-by-step workflow (paper→figures→method→datasets→software→pipeline) | Functional | `views.py` `_run_step()` |
| Figure image proxy (PMC, direct URLs, HTML scraping) | Production-quality | `views.py` `figure_image_proxy()` |
| Figure ground truth injection (user corrections) | Full implementation | `views.py` `_inject_figure_ground_truth()` |
| Svelte JSON editor for component editing | Integrated | `forms.py` `ComponentJSONForm` |
| Figure uncertainty detection | Working | `views.py` `_figure_uncertainty_rows()` |
| Figure provenance/calibration tracking | Working | `views.py` `_figure_provenance_rows()` |
| Pydantic validation on user edits | Working | `views.py` `_validate_component_json()` |
| LLM model selection + API key handling | Working | `views.py` `_llm_env()`, `start_parse()` |
| Per-figure progress tracking during parse | Working | `views.py` `_run_step()` figures branch |

### What Needs to Change

| Gap | Impact | Priority |
|-----|--------|----------|
| In-memory job store — lost on restart, no per-user isolation | Must fix | P0 |
| No DAG visualization — pipeline topology is a linear scatter plot | Core feature gap | P0 |
| No aggregate confidence model (assay-level, pipeline-level) | Core feature gap | P0 |
| Jobs not tied to users — anyone can access any job_id | Security gap | P0 |
| No paper cache — reparsing identical PMIDs wastes LLM calls | Efficiency | P1 |
| Dashboard is static Plotly — no interactive node editing | UX gap | P1 |
| No nf-core / GitHub link display | Feature request | P2 |
| Templates use inline CSS — no design system | Polish | P2 |

### Why Not Start From Scratch

The 1,378-line `views.py` is well-organized: clear helper functions, proper separation between parsing logic and HTTP handling, robust figure URL resolution with fallback chains, and Pydantic validation on every user edit. The ground truth injection system alone (`_inject_figure_ground_truth`, `_figure_uncertainty_rows`, `_figure_provenance_rows`) is ~300 lines of carefully tested code that directly implements the "user intervention" requirement. Rewriting this in FastAPI/React would take weeks and produce the same logic.

The django-plotly-dash + Globus stack is already configured and tested. Adding `dash-cytoscape` for DAG visualization slots directly into this stack without fighting it.

---

## Architecture After Modifications

```
┌──────────────────────────────────────────────────────────────┐
│                   Django Templates + Dash Apps               │
│                                                              │
│  home.html ──→ progress.html ──→ workflow_step.html          │
│                  (polls Redis)       ──→ dashboard.html       │
│                                        │                     │
│                        ┌───────────────┴──────────────┐      │
│                        │   Dash Cytoscape DAG Canvas  │      │
│                        │   (AssayGraph + Confidence)   │      │
│                        │   + Plotly summary charts      │      │
│                        │   + Figure image gallery       │      │
│                        └───────────────────────────────┘      │
│                                                              │
│  Auth: Globus OAuth2 (social_django + globus_portal_framework)│
│  API keys: stored in request.session (ephemeral, not DB)     │
└──────────────────────────┬───────────────────────────────────┘
                           │ dispatch (async)
┌──────────────────────────┴───────────────────────────────────┐
│                    Celery Worker (Redis broker)               │
│                                                              │
│  tasks.run_workflow_step(job_id, step)                       │
│    → Writes progress to Redis (fast, ephemeral)              │
│    → Writes results to PostgreSQL (durable, on completion)   │
└──────────────────────────┬───────────────────────────────────┘
                           │
┌──────────────────────────┴───────────────────────────────────┐
│              Django ORM (PostgreSQL / SQLite)                 │
│                                                              │
│  WorkflowJob  ─┬─→  ComponentSnapshot (per-step, hashed)     │
│  (user, status) └─→  PaperCache (pmid/doi → parsed JSON)     │
└──────────────────────────────────────────────────────────────┘
                           │
┌──────────────────────────┴───────────────────────────────────┐
│              researcher-ai Package (unchanged)                │
│  WorkflowOrchestrator / Parsers / PipelineBuilder            │
└──────────────────────────────────────────────────────────────┘
```

---

## Critical Infrastructure: Concurrency, Caching, Invalidation, and Security

These five cross-cutting concerns must be addressed before the phased feature work. They are woven into Phases 0-6 below rather than treated as separate phases.

### 1. Celery + Redis Task Queue (Fixes HTTP Timeout)

**Problem:** `_run_step()` calls LLM parsers synchronously inside the Django request-response cycle. A single figure parse can take 30-60s; a full 6-step workflow can take 5+ minutes. Nginx/gunicorn will kill the connection long before that.

**Fix:** `start_parse()` and `workflow_step()` (action="run") no longer call `_run_step()` directly. Instead:

```python
# views.py — start_parse()
job = WorkflowJob.objects.create(user=request.user, source=source, ...)
run_workflow_step.delay(str(job.id), "paper")  # Celery async dispatch
return redirect("workflow_step", job_id=str(job.id), step="paper")
```

New file `researcher_ai_portal_app/tasks.py`:

```python
from celery import shared_task
from django.core.cache import cache  # Redis-backed

@shared_task(bind=True, max_retries=0)
def run_workflow_step(self, job_id: str, step: str):
    """Execute one parser step in a Celery worker process."""
    job = WorkflowJob.objects.get(id=job_id)

    # Write live progress to Redis (not SQL)
    cache_key = f"job_progress:{job_id}"
    cache.set(cache_key, {
        "status": "in_progress",
        "progress": _progress_for_step(step),
        "stage": f"Running {STEP_LABELS[step]}",
        "figure_parse_current": 0,
        "figure_parse_total": 0,
    }, timeout=3600)

    try:
        with _llm_env_from_session(job):
            _run_step(job, step)  # Refactored to accept ORM object

        # Write final result to SQL (durable)
        job.status = "in_progress"
        job.current_step = step
        job.progress = _progress_for_step(step)
        job.stage = f"Completed {STEP_LABELS[step]}"
        job.save(update_fields=["status", "current_step", "progress", "stage", "updated_at"])

        # Update Redis with completion
        cache.set(cache_key, {
            "status": "step_complete",
            "progress": job.progress,
            "stage": job.stage,
        }, timeout=3600)

    except Exception as exc:
        job.status = "failed"
        job.error = str(exc)
        job.save(update_fields=["status", "error", "updated_at"])
        cache.set(cache_key, {
            "status": "failed",
            "progress": job.progress,
            "stage": f"{STEP_LABELS[step]} failed",
            "error": str(exc),
        }, timeout=3600)
```

Settings additions:

```python
# settings.py
CELERY_BROKER_URL = os.environ.get("CELERY_BROKER_URL", "redis://localhost:6379/0")
CELERY_RESULT_BACKEND = os.environ.get("CELERY_RESULT_BACKEND", "redis://localhost:6379/0")

CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.redis.RedisCache",
        "LOCATION": os.environ.get("REDIS_URL", "redis://localhost:6379/1"),
    }
}
```

### 2. Redis-Backed Progress Polling (Fixes Database Hammering)

**Problem:** `progress.html` polls `job_status()` every 1.5s. With the ORM, that's a SQL query per poll per user. Under 20 concurrent users, that's ~800 queries/minute for status checks alone.

**Fix:** The `job_status()` view reads from Redis first, falling back to SQL only if the cache key is missing (i.e., the job has finished and the ephemeral progress was evicted):

```python
# views.py — job_status()
@login_required
@require_GET
def job_status(request, job_id: str):
    # Fast path: read from Redis
    cached = cache.get(f"job_progress:{job_id}")
    if cached:
        return JsonResponse({"job_id": job_id, **cached})

    # Slow path: job finished or cache expired, read from SQL
    try:
        job = WorkflowJob.objects.get(id=job_id, user=request.user)
    except WorkflowJob.DoesNotExist:
        return JsonResponse({"error": "unknown job"}, status=404)

    return JsonResponse({
        "job_id": job_id,
        "status": job.status,
        "progress": job.progress,
        "stage": job.stage,
        "error": job.error,
        "current_step": job.current_step,
    })
```

The Celery task writes progress to Redis at each sub-step (e.g., per-figure during figure parsing). Redis reads are O(1) with no connection pool overhead.

### 3. DAG-Aware Rebuild Invalidation (Fixes Wasted Recomputation)

**Problem:** If a user edits the STAR alignment parameters in the RNA-seq assay and hits "Rebuild," the plan currently re-runs the entire `PipelineBuilder.build()`. But the paper parse, figure parse, dataset resolution, and software identification are all upstream and unchanged. Worse, if the user only edited one assay in a multi-omic paper, even other assays' pipeline steps shouldn't be regenerated.

**Fix:** Add a content hash to `ComponentSnapshot` and track which components are "dirty":

```python
# models.py
import hashlib, json

class ComponentSnapshot(models.Model):
    job = models.ForeignKey(WorkflowJob, on_delete=models.CASCADE, related_name="components")
    step = models.CharField(max_length=32)
    payload = models.JSONField()
    payload_hash = models.CharField(max_length=64, blank=True)  # SHA-256 of payload
    status = models.CharField(max_length=16)
    missing_fields = models.JSONField(default=list)
    source = models.CharField(max_length=32)
    created_at = models.DateTimeField(auto_now_add=True)

    def save(self, *args, **kwargs):
        self.payload_hash = hashlib.sha256(
            json.dumps(self.payload, sort_keys=True, ensure_ascii=False).encode()
        ).hexdigest()
        super().save(*args, **kwargs)

    class Meta:
        unique_together = [("job", "step")]
```

The rebuild logic uses a dependency map to determine what's actually changed:

```python
# Dependency ordering: each step depends on these upstream steps
STEP_DEPENDENCIES = {
    "paper":    [],
    "figures":  ["paper"],
    "method":   ["paper", "figures"],
    "datasets": ["paper", "method"],
    "software": ["method"],
    "pipeline": ["method", "datasets", "software", "figures"],
}

def invalidated_steps(job: WorkflowJob, edited_step: str) -> list[str]:
    """Return steps that must be re-run because edited_step changed."""
    dirty = {edited_step}
    for step in STEP_ORDER:
        if step == edited_step:
            continue
        # If any of this step's dependencies are dirty, this step is dirty too
        if dirty & set(STEP_DEPENDENCIES[step]):
            dirty.add(step)
    # Only return steps that are downstream of the edit
    idx = STEP_ORDER.index(edited_step)
    return [s for s in STEP_ORDER[idx+1:] if s in dirty]
```

When "Rebuild" is clicked after editing the method step, `invalidated_steps(job, "method")` returns `["datasets", "software", "pipeline"]` — paper and figures are untouched. If only `"software"` was edited, only `["pipeline"]` is invalidated.

The Celery task for rebuild dispatches only the dirty steps:

```python
@shared_task
def rebuild_from_step(job_id: str, edited_step: str):
    job = WorkflowJob.objects.get(id=job_id)
    dirty = invalidated_steps(job, edited_step)
    for step in dirty:
        run_workflow_step(job_id, step)  # sequential within worker
```

### 4. Dash Routing (Clarification)

The plan's assertion that `urls.py` needs "No new URL patterns" is **correct for the existing codebase** — `path('django_plotly_dash/', include('django_plotly_dash.urls'))` is already registered at line 30 of `urls.py`. Cytoscape callbacks will route through this existing path. No change needed.

However, `urls.py` **does** need one new pattern for the Celery-driven architecture: a dedicated status endpoint that reads from Redis. The existing `job_status` endpoint will be refactored in-place (see #2 above), so this is a modification, not an addition.

### 5. API Key Security (Session-Based Storage)

**Problem:** Storing LLM API keys in the database — even encrypted — creates a liability. If the DB is compromised alongside the Django `SECRET_KEY` or Fernet key, all user keys are exposed.

**Fix:** Store API keys exclusively in the Django session, which is ephemeral and auto-expires:

```python
# views.py — start_parse()
request.session["llm_api_key"] = llm_api_key  # Stored in session backend
request.session["llm_model"] = llm_model
request.session.set_expiry(7200)  # 2-hour session timeout

# Remove llm_api_key from WorkflowJob model entirely
job = WorkflowJob.objects.create(
    user=request.user,
    source=source,
    source_type=source_type,
    input_display=input_value,
    llm_model=llm_model,
    # NO llm_api_key field
)
```

The Celery task retrieves the key from the session at dispatch time, passing it as a task argument (encrypted in transit by Redis TLS):

```python
# views.py — dispatching the task
run_workflow_step.delay(
    str(job.id),
    "paper",
    llm_api_key=request.session.get("llm_api_key", ""),
    llm_model=request.session.get("llm_model", ""),
)
```

```python
# tasks.py — receiving the key
@shared_task(bind=True)
def run_workflow_step(self, job_id: str, step: str, llm_api_key: str = "", llm_model: str = ""):
    # Use the key directly, never persisted to disk
    with _llm_env(llm_model, llm_api_key):
        _run_step(...)
```

**Trade-off:** If the user's session expires mid-workflow (e.g., they close the tab during a long parse), the key is lost and the workflow cannot continue. This is acceptable because: (a) the workflow steps are dispatched immediately on submission, so the key only needs to survive the initial dispatch, and (b) for multi-step manual workflows where the user clicks "Run" per step, the session is refreshed on each page load.

**If long-running background jobs need the key beyond the session window**, use Django's `SESSION_ENGINE = 'django.contrib.sessions.backends.cache'` backed by Redis with a longer TTL, or pass the key as an encrypted Celery task argument at dispatch time (which is what the design above does — the key travels with the task, not via the session).

The `WorkflowJob` model is updated to remove the `llm_api_key` field entirely:

```python
class WorkflowJob(models.Model):
    # ... all fields from Phase 0 EXCEPT llm_api_key
    llm_model = models.CharField(max_length=64)
    # llm_api_key is NOT stored — passed via Celery task args only
```

# researcher-ai Interface Plan (Revised for Distributed Execution)

## Assessment: Distributed Architecture Upgrade

The existing Django portal has a solid foundation (Globus Auth, Svelte JSON editor, LLM model selection). However, migrating long-running computational pipelines to a web environment requires robust asynchronous execution. 

This plan upgrades the architecture using **Celery** for background task execution and **Redis** for state management, while addressing critical distributed system challenges (air-gapped security, live UI polling, cache invalidation, and shared storage).

---

## Core Architectural Solutions

### 1. Secure API Key Handoff (Option B: Ephemeral Redis Store)
**Problem:** Celery workers operate outside the Django HTTP request/response cycle and cannot read the user's session cookie to retrieve the `llm_api_key`. Passing it as a plain-text task argument exposes it in the broker payload.
**Solution:** When the user submits a job, `views.py` will generate a unique `job_id`, store the API key in Redis mapped to that `job_id` with a strict Time-To-Live (TTL) (e.g., 24 hours), and dispatch the task. The Celery worker will read the key from Redis at runtime and delete the key immediately upon task completion or failure.

### 2. Live DAG Updates in Dash (`dcc.Interval`)
**Problem:** Dash Cytoscape renders synchronously. A background Celery task updating state won't reflect on the frontend unless the user manually refreshes.
**Solution:** Inside `dag_app.py`, integrate a `dcc.Interval` component set to trigger every ~3000ms. A Dash callback will read the live execution state from Redis (or the database) and dynamically update the Cytoscape `elements` property, allowing node colors to transition (Pending $\rightarrow$ Running $\rightarrow$ Completed/Failed) in real-time.

### 3. Polling Fallback Cascade
**Problem:** To save memory, ephemeral Redis keys tracking job progress should expire after completion. If a user revisits a completed job page a day later, querying Redis will fail.
**Solution:**
The `progress.html` polling endpoint will implement a resilient cascade:
1. **Query Redis:** If `job_id` exists, return live streaming progress.
2. **Query Database:** If not in Redis, query the Django ORM (`WorkflowJob`). If found, return its terminal state (Completed, Failed, or Aborted).
3. **Fail Gracefully:** If absent from both, return a 404.

### 4. DAG-Aware Cache Invalidation (Smart Rebuilds)
**Problem:** Re-running a pipeline from scratch when a user only edits a downstream parameter (e.g., `DESeq2` settings) wastes compute and LLM tokens.
**Solution:**
Implement recursive invalidation using the pipeline's `networkx` DAG. When a user submits an edit via the Svelte JSON interface:
1. Identify the edited component node.
2. Traverse the graph to find all downstream **descendant nodes**.
3. Invalidate/clear the cached states for *only* the edited node and its descendants.
4. Pass the partially cached pipeline back to the Celery worker, ensuring upstream tasks (like `STAR alignment`) use their cached results.

### 5. Centralized Artifact Storage
**Problem:** Writing Snakemake files, Jupyter notebooks, or Conda environments to local disk (`/tmp/` or `./outputs`) will result in missing files if the app scales to multiple servers or workers.
**Solution:**
All pipeline generators will be refactored to utilize Django's `default_storage` API (backed by AWS S3, MinIO, or an NFS mount) OR serialize their string/JSON outputs directly into the PostgreSQL `ComponentSnapshot` database tables. No artifacts will be saved to the ephemeral local disk of the Celery worker.

---

## Infrastructure & Dependencies

Add the following to your Django environment:
```bash
pip install celery redis  # Async task queue and broker/cache
pip install dash-cytoscape  # DAG visualization
pip install networkx  # DAG traversal for cache invalidation
---

## Phase 0: Django ORM Models (Replaces `job_store.py`)

Create `researcher_ai_portal_app/models.py`:

### `WorkflowJob`

Replaces the in-memory `_JOBS` dict. Each row is one parse session.

```python
class WorkflowJob(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="workflow_jobs")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    # Input
    source = models.TextField()                    # PMID, file path, or URL
    source_type = models.CharField(max_length=16)  # "pmid", "pdf", "doi", "url"
    input_display = models.CharField(max_length=255)  # Human-readable (e.g., "26971820" or "paper.pdf")

    # LLM config (key is NOT stored — passed via Celery task args, see §5 API Key Security)
    llm_model = models.CharField(max_length=64)

    # Progress (live progress is in Redis; these fields are the durable snapshot)
    status = models.CharField(max_length=16, default="queued")   # queued, in_progress, completed, failed
    progress = models.IntegerField(default=0)
    stage = models.CharField(max_length=255, default="Queued")
    current_step = models.CharField(max_length=32, default="paper")
    error = models.TextField(blank=True, default="")

    # Figure parse progress
    figure_parse_total = models.IntegerField(default=0)
    figure_parse_current = models.IntegerField(default=0)
    supplementary_figure_ids = models.JSONField(default=list)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["user", "-created_at"])]
```

### `ComponentSnapshot`

Stores each parsed component as a JSON blob, replacing `job["components"]` and `job["component_meta"]`. Includes a content hash for DAG-aware rebuild invalidation (see §3 above).

```python
class ComponentSnapshot(models.Model):
    job = models.ForeignKey(WorkflowJob, on_delete=models.CASCADE, related_name="components")
    step = models.CharField(max_length=32)        # "paper", "figures", "method", etc.
    payload = models.JSONField()                   # The Pydantic model_dump(mode="json") output
    payload_hash = models.CharField(max_length=64, blank=True)  # SHA-256 for dirty tracking
    status = models.CharField(max_length=16)       # "found", "inferred", "missing"
    missing_fields = models.JSONField(default=list)
    source = models.CharField(max_length=32)       # "parsed", "corrected_by_user", "ground_truth_injected"
    created_at = models.DateTimeField(auto_now_add=True)

    def save(self, *args, **kwargs):
        self.payload_hash = hashlib.sha256(
            json.dumps(self.payload, sort_keys=True, ensure_ascii=False).encode()
        ).hexdigest()
        super().save(*args, **kwargs)

    class Meta:
        unique_together = [("job", "step")]
```

### `PaperCache`

Avoids re-parsing the same PMID/DOI. Keyed by canonical identifier.

```python
class PaperCache(models.Model):
    canonical_id = models.CharField(max_length=64, unique=True, db_index=True)  # e.g., "pmid:26971820"
    paper_json = models.JSONField()
    figures_json = models.JSONField(default=list)
    created_at = models.DateTimeField(auto_now_add=True)
    llm_model = models.CharField(max_length=64)    # Track which model produced this parse

    class Meta:
        indexes = [models.Index(fields=["canonical_id"])]
```

**Migration from `job_store.py`:** Replace all `create_job()`, `update_job()`, `get_job()` calls in `views.py` with ORM equivalents. The `_persist_component()` helper becomes a `ComponentSnapshot.objects.update_or_create()`. The `_run_step()` function checks `PaperCache` before calling `PaperParser.parse()`.

**Per-user isolation:** All views filter by `request.user`. The `workflow_step` and `dashboard` views add `job.user == request.user` checks (return 403 otherwise). The home page shows a "My Previous Parses" list via `WorkflowJob.objects.filter(user=request.user)`.

---

## Phase 1: Dash-Cytoscape DAG Visualization

Replace the linear scatter plot in `dashboards.py` with an interactive `dash-cytoscape` graph rendering the `AssayGraph`.

### New file: `researcher_ai_portal_app/dag_app.py`

```python
import dash_cytoscape as cyto
from dash import html, dcc, Input, Output, State
from django_plotly_dash import DjangoDash

def build_dag_app(job_id: str, method_json: dict, figures_json: list,
                  datasets_json: list, pipeline_json: dict,
                  confidence: dict) -> str:
    """Build an interactive DAG Dash app for the AssayGraph."""
    app_name = f"researcher_ai_dag_{job_id}"
    app = DjangoDash(app_name)

    # Build Cytoscape elements from AssayGraph
    assay_graph = method_json.get("assay_graph", {})
    assays = assay_graph.get("assays", [])
    dependencies = assay_graph.get("dependencies", [])

    elements = []
    for assay in assays:
        name = assay["name"]
        assay_conf = confidence.get("assay_confidences", {}).get(name, {})
        overall = assay_conf.get("overall", 50)
        color = "#16a34a" if overall >= 80 else "#ca8a04" if overall >= 50 else "#dc2626"
        n_steps = len(assay.get("steps", []))
        software_names = [s.get("software", "") for s in assay.get("steps", []) if s.get("software")]
        linked_figs = assay.get("figures_produced", [])

        elements.append({
            "data": {
                "id": name,
                "label": name,
                "n_steps": n_steps,
                "software": ", ".join(software_names[:3]),
                "figures": ", ".join(linked_figs[:4]),
                "confidence": round(overall),
                "color": color,
                "category": assay.get("method_category", "computational"),
            }
        })

    for dep in dependencies:
        elements.append({
            "data": {
                "source": dep["upstream_assay"],
                "target": dep["downstream_assay"],
                "label": dep.get("dependency_type", ""),
                "line_style": "dashed" if dep.get("dependency_type") in
                    ("normalization_reference", "co-analysis") else "solid",
            }
        })

    # Cytoscape stylesheet
    stylesheet = [
        {"selector": "node", "style": {
            "label": "data(label)",
            "background-color": "data(color)",
            "color": "#1e293b",
            "text-valign": "top",
            "text-halign": "center",
            "font-size": "13px",
            "font-family": "Inter, sans-serif",
            "width": "180px",
            "height": "80px",
            "shape": "roundrectangle",
            "border-width": 2,
            "border-color": "#e2e8f0",
            "text-wrap": "wrap",
            "text-max-width": "160px",
        }},
        {"selector": "edge", "style": {
            "curve-style": "bezier",
            "target-arrow-shape": "triangle",
            "target-arrow-color": "#475569",
            "line-color": "#475569",
            "label": "data(label)",
            "font-size": "10px",
            "text-rotation": "autorotate",
        }},
        {"selector": "edge[line_style = 'dashed']", "style": {
            "line-style": "dashed",
            "line-color": "#94a3b8",
            "target-arrow-color": "#94a3b8",
        }},
        {"selector": ":selected", "style": {
            "border-color": "#3b82f6",
            "border-width": 3,
        }},
    ]

    # Layout
    pipeline_confidence = confidence.get("overall", 0)
    conf_color = "#16a34a" if pipeline_confidence >= 80 else "#ca8a04" if pipeline_confidence >= 50 else "#dc2626"

    app.layout = html.Div([
        # Pipeline confidence banner
        html.Div([
            html.Span("End-to-End Pipeline Confidence: ", style={"fontWeight": "bold"}),
            html.Span(f"{pipeline_confidence}%", style={"color": conf_color, "fontWeight": "bold", "fontSize": "1.2em"}),
            html.Span(
                " — Validation passed" if confidence.get("validation_passed") else " — Validation failed",
                style={"color": "#64748b", "marginLeft": "8px"}
            ),
        ], style={"padding": "12px 16px", "backgroundColor": "#f8fafc", "borderBottom": "1px solid #e2e8f0", "marginBottom": "8px"}),

        # DAG canvas
        cyto.Cytoscape(
            id="assay-dag",
            elements=elements,
            stylesheet=stylesheet,
            layout={"name": "dagre", "rankDir": "TB", "spacingFactor": 1.4},
            style={"width": "100%", "height": "500px", "border": "1px solid #e2e8f0", "borderRadius": "8px"},
            responsive=True,
        ),

        # Node detail panel (populated by callback)
        html.Div(id="node-detail", style={"padding": "16px", "marginTop": "12px", "border": "1px solid #e2e8f0", "borderRadius": "8px", "display": "none"}),
    ])

    # Callback: click node → show detail
    @app.callback(
        Output("node-detail", "children"),
        Output("node-detail", "style"),
        Input("assay-dag", "tapNodeData"),
    )
    def show_node_detail(node_data):
        if not node_data:
            return [], {"display": "none"}

        name = node_data.get("label", "")
        assay = next((a for a in assays if a["name"] == name), None)
        if not assay:
            return [], {"display": "none"}

        assay_conf = confidence.get("assay_confidences", {}).get(name, {})
        steps = assay.get("steps", [])

        step_items = []
        for s in steps:
            step_items.append(html.Div([
                html.Strong(f"Step {s.get('step_number', '?')}: {s.get('description', '')}"),
                html.Div(f"Software: {s.get('software', 'unknown')} {s.get('software_version', '')}"),
                html.Div(f"Input: {s.get('input_data', '')} → Output: {s.get('output_data', '')}"),
                html.Div(f"Parameters: {s.get('parameters', {})}"),
            ], style={"padding": "8px", "marginBottom": "8px", "backgroundColor": "#f8fafc", "borderRadius": "4px"}))

        return [
            html.H4(f"{name}", style={"marginBottom": "4px"}),
            html.Div(f"{assay.get('description', '')}", style={"color": "#475569", "marginBottom": "8px"}),
            html.Div(f"Confidence: {assay_conf.get('overall', 50)}%  |  Steps: {len(steps)}  |  Category: {assay.get('method_category', 'unknown')}"),
            html.Hr(),
            *step_items,
        ], {"padding": "16px", "marginTop": "12px", "border": "1px solid #e2e8f0", "borderRadius": "8px", "display": "block"}

    return app_name
```

### Integration

In `dashboard.html`, embed alongside the existing charts:

```html
{% load plotly_dash %}
<h3>Workflow Graph</h3>
{% plotly_app name=dag_app_name ratio=0.7 %}

<h3>Summary</h3>
{% plotly_app name=dashboard_name ratio=0.5 %}
```

In `views.py` `dashboard()`, call `build_dag_app()` in addition to `build_dashboard_app()` and pass `dag_app_name` to the template context.

**dash-cytoscape dagre layout** handles topological ordering automatically, matching the `AssayGraph` dependency structure. Nodes are positioned top-to-bottom by dependency depth.

---

## Phase 2: Confidence Model

### New file: `researcher_ai/models/confidence.py`

```python
from pydantic import BaseModel, Field

class StepConfidence(BaseModel):
    has_software: bool = False
    has_version: bool = False
    has_parameters: bool = False
    has_input_output: bool = False
    parameter_completeness: float = 0.0  # 0-1
    overall: float = 50.0                # 0-100

class AssayConfidence(BaseModel):
    step_confidences: list[StepConfidence] = Field(default_factory=list)
    dataset_resolved: bool = False
    figure_confidence_mean: float = 50.0
    parse_warning_count: int = 0
    overall: float = 50.0

class PipelineConfidence(BaseModel):
    assay_confidences: dict[str, AssayConfidence] = Field(default_factory=dict)
    validation_passed: bool = False
    overall: float = 50.0
    human_edited_steps: int = 0
```

### New file: `researcher_ai_portal_app/confidence.py`

Computes confidence from the stored component payloads:

```python
def compute_confidence(components: dict) -> dict:
    """Compute pipeline confidence from stored component JSONs."""
    method = components.get("method", {})
    figures = components.get("figures", [])
    datasets = components.get("datasets", [])
    pipeline = components.get("pipeline", {})

    assay_graph = method.get("assay_graph", {})
    assays = assay_graph.get("assays", [])
    parse_warnings = method.get("parse_warnings", [])
    dataset_accessions = {d.get("accession", "").upper() for d in datasets}

    # Build figure confidence lookup: assay_name → mean subfigure confidence
    fig_conf_by_assay = _figure_confidence_by_assay(figures, assays)

    assay_confidences = {}
    total_steps = 0
    weighted_sum = 0.0

    for assay in assays:
        name = assay["name"]
        steps = assay.get("steps", [])
        step_confs = []
        for s in steps:
            sc = StepConfidence(
                has_software=bool(s.get("software")),
                has_version=bool(s.get("software_version")),
                has_parameters=bool(s.get("parameters")),
                has_input_output=bool(s.get("input_data") and s.get("output_data")),
            )
            sc.parameter_completeness = sum([sc.has_software, sc.has_version, sc.has_parameters, sc.has_input_output]) / 4.0
            sc.overall = sc.parameter_completeness * 100
            step_confs.append(sc)

        raw_source = (assay.get("raw_data_source") or "").upper()
        dataset_resolved = any(acc in raw_source for acc in dataset_accessions) if dataset_accessions else False

        warning_count = sum(1 for w in parse_warnings if name.lower() in w.lower())

        ac = AssayConfidence(
            step_confidences=step_confs,
            dataset_resolved=dataset_resolved,
            figure_confidence_mean=fig_conf_by_assay.get(name, 50.0),
            parse_warning_count=warning_count,
        )
        # Weighted composite: steps 50%, figures 20%, dataset 15%, warnings 15%
        step_mean = sum(s.overall for s in step_confs) / max(len(step_confs), 1)
        ac.overall = (
            step_mean * 0.50
            + ac.figure_confidence_mean * 0.20
            + (100.0 if dataset_resolved else 30.0) * 0.15
            + max(0, 100.0 - warning_count * 25) * 0.15
        )
        assay_confidences[name] = ac.model_dump()
        n = max(len(steps), 1)
        total_steps += n
        weighted_sum += ac.overall * n

    validation_passed = bool((pipeline.get("validation_report") or {}).get("passed", True))

    return {
        "assay_confidences": assay_confidences,
        "validation_passed": validation_passed,
        "overall": round(weighted_sum / max(total_steps, 1), 1),
        "human_edited_steps": 0,
    }
```

---

## Phase 3: Figure Gallery in Dashboard

The existing `_figure_media_rows()` and `figure_image_proxy()` logic already fetches and proxies figure images. Integrate into the dashboard by:

1. Add a "Figures" tab/section in `dashboard.html` that renders each figure alongside its DAG node.
2. In the Dash DAG app, clicking an assay node that has `figures_produced` shows the linked figure images in the detail panel using `html.Img(src=proxy_url)`.
3. The existing ground truth injection form (`FigureGroundTruthForm`) is already available in both `workflow_step.html` and `dashboard.html` — no changes needed.

---

## Phase 4: Step Editing via Dash Callbacks

The current editing model uses the Svelte JSON editor (`ComponentJSONForm`) — users edit raw JSON. This is powerful but not user-friendly for non-technical users.

**Add structured editing in the DAG detail panel:**

When a user clicks an assay node in the Cytoscape graph, the detail panel shows each `AnalysisStep` as a structured form (not raw JSON). django-plotly-dash extended callbacks have access to the Django `request` and `session`, so edits can be persisted to the database directly from Dash callbacks.

```python
@app.callback(
    Output("save-status", "children"),
    Input("save-step-btn", "n_clicks"),
    State("step-software", "value"),
    State("step-version", "value"),
    State("step-params", "value"),
    # ... other fields
)
def save_step_edit(n_clicks, software, version, params, **kwargs):
    request = kwargs.get("request")
    session = kwargs.get("session")
    # Update ComponentSnapshot via ORM
    # Recompute confidence
    # Return status message
```

**The raw JSON editor remains available** as a "power user" toggle — this preserves the existing `ComponentJSONForm` workflow for advanced users who want full control.

---

## Phase 5: nf-core and GitHub Links

In the DAG node detail panel, when `PipelineConfig` has `nf_core_pipeline` set for an assay, display:

```
🔗 nf-core/rnaseq v3.14 — https://nf-co.re/rnaseq/3.14.0
```

When `Method.code_availability` contains GitHub URLs, parse and display them with the assay they relate to.

---

## Phase 6: Rebuild Pipeline After Edits

Add a "Rebuild Pipeline" button to the dashboard. When clicked:

1. Determine which step was edited (tracked by the `source` field on `ComponentSnapshot`).
2. Compute `invalidated_steps(job, edited_step)` using the DAG dependency map (see §3 above) to find downstream dirty steps.
3. Dispatch `rebuild_from_step.delay(job_id, edited_step, llm_api_key, llm_model)` — a Celery task that re-runs only the dirty steps sequentially.
4. The frontend shows a progress indicator for the rebuild (reads from Redis, same as initial parse).
5. On completion, recompute confidence and refresh the DAG app.

If the user edited the method JSON (e.g., changed STAR parameters), only `datasets`, `software`, and `pipeline` are re-run. Paper and figure parsing are untouched. If only `software` was edited, only `pipeline` is re-run.

The rebuild button label reflects the scope: "Rebuild pipeline (3 steps)" or "Rebuild pipeline only (1 step)" — so the user knows what they're triggering before they click.

---

## Implementation Phases (Summary)

| Phase | Scope | Effort | Files Changed |
|-------|-------|--------|---------------|
| 0 | Django ORM models + Celery + Redis + migration from job_store | 3-4 days | `models.py`, `tasks.py`, `celery.py` (new), `views.py` (refactor), `settings.py`, `job_store.py` (delete) |
| 1 | dash-cytoscape DAG visualization | 2-3 days | `dag_app.py` (new), `dashboards.py` (update), `dashboard.html` (update) |
| 2 | Confidence model + computation | 1-2 days | `confidence.py` (new in both packages), `dag_app.py` (wire in) |
| 3 | Figure gallery in dashboard | 1 day | `dashboard.html` (update), `dag_app.py` (add images to detail) |
| 4 | Structured step editing via Dash callbacks | 2-3 days | `dag_app.py` (expand), `workflow_step.html` (optional) |
| 5 | nf-core / GitHub link display | 0.5 day | `dag_app.py` (node detail) |
| 6 | Rebuild with DAG-aware invalidation | 1-2 days | `tasks.py` (rebuild task), `views.py` (rebuild action), `dashboard.html` (button) |

**Total: ~12-16 days of implementation**, all within the existing Django project structure.

---

## New Dependencies

```
# Task queue and caching
celery[redis]>=5.3         # Background task execution
redis>=5.0                 # Broker + cache backend
django-celery-results      # Optional: store task results in Django ORM

# DAG visualization
dash-cytoscape>=1.0.0      # Cytoscape.js for Dash (DAG rendering)
dash-bootstrap-components  # Optional: cleaner Dash layout primitives
```

The `dagre` layout algorithm is bundled with dash-cytoscape and requires no additional install.

Removed from earlier draft: `django-encrypted-model-fields` — API keys are no longer stored in the database.

---

## Infrastructure Requirements

```
Redis server (local or managed)
  - Celery broker: redis://localhost:6379/0
  - Django cache: redis://localhost:6379/1
  - Dev: brew install redis / docker run -p 6379:6379 redis:7
  - Prod: AWS ElastiCache, GCP Memorystore, or Redis Cloud

Celery worker process
  - Dev: celery -A researcher_ai_portal worker -l info
  - Prod: systemd unit or Docker container alongside gunicorn
```

---

## Files to Create

```
researcher_ai_portal_app/
├── models.py              # WorkflowJob, ComponentSnapshot, PaperCache
├── tasks.py               # Celery tasks: run_workflow_step, rebuild_from_step
├── dag_app.py             # Dash Cytoscape DAG application
├── confidence.py          # Confidence computation logic
├── migrations/
│   └── 0001_initial.py    # Auto-generated

researcher_ai_portal/
├── celery.py              # Celery app configuration
```

## Files to Modify

```
researcher_ai_portal_app/
├── views.py               # Dispatch to Celery instead of sync _run_step(); read progress from Redis;
│                          #   add user ownership checks; remove llm_api_key from ORM; add rebuild action
├── dashboards.py          # Add DAG app call alongside existing charts
├── job_store.py           # DELETE (replaced by ORM + Redis)
├── templates/
│   ├── dashboard.html     # Add DAG embed, figure gallery, rebuild button
│   ├── home.html          # Add "My Previous Parses" list
│   └── progress.html      # Unchanged logic (still polls job_status), but job_status now reads Redis

researcher_ai_portal/
├── settings.py            # Add Celery config, Redis cache backend, dash_cytoscape to PLOTLY_COMPONENTS
├── __init__.py            # Import celery app (standard Celery-Django pattern)
```

## Files Unchanged

```
researcher_ai_portal_app/
├── forms.py               # ComponentJSONForm + FigureGroundTruthForm stay as-is
├── templates/
│   └── workflow_step.html # Keep step-by-step flow as-is (dispatch via Celery, but same UX)

researcher_ai_portal/
├── urls.py                # django_plotly_dash route already registered at line 30; no new patterns needed
├── wsgi.py / asgi.py      # Unchanged
```
