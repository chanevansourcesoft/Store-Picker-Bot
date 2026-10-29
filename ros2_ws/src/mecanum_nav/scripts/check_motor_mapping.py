#!/usr/bin/env python3
"""Find out which motor id drives which wheel, and which way round it turns.

Run this once before trusting any mecanum motion. It answers three things that the kinematics
cannot work without, and that no amount of reading the code can tell you:

  1. ARE ALL FOUR WHEELS DRIVEN? On the Ackermann build they were not. ackermann.py sends speed
     to motors 2 and 4 only -- the rear pair -- because the front wheels were steered idlers with
     no motor in them. Mecanum strafing cannot work on two driven wheels: it works by cancelling
     the four wheels' forward force components against each other while their 45-degree sideways
     components add. With only one axle powered there is nothing to cancel against, and a strafe
     command just scrubs the tyres. If ids 1 and 3 turn nothing here, the robot needs motors
     fitted before any of the rest of this is meaningful.

  2. WHICH WHEEL IS WHICH id. The kinematics assigns a different formula to each corner, so a
     swapped pair turns a strafe into a rotation.

  3. WHICH WAY IS POSITIVE. The right-hand motors are mounted mirrored, so positive rps drives
     them backwards. Getting this wrong makes the robot spin when told to go straight.

Each motor runs on its own, briefly, at low speed. Nothing else needs to be running -- this talks
straight to the board over serial.

    python3 check_motor_mapping.py
"""

import sys
import time

sys.path.insert(0, '/home/ubuntu/ros2_ws/src/driver/ros_robot_controller')

from ros_robot_controller.ros_robot_controller_sdk import Board

SPEED_RPS = 0.6      # gentle: about 0.15 m/s at the rim on 80 mm wheels
RUN_SEC = 1.5

EXPECTED = {1: 'FRONT-LEFT', 2: 'REAR-LEFT', 3: 'FRONT-RIGHT', 4: 'REAR-RIGHT'}


def main():
    print(__doc__)
    print('=' * 72)
    print('SAFETY: put the robot on a stand or hold it so the wheels are OFF the ground.')
    print('        It will try to drive. One motor at a time, 1.5 s each, slowly.')
    print('=' * 72)
    if input('Wheels clear of the ground? type yes to continue: ').strip().lower() != 'yes':
        print('aborted, nothing sent.')
        return 1

    board = Board()
    board.enable_reception(True)

    def all_stop():
        for _ in range(5):
            board.set_motor_speed([[i, 0.0] for i in (1, 2, 3, 4)])
            time.sleep(0.02)

    results = {}
    try:
        for motor_id in (1, 2, 3, 4):
            print()
            print('--- motor id %d  (kinematics expects this to be the %s wheel) ---'
                  % (motor_id, EXPECTED[motor_id]))
            input('    press Enter to run it... ')

            all_stop()
            board.set_motor_speed([[motor_id, SPEED_RPS]])
            time.sleep(RUN_SEC)
            all_stop()

            which = input('    Which wheel turned? [fl/rl/fr/rr/none]: ').strip().lower()
            if which == 'none':
                results[motor_id] = ('none', None)
                continue
            direction = input('    Did its TOP surface move toward the front of the robot? '
                              '[y/n]: ').strip().lower()
            results[motor_id] = (which, direction.startswith('y'))
    finally:
        all_stop()
        print('\nall motors stopped.')

    print()
    print('=' * 72)
    print('RESULT')
    print('=' * 72)
    dead = [i for i, (w, _) in results.items() if w == 'none']
    if dead:
        print('Motor id(s) %s drove nothing.' % ', '.join(str(i) for i in dead))
        if set(dead) & {1, 3}:
            print()
            print('Ids 1 and/or 3 are the FRONT pair. On the Ackermann chassis those positions')
            print('had no motors -- only the rear two were driven. If the front wheels are')
            print('unpowered then this robot cannot strafe no matter how the software is')
            print('configured: holonomic motion needs torque at all four corners.')
            print('Fit front motors, or keep driving it as a differential/skid robot.')
        print()

    name_to_param = {'fl': 'id_front_left', 'rl': 'id_rear_left',
                     'fr': 'id_front_right', 'rr': 'id_rear_right'}
    print('Put this in config/mecanum_base.yaml:')
    for motor_id, (which, fwd) in sorted(results.items()):
        if which in name_to_param:
            print('    %-16s %d' % (name_to_param[which] + ':', motor_id))
    # Per wheel, matching mecanum_base.yaml: polarity need not follow the side (on this robot
    # the reversed pair was once a diagonal), so a per-side answer can be wrong.
    # OLD: printed invert_left / invert_right
    for motor_id, (which, fwd) in sorted(results.items()):
        if which in name_to_param and fwd is not None:
            print('    %-20s %.1f' % (name_to_param[which].replace('id_', 'invert_') + ':',
                                      1.0 if fwd else -1.0))
    print()
    print('(A wheel whose top moves toward the front of the robot on a positive command is')
    print(' driving the robot forward, so that side needs no inversion.)')
    return 0


if __name__ == '__main__':
    sys.exit(main())
