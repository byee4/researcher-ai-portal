# Unified Plan: UX Redesign + FastAPI Integration (Phases 3–4)

This plan merges the UX redesign (source → pipeline journey, structured editors, confidence-driven dashboard) with the remaining FastAPI integration work from `FASTAPI_INTEGRATION_PLAN.md` Phases 3–4. FastAPI Phases 1–2 are already implemented: the `api/` package exists with routes, schemas, repository, auth dependency (`get_current_user`), and the ASGI router dispatches `/api/v1/*` to FastAPI. What remains is wiring the React frontend to those endpoints (Phase 3) and adding graph compilation + real-time execution feedback (Phase 4).

---

## Critique of Draft UX Plan

### What the draft gets right

The draft correctly identifies the three highest-value surfaces in the existing dashboard — confidence metrics, the visual pipeline builder, and dataset views — and proposes elevating them from tab-parity peers to the dominant first-screen experience. This is the right call. Today a user landing on the dashboard sees the Overview tab (parser status grid + Plotly charts + a sidebar with assay confidence) while the Pipeline Builder and dataset information are buried in separate tabs. A biologist who just finished parsing wants to know "is this pipeline ready?" and "what needs fixing?" — not "here are six tabs, good luck."

The plan to replace JSON-first editing with structured form editors is also well-grounded. The codebase already has a `save_structured_step` action in the dashboard POST handler that edits individual assay steps (software, version, input_data, output_data, parameters) via form fields. The draft proposes extending this pattern to datasets, software, and pipeline config. That's the right trajectory — it follows the existing serialization path (`_validate_component_json` → Pydantic model round-trip → `_persist_component`) and doesn't require new API contracts.

The "confidence-driven correction loop" concept — linking each low-confidence reason to the exact form control that fixes it — is the single most impactful UX idea in the draft. The confidence module (`confidence.py`) already decomposes scores into `has_software`, `has_version`, `has_parameters`, `has_input_output`, `dataset_resolved`, `figure_confidence_mean`, and `parse_warning_count`. Today these factors are computed but only surfaced as a single percentage bar. Exposing them as actionable items is feasible with no backend changes.

### Where the draft needs correction or refinement

**1. The "Pipeline Journey rail" duplicates existing navigation and adds complexity without clear payoff.**

The codebase already has two navigation systems: (a) a 6-step numbered stepper in `workflow_step.html` with a dashboard endpoint node, and (b) the dashboard's 6-tab bar. Adding a third persistent "Pipeline Journey rail" across all three page types (progress, workflow steps, dashboard) risks creating a navigation layer that competes with the existing stepper rather than replacing it. The draft proposes step states `not started | running | needs review | confirmed` — but the system already tracks `missing | found | inferred` on `ComponentSnapshot.status` and `queued | in_progress | completed | failed` on `WorkflowJob.status`. Introducing a new state vocabulary that doesn't map to existing model fields means either duplicating state logic or building an impedance-mismatch translation layer.

**Correction:** Instead of adding a third nav rail, unify the existing stepper into a richer component that surfaces per-step readiness (using existing `component_meta.status` + confidence thresholds). The stepper in `workflow_step.html` already has numbered circles with connecting lines — enhance those circles with confidence-based color coding and a "needs review" badge derived from `confidence.assay_confidences[assay].overall < threshold`. Keep the dashboard tabs but reorder them.

**2. The "hard-priority layout" conflates three very different interaction modes into one scroll.**

Making confidence, pipeline builder, and datasets render as stacked panels on a single scroll means the pipeline builder canvas (currently 760px tall) gets squeezed above a dataset table and below a confidence command center. React Flow's `fitView` already needs the container to have non-zero dimensions and the codebase includes a `pb-tab-activated` custom event to trigger viewport recalculation specifically because tab visibility is a problem. Stacking the builder in a scroll where it may be partially off-screen will degrade the drag-to-connect interaction that is the builder's core value.

**Correction:** Keep tab-based layout but change the default tab and tab order. Make the dashboard open to a new "Command Center" tab that merges confidence + dataset readiness + a "next action" CTA. Make Pipeline Builder the second tab. This gives the builder its full viewport while making confidence the first thing users see.

**3. The draft underestimates the structured editor scope.**

The existing `save_structured_step` handler only covers method step fields (software, version, input/output, parameters). Building structured editors for datasets, software, and pipeline config metadata means:

- Datasets: each `Dataset` Pydantic model has accession, source, description, organism, platform, and other fields — needs a new `save_structured_dataset` POST action and form.
- Software: each `Software` model has name, version, language, url, citation — needs a `save_structured_software` POST action.
- Pipeline config: nested `config.steps` with depends_on chains — this is the most complex editor and likely needs a different UI pattern (step list with dependency selectors).

The draft says "expand current structured editing pattern" as if it's a simple extension. In practice, each domain needs its own form, its own POST handler, its own validation path, and its own set of typed controls. This is 3-4 discrete implementation units, not one.

**4. "No breaking change to existing step order or current API routes" is correct but incomplete.**

The plan needs to explicitly state what new API routes are required. The existing FastAPI routes (`/api/v1/graphs/{job_id}`) support graph CRUD but there are no structured-save endpoints in the FastAPI layer. The current structured saves go through Django's dashboard POST handler. If the goal is to eventually have the React-based pipeline builder trigger structured saves (e.g., editing a node's parameters inline), those saves will need FastAPI endpoints that the React code can call — Django form POSTs won't work from React `fetch()` calls without CSRF gymnastics.

**5. The "Next recommended action" CTA needs a concrete algorithm.**

The draft says add a CTA on each screen but doesn't specify how to rank actions. The confidence module already provides the decomposition: for each assay, we know which of the 4 completeness factors are false, whether the dataset is resolved, and the warning count. A concrete algorithm: sort unresolved factors by (a) impact on overall confidence, (b) user effort required, and surface the top 1-2. Missing software version is a quick text field fix; unresolved dataset accession may require re-running the datasets step. These should be ranked differently.

**6. Mobile responsiveness is under-specified.**

The current CSS has a single `@media (max-width: 700px)` breakpoint. The draft mentions "correct order on mobile" but the existing grid layouts (5-column stat tiles, 2-column overview) will break badly on small screens. This needs explicit attention in implementation, particularly for the confidence command center which will have multiple visualization types.

---

## Architecture Decisions

- **Stay within Django templates + vanilla JS + existing React (ESM) for the pipeline builder.** No framework migration.
- **Tab-based dashboard layout** with reordered tabs, not a single stacked scroll.
- **Enhance existing stepper** rather than adding a new navigation rail.
- **New FastAPI endpoints** for structured saves and graph compilation, using the existing `get_current_user` auth dependency.
- **Confidence decomposition** surfaced as actionable items, computed from existing `confidence.py` output.
- **WebSockets optional** — polling-first for real-time execution feedback, with WebSocket upgrade path if latency matters.

## Prior Art (Completed)

These items from `FASTAPI_INTEGRATION_PLAN.md` Phases 1–2 are already implemented and do not need rework:

- ASGI co-habitation: `researcher_ai_portal/asgi.py` routes `/api/v1/*` to FastAPI, everything else to Django.
- FastAPI package structure: `researcher_ai_portal_app/api/` with `routes.py`, `schemas.py`, `repository.py`, `dependencies.py`, `graph_layout.py`.
- Pydantic schemas: `GraphNode`, `GraphEdge`, `WorkflowGraph`, `NodePort`, `ParsePublicationRequest/Response`, `JobStatusResponse`, `JobSummary`.
- Core endpoints: `GET/PUT /api/v1/graphs/{job_id}`, `GET /api/v1/graphs/{job_id}/nodes/{node_id}`, `GET /api/v1/jobs`, `GET /api/v1/jobs/{job_id}`, `GET /api/v1/jobs/{job_id}/status`, `POST /api/v1/parse-publication`, `GET /api/v1/ping`.
- Auth dependency: `get_current_user()` reads Django session cookie, loads session, extracts user. Returns HTTP 401 on failure.
- Repository layer: `repository.py` wraps all ORM access with `sync_to_async`.
- `WorkflowJob.graph_data` JSONField (migration 0003) stores React Flow state.
- Graph layout: `generate_default_graph()` and `generate_tool_graph()` produce node/edge layouts from parsed components.
- React Flow pipeline builder: inline ESM module in `dashboard.html`, `ToolNode` component, `ParamEditor`, `ListEditor`, save via `PUT /api/v1/graphs/{job_id}`.

---

## Phase 0: Confidence Command Center (Default Dashboard Tab)

**Goal:** When a user arrives at the dashboard, the first thing they see is "your pipeline is X% ready, here's what to fix."

**Files to modify:**

- `confidence.py` — add `compute_actionable_items()` returning ranked `{reason, severity, fix_target, fix_label}` tuples
- `views.py` (dashboard view) — pass actionable items to template context
- `dashboard.html` — new "Command Center" tab replacing Overview as default

**Implementation:**

1. **Extend `confidence.py`** with `compute_actionable_items(components, confidence_result)` that iterates through `assay_confidences` and emits concrete fix reasons:
   - For each step where `has_software=False`: `"Missing software for {assay}/{step}" → fix_target: "editing", fix_label: "Add software name"`
   - For each step where `has_version=False`: `"Missing version for {software} in {assay}" → fix_target: "editing"`
   - For each step where `has_parameters=False`: `"No parameters for {software}" → fix_target: "editing"`
   - For each step where `has_input_output=False`: `"Missing input/output for {step}" → fix_target: "editing"`
   - For each assay where `dataset_resolved=False`: `"No dataset accession linked to {assay}" → fix_target: "datasets"`
   - For each assay where `figure_confidence_mean < 60`: `"Weak figure evidence for {assay}" → fix_target: "figures"`
   - For each assay where `parse_warning_count > 0`: `"Parse warnings for {assay}" → fix_target: "workflow_step/method"`
   - If `validation_passed=False`: `"Pipeline validation failed" → fix_target: "advanced"`
   - Sort by severity (confidence impact weight × current deficit) descending.

2. **New "Command Center" tab content** in `dashboard.html`:
   - **Top row:** Enlarged confidence ring (128px) + overall score + validation badge + "X of Y factors resolved" summary text.
   - **Per-assay confidence cards** (promoted from sidebar to main content area): each card shows assay name, overall %, a horizontal stacked bar showing the 4 weighted factors (step completeness 50%, figure evidence 20%, dataset resolution 15%, warnings 15%), and factor-level pills (green check / red X for each).
   - **Actionable items list:** each item is a card with the reason text, a severity indicator (high/medium/low), and a button that navigates to the relevant tab or form field. The button text is the `fix_label` from the algorithm. Items targeting the "editing" tab include `?assay={name}&step={index}` query params that auto-open the correct accordion and scroll to the field.
   - **"Next recommended action" banner** at top: the highest-severity unresolved item, displayed prominently with a single CTA button.
   - **Dataset readiness summary** (compact table): accession, source type, resolution status, linked assay(s). Clicking an unresolved dataset opens the dataset structured editor (Phase 2).

3. **Reorder tabs:** Command Center (default) → Pipeline Builder → Datasets (new) → Figures → Step Editing → Workflow Graph → Advanced.

4. **Enhance existing step stepper** in `workflow_step.html`:
   - Color-code each numbered circle based on the component's confidence contribution (green ≥80%, amber 50-79%, red <50%) using data already available in `component_meta`.
   - Add a small dot badge on steps that have actionable items (count from `compute_actionable_items` filtered by step).

**Test criteria:**

- Command Center renders as default tab on dashboard load.
- Actionable items list is non-empty when confidence < 100%.
- Each actionable item's CTA navigates to the correct tab/form/field.
- "Next recommended action" points to the highest-severity unresolved item.
- Stepper circles reflect confidence coloring accurately.

---

## Phase 1: Pipeline Builder Promotion + FastAPI Frontend Wiring

**Goal:** Pipeline Builder becomes the prominent second tab with save UX, and the React frontend is properly wired to FastAPI endpoints with auth.

This phase absorbs the remaining work from `FASTAPI_INTEGRATION_PLAN.md` Phase 3 (Frontend & Auth Wiring). The auth dependency itself is already implemented — what remains is CSRF handling, connecting the React Flow `fetch` calls to FastAPI, and adding status polling through FastAPI.

**Files to modify:**

- `dashboard.html` — tab reorder, save status indicator, React Flow fetch wiring
- `api/routes.py` — verify CSRF exemption for `/api/v1/*` (FastAPI handles these directly, bypassing Django CSRF middleware)
- Pipeline Builder React code (inline in `dashboard.html`) — `fetch` calls with `credentials: "include"`, save indicator, status polling

**Implementation:**

1. **CSRF handling for FastAPI endpoints:**
   - Verify that the ASGI path router in `asgi.py` ensures Django's `CsrfViewMiddleware` never touches `/api/v1/*` requests. The current router dispatches `/api/v1` paths to FastAPI before Django middleware runs — confirm this with a test that sends a POST to `/api/v1/parse-publication` without a CSRF token and gets a 200/202 (not 403).
   - For React `fetch` calls: use `credentials: "include"` to send the session cookie. No CSRF token header needed since FastAPI handles auth via session cookie directly.

2. **Pipeline Builder save wiring:**
   - The React app already calls `PUT /api/v1/graphs/{job_id}` for saves. Add a `savedState` React state: `"saved" | "unsaved" | "saving" | "error"`.
   - On any node/edge change → set `"unsaved"`.
   - On save → transition through `"saving"` → `"saved"` (or `"error"` on failure).
   - Render a status chip in the builder's header: green "Saved" / amber "Unsaved changes" / spinner "Saving…" / red "Save failed".
   - Add `beforeunload` listener when state is `"unsaved"`.

3. **Pipeline Builder node enhancement:**
   - Add confidence data to `ToolNode` component: inject per-step confidence factors from `confidence.assay_confidences` into the React Flow node data via `json_script`. If a node's software has `has_version=False`, show a small warning icon on the node.
   - When clicking a node, the detail panel (`ParamEditor` + `ListEditor`) shows which confidence factors are satisfied/missing.
   - Add an "Edit in Step Editor →" link from the node detail panel that calls `showTab('editing')` with the correct accordion anchor.

4. **Status polling via FastAPI:**
   - The existing `GET /api/v1/jobs/{job_id}/status` endpoint returns `JobStatusResponse` (status, progress, stage, current_step, error, parse_logs, figure_parse_*).
   - Wire the dashboard to poll this endpoint (replacing or supplementing the Django `job_status` poll) during rebuild operations. Display a "Rebuilding…" progress bar in the Command Center tab while `status == "in_progress"`.

**Test criteria:**

- `POST /api/v1/parse-publication` without CSRF token succeeds (202) when session cookie is valid.
- Save indicator reflects actual save state in the pipeline builder.
- `beforeunload` fires when leaving with unsaved changes.
- Node confidence badges render correctly from injected data.
- Status polling updates the dashboard during rebuild without manual refresh.

---

## Phase 2: Structured Metadata Editors + Generic FastAPI Save Endpoint

**Goal:** Replace JSON editing with typed form controls for datasets, software, and pipeline config. Build the FastAPI structured save endpoint so React-based editors can save without Django form POSTs.

This phase absorbs the "add new save endpoints" aspect of FastAPI Phase 3 (the auth is already wired from Phase 1).

**Files to modify:**

- `views.py` — new POST handlers: `save_structured_dataset`, `save_structured_software`, `save_structured_pipeline_config`
- `dashboard.html` — new Datasets tab content, enhanced Step Editing tab, pipeline config section
- `api/routes.py` — new generic component save endpoint
- `api/schemas.py` — request/response schemas for structured saves
- `api/repository.py` — async wrapper for `_persist_component` and `_validate_component_json`

### 2a: Generic FastAPI structured save endpoint

Rather than one endpoint per component, add a single generic endpoint:

```
PUT /api/v1/jobs/{job_id}/components/{step}
Body: { "payload": <component JSON> }
Response: { "payload": <validated JSON>, "confidence": <updated confidence>, "actionable_items": [...] }
```

**Implementation:**

- New Pydantic schemas in `schemas.py`:
  ```python
  class ComponentSaveRequest(BaseModel):
      payload: Any  # component JSON, validated server-side per step

  class ComponentSaveResponse(BaseModel):
      step: str
      payload: Any
      status: str  # "found" | "inferred" | "missing"
      missing_fields: list[str]
      confidence: dict  # full confidence result from compute_confidence()
      actionable_items: list[dict]  # from compute_actionable_items()
  ```

- New repository function `async def save_component(job_id, user_id, step, payload, source)` that wraps `_validate_component_json` + `_persist_component` + `compute_confidence` + `compute_actionable_items` and returns the response.

- The endpoint validates via the same Pydantic models used by `_validate_component_json` (Paper, Figure, Method, Dataset, Software, Pipeline from `researcher_ai`). On validation failure, return 422 with field-level error details.

- **This single endpoint serves all structured editors** — both Django form POSTs (for server-rendered forms) and React `fetch` calls (for the pipeline builder's inline editors). Django form handlers remain as a convenience layer that marshals form fields into component JSON and calls the same validation path.

### 2b: Dataset structured editor

**New "Datasets" tab** (3rd position in tab bar):

- **Table/card view** of all datasets from `components["datasets"]`.
- Each row shows: accession (editable text input with accession-format validation via regex `_ACC_RE`), source type (select: GEO/SRA/other), organism (text), platform (text), description (textarea), resolution status (auto-computed: does this accession appear in any assay's `raw_data_source`?).
- **Add dataset** button appends a blank row.
- **Delete dataset** button with confirmation.
- **Save** button per row or bulk save.

**Backend (Django):**

- New POST action `save_structured_dataset` in dashboard view.
- Accepts: `dataset_index`, `accession`, `source_type`, `organism`, `platform`, `description` (or `dataset_action=add|delete` for add/delete).
- Validates via `_validate_component_json("datasets", ...)`.
- Persists via `_persist_component(job_id, "datasets", validated, "corrected_structured_dashboard")`.

**Backend (FastAPI):**

- Uses the generic `PUT /api/v1/jobs/{job_id}/components/datasets` endpoint from Phase 2a.

### 2c: Software structured editor

**Extend the Step Editing tab** with a software subsection:

- Table of all software from `components["software"]`.
- Each row: name (text), version (text with "missing" warning if empty), language (select from known languages — Python, R, C++, Bash, Perl, Java, other — matching the `LANG_COLORS` map in the pipeline builder), URL (text, validated as URL), citation (textarea).
- Confidence impact shown: "Adding a version here will improve {assay} confidence by ~X%."

**Backend (Django):**

- New POST action `save_structured_software` in dashboard view.
- Validates via `_validate_component_json("software", ...)`.

**Backend (FastAPI):**

- Uses `PUT /api/v1/jobs/{job_id}/components/software`.

### 2d: Pipeline config editor

**Section within the Pipeline Builder tab** (accessible from a "Configure" button in the builder header):

- Pipeline config has `config.steps` — a list of step definitions with `name`, `software`, `depends_on` (list of step names), `parameters`.
- Editor: ordered list of step cards, each with:
  - Name (text, read-only or editable)
  - Software (select from known software list)
  - Depends on (multi-select chips from other step names)
  - Parameters (key-value editor, reusing the `ParamEditor` React component from the pipeline builder)
- Drag to reorder steps (with dependency constraint validation).

**Backend (Django):**

- New POST action `save_structured_pipeline` in dashboard view.
- Validates via `_validate_component_json("pipeline", ...)`.

**Backend (FastAPI):**

- Uses `PUT /api/v1/jobs/{job_id}/components/pipeline`.

**Test criteria:**

- `PUT /api/v1/jobs/{job_id}/components/{step}` validates and persists for all 6 step types.
- Response includes updated confidence and actionable items.
- 422 response on invalid payload includes field-level error details.
- Structured dataset editor can add, edit, delete datasets; all changes persist and pass validation.
- Structured software editor can edit all fields; missing version shows confidence impact.
- Pipeline config editor respects dependency constraints.
- All structured editors reject invalid input with field-level errors (not just "invalid JSON").
- Raw JSON editors (Advanced tab) still work for all component types.
- Round-trip: structured edit → save → reload page → values preserved.

---

## Phase 3: Confidence-Driven Correction Loops

**Goal:** Close the loop — structured edits immediately update confidence, confidence surfaces link directly to editors, and rebuild progress streams in real-time.

**Files to modify:**

- `confidence.py` — expose per-factor breakdown in API-friendly format
- `dashboard.html` — inline confidence refresh after saves, deep-linking from actionable items
- `views.py` — return updated confidence in structured save responses
- `api/routes.py` — confidence in component save responses (already designed in Phase 2a)

**Implementation:**

1. **Inline confidence refresh:**
   - After any structured save via Django form POST, the dashboard view already recomputes confidence on reload. Ensure the Command Center tab reflects the updated score immediately.
   - For FastAPI saves from React (pipeline builder inline edits), the `ComponentSaveResponse` from Phase 2a includes the updated confidence object. The React app updates node badges and the confidence display without a full page reload.
   - Add a small "confidence delta" indicator after saves: "+3% confidence" toast notification showing the improvement from the edit.

2. **Cross-linking from confidence items to editors:**
   - Each actionable item from Phase 0 includes a `fix_target` (tab name) and enough context to deep-link. Implement this as:
     - For step-editing targets: `showTab('editing')` + scroll to the assay accordion + open it + highlight the specific field with a CSS pulse animation.
     - For dataset targets: `showTab('datasets')` + scroll to the relevant row + highlight.
     - For figure targets: `showTab('figures')` + scroll to the relevant figure card.
   - Use URL hash fragments (`#fix-assay=X&step=Y&field=version`) so deep links work from bookmarks and stepper badges.

3. **Confidence recomputation after rebuild:**
   - The existing `rebuild_pipeline` action re-runs downstream steps via `_dispatch_rebuild`. After rebuild completes, the dashboard reload shows updated confidence.
   - Add a "Rebuilding…" progress indicator that polls `GET /api/v1/jobs/{job_id}/status` (same polling pattern as `workflow_step.html`) so the user sees real-time progress without manually refreshing.
   - When the poll detects `status == "completed"`, auto-reload the Command Center tab content (or fetch updated confidence via a new lightweight `GET /api/v1/jobs/{job_id}/confidence` endpoint).

4. **New FastAPI confidence endpoint:**
   ```
   GET /api/v1/jobs/{job_id}/confidence
   Response: { "overall": float, "assay_confidences": {...}, "actionable_items": [...] }
   ```
   This lightweight endpoint returns the current confidence state without fetching full component payloads. Used by React for polling after saves and rebuilds.

**Test criteria:**

- After a structured save, confidence % updates on the same page load (Django) or via React state update (FastAPI).
- Clicking an actionable item navigates to and highlights the correct form field.
- After rebuild, confidence reflects the re-parsed downstream components.
- Deep-link URLs work when shared or bookmarked.
- Confidence delta toast appears after saves that change the score.

---

## Phase 4: Graph Compilation, Execution & Real-Time Feedback

**Goal:** Compile the visual graph into an executable pipeline, validate topology, and stream real-time execution feedback. This absorbs `FASTAPI_INTEGRATION_PLAN.md` Phase 4 (Execution, WebSockets & Handoff).

**Files to modify:**

- `api/routes.py` — new `POST /api/v1/graphs/{job_id}/compile` and `POST /api/v1/graphs/{job_id}/execute` endpoints
- `api/schemas.py` — `GraphCompileRequest`, `GraphCompileResponse`, `ExecutionStatusResponse`
- `api/repository.py` — async wrappers for graph validation and step dispatch
- `dashboard.html` — "Compile & Run" button in pipeline builder, execution progress panel
- `views.py` — reuse `_dispatch_rebuild` / `_run_step` orchestration

**Implementation:**

1. **Graph validation endpoint:**
   ```
   POST /api/v1/graphs/{job_id}/compile
   Body: { "nodes": [...], "edges": [...] }  (or omit to use saved graph_data)
   Response: {
     "valid": bool,
     "errors": [{"node_id": str, "message": str}],
     "execution_order": [str],  // topologically sorted node IDs
     "warnings": [str]
   }
   ```
   - Validates that all required upstream nodes are connected (checks against `STEP_DEPENDENCIES`).
   - Validates that each node's component payload passes `_validate_component_json`.
   - Returns topological execution order or descriptive errors per node.
   - Validates that the graph is a DAG (no cycles).

2. **Graph compilation:**
   - The `compile` endpoint converts the React Flow graph topology into an execution plan. The graph's node ordering determines execution sequence.
   - The compilation result maps graph nodes to the existing `STEP_ORDER` execution model. Custom tool nodes (from the tool-based graph) map to pipeline steps.
   - Store the compiled execution plan in `WorkflowJob.graph_data` alongside the visual layout.

3. **Execution endpoint:**
   ```
   POST /api/v1/graphs/{job_id}/execute
   Body: { "force_reparse": false }
   Response: 202 Accepted { "job_id": str, "status": "in_progress" }
   ```
   - Dispatches execution via the existing `_dispatch_workflow_step` / `_dispatch_rebuild` code path, called through `sync_to_async` in the repository layer.
   - Returns 202 immediately; client polls for completion.

4. **Real-time execution feedback (polling-first):**
   - The existing `GET /api/v1/jobs/{job_id}/status` endpoint already returns `parse_logs`, `progress`, `stage`, `current_step`, `figure_parse_current/total`.
   - Add an execution progress panel in the pipeline builder: when execution is running, overlay a progress sidebar showing the current step, log tail (last 10 entries from `parse_logs`), and per-step status (queued/running/done).
   - Each pipeline builder node gets a real-time status ring: grey (queued), blue pulsing (running), green (completed), red (failed).
   - Poll `GET /api/v1/jobs/{job_id}/status` every 2 seconds during execution.

5. **WebSocket upgrade path (optional, recommended for production):**
   ```
   WS /api/v1/ws/jobs/{job_id}/logs
   ```
   - FastAPI handles WebSocket connections natively.
   - Server reads from `parse_logs` and streams new entries as they appear.
   - For single-worker deployment: in-process streaming from `append_job_log` events.
   - For multi-worker production: requires Redis pub/sub as channel layer (pairs with optional Celery Redis broker).
   - **Skip WebSockets for MVP.** The polling approach works. Add WebSockets in a follow-up sprint if polling latency is a problem.

6. **Execution adapters:**
   - FastAPI triggers the relevant adapter (Snakemake, Nextflow, TSCC/Slurm) via the existing `PipelineBuilder` output.
   - Update `WorkflowJob` with the active job PID or container ID for status tracking.
   - This is the longest-lead item and may extend beyond the initial timeline.

7. **"Compile & Run" UX in pipeline builder:**
   - Add a "Compile & Run" button in the pipeline builder header (next to the save status chip).
   - On click: call `POST /api/v1/graphs/{job_id}/compile` first. If valid, show a confirmation dialog with the execution order and estimated steps. On confirm, call `POST /api/v1/graphs/{job_id}/execute`.
   - If compilation fails: highlight invalid nodes in red with error tooltips.
   - During execution: show the progress panel overlay. When complete, auto-refresh the Command Center tab to show updated confidence.

**Test criteria:**

- `POST /compile` rejects graphs with missing dependencies (returns descriptive error per node).
- `POST /compile` rejects graphs with cycles.
- `POST /compile` returns valid topological order for well-formed graphs.
- `POST /execute` returns 202 and triggers background execution via existing `_run_step` pipeline.
- Status polling updates node status rings during execution.
- On execution completion, confidence is recomputed and Command Center reflects new scores.
- Invalid nodes are highlighted in the builder on compilation failure.

---

## Phase 5: Journey Progression Polish

**Goal:** The full source → pipeline journey feels guided and complete.

**Files to modify:**

- `workflow_step.html` — enhanced stepper with readiness states
- `progress.html` — connect progress page to stepper UX
- `dashboard.html` — completion state, unified stepper
- New template partial: `_stepper.html`

**Implementation:**

1. **Unified stepper component:**
   - Extract the stepper HTML from `workflow_step.html` into a shared template partial `_stepper.html`.
   - Include it in both `workflow_step.html` and `dashboard.html` (above the tab bar).
   - Stepper state per step (derived from existing data, no new model fields):
     - **Not started**: `component_meta.status == "missing"` → grey circle
     - **Running**: `job.status == "in_progress" and job.current_step == step` → blue pulsing circle
     - **Needs review**: component exists but confidence contribution < threshold (e.g. 70%) → amber circle with warning dot
     - **Confirmed**: component exists and confidence contribution ≥ threshold → green circle with checkmark
   - These states map directly to existing `component_meta.status`, `WorkflowJob.status`, and `confidence.assay_confidences` — no new state vocabulary needed.

2. **Progress page connection:**
   - `progress.html` already polls `job_status`. When the job completes, instead of just showing a "Go to dashboard" button, show the stepper with the first step that needs review highlighted and a CTA: "Review {step_label}" that goes to `workflow_step` for that step, or "Go to Dashboard" if all steps pass threshold.

3. **Dashboard completion state:**
   - When overall confidence ≥ 90% and all components are `found` or `inferred` with no actionable items: show a "Pipeline Ready" banner on the Command Center tab with a "Compile & Run" CTA (connecting to Phase 4's execution flow).
   - Show a completion percentage ring that counts resolved actionable items / total actionable items.

**Test criteria:**

- Stepper renders consistently between `workflow_step.html` and `dashboard.html`.
- Each stepper state accurately reflects the underlying data.
- Progress page redirects to the highest-priority review step on completion.
- Completion banner appears when all criteria are met.
- "Compile & Run" CTA on completion banner works end-to-end.

---

## Implementation Order and Dependencies

```
Phase 0 ─── Confidence command center ──────────────── (no dependencies)
   │
Phase 1 ─── Pipeline Builder + FastAPI wiring ──────── (no dependencies, parallel with 0)
   │         [absorbs FASTAPI_INTEGRATION_PLAN Phase 3]
   │
Phase 2a ── Generic FastAPI save endpoint ──────────── (depends on Phase 1 for auth wiring)
Phase 2b ── Dataset structured editor ──────────────── (depends on 2a for save endpoint)
Phase 2c ── Software structured editor ─────────────── (parallel with 2b)
Phase 2d ── Pipeline config editor ─────────────────── (parallel with 2b)
   │
Phase 3 ─── Confidence correction loops ────────────── (depends on Phase 0 + Phase 2)
   │
Phase 4 ─── Graph compilation + execution ──────────── (depends on Phase 1 + Phase 2a)
   │         [absorbs FASTAPI_INTEGRATION_PLAN Phase 4]
   │
Phase 5 ─── Journey progression polish ─────────────── (depends on Phase 0 + Phase 3)
```

Phases 0 and 1 can be done in parallel. Phases 2b-2d can be done in parallel with each other after 2a. Phase 3 needs Phases 0 and 2 complete. Phase 4 can begin once Phase 1 and 2a are done (independent of 2b-d and 3). Phase 5 is the final polish pass.

## Estimated Scope Per Phase

| Phase | Sessions | Primary Work |
|-------|----------|-------------|
| **0** | 2–3 | `confidence.py` extension + Command Center tab template |
| **1** | 2–3 | React save indicator + CSRF verification + status polling + node confidence badges |
| **2a** | 1–2 | Generic FastAPI save endpoint + schemas + repository |
| **2b** | 1–2 | Dataset structured editor (form + POST handler + tab content) |
| **2c** | 1 | Software structured editor |
| **2d** | 1–2 | Pipeline config editor (most complex form) |
| **3** | 2 | Deep-linking + confidence refresh + rebuild polling |
| **4** | 3–4 | Graph validation + compile + execute + progress panel |
| **5** | 1–2 | Stepper extraction + progress page + completion state |
| **Total** | **14–20** | |

## New API Endpoints Summary

| Method | Path | Phase | Purpose |
|--------|------|-------|---------|
| `PUT` | `/api/v1/jobs/{job_id}/components/{step}` | 2a | Generic structured save with validation |
| `GET` | `/api/v1/jobs/{job_id}/confidence` | 3 | Lightweight confidence + actionable items |
| `POST` | `/api/v1/graphs/{job_id}/compile` | 4 | Validate + compile graph to execution plan |
| `POST` | `/api/v1/graphs/{job_id}/execute` | 4 | Trigger pipeline execution |
| `WS` | `/api/v1/ws/jobs/{job_id}/logs` | 4 (optional) | Real-time log streaming |

All existing endpoints remain unchanged.

## What Stays Unchanged

- `ComponentSnapshot.payload` remains the source of truth.
- `_validate_component_json()` remains the validation gate for all saves.
- Existing `save_structured_step` action continues to work.
- Raw JSON editors remain available in the Advanced tab.
- All existing Django URL routes continue to function.
- All existing FastAPI endpoints continue to function.
- `STEP_ORDER` and `STEP_DEPENDENCIES` are unchanged.
- No new Django models or migrations required.
- No frontend framework migration.
- Django session cookie auth used by both frameworks.
- The Plotly/Dash dashboard app and Cytoscape DAG app remain intact.

## Risk Mitigation

| Risk | Mitigation |
|------|------------|
| Command Center tab adds template complexity to already-large `dashboard.html` | Extract each tab into a partial (`_tab_command_center.html`, etc.) during Phase 0 |
| Generic save endpoint becomes a security surface | Validate `step` param against `STEP_ORDER` whitelist; auth via existing `get_current_user` |
| React Flow graph state diverges from component payloads | Graph is visual layout only; `ComponentSnapshot.payload` remains source of truth. Compile step validates consistency. |
| WebSocket complexity blows Phase 4 timeline | WebSockets explicitly optional. Polling works. Ship without them. |
| Structured editors don't cover all Pydantic model fields | Start with high-impact fields (those in confidence calculation). Add remaining fields iteratively. Advanced JSON toggle covers everything. |
| `sync_to_async` overhead on structured save hot path | Repository layer centralizes ORM calls; profile in Phase 2a. Use Django cache for read-heavy confidence endpoint. |
| Mobile layout breaks with new Command Center content | Add responsive breakpoints in Phase 0; test at 375px, 768px, 1024px widths |
