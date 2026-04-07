#!/usr/bin/env sh
set -eu

ROOT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
cd "$ROOT_DIR"

RESEARCHER_AI_SRC="${RESEARCHER_AI_SRC:-/Users/brianyee/Documents/work/01_active/researcher-ai/researcher-ai}"
VENDOR_DIR="$ROOT_DIR/.vendor/researcher-ai"
WHEEL_DIR="$ROOT_DIR/.vendor/wheels"
FORCE_BUILD="${FORCE_BUILD:-0}"

if [ ! -d "$RESEARCHER_AI_SRC" ]; then
  echo "ERROR: researcher-ai source not found at: $RESEARCHER_AI_SRC" >&2
  echo "Set RESEARCHER_AI_SRC to your local researcher-ai package path." >&2
  exit 1
fi

mkdir -p "$ROOT_DIR/.vendor"
mkdir -p "$VENDOR_DIR"
mkdir -p "$WHEEL_DIR"
rsync -a --delete --exclude='.git' --exclude='__pycache__' --exclude='.pytest_cache' "$RESEARCHER_AI_SRC/" "$VENDOR_DIR/"

# Build an explicit local wheel for faster and deterministic image installs.
rm -f "$WHEEL_DIR"/researcher_ai-*.whl
python3 -m pip wheel --no-deps --wheel-dir "$WHEEL_DIR" "$VENDOR_DIR"
WHEEL_FILE="$(ls -1 "$WHEEL_DIR"/researcher_ai-*.whl | head -n 1)"
WHEEL_BASENAME="$(basename "$WHEEL_FILE")"

export RESEARCHER_AI_PIP_SPEC="/app/.vendor/wheels/$WHEEL_BASENAME"

echo "Launching portal stack (postgres + web; serial parser mode)..."
if [ "$FORCE_BUILD" = "1" ]; then
  docker compose up --build -d db web
else
  docker compose up -d db web
fi

echo
echo "Portal is deploying."
echo "Web URL: http://127.0.0.1:8000"
echo "Tail logs: docker compose logs -f web db"
echo "Force image rebuild: FORCE_BUILD=1 ./run_portal.sh"
echo "researcher-ai wheel: $WHEEL_BASENAME"
