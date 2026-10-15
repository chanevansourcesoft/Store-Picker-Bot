"""
Fake rosbridge server: test the live map without the robot (and without ROS 2).

Speaks the rosbridge JSON protocol over websocket (only "subscribe" / "unsubscribe").
Sends a sample store map, a robot that drives between our stations,
/tf (map -> odom -> base_footprint -> base_link), /amcl_pose, /odom, /scan and /plan.
The throttle_rate in "subscribe" is respected, like the real rosbridge.

Run:
    python -m robot_server.fake_rosbridge                 # ws://localhost:9090
    python -m robot_server.fake_rosbridge --host 0.0.0.0  # other PCs can connect too
Then set ROSBRIDGE_URL=ws://localhost:9090 for the Streamlit app.
"""

import argparse
import asyncio
import json
import math
import random
import time

from websockets.asyncio.server import serve

# ---------- sample map (same station positions as mock_robot.py) ----------

STATIONS = {
    "home": (0.0, 0.0),
    "shelf_A": (2.0, 1.0),
    "shelf_B": (2.0, 2.0),
    "shelf_C": (2.0, 3.0),
    "customer": (0.0, 3.0),
}
ROUTE = ["home", "shelf_A", "shelf_B", "shelf_C", "customer"]

RES = 0.05                 # meters per cell
ORIGIN = (-1.5, -1.5)      # world position of cell (0, 0)
WIDTH, HEIGHT = 140, 130   # cells (7 m x 6.5 m)

# Walls and objects as boxes: (min_x, min_y, max_x, max_y) in meters
WALLS = [
    (-1.0, -1.0, 5.0, -0.9),   # bottom wall
    (-1.0, 4.4, 5.0, 4.5),     # top wall
    (-1.0, -1.0, -0.9, 4.5),   # left wall
    (4.9, -1.0, 5.0, 4.5),     # right wall
    (2.6, 0.5, 3.0, 3.5),      # shelves
    (0.9, 1.8, 1.2, 2.2),      # a box on the floor
    (-0.9, 2.6, -0.5, 3.4),    # customer counter
]
# This one comes and goes, so we can see the map being redrawn
MOVING_BOX = (3.8, 1.5, 4.2, 1.9)

SPEED = 0.4        # m/s
TURN_SPEED = 1.5   # rad/s
WAIT_SEC = 2.0     # wait at each station
MAP_ODOM = (0.3, -0.2, 0.1)  # map -> odom offset (like AMCL drift), x, y, yaw


def make_grid(with_box):
    """OccupancyGrid data: -1 unknown (outside the walls), 0 free, 100 occupied."""
    boxes = WALLS + ([MOVING_BOX] if with_box else [])
    data = []
    for row in range(HEIGHT):
        y = ORIGIN[1] + (row + 0.5) * RES
        for col in range(WIDTH):
            x = ORIGIN[0] + (col + 0.5) * RES
            if any(x0 <= x <= x1 and y0 <= y <= y1 for x0, y0, x1, y1 in boxes):
                data.append(100)
            elif -1.0 <= x <= 5.0 and -1.0 <= y <= 4.5:
                data.append(0)
            else:
                data.append(-1)
    return data


def occupied(grid, x, y):
    col = int((x - ORIGIN[0]) / RES)
    row = int((y - ORIGIN[1]) / RES)
    if not (0 <= col < WIDTH and 0 <= row < HEIGHT):
        return False
    return grid[row * WIDTH + col] == 100


# ---------- small helpers ----------

def stamp():
    t = time.time()
    return {"sec": int(t), "nanosec": int((t % 1) * 1e9)}


def header(frame):
    return {"stamp": stamp(), "frame_id": frame}


def quat(yaw):
    return {"x": 0.0, "y": 0.0, "z": math.sin(yaw / 2), "w": math.cos(yaw / 2)}


def compose(a, b):
    """Pose a, then pose b inside a. Poses are (x, y, yaw)."""
    c, s = math.cos(a[2]), math.sin(a[2])
    return (a[0] + c * b[0] - s * b[1], a[1] + s * b[0] + c * b[1], a[2] + b[2])


def inverse(a):
    c, s = math.cos(a[2]), math.sin(a[2])
    return (-c * a[0] - s * a[1], s * a[0] - c * a[1], -a[2])


def wrap(angle):
    return math.atan2(math.sin(angle), math.cos(angle))


def transform(parent, child, pose):
    return {
        "header": header(parent),
        "child_frame_id": child,
        "transform": {
            "translation": {"x": pose[0], "y": pose[1], "z": 0.0},
            "rotation": quat(pose[2]),
        },
    }


# ---------- the simulated robot ----------

class Sim:
    def __init__(self):
        self.pose = (0.0, 0.0, 0.0)  # true pose in the map frame
        self.speed = 0.0
        self.target_index = 1
        self.wait_until = time.time() + WAIT_SEC
        self.with_box = True
        self.grid = make_grid(self.with_box)

    def target(self):
        return STATIONS[ROUTE[self.target_index]]

    def step(self, dt):
        x, y, yaw = self.pose
        if time.time() < self.wait_until:
            self.speed = 0.0
            return
        tx, ty = self.target()
        dist = math.hypot(tx - x, ty - y)
        if dist < 0.03:
            self.target_index = (self.target_index + 1) % len(ROUTE)
            self.wait_until = time.time() + WAIT_SEC
            self.speed = 0.0
            return
        error = wrap(math.atan2(ty - y, tx - x) - yaw)
        turn = max(-TURN_SPEED * dt, min(TURN_SPEED * dt, error))
        yaw = wrap(yaw + turn)
        self.speed = SPEED if abs(error) < 0.3 else 0.0  # turn first, then drive
        move = min(self.speed * dt, dist)
        self.pose = (x + move * math.cos(yaw), y + move * math.sin(yaw), yaw)

    def map_odom(self):
        """map -> odom, with a small slow wobble (like AMCL corrections)."""
        t = time.time()
        return (MAP_ODOM[0] + 0.02 * math.sin(t / 3), MAP_ODOM[1], MAP_ODOM[2])

    # ----- messages -----

    def msg_map(self):
        return {
            "header": header("map"),
            "info": {
                "map_load_time": stamp(),
                "resolution": RES,
                "width": WIDTH,
                "height": HEIGHT,
                "origin": {"position": {"x": ORIGIN[0], "y": ORIGIN[1], "z": 0.0}, "orientation": quat(0.0)},
            },
            "data": self.grid,
        }

    def msg_tf(self):
        map_odom = self.map_odom()
        odom_base = compose(inverse(map_odom), self.pose)
        return {"transforms": [
            transform("map", "odom", map_odom),
            transform("odom", "base_footprint", odom_base),
            transform("base_footprint", "base_link", (0.0, 0.0, 0.0)),
        ]}

    def msg_amcl(self):
        x, y, yaw = self.pose
        return {"header": header("map"), "pose": {
            "pose": {"position": {"x": x, "y": y, "z": 0.0}, "orientation": quat(yaw)},
            "covariance": [0.0] * 36,
        }}

    def msg_odom(self):
        x, y, yaw = compose(inverse(self.map_odom()), self.pose)
        return {"header": header("odom"), "child_frame_id": "base_footprint", "pose": {
            "pose": {"position": {"x": x, "y": y, "z": 0.0}, "orientation": quat(yaw)},
            "covariance": [0.0] * 36,
        }, "twist": {"twist": {"linear": {"x": self.speed, "y": 0.0, "z": 0.0},
                               "angular": {"x": 0.0, "y": 0.0, "z": 0.0}},
                     "covariance": [0.0] * 36}}

    def msg_scan(self, rays=240, range_max=8.0):
        x, y, yaw = self.pose
        step = 2 * math.pi / rays
        ranges = []
        for i in range(rays):
            angle = yaw - math.pi + i * step
            c, s = math.cos(angle), math.sin(angle)
            r = 0.12
            hit = None
            while r < range_max:
                if occupied(self.grid, x + r * c, y + r * s):
                    hit = r + random.gauss(0, 0.01)
                    break
                r += RES
            ranges.append(hit)  # None = no hit (real rosbridge sends inf as null)
        return {"header": header("base_link"), "angle_min": -math.pi, "angle_max": math.pi - step,
                "angle_increment": step, "time_increment": 0.0, "scan_time": 0.1,
                "range_min": 0.12, "range_max": range_max, "ranges": ranges, "intensities": []}

    def msg_plan(self):
        if time.time() < self.wait_until:
            return {"header": header("map"), "poses": []}
        x, y, _ = self.pose
        tx, ty = self.target()
        n = max(2, int(math.hypot(tx - x, ty - y) / 0.1))
        poses = []
        for i in range(n + 1):
            px, py = x + (tx - x) * i / n, y + (ty - y) * i / n
            poses.append({"header": header("map"), "pose": {
                "position": {"x": px, "y": py, "z": 0.0}, "orientation": quat(0.0)}})
        return {"header": header("map"), "poses": poses}


# ---------- rosbridge protocol ----------

sim = Sim()
clients = {}  # websocket -> {topic: {"throttle": sec, "last": time}}


async def send(ws, topic, msg):
    try:
        await ws.send(json.dumps({"op": "publish", "topic": topic, "msg": msg}))
    except Exception:
        pass  # client gone; the handler cleans up


async def publish(topic, make_msg):
    """Send one message to every client that subscribed and is not throttled."""
    now = time.time()
    msg = None
    for ws, subs in list(clients.items()):
        sub = subs.get(topic)
        if sub is None or now - sub["last"] < sub["throttle"]:
            continue
        sub["last"] = now
        if msg is None:
            msg = make_msg()  # build only if someone needs it
        await send(ws, topic, msg)


async def handler(ws):
    subs = {}
    clients[ws] = subs
    print(f"[fake rosbridge] client connected ({len(clients)} now)")
    try:
        async for text in ws:
            try:
                req = json.loads(text)
            except ValueError:
                continue
            op, topic = req.get("op"), req.get("topic")
            if op == "subscribe" and topic:
                subs[topic] = {"throttle": (req.get("throttle_rate") or 0) / 1000.0, "last": 0.0}
                print(f"[fake rosbridge] subscribe {topic} throttle={req.get('throttle_rate', 0)} ms")
                if topic == "/map":  # latched topic: send it now
                    subs[topic]["last"] = time.time()
                    await send(ws, topic, sim.msg_map())
            elif op == "unsubscribe" and topic:
                subs.pop(topic, None)
    except Exception:
        pass
    finally:
        clients.pop(ws, None)
        print(f"[fake rosbridge] client left ({len(clients)} now)")


async def every(rate_hz, job):
    while True:
        await job()
        await asyncio.sleep(1.0 / rate_hz)


async def sim_loop():
    last = time.time()
    while True:
        now = time.time()
        sim.step(now - last)
        last = now
        await asyncio.sleep(0.02)


async def toggle_box():
    """Every 10 s the moving box comes or goes, and we send a new /map."""
    sim.with_box = not sim.with_box
    sim.grid = make_grid(sim.with_box)
    for ws, subs in list(clients.items()):
        if "/map" in subs:
            await send(ws, "/map", sim.msg_map())


async def main(host, port):
    print(f"[fake rosbridge] ws://{host}:{port}  (Ctrl+C to stop)")
    async with serve(handler, host, port, max_size=None):
        await asyncio.gather(
            sim_loop(),
            every(30, lambda: publish("/tf", sim.msg_tf)),
            every(20, lambda: publish("/odom", sim.msg_odom)),
            every(15, lambda: publish("/scan", sim.msg_scan)),  # client throttles to 10 Hz
            every(2, lambda: publish("/plan", sim.msg_plan)),
            every(2, lambda: publish("/amcl_pose", sim.msg_amcl)),
            every(0.1, toggle_box),
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Fake rosbridge server for the live map")
    parser.add_argument("--host", default="localhost")
    parser.add_argument("--port", type=int, default=9090)
    args = parser.parse_args()
    try:
        asyncio.run(main(args.host, args.port))
    except KeyboardInterrupt:
        print("\n[fake rosbridge] stopped")
