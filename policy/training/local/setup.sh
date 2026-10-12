#!/usr/bin/env bash
# One-time setup of the local training PC (2x RTX 5090). Safe to run again: finished steps are skipped.
#
#   ./setup.sh                  # everything, including the GR00T model download (~12 GB)
#   ./setup.sh --no-download    # skip the model download (it then happens at the first training)
#
# What it does:
#   1. checks the NVIDIA driver (570 or newer for RTX 50 series)
#   2. installs a C compiler if missing (one package is built from source)
#   3. installs uv 0.12.23 if missing
#   4. installs the locked Python environment in policy/.venv (Python 3.12, torch 2.11 + CUDA 12.8)
#   5. checks that PyTorch sees the GPUs
#   6. logs in to Hugging Face and checks access to the gated GR00T backbone
#   7. downloads the GR00T base model and its backbone
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
POLICY_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
REPO_ROOT="$(cd "$POLICY_DIR/.." && pwd)"
UV_VERSION="0.12.23"   # same as the Jetson Dockerfile
MIN_DRIVER=570
BASE_MODEL="nvidia/GR00T-N1.7-3B"
BACKBONE="nvidia/Cosmos-Reason2-2B"

DOWNLOAD=1
for arg in "$@"; do
  case "$arg" in
    --no-download) DOWNLOAD=0 ;;
    -h|--help) sed -n '2,16p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "Unknown option: $arg" >&2; exit 1 ;;
  esac
done

step() { echo; echo "==> $*"; }
fail() { echo "ERROR: $*" >&2; exit 1; }

step "1/7 NVIDIA driver"
[[ "$(uname -s)" == "Linux" && "$(uname -m)" == "x86_64" ]] || fail "This setup is for x86_64 Linux."
command -v nvidia-smi >/dev/null || fail "nvidia-smi not found. Install the NVIDIA driver ($MIN_DRIVER or newer), reboot, and run this again."
DRIVER="$(nvidia-smi --query-gpu=driver_version --format=csv,noheader | head -1)"
[[ "${DRIVER%%.*}" -ge "$MIN_DRIVER" ]] || fail "NVIDIA driver $DRIVER is too old: RTX 50 series and CUDA 12.8 need $MIN_DRIVER or newer."
nvidia-smi --query-gpu=index,name,memory.total --format=csv,noheader | sed 's/^/    GPU /'
echo "    driver $DRIVER: OK"

step "2/7 C compiler"
if command -v gcc >/dev/null && command -v make >/dev/null; then
  echo "    already installed"
elif command -v apt-get >/dev/null; then
  echo "    installing build-essential (needs your password)"
  sudo apt-get update && sudo apt-get install -y build-essential
else
  fail "Install gcc and make with your package manager, then run this again."
fi

step "3/7 uv"
export PATH="$HOME/.local/bin:$PATH"
if command -v uv >/dev/null && [[ "$(printf '%s\n' "0.12" "$(uv --version | cut -d' ' -f2)" | sort -V | head -1)" == "0.12" ]]; then
  echo "    $(uv --version): OK"
else
  echo "    installing uv $UV_VERSION into ~/.local/bin"
  curl -LsSf "https://astral.sh/uv/$UV_VERSION/install.sh" | sh
fi

step "4/7 Python environment (policy/.venv)"
cd "$POLICY_DIR"
# --managed-python: uv's own Python 3.12, which includes the headers the source build needs.
uv sync --frozen --managed-python
PY="$POLICY_DIR/.venv/bin/python"
HF="$POLICY_DIR/.venv/bin/hf"

step "5/7 PyTorch and GPUs"
"$PY" - <<'EOF'
import torch
print(f"    torch {torch.__version__}, CUDA {torch.version.cuda}")
if not torch.cuda.is_available():
    raise SystemExit("ERROR: PyTorch cannot use the GPU. Check the driver with nvidia-smi.")
for i in range(torch.cuda.device_count()):
    print(f"    cuda:{i} {torch.cuda.get_device_name(i)}, compute capability {torch.cuda.get_device_capability(i)}")
torch.ones(1, device="cuda").add_(1)  # fails here if this torch build has no kernels for the GPU
print("    GPU test: OK")
EOF

step "6/7 Hugging Face"
export HF_HUB_DISABLE_UPDATE_CHECK=1
HF_TOKEN_FROM_ENV=""
if [[ -f "$REPO_ROOT/.env" ]]; then
  HF_TOKEN_FROM_ENV="$(grep -E '^HF_TOKEN=' "$REPO_ROOT/.env" | tail -1 | cut -d= -f2- | tr -d '"'"'"' ')"
fi
if "$HF" auth whoami >/dev/null 2>&1; then
  echo "    logged in as $("$HF" auth whoami 2>/dev/null | grep -v '^Hint' | head -1)"
elif [[ -n "$HF_TOKEN_FROM_ENV" ]]; then
  echo "    logging in with HF_TOKEN from $REPO_ROOT/.env"
  "$HF" auth login --token "$HF_TOKEN_FROM_ENV" || fail "HF_TOKEN in $REPO_ROOT/.env was rejected. Put a valid token there (or remove it to log in by hand) and run this again."
else
  echo "    paste a Hugging Face access token (https://huggingface.co/settings/tokens):"
  "$HF" auth login
fi
"$PY" - "$BASE_MODEL" "$BACKBONE" <<'EOF'
import sys
from huggingface_hub import auth_check
from huggingface_hub.errors import GatedRepoError
for repo in sys.argv[1:]:
    try:
        auth_check(repo)
        print(f"    {repo}: access OK")
    except GatedRepoError:
        raise SystemExit(f"ERROR: no access to {repo}. Open https://huggingface.co/{repo}, accept the terms "
                         "with the same account, then run this again.")
EOF

step "7/7 GR00T model"
if [[ "$DOWNLOAD" == 1 ]]; then
  "$HF" download "$BASE_MODEL" >/dev/null
  "$HF" download "$BACKBONE" >/dev/null
  echo "    $BASE_MODEL and $BACKBONE are in the Hugging Face cache"
else
  echo "    skipped (--no-download)"
fi

echo
echo "Setup done. Next: copy datasets into policy/data_collection/dataset/, then"
echo "    cd $SCRIPT_DIR && ./train_groot.sh <dataset folder>"
