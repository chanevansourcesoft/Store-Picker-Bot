"""
Remove the episodes marked bad (key B in record.py) from a dataset.

LeRobot's delete_episodes writes a new copy without them; the episodes after a removed one are
renumbered. The original folder is kept next to it as <folder>_old until you delete it, and the
plan and episode log are carried over with the new numbers.

Usage (from policy/):
    docker compose run --rm policy python3 data_collection/remove_bad_episodes.py dataset_v1_12102026_153045  # Jetson
    python data_collection/remove_bad_episodes.py dataset_v1_12102026_153045     # PC, after `source env.sh`
    python data_collection/remove_bad_episodes.py <folder> --yes                # no question
"""

import argparse
import json
import os
import shutil
import sys
from pathlib import Path

from lerobot.datasets import LeRobotDataset
from lerobot.datasets.dataset_tools import delete_episodes

HERE = Path(__file__).resolve().parent
DATASET_DIR = HERE / "dataset"
LOG_FILE = "episode_log.json"
PLAN_FILE = "plan.json"


def main():
    parser = argparse.ArgumentParser(description="Remove the episodes marked bad from a recorded dataset.")
    parser.add_argument("dataset", help="Dataset folder in data_collection/dataset/")
    parser.add_argument("--yes", action="store_true", help="Do not ask before removing")
    args = parser.parse_args()

    root = DATASET_DIR / Path(args.dataset).name
    log_path = root / "meta" / LOG_FILE
    if not log_path.exists():
        sys.exit(f"{log_path} not found: is {root.name} a dataset recorded with record.py?")
    log = json.loads(log_path.read_text())
    bad = sorted(e["episode_index"] for e in log["episodes"] if e["bad"])
    if not bad:
        sys.exit("No episodes are marked bad. Nothing to do.")
    backup = root.with_name(root.name + "_old")
    if backup.exists():
        sys.exit(f"{backup.name} already exists (backup of an earlier removal). Check and delete it first.")

    print(f"{root.name}: removing {len(bad)} episode(s) marked bad: {bad}")
    if not args.yes and input("Continue? [y/N] ").strip().lower() not in ("y", "yes"):
        sys.exit("Nothing changed.")

    repo_id = f"{os.environ.get('HF_USER') or 'local'}/{root.name}"
    dataset = LeRobotDataset(repo_id, root=root)
    if dataset.meta.total_episodes != len(log["episodes"]):
        sys.exit(f"The episode log lists {len(log['episodes'])} episodes but the dataset has "
                 f"{dataset.meta.total_episodes}. Fix that first; nothing changed.")
    new_root = root.with_name(root.name + "_new")
    if new_root.exists():
        shutil.rmtree(new_root)  # left over from an interrupted run
    delete_episodes(dataset, episode_indices=bad, output_dir=new_root, repo_id=repo_id)

    # Keep the remaining log entries in order and renumber them like delete_episodes does.
    kept = sorted((e for e in log["episodes"] if not e["bad"]), key=lambda e: e["episode_index"])
    for new_index, entry in enumerate(kept):
        entry["episode_index"] = new_index
    log["episodes"] = kept
    (new_root / "meta" / LOG_FILE).write_text(json.dumps(log, indent=2) + "\n")
    if (root / "meta" / PLAN_FILE).exists():
        shutil.copy2(root / "meta" / PLAN_FILE, new_root / "meta" / PLAN_FILE)

    root.rename(backup)
    new_root.rename(root)
    print(f"Done: {root.name} now has {len(kept)} episodes. The original is in {backup.name}: "
          "check the new dataset, then delete the backup.")


if __name__ == "__main__":
    main()
