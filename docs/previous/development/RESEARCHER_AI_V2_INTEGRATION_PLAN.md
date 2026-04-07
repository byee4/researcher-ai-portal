# Hardened researcher-ai v2.0.0 Compatibility Integration Plan

## Summary
- Keep compatibility-first scope, but harden for v2 multimodal + RAG operational realities.
- Branch target: `codex/integrate-researcher-ai-v2`.
- No new product UX/features beyond what is required for safe v2 runtime compatibility.

## Key Changes
- Multimodal figure input integrity:
  - Preserve uploaded PDFs to a durable per-job staging path, store absolute path in job `source`, and verify it exists before `paper`/`figures` execution.
  - Add a guard in `_run_step` for `source_type=pdf`: fail early with a clear error if staged PDF is missing/unreadable.
  - Ensure `FigureParser` receives a `Paper` with valid `source_path` so v2 panel-cropping can access raw binary PDF content.
- RAG/Chroma concurrency safety (default per-job isolation):
  - Use per-job RAG persistence directories for `MethodsParser(rag_persist_dir=...)`.
  - Clean up per-job RAG dirs after completion/failure.
  - Keep shared `.rag_chroma/` as explicit opt-in mode only.
- Provider routing and key validation:
  - `gpt-*`, `chatgpt-*`, `o1-*`, `o3-*`, `o4-*` -> `OPENAI_API_KEY`
  - `claude-*` -> `ANTHROPIC_API_KEY`
  - `gemini-*` -> `GEMINI_API_KEY`
  - Validation:
    - OpenAI: allow `sk-` and `sk-proj-` patterns.
    - Anthropic: require `sk-ant-`.
    - Gemini: allow 39-char `[A-Za-z0-9_-]{39}`.
- Vision-model rate-limit/error handling:
  - Normalize known provider 429/rate-limit exceptions to user-readable messages.
  - On failures, set job status `failed`, persist actionable `error`, and surface guidance in workflow UI status text.
- Versioning/docs/deploy alignment:
  - Pin default package install target to `researcher-ai==2.0.0` for non-vendored paths.
  - Update docs/scripts from deprecated `django_run_workflow.py` references to v2 `scripts/run_workflow.py`.
  - Add staging-path and provider-env requirements to ops docs.

## Public Interfaces / Config
- Runtime env handling:
  - `GEMINI_API_KEY` support.
  - Optional `RESEARCHER_AI_VISION_MODEL`.
  - Optional `RESEARCHER_AI_RAG_MODE` (`per_job` default, `shared` opt-in) and `RESEARCHER_AI_RAG_BASE_DIR`.
- No DB schema or HTTP route changes.

## Test Plan
- Run portal test suite under v2 package.
- Add targeted tests:
  - PDF pass-through: uploaded PDF is persisted, absolute path is stored in job state, and figure parsing path-dependent flow is reachable.
  - Provider/key validation:
    - Gemini 39-char key accepted for `gemini-*`.
    - OpenAI and Anthropic key rules enforced.
    - Provider->env mapping behaves exactly as specified.
  - RAG concurrency:
    - Launch two async method-step tasks simultaneously; assert no Chroma lock errors with per-job dirs.
  - Rate-limit handling:
    - Simulate vision-parser 429/rate-limit exception; assert step becomes `failed`, job `error` is human-readable, and UI status endpoint surfaces it.
  - Missing PDF guard:
    - Simulate deleted staged PDF before figure step; assert deterministic `failed` state with explicit remediation message.

## Assumptions
- Compatibility-first excludes adding new UI controls for orchestrator/state-graph behavior.
- Per-job RAG isolation is the default reliability posture; shared cache is deferred and opt-in.
