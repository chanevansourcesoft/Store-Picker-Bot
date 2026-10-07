# Data collection

How to record teleoperated demonstrations for GR00T on the Jetson, using LeRobot's own scripts.
Complete [ARM_SETUP.md](ARM_SETUP.md) and [CAMERA_SETUP.md](CAMERA_SETUP.md) first (container built, both arms calibrated, teleoperation working, cameras checked).

All commands run from the `policy/` folder. Recorded data is stored in
`/mnt/nvme/store-picker-bot-data/lerobot/<hf-user>/<dataset-name>` and can be pushed to the Hugging Face Hub.

## 1. Conventions

Keep these fixed for the whole project. Training and the pick service depend on them.

| Item | Value |
|---|---|
| Follower ID | `picker_follower` |
| Leader ID | `picker_leader` |
| Camera names | `wrist`, `scene` |
| Resolution / FPS | 640×480 at 30 FPS |
| Dataset | `<hf-user>/store_picker_picks` (one dataset for all products) |
| Task text | One sentence per product, e.g. `Pick the tea packet from bin 1 and place it on the tray.` |

The task text is the language instruction GR00T is trained on. Write one sentence per product, keep it identical
for every episode of that product, and reuse the exact same sentence later as the pick service's `instruction`.

## 2. Before each session

- If the camera exposure and focus were set manually, apply the settings again ([CAMERA_SETUP.md](CAMERA_SETUP.md), step 4).
- Check that lighting, the shelf, and the dock position match previous sessions.

## 3. Record

`FOLLOWER_PORT` and `LEADER_PORT` come from [ARM_SETUP.md](ARM_SETUP.md); `WRIST_CAM` and `SCENE_CAM` from [CAMERA_SETUP.md](CAMERA_SETUP.md).

Record one product at a time. The first run creates the dataset:

```bash
docker compose run --rm policy lerobot-record \
  --robot.type=so100_follower \
  --robot.port=FOLLOWER_PORT \
  --robot.id=picker_follower \
  --robot.cameras="{ wrist: {type: opencv, index_or_path: WRIST_CAM, width: 640, height: 480, fps: 30}, scene: {type: opencv, index_or_path: SCENE_CAM, width: 640, height: 480, fps: 30} }" \
  --teleop.type=so100_leader \
  --teleop.port=LEADER_PORT \
  --teleop.id=picker_leader \
  --dataset.repo_id=<hf-user>/store_picker_picks \
  --dataset.single_task="Pick the tea packet from bin 1 and place it on the tray." \
  --dataset.num_episodes=10 \
  --dataset.episode_time_s=20 \
  --dataset.reset_time_s=10 \
  --dataset.push_to_hub=false \
  --play_sounds=false
```

To add more episodes (same or another product) to the same dataset, run the same command with the new
`--dataset.single_task` and add `--resume=true`.

Each episode runs for `episode_time_s`, followed by a reset period of `reset_time_s` to put the item back.

### Keyboard controls

Press these in the terminal running the command:

| Key | Action |
|---|---|
| → (right arrow) | End the current episode or reset period early |
| ← (left arrow) | Discard the current episode and record it again |
| Esc | Stop recording and save the dataset |

If the keys do not respond, run the command with the display disabled so LeRobot reads keys from the terminal:
`DISPLAY= docker compose run --rm policy lerobot-record ...`

## 4. How to record good demonstrations

- Start every episode from the same arm rest pose and end it the same way, after the item is on the tray.
- Vary the item's position and orientation inside its bin between episodes; this is what the policy has to handle.
- Move smoothly and at a steady speed; avoid pauses and corrections mid-grasp.
- Keep lighting, camera mounts, the shelf, and the dock position unchanged between sessions.
- Re-record (←) any episode with a failed grasp, a dropped item, or a person in the camera view.
- Record in sessions of about 10 episodes and check them before continuing.

## 5. Check the recording

Replay an episode on the follower arm:

```bash
docker compose run --rm policy lerobot-replay \
  --robot.type=so100_follower --robot.port=FOLLOWER_PORT --robot.id=picker_follower \
  --dataset.repo_id=<hf-user>/store_picker_picks \
  --dataset.episode=0
```

Print the dataset summary (episodes, frames, tasks):

```bash
docker compose run --rm policy lerobot-edit-dataset \
  --repo_id=<hf-user>/store_picker_picks \
  --operation.type=info
```

Episode videos can also be watched directly in
`/mnt/nvme/store-picker-bot-data/lerobot/<hf-user>/store_picker_picks/videos/`.

## 6. Remove bad episodes

```bash
docker compose run --rm policy lerobot-edit-dataset \
  --repo_id=<hf-user>/store_picker_picks \
  --operation.type=delete_episodes \
  --operation.episode_indices="[3, 7]"
```

## 7. Upload to the Hugging Face Hub

Requires `HF_TOKEN` in the repo root `.env` (see "Getting started" in [README.md](../README.md)). Upload the finished dataset:

```bash
docker compose run --rm policy hf upload <hf-user>/store_picker_picks \
  /data/lerobot/<hf-user>/store_picker_picks --repo-type=dataset
```

The Nebius training machine downloads the dataset from the Hub.
