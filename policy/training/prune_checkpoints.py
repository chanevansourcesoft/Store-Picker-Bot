"""
Keep a training run within the disk space: delete older checkpoints as new ones arrive.

    - the newest --keep_last checkpoints stay complete (model + training state, so the run can be resumed),
    - of the older ones, those at a multiple of --keep_model_every steps keep only their model
      (pretrained_model/), to compare or run them later,
    - the other older ones are deleted.

start_training.sh runs this every minute while training, and once at the end. It never touches the
newest checkpoints or the `last` link, so it is safe while a checkpoint is being written.

Usage:
    python3 prune_checkpoints.py <run folder> [--keep_last 2] [--keep_model_every 10000] [--dry_run]
"""

import argparse
import shutil
from pathlib import Path


def plan(checkpoints_dir, keep_last, keep_model_every):
    """[(folder, "delete" | "model only")] for the checkpoints to thin out, oldest first."""
    steps = sorted((p for p in checkpoints_dir.iterdir() if p.is_dir() and not p.is_symlink() and p.name.isdigit()),
                   key=lambda p: int(p.name))
    actions = []
    for path in steps[:-keep_last] if keep_last else steps:
        if keep_model_every and int(path.name) % keep_model_every == 0:
            if (path / "training_state").exists():
                actions.append((path, "model only"))
        else:
            actions.append((path, "delete"))
    return actions


def main():
    parser = argparse.ArgumentParser(description="Delete older checkpoints of a training run.")
    parser.add_argument("run", type=Path, help="Training run folder (with checkpoints/)")
    parser.add_argument("--keep_last", type=int, default=2, help="Newest checkpoints kept complete (at least 1)")
    parser.add_argument("--keep_model_every", type=int, default=10000,
                        help="Older checkpoints at a multiple of this many steps keep their model (0 = none)")
    parser.add_argument("--dry_run", action="store_true", help="Only print what would be deleted")
    args = parser.parse_args()
    if args.keep_last < 1:
        parser.error("--keep_last must be at least 1: the newest checkpoint may still be being written")

    checkpoints_dir = args.run / "checkpoints"
    if not checkpoints_dir.is_dir():
        return
    for path, action in plan(checkpoints_dir, args.keep_last, args.keep_model_every):
        print(f"{'Would' if args.dry_run else 'Will'} {'delete' if action == 'delete' else 'keep only the model of'} "
              f"{path}", flush=True)
        if args.dry_run:
            continue
        shutil.rmtree(path if action == "delete" else path / "training_state")


if __name__ == "__main__":
    main()
