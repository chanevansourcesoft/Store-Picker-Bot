# Environment for running the policy tools without Docker (a PC, e.g. Ubuntu 24.04).
# It does what compose.yaml does for the Jetson container:
#   - uses a Python environment with LeRobot: an already active one (e.g. `conda activate lerobot_env`),
#     otherwise policy/.venv (made with `uv sync`, see README.md),
#   - loads the repo root .env (arm ports, cameras, Hugging Face token),
#   - keeps the arm calibration in policy/calibration/, shared through Git,
#   - changes to policy/, where all commands run.
#
# Use it once per terminal, from anywhere:
#   source policy/env.sh

POLICY_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if (python -c "import lerobot" 2>/dev/null) && [ -n "${CONDA_PREFIX:-}${VIRTUAL_ENV:-}" ]; then
  : # an active conda env or venv already has LeRobot: use it
elif [ -x "$POLICY_DIR/.venv/bin/python" ]; then
  . "$POLICY_DIR/.venv/bin/activate"
else
  echo "No Python environment with LeRobot. Either activate one first (e.g. 'conda activate lerobot_env')," >&2
  echo "or run 'uv sync --frozen --managed-python' in $POLICY_DIR (see README.md)." >&2
  return 1 2>/dev/null || exit 1
fi

if [ -f "$POLICY_DIR/../.env" ]; then
  set -a
  . "$POLICY_DIR/../.env"
  set +a
fi

export HF_LEROBOT_CALIBRATION="$POLICY_DIR/calibration"
cd "$POLICY_DIR" || return 1
echo "Policy environment ready: $(python -c 'import sys, lerobot; print(sys.prefix, "| LeRobot", lerobot.__version__)')"
if ! python -c "import scservo_sdk" 2>/dev/null; then
  echo "Note: feetech-servo-sdk is missing, so the arms cannot be used: pip install feetech-servo-sdk" >&2
fi
