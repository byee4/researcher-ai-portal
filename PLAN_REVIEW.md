# INTERFACE_PLAN.md — Implementation Review & Amendments

Reviewed against the current state of `researcher-ai-portal/` as of 2026-04-06.

---

## Phase-by-Phase Scorecard

| Phase | Planned | Implemented | Verdict |
|-------|---------|-------------|---------|
| 0: ORM Models | WorkflowJob, ComponentSnapshot, PaperCache | All three models created, migration generated, `job_store.py` rewritten as ORM wrapper | **Done — with issues** |
| 1: DAG Visualization | dash-cytoscape interactive graph | `dag_app.py` with Cytoscape + fallback, wired into dashboard | **Done — with issues** |
| 2: Confidence Model | Assay + pipeline-level scoring | `confidence.py` with weighted composite, wired into dashboard context | **Done** |
| 3: Figure Gallery | Images rendered alongside DAG | `figure_media_rows` in dashboard template, proxy URLs | **Done** |
| 4: Structured Step Editing | Inline editing without raw JSON | `save_structured_step` action + template forms per assay | **Done — with issues** |
| 5: nf-core / GitHub Links | Display pipeline + code links | Extracted in `dag_app.py` `_build_elements`, shown in node detail | **Done — minimal** |
| 6: Rebuild Pipeline | Re-run downstream steps after edits | `rebuild_from_step` Celery task + `invalidated_steps()` + dashboard button | **Done — with issues** |
| Bonus: Celery async | Not in plan | `tasks.py`, `celery.py`, settings wired | **Added — with issues** |
| Bonus: Redis cache | Not in plan | Settings for Redis + LocMem fallback, progress caching | **Added** |

---

## Issues Found (Ordered by Severity)

### CRITICAL — Will cause runtime errors or data loss

#### 1. `_llm_env` is process-global and not safe for Celery workers

**File:** `views.py` lines 164–189

`_llm_env()` sets `os.environ["OPENAI_API_KEY"]` and `os.environ["ANTHROPIC_API_KEY"]` during parsing, then restores them in a `finally` block. When two Celery tasks run concurrently in the same worker process, they overwrite each other's environment variables. User A's API key leaks into User B's LLM call.

**Fix:** Pass `llm_api_key` and `llm_model` as explicit arguments through the parser call chain instead of environment variables. The researcher-ai parsers already accept `llm_model` as a constructor arg; extend them to accept `api_key` as well, or use a thread-local rather than `os.environ`. As a short-term patch, force `CELERY_WORKER_CONCURRENCY=1` (prefork with 1 process) or use `--pool=solo`.

#### 2. `llm_api_key` stored in Django session unencrypted

**File:** `views.py` line 1139

```python
request.session["llm_api_key"] = llm_api_key
```

Django sessions are stored in the database (default `django.contrib.sessions.backends.db`) as base64-encoded pickles. Anyone with database read access can extract every user's LLM API key. The plan specified encrypted-at-rest storage.

**Fix:** Either encrypt the key before session storage (using `cryptography.fernet` with `SECRET_KEY`-derived key), or don't store it at all — pass it through the Celery task chain only and let it expire. If you keep session storage, set `SESSION_ENGINE = 'django.contrib.sessions.backends.cache'` with Redis so keys live only in memory and honor `session.set_expiry(7200)`.

#### 3. `PaperCache` is never read or written

**File:** `models.py` lines 65–75, `views.py` (entire file)

The `PaperCache` model exists and has a migration, but `_run_step()` never checks it before calling `PaperParser.parse()`. The plan's primary efficiency feature — "avoid re-parsing identical PMIDs" — is not implemented.

**Fix:** In `_run_step()`, before calling `PaperParser.parse()`, check `PaperCache.objects.filter(canonical_id=f"{source_type}:{source}").first()`. If found, load `paper_json` and `figures_json` directly. After a fresh parse, write to `PaperCache`. Include a `llm_model` check so cached results from a different model don't pollute.

#### 4. `get_job()` user filtering has a fallback bypass

**File:** `job_store.py` lines 226–249 (the `get_job` function — not shown in reads but present)

The `get_job()` function accepts an optional `user` kwarg. When `user=None` (the default), it falls back to `_FALLBACK_JOBS` dict or queries without user filtering. The `tasks.py` module calls `get_job(job_id)` without a user (line 43, line 77), which means Celery tasks bypass user isolation.

**Fix:** This is acceptable for backend tasks (they need to access any job), but document it explicitly. Add a comment in `get_job()` explaining that `user=None` is intentionally allowed for Celery workers, and that all view-level calls MUST pass `user=request.user`.

---

### HIGH — Incorrect behavior or UX problems

#### 5. DAG app is cached forever — stale after edits

**File:** `dag_app.py` line 100

```python
if app_name in _DAG_APPS:
    return app_name
```

The `_DAG_APPS` dict caches the Dash app by `job_id`. After a user edits a step or rebuilds the pipeline, the DAG app still shows the old graph because the cache key hasn't changed. The dashboard will display outdated confidence scores and node data until the Django process restarts.

**Fix:** Either bust the cache by appending a version suffix (e.g., `f"researcher_ai_dag_{job_id}_{payload_hash}"`), or remove the cache entirely and rebuild on every dashboard load. The Dash app construction is lightweight (no LLM calls), so rebuilding is acceptable. Same issue exists in `dashboards.py` `_APPS` dict (line 7).

#### 6. `rebuild_from_step` calls `run_workflow_step` synchronously inside a Celery task

**File:** `tasks.py` lines 98–101

```python
for step in dirty:
    run_workflow_step(job_id, step, llm_api_key=llm_api_key, llm_model=llm_model)
```

This calls the function directly, not `.delay()`. The rebuild runs all invalidated steps sequentially inside a single Celery task. If one step takes 10 minutes, the entire chain blocks that worker. More importantly, if the worker dies mid-chain, partially rebuilt state is persisted but the remaining steps are lost.

**Fix:** Use a Celery chain or group:
```python
from celery import chain
chain(
    run_workflow_step.s(job_id, step, llm_api_key=llm_api_key, llm_model=llm_model)
    for step in dirty
).apply_async()
```
Or at minimum call `.delay()` sequentially with status checks between steps.

#### 7. `invalidated_steps` logic is too aggressive

**File:** `views.py` lines 86–97

The function marks a step as dirty if *any* of its dependencies are dirty. But it also skips the edited step itself (returns only downstream steps). If a user edits `method`, the function returns `["datasets", "software", "pipeline"]`. This is correct for a cascade rebuild, but the rebuild task then re-runs all three even if only the pipeline step actually depends on the method change. For example, editing a step's software name doesn't invalidate datasets at all.

**Fix:** This is conservative and safe — not a bug. But for efficiency, consider fine-grained invalidation: track *which fields* changed in the edit and only invalidate steps that actually consume those fields. Low priority.

#### 8. Structured step editing doesn't update `human_edited_steps` in confidence

**File:** `views.py` line 1489 vs `confidence.py` line 104

The structured step editor persists edits as `"corrected_structured_dashboard"`, but `compute_confidence()` always returns `"human_edited_steps": 0`. The plan specified that user-edited steps should boost confidence (since human review reduces uncertainty).

**Fix:** In `compute_confidence()`, count `ComponentSnapshot` rows where `source` starts with `"corrected"` or `"ground_truth"`, and pass that count into the confidence dict. Use it to adjust the overall score (e.g., `+5` per edited assay, capped at `+20`).

#### 9. `home.html` queries `recent_jobs` but doesn't render them

**File:** `views.py` line 1072 passes `recent_jobs`, but `home.html` has no `{% for job in recent_jobs %}` block.

**Fix:** Add a "My Previous Parses" section to `home.html`:
```html
{% if recent_jobs %}
<h2>Recent Parses</h2>
{% for job in recent_jobs %}
  <a href="{% url 'dashboard' job_id=job.id %}">{{ job.input_display }} — {{ job.status }}</a>
{% endfor %}
{% endif %}
```

---

### MEDIUM — Missing features or incomplete implementation

#### 10. No DOI or URL input support

**File:** `views.py` `start_parse()` line 1134

The form only handles PMID or PDF. The plan and the backend both support DOI and URL sources (`PaperSource.DOI`, `PaperSource.URL`), but the home page has no input field for them, and `source_type` is hardcoded to `"pmid"` or `"pdf"`.

**Fix:** Add source type auto-detection in `start_parse()`:
```python
if pmid.startswith("10."):
    source_type = "doi"
elif pmid.startswith(("http://", "https://")):
    source_type = "url"
elif pmid.startswith("PMC"):
    source_type = "pmcid"
else:
    source_type = "pmid"
```
Update the form placeholder text to read "PMID, DOI, PMCID, or URL".

#### 11. DAG node detail panel is read-only — no editing from the graph

**File:** `dag_app.py` lines 190–210

The node detail panel in the Cytoscape callback shows assay info but has no edit controls. The plan specified that clicking a node should open editable fields. Currently, structured editing is only available in a separate section below the graph.

**Fix:** This is a UX preference. The current approach (separate "Structured Step Editing" section) works but breaks the "click node → edit" flow. To implement in-graph editing, use django-plotly-dash extended callbacks with `session` access to persist edits. However, the current separated approach is simpler and avoids callback complexity. Recommend keeping the current approach but adding a "Jump to edit" link in the node detail that scrolls/anchors to the relevant assay in the structured editing section.

#### 12. No export buttons

The plan specified download buttons for Snakefile, Nextflow, Jupyter, and conda YAML. The dashboard doesn't have these.

**Fix:** Add an "Export" section to `dashboard.html` with links to a new view that reads `ComponentSnapshot` for the pipeline step and returns the file content with appropriate `Content-Type` and `Content-Disposition` headers. Minimal work — one new view with 6 branches.

#### 13. Confidence banner doesn't distinguish validation failure clearly

**File:** `dashboard.html` lines 47–53

The template shows "validation passed" or "validation failed" as parenthetical text. The plan specified a colored banner that turns red on failure.

**Fix:** Wrap in a styled div:
```html
<div style="padding:12px; border-radius:8px; background:{% if confidence.validation_passed %}#e7f6ea{% else %}#fdecec{% endif %}">
```

---

### LOW — Polish, testing, and tech debt

#### 14. ~60% of tests are architecture grep checks, not behavioral tests

The test suite has ~44 tests, but ~25 of them read source files and assert that certain strings exist in the code. These verify that the implementation was *written* but not that it *works*. There are zero Django `TestCase` / `RequestFactory` tests for any view.

**Fix:** Add a `test_views.py` with `RequestFactory` tests for the core flows:
- `start_parse` creates a `WorkflowJob` tied to `request.user`
- `workflow_step` returns 404 for wrong user
- `dashboard` returns 404 for non-existent job
- `job_status` returns cached progress when available
- `save_structured_step` updates the correct assay step

#### 15. `ComponentSnapshot` has no `updated_at` field

**File:** `models.py` line 43

The model has `created_at` (auto_now_add) but no `updated_at`. Since `unique_together = [("job", "step")]` with `update_or_create`, the row is overwritten in place, but `created_at` stays fixed at the original creation time. You can't tell when a component was last modified.

**Fix:** Add `updated_at = models.DateTimeField(auto_now=True)` and generate a migration.

#### 16. `_FALLBACK_JOBS` in `job_store.py` is a hidden memory leak

**File:** `job_store.py` line 12

The in-memory fallback dict `_FALLBACK_JOBS` grows unbounded when the ORM path raises exceptions (e.g., unmigrated database). In production with the ORM working, this dict stays empty and is harmless. But if a test suite or dev environment triggers the fallback path, jobs accumulate forever.

**Fix:** Add a TTL or max-size check. Or remove the fallback entirely and let ORM failures surface as 500s — better to fail loudly than silently lose data.

#### 17. Inline CSS in all templates — no shared stylesheet

All four templates duplicate the same CSS block. Changes require editing every file.

**Fix:** Extract to `researcher_ai_portal_app/static/researcher_ai_portal/style.css` and load via `{% load static %}`. Low priority but reduces maintenance burden.

---

## Amended Architecture Recommendations

### A. API key flow redesign (addresses issues #1 and #2)

The current flow is: user submits API key → stored in Django session → passed to Celery task via args → set in `os.environ` during parse.

Proposed flow:
1. User submits API key → encrypted with Fernet (key derived from `SECRET_KEY`) → stored in Redis cache with TTL=7200 keyed by `f"api_key:{job_id}"`
2. Celery task reads from Redis cache by `job_id`
3. Parser receives API key as explicit constructor arg (no `os.environ`)
4. After workflow completes, cache entry auto-expires

This eliminates both the session storage risk and the `os.environ` concurrency problem.

### B. DAG app cache invalidation (addresses issue #5)

Replace the module-level `_DAG_APPS` dict with a hash-based cache:

```python
def build_dag_app(job_id, method_json, ...):
    content_hash = hashlib.md5(
        json.dumps(method_json, sort_keys=True).encode()
    ).hexdigest()[:8]
    app_name = f"researcher_ai_dag_{job_id}_{content_hash}"
    if app_name in _DAG_APPS:
        return app_name
    # ... build app ...
```

This ensures the DAG refreshes whenever the method payload changes, while still caching for repeated dashboard loads of the same state.

### C. PaperCache integration (addresses issue #3)

In `_run_step()`, wrap the paper parsing step:

```python
if step == "paper":
    canonical = f"{job.get('source_type')}:{job.get('source')}"
    cached = PaperCache.objects.filter(
        canonical_id=canonical, llm_model=model
    ).first()
    if cached:
        _persist_component(job_id, "paper", cached.paper_json, "cached")
        if cached.figures_json:
            _persist_component(job_id, "figures", cached.figures_json, "cached")
        return
    # ... existing parse logic ...
    PaperCache.objects.update_or_create(
        canonical_id=canonical,
        defaults={
            "paper_json": paper.model_dump(mode="json"),
            "llm_model": model,
        },
    )
```

### D. Test strategy (addresses issue #14)

Priority test additions:

1. **`test_views_http.py`** — Django `RequestFactory` + `@pytest.mark.django_db` tests for each view endpoint, asserting status codes, redirects, and user isolation.
2. **`test_job_store_orm.py`** — Tests for `create_job`, `update_job`, `get_job` with real database, verifying `ComponentSnapshot` writes and user filtering.
3. **`test_tasks_celery.py`** — Tests with `CELERY_TASK_ALWAYS_EAGER=True` (already in settings), verifying that `run_workflow_step` and `rebuild_from_step` update job state correctly.

---

## Priority Implementation Order

| Priority | Issue | Effort | Impact |
|----------|-------|--------|--------|
| 1 | #1 `_llm_env` concurrency | 2h | Prevents API key cross-contamination |
| 2 | #2 Session key encryption | 1h | Security |
| 3 | #5 DAG cache invalidation | 30m | UX — stale graph after edits |
| 4 | #3 PaperCache integration | 1h | Efficiency — saves LLM cost |
| 5 | #9 Recent jobs in home.html | 15m | UX — users can't find old parses |
| 6 | #12 Export buttons | 2h | Feature completeness |
| 7 | #6 Rebuild task chaining | 1h | Reliability |
| 8 | #10 DOI/URL auto-detect | 30m | Feature completeness |
| 9 | #14 View-level tests | 3h | Quality |
| 10 | #8 human_edited_steps | 30m | Confidence accuracy |
