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

## Jetson: container

The Jetson runs the policy in Docker so it never touches the host Python or other projects.

```bash
cd policy
docker compose build                       # once, and after uv.lock changes
docker compose run --rm policy bash        # interactive shell
docker compose run --rm policy lerobot-record --help
```

- Arm serial ports and cameras are available inside the container, including after re-plugging.
- Data lives outside the image in `/mnt/nvme/store-picker-bot-data` (mounted at `/data`): Hugging Face cache, LeRobot datasets and calibration, training outputs. Override with `STORE_PICKER_DATA_DIR`.
- Secrets come from the repo root `.env`.

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
