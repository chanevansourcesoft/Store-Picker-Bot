#!/usr/bin/env python3
# Lidar watchdog: does the job of lidar_watchdog.service on the robot's own image.
# Starts the A1 driver (peripherals sllidar_a1.launch.py) and restarts it when /scan_raw stops
# arriving for --stall seconds, or when the driver exits (e.g. the lidar was unplugged: it keeps
# retrying until the port is back). Ctrl+C / SIGTERM stops the driver cleanly, then exits.
#   python3 tools/lidar_watchdog.py              # in Docker: the compose lidar service runs this
import argparse
import os
import signal
import subprocess
import time

import rclpy
from rclpy.qos import qos_profile_sensor_data
from rclpy.signals import SignalHandlerOptions
from sensor_msgs.msg import LaserScan

DRIVER = ['ros2', 'launch', 'peripherals', 'sllidar_a1.launch.py']


def log(msg):
    print('[lidar_watchdog] %s' % msg, flush=True)


class Watchdog:
    def __init__(self, stall, startup):
        self.stall = stall      # s without a scan before a restart
        self.startup = startup  # s the driver gets to spin up and send its first scan
        self.proc = None
        self.started = 0.0
        self.last_scan = None
        self.stopping = False
        self.node = rclpy.create_node('lidar_watchdog')
        # Sensor-data QoS (best effort) matches both reliable and best-effort publishers.
        self.node.create_subscription(LaserScan, 'scan_raw', self.on_scan, qos_profile_sensor_data)

    def on_scan(self, msg):
        self.last_scan = time.monotonic()

    def start(self):
        log('starting: %s' % ' '.join(DRIVER))
        # Own process group, so a stop reaches the launch and the driver node it started.
        self.proc = subprocess.Popen(DRIVER, start_new_session=True)
        self.started = time.monotonic()
        self.last_scan = None

    def stop(self):
        if self.proc is None:
            return
        if self.proc.poll() is None:
            os.killpg(self.proc.pid, signal.SIGINT)
            try:
                self.proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                log('driver did not stop, killing it')
                os.killpg(self.proc.pid, signal.SIGKILL)
                self.proc.wait()
        self.proc = None

    def check(self):
        now = time.monotonic()
        if self.proc.poll() is not None:
            log('driver exited (code %s), restarting in 2 s' % self.proc.returncode)
            self.proc = None
            time.sleep(2)
            self.start()
        elif self.last_scan is None:
            if now - self.started > self.startup:
                log('no /scan_raw %.0f s after start, restarting' % self.startup)
                self.stop()
                self.start()
        elif now - self.last_scan > self.stall:
            log('/scan_raw stalled for %.0f s, restarting' % self.stall)
            self.stop()
            self.start()

    def run(self):
        self.start()
        while not self.stopping:
            rclpy.spin_once(self.node, timeout_sec=0.5)
            if not self.stopping:
                self.check()
        log('stopping')
        self.stop()
        self.node.destroy_node()


def main():
    parser = argparse.ArgumentParser(description='Run the A1 lidar driver and restart it when /scan_raw stalls.')
    parser.add_argument('--stall', type=float, default=5.0, help='seconds without a scan before a restart')
    parser.add_argument('--startup', type=float, default=15.0, help='seconds the driver gets to send its first scan')
    args = parser.parse_args()

    rclpy.init(signal_handler_options=SignalHandlerOptions.NO)
    watchdog = Watchdog(args.stall, args.startup)

    def on_signal(signum, frame):
        watchdog.stopping = True
    signal.signal(signal.SIGINT, on_signal)
    signal.signal(signal.SIGTERM, on_signal)

    watchdog.run()
    rclpy.shutdown()


if __name__ == '__main__':
    main()
