# Threat Model: RAG Data Visualization Fix

## Trust Boundaries

The RAG workflow page is a read-only diagnostic view. The only data flows are:

```
[Browser]  ←GET— [Django View: rag_workflow()] ←read— [Job Store (in-memory dict)]
```

No user-supplied data is written in this request path. No external API calls.

## STRIDE Analysis

| Boundary | Threat | Category | Assessment |
|----------|--------|----------|------------|
| Browser ← Django view | XSS via rag_workflow_json | I | LOW: `json_script` tag (used on line 13) escapes JSON for safe embedding in HTML. |
| Browser ← Django view | XSS via template fields | I | LOW: Django auto-escaping applies to all `{{ }}` expressions. The one JS string interpolation (`String(row.message || '').replace(/</g, '&lt;')`) correctly escapes HTML in message text. |
| Job store → view | Data integrity | T | LOW: Job store is in-memory; no user input modifies the RAG payload through this view. |
| Template → browser | Information disclosure | I | LOW: Page is user-scoped (access control already validated in `rag_workflow()` view). |

## Finding

No new trust boundaries are introduced by this fix. All template changes use Django's auto-escaping and the existing `json_script` escaping. No STRIDE threats above LOW severity.

## MAESTRO (AI Component)

The RAG phase graph visualizes the Methods Parser RAG pipeline (indexing → retrieval → LLM generation). The data displayed is diagnostic metadata — it does not feed back into the AI pipeline.

| Domain | Assessment |
|--------|------------|
| Model Input | Not in scope (read-only diagnostic page) |
| Model Output | `generation.model` is displayed but not interpreted by the UI |
| Human-AI Interface | Adding `human_review_summary.recommended_action` text improves transparency — this is positive for oversight. The text is LLM-sourced but the page is clearly labeled as a diagnostic view. |
| Agent Autonomy | Not applicable |

No MAESTRO risks introduced.
