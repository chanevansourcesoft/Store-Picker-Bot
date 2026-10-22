#!/usr/bin/env bash
# Run a trained GR00T model on the arm on the Jetson, in the policy container (compose.yaml).
# The container only sees the policy/ folder, so the model must be inside it: put it in
# policy/inference/models/<name>/ and pass --model <name>.
#
# Usage, from any folder:
#   <repo>/policy/inference/jetson/run.sh --model <name> --product mentos --dry_run
# All options: --help   Guide: policy/docs/INFERENCE.md
set -eo pipefail

POLICY_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$POLICY_DIR"
exec docker compose run --rm policy python3 inference/run_policy.py "$@"
