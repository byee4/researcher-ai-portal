#!/usr/bin/env sh
set -eu

ROOT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
cd "$ROOT_DIR"

PYTHON_BIN="${PYTHON_BIN:-python3}"
RESEARCHER_AI_SRC="${RESEARCHER_AI_SRC:-/Users/brianyee/Documents/work/01_active/researcher-ai/researcher-ai}"

if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
  echo "ERROR: Python binary '$PYTHON_BIN' not found." >&2
  exit 1
fi

if [ ! -d "$RESEARCHER_AI_SRC" ]; then
  echo "ERROR: researcher-ai source not found at: $RESEARCHER_AI_SRC" >&2
  echo "Set RESEARCHER_AI_SRC to your local researcher-ai package path." >&2
  exit 1
fi

echo "Installing portal dependencies..."
"$PYTHON_BIN" -m pip install -r "$ROOT_DIR/requirements.txt"

echo "Installing local researcher-ai package..."
"$PYTHON_BIN" -m pip install -e "$RESEARCHER_AI_SRC"

if [ ! -f "$ROOT_DIR/.env" ]; then
  cat > "$ROOT_DIR/.env" <<EOF
DJANGO_DEBUG=True
DJANGO_ALLOWED_HOSTS=localhost,127.0.0.1,0.0.0.0
DJANGO_SECRET_KEY=change-me
EOF
  echo "Wrote $ROOT_DIR/.env with local defaults."
fi

echo "Running migrations..."
"$PYTHON_BIN" manage.py migrate --noinput

echo
echo "Setup complete."
echo "Next: ./scripts/run_portal_local.sh"
