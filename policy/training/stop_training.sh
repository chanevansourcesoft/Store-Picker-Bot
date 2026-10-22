#!/usr/bin/env bash
# Stop a training run started with start_training.sh. Its checkpoints stay; continue it later
# from the newest one with resume_groot.sh.
#
# Usage: ./stop_training.sh <run name>
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [[ $# -ne 1 ]]; then
  echo "Usage: $0 <run name>" >&2
  exit 1
fi
RUN="$(basename "$1")"
PID_FILE="$SCRIPT_DIR/logs/$RUN.pid"
if [[ ! -f "$PID_FILE" ]]; then
  echo "No run $RUN started with start_training.sh (no logs/$RUN.pid)" >&2
  exit 1
fi
PID="$(cat "$PID_FILE")"
if ! kill -0 "$PID" 2>/dev/null; then
  echo "$RUN is not running."
  exit 0
fi
touch "$SCRIPT_DIR/logs/$RUN.stopped"
# The whole process group: the training, its data loader workers and the checkpoint thinning.
kill -TERM -- "-$PID"
for _ in $(seq 30); do
  kill -0 "$PID" 2>/dev/null || { echo "Stopped $RUN."; exit 0; }
  sleep 1
done
kill -KILL -- "-$PID" 2>/dev/null || true
echo "Stopped $RUN (forced)."
