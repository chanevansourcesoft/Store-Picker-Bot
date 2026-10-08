# Arm setup

Set up and calibrate the SO-100 follower (on the robot) and leader (for teleoperation) on the Jetson.
Complete "Getting started" in [README.md](../README.md) first. All commands run from the `policy/` folder.

## 1. Find the arm ports

Power both arms and connect their USB cables to the Jetson.

```bash
docker compose run --rm policy lerobot-find-port
```

Unplug one arm's USB when asked and press Enter; it prints that arm's port. Repeat for the other arm.

Prefer the stable names, which do not change when cables are re-plugged:

```bash
ls -l /dev/serial/by-id/
```

Use those paths (e.g. `/dev/serial/by-id/usb-1a86_USB_Single_Serial_<serial>-if00`) wherever `FOLLOWER_PORT` and `LEADER_PORT` appear below.

## 2. Assign servo IDs (new arms only)

Skip this if the arms have already been set up.

```bash
docker compose run --rm policy lerobot-setup-motors \
  --robot.type=so100_follower --robot.port=FOLLOWER_PORT

docker compose run --rm policy lerobot-setup-motors \
  --teleop.type=so100_leader --teleop.port=LEADER_PORT
```

Connect one servo at a time when prompted (gripper first, down to the shoulder pan).

## 3. Calibrate

Follower:

```bash
docker compose run --rm policy lerobot-calibrate \
  --robot.type=so100_follower \
  --robot.port=FOLLOWER_PORT \
  --robot.id=picker_follower
```

Leader:

```bash
docker compose run --rm policy lerobot-calibrate \
  --teleop.type=so100_leader \
  --teleop.port=LEADER_PORT \
  --teleop.id=picker_leader
```

For each arm:

1. Move the arm to the middle of its range (joints roughly centred, gripper half open) and press Enter.
2. Move every joint slowly through its full range, then press Enter.

Calibration files are saved to:

```
/mnt/nvme/store-picker-bot-data/lerobot/calibration/robots/so_follower/picker_follower.json
/mnt/nvme/store-picker-bot-data/lerobot/calibration/teleoperators/so_leader/picker_leader.json
```

Back them up once they are good. Recalibrate only if a servo is replaced, an arm is rebuilt, or teleoperation looks offset.

## Notes

- Always use the same IDs, `picker_follower` and `picker_leader`. LeRobot finds each arm's calibration by its ID, and recording and the pick service use the same names.

Next: [TELEOP_TEST.md](TELEOP_TEST.md).
