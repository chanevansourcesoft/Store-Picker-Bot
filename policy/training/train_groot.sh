#!/usr/bin/env bash
# Fine-tune GR00T N1.7 with LeRobot on the local training PC (2x RTX 5090, 32 GB each).
#
# Based on train_groot.sh from the SO-101 sim project. Run from anywhere, with the
# environment from setup.sh (see policy/docs/TRAINING.md).
#
# Usage:
#   ./train_groot.sh <dataset folder>             # a folder in policy/data_collection/dataset/
#   NUM_GPUS=1 STEPS=10000 ./train_groot.sh 12102026_153045_store_v1
# Settings come from env vars; extra args are passed to lerobot-train.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DATASET_DIR="${DATASET_DIR:-$SCRIPT_DIR/../data_collection/dataset}"

if [[ $# -lt 1 ]]; then
  echo "Usage: $0 <dataset folder> [extra lerobot-train args]" >&2
  echo "Datasets in $DATASET_DIR:" >&2
  ls -1 "$DATASET_DIR" >&2 2>/dev/null || echo "  (none)" >&2
  exit 1
fi
DATASET_NAME="$(basename "$1")"
shift
DATASET_ROOT="$DATASET_DIR/$DATASET_NAME"
if [[ ! -f "$DATASET_ROOT/meta/info.json" ]]; then
  echo "Not a dataset: $DATASET_ROOT" >&2
  exit 1
fi
# Use the environment made by setup.sh (policy/.venv), no activation needed.
VENV_BIN="$SCRIPT_DIR/../.venv/bin"
[[ -x "$VENV_BIN/lerobot-train" ]] && export PATH="$VENV_BIN:$PATH"
if ! command -v lerobot-train >/dev/null; then
  echo "lerobot-train not found. Run ./setup.sh first." >&2
  exit 1
fi

# Each GPU holds a full model copy and gets BATCH_SIZE samples, so 2 GPUs
# process 2 x BATCH_SIZE samples per step (faster, but no extra memory per GPU).
NUM_GPUS="${NUM_GPUS:-2}"
GPU="${GPU:-$(seq -s, 0 $((NUM_GPUS - 1)))}"
BATCH_SIZE="${BATCH_SIZE:-8}"
STEPS="${STEPS:-30000}"
SAVE_FREQ="${SAVE_FREQ:-5000}"
# How much of the model fits in GPU memory:
#   full : NVIDIA's recipe, fp32 weights, diffusion model trained. Needs ~40 GB or more per GPU.
#   bf16 : bf16 weights, diffusion model trained. Meant for 32 GB GPUs. Not tested yet.
#   low  : bf16 weights, diffusion model frozen. Fits 16 GB (tested in the sim project),
#          but the model learns less. Use it if bf16 runs out of memory.
MEMORY_MODE="${MEMORY_MODE:-bf16}"

case "$MEMORY_MODE" in
  full) MEMORY_ARGS=() ;;
  bf16) MEMORY_ARGS=(--policy.model_params_fp32=false) ;;
  low)  MEMORY_ARGS=(--policy.model_params_fp32=false --policy.tune_diffusion_model=false) ;;
  *) echo "MEMORY_MODE must be full, bf16 or low (got '$MEMORY_MODE')" >&2; exit 1 ;;
esac

RUN_NAME="$(date +%d%m%Y_%H%M%S)_${DATASET_NAME}"
OUTPUT_DIR="${OUTPUT_DIR:-$SCRIPT_DIR/outputs/$RUN_NAME}"

export CUDA_VISIBLE_DEVICES="$GPU"
export PYTORCH_CUDA_ALLOC_CONF="${PYTORCH_CUDA_ALLOC_CONF:-expandable_segments:True}"

LAUNCHER=(lerobot-train)
if [[ "$NUM_GPUS" -gt 1 ]]; then
  LAUNCHER=(accelerate launch --multi_gpu --num_processes="$NUM_GPUS" "$(command -v lerobot-train)")
fi

echo "Dataset: $DATASET_ROOT"
echo "Output:  $OUTPUT_DIR"
echo "GPUs: $GPU, batch $BATCH_SIZE per GPU, $STEPS steps, memory mode $MEMORY_MODE"

# chunk 16 and relative actions with an absolute gripper: same as the sim project.
# The joint names (shoulder_pan.pos ... gripper.pos) are written by LeRobot when recording.
"${LAUNCHER[@]}" \
  --dataset.repo_id="local/$DATASET_NAME" \
  --dataset.root="$DATASET_ROOT" \
  --dataset.image_transforms.enable=true \
  --policy.type=groot \
  --policy.device=cuda \
  --policy.base_model_path=nvidia/GR00T-N1.7-3B \
  --policy.embodiment_tag=new_embodiment \
  --policy.chunk_size=16 \
  --policy.n_action_steps=16 \
  --policy.use_relative_actions=true \
  --policy.relative_exclude_joints='["gripper"]' \
  --policy.use_bf16=true \
  "${MEMORY_ARGS[@]}" \
  --policy.push_to_hub=false \
  --batch_size="$BATCH_SIZE" \
  --steps="$STEPS" \
  --save_freq="$SAVE_FREQ" \
  --use_policy_training_preset=true \
  --env_eval_freq=0 \
  --eval_steps=0 \
  --log_freq=10 \
  --num_workers=4 \
  --output_dir="$OUTPUT_DIR" \
  --job_name="$RUN_NAME" \
  --wandb.enable=false \
  "$@"
