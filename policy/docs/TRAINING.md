# Training GR00T

How to fine-tune GR00T N1.7 on the recorded datasets, on the local training PC (2× RTX 5090, 32 GB each).
The scripts are in `training/`. They use the same GR00T settings as the SO-101 sim project.
The examples use `~/Documents/Store-Picker-Bot` as the repo folder: change it to where you cloned the repo.

## 1. Set up the PC (once)

Before you start:

- Ubuntu (or another x86_64 Linux) with the **NVIDIA driver 570 or newer** installed (needed for RTX 50 series).
  Check with `nvidia-smi`.
- A Hugging Face account that has accepted the access terms of
  https://huggingface.co/nvidia/Cosmos-Reason2-2B (GR00T N1.7 uses it as its backbone), and an access token
  from https://huggingface.co/settings/tokens. Put it in `HF_TOKEN` in the repo root `.env`
  (copy `.env.example`). The training scripts read it from there.

Then choose **one** Python environment.

### Option A: the existing conda env `lerobot_env` (used on the training PC)

The sim project's `lerobot_env` (Python 3.12, LeRobot installed from `~/lerobot`) already works. Add the GR00T
packages once, without changing torch or LeRobot:

```bash
conda activate lerobot_env
pip install --dry-run -e "$HOME/lerobot[groot,accelerate-dep]"   # check: must not replace torch or lerobot
pip install -e "$HOME/lerobot[groot,accelerate-dep]"
hf download nvidia/GR00T-N1.7-3B
hf download nvidia/Cosmos-Reason2-2B                              # gated: needs HF_TOKEN
```

Run the training in a terminal where `lerobot_env` is active.

**`policy/.venv` must not exist** with this option. If it exists, the training scripts use it instead of the
active conda env.

### Option B: a new environment with `setup.sh`

```bash
cd ~/Documents/Store-Picker-Bot/policy/training
./setup.sh
```

It can be run again at any time (finished steps are skipped):

1. checks the NVIDIA driver,
2. installs a C compiler if missing (asks for your password; one package is built from source),
3. installs [uv](https://docs.astral.sh/uv/) if missing,
4. installs the locked Python environment in `policy/.venv` (Python 3.12, torch 2.11 with CUDA 12.8, LeRobot 0.6.1),
5. checks that PyTorch can use both GPUs,
6. logs in to Hugging Face (with `HF_TOKEN` from the repo root `.env` if it is set, otherwise it asks for the token)
   and checks access to the gated backbone,
7. downloads the GR00T base model and backbone (about 12 GB; skip with `./setup.sh --no-download`).

The training scripts use `policy/.venv` by themselves: no environment needs to be activated.
Note: this environment has LeRobot **0.6.1**, which does not know the precision setting the training script
uses now (see [LeRobot versions](#lerobot-versions)). Use option A until this is fixed.

## 2. Get the datasets

Copy the dataset folders from the recording machine (the Jetson or a PC) into `policy/data_collection/dataset/` on
this PC, keeping the folder names ([DATA_COLLECTION.md](DATA_COLLECTION.md), step 8).

## 3. Merge datasets (optional)

GR00T trains on one dataset. To train on several (for example several recording plans), merge them first:

```bash
cd ~/Documents/Store-Picker-Bot/policy/training
./merge_datasets.sh all_products dataset_v1_12102026_153045 dataset_v2_14102026_100000
```

This creates a new folder such as `data_collection/dataset/all_products_13102026_090000/`. The inputs are not changed.
The script then runs `set_episode_file_indices.py`, which sets the file numbers in the merged episode metadata.
LeRobot 0.6.1 copies them from the inputs, and they do not match for datasets recorded in several sessions (`--resume`).
Dataset edits such as deleting episodes need them to match.
It also combines the inputs' episode logs (product and round of each episode, from `record.py`) into the merged dataset.
All inputs must have the same cameras and FPS, which is always true for datasets made with `record.py`.

## 4. Train

```bash
conda activate lerobot_env      # option A only; skip it with option B (policy/.venv)
cd ~/Documents/Store-Picker-Bot/policy/training
GPU=1 STEPS=10000 ./train_groot.sh dataset_v1_21102026_183539
```

The run is saved in `training/outputs/<DDMMYYYY_HHMMSS>_<dataset>/`. A checkpoint is written every
5,000 steps to `checkpoints/<step>/pretrained_model/` (about 11 GB each), and `checkpoints/last` points to the newest.
Training prints the loss every 10 steps.

On one RTX 5090 with the defaults, training uses about **20 GB** and runs about **1 step per second**:
10,000 steps take about **2 h 45 min**.

While it trains, do not start anything else on the training GPU (for example a policy server).

Settings are changed with environment variables in front of the command:

| Variable | Default | Meaning |
|---|---|---|
| `GPU` | `1` | Which GPU to use. GPU 0 also runs the screen |
| `STEPS` | `30000` | Training steps |
| `BATCH_SIZE` | `64` | Samples per GPU per step (same as the sim project) |
| `SAVE_FREQ` | `5000` | Steps between checkpoints |
| `MEMORY_MODE` | `low` | See step 5 |
| `NUM_WORKERS` | `16` | Processes that read the videos for training |
| `NUM_GPUS` | `1` | Number of GPUs. Read "Two GPUs" below first |

Extra arguments are passed to `lerobot-train`.

### In the background (also used by the Store Picker Bot app)

`train_groot.sh` stops when its terminal closes. To keep training after closing the terminal or an SSH
connection, start it with `start_training.sh` instead. It takes the same settings, and also activates a
conda env (`CONDA_ENV`) and keeps the run within the disk space:

```bash
GPU=1 STEPS=50000 CONDA_ENV=lerobot_env ./start_training.sh dataset_v1_21102026_183539
```

It prints the run name. Then:

```bash
tail -f logs/<run name>.log            # follow the training (Ctrl+C stops following, not the training)
python3 training_status.py             # every run, with its step, loss and checkpoints (JSON)
./stop_training.sh <run name>          # stop it; its checkpoints stay
CONDA_ENV=lerobot_env ./start_training.sh --resume <run name>   # continue it from its newest complete checkpoint
```

`--resume` continues a stopped or failed run in the background, with the run's own steps, settings and
learning-rate schedule. Its log goes on in the same file.

While it trains, `prune_checkpoints.py` thins out older checkpoints every minute: the newest 2 stay
complete (`KEEP_LAST`), older ones at a multiple of 10,000 steps keep only their model (`KEEP_MODEL_EVERY`),
and the others are deleted. One complete checkpoint is about 11.5 GB (9.3 GB model + 2.1 GB resume state),
so a 50,000-step run needs up to about 62 GB instead of 115 GB.

The Store Picker Bot app's Train step ([DATA_STUDIO.md](DATA_STUDIO.md)) sends the dataset from the
recording PC and runs these scripts over SSH.

### Two GPUs

`NUM_GPUS=2 BATCH_SIZE=32 ./train_groot.sh <dataset>` trains on both GPUs (32 samples each, 64 per step).
Each GPU holds a full copy of the model.

**On this PC it is slower than one GPU.** GPU 1 sits in a slot with only 4 PCIe lanes (PCIe 4.0 x4), and the GPUs
must exchange about 1 GB after every step. Measured: 44 samples per second with two GPUs, 66 with one.
Two GPUs only help if both sit in x16 slots. Check the slots with (while training runs):

```bash
nvidia-smi --query-gpu=index,pcie.link.gen.current,pcie.link.width.current --format=csv
```

## 5. Memory modes

GR00T N1.7 has about 3.5 billion parameters.

| `MEMORY_MODE` | What it does | GPU memory |
|---|---|---|
| `full` | NVIDIA's recipe: fp32 weights, everything trainable is trained | more than 32 GB: runs out of memory on the training PC, even at batch 8 |
| `bf16` | bf16 base weights, diffusion model trained (about 1.6 billion trainable parameters) | more than one 32 GB GPU: runs out of memory at batch 64 and 32. Fits at batch 32 with the backbone on the second GPU, see below |
| `low` (default) | bf16 base weights, diffusion model frozen (529 million trainable parameters) | about 25 GB at batch 64 |

`low` is what the sim project used for its final training (batch 64, one RTX 5090).

In every mode, `lerobot_train_groot.py` keeps the weights being trained in fp32, while the frozen weights stay as
they are. Runs before 2026-10-30 trained them in bf16, where an optimizer step rounds away to nothing on any weight
above about 0.03: in 20,000 steps, only 33% of the trained weights changed, and almost none of the large ones. Those
models learned less than they could have.

**Diffusion model on the training PC:** with `SPLIT_BACKBONE=1`, the frozen backbone runs on the second GPU and everything
else on the first (one process, one model, unlike "Two GPUs" above), so the diffusion model can be trained in fp32:

```bash
GPU=1,0 SPLIT_BACKBONE=1 MEMORY_MODE=bf16 BATCH_SIZE=32 STEPS=100000 CONDA_ENV=lerobot_env \
  ./start_training.sh <dataset folder>
```

Measured over 30 steps: 31 GB on GPU 1 (of 32), 10 GB on GPU 0, about 0.6 s per step, 12 GB per model. Start this kind
of run from a terminal, not the app (the app uses one GPU). `--resume` keeps the setting.
If a mode stops with `CUDA out of memory`, lower `BATCH_SIZE`.

## 6. Resume an interrupted run

```bash
./resume_groot.sh 13102026_100000_all_products_13102026_090000
```

It continues from `checkpoints/last` with the same step, optimizer and learning-rate schedule.
Use the same `NUM_GPUS` as the original run (default 1). It stops when the terminal closes. For a run started
with `start_training.sh` (or the app), use `./start_training.sh --resume <run name>` instead: it runs in the
background, like the original run ("In the background" above), and the app's **Resume** button does the same.

## 7. Train a model further on more data (`FROM_MODEL`)

When a dataset gets more episodes (for example to fix one situation), you can go on from a trained model instead of
starting again from NVIDIA's base model: the model already knows the task, so far fewer steps are needed.

In the Store Picker Bot app: Train step, **Start from**, choose the checkpoint ([DATA_STUDIO.md](DATA_STUDIO.md)). From a
terminal:

```bash
# in the background, like a normal run (the run name is printed)
FROM_MODEL=27102026_121427_dataset_v3_26102026_191752_060000 STEPS=20000 CONDA_ENV=lerobot_env \
  ./start_training.sh dataset_v3_26102026_191752

# or in the terminal
FROM_MODEL=27102026_121427_dataset_v3_26102026_191752_060000 STEPS=20000 ./train_groot.sh dataset_v3_26102026_191752
```

- `FROM_MODEL` is `<run>_<step>` (a checkpoint of a run in `outputs/`), a run name (its newest checkpoint), or a path
  to a checkpoint or `pretrained_model` folder.
- It is a **new run** with a new learning-rate schedule over `STEPS` steps, not a resume.
- The model's own settings are used: memory mode, chunk size, relative actions (`MEMORY_MODE` is ignored), and its
  action scaling from the dataset it was trained on (same robot and task, so it still fits).
- **Train on the whole dataset** (old and new episodes), not on the new episodes alone: otherwise the model slowly
  forgets the situations that are only in the old ones.
- About 20,000 steps (about 5.5 h) is a good start; compare its checkpoints on the arm with the model it started from.
  If it does not get better, train from the base model as usual.

Checked on 2026-10-29 with a 3-step run on dataset_v3 from the 60k model: the model loads (the loss starts at
0.006–0.03, like at the end of its training, instead of about 1.1 for a new run) and keeps its settings.

**Image augmentation:** `train_groot.sh` and `resume_groot.sh` start `lerobot_train_groot.py` instead of
`lerobot-train`. It is the same program, except that GR00T's random image crop (a training augmentation) stays on when
a run starts from a saved model. LeRobot loads a saved model's processors in evaluation mode, so without it a
`FROM_MODEL` run, **and every resumed run**, would train without the crop. Runs resumed before 2026-10-29 (for example
`27102026_121427_dataset_v3_26102026_191752` from step 20,000) trained without it after the resume.

## LeRobot versions

The training PC uses LeRobot **0.6.2** from `~/lerobot` (`lerobot_env`). This version is not released yet;
the Jetson and `setup.sh` use the released **0.6.1**.

- **Precision setting:** 0.6.2 replaced `--policy.model_params_fp32=false` with `--policy.dtype=bfloat16` and
  silently ignores the old setting (the model then loads in fp32 and runs out of memory). `train_groot.sh`
  uses `--policy.dtype`, so it needs 0.6.2.
- **Models:** the GR00T code is the same in both versions, so the trained model behaves the same. But a model saved
  by 0.6.2 has a `"dtype"` line in its `pretrained_model/config.json`, and 0.6.1 refuses to load it.
  `inference/run_policy.py` translates it, so 0.6.1 can run these models ([INFERENCE.md](INFERENCE.md)).

## Notes

- Do not use the `lora_*` options of the GR00T policy: LeRobot's GR00T code does not use them.
- Running the trained model on the arm: [INFERENCE.md](INFERENCE.md).

Next: [INFERENCE.md](INFERENCE.md).
