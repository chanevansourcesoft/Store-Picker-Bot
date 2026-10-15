#!/usr/bin/env bash
# Development mode (no kiosk): backend with auto-reload on :8081 + Vite dev server on :5173.
# Open http://localhost:5173 in any browser. The installed service on :8080 can keep running.
set -eu
ROOT="$(cd "$(dirname "$0")" && pwd)"
export PATH="$HOME/.local/opt/node/bin:$PATH"
(cd "$ROOT/backend" && exec .venv/bin/python -m uvicorn storepilot.main:app --reload --host 127.0.0.1 --port 8081) &
BACK=$!
trap 'kill $BACK 2>/dev/null' EXIT
cd "$ROOT/frontend" && STOREPILOT_BACKEND=http://127.0.0.1:8081 npx vite
