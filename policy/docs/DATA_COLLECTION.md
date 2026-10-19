# Data collection

How to record teleoperated demonstrations for GR00T on the Jetson or a PC.
Commands are shown for both setups: **with Docker** on the Jetson, and **without Docker** on a PC after `source env.sh` (see [README.md](../README.md)).
Complete [ARM_SETUP.md](ARM_SETUP.md), [TELEOP_TEST.md](TELEOP_TEST.md) and [CAMERA_SETUP.md](CAMERA_SETUP.md) first (container built, both arms calibrated, teleoperation working, cameras checked).

Recording uses `data_collection/record.py`. LeRobot does the recording, exactly like `lerobot-record`
(same units and dataset format), but **you control every episode with keys**, and the script follows a
**recording plan**: which products, how many rounds, and how many episodes of each.

All commands run from the `policy/` folder.

## 1. Conventions

Keep these fixed for the whole project. Training and the pick service depend on them.

| Item | Value |
|---|---|
| Arms | SO-100 follower `picker_follower`, SO-100 leader `picker_leader` |
| Camera names | `gripper_cam`, `scene_cam` |
| Resolution / FPS | 640×480 at 30 FPS |
| Products | `policy/instructions.json`: item id → shelf and GR00T instruction |

The instruction is the sentence GR00T is trained on. It is fixed per product in `instructions.json`:

```json
"soup_can": {"shelf": "shelf_A", "instruction": "pick up the soup can and place it in the basket"}
```

The item ids are the same as in the store's `inventory.json`. The pick service reads the same file when
it runs the trained model, so the text it sends to GR00T always matches the one it was trained with.

- **To add a product,** add one line before recording it.
- **Never change the text of a product that already has recordings:** models trained on them would no
  longer match. `record.py` refuses to continue a dataset whose texts changed.

## 2. One-time setup

**Devices in `.env`.** Add the arm ports and camera paths to the repo root `.env`, so you don't have to
type them each time (values from [ARM_SETUP.md](ARM_SETUP.md) and [CAMERA_SETUP.md](CAMERA_SETUP.md)):

```
FOLLOWER_PORT=/dev/serial/by-id/usb-1a86_USB_Single_Serial_<serial>-if00
LEADER_PORT=/dev/serial/by-id/usb-1a86_USB_Single_Serial_<serial>-if00
GRIPPER_CAM=/dev/v4l/by-id/<gripper camera>-video-index0
SCENE_CAM=/dev/v4l/by-id/<scene camera>-video-index0
```

**Camera settings.** Create `data_collection/camera_settings.json` ([CAMERA_SETUP.md](CAMERA_SETUP.md), step 4).
`record.py` applies it at every start, so exposure, white balance and focus are the same in every session.
Without it, the script warns and the cameras stay automatic.

**Floor marks.** Mark the robot's normal stop point in front of each shelf with tape, plus the round offsets
(see "Rounds" below), so every base position can be repeated exactly.

## 3. Before each session

- Check that lighting, the shelves, the products and the basket look like in previous sessions.
- Put both arms in a similar pose: when the script starts, the follower moves straight to the leader's position.

## 4. Record

### Start a new dataset

```bash
# With Docker (Jetson)
docker compose run --rm policy python3 data_collection/record.py

# Without Docker (after `source env.sh`, see README.md)
python data_collection/record.py
```

It asks for the plan. Press Enter to accept a default in brackets:

```
Products (from instructions.json):
  1) soup_can       shelf_A    "pick up the soup can and place it in the basket"
  2) chips_pack     shelf_B    "pick up the chips pack and place it in the basket"
  ...
Products to record (numbers or ids, comma-separated, or all) [all]:
Rounds (base positions per shelf, 1-7) [5]:
Episodes per product per round [10]:

Plan: 4 product(s) x 5 round(s) x 10 episodes = 200 episodes
Start? [Y]:
```

The same choices can be given as options, without questions:
`--products soup_can,chips_pack --rounds 5 --per_round 10`.

The dataset is saved in `data_collection/dataset/dataset_v<N>_<DDMMYYYY_HHMMSS>/`, for example
`data_collection/dataset/dataset_v1_12102026_153045/`. The version N counts up by itself: the next new
dataset is `dataset_v2_...`. To choose another name, add `--name <name>` (letters, digits, `-` and `_`):
the folder is then `<name>_<DDMMYYYY_HHMMSS>/`.

### Rounds

The robot never stops at exactly the same spot in front of a shelf, so the demos must show the shelf
from slightly different base positions. A **round** is one base position. At each shelf the script goes
through the rounds in order, and in each round records every product of that shelf:

```
shelf_A:  round 1 (centre)  -> soup_can x 10, tea_box x 10
          round 2 (left)    -> soup_can x 10, tea_box x 10
          ...
shelf_B:  round 1 (centre)  -> ...
```

So the base moves only once per round. The script says where to put it, seen from behind the robot facing the shelf:

| Rounds | Base positions |
|---|---|
| 1–5 | centre, 3 cm left, 3 cm right, 3 cm closer and turned 3° left, 3 cm further and turned 3° right |
| 6–7 | centre, left, right, closer, further, turned left, turned right |

Change the distances with `--offset_cm` and `--turn_deg` when starting a dataset. Set them to the robot's real stop error:
drive it to a shelf about 10 times and measure how far apart the stops are.

### What one episode is

One full pick and place: from the rest pose, grasp the product, put it in the basket, return to the
rest pose, then press N. Every episode, for every product and shelf, starts and ends in the same rest pose.

### Keys

Press these in the terminal running the script. The last line of the terminal always shows the state,
for example `WAITING  shelf_A round 2/5 | soup_can 4/10 | plan 14/200`.

| Key | When | What it does |
|---|---|---|
| **Space** | Waiting | Start recording an episode |
| **N** | Recording | Save the episode |
| **X** | Recording | Cancel the episode: nothing is saved |
| **B** | Waiting | The episode just saved was bad: mark it, and it is recorded again |
| **Q** or **Ctrl+C** | Any time | Quit. An episode that is still recording is dropped. A save that is running finishes first. Ctrl+C twice stops at once |

> When the script ends, the follower's motors are switched off and the arm drops.
> Move the arm to its rest pose, or hold it, before you quit.

The cycle is: **Waiting → Space → Recording → N or X → Waiting** …

- While waiting, nothing is recorded, but the follower still copies the leader: put the product back
  and move the arm to its rest pose, then press Space.
- When the shelf, the round or the product changes, the script prints what to do (drive to the shelf,
  move the base, which product) before the next episode.
- Episodes have no time limit. After 60 s the status line warns you to press N or X
  (change it with `--warn_after_s`).
- Saving takes a while (the two videos are encoded on the CPU; longer episodes take longer).
  Wait for "Saved episode …" before pressing Space. `--streaming_encoding` encodes during the episode
  instead, so saving is almost instant, but it uses more CPU while recording.
- `--note "..."` saves a note for the session (operator, lighting, anything unusual).

### Continue later

Quit at any time, and continue on another day with the dataset's folder name:

```bash
# With Docker (Jetson)
docker compose run --rm policy python3 data_collection/record.py --resume dataset_v1_12102026_153045

# Without Docker (after `source env.sh`, see README.md)
python data_collection/record.py --resume dataset_v1_12102026_153045
```

It continues the plan exactly where it stopped. The script refuses to resume if the cameras, FPS or arm
differ from the ones the dataset was recorded with, or if a planned product's instruction changed.
When every product has all its episodes, it says "Plan complete" and ends.

### What is saved

Besides the LeRobot dataset itself:

- `meta/plan.json`: the plan, with each product's instruction at the time.
- `meta/episode_log.json`: for every saved episode, its product, shelf, round, base position, session
  and bad mark. It shows how many demos each base position has, and later which positions the
  trained model handles badly.

### Other options

`python3 data_collection/record.py --help` lists them all. Useful ones:

| Option | Use |
|---|---|
| `--fourcc MJPG` | If a camera only reaches 30 FPS with MJPG ([CAMERA_SETUP.md](CAMERA_SETUP.md), step 3) |
| `--follower_port`, `--leader_port`, `--gripper_cam`, `--scene_cam` | Override the values from `.env` |

## 5. How to record good demonstrations

- Start every episode from the same arm rest pose and end it the same way, after the product is in the basket.
- Press Space just before you start moving, and N as soon as the arm is back at rest, so episodes have no long idle parts.
- Within a round, vary the product's position and orientation on the shelf a little between episodes.
  The rounds cover the base position; the episodes cover the product position.
- If other products sit on the same shelf in the store, keep them there while recording: GR00T must
  learn that the instruction decides which product to take.
- Move smoothly and at a similar speed every time, with the same grasp for the same product. If several
  people record, practise together first.
- A calm re-grasp after a slip is a useful demo; keep it. Cancel (X) a chaotic one, a dropped product,
  or a person in the camera view. If you notice only after saving, press B.
- If the light changes during the day, spread the recording over the day.

## 6. Check the recording

Replay an episode on the follower arm (here episode 0 of one dataset):

```bash
# With Docker (Jetson)
docker compose run --rm policy lerobot-replay \
  --robot.type=so100_follower --robot.port=FOLLOWER_PORT --robot.id=picker_follower \
  --dataset.repo_id=local/dataset_v1_12102026_153045 \
  --dataset.root=data_collection/dataset/dataset_v1_12102026_153045 \
  --dataset.episode=0

# Without Docker (after `source env.sh`, see README.md)
lerobot-replay \
  --robot.type=so100_follower --robot.port=FOLLOWER_PORT --robot.id=picker_follower \
  --dataset.repo_id=local/dataset_v1_12102026_153045 \
  --dataset.root=data_collection/dataset/dataset_v1_12102026_153045 \
  --dataset.episode=0
```

Print the dataset summary (episodes, frames, tasks):

```bash
# With Docker (Jetson)
docker compose run --rm policy lerobot-edit-dataset \
  --repo_id=local/dataset_v1_12102026_153045 \
  --root=data_collection/dataset/dataset_v1_12102026_153045 \
  --operation.type=info

# Without Docker (after `source env.sh`, see README.md)
lerobot-edit-dataset \
  --repo_id=local/dataset_v1_12102026_153045 \
  --root=data_collection/dataset/dataset_v1_12102026_153045 \
  --operation.type=info
```

Episode videos can also be watched directly in `data_collection/dataset/<folder>/videos/`.

## 7. Remove bad episodes

Episodes marked with B stay in the dataset until you remove them, best once a recording day is done:

```bash
# With Docker (Jetson)
docker compose run --rm policy python3 data_collection/remove_bad_episodes.py dataset_v1_12102026_153045

# Without Docker (after `source env.sh`, see README.md)
python data_collection/remove_bad_episodes.py dataset_v1_12102026_153045
```

It lists the marked episodes and asks before removing them. The remaining episodes are renumbered from 0,
and the episode log is updated to match. The original is kept as `dataset_v1_12102026_153045_old/` next
to it: check the new dataset, then delete the `_old` folder.

## 8. Move the dataset to the training machine

Datasets are not stored in Git. Copy the folder to the same place on the training machine:

```bash
rsync -a --progress data_collection/dataset/dataset_v1_12102026_153045 \
  <user>@<training-pc>:<repo>/policy/data_collection/dataset/
```

Or upload it to the Hugging Face Hub (needs `HF_TOKEN` in the repo root `.env`, see "Getting started" in [README.md](../README.md)):

```bash
# With Docker (Jetson)
docker compose run --rm policy hf upload <hf-user>/dataset_v1_12102026_153045 \
  /workspace/policy/data_collection/dataset/dataset_v1_12102026_153045 --repo-type=dataset

# Without Docker (after `source env.sh`, see README.md)
hf upload <hf-user>/dataset_v1_12102026_153045 \
  data_collection/dataset/dataset_v1_12102026_153045 --repo-type=dataset
```

Next: [TRAINING.md](TRAINING.md).
