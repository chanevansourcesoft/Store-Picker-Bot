#!/usr/bin/env bash
# Start the policy worker on a PC, without Docker (for testing): GR00T stays loaded and the robot
# server asks it for one pick at a time (pick_worker.py). Activate the Python environment first
# (e.g. `conda activate lerobot_env`); this script then loads the repo root .env and the arm
# calibration with env.sh.
#
# Usage, from any folder:
#   <repo>/policy/inference/pc/pick_worker.sh --model <name in policy/inference/models/ or a path>
# Stop it with Ctrl+C (the arm goes back to rest, then its motors switch off).
# All options: --help   Guide: policy/docs/INFERENCE.md
set -eo pipefail

POLICY_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
CALLER_DIR="$PWD"
source "$POLICY_DIR/env.sh" >/dev/null
cd "$CALLER_DIR"   # env.sh changes to policy/; come back so relative paths mean what was typed
export PYTORCH_CUDA_ALLOC_CONF="${PYTORCH_CUDA_ALLOC_CONF:-expandable_segments:True}"
exec python "$POLICY_DIR/inference/pick_worker.py" "$@"
