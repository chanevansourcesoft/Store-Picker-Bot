# Data collection

Recording teleoperated demonstrations on the real SO-100 arms. Guide: [DATA_COLLECTION.md](../docs/DATA_COLLECTION.md).

| File / folder | What it is |
|---|---|
| `record.py` | Records episodes with LeRobot following a plan of products and rounds, controlled with keys (Space, N, X, B, Q) |
| `remove_bad_episodes.py` | Removes the episodes marked bad (key B) from a dataset |
| `camera_settings.json` | Fixed exposure, white balance and focus, applied by `record.py` (start from `camera_settings.example.json`) |
| `dataset/` | Recorded datasets, one folder each: `<name>_<DDMMYYYY_HHMMSS>/` (not stored in Git) |
