# policy

LeRobot project for the SO-100 arm and GR00T N1.7. Runs on the Jetson as its own process and is the only process that opens the arm and both cameras (wrist and scene).

Current step: recording real teleoperated demonstrations.

Kept out of the ROS 2 build by `COLCON_IGNORE`.

## Versions

`uv.lock` pins every package (LeRobot 0.6.1, Python 3.12) for all machines. Only torch differs by platform:

| Machine | torch |
|---|---|
| Jetson AGX Orin (container) | 2.7.0 from the `dustynv/pytorch` base image, built for the Orin GPU |
| x86_64 Linux (Nebius, dev PCs) | 2.11.0+cu128 |
| Others | 2.11.0 from PyPI |

## Guides

1. Getting started (below): Hugging Face access and the Jetson container
2. [ARM_SETUP.md](docs/ARM_SETUP.md): ports, servo IDs, calibration, teleoperation
3. [CAMERA_SETUP.md](docs/CAMERA_SETUP.md): mounting, device paths, formats, exposure
4. [DATA_COLLECTION.md](docs/DATA_COLLECTION.md): recording demonstrations and uploading them

## Getting started (Jetson)

The Jetson runs the policy in Docker so it never touches the host Python or other projects.

### Prerequisites

- Jetson AGX Orin with JetPack 6.2, Docker, and the NVIDIA container runtime.
- Data folder on the NVMe (holds the Hugging Face cache, datasets, calibration, training outputs):
  ```bash
  mkdir -p /mnt/nvme/store-picker-bot-data/{hf,lerobot,outputs,home}
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

```bash
cd policy
uv sync
uv run lerobot-record --help
```

## Changing dependencies

Edit `pyproject.toml`, then regenerate the lock and rebuild the Jetson image:

```bash
uv lock
docker compose build   # on the Jetson
```
