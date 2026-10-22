#!/usr/bin/env bash
# Start train_groot.sh in the background, so the training keeps running after the terminal or the
# SSH connection closes. While it trains, older checkpoints are thinned out every minute
# (prune_checkpoints.py) so the run fits on the disk. Used by the Store Picker Bot app (Train step);
# works from a terminal too.
#
# Usage:
#   ./start_training.sh <dataset folder>
#   GPU=1 STEPS=50000 CONDA_ENV=lerobot_env ./start_training.sh dataset_v1_21102026_183539
# Settings: the same variables as train_groot.sh (GPU, STEPS, BATCH_SIZE, SAVE_FREQ, MEMORY_MODE ...),
# plus CONDA_ENV (conda env to activate; default: none, use the environment train_groot.sh finds),
# KEEP_LAST (newest checkpoints kept complete, default 2) and KEEP_MODEL_EVERY (older checkpoints at a
# multiple of this many steps keep their model, default 10000).
#
# Prints the run name. Its log, process id and settings are in logs/<run name>.{log,pid,json};
# the run itself is in outputs/<run name>/. Follow it with:  tail -f logs/<run name>.log
# Stop it with:  ./stop_training.sh <run name>
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [[ $# -ne 1 ]]; then
  echo "Usage: $0 <dataset folder>" >&2
  exit 1
fi
DATASET_NAME="$(basename "$1")"
if [[ ! -f "$SCRIPT_DIR/../data_collection/dataset/$DATASET_NAME/meta/info.json" ]]; then
  echo "Not a dataset: policy/data_collection/dataset/$DATASET_NAME" >&2
  exit 1
fi

if [[ -n "${CONDA_ENV:-}" ]]; then
  for base in "$HOME/miniconda3" "$HOME/anaconda3" "$HOME/miniforge3" "$HOME/mambaforge"; do
    if [[ -f "$base/etc/profile.d/conda.sh" ]]; then
      source "$base/etc/profile.d/conda.sh"
      break
    fi
  done
  conda activate "$CONDA_ENV"
fi

KEEP_LAST="${KEEP_LAST:-2}"
KEEP_MODEL_EVERY="${KEEP_MODEL_EVERY:-10000}"
RUN_NAME="$(date +%d%m%Y_%H%M%S)_${DATASET_NAME}"
LOG_DIR="$SCRIPT_DIR/logs"
mkdir -p "$LOG_DIR"
export OUTPUT_DIR="$SCRIPT_DIR/outputs/$RUN_NAME"

# The settings of this run, for the app and for later reference.
python3 - "$LOG_DIR/$RUN_NAME.json" <<EOF
import json, sys, time
json.dump({"run": "$RUN_NAME", "dataset": "$DATASET_NAME", "started": time.time(),
           "gpu": "${GPU:-1}", "steps": int("${STEPS:-30000}"), "batch_size": int("${BATCH_SIZE:-64}"),
           "save_freq": int("${SAVE_FREQ:-5000}"), "memory_mode": "${MEMORY_MODE:-low}",
           "keep_last": $KEEP_LAST, "keep_model_every": $KEEP_MODEL_EVERY, "log_freq": 10},
          open(sys.argv[1], "w"), indent=1)
EOF

# setsid: own session, so the run survives the SSH connection and can be stopped as one group.
setsid nohup bash -c '
  "$1/train_groot.sh" "$2" &
  train=$!
  while kill -0 "$train" 2>/dev/null; do
    for _ in $(seq 60); do kill -0 "$train" 2>/dev/null || break; sleep 1; done
    python3 "$1/prune_checkpoints.py" "$3" --keep_last "$4" --keep_model_every "$5" || true
  done
  code=0
  wait "$train" || code=$?
  python3 "$1/prune_checkpoints.py" "$3" --keep_last "$4" --keep_model_every "$5" || true
  echo "$code" > "$6.exit"
' _ "$SCRIPT_DIR" "$DATASET_NAME" "$OUTPUT_DIR" "$KEEP_LAST" "$KEEP_MODEL_EVERY" "$LOG_DIR/$RUN_NAME" \
  > "$LOG_DIR/$RUN_NAME.log" 2>&1 < /dev/null &
echo $! > "$LOG_DIR/$RUN_NAME.pid"
echo "$RUN_NAME"
