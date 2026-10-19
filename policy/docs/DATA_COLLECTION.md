# Data collection

How to record teleoperated demonstrations for GR00T on the Jetson.
Complete [ARM_SETUP.md](ARM_SETUP.md), [TELEOP_TEST.md](TELEOP_TEST.md) and [CAMERA_SETUP.md](CAMERA_SETUP.md) first (container built, both arms calibrated, teleoperation working, cameras checked).

Recording uses `data_collection/record.py`. LeRobot does the recording, exactly like `lerobot-record`
(same units and dataset format), but **you control every episode with keys**: an episode starts and
ends only when you press a key, and a bad episode can be cancelled at once.

All commands run from the `policy/` folder.

## 1. Conventions

Keep these fixed for the whole project. Training and the pick service depend on them.

| Item | Value |
|---|---|
| Arms | SO-100 follower `picker_follower`, SO-100 leader `picker_leader` |
| Camera names | `gripper_cam`, `scene_cam` |
| Resolution / FPS | 640×480 at 30 FPS |
| Task text | From `policy/instructions.json`, one per item, e.g. `soup_can` → `pick up the soup can and place it in the basket` |

The task text is the language instruction GR00T is trained on. It is fixed per item in
`instructions.json`, so every episode of a product gets the same sentence, and the pick service reads
the same file when it runs the trained model.

## 2. One-time setup: devices in `.env`

Add the arm ports and camera paths to the repo root `.env`, so you don't have to type them each time
(values from [ARM_SETUP.md](ARM_SETUP.md) and [CAMERA_SETUP.md](CAMERA_SETUP.md)):

```
FOLLOWER_PORT=/dev/serial/by-id/usb-1a86_USB_Single_Serial_<serial>-if00
LEADER_PORT=/dev/serial/by-id/usb-1a86_USB_Single_Serial_<serial>-if00
GRIPPER_CAM=/dev/v4l/by-id/<gripper camera>-video-index0
SCENE_CAM=/dev/v4l/by-id/<scene camera>-video-index0
```

## 3. Before each session

- If the camera exposure and focus were set manually, apply the settings again ([CAMERA_SETUP.md](CAMERA_SETUP.md), step 4).
- Check that lighting, the shelf, and the dock position match previous sessions.
- Put both arms in a similar pose: when the script starts, the follower moves straight to the leader's position.

## 4. Record

Start a **new dataset** by giving it a name and the item you pick:

```bash
docker compose run --rm policy python3 data_collection/record.py \
  --name soup_can \
  --item soup_can
```

The dataset is saved in `data_collection/dataset/<DDMMYYYY_HHMMSS>_<name>/`, for example
`data_collection/dataset/12102026_153045_soup_can/`. The name may contain letters, digits, `-` and `_`.

`--item` takes an item id (the same ids as the store's `inventory.json`) and reads the GR00T
instruction for it from `policy/instructions.json`, for example `soup_can` → "pick up the soup can and place it in the basket".
The robot uses the same file when it picks, so the text it sends to GR00T always matches the one it
was trained with. To add a product, add one line to that file before recording it. Do not change
the text of a product that already has recordings: retrained models would no longer match them.
`--task "..."` sets a custom text instead of `--item`, for experiments.

Each episode is one full pick and place: from the rest pose, grasp the item, put it in the basket,
return to the rest pose, then press N.

To **add episodes to an existing dataset** (after a break, or another day), give its folder name instead of `--name`:

```bash
docker compose run --rm policy python3 data_collection/record.py \
  --resume 12102026_153045_soup_can \
  --item soup_can
```

Episode numbers continue where the dataset stopped. The script refuses to resume if the cameras,
FPS or arm differ from the ones the dataset was recorded with.

Use one dataset per product or per session as you like; datasets can be merged before training
([TRAINING.md](TRAINING.md)).

### Keys

Press these in the terminal running the script. The last line of the terminal always shows the state.

| Key | When | What it does |
|---|---|---|
| **Space** | Waiting | Start recording an episode |
| **N** | Recording | Save the episode |
| **X** | Recording | Cancel the episode: nothing is saved, the episode number stays the same |
| **Q** | Any time | Quit. An episode that is still recording is dropped, never saved |

> When the script ends (Q or Ctrl+C), the follower's motors are switched off and the arm drops.
> Move the arm to its rest pose, or hold it, before you quit.

The cycle is: **Waiting → Space → Recording → N or X → Waiting** …

- While waiting, nothing is recorded, but the follower still copies the leader: put the item back
  and move the arm to its rest pose, then press Space.
- Episodes have no time limit. After 60 s the status line warns you to press N or X
  (change it with `--warn_after_s`).
- Saving takes a while (the two videos are encoded on the Jetson's CPU; longer episodes take longer).
  Wait for "Saved episode …" before pressing Space. `--streaming_encoding` encodes during the episode
  instead, so saving is almost instant, but it uses more CPU while recording.

### Other options

`python3 data_collection/record.py --help` lists them all. Useful ones:

| Option | Use |
|---|---|
| `--fourcc MJPG` | If a camera only reaches 30 FPS with MJPG ([CAMERA_SETUP.md](CAMERA_SETUP.md), step 3) |
| `--follower_port`, `--leader_port`, `--gripper_cam`, `--scene_cam` | Override the values from `.env` |

## 5. How to record good demonstrations

- Start every episode from the same arm rest pose and end it the same way, after the item is on the tray.
- Press Space just before you start moving, and N as soon as the arm is back at rest, so episodes have no long idle parts.
- Vary the item's position and orientation inside its bin between episodes; this is what the policy has to handle.
- Move smoothly and at a steady speed; avoid pauses and corrections mid-grasp.
- Keep lighting, camera mounts, the shelf, and the dock position unchanged between sessions.
- Cancel (X) any episode with a failed grasp, a dropped item, or a person in the camera view.
- Record in sessions of about 10 episodes and check them before continuing.

## 6. Check the recording

Replay an episode on the follower arm (here episode 0 of one dataset):

```bash
docker compose run --rm policy lerobot-replay \
  --robot.type=so100_follower --robot.port=FOLLOWER_PORT --robot.id=picker_follower \
  --dataset.repo_id=local/12102026_153045_soup_can \
  --dataset.root=data_collection/dataset/12102026_153045_soup_can \
  --dataset.episode=0
```

Print the dataset summary (episodes, frames, tasks):

```bash
docker compose run --rm policy lerobot-edit-dataset \
  --repo_id=local/12102026_153045_soup_can \
  --root=data_collection/dataset/12102026_153045_soup_can \
  --operation.type=info
```

Episode videos can also be watched directly in `data_collection/dataset/<folder>/videos/`.

## 7. Remove bad episodes

If a bad episode was saved by mistake (here episodes 3 and 7):

```bash
docker compose run --rm policy lerobot-edit-dataset \
  --repo_id=local/12102026_153045_soup_can \
  --root=data_collection/dataset/12102026_153045_soup_can \
  --new_root=data_collection/dataset/12102026_153045_soup_can \
  --operation.type=delete_episodes \
  --operation.episode_indices="[3, 7]"
```

`--new_root` must be the same as `--root`, otherwise LeRobot writes the result somewhere else.
The original is kept as `12102026_153045_soup_can_old/` next to it: check the new dataset, then delete
the `_old` folder. The remaining episodes are renumbered from 0.

## 8. Move the dataset to the training machine

Datasets are not stored in Git. Copy the folder to the same place on the training machine:

```bash
rsync -a --progress data_collection/dataset/12102026_153045_soup_can \
  <user>@<training-pc>:<repo>/policy/data_collection/dataset/
```

Or upload it to the Hugging Face Hub (needs `HF_TOKEN` in the repo root `.env`, see "Getting started" in [README.md](../README.md)):

```bash
docker compose run --rm policy hf upload <hf-user>/12102026_153045_soup_can \
  /workspace/policy/data_collection/dataset/12102026_153045_soup_can --repo-type=dataset
```

Next: [TRAINING.md](TRAINING.md).
