"""
Calibrate the wrist roll of an SO-100 arm: measure its travel between the two stops and centre it.

lerobot-calibrate sets the wrist roll's zero to wherever the wrist happens to be in the
"middle" step, and gives it the full turn (0..4095) instead of a measured range. The SO-100
wrist roll has a stop, so its travel is a little less than one turn. With a zero set by eye,
the encoder seam (+180 -> -180 degrees) can fall inside that travel, and the follower spins
away when the leader passes it.

This script fixes that for one arm:
  1. turns off the wrist roll motor, so you can turn it by hand,
  2. records its position while you turn it slowly from one stop to the other,
  3. puts the zero exactly in the middle of the travel (the seam then sits behind the stop)
     and stores the measured min and max, also as the motor's position limits,
  4. lets you check the result, then saves it in the arm's LeRobot calibration file.

Run it after lerobot-calibrate, for both arms, and again whenever lerobot-calibrate is run.
The other joints are not changed.

Usage (from policy/, on the Jetson):
    docker compose run --rm policy python3 calibration/calibrate_wrist_roll.py --arm follower
    docker compose run --rm policy python3 calibration/calibrate_wrist_roll.py --arm leader

Guide: policy/docs/ARM_SETUP.md
"""

import argparse
import os
import select
import sys
import time

from lerobot.motors import MotorCalibration
from lerobot.robots import make_robot_from_config
from lerobot.robots.so_follower import SO100FollowerConfig
from lerobot.teleoperators import make_teleoperator_from_config
from lerobot.teleoperators.so_leader import SO100LeaderConfig

# Same IDs as data_collection/record.py: LeRobot finds each arm's calibration file by its ID.
FOLLOWER_ID = "picker_follower"
LEADER_ID = "picker_leader"
MOTOR = "wrist_roll"
TICKS = 4096  # STS3215 encoder steps per turn
HALF = 2047  # LeRobot's centre position (half a turn)
MAX_HOMING = 2047  # largest Homing_Offset the STS3215 accepts (sign-magnitude, 11 bits)


def deg(ticks):
    return ticks * 360.0 / TICKS


def parse_args():
    parser = argparse.ArgumentParser(
        description="Centre the SO-100 wrist roll between its stops and store its range.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--arm", required=True, choices=["follower", "leader"])
    parser.add_argument("--port", default=None,
                        help="Serial port (default: FOLLOWER_PORT or LEADER_PORT from .env)")
    parser.add_argument("--margin_deg", type=float, default=2.0,
                        help="Keep the stored range this far inside each stop")
    parser.add_argument("--min_travel_deg", type=float, default=90.0,
                        help="Refuse a sweep shorter than this (the stops were probably not reached)")
    args = parser.parse_args()
    if args.port is None:
        args.port = os.environ.get("FOLLOWER_PORT" if args.arm == "follower" else "LEADER_PORT")
    if not args.port:
        parser.error("missing --port (pass it or set FOLLOWER_PORT / LEADER_PORT in the repo root .env)")
    return args


def make_arm(args):
    if args.arm == "follower":
        return make_robot_from_config(SO100FollowerConfig(port=args.port, id=FOLLOWER_ID))
    return make_teleoperator_from_config(SO100LeaderConfig(port=args.port, id=LEADER_ID))


def enter_pressed():
    """True once Enter was pressed (the terminal delivers the line on Enter)."""
    if select.select([sys.stdin], [], [], 0)[0]:
        sys.stdin.readline()
        return True
    return False


def wait_for_enter(prompt):
    print(prompt)
    sys.stdin.readline()


def record_sweep(bus):
    """Follow the wrist while it is turned by hand. Returns (start_raw, lowest, highest).

    Positions are unwrapped from the start position, so crossing the encoder seam during the
    sweep is fine: lowest and highest are in steps from start_raw and may be outside 0..4095.
    """
    start = prev = bus.read("Present_Position", MOTOR, normalize=False)
    total = lowest = highest = 0
    while not enter_pressed():
        raw = bus.read("Present_Position", MOTOR, normalize=False)
        step = (raw - prev + TICKS // 2) % TICKS - TICKS // 2  # shortest way from prev to raw
        prev = raw
        total += step
        lowest, highest = min(lowest, total), max(highest, total)
        print(f"\r    position {deg(total):+7.1f} deg   travel so far {deg(highest - lowest):6.1f} deg   ",
              end="", flush=True)
        time.sleep(0.01)
    print()
    return start, lowest, highest


def centred_calibration(old, start, lowest, highest, margin_deg):
    """Calibration that puts the middle of the travel at LeRobot's centre position."""
    raw_mid = round(start + (lowest + highest) / 2) % TICKS
    # Present_Position = Actual - Homing_Offset, so this offset moves raw_mid to HALF.
    homing = raw_mid - HALF
    if homing > MAX_HOMING:
        homing -= TICKS
    homing = max(-MAX_HOMING, min(MAX_HOMING, homing))  # off by at most one step at the limit

    half_travel = (highest - lowest) / 2
    margin = margin_deg * TICKS / 360.0
    return MotorCalibration(
        id=old.id,
        drive_mode=old.drive_mode,
        homing_offset=int(homing),
        range_min=max(0, round(HALF - half_travel + margin)),
        range_max=min(TICKS - 1, round(HALF + half_travel - margin)),
    )


def check(bus):
    """Show the calibrated angle live until Enter. Returns True if it ever jumped."""
    lo = hi = prev = None
    jumped = False
    while not enter_pressed():
        angle = bus.read("Present_Position", MOTOR)  # degrees, 0 = middle of the travel
        if prev is not None and abs(angle - prev) > 90:  # a hand cannot turn 90 deg in 20 ms
            jumped = True
        prev = angle
        lo = angle if lo is None else min(lo, angle)
        hi = angle if hi is None else max(hi, angle)
        print(f"\r    wrist roll {angle:+7.1f} deg   (seen {lo:+.1f} .. {hi:+.1f})   ", end="", flush=True)
        time.sleep(0.02)
    print()
    return jumped


def main():
    args = parse_args()
    arm = make_arm(args)
    if MOTOR not in arm.calibration:
        sys.exit(f"No calibration file for {arm.id} at {arm.calibration_fpath}.\n"
                 "Run lerobot-calibrate for this arm first (ARM_SETUP.md, step 3).")
    bus = arm.bus
    old = arm.calibration[MOTOR]
    saved = False

    bus.connect()
    try:
        bus.disable_torque(MOTOR)  # limp, and unlocks the motor's settings for writing
        # Factory settings for this motor only, so the sweep reads plain encoder positions.
        bus.write("Homing_Offset", MOTOR, 0, normalize=False)
        bus.write("Min_Position_Limit", MOTOR, 0, normalize=False)
        bus.write("Max_Position_Limit", MOTOR, TICKS - 1, normalize=False)

        print(f"\nWrist roll calibration of the {args.arm} ({arm.id}).")
        print("The wrist roll motor is off: it turns freely by hand. Hold the arm so it cannot fall.")
        wait_for_enter("\n1. Turn the wrist roll by hand against one stop, then press Enter.")
        print("2. Now turn it SLOWLY all the way to the other stop, then press Enter.")
        start, lowest, highest = record_sweep(bus)

        travel = deg(highest - lowest)
        if travel < args.min_travel_deg:
            sys.exit(f"Travel was only {travel:.0f} deg: turn from stop to stop. Nothing was changed.")
        if travel >= 359:
            sys.exit(f"Travel was {travel:.0f} deg, a full turn: no stop was found. Nothing was changed.")

        new = centred_calibration(old, start, lowest, highest, args.margin_deg)
        bus.write_calibration({MOTOR: new}, cache=False)
        bus.calibration = {**arm.calibration, MOTOR: new}
        limit = deg((new.range_max - new.range_min) / 2)
        print(f"\nTravel {travel:.1f} deg. Zero is now the middle of the travel; "
              f"range -{limit:.1f} .. +{limit:.1f} deg ({args.margin_deg:g} deg inside each stop).")

        print("\n3. Check: turn the wrist roll to both stops. The angle must change smoothly, "
              "without jumping, and\n   be close to 0 in the middle. Press Enter when done.")
        if check(bus):
            sys.exit("The angle jumped during the check: calibration not saved. Run the script again.")

        answer = input("\nSave this calibration? [Y/n] ").strip().lower()
        if answer not in ("", "y", "yes"):
            sys.exit("Not saved.")
        arm.calibration[MOTOR] = new
        arm._save_calibration()
        saved = True
        print(f"Saved to {arm.calibration_fpath}")
    finally:
        if not saved:
            # Put the motor back as the calibration file describes it, so the two stay consistent.
            bus.write_calibration({MOTOR: old}, cache=False)
            print("Wrist roll settings restored to the saved calibration.")
        bus.disconnect(disable_torque=False)


if __name__ == "__main__":
    main()
