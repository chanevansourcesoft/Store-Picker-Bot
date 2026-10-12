# Local training

GR00T N1.7 training on the local PC (2× RTX 5090). Guide: [TRAINING_LOCAL.md](../../docs/TRAINING_LOCAL.md).

| File | What it does |
|---|---|
| `setup.sh` | One-time setup of the PC: driver check, Python environment, Hugging Face login, model download |
| `train_groot.sh` | Trains GR00T on one dataset from `../../data_collection/dataset/` |
| `resume_groot.sh` | Continues an interrupted run from its last checkpoint |
| `merge_datasets.sh` | Merges several datasets into a new one |
| `set_episode_file_indices.py` | Sets the episode-metadata file numbers of a merged dataset (run by `merge_datasets.sh`) |

Runs are saved in `outputs/` (not stored in Git).
