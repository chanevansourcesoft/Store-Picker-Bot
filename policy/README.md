# policy

LeRobot project for the SO-100 arm and GR00T N1.7. Runs on the Jetson as its own process and is the only process that opens the arm and both cameras (`gripper_cam` and `scene_cam`).

Current step: training GR00T on the recorded demonstrations and testing the models on the arm.

Kept out of the ROS 2 build by `COLCON_IGNORE`.

## Folders

| Folder | What is inside |
|---|---|
| `calibration/` | Arm calibration files (made by `lerobot-calibrate`, kept in Git) and `calibrate_wrist_roll.py` |
| `data_collection/` | `record.py` for recording demonstrations; recorded datasets in `data_collection/dataset/` |
| `studio/` | Store Picker Bot app: record and review demonstrations without a terminal (PC) |
| `training/` | GR00T training scripts for the training PC (2× RTX 5090) |
| `inference/` | Runs a trained model on the arm: on a PC for testing, or on the Jetson |
| `instructions.json` | GR00T instruction per item id, used when recording and when picking |
| `docs/` | Step-by-step guides |
| `env.sh` | Environment for running without Docker on a PC (`source policy/env.sh`) |

## Versions

`uv.lock` pins every package (LeRobot 0.6.1, Python 3.12) for all machines. Only torch differs by platform:

| Machine | torch |
|---|---|
| Jetson AGX Orin (container) | 2.7.0 from the `dustynv/pytorch` base image, built for the Orin GPU |
| x86_64 Linux (training PC, dev PCs) | 2.11.0+cu128 |
| Others | 2.11.0 from PyPI |

## Guides

1. Getting started (below): Hugging Face access, then the Jetson container or a PC without Docker
2. [ARM_SETUP.md](docs/ARM_SETUP.md): ports, servo IDs, calibration
3. [TELEOP_TEST.md](docs/TELEOP_TEST.md): checking that the follower mirrors the leader
4. [CAMERA_SETUP.md](docs/CAMERA_SETUP.md): mounting, device paths, formats, exposure
5. [DATA_COLLECTION.md](docs/DATA_COLLECTION.md): recording demonstrations and moving them to the training machine
   - [DATA_STUDIO.md](docs/DATA_STUDIO.md): the same with the Store Picker Bot app, without a terminal (PC)
6. [TRAINING.md](docs/TRAINING.md): training GR00T on the training PC
7. [INFERENCE.md](docs/INFERENCE.md): running a trained model on the arm

## Getting started (Jetson)

The Jetson runs the policy in Docker so it never touches the host Python or other projects.

### Prerequisites

- Jetson AGX Orin with JetPack 6.2, Docker, and the NVIDIA container runtime.
- Data folder on the NVMe (holds the Hugging Face cache; the arm calibration is in `calibration/` in the repo):
  ```bash
  mkdir -p /mnt/nvme/store-picker-bot-data/{hf,lerobot,home}
  ```
  It is mounted in the container at `/data`. To use another location, set `STORE_PICKER_DATA_DIR`.

### Hugging Face access

A free Hugging Face account is enough.

1. Open https://huggingface.co/nvidia/Cosmos-Reason2-2B and accept the access terms (GR00T N1.7 uses it as its backbone).
2. Create an access token in Settings → Access Tokens (choose "Write" to upload datasets and models).
3. In the repo root, copy `.env.example` to `.env` and fill in:
   ```
   HF_TOKEN=<your token>
   HF_USER=<your Hugging Face username>
   ```

### Build and check the container

```bash
cd policy
docker compose build                       # once, and after uv.lock or Dockerfile changes
docker compose run --rm policy python3 -c "import torch, lerobot; print(torch.__version__, torch.cuda.is_available(), lerobot.__version__)"
```

Expected: `2.7.0 True 0.6.1`. Other useful commands:

```bash
docker compose run --rm policy bash        # interactive shell
docker compose run --rm policy lerobot-record --help
```

Arm serial ports and cameras are available inside the container, including after re-plugging.

## Getting started without Docker (PC, e.g. Ubuntu 24.04)

The arms, cameras and data collection also work on a normal Linux PC, without Docker. Every guide shows
both ways: the Docker command for the Jetson, and the same command without the
`docker compose run --rm policy` prefix for a PC. The training PC is set up with its own script
instead: see [TRAINING.md](docs/TRAINING.md).

1. System packages: a C compiler (one package is built from source) and `v4l2-ctl` for the cameras:
   ```bash
   sudo apt install build-essential v4l-utils libsndfile1 curl
   ```
2. Allow your user to use the arm serial ports, then log out and back in:
   ```bash
   sudo usermod -aG dialout $USER
   ```
3. A Python environment with LeRobot 0.6.1. Either:
   - **uv (recommended):** the exact locked versions, in `policy/.venv`, separate from other projects:
     ```bash
     curl -LsSf https://astral.sh/uv/0.12.23/install.sh | sh
     cd policy
     uv sync --frozen --managed-python
     ```
   - **An existing conda env** with LeRobot 0.6.x, e.g. the sim project's `lerobot_env` (it has LeRobot 0.6.2,
     not yet released, on the training PC; see [TRAINING.md](docs/TRAINING.md#lerobot-versions)). Add the servo
     library once (it only adds `feetech-servo-sdk` and `pyserial`):
     ```bash
     conda activate lerobot_env
     pip install feetech-servo-sdk
     ```
4. Create the repo root `.env` (`cp .env.example .env`) and fill it in as described in "Hugging Face
   access" above. The arm ports and cameras are added later ([ARM_SETUP.md](docs/ARM_SETUP.md), step 1, and
   [CAMERA_SETUP.md](docs/CAMERA_SETUP.md), step 2).
5. In every new terminal, load the environment (with conda, run `conda activate lerobot_env` first). Use the folder
   where you cloned the repo, for example:
   ```bash
   source ~/Documents/Store-Picker-Bot/policy/env.sh
   ```
   It prints the environment it uses and its LeRobot version (`0.6.1` with uv). A GPU is not needed for the arms or
   recording, only for running a trained model ([INFERENCE.md](docs/INFERENCE.md)).
   After every change to `.env`, run `source policy/env.sh` again: a terminal only sees `.env` as it was
   when `env.sh` was last loaded.

`env.sh` does what the container does on the Jetson: it uses the active conda env if it has LeRobot,
otherwise `policy/.venv`, loads `.env` (so
`$FOLLOWER_PORT`, `$SCENE_CAM` etc. can be typed in commands), keeps the arm calibration in
`policy/calibration/`, and changes to the `policy/` folder, where all commands run.

## Changing dependencies

Edit `pyproject.toml`, then regenerate the lock and rebuild the Jetson image:

```bash
uv lock
docker compose build   # on the Jetson
```
