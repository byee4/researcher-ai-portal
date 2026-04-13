# researcher-ai-portal

A Django portal that wraps the [`researcher-ai`](https://github.com/byee4/researcher-ai) package, letting researchers submit any publication (PubMed ID or PDF) and extract a fully structured computational workflow: figures, assay graphs, datasets, software, and an executable Snakemake/Nextflow pipeline config.

## What it does

1. Accept a PubMed ID, DOI, or uploaded PDF.
2. Run a six-step LLM-powered parsing pipeline (Paper → Figures → Method → Datasets → Software → Pipeline).
3. Let the user inspect, edit, and correct each parsed component through a web UI.
4. Render an interactive assay DAG, figure gallery, confidence dashboard, and a dedicated Methods RAG workflow visualization page.
5. Expose a FastAPI layer under `/api/v1/` for the visual pipeline builder and diagnostics APIs — submit publications, poll status, read/write React Flow graph state, and fetch normalized RAG telemetry.
   - Note: the dashboard Pipeline Builder tab is currently hidden behind a temporary server flag while other UX flows are prioritized.

Supports OpenAI (GPT-4/o4), Anthropic (Claude), and Google (Gemini) models. API keys are entered per-session and never stored in the database.

---

## Release notes

### v3.0.0 (April 11, 2026)

- Major baseline upgrade to `researcher-ai` release tag `v3.0.0` (`git+https://github.com/byee4/researcher-ai.git@v3.0.0`).
- Updated Docker and local setup defaults so installs are pinned to the same `v3.0.0` source by default.
- Harmonized Docker and non-Docker startup behavior to the same ASGI command:
  - `uvicorn researcher_ai_portal.asgi:application --reload --host 0.0.0.0 --port 8000`
- Documented and set safer runtime defaults for long parses (timeouts, retry caps, and token budgets) to reduce stuck runs and runaway latency.
- Verified non-live regression coverage against `researcher-ai` `v3.0.0` (`151 passed`).
- Bugfix documentation: expanded setup docs with a full default-env reference and explicit timeout/retry defaults so operators can reason about behavior without reading source code.

### v2.3.0 (April 10, 2026)

- Updated default package pin to `researcher-ai==2.3.0`.
- Verified portal compatibility by running the full test suite against `researcher-ai` 2.3.0.
- Added a live PMID `39303722` validation report documenting observed failures and fallback behavior.

### v2.2.2 (April 9, 2026)

- Updated default package pin to `researcher-ai==2.2.2`.
- Verified portal compatibility with `researcher-ai` v2.2.2 parser/orchestrator behavior.
- Documented optional new figure tuning env vars:
  - `RESEARCHER_AI_PARSE_FIGURES_TIMEOUT_PER_FIGURE_SECONDS`
  - `RESEARCHER_AI_SUBFIGURE_DECOMPOSE_MAX_TOKENS`
  - `RESEARCHER_AI_FIGURE_PURPOSE_MAX_TOKENS`
  - `RESEARCHER_AI_FIGURE_METHODS_DATASETS_MAX_TOKENS`

### v2.1.1 (April 8, 2026)

- Integrated compatibility updates for `researcher-ai==2.1.1`.
- Added terminal `needs_human_review` handling in portal polling/status flows.
- Added persistent diagnostic surfacing for review metadata and vision fallback telemetry.

---

## Quick start

### Docker (recommended)

Requires [Docker Desktop](https://www.docker.com/products/docker-desktop/).

```bash
# Clone and enter the portal
git clone <repo-url> researcher-ai-portal
cd researcher-ai-portal

# Launch Postgres + web server
./run_portal.sh
```

Open **http://localhost:8000** — that's it.

By default, Docker installs `researcher-ai` from the pinned `v3.0.0` release URL.  
If you want local co-development instead, set:

```bash
export RESEARCHER_AI_SRC=/path/to/researcher-ai
```

Force a full image rebuild after dependency changes:

```bash
FORCE_BUILD=1 ./run_portal.sh
```

### Local (native Python)

```bash
cd researcher-ai-portal

# Install portal + researcher-ai
pip install -r requirements.txt
pip install --upgrade "git+https://github.com/byee4/researcher-ai.git@v3.0.0"

# Optional: local editable co-development instead of pinned release
# pip install -e /path/to/researcher-ai

# Copy and fill in the environment file
cp .env.example .env   # edit DJANGO_SECRET_KEY at minimum

# Set up the database and static files
python manage.py migrate
python manage.py collectstatic --noinput

# Start the server (ASGI, supports Django + FastAPI)
uvicorn researcher_ai_portal.asgi:application --reload --host 0.0.0.0 --port 8000
```

---

## Documentation

| Document | Description |
|----------|-------------|
| [`docs/SETUP.md`](docs/SETUP.md) | Full setup guide: prerequisites, environment variables, local dev, Docker, and production deployment |
| [`docs/TUTORIAL.md`](docs/TUTORIAL.md) | End-to-end walkthrough: parsing a paper, editing components, reading the dashboard |
| [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) | System architecture, component inventory, FastAPI integration design |
| [`docs/REGRESSION_TEST_REPORT_v3.0.0.md`](docs/REGRESSION_TEST_REPORT_v3.0.0.md) | Non-live regression test report for portal compatibility against `researcher-ai` `v3.0.0` |
| [`docs/PMID_39303722_portal_test_2026-04-10_v230.md`](docs/PMID_39303722_portal_test_2026-04-10_v230.md) | Live compatibility test report for `researcher-ai` v2.3.0 (PMID `39303722`) |

---

## Running tests

```bash
python3 -m pytest researcher_ai_portal_app/tests -q
```

---

## API reference

Interactive Swagger docs are available at **http://localhost:8000/api/v1/docs** when the server is running.

### Phase 2 endpoints (visual builder)

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/v1/parse-publication` | Submit a publication; returns `202` with a `job_id`. Runs all six pipeline steps in the background. |
| `GET` | `/api/v1/jobs/{job_id}/status` | Poll parsing progress. Returns `status`, `progress` (0–100), `stage`, and `parse_logs`. |
| `GET` | `/api/v1/jobs/{job_id}/rag-workflow` | Retrieve normalized Methods-step RAG telemetry and timeline data for diagnostics UI. |
| `GET` | `/api/v1/graphs/{job_id}` | Retrieve the auto-generated React Flow graph once parsing completes. |
| `PUT` | `/api/v1/graphs/{job_id}` | Persist the graph after the user rearranges nodes. |
| `GET` | `/api/v1/graphs/{job_id}/nodes/{node_id}` | Full parsed payload for a single pipeline step. |

For long orchestrator runs, the status payload now advances `current_step`/`stage` as each parser node starts (paper → figures → method → datasets → software → pipeline), so the progress view reflects the active stage instead of staying pinned on `paper`.

All endpoints require the Django session cookie (`credentials: "include"` from the browser, or `--cookie sessionid=...` from curl).

---

## Environment variables

See [`docs/SETUP.md`](docs/SETUP.md#environment-variables) for the full reference. The minimum set for local development:

```env
DJANGO_SECRET_KEY=any-long-random-string
DJANGO_DEBUG=True
DJANGO_ALLOWED_HOSTS=localhost,127.0.0.1
DATABASE_URL=                  # omit to use SQLite fallback
```
