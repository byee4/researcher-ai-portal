#!/usr/bin/env sh
set -eu

ROOT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
cd "$ROOT_DIR"

PYTHON_BIN="${PYTHON_BIN:-python3}"
RESEARCHER_AI_SRC="${RESEARCHER_AI_SRC:-}"
RESEARCHER_AI_PIP_SPEC="${RESEARCHER_AI_PIP_SPEC:-git+https://github.com/byee4/researcher-ai.git@v3.0.0}"

if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
  echo "ERROR: Python binary '$PYTHON_BIN' not found." >&2
  exit 1
fi

echo "Installing portal dependencies..."
"$PYTHON_BIN" -m pip install -r "$ROOT_DIR/requirements.txt"

if [ -n "$RESEARCHER_AI_SRC" ]; then
  if [ ! -d "$RESEARCHER_AI_SRC" ]; then
    echo "ERROR: researcher-ai source not found at: $RESEARCHER_AI_SRC" >&2
    exit 1
  fi
  echo "Installing local researcher-ai package from $RESEARCHER_AI_SRC..."
  "$PYTHON_BIN" -m pip install -e "$RESEARCHER_AI_SRC"
else
  echo "Installing researcher-ai package from $RESEARCHER_AI_PIP_SPEC..."
  "$PYTHON_BIN" -m pip install --upgrade "$RESEARCHER_AI_PIP_SPEC"
fi

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
