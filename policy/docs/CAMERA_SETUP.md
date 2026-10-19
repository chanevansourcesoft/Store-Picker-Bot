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
| Scene | `scene_cam` | Overhead, looking down on the work area, rigidly fixed to the robot, seeing the shelf, the gripper, and the basket |

Both cameras move with the robot, so their views are the same every time the robot stops at a shelf.
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

Use the `...-video-index0` entries for the cameras: put them in the repo root `.env` as `GRIPPER_CAM` and
`SCENE_CAM` ([DATA_COLLECTION.md](DATA_COLLECTION.md), step 2). With Docker, replace `GRIPPER_CAM` and
`SCENE_CAM` in the commands with these paths. Without Docker, run `source env.sh` again (every time `.env`
changes); the commands then use `"$GRIPPER_CAM"` and `"$SCENE_CAM"` as they are. Check with
`echo "$GRIPPER_CAM" "$SCENE_CAM"`.

## 3. Check supported formats

Both cameras must support 640×480 at 30 FPS:

```bash
# With Docker (Jetson)
docker compose run --rm policy v4l2-ctl -d GRIPPER_CAM --list-formats-ext
docker compose run --rm policy v4l2-ctl -d SCENE_CAM --list-formats-ext

# Without Docker (PC)
v4l2-ctl -d "$GRIPPER_CAM" --list-formats-ext
v4l2-ctl -d "$SCENE_CAM" --list-formats-ext
```

Look for `640x480` with `30.000 fps`, ideally under `MJPG`, which uses less USB bandwidth when both cameras run together.
If a camera only reaches 30 FPS with MJPG, record with `--fourcc MJPG` ([DATA_COLLECTION.md](DATA_COLLECTION.md)).

Some cameras have no exact 30 fps mode, only a slightly faster one such as `30.500 fps`. LeRobot accepts
only an exact match, so `record.py` then opens that camera at its own rate and still saves 30 frames per
second; it prints a line saying so. A camera slower than 30 fps is refused. Avoid a format whose only
640x480 mode is much faster (for example `MJPG` at `120 fps`): the short exposure makes the image darker.

## 4. Lock the image settings

By default the cameras adjust exposure (brightness) and white balance (colour) by themselves, all the
time. Then the same shelf looks different from one episode to the next, which makes training harder.
So you fix these settings once, in a file, and `record.py` applies them every time it starts.

Do this with the cameras mounted (step 1), in the lighting you will record in.

### 4.1 See what each camera can change

```bash
# With Docker (Jetson)
docker compose run --rm policy v4l2-ctl -d GRIPPER_CAM --list-ctrls
docker compose run --rm policy v4l2-ctl -d SCENE_CAM --list-ctrls

# Without Docker (PC)
v4l2-ctl -d "$GRIPPER_CAM" --list-ctrls
v4l2-ctl -d "$SCENE_CAM" --list-ctrls
```

Each line is one setting (a "control"), with its current value and its allowed range, for example:

```
auto_exposure 0x009a0901 (menu) : min=0 max=3 default=3 value=3 (Aperture Priority Mode)
exposure_time_absolute 0x009a0902 (int) : min=1 max=5000 step=1 default=157 value=157 flags=inactive
```

### 4.2 Create the settings file

```bash
cp data_collection/camera_settings.example.json data_collection/camera_settings.json
```

It has one block per camera (`gripper_cam` and `scene_cam`). The example has the controls most USB
cameras have:

| Control | Set to | Why |
|---|---|---|
| `power_line_frequency` | `1` = 50 Hz (India, Europe), `2` = 60 Hz (USA) | Matches the mains power, so lamps do not flicker as stripes in the image |
| `exposure_dynamic_framerate` | `0` | Otherwise the camera slows below 30 fps in dim light to expose longer |
| `auto_exposure` | `1` = manual (`3` = automatic) | Turns automatic exposure off, so the value below is used |
| `exposure_time_absolute` | e.g. `157` | Exposure time in 0.1 ms (`157` = 15.7 ms). Higher = brighter. Stay at about `250` or less: a longer exposure makes some cameras slower than 30 fps |
| `gain` | `0` to about `60` | Amplifies the image; higher = brighter but noisier |
| `white_balance_automatic` | `0` | Turns automatic colour correction off |
| `white_balance_temperature` | e.g. `4600` | Colour: lower = warmer (more orange), higher = colder (more blue) |
| `backlight_compensation` | `0` | Otherwise the camera brightens the image when the background is bright |

Remove any line whose control is not in your camera's list from 4.1 (for example, many cameras have no
focus control). Keep the order: each automatic mode is switched off before its manual value.

### 4.3 Find good values

Try a value on the live camera, then take test images and look at them:

```bash
# With Docker (Jetson)
docker compose run --rm policy v4l2-ctl -d SCENE_CAM --set-ctrl=auto_exposure=1 --set-ctrl=exposure_time_absolute=200
docker compose run --rm policy lerobot-find-cameras opencv

# Without Docker (PC)
v4l2-ctl -d "$SCENE_CAM" --set-ctrl=auto_exposure=1 --set-ctrl=exposure_time_absolute=200
lerobot-find-cameras opencv
```

The images are saved in `policy/outputs/captured_images`. Adjust and repeat:

- **Too dark:** raise `exposure_time_absolute` up to about `250`, then raise `gain`. Better still: more light
  on the shelf, which gives sharper, less noisy images than a high gain.
- **Too bright or washed out:** lower `exposure_time_absolute`.
- **Colours look wrong:** change `white_balance_temperature` (try `4000` for warmer, `5500` for colder).
- **Moving parts are blurred:** lower `exposure_time_absolute` and raise `gain` instead.

Do the same for the gripper camera (`GRIPPER_CAM`).

### 4.4 Save the values

Write the values you chose into `data_collection/camera_settings.json`. When `record.py` starts, it
prints one line per camera, for example:

```
Camera settings applied to scene_cam: power_line_frequency=1, exposure_dynamic_framerate=0, auto_exposure=1, ...
```

If a control name is wrong or missing on the camera, `record.py` stops before recording and says which
one: remove or fix that line. It then measures how many new frames each camera really delivers, for
example `scene_cam: 30 new frames per second`. If a camera is slower than 30, it warns and asks before
recording: lower that camera's `exposure_time_absolute` and raise `gain` instead. Commit the file: it belongs to these cameras, like the arm calibration.
If the lighting changes for good (new lamps, different room), repeat 4.3 and 4.4.

## 5. Check the views

Take new test images with the arm in its rest pose, in front of a shelf with the products on it:

```bash
# With Docker (Jetson)
docker compose run --rm policy lerobot-find-cameras opencv

# Without Docker (PC)
lerobot-find-cameras opencv
```

Check in `policy/outputs/captured_images` that:

- the gripper image shows both gripper fingers,
- the scene image shows the products on the shelf, the gripper, and the basket,
- nothing is blurred, too dark, or washed out.

## Notes

- Always use the camera names `gripper_cam` and `scene_cam` (`record.py` sets them). They become feature names in the dataset
  (`observation.images.gripper_cam`, `observation.images.scene_cam`), and training and the pick service rely on them.
- Keep lighting, the shelves, and the robot's stop positions the same across recording sessions.

Next: [DATA_COLLECTION.md](DATA_COLLECTION.md).
