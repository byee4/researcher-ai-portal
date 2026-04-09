# researcher-ai v2.1.1 Integration Plan (Portal)

## Summary
- Integrate `researcher-ai==2.1.1` with low-risk compatibility scope.
- Keep current step-wise portal execution (no orchestrator migration now).
- Add explicit terminal review state handling, persist diagnostics, and expose new runtime controls.

## Implementation Changes
1. **Version and docs alignment**
- Update default pin from `researcher-ai==2.0.0` to `researcher-ai==2.1.1` in:
  - `Dockerfile` build arg default
  - `docker-compose.yml` build arg default
  - `docs/SETUP.md` pinned install examples and baseline text
  - `README.md` release notes/baseline references

2. **Workflow status modeling**
- Add explicit status support for `needs_human_review` in `WorkflowJob.status` handling.
- Ensure job update paths can persist terminal review state with:
  - `status="needs_human_review"`
  - `stage="needs_human_review"` (UI-safe mapped terminal label)

3. **Persisted review/diagnostic metadata**
- Add durable job-level metadata field strategy (JSON-backed) for:
  - `human_review_required` (bool)
  - `human_review_summary` (object)
  - `vision_fallback_count` (int)
  - `vision_fallback_latency_seconds` (float)
- Populate from parser warnings/state where available; preserve raw warning text fallback.

4. **Robust warning parsing**
- Implement tolerant parser for method warning format:
  - `paper_rag_vision_fallback: count=<int> latency_seconds=<float>`
- Regex tolerates extra whitespace/newlines and minor formatting drift.
- Parsing failure is non-fatal.

5. **API surface extension (backward compatible)**
- Extend status responses (Django + FastAPI) with optional nullable fields:
  - `review_required: Optional[bool] = None`
  - `review_summary: Optional[dict[str, Any]] = None`
  - `vision_fallback_count: Optional[int] = None`
  - `vision_fallback_latency_seconds: Optional[float] = None`
- Keep old clients functional when fields are absent.

6. **Frontend terminal-state behavior**
- Update progress/workflow polling logic:
  - treat `needs_human_review` as terminal
  - stop success auto-redirect
  - show manual-review CTA
- Surface persisted diagnostic telemetry on workflow/dashboard views.

7. **Config surfaces**
- Add to `.env.example` and `docs/SETUP.md`:
  - `RESEARCHER_AI_BIOWORKFLOW_MODE=warn` (document `off`/`warn`/`on`)
  - `RESEARCHER_AI_MAX_RETRIEVAL_REFINEMENT_ROUNDS`
- Keep existing RAG controls and defaults documented.

8. **Issue tracking and git workflow**
- Use `bd` lifecycle:
  - `bd prime`
  - `bd ready` / `bd update <id> --claim`
  - implement + test
  - `bd close <id>`
  - `git pull --rebase && bd dolt push && git push`
- Commit prefixes:
  - `feature: ...` for feature additions
  - `bugfix: ...` for bug fixes

## Test Plan
1. **Model/status tests**
- Verify review terminal state persists and is queryable without parsing component JSON.
- Verify existing `completed`/`failed` flows unchanged.

2. **API/schema tests**
- Validate optional review/diagnostic fields serialize when present.
- Validate legacy records with missing fields pass schema validation.

3. **Parser tests**
- Test warning parser against:
  - canonical format
  - extra spaces/newlines
  - malformed content (graceful no-crash behavior)

4. **UI polling regression tests**
- Mock transition frame `in_progress -> needs_human_review`.
- Assert polling stops, no success redirect, manual-review CTA appears.

5. **End-to-end checks**
- Run `pytest -q`.
- Smoke test PMID/PDF parse flow.
- Verify persisted review/diagnostic data visible after refresh/history lookup.

## Assumptions
- `v2.1.1` diff is docs-only relative to `v2.1.0` in local tagged `researcher-ai`.
- No orchestrator migration in this cycle.
- Metadata persistence uses existing JSON-capable job storage unless migration is required for query ergonomics.
