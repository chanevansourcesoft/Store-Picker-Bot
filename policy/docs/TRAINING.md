# Training GR00T

How to fine-tune GR00T N1.7 on the recorded datasets, on the local training PC (2× RTX 5090, 32 GB each).
The scripts are in `training/`. They use the same GR00T settings as the SO-101 sim project.

> Not tested yet on this PC. The memory settings for 32 GB GPUs (step 5) are a best guess.

## 1. Set up the PC (once)

Before you start:

- Ubuntu (or another x86_64 Linux) with the **NVIDIA driver 570 or newer** installed (needed for RTX 50 series).
  Check with `nvidia-smi`.
- A Hugging Face account that has accepted the access terms of
  https://huggingface.co/nvidia/Cosmos-Reason2-2B (GR00T N1.7 uses it as its backbone), and an access token
  from https://huggingface.co/settings/tokens.

Then run one script:

```bash
git clone <repo url> store-picker-bot
cd store-picker-bot/policy/training
./setup.sh
```

It does everything else and can be run again at any time (finished steps are skipped):

1. checks the NVIDIA driver,
2. installs a C compiler if missing (asks for your password; one package is built from source),
3. installs [uv](https://docs.astral.sh/uv/) if missing,
4. installs the locked Python environment in `policy/.venv` (Python 3.12, torch 2.11 with CUDA 12.8, LeRobot 0.6.1),
5. checks that PyTorch can use both GPUs,
6. logs in to Hugging Face (with `HF_TOKEN` from the repo root `.env` if it is set, otherwise it asks for the token)
   and checks access to the gated backbone,
7. downloads the GR00T base model and backbone (about 12 GB; skip with `./setup.sh --no-download`).

The training scripts use `policy/.venv` by themselves: no environment needs to be activated.

## 2. Get the datasets

Copy the dataset folders from the Jetson into `policy/data_collection/dataset/` on this PC, keeping the
folder names ([DATA_COLLECTION.md](DATA_COLLECTION.md), step 8).

## 3. Merge datasets (optional)

GR00T trains on one dataset. To train on several (for example several recording plans), merge them first:

```bash
cd training
./merge_datasets.sh all_products 12102026_153045_store_v1 14102026_100000_store_v2
```

This creates a new folder such as `data_collection/dataset/13102026_090000_all_products/`. The inputs are not changed.
The script then runs `set_episode_file_indices.py`, which sets the file numbers in the merged episode metadata.
LeRobot 0.6.1 copies them from the inputs, and they do not match for datasets recorded in several sessions (`--resume`).
Dataset edits such as deleting episodes need them to match.
It also combines the inputs' episode logs (product and round of each episode, from `record.py`) into the merged dataset.
All inputs must have the same cameras and FPS, which is always true for datasets made with `record.py`.

## 4. Train

```bash
cd training
./train_groot.sh 13102026_090000_all_products
```

The run is saved in `training/outputs/<DDMMYYYY_HHMMSS>_<dataset>/`. A checkpoint is written every
5,000 steps to `checkpoints/<step>/pretrained_model/` (about 11 GB each), and `checkpoints/last` points to the newest.
Training prints the loss every 10 steps.

Settings are changed with environment variables in front of the command:

| Variable | Default | Meaning |
|---|---|---|
| `NUM_GPUS` | `2` | Number of GPUs. Each one holds a full copy of the model and gets `BATCH_SIZE` samples per step |
| `GPU` | `0,1` | Which GPUs to use, e.g. `GPU=1` with `NUM_GPUS=1` |
| `BATCH_SIZE` | `8` | Samples per GPU per step |
| `STEPS` | `30000` | Training steps |
| `SAVE_FREQ` | `5000` | Steps between checkpoints |
| `MEMORY_MODE` | `bf16` | See step 5 |

Example: `NUM_GPUS=1 GPU=1 STEPS=10000 ./train_groot.sh 13102026_090000_all_products`.
Extra arguments are passed to `lerobot-train`.

## 5. Memory modes

GR00T N1.7 has about 3.5 billion parameters. Using two GPUs makes training faster, but each GPU still needs
the whole model, so the memory per GPU is what matters.

| `MEMORY_MODE` | What it does | GPU memory |
|---|---|---|
| `full` | NVIDIA's recipe: fp32 weights, everything trainable is trained | about 40 GB or more: needs larger GPUs than the training PC |
| `bf16` (default) | bf16 weights, diffusion model trained | aimed at 32 GB, not tested yet |
| `low` | bf16 weights, diffusion model frozen | about 14 GB at batch 8 (tested in the sim project) |

If `bf16` stops with `CUDA out of memory`, first try `BATCH_SIZE=4`, then `MEMORY_MODE=low`.
`low` trains much less of the model; in the sim project it learned noticeably worse.

## 6. Resume an interrupted run

```bash
./resume_groot.sh 13102026_100000_13102026_090000_all_products
```

It continues from `checkpoints/last` with the same step, optimizer and learning-rate schedule.
Use the same `NUM_GPUS` as the original run.

## Notes

- Do not use the `lora_*` options of the GR00T policy: LeRobot's GR00T code does not use them.
- Running the trained model on the Jetson is not set up yet.
