# Setup & Deployment Guide

This document covers everything needed to run `researcher-ai-portal` — from a first-time local install to a production server.

---

## Release baseline

`v3.0.0` is the current major release baseline, pinned to `researcher-ai` tag `v3.0.0`.

If you need to reproduce the exact baseline state:

```bash
git fetch --tags
git checkout v3.0.0
```

For new major feature branches, run a quick preflight first:

```bash
python manage.py check
pytest -q
```

---

## Prerequisites

| Requirement | Version | Notes |
|-------------|---------|-------|
| Python | 3.11+ | 3.12 recommended |
| Docker + Compose | v24+ | Docker Desktop on Mac/Windows |
| `researcher-ai` package | 3.0.0 | Pinned GitHub release URL by default (`git+https://github.com/byee4/researcher-ai.git@v3.0.0`) |
| PostgreSQL | 14+ | Docker image used in all compose setups; SQLite works for development |
| An LLM API key | — | OpenAI, Anthropic, or Google — entered per-session in the UI |

---

## Environment variables

All configuration is environment-driven. Copy `.env.example` to `.env` and fill in the values relevant to your setup.

### Required for any environment

| Variable | Description | Default |
|----------|-------------|---------|
| `DJANGO_SECRET_KEY` | Django cryptographic key. Must be long and random in production. | Insecure dev key |
| `DJANGO_DEBUG` | `True` for development, `False` for production. | `True` |
| `DJANGO_ALLOWED_HOSTS` | Comma-separated list of hostnames Django will serve. | `*` (dev) |

### Database

| Variable | Description | Default |
|----------|-------------|---------|
| `DATABASE_URL` | PostgreSQL connection string, e.g. `postgresql://user:pass@host:5432/db`. Omit to use a local SQLite file (`template.db`). | — |

### Globus authentication (optional)

Globus OAuth is the primary auth backend. Without it, the app falls back to Django's local authentication, which is sufficient for development.

| Variable | Description |
|----------|-------------|
| `GLOBUS_CLIENT_ID` | OAuth client ID from [app.globus.org](https://app.globus.org) |
| `GLOBUS_CLIENT_SECRET` | OAuth client secret |
| `GLOBUS_ADMIN_GROUP` | UUID of a Globus group whose members receive admin access |
| `SOCIAL_AUTH_GLOBUS_REDIRECT_URI` | Full callback URL, e.g. `https://yourdomain.com/complete/globus/` |
| `SOCIAL_AUTH_REDIRECT_IS_HTTPS` | `True` in production behind TLS |

### LLM provider routing (server-side fallback)

The UI always accepts an API key per session. These env vars let the server supply a default key when the user leaves the field blank — useful for shared deployments.

| Variable | Provider |
|----------|----------|
| `OPENAI_API_KEY` | GPT-4, o4, o3 models |
| `ANTHROPIC_API_KEY` | Claude models |
| `GEMINI_API_KEY` | Gemini models |

### researcher-ai v3 runtime controls

| Variable | Description | Default |
|----------|-------------|---------|
| `RESEARCHER_AI_VISION_MODEL` | Override the default multimodal model used for figure parsing | provider default |
| `RESEARCHER_AI_PARSE_FIGURES_TIMEOUT_SECONDS` | Global timeout for figure parsing in a single run | `1800` |
| `RESEARCHER_AI_PARSE_FIGURES_TIMEOUT_PER_FIGURE_SECONDS` | Figure-count timeout floor used when total figure parse timeout is configured | `180` |
| `RESEARCHER_AI_LLM_TIMEOUT_SECONDS` | Per-request LLM network timeout used by researcher-ai | `180` |
| `RESEARCHER_AI_SUBFIGURE_TIMEOUT_SECONDS` | Timeout per subfigure multimodal extraction call | `180` |
| `RESEARCHER_AI_MAX_FIGURE_LLM_TIMEOUTS` | Max tolerated figure-level LLM timeout events before failing the step | `4` |
| `RESEARCHER_AI_PROVIDER_MAX_RETRIES` | Upper bound on provider retry attempts for transient failures | `2` |
| `RESEARCHER_AI_SUBFIGURE_DECOMPOSE_MAX_TOKENS` | Token budget for per-subfigure decomposition calls | `1800` |
| `RESEARCHER_AI_FIGURE_PURPOSE_MAX_TOKENS` | Token budget for figure-purpose extraction calls | `900` |
| `RESEARCHER_AI_FIGURE_METHODS_DATASETS_MAX_TOKENS` | Token budget for figure methods/datasets extraction calls | `700` |
| `RESEARCHER_AI_DISABLE_MODEL_FALLBACKS` | Disable model-fallback routing during investigation/debug runs | unset (`false`) |
| `RESEARCHER_AI_RAG_MODE` | `per_job` (isolated vector store per parse job) or `shared` | `per_job` |
| `RESEARCHER_AI_RAG_BASE_DIR` | Base directory for RAG persistence when using `per_job` mode | `/tmp` |
| `RESEARCHER_AI_BIOWORKFLOW_MODE` | BioWorkflow rollout mode: `off`, `warn`, `on` | `warn` |
| `RESEARCHER_AI_MAX_RETRIEVAL_REFINEMENT_ROUNDS` | Hard cap for retrieval refinement rounds in method parsing | `3` |
| `RESEARCHER_AI_PORTAL_RUNNER_MODE` | Portal full-run execution adapter mode: `orchestrator` or `legacy` | `orchestrator` |
| `RESEARCHER_AI_EXPECTED_VERSION` | Optional expected `researcher-ai` version for runtime drift checks (recommended in shared/dev/prod envs) | unset |
| `RESEARCHER_AI_PORTAL_ORCHESTRATOR_SOFT_TIMEOUT_SECONDS` | Soft timeout warning threshold for orchestrator runner | `3600` |
| `RESEARCHER_AI_PORTAL_ORCHESTRATOR_HARD_TIMEOUT_SECONDS` | Hard timeout for orchestrator runner (fails job when exceeded) | `7200` |
| `RESEARCHER_AI_PORTAL_ORCHESTRATOR_CALL_TIMEOUT_SECONDS` | Optional wall-clock timeout around `WorkflowOrchestrator.run()` (fails fast even if downstream call blocks) | falls back to hard timeout |
| `RESEARCHER_AI_PORTAL_ORCHESTRATOR_HEARTBEAT_SECONDS` | Heartbeat interval while an orchestrator node is running; keeps `current_step`/`stage` fresh during long steps | `15` |
| `RESEARCHER_AI_PORTAL_STUCK_JOB_TIMEOUT_SECONDS` | Marks a job as stalled when no updates arrive for this many seconds | `3600` |
| `RESEARCHER_AI_PORTAL_LEGACY_SOFT_TIMEOUT_SECONDS` | Soft timeout warning threshold for legacy runner | `5400` |
| `RESEARCHER_AI_PORTAL_LEGACY_HARD_TIMEOUT_SECONDS` | Hard timeout for legacy runner (fails job when exceeded) | `10800` |

### FastAPI / CORS

| Variable | Description | Default |
|----------|-------------|---------|
| `FASTAPI_CORS_ORIGINS` | Comma-separated allowed origins for `/api/v1/` endpoints. Only needed when the React frontend is served from a different port during development. | `http://localhost:3000,http://localhost:5173` |

### Production security

| Variable | Description | Default when `DEBUG=False` |
|----------|-------------|---------------------------|
| `SECURE_SSL_REDIRECT` | Redirect HTTP to HTTPS | `True` |
| `SECURE_HSTS_SECONDS` | HSTS max-age in seconds | `31536000` (1 year) |
| `SECURE_HSTS_INCLUDE_SUBDOMAINS` | Extend HSTS to subdomains | `True` |
| `SESSION_COOKIE_SECURE` | Send session cookie over HTTPS only | `True` |
| `CSRF_COOKIE_SECURE` | Send CSRF cookie over HTTPS only | `True` |
| `CSRF_TRUSTED_ORIGINS` | Comma-separated origins trusted for CSRF (required behind a reverse proxy) | — |

### Complete default env var reference

This table lists every environment variable used by the portal runtime with a built-in default value when unset.

| Variable | Default | Plain-English meaning |
|----------|---------|-----------------------|
| `DJANGO_SECRET_KEY` | `django-insecure-template-dev-key-change-in-production` | Dev-only fallback signing key. Replace in any shared or production deployment. |
| `DJANGO_DEBUG` | `True` | Enables debug behavior and relaxed security defaults. |
| `DJANGO_ALLOWED_HOSTS` | `*` | Accept requests for any host header in development. |
| `TEMPLATE_DB_PATH` | `<repo>/template.db` | SQLite file path used when `DATABASE_URL` is unset. |
| `SESSION_ENGINE` | `django.contrib.sessions.backends.db` | Stores sessions in Django's database tables. |
| `DJANGO_LOG_LEVEL` | `WARNING` | App logger threshold for portal/Django logs. |
| `SESSION_COOKIE_SAMESITE` | `Lax` | Browser cookie cross-site policy for session cookie. |
| `CSRF_COOKIE_SAMESITE` | `Lax` | Browser cookie cross-site policy for CSRF cookie. |
| `SOCIAL_AUTH_REDIRECT_IS_HTTPS` | `True` when `DEBUG=False`, otherwise `False` | Controls OAuth callback URL scheme handling behind proxies. |
| `FASTAPI_CORS_ORIGINS` | `http://localhost:3000,http://localhost:5173` | Allowed browser origins for cross-origin API calls in local frontend dev. |
| `HF_HUB_DISABLE_IMPLICIT_TOKEN` | `1` | Silences HuggingFace unauthenticated-token warnings in shared environments. |
| `TOKENIZERS_PARALLELISM` | `false` | Suppresses tokenizer fork warnings in multiprocess runs. |
| `FIGURE_PROXY_CACHE_TTL_SEC` | `604800` | Figure-proxy cache retention in seconds (7 days). |
| `LLM_MODEL` | `gpt-5.4` | API fallback model when request/job model is omitted. |
| `LLM_API_KEY` | empty | API fallback key when the request does not include one. |
| `RESEARCHER_AI_PORTAL_RUNNER_MODE` | `orchestrator` | Chooses orchestrator execution path instead of legacy runner. |
| `RESEARCHER_AI_PORTAL_ORCHESTRATOR_SOFT_TIMEOUT_SECONDS` | `3600` | Warn threshold for long orchestrator runs. |
| `RESEARCHER_AI_PORTAL_ORCHESTRATOR_HARD_TIMEOUT_SECONDS` | `7200` | Hard fail timeout for orchestrator runs. |
| `RESEARCHER_AI_PORTAL_ORCHESTRATOR_HEARTBEAT_SECONDS` | `15` | Status heartbeat interval while a node is executing. |
| `RESEARCHER_AI_PORTAL_ORCHESTRATOR_CALL_TIMEOUT_SECONDS` | unset | Optional wrapper timeout around orchestrator call boundary. |
| `RESEARCHER_AI_PORTAL_LEGACY_SOFT_TIMEOUT_SECONDS` | `5400` | Warn threshold for legacy runs. |
| `RESEARCHER_AI_PORTAL_LEGACY_HARD_TIMEOUT_SECONDS` | `10800` | Hard fail timeout for legacy runs. |
| `RESEARCHER_AI_RAG_MODE` | `per_job` | Uses isolated per-job RAG vector stores by default. |
| `RESEARCHER_AI_RAG_BASE_DIR` | empty | Base directory for RAG persistence (when required by mode). |
| `RESEARCHER_AI_BIOWORKFLOW_MODE` | `warn` | Keeps BioWorkflow in warning mode unless explicitly tightened. |
| `RESEARCHER_AI_PARSE_FIGURES_TIMEOUT_SECONDS` | `1800` | End-to-end figure parsing timeout cap. |
| `RESEARCHER_AI_PARSE_FIGURES_TIMEOUT_PER_FIGURE_SECONDS` | `180` | Scales figure timeout floor with figure count. |
| `RESEARCHER_AI_LLM_TIMEOUT_SECONDS` | `180` | Network timeout for each LLM request. |
| `RESEARCHER_AI_SUBFIGURE_TIMEOUT_SECONDS` | `180` | Timeout for each subfigure call. |
| `RESEARCHER_AI_MAX_FIGURE_LLM_TIMEOUTS` | `4` | Fails figure stage after repeated timeout events. |
| `RESEARCHER_AI_PROVIDER_MAX_RETRIES` | `2` | Retry budget for provider-level transient errors. |
| `RESEARCHER_AI_SUBFIGURE_DECOMPOSE_MAX_TOKENS` | `1800` | Token budget for detailed subfigure decomposition prompts. |
| `RESEARCHER_AI_FIGURE_PURPOSE_MAX_TOKENS` | `900` | Token budget for figure-purpose extraction prompts. |
| `RESEARCHER_AI_FIGURE_METHODS_DATASETS_MAX_TOKENS` | `700` | Token budget for figure-to-method/dataset extraction prompts. |
| `RESEARCHER_AI_MAX_RETRIEVAL_REFINEMENT_ROUNDS` | `3` | Maximum iterative refinement loops in method retrieval. |
| `RESEARCHER_AI_PORTAL_STUCK_JOB_TIMEOUT_SECONDS` | `3600` | Marks jobs stalled after one hour without updates. |
| `SECURE_SSL_REDIRECT` | `True` when `DEBUG=False` | Redirects HTTP requests to HTTPS in production. |
| `SECURE_HSTS_SECONDS` | `31536000` when `DEBUG=False` | Browser HSTS max-age (1 year). |
| `SECURE_HSTS_INCLUDE_SUBDOMAINS` | `True` when `DEBUG=False` | Applies HSTS policy to subdomains. |
| `SECURE_HSTS_PRELOAD` | `True` when `DEBUG=False` | Enables preload-ready HSTS policy in production. |
| `SESSION_COOKIE_SECURE` | `True` when `DEBUG=False` | Restricts session cookie transmission to HTTPS only. |
| `CSRF_COOKIE_SECURE` | `True` when `DEBUG=False` | Restricts CSRF cookie transmission to HTTPS only. |

---

## Option 1: Docker (recommended)

This is the fastest path to a running stack. It starts a PostgreSQL database and the web server in containers, with no local Python environment needed beyond Docker itself.

### Step 1 — Choose your researcher-ai install source

```bash
# Default pinned release (already used when unset):
export RESEARCHER_AI_PIP_SPEC="git+https://github.com/byee4/researcher-ai.git@v3.0.0"

# Optional local co-development override:
# export RESEARCHER_AI_SRC=/absolute/path/to/researcher-ai
```

`run_portal.sh` installs from the pinned release by default. If `RESEARCHER_AI_SRC` is set, it syncs that local checkout into `.vendor/researcher-ai/`, builds a wheel, and installs the local wheel in the Docker image.

### Step 2 — Configure environment (optional for first run)

```bash
cp .env.example .env
# Edit .env — set DJANGO_SECRET_KEY at minimum
# Add GLOBUS_* values if you want Globus auth
```

### Step 3 — Launch

```bash
chmod +x run_portal.sh
./run_portal.sh
```

The script:
1. Uses the pinned `researcher-ai` `v3.0.0` release by default.
2. If `RESEARCHER_AI_SRC` is set, builds a local `researcher-ai` wheel instead.
3. Passes the selected package spec to `docker compose build`.
4. Starts `db` (Postgres) and `web` (the portal) containers.

### Step 4 — Open the portal

- Web UI: http://localhost:8000
- Health check: http://localhost:8000/healthz/
- FastAPI docs: http://localhost:8000/api/v1/docs

### Useful Docker commands

```bash
# Tail logs from all containers
docker compose logs -f

# Force a full image rebuild (after requirements.txt changes)
FORCE_BUILD=1 ./run_portal.sh

# Override the researcher-ai source path
RESEARCHER_AI_SRC=/other/path ./run_portal.sh

# Override the pinned release spec directly
RESEARCHER_AI_PIP_SPEC="git+https://github.com/byee4/researcher-ai.git@v3.0.0" ./run_portal.sh

# Stop all containers
docker compose down

# Stop and wipe the database volume
docker compose down -v
```

---

## Option 2: Local native Python

Suitable for active development where you want hot-reload and direct access to the Python process.

### Step 1 — Create a virtual environment

```bash
cd researcher-ai-portal
python3.11 -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
```

### Step 2 — Install dependencies

```bash
pip install -r requirements.txt
```

### Step 3 — Install researcher-ai

For local co-development (editable install, changes take effect immediately):

```bash
pip install -e /path/to/researcher-ai
```

For a pinned release:

```bash
pip install --upgrade "git+https://github.com/byee4/researcher-ai.git@v3.0.0"
```

Optional — install the Cytoscape DAG canvas:

```bash
pip install dash-cytoscape
```

### Step 4 — Configure environment

```bash
cp .env.example .env
```

Minimum `.env` for local development:

```env
DJANGO_SECRET_KEY=any-long-random-string-here
DJANGO_DEBUG=True
DJANGO_ALLOWED_HOSTS=localhost,127.0.0.1
```

### Step 5 — Set up the database

```bash
python manage.py migrate
python manage.py collectstatic --noinput
```

By default this creates a SQLite database at `template.db` in the project root. To use PostgreSQL instead, set `DATABASE_URL` in `.env` before running migrations.

### Step 6 — Start the server

```bash
uvicorn researcher_ai_portal.asgi:application --reload --host 0.0.0.0 --port 8000
```

`--reload` watches for source changes and restarts automatically. The server handles both Django routes and the FastAPI `/api/v1/` layer through a single ASGI process.

Open http://localhost:8000.

### Running tests

```bash
python3 -m pytest researcher_ai_portal_app/tests -q
```

Three tests (`test_dataset_step_key_resources`, `test_phase8_pdf_staging`, `test_phase9_rag_isolation`) require `researcher_ai` models to be importable and are expected to be skipped in environments where only the portal is installed without a full researcher-ai checkout.

---

## Option 3: Production deployment

This section covers a production setup behind a reverse proxy (nginx / Caddy / load balancer).

### Server process

The server command is in `scripts/start_web.sh`. Docker and non-Docker local runs now use the same ASGI command for consistent behavior:

```bash
uvicorn researcher_ai_portal.asgi:application --reload --host 0.0.0.0 --port 8000
```

For production environments that require process supervision and multiple workers, run a process manager (for example `systemd` or `supervisord`) around this same ASGI command.

### Required environment for production

```env
DJANGO_DEBUG=False
DJANGO_SECRET_KEY=<generate with: python -c "import secrets; print(secrets.token_urlsafe(50))">
DJANGO_ALLOWED_HOSTS=yourdomain.com,www.yourdomain.com
DATABASE_URL=postgresql://user:password@host:5432/researcher_ai
CSRF_TRUSTED_ORIGINS=https://yourdomain.com

# Globus auth (required if not using local Django auth)
GLOBUS_CLIENT_ID=...
GLOBUS_CLIENT_SECRET=...
SOCIAL_AUTH_GLOBUS_REDIRECT_URI=https://yourdomain.com/complete/globus/
SOCIAL_AUTH_REDIRECT_IS_HTTPS=True
```

### Reverse proxy: nginx example

```nginx
server {
    listen 443 ssl;
    server_name yourdomain.com;

    # TLS config (certbot / Let's Encrypt)
    ssl_certificate     /etc/letsencrypt/live/yourdomain.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/yourdomain.com/privkey.pem;

    # Pass everything to the ASGI process
    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host              $host;
        proxy_set_header X-Real-IP         $remote_addr;
        proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 150s;   # should exceed expected long-running parser calls
    }

    # WebSocket support for future FastAPI WS endpoints
    location /api/v1/ws/ {
        proxy_pass http://127.0.0.1:8000;
        proxy_http_version 1.1;
        proxy_set_header Upgrade    $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_read_timeout 300s;
    }
}

server {
    listen 80;
    server_name yourdomain.com;
    return 301 https://$host$request_uri;
}
```

### Database

Run migrations before starting the application server:

```bash
python manage.py migrate --noinput
python manage.py collectstatic --noinput
```

In Docker, `scripts/start_web.sh` already runs `migrate` and `collectstatic` at container startup before starting Uvicorn.

### Static files

WhiteNoise serves static files directly from the application process — no separate nginx `location /static/` block is needed. Static files are collected to `researcher_ai_portal/staticfiles/` at build time.

### Healthcheck

The `/healthz/` endpoint returns `200 OK` with the text `ok`. Use this for load balancer and container health checks.

---

## Upgrading researcher-ai

For Docker deployments, re-run `./run_portal.sh` after updating your desired package spec (`RESEARCHER_AI_PIP_SPEC`) or local source override (`RESEARCHER_AI_SRC`).

For native deployments:

```bash
pip install --upgrade -e /path/to/researcher-ai
# or
pip install --upgrade "git+https://github.com/byee4/researcher-ai.git@v3.0.0"

python manage.py migrate   # apply any new migrations
```

---

## Redeploying after Phase 2

Phase 2 adds a new database column (`graph_data` on `WorkflowJob`) and new API endpoints. The steps below apply to any environment being upgraded from Phase 1.

### Docker (recommended)

```bash
# 1. Pull or copy the updated code into place (git pull, rsync, etc.)

# 2. Rebuild the image and restart containers
FORCE_BUILD=1 ./run_portal.sh

# That's it — scripts/start_web.sh runs migrate automatically before
# starting Uvicorn, so the new migration (0003_workflowjob_graph_data)
# is applied inside the container on startup.
```

Verify the new endpoints are live:

```bash
curl http://localhost:8000/api/v1/ping
# → {"status":"ok","framework":"fastapi","version":"..."}

curl -s http://localhost:8000/api/v1/docs | grep -o "parse-publication"
# → parse-publication  (confirms the Phase 2 routes registered correctly)
```

### Native Python

```bash
# 1. Pull updated code
git pull

# 2. No new Python packages needed for Phase 2 (fastapi/uvicorn already in requirements.txt)

# 3. Apply the new migration
python manage.py migrate

# 4. Restart the server (kill existing uvicorn if running, then:)
uvicorn researcher_ai_portal.asgi:application --reload --host 0.0.0.0 --port 8000
```

### What changed on disk

| Path | Change |
|------|--------|
| `researcher_ai_portal_app/models.py` | `graph_data = models.JSONField(...)` added to `WorkflowJob` |
| `researcher_ai_portal_app/migrations/0003_workflowjob_graph_data.py` | New migration — **must be applied** |
| `researcher_ai_portal_app/api/schemas.py` | Phase 2 Pydantic types added |
| `researcher_ai_portal_app/api/repository.py` | `get_graph_state`, `get_component_snapshot`, `get_job_status` added |
| `researcher_ai_portal_app/api/routes.py` | Five new endpoints added |
| `researcher_ai_portal_app/api/graph_layout.py` | New file — auto-layout utility |

No existing Django URL patterns, views, or templates were modified. Rollback is safe: reverting these files and running `python manage.py migrate researcher_ai_portal_app 0002` removes the column and restores Phase 1.

---

## Troubleshooting

**`ModuleNotFoundError: No module named 'researcher_ai'`**
The `researcher-ai` package is not installed. Run `pip install --upgrade "git+https://github.com/byee4/researcher-ai.git@v3.0.0"` or `pip install -e /path/to/researcher-ai`.

**`DisallowedHost` error**
Add your hostname to `DJANGO_ALLOWED_HOSTS` in `.env`.

**Globus login redirects to wrong URL**
Set `SOCIAL_AUTH_GLOBUS_REDIRECT_URI` to the full callback URL including protocol: `https://yourdomain.com/complete/globus/`.

**Parse steps time out in Docker**
Increase the parser timeout env vars in `.env` (`RESEARCHER_AI_PARSE_FIGURES_TIMEOUT_SECONDS`, `RESEARCHER_AI_PARSE_FIGURES_TIMEOUT_PER_FIGURE_SECONDS`, `RESEARCHER_AI_LLM_TIMEOUT_SECONDS`) and increase `proxy_read_timeout` in nginx for long-running requests.

**FastAPI docs not loading at `/api/v1/docs`**
Confirm the server is started with `researcher_ai_portal.asgi:application`, not the legacy `wsgi:application`. The WSGI entry point does not include FastAPI.

**`POST /api/v1/parse-publication` returns 500 with a migration error**
The Phase 2 migration hasn't been applied. Run `python manage.py migrate` (native) or `FORCE_BUILD=1 ./run_portal.sh` (Docker) and retry.

**`GET /api/v1/graphs/{job_id}` returns an empty graph after parsing**
The background pipeline thread may still be running. Check `GET /api/v1/jobs/{job_id}/status` — wait for `status: "completed"` before fetching the graph.

**Pipeline background thread silently fails**
Check `GET /api/v1/jobs/{job_id}/status` for `status: "failed"` and the `error` field. Common causes: missing LLM API key (`llm_api_key` field in the request body or `LLM_API_KEY` env var), or researcher-ai not installed.
