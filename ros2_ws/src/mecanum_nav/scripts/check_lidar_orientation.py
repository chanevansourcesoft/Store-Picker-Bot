#!/usr/bin/env python3
"""Decide whether lidar_frame's yaw in mecanum_robot.urdf is right.

Why this exists: the RPLidar A1 driver stamps scans with frame_id "lidar_frame", and the URDF
fixes that frame's yaw relative to the robot. If the yaw is 180 deg wrong, every scan is rotated
by half a turn. SLAM still converges and the map still looks plausible -- it is just mirrored,
and the robot drives into walls that are not where the map says. Nothing downstream warns you.

So: put a single obstacle at a known side of the robot, run this, and read off which yaw value
puts the obstacle where it actually is.

    python3 check_lidar_orientation.py           # snapshot: nearest return per sector
    python3 check_lidar_orientation.py --diff    # differential: what MOVED (use this one)

Prefer --diff. The snapshot mode only works in a room where one side is clearly closer than the
others; in a corridor, or anywhere roughly symmetric left-to-right, both hypotheses look equally
plausible and it tells you nothing. --diff compares a scan before and after you place an obstacle,
so only the obstacle you moved matters and the surroundings cancel out.

It only reads /scan_raw. It changes nothing.
"""

import math
import sys

import rclpy
from rclpy.node import Node
from sensor_msgs.msg import LaserScan

SECTOR_HALF_WIDTH = math.radians(25)
SECTORS = [('FRONT', 0.0), ('LEFT', math.pi / 2), ('REAR', math.pi), ('RIGHT', -math.pi / 2)]


def wrap(a):
    return math.atan2(math.sin(a), math.cos(a))


def nearest_per_sector(scan, yaw_offset):
    """Nearest valid return in each robot-frame sector, given lidar_frame yaw = yaw_offset."""
    best = {name: None for name, _ in SECTORS}
    for i, r in enumerate(scan.ranges):
        if math.isinf(r) or math.isnan(r) or not (scan.range_min < r < scan.range_max):
            continue
        robot_angle = wrap(scan.angle_min + i * scan.angle_increment + yaw_offset)
        for name, centre in SECTORS:
            if abs(wrap(robot_angle - centre)) < SECTOR_HALF_WIDTH:
                if best[name] is None or r < best[name][0]:
                    best[name] = (r, math.degrees(robot_angle))
    return best


def render(best):
    out = []
    for name, _ in SECTORS:
        hit = best[name]
        out.append('    %-6s %s' % (
            name, 'clear' if hit is None else '%5.2f m  (bearing %+4.0f deg)' % hit))
    return '\n'.join(out)


def sector_deltas(before, after, yaw_offset):
    """Per-sector largest drop in range between two scans, i.e. where something appeared."""
    best = {name: None for name, _ in SECTORS}
    n = min(len(before.ranges), len(after.ranges))
    for i in range(n):
        b, a = before.ranges[i], after.ranges[i]
        for r in (b, a):
            if math.isinf(r) or math.isnan(r):
                break
        else:
            if not (before.range_min < b < before.range_max):
                continue
            if not (after.range_min < a < after.range_max):
                continue
            drop = b - a
            if drop < 0.10:          # 10 cm, comfortably above A1 range noise
                continue
            robot_angle = wrap(before.angle_min + i * before.angle_increment + yaw_offset)
            for name, centre in SECTORS:
                if abs(wrap(robot_angle - centre)) < SECTOR_HALF_WIDTH:
                    if best[name] is None or drop > best[name][0]:
                        best[name] = (drop, a, math.degrees(robot_angle))
    return best


def render_deltas(best):
    out = []
    for name, _ in SECTORS:
        hit = best[name]
        if hit is None:
            out.append('    %-6s unchanged' % name)
        else:
            out.append('    %-6s something APPEARED at %.2f m  (was %.2f m further out, '
                       'bearing %+4.0f deg)' % (name, hit[1], hit[0], hit[2]))
    return '\n'.join(out)


class Checker(Node):
    def __init__(self):
        super().__init__('check_lidar_orientation')
        self.scan = None
        self.create_subscription(LaserScan, '/scan_raw', self._cb, 10)

    def _cb(self, msg):
        if self.scan is None:
            self.scan = msg

    def grab(self, what, timeout=20.0):
        """Wait for one fresh scan."""
        self.scan = None
        waited = 0.0
        while self.scan is None and waited < timeout:
            rclpy.spin_once(self, timeout_sec=0.5)
            waited += 0.5
        if self.scan is None:
            print('\nNo scan arrived in %.0f s while capturing %s. Check, in order:' % (timeout, what))
            print('  ros2 topic info /scan_raw      -> "Publisher count" must be 1, not 0')
            print('  systemctl status lidar_watchdog')
            print('  is the robot powered and is the LiDAR physically spinning?')
            return None
        return self.scan


def run_diff(node):
    print('Differential check. The robot must NOT move at any point during this.\n')
    print('Step 1: clear the space around the robot as best you can, then')
    input('        press Enter to capture the BEFORE scan... ')
    before = node.grab('the before scan')
    if before is None:
        return 1

    print('\nStep 2: place one obstacle (a box, a chair) about 1 m from ONE side of the robot.')
    print('        Any side -- you will tell me which, and this works for all four.')
    input('        Press Enter to capture the AFTER scan... ')
    after = node.grab('the after scan')
    if after is None:
        return 1

    print()
    print('=' * 72)
    print('IF  lidar_frame rpy="0 0 3.14159"   (what the URDF says now)')
    print('=' * 72)
    print(render_deltas(sector_deltas(before, after, math.pi)))
    print()
    print('=' * 72)
    print('IF  lidar_frame rpy="0 0 0"')
    print('=' * 72)
    print(render_deltas(sector_deltas(before, after, 0.0)))
    print()
    print('Exactly one block should report the obstacle in the sector you actually put it in.')
    print('That block is the correct rpy. The other will report it in the opposite sector.')
    print('Front of the robot = the end the LiDAR sits on (80 mm forward of centre).')
    print('Edit lidar_frame_joint in')
    print('  simulations/jetacker_description/urdf/mecanum_robot.urdf')
    return 0


def main():
    rclpy.init()
    node = Checker()

    if '--diff' in sys.argv:
        rc = run_diff(node)
        node.destroy_node()
        rclpy.shutdown()
        return rc

    print('Waiting for a scan on /scan_raw ...')
    waited = 0.0
    while node.scan is None and waited < 20.0:
        rclpy.spin_once(node, timeout_sec=0.5)
        waited += 0.5

    if node.scan is None:
        print('\nNo scan arrived in 20 s. Check, in order:')
        print('  ros2 topic info /scan_raw      -> "Publisher count" must be 1, not 0')
        print('  systemctl status lidar_watchdog')
        print('  is the robot powered and is the LiDAR physically spinning?')
        node.destroy_node()
        rclpy.shutdown()
        return 1

    scan = node.scan
    valid = sum(1 for r in scan.ranges
                if not math.isinf(r) and not math.isnan(r) and scan.range_min < r < scan.range_max)
    print('Got a scan: %d beams, %d valid returns.\n' % (len(scan.ranges), valid))

    print('=' * 68)
    print('IF  lidar_frame rpy="0 0 3.14159"   (what the URDF says now)')
    print('=' * 68)
    print(render(nearest_per_sector(scan, math.pi)))
    print()
    print('=' * 68)
    print('IF  lidar_frame rpy="0 0 0"')
    print('=' * 68)
    print(render(nearest_per_sector(scan, 0.0)))
    print()
    print('Front of the robot = the end the LiDAR sits on (it is 80 mm forward of centre).')
    print('Whichever block agrees with where your obstacle really is, is the correct rpy.')
    print('Keep the current value, or edit lidar_frame_joint in')
    print('  simulations/jetacker_description/urdf/mecanum_robot.urdf')

    node.destroy_node()
    rclpy.shutdown()
    return 0


if __name__ == '__main__':
    sys.exit(main())
