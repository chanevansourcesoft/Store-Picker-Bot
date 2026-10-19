# Camera setup

Set up the gripper and scene cameras on the Jetson or a PC.
Only the policy process opens the cameras.
Complete "Getting started" in [README.md](../README.md) first. All commands run from the `policy/` folder.

## Start a terminal

Every command in this guide is shown twice. Use the one for your setup.

**With Docker (Jetson):** go to the `policy/` folder; each command starts with `docker compose run --rm policy`.

```bash
cd path/to/Store-Picker-Bot/policy
```

**Without Docker (PC):** in every new terminal, activate the Python environment, then load `env.sh`
(it loads `.env` and the calibration folder, and goes to `policy/`). Setup: "Getting started without
Docker" in [README.md](../README.md).

```bash
conda activate lerobot_env      # only with a conda env; skip it with uv (policy/.venv)
source path/to/Store-Picker-Bot/policy/env.sh
```

## 1. Mount the cameras

| Camera | Name | Mounting |
|---|---|---|
| Gripper | `gripper_cam` | On the follower's gripper, looking along it so both fingers and the item being grasped are visible |
| Scene | `scene_cam` | Overhead, looking down on the work area, rigidly fixed to the robot, seeing the bins, the gripper, and the tray |

Both cameras move with the robot, so their views are the same every time the robot docks.
Once mounted, do not move them: a policy trained on one camera position degrades if the view changes.

## 2. Find the cameras

Connect both cameras to the Jetson (or the PC), then:

```bash
# With Docker (Jetson)
docker compose run --rm policy lerobot-find-cameras opencv

# Without Docker (PC)
lerobot-find-cameras opencv
```

It lists the cameras and saves a test image from each to `policy/outputs/captured_images` (ignored by Git).
Open the images to see which is which.

Prefer the stable names, which do not change when cables are re-plugged:

```bash
ls -l /dev/v4l/by-id/
```

Use the `...-video-index0` entries wherever `GRIPPER_CAM` and `SCENE_CAM` appear in the other guides,
and put them in the repo root `.env` ([DATA_COLLECTION.md](DATA_COLLECTION.md), step 2).

## 3. Check supported formats

Both cameras must support 640×480 at 30 FPS:

```bash
# With Docker (Jetson)
docker compose run --rm policy v4l2-ctl -d GRIPPER_CAM --list-formats-ext
docker compose run --rm policy v4l2-ctl -d SCENE_CAM --list-formats-ext

# Without Docker (PC)
v4l2-ctl -d GRIPPER_CAM --list-formats-ext
v4l2-ctl -d SCENE_CAM --list-formats-ext
```

Look for `640x480` with `30.000 fps`, ideally under `MJPG`, which uses less USB bandwidth when both cameras run together.
If a camera only reaches 30 FPS with MJPG, record with `--fourcc MJPG` ([DATA_COLLECTION.md](DATA_COLLECTION.md)).

## 4. Fix exposure and focus

Automatic exposure, white balance, and focus make the images change between episodes. List a camera's controls:

```bash
# With Docker (Jetson)
docker compose run --rm policy v4l2-ctl -d SCENE_CAM --list-ctrls

# Without Docker (PC)
v4l2-ctl -d SCENE_CAM --list-ctrls
```

If the camera has them, switch to manual and set values that look good under the shelf lighting, for example:

```bash
# With Docker (Jetson)
docker compose run --rm policy v4l2-ctl -d SCENE_CAM \
  --set-ctrl=auto_exposure=1 \
  --set-ctrl=white_balance_automatic=0 \
  --set-ctrl=focus_automatic_continuous=0

# Without Docker (PC)
v4l2-ctl -d SCENE_CAM \
  --set-ctrl=auto_exposure=1 \
  --set-ctrl=white_balance_automatic=0 \
  --set-ctrl=focus_automatic_continuous=0
```

Control names and values differ between camera models; use the names printed by `--list-ctrls`.
Try values until the image looks good, then check it in step 5.

These settings reset when the camera is unplugged or the computer reboots. To have them applied
automatically, save them in `data_collection/camera_settings.json`: `record.py` sets them on both
cameras at every start, in the order written (put each `..._automatic` or `auto_...` control before the
manual value it unlocks). Start from the example:

```bash
cp data_collection/camera_settings.example.json data_collection/camera_settings.json
```

Then edit the values (and remove controls your cameras don't have). Commit the file: it belongs to these
cameras, like the arm calibration. If a control name is wrong, `record.py` stops before it starts and
says which one.

## 5. Check the views

Take new test images with the arm in its rest pose and an item in each bin:

```bash
# With Docker (Jetson)
docker compose run --rm policy lerobot-find-cameras opencv

# Without Docker (PC)
lerobot-find-cameras opencv
```

Check in `policy/outputs/captured_images` that:

- the gripper image shows both gripper fingers,
- the scene image shows every bin, the gripper, and the tray,
- nothing is blurred, too dark, or washed out.

## Notes

- Always use the camera names `gripper_cam` and `scene_cam` (`record.py` sets them). They become feature names in the dataset
  (`observation.images.gripper_cam`, `observation.images.scene_cam`), and training and the pick service rely on them.
- Keep lighting, the shelf, and the dock position the same across recording sessions.

Next: [DATA_COLLECTION.md](DATA_COLLECTION.md).
