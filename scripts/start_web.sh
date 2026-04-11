#!/usr/bin/env sh
set -eu

python manage.py migrate --noinput
python manage.py collectstatic --noinput || true
# Harmonize Docker and non-Docker dev startup behavior.
exec uvicorn researcher_ai_portal.asgi:application --reload --host 0.0.0.0 --port 8000
