# Unified Plan: UX Redesign + FastAPI Integration (Phases 0–5)

This plan merges the UX redesign (source → pipeline journey, structured editors, confidence-driven dashboard) with the remaining FastAPI integration work from `FASTAPI_INTEGRATION_PLAN.md` Phases 3–4. FastAPI Phases 1–2 are already implemented.

---

## Design Critiques Incorporated

### 1. Frontend Architecture — App Island Boundary

The original plan mixed Django server-side rendered HTML partials with React Flow state. This is a DOM-syncing risk: if Django swaps a partial while React controls state, the tree gets clobbered.

**Resolution:** Define a strict "App Island" boundary.

- Django owns: global layout (`base.html`), navigation, authentication, breadcrumbs, and the initial page shell.
- A single React application mounts the entire interactive dashboard: Confidence Command Center, the enhanced stepper, the React Flow graph, and all structured editors.
- Initial data passes through `json_script` blocks (XSS-safe):

  ```html
  {{ graph_data|json_script:"initial-graph-state" }}
  {{ confidence_data|json_script:"initial-confidence" }}
  {{ actionable_items|json_script:"initial-actions" }}
  {{ components_json|json_script:"initial-components" }}
  ```

- FastAPI (`/api/v1/`) handles 100% of state mutations from that point. Django form POSTs are removed from the dashboard; the dashboard becomes a purely API-driven SPA shell served by Django.
- The existing inline ESM pipeline builder in `dashboard.html` is the seed of this React app; it expands to cover the full dashboard in Phase 2 without a build step change.

### 2. Confidence Actionability — Strict Schema with Deep Links

`compute_actionable_items` must emit a strict schema, not free-text strings. Clicking a warning in the Command Center must automatically: switch to the correct tab, scroll to the field, and flash it yellow. The dashboard becomes an interactive to-do list, not a static report.

**Actionable item schema:**
```python
{
    "id": str,                  # stable, hashable key for deduplication
    "reason": str,              # human-readable description
    "severity": "high" | "medium" | "low",
    "confidence_impact": float, # estimated % gain if resolved
    "fix_target_tab": str,      # tab name to switch to
    "fix_target_node_id": str | None,  # React Flow node ID if applicable
    "fix_target_field": str | None,    # CSS selector or field key to focus
    "action_url": str | None,  # deep-link URL (for non-React surfaces)
    "fix_label": str,           # CTA button text
}
```

The React dashboard consumes this schema: `onClick → showTab → scrollIntoView → classList.add('field-highlight')`.

### 3. Structured Editors — PATCH + Debounced Autosave

Bioinformatic forms are long; users should never lose edits. Change `PUT` to `PATCH` across all structured save endpoints. `ComponentSnapshot.payload` is a large JSON object; `PATCH` sends only the changed sub-field, reducing payload and preventing race conditions when multiple fields are edited quickly.

**Endpoint change:**
```
PATCH /api/v1/jobs/{job_id}/components/{step}
Body: { "path": "assay_graph.assays[1].steps[0].software_version", "value": "2.1.3" }
```

Autosave: debounce 800ms after last keypress. Show per-field "saving…" / "saved ✓" / "error" inline state (not a page-level toast).

### 4. Phase 4 — Pre-Flight Visual Linting

The compile endpoint must return `ValidationIssue[]` objects tied to specific React Flow Node IDs — not a generic 400 error.

**Compile response schema:**
```python
{
    "valid": bool,
    "execution_order": list[str],
    "issues": [
        {
            "node_id": str,      # React Flow node ID
            "severity": "error" | "warning",
            "message": str,      # "Missing BAM file input"
            "field": str | None  # node data field causing the issue
        }
    ]
}
```

UX: "Dry Run / Validate" button. On failure, React Flow outlines broken nodes in red with inline tooltips. On success, shows execution order + "Run Pipeline" confirmation dialog.

### 5. Phase 4 — Log Pagination for Polling

The `/api/v1/jobs/{job_id}/logs` polling endpoint must support incremental fetching. Downloading a full log file every 3–5 seconds is a browser freeze risk.

**Endpoint:**
```
GET /api/v1/jobs/{job_id}/logs?since_ts=<ISO8601>&limit=50
```
Returns only log entries after `since_ts`. Client tracks its watermark. Fetches delta only.

---

## Prior Work (Already Completed)

From `FASTAPI_INTEGRATION_PLAN.md` Phases 1–2:
- ASGI co-habitation: `researcher_ai_portal/asgi.py` routes `/api/v1/*` to FastAPI, everything else to Django
- FastAPI package: `researcher_ai_portal_app/api/` with `routes.py`, `schemas.py`, `repository.py`, `dependencies.py`
- Pydantic schemas: `GraphNode`, `GraphEdge`, `WorkflowGraph`, `ParsePublicationRequest/Response`, `JobStatusResponse`
- Core endpoints: GET/PUT `/api/v1/graphs/{job_id}`, GET `/api/v1/jobs/*`, POST `/api/v1/parse-publication`
- Auth dependency: `get_current_user()` reads Django `sessionid` cookie via Django session backend
- Repository layer: async ORM wrappers, `save_graph_state`, `get_job_with_components`
- `WorkflowJob.graph_data` JSONField stores React Flow state
- Graph layout: `generate_default_graph()`, `generate_tool_graph()`
- React Flow inline ESM: `ToolNode`, `ParamEditor`, `ListEditor`, save via `PUT /api/v1/graphs/{job_id}`
- Confidence system: `compute_confidence()` decomposing into per-factor step booleans

---

## Phase 0: Confidence Command Center

**Goal:** First screen on dashboard arrival shows "pipeline is X% ready, here's what to fix." Implements the strict actionable item schema with deep links.

**Files modified:**
- `confidence.py` — `compute_actionable_items(components, confidence_result) -> list[dict]`
- `views.py` — pass `actionable_items` + `confidence` JSON to dashboard template
- `dashboard.html` — new Command Center tab (default), reordered tabs

### 0a: `confidence.py` — `compute_actionable_items`

Emits ranked, schema-validated items. Severity weights mirror confidence formula weights:

| Factor | Weight | Effort |
|--------|--------|--------|
| has_software | 12.5% | low (text field) |
| has_version | 12.5% | low |
| has_parameters | 12.5% | medium (JSON editor) |
| has_input_output | 12.5% | low |
| dataset_resolved | 15.0% | medium (re-run datasets step) |
| figure_evidence | 20.0% | high (re-run figures) |
| warnings | 15.0% | high (re-run method) |

Confidence impact = weight × (1.0 − current_value). Sorted descending by impact.

### 0b: `views.py` — dashboard context

Add to dashboard view context:
- `actionable_items_json` — serialized list for `json_script` injection
- `confidence_json` — full confidence dict for `json_script` injection
- Keep existing `confidence` dict for server-side rendered ring (no React dependency for first paint)

### 0c: `dashboard.html` — Command Center tab

New default tab replaces Overview. Three sections:

**Section 1 — Score bar:**
- Enlarged 128px confidence ring (server-side rendered for instant first paint)
- Overall % + validation badge + "X of Y factors resolved" text
- Per-assay confidence cards: stacked factor bar (step 50% / figure 20% / dataset 15% / warnings 15%) + factor pills

**Section 2 — Next recommended action banner:**
- Highest-severity unresolved item from `actionable_items`
- Single prominent CTA button calling `activateAction(item)` JS

**Section 3 — Actionable items list:**
- Each card: reason, severity badge (red/amber/green), confidence impact "+X%", CTA button
- CTA calls `activateAction({fix_target_tab, fix_target_field, fix_target_node_id})`:
  ```js
  function activateAction(item) {
      showTab(item.fix_target_tab);
      if (item.fix_target_field) {
          const el = document.querySelector(item.fix_target_field);
          if (el) { el.scrollIntoView({behavior:'smooth', block:'center'}); el.focus(); el.classList.add('field-highlight'); }
      }
  }
  ```

**Section 4 — Dataset readiness table:**
- Compact table: accession, source type, resolution status, linked assay. Clicking unresolved row calls `showTab('datasets')`.

### 0d: Tab reorder

```
Command Center (default) → Pipeline Builder → Datasets → Figures → Step Editing → Workflow Graph → Advanced
```

### 0e: `workflow_step.html` — stepper enhancement

Stepper circles gain:
- Color coding from `component_meta.status` and confidence contribution (green/amber/red/grey)
- Small dot badge with count of actionable items per step
- CSS `field-highlight` class with yellow pulse animation

**Scope: 2–3 sessions**

**Test criteria:**
- Command Center is default tab; server-renders confidence ring on first paint
- Actionable items list non-empty when confidence < 100%
- `activateAction()` correctly switches tab, scrolls, and highlights field
- "Next recommended action" points to highest-impact unresolved item
- Stepper color matches actual step status

---

## Phase 1: Pipeline Builder + FastAPI Frontend Wiring

**Goal:** Pipeline Builder fully wired to FastAPI with save/unsaved state. CSRF exempt. Status polling via FastAPI.

Absorbs `FASTAPI_INTEGRATION_PLAN` Phase 3.

**Files modified:**
- `dashboard.html` — save indicator, React fetch to `/api/v1/`
- Pipeline Builder React — `savedState` machine, `beforeunload`, polling
- `api/routes.py` — verify CSRF exemption at ASGI boundary

**Implementation:**
1. Confirm FastAPI path routing bypasses Django CSRF middleware (existing ASGI router already provides this)
2. Add `savedState: "saved" | "unsaved" | "saving" | "error"` React state; render chip in builder header
3. Add `beforeunload` warning on unsaved changes
4. Enhance `ToolNode` with confidence badges from `GraphNode.data.confidence_factors`
5. Add "Edit in Step Editor" link from node detail panel: `activateAction({fix_target_tab: 'editing', fix_target_field: '#assay-{name}-step-{i}-version'})`
6. Wire status polling: `GET /api/v1/jobs/{job_id}/status` every 2s during active jobs

**Scope: 2–3 sessions**

---

## Phase 2: Structured Editors + PATCH Save Endpoint

**Goal:** Typed form editors for datasets, software, pipeline config. PATCH-based saves with debounced autosave.

**Files modified:**
- `api/routes.py` — PATCH component endpoint
- `api/schemas.py` — `ComponentPatchRequest`, `ComponentSaveResponse`
- `api/repository.py` — async patch + recompute confidence
- `dashboard.html` — Datasets tab, enhanced Step Editing, pipeline config

### 2a: PATCH endpoint (1–2 sessions)

```
PATCH /api/v1/jobs/{job_id}/components/{step}
Body: { "path": "assay_graph.assays[1].steps[0].software_version", "value": "2.1.3" }
Response: { "payload": <validated full payload>, "confidence": <updated>, "actionable_items": [...] }
```

Path uses dot-bracket notation. Repository resolves path, mutates payload, validates via Pydantic, persists, recomputes confidence, returns delta.

### 2b: Dataset editor (1–2 sessions)

New Datasets tab: table with inline editing. Each row: accession (with format validation), source type, organism, platform, description, resolution badge. Add/delete/inline save with per-field autosave debounce.

### 2c: Software editor (1 session)

Table in Step Editing tab: name, version (missing version shows "+X% confidence" impact inline), language select, URL, citation. Autosave debounce.

### 2d: Pipeline config editor (1–2 sessions)

Step list in Pipeline Builder tab behind "Configure" button: name, software select, depends_on multi-chips, parameters key-value. Drag to reorder. Dependency constraint validation client-side.

**Scope: 4–6 sessions**

---

## Phase 3: Confidence Correction Loops

**Goal:** Edits update confidence immediately. Deep links work from bookmarks. Rebuild streams progress.

**Files modified:**
- `confidence.py` — expose per-factor breakdown as JSON-serialisable format
- `dashboard.html` — URL hash deep-linking, rebuild progress overlay
- `api/routes.py` — `GET /api/v1/jobs/{job_id}/confidence`

**Implementation:**
1. React updates confidence on `ComponentSaveResponse` without page reload
2. `actionable_items` refreshed from PATCH response; Command Center re-renders
3. URL hash deep-links: `#fix-assay=X&step=Y&field=version` — parsed on load by `activateAction()`
4. `GET /api/v1/jobs/{job_id}/confidence` — lightweight endpoint for React to poll after saves
5. Rebuild progress overlay: polls `job_status` every 1.5s, shows current step + last 5 log lines

**Scope: 2 sessions**

---

## Phase 4: Graph Compilation + Execution + Log Streaming

**Goal:** Compile visual graph to executable pipeline. Visual lint before execute. Paginated log streaming.

Absorbs `FASTAPI_INTEGRATION_PLAN` Phase 4.

**Files modified:**
- `api/routes.py` — compile, execute, logs endpoints
- `api/schemas.py` — `ValidationIssue`, `CompileResponse`, `ExecuteResponse`, `LogsResponse`
- `api/repository.py` — compile validation, execute dispatch
- `dashboard.html` — "Dry Run" button, execution progress panel, per-node error rendering

### Compile endpoint (1–2 sessions)

```
POST /api/v1/graphs/{job_id}/compile
Response: { "valid": bool, "execution_order": [...], "issues": [{ "node_id", "severity", "message", "field" }] }
```

Validates DAG against `STEP_DEPENDENCIES`. On failure, React Flow outlines broken nodes in red with tooltips. On success, shows execution order + "Run Pipeline" confirmation.

### Execute endpoint (1 session)

```
POST /api/v1/graphs/{job_id}/execute
Response: 202 { "job_id": str }
```

Dispatches via `_dispatch_workflow_step` through `sync_to_async`. Execution adapters (Snakemake, Nextflow, Slurm) called here.

### Paginated logs endpoint (1 session)

```
GET /api/v1/jobs/{job_id}/logs?since_ts=<ISO8601>&limit=50
Response: { "entries": [...], "next_since_ts": str, "has_more": bool }
```

Client tracks `since_ts` watermark. Polls every 3–5s. Fetches delta only — no full log download.

### WebSocket (optional, post-MVP)

`WS /api/v1/ws/jobs/{job_id}/logs` — push-based alternative to polling. Requires Redis channel layer for multi-worker. Skip for MVP.

**Scope: 3–4 sessions (polling only)**

---

## Phase 5: Journey Progression Polish

**Goal:** Full source → pipeline journey feels guided and complete.

**Files modified:**
- New: `_stepper.html` shared partial
- `workflow_step.html` — use `_stepper.html`
- `progress.html` — connect to stepper, guide to first review step on completion
- `dashboard.html` — unified stepper above tabs, completion state

**Implementation:**
1. Extract stepper → `_stepper.html`. Include in both `workflow_step.html` and `dashboard.html`
2. Stepper states (existing data only, no new fields):
   - Not started: `component_meta.status == "missing"` → grey
   - Running: `job.current_step == step && job.status == "in_progress"` → blue pulsing
   - Needs review: confidence contribution < 70% → amber + warning dot
   - Confirmed: contribution ≥ 70% → green + checkmark
3. Progress page: on job completion, show stepper with highest-priority review step highlighted + CTA
4. Dashboard: completion banner (confidence ≥ 90% + all components found/inferred + 0 actionable items)

**Scope: 1–2 sessions**

---

## Dependency Graph

```
Phase 0 ─── Confidence command center             (no dependencies)
Phase 1 ─── Pipeline Builder + FastAPI wiring     (parallel with 0)
    ↓
Phase 2a ── PATCH component endpoint
    ↓
Phase 2b-d ─ Dataset, software, pipeline editors  (parallel)
    ↓
Phase 3 ─── Confidence correction loops
Phase 4 ─── Compile + execute + logs              (can start after 2a)
    ↓
Phase 5 ─── Journey progression polish
```

---

## API Surface

| Method | Path | Phase | Note |
|--------|------|-------|------|
| PATCH | `/api/v1/jobs/{job_id}/components/{step}` | 2a | replaces PUT, path-based partial update |
| GET | `/api/v1/jobs/{job_id}/confidence` | 3 | lightweight confidence poll |
| GET | `/api/v1/jobs/{job_id}/logs` | 4 | `?since_ts&limit` paginated |
| POST | `/api/v1/graphs/{job_id}/compile` | 4 | returns `ValidationIssue[]` per node ID |
| POST | `/api/v1/graphs/{job_id}/execute` | 4 | 202 dispatch |
| WS | `/api/v1/ws/jobs/{job_id}/logs` | 4+ | optional, post-MVP |

All existing endpoints unchanged.

---

## Scope Summary

| Phase | Sessions | Primary work |
|-------|----------|--------------|
| 0 | 2–3 | Confidence decomposition, Command Center tab, stepper |
| 1 | 2–3 | React fetch wiring, save indicator, polling |
| 2a | 1–2 | PATCH endpoint + schemas |
| 2b–2d | 4–5 | Three structured editors |
| 3 | 2 | Deep-linking, confidence refresh |
| 4 | 3–4 | Compile + execute + paginated logs |
| 5 | 1–2 | Stepper extraction, completion state |
| **Total** | **15–21** | |

---

## Risk Mitigation

| Risk | Mitigation |
|------|------------|
| App Island scope creep | Expand React coverage incrementally per phase; Django templates remain for non-interactive pages |
| PATCH path injection | Validate path against a whitelist of allowed JSON Pointer paths per step |
| Large `dashboard.html` | Extract tabs into partials during Phase 0 to keep files manageable |
| Autosave conflict on concurrent edits | Last-write-wins for single-user MVP; add ETag optimistic concurrency in Phase 3 |
| WebSocket timeline | Polling is default; WebSocket is explicitly deferred to post-MVP |
| Mobile layout | Add responsive breakpoints in Phase 0; test at 375px, 768px, 1024px |
| `sync_to_async` hot paths | Repository layer centralises all ORM calls; profile in Phase 2a |

---

## What Stays Unchanged

- All existing Django routes and form POST handlers (until superseded per phase)
- All existing FastAPI endpoints
- `ComponentSnapshot.payload` as source of truth
- `_validate_component_json()` validation gate for all saves
- Raw JSON editors in the Advanced tab
- `STEP_ORDER` and `STEP_DEPENDENCIES`
- No new Django models or migrations
- No frontend framework migration (ESM inline React, no bundler)
- Plotly/Dash and Cytoscape visualisations
