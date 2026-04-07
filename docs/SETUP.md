# Setup & Deployment Guide

This document covers everything needed to run `researcher-ai-portal` — from a first-time local install to a production server.

---

## Prerequisites

| Requirement | Version | Notes |
|-------------|---------|-------|
| Python | 3.11+ | 3.12 recommended |
| Docker + Compose | v24+ | Docker Desktop on Mac/Windows |
| `researcher-ai` package | 2.0.0+ | Local checkout or pip release |
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

### researcher-ai v2 runtime controls

| Variable | Description | Default |
|----------|-------------|---------|
| `RESEARCHER_AI_VISION_MODEL` | Override the default multimodal model used for figure parsing | provider default |
| `RESEARCHER_AI_RAG_MODE` | `per_job` (isolated vector store per parse job) or `shared` | `per_job` |
| `RESEARCHER_AI_RAG_BASE_DIR` | Base directory for RAG persistence when using `per_job` mode | `/tmp` |

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

---

## Option 1: Docker (recommended)

This is the fastest path to a running stack. It starts a PostgreSQL database and the web server in containers, with no local Python environment needed beyond Docker itself.

### Step 1 — Point to your researcher-ai source

```bash
export RESEARCHER_AI_SRC=/absolute/path/to/researcher-ai
```

`run_portal.sh` syncs this directory into `.vendor/researcher-ai/`, builds a wheel, and installs it inside the Docker image. This means the image always contains the exact researcher-ai code you have locally.

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
1. Syncs and builds a `researcher-ai` wheel.
2. Passes the wheel path to `docker compose build`.
3. Starts `db` (Postgres) and `web` (the portal) containers.

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
pip install researcher-ai==2.0.0
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
python -m pytest researcher_ai_portal_app/tests -q
```

Three tests (`test_dataset_step_key_resources`, `test_phase8_pdf_staging`, `test_phase9_rag_isolation`) require `researcher_ai` models to be importable and are expected to be skipped in environments where only the portal is installed without a full researcher-ai checkout.

---

## Option 3: Production deployment

This section covers a production setup behind a reverse proxy (nginx / Caddy / load balancer).

### Server process

The server command is in `scripts/start_web.sh`. It uses Gunicorn as a process manager with Uvicorn workers — this gives you multi-worker process supervision (Gunicorn) while handling async ASGI correctly (Uvicorn):

```bash
gunicorn researcher_ai_portal.asgi:application \
    --worker-class uvicorn.workers.UvicornWorker \
    --workers 2 \
    --bind 0.0.0.0:8000 \
    --timeout 120 \
    --access-logfile - \
    --error-logfile -
```

**Worker count:** Start with `2 × CPU cores`. Each worker handles async I/O natively, so you don't need as many workers as you would with sync Django.

**Timeout:** LLM parsing steps can run for 60–120 seconds. The 120-second timeout allows a single step to complete before the worker is killed.

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

    # Pass everything to the Gunicorn/Uvicorn process
    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host              $host;
        proxy_set_header X-Real-IP         $remote_addr;
        proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 150s;   # must exceed gunicorn --timeout
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

In Docker, `scripts/start_web.sh` already runs `migrate` and `collectstatic` at container startup before starting Gunicorn.

### Static files

WhiteNoise serves static files directly from the application process — no separate nginx `location /static/` block is needed. Static files are collected to `researcher_ai_portal/staticfiles/` at build time.

### Healthcheck

The `/healthz/` endpoint returns `200 OK` with the text `ok`. Use this for load balancer and container health checks.

---

## Upgrading researcher-ai

For Docker deployments, re-run `./run_portal.sh` after updating the source. The script rebuilds the wheel and reinstalls it in the container.

For native deployments:

```bash
pip install --upgrade -e /path/to/researcher-ai
# or
pip install researcher-ai==<new-version>

python manage.py migrate   # apply any new migrations
```

---

## Troubleshooting

**`ModuleNotFoundError: No module named 'researcher_ai'`**
The `researcher-ai` package is not installed. Run `pip install -e /path/to/researcher-ai` or `pip install researcher-ai==2.0.0`.

**`DisallowedHost` error**
Add your hostname to `DJANGO_ALLOWED_HOSTS` in `.env`.

**Globus login redirects to wrong URL**
Set `SOCIAL_AUTH_GLOBUS_REDIRECT_URI` to the full callback URL including protocol: `https://yourdomain.com/complete/globus/`.

**Parse steps time out in Docker**
Increase `--timeout` in `scripts/start_web.sh` and the `proxy_read_timeout` in nginx. LLM-heavy steps (figures, method) can take 90–120 seconds.

**FastAPI docs not loading at `/api/v1/docs`**
Confirm the server is started with `researcher_ai_portal.asgi:application`, not the legacy `wsgi:application`. The WSGI entry point does not include FastAPI.
