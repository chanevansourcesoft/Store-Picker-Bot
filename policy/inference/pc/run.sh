#!/usr/bin/env bash
# Run a trained GR00T model on the arm from a PC, without Docker (for testing).
# Activate the Python environment first (e.g. `conda activate lerobot_env`); this script then
# loads the repo root .env and the arm calibration with env.sh.
#
# Usage, from any folder:
#   <repo>/policy/inference/pc/run.sh --model <name in policy/inference/models/ or a path> --product mentos --dry_run
# A relative --model path is relative to the folder you are in.
# All options: --help   Guide: policy/docs/INFERENCE.md
set -eo pipefail

POLICY_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
CALLER_DIR="$PWD"
source "$POLICY_DIR/env.sh" >/dev/null
cd "$CALLER_DIR"   # env.sh changes to policy/; come back so relative paths mean what was typed
# Lets the GPU memory used by the model be given back between allocations (16 GB GPUs).
export PYTORCH_CUDA_ALLOC_CONF="${PYTORCH_CUDA_ALLOC_CONF:-expandable_segments:True}"
exec python "$POLICY_DIR/inference/run_policy.py" "$@"
