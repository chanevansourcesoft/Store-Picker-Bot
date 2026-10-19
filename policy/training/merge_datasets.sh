#!/usr/bin/env bash
# Merge several recorded datasets into one, so GR00T trains on all of them.
# All inputs must have the same cameras, FPS and robot (record.py makes sure of that).
# The inputs are left unchanged; the result is a new <name>_<DDMMYYYY_HHMMSS> folder.
#
# Usage:
#   ./merge_datasets.sh <name> <dataset folder> <dataset folder> [...]
#   ./merge_datasets.sh all_products dataset_v1_12102026_153045 dataset_v2_14102026_100000
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DATASET_DIR="${DATASET_DIR:-$SCRIPT_DIR/../data_collection/dataset}"

if [[ $# -lt 3 ]]; then
  echo "Usage: $0 <name> <dataset folder> <dataset folder> [...]" >&2
  echo "Datasets in $DATASET_DIR:" >&2
  ls -1 "$DATASET_DIR" >&2 2>/dev/null || echo "  (none)" >&2
  exit 1
fi
NAME="$1"
shift
if [[ ! "$NAME" =~ ^[A-Za-z0-9_-]+$ ]]; then
  echo "The name may only contain letters, digits, '-' and '_'" >&2
  exit 1
fi
# Use the environment made by setup.sh (policy/.venv), no activation needed.
VENV_BIN="$SCRIPT_DIR/../.venv/bin"
[[ -x "$VENV_BIN/lerobot-edit-dataset" ]] && export PATH="$VENV_BIN:$PATH"
if ! command -v lerobot-edit-dataset >/dev/null; then
  echo "lerobot-edit-dataset not found. Run ./setup.sh first." >&2
  exit 1
fi

REPO_IDS=()
ROOTS=()
PATHS=()
for dataset in "$@"; do
  dataset="$(basename "$dataset")"
  if [[ ! -f "$DATASET_DIR/$dataset/meta/info.json" ]]; then
    echo "Not a dataset: $DATASET_DIR/$dataset" >&2
    exit 1
  fi
  REPO_IDS+=("'local/$dataset'")
  ROOTS+=("'$DATASET_DIR/$dataset'")
  PATHS+=("$DATASET_DIR/$dataset")
done

OUT_NAME="${NAME}_$(date +%d%m%Y_%H%M%S)"
join() { local IFS=,; echo "$*"; }

lerobot-edit-dataset \
  --new_repo_id="local/$OUT_NAME" \
  --new_root="$DATASET_DIR/$OUT_NAME" \
  --operation.type=merge \
  --operation.repo_ids="[$(join "${REPO_IDS[@]}")]" \
  --operation.roots="[$(join "${ROOTS[@]}")]"

# Make the episode-metadata file numbers match the merged files (see set_episode_file_indices.py).
python "$SCRIPT_DIR/set_episode_file_indices.py" "$DATASET_DIR/$OUT_NAME"
# Carry over each episode's round and product from record.py's logs (see merge_episode_logs.py).
python "$SCRIPT_DIR/merge_episode_logs.py" "$DATASET_DIR/$OUT_NAME" "${PATHS[@]}"

echo "Merged dataset: $DATASET_DIR/$OUT_NAME"
