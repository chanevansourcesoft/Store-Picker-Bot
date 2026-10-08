# Teleoperation test

Check that the follower mirrors the leader before setting up cameras and recording.
Complete [ARM_SETUP.md](ARM_SETUP.md) first (both arms calibrated). All commands run from the `policy/` folder.

## 1. Before you start

- Both controller boards are connected to the Jetson, and both arms are powered.
- Both calibration files exist:
  ```bash
  ls /mnt/nvme/store-picker-bot-data/lerobot/calibration/robots/so_follower/picker_follower.json \
     /mnt/nvme/store-picker-bot-data/lerobot/calibration/teleoperators/so_leader/picker_leader.json
  ```
- Look up which board is on which arm:
  ```bash
  ls -l /dev/serial/by-id/
  ```
  Use those paths for `FOLLOWER_PORT` and `LEADER_PORT`.
- Put both arms in a similar pose. When teleoperation starts, the follower moves straight to the leader's position at full speed.
- Keep hands and objects clear of the follower.

## 2. Run

```bash
docker compose run --rm policy lerobot-teleoperate \
  --robot.type=so100_follower --robot.port=FOLLOWER_PORT --robot.id=picker_follower \
  --teleop.type=so100_leader --teleop.port=LEADER_PORT --teleop.id=picker_leader
```

Move the leader by hand; the follower copies it. Stop with Ctrl+C.

To stop automatically after a set time, add `--teleop_time_s=60`.

## 3. What to check

Move one joint at a time on the leader, then all together:

| Check | Expected |
|---|---|
| Each joint | Moves in the same direction as the leader, by about the same angle |
| Rest pose | Both arms look the same when the leader is at rest |
| Gripper | Opens and closes fully |
| Holding | The follower holds its pose without sagging when the leader is still |
| Servos | No buzzing, clicking, or servos getting hot |

## 4. Troubleshooting

| Problem | Likely cause | Fix |
|---|---|---|
| Error that a motor is missing or not responding | Servo unpowered, loose cable, or wrong ID | Check the arm's power supply and servo cables; run `lerobot-setup-motors` for that arm ([ARM_SETUP.md](ARM_SETUP.md), step 2) |
| A joint moves the wrong way or is offset | Calibration is wrong | Recalibrate that arm ([ARM_SETUP.md](ARM_SETUP.md), step 3) |
| A joint moves freely or does not follow | Loose horn screw, or a stripped gear in the servo | Tighten the horn screw; replace the servo if the gears slip |
| Follower sags or stalls when lifting | Supply voltage too low for the servos | Use the supply rated on the follower's servos |
| Port not found | Board unplugged, or the path changed | Check `ls -l /dev/serial/by-id/` |

Next: [CAMERA_SETUP.md](CAMERA_SETUP.md).
