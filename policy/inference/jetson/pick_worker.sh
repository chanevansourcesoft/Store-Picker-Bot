#!/usr/bin/env bash
# Start the policy worker on the Jetson, in the policy container (compose.yaml): GR00T stays loaded
# and the robot server asks it for one pick at a time (pick_worker.py). The container uses the
# host network, so the robot server reaches it at http://127.0.0.1:8790.
# The model must be in policy/inference/models/<name>/.
#
# Usage, from any folder:
#   <repo>/policy/inference/jetson/pick_worker.sh --model <name>
# Stop it with Ctrl+C (the arm goes back to rest, then its motors switch off).
# All options: --help   Guide: policy/docs/INFERENCE.md
set -eo pipefail

POLICY_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$POLICY_DIR"
exec docker compose run --rm policy python3 inference/pick_worker.py "$@"
