# policy

LeRobot project for the SO-100 arm and GR00T N1.7. Runs on the Jetson as its own process and is the only process that opens the arm and both cameras (`gripper_cam` and `scene_cam`).

Current step: recording real teleoperated demonstrations.

Kept out of the ROS 2 build by `COLCON_IGNORE`.

## Folders

| Folder | What is inside |
|---|---|
| `data_collection/` | `record.py` for recording demonstrations; recorded datasets in `data_collection/dataset/` |
| `training/local/` | GR00T training scripts for the local PC (2× RTX 5090) |
| `training/nebius_cloud_ai/` | Plan for training on Nebius AI Cloud |
| `docs/` | Step-by-step guides |

## Versions

`uv.lock` pins every package (LeRobot 0.6.1, Python 3.12) for all machines. Only torch differs by platform:

| Machine | torch |
|---|---|
| Jetson AGX Orin (container) | 2.7.0 from the `dustynv/pytorch` base image, built for the Orin GPU |
| x86_64 Linux (training PC, Nebius, dev PCs) | 2.11.0+cu128 |
| Others | 2.11.0 from PyPI |

## Guides

1. Getting started (below): Hugging Face access and the Jetson container
2. [ARM_SETUP.md](docs/ARM_SETUP.md): ports, servo IDs, calibration
3. [TELEOP_TEST.md](docs/TELEOP_TEST.md): checking that the follower mirrors the leader
4. [CAMERA_SETUP.md](docs/CAMERA_SETUP.md): mounting, device paths, formats, exposure
5. [DATA_COLLECTION.md](docs/DATA_COLLECTION.md): recording demonstrations and moving them to the training machine
6. [TRAINING_LOCAL.md](docs/TRAINING_LOCAL.md): training GR00T on the local PC

## Getting started (Jetson)

The Jetson runs the policy in Docker so it never touches the host Python or other projects.

### Prerequisites

- Jetson AGX Orin with JetPack 6.2, Docker, and the NVIDIA container runtime.
- Data folder on the NVMe (holds the Hugging Face cache and the arm calibration files):
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

## Other machines: uv

The training PC is set up with one script: see [TRAINING_LOCAL.md](docs/TRAINING_LOCAL.md).
On other Linux machines, install the same environment with uv (needs a C compiler, e.g. `build-essential`):

```bash
cd policy
uv sync --managed-python
uv run lerobot-record --help
```

## Changing dependencies

Edit `pyproject.toml`, then regenerate the lock and rebuild the Jetson image:

```bash
uv lock
docker compose build   # on the Jetson
```
