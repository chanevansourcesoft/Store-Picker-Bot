#!/usr/bin/env bash
# Stop a training run started with start_training.sh. Its checkpoints stay; continue it later
# from the newest complete one with: ./start_training.sh --resume <run name>
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
# Who stopped it, from where: in the .stopped file (shown by the app) and in the run's log.
# STOP_SOURCE is set by the Store Picker Bot app; SSH_CLIENT by SSH; the callers are the parent processes.
callers="" p=$PPID
for _ in 1 2 3; do
  [[ "$p" =~ ^[0-9]+$ && "$p" -gt 1 ]] || break
  callers+="${callers:+ < }$(ps -o args= -p "$p" | cut -c1-80)"
  p="$(ps -o ppid= -p "$p" | tr -d ' ')"
done
by="$(date '+%F %T %Z') by $(id -un), ${STOP_SOURCE:-not the app}, ${SSH_CLIENT:+from ${SSH_CLIENT%% *} over SSH, }terminal $(tty -s && tty || echo none), called by: ${callers:-?}"
echo "$by" > "$SCRIPT_DIR/logs/$RUN.stopped"
echo "[stop] $by" >> "$SCRIPT_DIR/logs/$RUN.log"
# The whole process group: the training, its data loader workers and the checkpoint thinning.
kill -TERM -- "-$PID"
for _ in $(seq 30); do
  kill -0 "$PID" 2>/dev/null || { echo "Stopped $RUN."; exit 0; }
  sleep 1
done
kill -KILL -- "-$PID" 2>/dev/null || true
echo "Stopped $RUN (forced)."
