# Arm setup

Set up and calibrate the SO-100 follower (on the robot) and leader (for teleoperation) on the Jetson or a PC.
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

## 1. Find the arm ports

Power both arms and connect their USB cables to the Jetson (or the PC).

```bash
# With Docker (Jetson)
docker compose run --rm policy lerobot-find-port

# Without Docker (PC)
lerobot-find-port
```

Unplug one arm's USB when asked and press Enter; it prints that arm's port. Repeat for the other arm.

Prefer the stable names, which do not change when cables are re-plugged:

```bash
ls -l /dev/serial/by-id/
```

Each line ends with an arrow to the port it points to, e.g.
`usb-1a86_USB_Single_Serial_5A68012416-if00 -> ../../ttyACM0`: the one pointing to the port that
`lerobot-find-port` printed for an arm belongs to that arm.

Put both paths in the repo root `.env` (copy `.env.example` to `.env` first if it does not exist yet).
`record.py` reads them from there on both setups:

```
FOLLOWER_PORT=/dev/serial/by-id/usb-1a86_USB_Single_Serial_<follower serial>-if00
LEADER_PORT=/dev/serial/by-id/usb-1a86_USB_Single_Serial_<leader serial>-if00
```

- **With Docker:** replace `FOLLOWER_PORT` and `LEADER_PORT` in the commands below with these paths.
- **Without Docker:** load `.env` again, then the commands below work as they are:
  ```bash
  source env.sh
  echo "$FOLLOWER_PORT" "$LEADER_PORT"     # must print both paths
  ```
  Do this every time you change `.env`: a terminal only sees `.env` as it was when `env.sh` was last loaded.

## 2. Assign servo IDs (new arms only)

Skip this if the arms have already been set up.

```bash
# With Docker (Jetson)
docker compose run --rm policy lerobot-setup-motors \
  --robot.type=so100_follower --robot.port=FOLLOWER_PORT

docker compose run --rm policy lerobot-setup-motors \
  --teleop.type=so100_leader --teleop.port=LEADER_PORT

# Without Docker (PC)
lerobot-setup-motors \
  --robot.type=so100_follower --robot.port="$FOLLOWER_PORT"

lerobot-setup-motors \
  --teleop.type=so100_leader --teleop.port="$LEADER_PORT"
```

Connect one servo at a time when prompted (gripper first, down to the shoulder pan).

## 3. Calibrate

Follower:

```bash
# With Docker (Jetson)
docker compose run --rm policy lerobot-calibrate \
  --robot.type=so100_follower \
  --robot.port=FOLLOWER_PORT \
  --robot.id=picker_follower

# Without Docker (PC)
lerobot-calibrate \
  --robot.type=so100_follower \
  --robot.port="$FOLLOWER_PORT" \
  --robot.id=picker_follower
```

Leader:

```bash
# With Docker (Jetson)
docker compose run --rm policy lerobot-calibrate \
  --teleop.type=so100_leader \
  --teleop.port=LEADER_PORT \
  --teleop.id=picker_leader

# Without Docker (PC)
lerobot-calibrate \
  --teleop.type=so100_leader \
  --teleop.port="$LEADER_PORT" \
  --teleop.id=picker_leader
```

If the arm already has a calibration file, it first asks whether to use it: type `c` and press Enter to
calibrate again (Enter alone keeps the old one). Then, for each arm:

1. Move the arm to the middle of its range (joints roughly centred, gripper half open) and press Enter.
2. Move every joint slowly through its full range, then press Enter. The wrist roll is not needed here:
   step 4 calibrates it.

## 4. Calibrate the wrist roll

`lerobot-calibrate` does not measure the wrist roll's range: it treats it as a joint that turns all the way
round. The SO-100 wrist roll has a stop, and its zero is simply where the wrist was in step 3.1. Its reading
jumps from +180° to −180° half a turn from that zero, and if that point lies inside the wrist's travel,
the follower spins away when the leader passes it. This script measures the travel and puts the zero
exactly in its middle, so the jump sits behind the stop where it can never be reached:

```bash
# With Docker (Jetson)
docker compose run --rm policy python3 calibration/calibrate_wrist_roll.py --arm follower
docker compose run --rm policy python3 calibration/calibrate_wrist_roll.py --arm leader

# Without Docker (PC)
python calibration/calibrate_wrist_roll.py --arm follower
python calibration/calibrate_wrist_roll.py --arm leader
```

For each arm, the wrist roll motor turns off (hold the arm), then:

1. Turn the wrist roll by hand against one stop and press Enter.
2. Turn it slowly to the other stop and press Enter.
3. Check: turn it to both stops again. The angle must change smoothly, be close to 0° in the middle, and
   reach about ±170°. Press Enter, then Y to save.

It only changes the wrist roll in the arm's calibration file. Because both arms now use the middle of
their travel as zero, they agree on the wrist angle without matching poses by eye. The measured range is
also stored in the motor, so the follower cannot drive into its stop. Run this again every time
`lerobot-calibrate` is run, because that resets the wrist roll.

Calibration files are saved in the repo:

```
policy/calibration/robots/so_follower/picker_follower.json
policy/calibration/teleoperators/so_leader/picker_leader.json
```

Commit them once they are good: every machine that uses these arms then gets the same calibration
with `git pull`, without calibrating again. A calibration belongs to the physical arm (its motors), not to
the computer. Recalibrate only if a servo is replaced, an arm is rebuilt, or teleoperation looks offset.

## Notes

- Always use the same IDs, `picker_follower` and `picker_leader`. LeRobot finds each arm's calibration by its ID, and recording and the pick service use the same names.

Next: [TELEOP_TEST.md](TELEOP_TEST.md).
