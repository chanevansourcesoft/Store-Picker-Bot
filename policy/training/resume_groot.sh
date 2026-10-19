#!/usr/bin/env bash
# Resume an interrupted GR00T run from its last checkpoint (keeps the step, optimizer
# and learning-rate schedule). Use the same GPU count as the original run.
#
# Usage:
#   ./resume_groot.sh <run folder>            # a folder in policy/training/outputs/
#   NUM_GPUS=1 ./resume_groot.sh 13102026_090000_12102026_153045_store_v1
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [[ $# -lt 1 ]]; then
  echo "Usage: $0 <run folder> [extra lerobot-train args]" >&2
  echo "Runs in $SCRIPT_DIR/outputs:" >&2
  ls -1 "$SCRIPT_DIR/outputs" >&2 2>/dev/null || echo "  (none)" >&2
  exit 1
fi
RUN_DIR="$SCRIPT_DIR/outputs/$(basename "$1")"
shift
CONFIG="$RUN_DIR/checkpoints/last/pretrained_model/train_config.json"
if [[ ! -f "$CONFIG" ]]; then
  echo "No checkpoint to resume from: $CONFIG" >&2
  exit 1
fi
# Use the environment made by setup.sh (policy/.venv), no activation needed.
VENV_BIN="$SCRIPT_DIR/../.venv/bin"
[[ -x "$VENV_BIN/lerobot-train" ]] && export PATH="$VENV_BIN:$PATH"
if ! command -v lerobot-train >/dev/null; then
  echo "lerobot-train not found. Run ./setup.sh first." >&2
  exit 1
fi

NUM_GPUS="${NUM_GPUS:-2}"
GPU="${GPU:-$(seq -s, 0 $((NUM_GPUS - 1)))}"
export CUDA_VISIBLE_DEVICES="$GPU"
export PYTORCH_CUDA_ALLOC_CONF="${PYTORCH_CUDA_ALLOC_CONF:-expandable_segments:True}"

LAUNCHER=(lerobot-train)
if [[ "$NUM_GPUS" -gt 1 ]]; then
  LAUNCHER=(accelerate launch --multi_gpu --num_processes="$NUM_GPUS" "$(command -v lerobot-train)")
fi

echo "Resuming $RUN_DIR on GPUs $GPU"
"${LAUNCHER[@]}" --config_path="$CONFIG" --resume=true "$@"
