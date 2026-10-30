#!/usr/bin/env bash
# Fine-tune GR00T N1.7 with LeRobot on the local training PC (2x RTX 5090, 32 GB each).
#
# Based on train_groot.sh from the SO-101 sim project. Run from anywhere, with the
# environment from setup.sh (see policy/docs/TRAINING.md).
#
# Usage:
#   ./train_groot.sh <dataset folder>             # a folder in policy/data_collection/dataset/
#   NUM_GPUS=1 STEPS=10000 ./train_groot.sh dataset_v1_12102026_153045
#   FROM_MODEL=<run>_060000 STEPS=20000 ./train_groot.sh <dataset folder>   # go on from a trained model
# Settings come from env vars; extra args are passed to lerobot-train.
#
# FROM_MODEL starts from one of our trained models instead of NVIDIA's base model, with a new
# learning-rate schedule (a new run, not a resume): for example to train an existing model further
# on a dataset with more episodes. It is a run in training/outputs/ (its newest checkpoint),
# <run>_<step> (e.g. <run>_060000), or a path to a checkpoint or pretrained_model folder. The model's
# own settings are used (memory mode, chunk, relative actions; MEMORY_MODE is ignored), and so is its
# action scaling (from the dataset it was trained on). Train on the whole dataset (old and new
# episodes), not on the new episodes alone, or the model forgets what it knew.
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
# process 2 x BATCH_SIZE samples per step (no extra memory per GPU).
# Default: one GPU x 64, the same as the sim project's run. On this PC, GPU 1 has a
# PCIe 4.0 x4 link, and syncing the GPUs after each step made 2 x 32 slower than
# 1 x 64 (44 vs 66 samples/s, measured 21.10.2026).
NUM_GPUS="${NUM_GPUS:-1}"
if [[ "$NUM_GPUS" -gt 1 ]]; then
  GPU="${GPU:-$(seq -s, 0 $((NUM_GPUS - 1)))}"
else
  GPU="${GPU:-1}"   # GPU 0 also drives the display
fi
BATCH_SIZE="${BATCH_SIZE:-64}"   # ~25 GB of 32 GB in low mode
STEPS="${STEPS:-30000}"
SAVE_FREQ="${SAVE_FREQ:-5000}"
# Video frames are decoded on the CPU, so the GPU needs enough loader workers.
NUM_WORKERS="${NUM_WORKERS:-16}"
# How much of the model fits in GPU memory:
#   full : NVIDIA's recipe, fp32 weights, diffusion model trained. Needs ~40 GB or more per GPU.
#   bf16 : bf16 weights, diffusion model trained. Runs out of memory on one 32 GB GPU (tested at batch 64 and
#          32). With two: GPU=1,0 SPLIT_BACKBONE=1 BATCH_SIZE=32 (the backbone runs on the second GPU).
#   low  : bf16 weights, diffusion model frozen. Same as the sim project's training
#          (batch 64 on one RTX 5090). Trains less of the model than bf16.
MEMORY_MODE="${MEMORY_MODE:-low}"

# HF_TOKEN for the gated nvidia/Cosmos-Reason2-2B backbone, from the repo root .env.
if [[ -f "$SCRIPT_DIR/../../.env" ]]; then
  set -a; source "$SCRIPT_DIR/../../.env"; set +a
fi

# FROM_MODEL: the pretrained_model folder of the model to start from.
FROM_DIR=""
if [[ -n "${FROM_MODEL:-}" ]]; then
  if [[ -e "$FROM_MODEL" ]]; then
    FROM_DIR="$FROM_MODEL"
  elif [[ -d "$SCRIPT_DIR/outputs/$FROM_MODEL" ]]; then
    FROM_DIR="$SCRIPT_DIR/outputs/$FROM_MODEL/checkpoints/last"
  elif [[ "$FROM_MODEL" =~ ^(.+)_([0-9]{6})$ ]]; then
    FROM_DIR="$SCRIPT_DIR/outputs/${BASH_REMATCH[1]}/checkpoints/${BASH_REMATCH[2]}"
  fi
  [[ -d "$FROM_DIR/pretrained_model" ]] && FROM_DIR="$FROM_DIR/pretrained_model"
  if [[ ! -f "$FROM_DIR/model.safetensors" || ! -f "$FROM_DIR/config.json" ]]; then
    echo "FROM_MODEL: no model found for '$FROM_MODEL' (a run in $SCRIPT_DIR/outputs, <run>_<step>, or a path)" >&2
    exit 1
  fi
  FROM_DIR="$(readlink -f "$FROM_DIR")"
fi

case "$MEMORY_MODE" in
  full) MEMORY_ARGS=() ;;
  # LeRobot >= 0.6.2 ignores model_params_fp32; dtype is the flag that works.
  bf16) MEMORY_ARGS=(--policy.dtype=bfloat16) ;;
  low)  MEMORY_ARGS=(--policy.dtype=bfloat16 --policy.tune_diffusion_model=false) ;;
  *) echo "MEMORY_MODE must be full, bf16 or low (got '$MEMORY_MODE')" >&2; exit 1 ;;
esac

RUN_NAME="$(date +%d%m%Y_%H%M%S)_${DATASET_NAME}"
OUTPUT_DIR="${OUTPUT_DIR:-$SCRIPT_DIR/outputs/$RUN_NAME}"

export CUDA_VISIBLE_DEVICES="$GPU"
export PYTORCH_CUDA_ALLOC_CONF="${PYTORCH_CUDA_ALLOC_CONF:-expandable_segments:True}"

# lerobot_train_groot.py: lerobot-train, with GR00T's image augmentation also for FROM_MODEL and resumes.
PYTHON="$(dirname "$(command -v lerobot-train)")/python"
LAUNCHER=("$PYTHON" "$SCRIPT_DIR/lerobot_train_groot.py")
if [[ "$NUM_GPUS" -gt 1 ]]; then
  LAUNCHER=(accelerate launch --multi_gpu --num_processes="$NUM_GPUS" "$SCRIPT_DIR/lerobot_train_groot.py")
fi

echo "Dataset: $DATASET_ROOT"
echo "Output:  $OUTPUT_DIR"
if [[ -n "$FROM_DIR" ]]; then
  echo "GPUs: $GPU, batch $BATCH_SIZE per GPU, $STEPS steps, from $FROM_DIR (its own settings)"
  # Weights, settings and action scaling from that model; a new learning-rate schedule.
  POLICY_ARGS=(--policy.path="$FROM_DIR" --policy.device=cuda --policy.push_to_hub=false)
else
  echo "GPUs: $GPU, batch $BATCH_SIZE per GPU, $STEPS steps, memory mode $MEMORY_MODE"
  # chunk 16 and relative actions with an absolute gripper: same as the sim project.
  # The joint names (shoulder_pan.pos ... gripper.pos) are written by LeRobot when recording.
  POLICY_ARGS=(
    --policy.type=groot
    --policy.device=cuda
    --policy.base_model_path=nvidia/GR00T-N1.7-3B
    --policy.embodiment_tag=new_embodiment
    --policy.chunk_size=16
    --policy.n_action_steps=16
    --policy.use_relative_actions=true
    --policy.relative_exclude_joints='["gripper"]'
    --policy.use_bf16=true
    "${MEMORY_ARGS[@]}"
    --policy.push_to_hub=false
  )
fi

"${LAUNCHER[@]}" \
  --dataset.repo_id="local/$DATASET_NAME" \
  --dataset.root="$DATASET_ROOT" \
  --dataset.image_transforms.enable=true \
  "${POLICY_ARGS[@]}" \
  --batch_size="$BATCH_SIZE" \
  --steps="$STEPS" \
  --save_freq="$SAVE_FREQ" \
  --use_policy_training_preset=true \
  --env_eval_freq=0 \
  --eval_steps=0 \
  --log_freq=10 \
  --num_workers="$NUM_WORKERS" \
  --output_dir="$OUTPUT_DIR" \
  --job_name="$RUN_NAME" \
  --wandb.enable=false \
  "$@"
