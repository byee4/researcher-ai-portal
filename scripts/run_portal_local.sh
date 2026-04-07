#!/usr/bin/env sh
set -eu

ROOT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
cd "$ROOT_DIR"

PYTHON_BIN="${PYTHON_BIN:-python3}"
HOST="${HOST:-0.0.0.0}"
PORT="${PORT:-8000}"

if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
  echo "ERROR: Python binary '$PYTHON_BIN' not found." >&2
  exit 1
fi

export DJANGO_ALLOWED_HOSTS="${DJANGO_ALLOWED_HOSTS:-localhost,127.0.0.1,0.0.0.0}"
export DJANGO_DEBUG="${DJANGO_DEBUG:-True}"

if [ -f "$ROOT_DIR/.env" ]; then
  # shellcheck source=/dev/null
  . "$ROOT_DIR/.env"
fi

echo "Running migrations..."
"$PYTHON_BIN" manage.py migrate --noinput

echo "Starting Django server on http://127.0.0.1:$PORT (serial parser mode)..."
exec "$PYTHON_BIN" manage.py runserver "$HOST:$PORT"
