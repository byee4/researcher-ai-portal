#!/usr/bin/env sh
set -eu

python manage.py migrate --noinput
python manage.py collectstatic --noinput || true
# Use Gunicorn as the process manager with Uvicorn workers so the ASGI
# application (FastAPI + Django) is served correctly.  The UvicornWorker
# handles async I/O; Gunicorn handles process supervision and restarts.
exec gunicorn researcher_ai_portal.asgi:application \
    --worker-class uvicorn.workers.UvicornWorker \
    --bind 0.0.0.0:8000 \
    --workers 2 \
    --timeout 120 \
    --access-logfile - \
    --error-logfile -
