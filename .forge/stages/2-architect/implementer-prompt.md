# Implementer Prompt: Fix RAG Data Visualization

## What you are fixing

The RAG Workflow page (`rag_workflow.html`) has two categories of bugs:

1. **SVG overflow**: The phase graph SVG has `width="900"` hardcoded. The card containing it is ~600px wide at the standard 1100px page max-width. Fix: change the JS `renderGraph()` function to emit `width="100%"` instead of `width="900"` on the SVG element. The `viewBox="0 0 900 160"` attribute stays — the browser uses it to scale proportionally.

2. **Missing data fields**: `build_rag_workflow_payload()` in `views.py` already populates:
   - `rag_workflow.generation.model` (line 574)
   - `rag_workflow.diagnostics.vision_fallback_count` (line 417)
   - `rag_workflow.diagnostics.vision_fallback_latency_seconds` (line 418)
   - `rag_workflow.diagnostics.human_review_summary.recommended_action` (line 393)
   
   None of these are rendered in `rag_workflow.html`. Add them.

## Files to modify

1. `researcher_ai_portal_app/templates/researcher_ai_portal/rag_workflow.html`
2. `researcher_ai_portal_app/tests/test_workflow_step_regressions.py`

**Do NOT modify `views.py`, `forms.py`, or any Python files.**

## Task 1: Fix SVG responsiveness

In `renderGraph()` (around line 172 of the template), change:
```javascript
graph.innerHTML = `
  <svg width="900" height="160" viewBox="0 0 900 160" ...>
```
to:
```javascript
graph.innerHTML = `
  <svg width="100%" height="160" viewBox="0 0 900 160" ...>
```

Also ensure `#rag-graph` does not have `overflow:auto` that would scroll inside the card — remove it or change to `overflow:hidden`.

## Task 2: Extend summary grid to 6 cards

Current 5-card grid (line 38): `grid-template-columns: repeat(5, minmax(0,1fr))`

Change to: `grid-template-columns: repeat(6, minmax(0,1fr))`

Add a 6th card after "Assays Parsed":
```html
<div class="card card-sm" style="text-align:center;">
  <div class="text-xs text-muted">Model</div>
  <div class="font-700" style="color:var(--text);">{{ rag_workflow.generation.model|default:"—" }}</div>
</div>
```

Update the responsive media query at the bottom from `repeat(2, ...)` to `repeat(3, ...)` for the top cards.

## Task 3: Add vision fallback to diagnostics

Inside the Diagnostics `div.card` panel (after the "Context Tokens" card), add:
```html
<div class="card card-sm" style="background:var(--surface-2);">
  <div class="text-xs text-muted">Vision Fallback</div>
  <div class="font-600" style="color:var(--text);">
    {% with fb=rag_workflow.diagnostics.vision_fallback_count|default:0 lat=rag_workflow.diagnostics.vision_fallback_latency_seconds %}
      {{ fb }}{% if fb and lat %} · {{ lat }}s latency{% endif %}
    {% endwith %}
  </div>
</div>
```

## Task 4: Surface human review recommendation

After the Human Review `card card-sm` (around line 75-79 of the template), add:
```html
{% if rag_workflow.result.review_required and rag_workflow.diagnostics.human_review_summary.recommended_action %}
  <p class="text-xs text-muted" style="margin: 2px 0 0 4px;">
    {{ rag_workflow.diagnostics.human_review_summary.recommended_action }}
  </p>
{% endif %}
```

## Task 5: Add regression test

In `researcher_ai_portal_app/tests/test_workflow_step_regressions.py`, add a new test after the existing `test_workflow_step_template_includes_dataset_correction_drawer`:

```python
def test_rag_workflow_template_includes_visualization_fields():
    template_path = (
        Path(__file__).resolve().parents[1]
        / "templates"
        / "researcher_ai_portal"
        / "rag_workflow.html"
    )
    text = template_path.read_text(encoding="utf-8")
    assert "generation.model" in text
    assert "vision_fallback_count" in text
    assert "human_review_summary" in text
    assert "recommended_action" in text
    # SVG must not have hardcoded width="900"
    assert 'width="900"' not in text
```

## Verification

After changes, run:
```python
from pathlib import Path
text = Path("researcher_ai_portal_app/templates/researcher_ai_portal/rag_workflow.html").read_text()
assert "generation.model" in text
assert "vision_fallback_count" in text
assert "recommended_action" in text
assert 'width="900"' not in text
print("All checks pass")
```

The test in test_workflow_step_regressions.py is a static text scan and does not require Django to be installed.
