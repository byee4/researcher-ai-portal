# Architecture Decisions Summary

## DR-01: Template-only fix (no Python changes)
**Choice**: All 5 tasks modify only `rag_workflow.html` and one test file.  
**Why**: `build_rag_workflow_payload()` already populates all required data. Touching Python adds migration risk for zero benefit.  
**Alternative rejected**: Restructuring the view to pre-format display strings — unnecessary indirection.

## DR-02: SVG width="100%" over JS ResizeObserver
**Choice**: Change the hardcoded `width="900"` to `width="100%"` in the JS string template.  
**Why**: The SVG already has `viewBox="0 0 900 160"` which handles proportional scaling natively. A ResizeObserver would add ~20 lines of JS for the same outcome.  
**Alternative rejected**: CSS `max-width: 100%; overflow: hidden` on the SVG — clips the rightmost phase node instead of scaling.

## DR-03: 6-card top grid (add Model)
**Choice**: Add Model as a 6th summary card.  
**Why**: `generation.model` is top-level metadata (which LLM was used), not a diagnostic signal. It belongs in the summary section alongside Mode, Sections, etc.  
**Alternative rejected**: Adding it to the Diagnostics panel — diagnostics are about quality signals; model identity is a configuration fact.

## DR-04: Vision fallback in diagnostics, not summary
**Choice**: `vision_fallback_count` added to Diagnostics panel.  
**Why**: Vision fallback is a quality/warning signal — it indicates the primary text-based retrieval failed for some figures. Belongs alongside Human Review and Warnings.  
**Alternative rejected**: Summary card — would add a 7th card that clutters the summary for the common case where count=0.

## DR-05: Human review recommendation as plain text below the card
**Choice**: A `<p class="text-xs text-muted">` below the Human Review card, rendered only when `review_required` is true.  
**Why**: Keeps the recommended action visually adjacent to the flag that triggers it. No new UI component needed.  
**Alternative rejected**: A separate "Review Details" expandable accordion — adds complexity for a short string.
