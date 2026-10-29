#!/usr/bin/env bash
# Run the full StorePilot application on a laptop / PC (no robot needed).
#
#   ./run_local.sh setup    one time: venvs, npm packages, frontend build, .env, instructions.json
#   ./run_local.sh          start everything; Ctrl+C stops everything
#   ./run_local.sh dev      same, but the dashboard runs with Vite hot reload on :5173
#   ./run_local.sh test     run every test suite
#
# What starts:
#   mock robot server      http://127.0.0.1:8000   (same HTTP API as the Jetson robot server)
#   StorePilot dashboard   http://localhost:8080   (built-in simulator, labelled SIMULATED)
#   QuickPick kiosk+owner  http://localhost:8501   (drives the mock robot over HTTP)
# Logs: storepilot/logs/*.log
set -eu
ROOT="$(cd "$(dirname "$0")" && pwd)"      # storepilot/
REPO="$(dirname "$ROOT")"
export PATH="$HOME/.local/opt/node/bin:$PATH"
BACK_PY="$ROOT/backend/.venv/bin/python"
QP_PY="$ROOT/quickpick/venv/bin/python"
LOGS="$ROOT/logs"

setup() {
    command -v python3 >/dev/null || { echo "Python 3.10+ is needed"; exit 1; }
    command -v npm >/dev/null || { echo "Node.js 20+ is needed (npm not found)"; exit 1; }
    [ -f "$ROOT/.env" ] || { cp "$ROOT/.env.example" "$ROOT/.env"; echo "created storepilot/.env"; }
    if [ ! -f "$REPO/policy/instructions.json" ]; then
        # Item ids must match agent/store_agent/catalog.json; "shelf" here is the catalog's "level"
        cat > "$REPO/policy/instructions.json" <<'EOF'
{
  "ink_box": {"shelf": "upper_shelf", "instruction": "pick up the ink box and place it in the basket"},
  "biscuit": {"shelf": "lower_shelf", "instruction": "pick up the biscuit packet and place it in the basket"},
  "fevicol": {"shelf": "upper_shelf", "instruction": "pick up the Fevicol bottle and place it in the basket"},
  "screen_cleaning_gel": {"shelf": "upper_shelf", "instruction": "pick up the screen cleaning gel bottle and place it in the basket"},
  "frooti": {"shelf": "lower_shelf", "instruction": "pick up the Frooti mango drink box and place it in the basket"}
}
EOF
        echo "created policy/instructions.json (local, not in Git)"
    fi
    echo "== StorePilot backend venv"
    [ -x "$BACK_PY" ] || python3 -m venv "$ROOT/backend/.venv"
    "$BACK_PY" -m pip install -q -r "$ROOT/backend/requirements.txt" -e "$REPO/agent"
    echo "== QuickPick venv"
    [ -x "$QP_PY" ] || python3 -m venv "$ROOT/quickpick/venv"
    (cd "$ROOT/quickpick" && "$QP_PY" -m pip install -q -r requirements.txt)
    echo "== Frontend"
    (cd "$ROOT/frontend" && npm ci --no-audit --no-fund && npm run build)
    echo "Setup done. Start with: ./run_local.sh"
}

run_tests() {
    (cd "$ROOT/backend" && "$BACK_PY" -m tests.test_backend)
    (cd "$ROOT/frontend" && npm run typecheck)
    for t in test_store test_worker test_step_log test_metrics test_fake_rosbridge; do
        (cd "$ROOT" && "$QP_PY" -m "quickpick.$t")
    done
    for t in store_agent.test_inventory store_agent.test_robot_client store_agent.test_agent \
             store_agent.test_order_tracker store_agent.test_autopilot robot_server.test_stations \
             robot_server.test_mock_robot robot_server.test_nav2_client robot_server.test_jetson_pick; do
        (cd "$REPO/agent" && "$QP_PY" -m "$t")
    done
    echo "All test suites passed."
}

start() {
    [ -x "$BACK_PY" ] && [ -x "$QP_PY" ] && [ -f "$REPO/policy/instructions.json" ] \
        || { echo "Run ./run_local.sh setup first"; exit 1; }
    for port in 8000 8080 8501; do
        if (exec 3<>"/dev/tcp/127.0.0.1/$port") 2>/dev/null; then
            echo "Port $port is already in use. Stop what runs there first."; exit 1
        fi
    done
    mkdir -p "$LOGS"
    PIDS=()
    trap 'echo; echo "Stopping..."; kill "${PIDS[@]}" 2>/dev/null; wait 2>/dev/null' EXIT INT TERM

    (cd "$REPO/agent" && exec "$QP_PY" -m robot_server.mock_robot) > "$LOGS/mock_robot.log" 2>&1 &
    PIDS+=($!)
    if [ "${1:-}" = dev ]; then
        (cd "$ROOT/backend" && exec "$BACK_PY" -m uvicorn storepilot.main:app --reload \
            --host 127.0.0.1 --port 8080) > "$LOGS/storepilot.log" 2>&1 &
        PIDS+=($!)
        (cd "$ROOT/frontend" && STOREPILOT_BACKEND=http://127.0.0.1:8080 exec npx vite --host 127.0.0.1) \
            > "$LOGS/vite.log" 2>&1 &
        PIDS+=($!)
        DASH=http://localhost:5173
    else
        [ -f "$ROOT/frontend/dist/index.html" ] || (cd "$ROOT/frontend" && npm run build)
        (cd "$ROOT/backend" && STOREPILOT_PORT=8080 exec "$BACK_PY" -m storepilot) > "$LOGS/storepilot.log" 2>&1 &
        PIDS+=($!)
        DASH=http://localhost:8080
    fi
    (cd "$ROOT/quickpick" && ROBOT_URL=http://127.0.0.1:8000 exec "$QP_PY" -m streamlit run Home.py \
        --server.headless true --server.port 8501) > "$LOGS/quickpick.log" 2>&1 &
    PIDS+=($!)

    # QuickPick checks the robot once at start, so it starts after the mock robot answers
    for _ in $(seq 1 40); do
        if curl -sf http://127.0.0.1:8000/health >/dev/null && curl -sf http://127.0.0.1:8080/api/health >/dev/null \
            && curl -sf http://127.0.0.1:8501/_stcore/health >/dev/null; then
            break
        fi
        sleep 0.5
    done
    if grep -qE '^NEBIUS_API_KEY=.+' "$ROOT/.env" 2>/dev/null; then AI="Nemotron (Nebius Token Factory)"
    else AI="offline keyword parser + autopilot (set NEBIUS_API_KEY in storepilot/.env for Nemotron)"; fi
    cat <<EOF

  StorePilot dashboard   $DASH
  QuickPick kiosk        http://localhost:8501
  Mock robot server      http://127.0.0.1:8000/health
  AI planner             $AI
  Logs                   $LOGS/

  Press Ctrl+C to stop everything.
EOF
    wait
}

case "${1:-}" in
    setup) setup ;;
    test) run_tests ;;
    dev) start dev ;;
    "") start ;;
    *) echo "usage: $0 [setup|dev|test]"; exit 1 ;;
esac
