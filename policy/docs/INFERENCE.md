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

On the recording PC, the Store Picker Bot app can also run the model: Run step ([DATA_STUDIO.md](DATA_STUDIO.md)),
with buttons instead of keys and the same options. It uses `inference/run_policy_studio.py`, which runs the same code as
`run_policy.py`.

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

## 5. For the robot: the policy worker

On the robot, the robot server (`agent/robot_server/`, Nemotron's side) asks for one pick at a time. The **policy worker**
(`inference/pick_worker.py`) keeps the model loaded and the arm and cameras open all the time, so a pick starts at once (no
loading per pick). The arm never moves unless a pick (or "home") is asked for.

Start it once, with the model to use (same options as above, without `--product`):

```bash
# With Docker (Jetson)
inference/jetson/pick_worker.sh --model <run>_060000

# Without Docker (PC)
inference/pc/pick_worker.sh --model <run>_060000
```

It loads the model, warms up and shows `Ready on http://127.0.0.1:8790`. Stop it with **Ctrl+C**: the arm goes back to
rest, then its motors switch off.

A pick, for example "ink_box":

1. The instruction is looked up in `instructions.json` (the caller sends only the item id; an unknown id is refused and the
   arm does not move).
2. The arm starts in its rest pose (if a stop left it elsewhere, it first goes back to rest).
3. The model picks the item and puts it in the basket. The episode **ends by itself** when the arm is back near its rest
   pose, with the gripper closed and still for half a second, or after `--max_s` seconds.
4. The arm moves exactly back to its rest pose (like **R**).
5. The answer is **ok** only if the episode ended by itself, a **grasp was seen** (the gripper stayed steady part-way open
   while the model pressed it closed: it was blocked by the item) and the gripper is empty at the end. Otherwise it says why:
   no grasp seen, time limit, or "the gripper still holds something".

These rules were checked on the 240 recorded episodes of dataset_v3: the grasp is seen in all of them, and the end in 94% (the
others were saved before the arm settled; with the model, the time limit and the move back to rest cover them).

The worker answers on a small local web port (`--host`, `--port`, default `127.0.0.1:8790`):

| Request | What it does |
|---|---|
| `GET /status` | ready, busy, state (`loading`, `ready`, `picking`, `returning`, `stopped`, `error`), at rest, the last result |
| `POST /pick` `{"item": "ink_box"}` | one pick; answers when the arm is back at rest: `{"ok", "message", "grasp_seen", "seconds", ...}` |
| `POST /stop` | stops a pick at once; the arm holds where it is (the next pick first goes back to rest) |
| `POST /home` | moves the arm back to rest |

The robot server uses `inference/pick_client.py`: `run_pick(item, instruction)` → `(ok, message)`, `stop_arm()` and
`arm_ready()`. It needs no LeRobot or GR00T. Another address: set `PICK_WORKER_URL`.

Try it from a second terminal while the worker runs:

```bash
curl localhost:8790/status
curl -X POST localhost:8790/pick -d '{"item": "ink_box"}'
curl -X POST localhost:8790/stop
```

With `--dry_run`, the model runs but the arm does not move, so each pick ends at the time limit (use `--max_s 10`).

## Options

| Option | Default | Meaning |
|---|---|---|
| `--dry_run` | off | run the model, do not move the arm |
| `--max_s` | 40 | stop an episode after this many seconds (recorded episodes take 17 to 28 s) |
| `--execute_steps` | 16 (the whole chunk) | use only the first N targets of each chunk |
| `--blend_steps` | 8 | a new chunk fades in over this many steps instead of replacing the old one at once (0 = switch at once) |
| `--smooth` | 0.3 | smoothing of the arm joints' targets, 0 (off) to 0.9 (strong, but the arm lags); the gripper is not smoothed |
| `--speed` | 1.0 | play the movements at this speed: below 1 is slower, for example `0.8` when the arm rushes |
| `--substeps` | 3 | commands per target: the arm is moved part of the way between two targets (90 commands a second instead of 30), so it moves smoothly instead of in small jumps (1 = off) |
| `--accel` | 0 (off) | acceleration limit of the arm servos, 1 (very gentle) to 253: they ramp their speed up and down instead of starting and stopping hard. Try 50–150; too low makes the arm lag behind the model. The gripper is not limited |
| `--max_step` | 30 | safety limit per command, see above |
| `--task` | | an instruction text instead of `--product` (not for the policy worker) |
| `--host`, `--port` | 127.0.0.1, 8790 | policy worker only: where it answers |
| `--follower_port`, `--gripper_cam`, `--scene_cam` | from `.env` | same as for recording |
| `--fourcc` | | camera format, the same as used for recording |

All options: `inference/pc/run.sh --help` (PC) or `inference/jetson/run.sh --help` (Jetson).

## How it moves the arm

The model looks at both cameras, the arm position and the instruction, and predicts the next 16 targets (one chunk, about
half a second). While the arm plays a chunk, the model already predicts the next one from a newer picture. When the new
chunk arrives, its targets for the time that went by meanwhile are skipped, and the arm goes on with it. So the arm does not
stop between chunks.

Two predictions in a row never agree exactly, so switching from one chunk to the next would give the arm a small jump about
every quarter of a second. Instead, the new chunk fades in over its first 8 steps (`--blend_steps`), and the arm joints'
targets are lightly smoothed (`--smooth`). At the end of each episode the program prints how smooth it was: the change
between two targets in a row (median, 95%, largest). Lower is smoother; compare settings with it.

Between two targets the arm also gets two commands that move it part of the way (`--substeps 3`), so it does not
move in 30 small jumps a second. `--accel` is a soft limit inside the servos: each one still goes to exactly its target,
but ramps its speed up and down gently. Nothing is cut off (unlike `--max_step`), but the arm reacts a little later. The
setting is lost when the servos lose power, LeRobot sets it back to the fastest when it connects (so recording is not
affected), and the program sets it back when it ends.

If the arm still rushes, the demonstrations were probably fast: the model copies their speed. `--speed 0.8` plays the
movements slower; recording calmer demonstrations fixes it for good.

## LeRobot versions

The training PC saves models with LeRobot 0.6.2. This program also runs them with 0.6.1 (the Jetson's version): 0.6.2
writes the weight precision as `"dtype"` in `config.json`, which 0.6.1 does not know, so the program translates it. The
weights are used as they were trained.

## Not tested yet

- The Jetson: speed and memory. If the model is too slow there, the arm waits for it between chunks.
- The policy worker with the real arm: tested with a simulated arm only (pick, grasp seen, end by itself, move
  back to rest, stop, time limit, closing).
