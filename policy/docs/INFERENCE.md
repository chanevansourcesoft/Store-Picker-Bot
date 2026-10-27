# Running a trained model

How to let a trained GR00T model control the real arm: it picks one product from the shelf and puts it in the basket.

Complete [ARM_SETUP.md](ARM_SETUP.md) and [CAMERA_SETUP.md](CAMERA_SETUP.md) first (arm calibrated, cameras and their
settings in `.env` and `camera_settings.json`). The leader arm is not needed.

The program is `inference/run_policy.py`. It is the same on every machine. Two small scripts start it in the right
environment:

| Script | Machine | Environment |
|---|---|---|
| `inference/pc/run.sh` | a PC with an NVIDIA GPU (for testing) | the conda env you activated (e.g. `lerobot_env`), or `policy/.venv` |
| `inference/jetson/run.sh` | the Jetson AGX Orin | the policy container (`compose.yaml`) |

It opens the follower arm and both cameras with the same ports and camera settings as `record.py`, so the model sees what
it saw in training.

## Start a terminal

Every command in this guide is shown twice. Use the one for your setup. The examples use `~/Documents/Store-Picker-Bot`
as the repo folder: change it to where you cloned the repo.

**With Docker (Jetson):** go to the `policy/` folder.

```bash
cd ~/Documents/Store-Picker-Bot/policy
```

**Without Docker (PC):** in every new terminal, activate the Python environment, then load `env.sh` (it loads `.env` and
the calibration folder, and goes to `policy/`).

```bash
conda activate lerobot_env      # only with a conda env; skip it with uv (policy/.venv)
source ~/Documents/Store-Picker-Bot/policy/env.sh
```

All commands below run from the `policy/` folder.

## 1. Prepare the machine (once)

**PC:** GR00T needs extra Python packages and a GPU with about **10 GB of free memory** (the model's weights alone are
9.3 GB).

- With a conda env, add them once. First the dry run: its last line lists what would be installed, and it must not
  contain `torch`, `torchvision` or `lerobot` with another version:
  ```bash
  pip install --dry-run -e "$HOME/lerobot[groot]"
  pip install -e "$HOME/lerobot[groot]"
  ```
  `$HOME/lerobot` is where LeRobot's source is. If yours is elsewhere, `pip show lerobot` shows it (the line
  `Editable project location`).
- With `policy/.venv` (uv), they are already installed.

On a 16 GB GPU, close other programs that use the GPU before running the model. Check with `nvidia-smi` (for example,
LM Studio with a model loaded takes about 8 GB).

**Jetson:** the container already has the packages.

**Both:** download the GR00T base model and its backbone (about 12 GB). Running a trained model needs them too. This needs
`HF_TOKEN` in the repo root `.env` ("Hugging Face access" in [README.md](../README.md)).

```bash
# With Docker (Jetson)
docker compose run --rm policy hf download nvidia/GR00T-N1.7-3B
docker compose run --rm policy hf download nvidia/Cosmos-Reason2-2B

# Without Docker (PC)
hf download nvidia/GR00T-N1.7-3B
hf download nvidia/Cosmos-Reason2-2B
```

## 2. Copy the model

Training saves a checkpoint every 5,000 steps on the training PC, in
`policy/training/outputs/<run>/checkpoints/<step>/` ([TRAINING.md](TRAINING.md), step 4). Only its `pretrained_model/`
folder is needed (`training_state/` is only for resuming training).

On the recording PC, the Store Picker Bot app does this: Train step, click the run, choose the checkpoint and
press **Copy to this PC** ([DATA_STUDIO.md](DATA_STUDIO.md)). It saves it as `inference/models/<run>_<step>/`.

By hand: copy it into `inference/models/`, in a folder with a name you choose, for example the run name and the
step. Run this on the machine that runs the model, from `policy/`:

```bash
rsync -a --progress \
  <user>@<training-pc>:<repo>/policy/training/outputs/<run>/checkpoints/010000/pretrained_model/ \
  inference/models/<run>_010000/
```

Check: `ls inference/models/<run>_010000/` must show `config.json` and `model.safetensors`.

The model is then called by its folder name: `--model <run>_010000`. Models are not stored in Git.

`--model` also takes a path (on the PC; relative paths are relative to the folder you are in) to a `pretrained_model/`
folder, a checkpoint folder, or a whole training run (its newest checkpoint is used). On the Jetson, the container only
sees the `policy/` folder: keep models in `inference/models/`.

## 3. Dry run first

With every new model, first run it with `--dry_run`: the model runs, but the arm does not move. This checks that the
model loads and that it is fast enough.

```bash
# With Docker (Jetson)
inference/jetson/run.sh --model <run>_010000 --product mentos --dry_run

# Without Docker (PC)
inference/pc/run.sh --model <run>_010000 --product mentos --dry_run
```

`--product` is an item id from `instructions.json`. Its instruction is sent to the model, so it must be a product the model
was trained on.

Loading takes a few seconds. The model then runs three times to warm up (the first runs are slow), and
`Model ready: ... ms per chunk` is shown. When the line shows `READY`, press **Space**. After a few seconds, press
**Space** again. It prints:

- how long the model took per chunk (median and slowest). On the RTX 5060 Ti: about 160 ms;
- how many steps the arm waited for the model. Only the first chunk of an episode is waited for (about 5 steps). Many
  more means the model is too slow for this machine;
- the largest distance between the model's targets and where the arm was.

Then press **Q**.

## 4. Run on the arm

1. Put the arm in its **rest pose** (the pose every recorded episode started from). Put the product on the shelf where it
   was during recording, and the base at a position used in recording.
2. Start the program, without `--dry_run`:
   ```bash
   # With Docker (Jetson)
   inference/jetson/run.sh --model <run>_010000 --product mentos

   # Without Docker (PC)
   inference/pc/run.sh --model <run>_010000 --product mentos
   ```
3. Stay next to the arm, ready to press **Space** to stop it. Press **Space** to start.

Keys (press them in the terminal running the program):

| Key | What it does |
|---|---|
| Space | start an episode / stop the arm where it is |
| R | move the arm slowly back to where the episode started |
| Q | quit: the arm first moves back to where the episode started, then its motors switch off |
| Ctrl+C | like Q. A second Ctrl+C while the arm moves back stops at once: the motors stay on and hold the arm |

An episode also stops after 40 seconds (`--max_s`). After it stops, the arm holds its position. Press **R** to bring it
back, put the product back on the shelf, and press **Space** for the next try.

**Safety limit:** each command may move a joint at most 30 units from where it is (`--max_step`). The recordings stay
below this, so a normal movement is not slowed, but a wrong prediction cannot make the arm jump. Do not raise it.

## Options

| Option | Default | Meaning |
|---|---|---|
| `--dry_run` | off | run the model, do not move the arm |
| `--max_s` | 40 | stop an episode after this many seconds (recorded episodes take 17 to 28 s) |
| `--execute_steps` | 16 (the whole chunk) | use only the first N targets of each chunk |
| `--max_step` | 30 | safety limit per command, see above |
| `--task` | | an instruction text instead of `--product` |
| `--follower_port`, `--gripper_cam`, `--scene_cam` | from `.env` | same as for recording |
| `--fourcc` | | camera format, the same as used for recording |

All options: `inference/pc/run.sh --help` (PC) or `inference/jetson/run.sh --help` (Jetson).

## How it moves the arm

The model looks at both cameras, the arm position and the instruction, and predicts the next 16 targets (one chunk, about
half a second). While the arm plays a chunk, the model already predicts the next one from a newer picture. When the new
chunk arrives, its targets for the time that went by meanwhile are skipped, and the arm goes on with it. So the arm does not
stop between chunks.

## LeRobot versions

The training PC saves models with LeRobot 0.6.2. This program also runs them with 0.6.1 (the Jetson's version): 0.6.2
writes the weight precision as `"dtype"` in `config.json`, which 0.6.1 does not know, so the program translates it. The
weights are used as they were trained.

## Not tested yet

- The Jetson: speed and memory. If the model is too slow there, the arm waits for it between chunks.
