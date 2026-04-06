# researcher-ai-portal Architecture (Current)

This document consolidates architecture/development understanding for `researcher-ai-portal` and validates against the current Django codebase.

## Scope and Inputs

Merged source sets:
- Portal-oriented historical docs: `README_QUICKSTART.md`, `README_django.md`, `TUTORIAL.md`
- Code validation anchors: `researcher_ai_portal/settings.py`, `researcher_ai_portal/urls.py`, `researcher_ai_portal_app/views.py`, `researcher_ai_portal_app/forms.py`, `researcher_ai_portal_app/dashboards.py`, `researcher_ai_portal_app/job_store.py`

## System Overview

`researcher-ai-portal` is a Django front end around the `researcher-ai` package workflow.

Main flow:
1. User submits PMID or PDF on `home`.
2. Portal creates an in-memory job record (`job_store.py`).
3. Workflow steps run sequentially (`paper` → `figures` → `method` → `datasets` → `software` → `pipeline`) via `_run_step`.
4. Each step output is validated/serialized and persisted to job components.
5. Users can inspect/edit JSON per step and inject figure ground truth.
6. Dashboard renders summaries and charts from stored components.

## Architecture Components

### Django Project Layer

- Settings module: `researcher_ai_portal/settings.py`
- URL router: `researcher_ai_portal/urls.py`
- App module: `researcher_ai_portal_app`

Key settings behavior:
- Environment-first configuration (dotenv from workspace root `.env` when present).
- SQLite fallback (`template.db`) when `DATABASE_URL` is not provided.
- Optional Globus/social-auth integration is configured but app routes also support local development patterns.
- Static serving uses WhiteNoise + Django Plotly Dash static finders.

### Workflow Orchestration Layer (`researcher_ai_portal_app/views.py`)

- `start_parse` validates LLM model/API key and starts a job.
- `_run_step` imports `researcher_ai` runtime modules and executes one parser stage at a time.
- Figure step parses primary figures incrementally and updates per-figure progress metadata.
- Dataset step extracts accessions from paper/method/figure text and dispatches GEO/SRA parsers.
- Pipeline step invokes `PipelineBuilder.build` on prior components.

### Job State Layer (`researcher_ai_portal_app/job_store.py`)

- In-memory dict (`_JOBS`) guarded by a threading lock.
- Tracks current status, progress, component payloads, component quality metadata, and errors.
- No persistent DB-backed job queue at present.

### Human-in-the-Loop Layer

- `workflow_step` allows rerun/save/next/prev actions per stage.
- `ComponentJSONForm` provides JSON editing with `django_svelte_jsoneditor`.
- `FigureGroundTruthForm` allows panel-level corrections (plot type/category/axes/confidence marker).
- Ground truth is injected directly into component payload and revalidated against Pydantic models.

### Dashboard Layer (`researcher_ai_portal_app/dashboards.py`)

- Creates per-job Dash apps using Django Plotly Dash.
- Displays counts, component quality status, simple pipeline topology, and extracted entity lists.

### Figure Media Proxy

- `figure_image_proxy` fetches remote figure media with allowlist-style URL checks and placeholder blocking heuristics.
- Supports resolving HTML pages to embedded image URLs when direct image links are unavailable.

## Integration Contract with researcher-ai

Runtime imports target the installed Python package `researcher_ai` (no repository path injection).

Expected outputs:
- `Paper`, `Figure[]`, `Method`, `Dataset[]`, `Software[]`, `Pipeline` Pydantic payloads.
- The portal validates user-edited payloads against those same models before saving.

## Testing Surface

Portal tests currently target workflow/dataset/figure-ground-truth behavior under `researcher_ai_portal_app/tests/`.

## Accuracy Notes (Compared to Older Docs)

- Current implementation executes step-by-step in-process using `_run_step`; it is not a distributed/background queue worker architecture.
- Job persistence is memory-backed, so restarts clear job state.
- The actual Django project directory for runtime commands is `researcher-ai-portal`, not `researcher_ai`.
