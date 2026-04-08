"""ASGI entry point — Django-outer, FastAPI-inner architecture.

Django is the top-level ASGI application so its full middleware stack
(WhiteNoise, sessions, CSRF, Globus auth, django-plotly-dash) is preserved
without modification.

FastAPI handles only requests whose path starts with /api/v1/.  Everything
else is delegated to Django.

Routing contract:
  /api/v1/*   → FastAPI  (visual builder API, Phase 1+ endpoints)
  /*          → Django   (home, auth, Dash apps, job management views)

Deployment:
  Development:
    uvicorn researcher_ai_portal.asgi:application --reload --host 0.0.0.0 --port 8000

  Production (Gunicorn process manager, Uvicorn workers):
    gunicorn researcher_ai_portal.asgi:application \
        -k uvicorn.workers.UvicornWorker \
        --workers 2 --bind 0.0.0.0:8000 --timeout 120
"""

import os
import warnings

# django-plotly-dash serves its component bundles as StreamingHttpResponse with
# a synchronous iterator.  Under Django 5.x ASGI this triggers a deprecation
# warning we cannot fix in third-party code, so we suppress it here at the
# ASGI entry point before the Django app is initialised.
warnings.filterwarnings(
    "ignore",
    message="StreamingHttpResponse must consume synchronous iterators",
    category=Warning,
)

# Django must be configured before any Django imports, including ORM models
# that FastAPI routes will query.  This ordering is mandatory.
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "researcher_ai_portal.settings")

import django  # noqa: E402
django.setup()

from django.core.asgi import get_asgi_application  # noqa: E402

from fastapi import FastAPI  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402

# Build the Django ASGI app after setup() so middleware is fully initialised.
_django_asgi = get_asgi_application()

# ---------------------------------------------------------------------------
# FastAPI sub-application
# ---------------------------------------------------------------------------

fastapi_app = FastAPI(
    title="Researcher AI Visual Builder",
    description=(
        "FastAPI endpoints for the visual pipeline builder.  "
        "Authenticated via Django session cookie."
    ),
    version="0.1.0",
    # Docs are only accessible from within the /api/v1 prefix.
    docs_url="/api/v1/docs",
    redoc_url="/api/v1/redoc",
    openapi_url="/api/v1/openapi.json",
)

# CORS: in development the React dev server (usually :3000 or :5173) needs
# cross-origin access.  In production, the frontend is served by Django/WhiteNoise
# on the same origin so CORS is not required — but it doesn't hurt.
fastapi_app.add_middleware(
    CORSMiddleware,
    allow_origins=os.environ.get(
        "FASTAPI_CORS_ORIGINS", "http://localhost:3000,http://localhost:5173"
    ).split(","),
    allow_credentials=True,  # required so the Django session cookie is sent
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "X-CSRFToken"],
)

# Import and register routers *after* django.setup() so that Django model
# imports inside the router modules succeed.
from researcher_ai_portal_app.api.routes import router as builder_router  # noqa: E402

fastapi_app.include_router(builder_router, prefix="/api/v1")


# ---------------------------------------------------------------------------
# Top-level ASGI router
# ---------------------------------------------------------------------------

_API_PREFIX = "/api/v1"


async def application(scope, receive, send):
    """Route /api/v1/* to FastAPI; everything else to Django."""
    if scope["type"] in ("http", "websocket") and scope.get("path", "").startswith(_API_PREFIX):
        await fastapi_app(scope, receive, send)
    else:
        await _django_asgi(scope, receive, send)
