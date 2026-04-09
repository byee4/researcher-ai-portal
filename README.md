# researcher-ai-portal

A Django portal that wraps the [`researcher-ai`](https://github.com/byee4/researcher-ai) package, letting researchers submit any publication (PubMed ID or PDF) and extract a fully structured computational workflow: figures, assay graphs, datasets, software, and an executable Snakemake/Nextflow pipeline config.

## What it does

1. Accept a PubMed ID, DOI, or uploaded PDF.
2. Run a six-step LLM-powered parsing pipeline (Paper → Figures → Method → Datasets → Software → Pipeline).
3. Let the user inspect, edit, and correct each parsed component through a web UI.
4. Render an interactive assay DAG, figure gallery, confidence dashboard, and a dedicated Methods RAG workflow visualization page.
5. Expose a FastAPI layer under `/api/v1/` for the visual pipeline builder and diagnostics APIs — submit publications, poll status, read/write React Flow graph state, and fetch normalized RAG telemetry.

Supports OpenAI (GPT-4/o4), Anthropic (Claude), and Google (Gemini) models. API keys are entered per-session and never stored in the database.

---

## Release notes

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

Requires [Docker Desktop](https://www.docker.com/products/docker-desktop/) and a local checkout of `researcher-ai`.

```bash
# Clone and enter the portal
git clone <repo-url> researcher-ai-portal
cd researcher-ai-portal

# Point to your local researcher-ai source (or set RESEARCHER_AI_SRC in env)
export RESEARCHER_AI_SRC=/path/to/researcher-ai

# Launch Postgres + web server
./run_portal.sh
```

Open **http://localhost:8000** — that's it.

Force a full image rebuild after dependency changes:

```bash
FORCE_BUILD=1 ./run_portal.sh
```

### Local (native Python)

```bash
cd researcher-ai-portal

# Install portal + researcher-ai
pip install -r requirements.txt
pip install -e /path/to/researcher-ai

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

---

## Running tests

```bash
python -m pytest researcher_ai_portal_app/tests -q
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
