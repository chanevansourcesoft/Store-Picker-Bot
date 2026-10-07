# Camera setup

Set up the wrist and scene cameras on the Jetson. Only the policy process opens the cameras.
Complete "Getting started" in [README.md](../README.md) first. All commands run from the `policy/` folder.

## 1. Mount the cameras

| Camera | Name | Mounting |
|---|---|---|
| Wrist | `wrist` | On the follower's wrist, looking along the gripper so both fingers and the item being grasped are visible |
| Scene | `scene` | High and behind the arm, rigidly fixed to the robot, seeing the bins, the gripper, and the tray |

Both cameras move with the robot, so their views are the same every time the robot docks.
Once mounted, do not move them: a policy trained on one camera position degrades if the view changes.

## 2. Find the cameras

Connect both cameras to the Jetson, then:

```bash
docker compose run --rm policy lerobot-find-cameras opencv
```

It lists the cameras and saves a test image from each to `policy/outputs/captured_images` (ignored by Git).
Open the images to see which is which.

Prefer the stable names, which do not change when cables are re-plugged:

```bash
ls -l /dev/v4l/by-id/
```

Use the `...-video-index0` entries wherever `WRIST_CAM` and `SCENE_CAM` appear in the other guides.

## 3. Check supported formats

Both cameras must support 640×480 at 30 FPS:

```bash
docker compose run --rm policy v4l2-ctl -d WRIST_CAM --list-formats-ext
docker compose run --rm policy v4l2-ctl -d SCENE_CAM --list-formats-ext
```

Look for `640x480` with `30.000 fps`, ideally under `MJPG`, which uses less USB bandwidth when both cameras run together.
If a camera only reaches 30 FPS with MJPG, add `fourcc: MJPG` to its entry in `--robot.cameras`.

## 4. Fix exposure and focus

Automatic exposure, white balance, and focus make the images change between episodes. List a camera's controls:

```bash
docker compose run --rm policy v4l2-ctl -d SCENE_CAM --list-ctrls
```

If the camera has them, switch to manual and set values that look good under the shelf lighting, for example:

```bash
docker compose run --rm policy v4l2-ctl -d SCENE_CAM \
  --set-ctrl=auto_exposure=1 \
  --set-ctrl=white_balance_automatic=0 \
  --set-ctrl=focus_automatic_continuous=0
```

Control names and values differ between camera models; use the names printed by `--list-ctrls`.
These settings reset when the camera is unplugged or the Jetson reboots, so apply them before each recording session.

## 5. Check the views

Take new test images with the arm in its rest pose and an item in each bin:

```bash
docker compose run --rm policy lerobot-find-cameras opencv
```

Check in `policy/outputs/captured_images` that:

- the wrist image shows both gripper fingers,
- the scene image shows every bin, the gripper, and the tray,
- nothing is blurred, too dark, or washed out.

## Notes

- Always use the camera names `wrist` and `scene`. They become feature names in the dataset, and training and the pick service rely on them.
- Keep lighting, the shelf, and the dock position the same across recording sessions.

Next: [DATA_COLLECTION.md](DATA_COLLECTION.md).
