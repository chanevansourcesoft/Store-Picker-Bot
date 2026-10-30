#!/usr/bin/env bash
# Start train_groot.sh in the background, so the training keeps running after the terminal or the
# SSH connection closes. While it trains, older checkpoints are thinned out every minute
# (prune_checkpoints.py) so the run fits on the disk. Used by the Store Picker Bot app (Train step);
# works from a terminal too.
#
# Usage:
#   ./start_training.sh <dataset folder>
#   GPU=1 STEPS=50000 CONDA_ENV=lerobot_env ./start_training.sh dataset_v1_21102026_183539
#   FROM_MODEL=<run>_060000 STEPS=20000 CONDA_ENV=lerobot_env ./start_training.sh <dataset folder>
#   CONDA_ENV=lerobot_env ./start_training.sh --resume <run name>
# Settings: the same variables as train_groot.sh (GPU, STEPS, BATCH_SIZE, SAVE_FREQ, MEMORY_MODE, FROM_MODEL ...),
# plus CONDA_ENV (conda env to activate; default: none, use the environment train_groot.sh finds),
# KEEP_LAST (newest checkpoints kept complete, default 2) and KEEP_MODEL_EVERY (older checkpoints at a
# multiple of this many steps keep their model, default 10000).
#
# --resume continues a stopped or failed run (started with this script) from its newest complete
# checkpoint, with resume_groot.sh: same steps, settings and learning-rate schedule. Its GPU and
# thinning settings come from the run's logs/<run name>.json; the log continues in the same file.
#
# Prints the run name. Its log, process id and settings are in logs/<run name>.{log,pid,json};
# the run itself is in outputs/<run name>/. Follow it with:  tail -f logs/<run name>.log
# Stop it with:  ./stop_training.sh <run name>
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOG_DIR="$SCRIPT_DIR/logs"
if [[ $# -eq 2 && "$1" == "--resume" ]]; then
  MODE=resume
  RUN_NAME="$(basename "$2")"
  META="$LOG_DIR/$RUN_NAME.json"
  OUTPUT_DIR="$SCRIPT_DIR/outputs/$RUN_NAME"
  if [[ ! -f "$META" ]]; then
    echo "No run $RUN_NAME started with start_training.sh (no logs/$RUN_NAME.json)" >&2
    exit 1
  fi
  if [[ -f "$LOG_DIR/$RUN_NAME.pid" ]] && kill -0 "$(cat "$LOG_DIR/$RUN_NAME.pid")" 2>/dev/null; then
    echo "$RUN_NAME is still running." >&2
    exit 1
  fi
  LAST="$OUTPUT_DIR/checkpoints/last"
  if [[ ! -d "$LAST/training_state" ]]; then
    echo "$RUN_NAME has no checkpoint to resume from (checkpoints/last has no training_state)." >&2
    exit 1
  fi
  RESUME_STEP=$((10#$(basename "$(readlink -f "$LAST")")))
  # GPU and thinning settings of the run itself.
  eval "$(python3 - "$META" <<'EOF'
import json, shlex, sys
m = json.load(open(sys.argv[1]))
for key, var in (("gpu", "GPU"), ("keep_last", "KEEP_LAST"), ("keep_model_every", "KEEP_MODEL_EVERY")):
    print(f"{var}={shlex.quote(str(m[key]))}")
print(f"export SPLIT_BACKBONE={shlex.quote(str(m.get('split_backbone') or ''))}")
EOF
)"
elif [[ $# -eq 1 && "$1" != -* ]]; then
  MODE=train
  DATASET_NAME="$(basename "$1")"
  if [[ ! -f "$SCRIPT_DIR/../data_collection/dataset/$DATASET_NAME/meta/info.json" ]]; then
    echo "Not a dataset: policy/data_collection/dataset/$DATASET_NAME" >&2
    exit 1
  fi
  RUN_NAME="$(date +%d%m%Y_%H%M%S)_${DATASET_NAME}"
  OUTPUT_DIR="$SCRIPT_DIR/outputs/$RUN_NAME"
else
  echo "Usage: $0 <dataset folder>   or   $0 --resume <run name>" >&2
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
mkdir -p "$LOG_DIR"
export OUTPUT_DIR GPU="${GPU:-1}"

if [[ "$MODE" == train ]]; then
  # The settings of this run, for the app and for later reference.
  python3 - "$LOG_DIR/$RUN_NAME.json" <<EOF
import json, sys, time
json.dump({"run": "$RUN_NAME", "dataset": "$DATASET_NAME", "started": time.time(),
           "gpu": "$GPU", "steps": int("${STEPS:-30000}"), "batch_size": int("${BATCH_SIZE:-64}"),
           "save_freq": int("${SAVE_FREQ:-5000}"), "memory_mode": "${MEMORY_MODE:-low}",
           "keep_last": $KEEP_LAST, "keep_model_every": $KEEP_MODEL_EVERY, "log_freq": 10,
           "split_backbone": "${SPLIT_BACKBONE:-}",
           "from_model": "${FROM_MODEL:-}" or None},
          open(sys.argv[1], "w"), indent=1)
EOF
  TRAIN=("$SCRIPT_DIR/train_groot.sh" "$DATASET_NAME")
  : > "$LOG_DIR/$RUN_NAME.log"
else
  # When it was resumed, from which step; the log goes on in the same file after a marker that
  # training_status.py uses to count the steps again from there.
  python3 - "$LOG_DIR/$RUN_NAME.json" "$RESUME_STEP" <<'EOF'
import json, sys, time
path, step = sys.argv[1], int(sys.argv[2])
m = json.load(open(path))
m.setdefault("resumed", []).append({"time": time.time(), "step": step})
json.dump(m, open(path, "w"), indent=1)
EOF
  rm -f "$LOG_DIR/$RUN_NAME.stopped" "$LOG_DIR/$RUN_NAME.exit"
  # On a new line: a crash can leave the log in the middle of a progress bar.
  printf '\n[resume] step %s\n' "$RESUME_STEP" >> "$LOG_DIR/$RUN_NAME.log"
  TRAIN=("$SCRIPT_DIR/resume_groot.sh" "$RUN_NAME")
fi

# setsid: own session, so the run survives the SSH connection and can be stopped as one group.
setsid nohup bash -c '
  dir=$1 out=$2 keep_last=$3 keep_every=$4 base=$5; shift 5
  "$@" &
  train=$!
  while kill -0 "$train" 2>/dev/null; do
    for _ in $(seq 60); do kill -0 "$train" 2>/dev/null || break; sleep 1; done
    python3 "$dir/prune_checkpoints.py" "$out" --keep_last "$keep_last" --keep_model_every "$keep_every" || true
  done
  code=0
  wait "$train" || code=$?
  python3 "$dir/prune_checkpoints.py" "$out" --keep_last "$keep_last" --keep_model_every "$keep_every" || true
  echo "$code" > "$base.exit"
' _ "$SCRIPT_DIR" "$OUTPUT_DIR" "$KEEP_LAST" "$KEEP_MODEL_EVERY" "$LOG_DIR/$RUN_NAME" "${TRAIN[@]}" \
  >> "$LOG_DIR/$RUN_NAME.log" 2>&1 < /dev/null &
echo $! > "$LOG_DIR/$RUN_NAME.pid"
echo "$RUN_NAME"
