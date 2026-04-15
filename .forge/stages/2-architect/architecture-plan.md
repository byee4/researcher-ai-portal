# Architecture Plan: Fix RAG Data Visualization
<!-- FORGE_STAGE: 2-architect -->

## 1. Problem Statement

The `rag_workflow.html` template has two categories of defects:

1. **SVG overflow (rendering bug)**: The RAG phase graph renders an SVG with `width="900"` inside a card that is ~600px wide at the standard 1100px page max-width. The SVG overflows its container horizontally. `overflow:auto` on `#rag-graph` provides a scrollbar but the graph still overflows outside the card boundary.

2. **Missing data fields (display gaps)**: `build_rag_workflow_payload()` in `views.py` already populates `generation.model`, `diagnostics.vision_fallback_count`, `diagnostics.vision_fallback_latency_seconds`, and `diagnostics.human_review_summary.recommended_action` — but the template never renders any of them.

No Python changes are required. All data is already present in the `rag_workflow` context variable and its JSON counterpart `rag_workflow_json`.

## 2. Approaches Evaluated

### A. CSS-only SVG fix (rejected)
Add `width: 100%; overflow: hidden;` to the SVG element. This clips labels on small screens without addressing the data gaps. No test coverage added.

### B. Template-only patch (selected)
- Make the SVG responsive with `width="100%"` + `viewBox` already present — browser will scale to fit
- Add generation model to the summary card grid (extend 5 → 6 cards)  
- Add vision fallback count + latency to the Diagnostics panel
- Surface `human_review_summary.recommended_action` text when review is required
- Add one regression test asserting the new strings appear in the template

### C. Full overhaul (rejected)
Timing waterfall chart, streaming updates, retrieval quality scores. Feature scope; incorrect for bug-fix archetype.

## 3. Selected Approach: B

**Rationale**: All payload data is already available. The fix is entirely template-side, keeps diff small, and unblocks immediate user need (seeing LLM model used, vision fallback signals, and review recommendations without navigating elsewhere).

## 4. Files Modified

| File | Change |
|------|--------|
| `researcher_ai_portal_app/templates/researcher_ai_portal/workflow_step.html` | **No change** |
| `researcher_ai_portal_app/templates/researcher_ai_portal/rag_workflow.html` | Fix SVG, extend summary grid, add diagnostics fields |
| `researcher_ai_portal_app/tests/test_workflow_step_regressions.py` | Add regression assertions for new template strings |

## 5. Acceptance Criteria

### AC-1: SVG graph fits its container
- The phase graph SVG renders without horizontal overflow at 800px, 1100px, and 1400px viewport widths
- No horizontal scrollbar appears inside the `#rag-graph` div at 1100px viewport
- All five phase labels remain visible (no clipping)

### AC-2: Generation model displayed
- The top summary card grid shows a "Model" card containing `rag_workflow.generation.model`
- When `generation.model` is an empty string, the card shows `"—"` (not blank)
- Card renders as a 6th card in the 5-card grid (adjust to `repeat(3, ...)` or `repeat(6, ...)`)

### AC-3: Vision fallback shown in diagnostics
- The Diagnostics panel shows a "Vision Fallback" card with `rag_workflow.diagnostics.vision_fallback_count`
- When `vision_fallback_count` is absent or zero, the card renders `"0"`
- When `vision_fallback_latency_seconds` is present and > 0, it renders as a subtitle in the same card (e.g., `"2 · 1.23s latency"`)

### AC-4: Human review recommendation text shown
- When `rag_workflow.result.review_required` is true AND `rag_workflow.diagnostics.human_review_summary.recommended_action` exists, the Diagnostics panel shows that text below the Human Review card
- The text is visually distinct (smaller, muted) from the card header
- When `review_required` is false, no recommendation text renders

### AC-5: Regression tests pass
- `test_workflow_step_template_includes_rag_visualization_fields` asserts the new strings are present in the template: `"generation.model"`, `"vision_fallback_count"`, `"human_review_summary"`, `"recommended_action"`

## 6. Task Decomposition

### TASK-1: Fix SVG responsiveness
**File**: `rag_workflow.html`
**Change**: In `renderGraph()`, remove `width="900"` (or set to `"100%"`) from the SVG element. The `viewBox="0 0 900 160"` stays; this preserves aspect ratio while allowing browser scaling.
**Verification**: AC-1

### TASK-2: Add generation model summary card
**File**: `rag_workflow.html`
**Change**: Extend the top card grid from 5 to 6 cards. Add a "Model" card after "Assays Parsed". Change `grid-template-columns: repeat(5, ...)` to `repeat(6, ...)`. Card content: `{{ rag_workflow.generation.model|default:"—" }}`.
**Verification**: AC-2

### TASK-3: Add vision fallback diagnostics
**File**: `rag_workflow.html`
**Change**: Add a new `card card-sm` inside the Diagnostics `div.card` panel. Content: `vision_fallback_count` with conditional latency subtitle.
**Verification**: AC-3

### TASK-4: Surface human review recommendation
**File**: `rag_workflow.html`
**Change**: After the Human Review `card card-sm`, add `{% if rag_workflow.result.review_required and rag_workflow.diagnostics.human_review_summary.recommended_action %}` block with a `<p class="text-xs text-muted">` rendering the recommended action.
**Verification**: AC-4

### TASK-5: Add regression test
**File**: `researcher_ai_portal_app/tests/test_workflow_step_regressions.py`
**Change**: Add `test_rag_workflow_template_includes_visualization_fields()` that reads the template file and asserts the new field references exist.
**Verification**: AC-5

## 7. Assumptions

- `build_rag_workflow_payload()` correctly populates `generation.model` from `job.llm_model` when no structured RAG telemetry exists — confirmed by reading views.py:574
- `diagnostics.vision_fallback_count` and `diagnostics.vision_fallback_latency_seconds` are only present when count > 0 — confirmed by `_extract_method_diagnostics()` at views.py:416
- The browser SVG scaling behaviour (width="100%" + viewBox) is supported in all modern browsers (Chrome 4+, Firefox 3.5+, Safari 3.1+) — no polyfill needed
- The `test_workflow_step_regressions.py` test pattern (reading template file as text) is the established pattern in this repo — confirmed by existing tests

## 8. Self-Reflection Findings

- **Scope boundary**: The `rag_workflow.html` responsive CSS already handles grid columns at `max-width: 980px` but does not address SVG width — confirmed the SVG fix is genuinely absent, not an oversight of existing media query
- **No Python needed**: Confirmed all four missing fields are present in `build_rag_workflow_payload()` return value at lines 559-584; template-only change is sufficient
- **Minimal diff**: 5 tasks, 2 files, ~40 lines of template changes + ~15 lines of test. Appropriate for bug-fix archetype
- **Responsive grid check**: Extending to 6 cards requires adjusting the media query breakpoint CSS to use `repeat(3, ...)` at `max-width: 980px` instead of `repeat(2, ...)`

## 9. Risk Register

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| 6-card grid wraps poorly on 768px screens | Low | Low | Update media query to 3 columns (already planned in TASK-2) |
| SVG text labels too small when scaled down | Medium | Low | viewBox preserves coordinates; labels at font-size 12 remain readable at 600px width |
| `diagnostics.vision_fallback_count` absent when count=0 | Known | None | Template uses `|default:"0"` |

## 10. Decision Register

| Decision | Choice | Rejected Alternative | Rationale |
|----------|--------|---------------------|-----------|
| SVG responsiveness | `width="100%"` on element | JS `ResizeObserver` redraw | Simpler; `viewBox` already present |
| Card count | 6 cards (add Model) | Keep 5 + add to diagnostics | Model is top-level metadata, not a diagnostic |
| Review text placement | Below Human Review card in diagnostics | Separate section | Keeps related data together |
| Template-only fix | Pure HTML/Django template | Python view changes | All data already in payload |
