"""Combine the episode logs of merged datasets. Run by merge_datasets.sh.

record.py lists every episode with its round and product in meta/episode_log.json. A merge
puts the inputs one after the other, so the episodes of the second input are numbered after
those of the first, and so on. This writes the same numbering into the merged dataset's log,
with the input each episode came from. Inputs without a log only shift the numbers.

Usage: python merge_episode_logs.py <merged dataset folder> <input folder> [<input folder> ...]
"""

import json
import sys
from pathlib import Path

LOG_FILE = "episode_log.json"

out_root, inputs = Path(sys.argv[1]), [Path(p) for p in sys.argv[2:]]
merged = {"sources": [], "episodes": []}
offset = 0
for root in inputs:
    total = json.loads((root / "meta" / "info.json").read_text())["total_episodes"]
    log_path = root / "meta" / LOG_FILE
    merged["sources"].append({"dataset": root.name, "first_episode": offset, "episodes": total,
                              "has_log": log_path.exists()})
    if log_path.exists():
        for entry in json.loads(log_path.read_text())["episodes"]:
            merged["episodes"].append({**entry, "episode_index": entry["episode_index"] + offset,
                                       "source": root.name})
    offset += total

(out_root / "meta" / LOG_FILE).write_text(json.dumps(merged, indent=2) + "\n")
print(f"Episode log: {len(merged['episodes'])} of {offset} episodes have round and product info.")
