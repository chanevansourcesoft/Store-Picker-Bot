# Local training

GR00T N1.7 training on the training PC (2× RTX 5090). Guide: [TRAINING.md](../docs/TRAINING.md).

| File | What it does |
|---|---|
| `setup.sh` | One-time setup of the PC: driver check, Python environment, Hugging Face login, model download |
| `train_groot.sh` | Trains GR00T on one dataset from `../data_collection/dataset/` |
| `start_training.sh` | Starts `train_groot.sh` in the background (keeps running after the terminal closes) and thins out old checkpoints; `--resume <run>` continues a stopped run the same way |
| `stop_training.sh` | Stops a run started with `start_training.sh` |
| `prune_checkpoints.py` | Deletes older checkpoints of a run, keeping the newest ones and some models (run by `start_training.sh`) |
| `training_status.py` | Every run with its progress, checkpoints and size, plus GPUs and disk, as JSON (used by the app) |
| `resume_groot.sh` | Continues an interrupted run from its last checkpoint |
| `merge_datasets.sh` | Merges several datasets into a new one |
| `set_episode_file_indices.py` | Sets the episode-metadata file numbers of a merged dataset (run by `merge_datasets.sh`) |
| `merge_episode_logs.py` | Combines the episode logs (product and round per episode) of merged datasets (run by `merge_datasets.sh`) |

Runs are saved in `outputs/`, logs of `start_training.sh` in `logs/` (both not stored in Git).
