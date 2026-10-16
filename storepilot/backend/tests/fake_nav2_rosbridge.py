"""
Fake Jetson for development: speaks the rosbridge protocol and behaves like the robot's Nav2.

It starts the way the real robot was found: Nav2 lifecycle nodes paused (inactive) and the
robot not localized. It supports what StorePilot uses:
    subscribe / unsubscribe / advertise / publish (/goal_pose, /initialpose)
    call_service: lifecycle is_active + manage_nodes, cancel_goal, map_server/load_map, rosapi/get_param
and publishes /map, /tf, /tf_static, /odom, /scan, /plan, battery, NavigateToPose status.

Run (from storepilot/backend):
    .venv/bin/python -m tests.fake_nav2_rosbridge            # ws://127.0.0.1:9091
"""

import argparse
import asyncio
import json
import math
import time

from websockets.asyncio.server import serve

RES = 0.05
MAPS_DIR = "/home/ubuntu/ros2_ws/src/slam/maps"
MAPS = {
    # name: (origin_x, origin_y, width_m, height_m, obstacle boxes)
    "conf3": (-4.4, -2.16, 6.6, 5.5, [(-1.0, -0.4, -0.4, 1.2), (0.8, 1.2, 1.4, 1.6)]),
    "office_map": (-1.36, -12.2, 7.8, 13.0, [(1.0, -2.0, 1.5, 1.5), (3.5, -4.0, 4.2, -1.0), (2.0, 2.0, 5.5, 2.4)]),
}
MAP_ODOM = (0.15, -0.1, 0.04)
SPEED, TURN = 0.3, 1.2


def quat(yaw):
    return {"x": 0.0, "y": 0.0, "z": math.sin(yaw / 2), "w": math.cos(yaw / 2)}


def compose(a, b):
    return (a[0] + b[0] * math.cos(a[2]) - b[1] * math.sin(a[2]), a[1] + b[0] * math.sin(a[2]) + b[1] * math.cos(a[2]),
            a[2] + b[2])


def inverse(a):
    c, s = math.cos(a[2]), math.sin(a[2])
    return (-(a[0] * c + a[1] * s), a[0] * s - a[1] * c, -a[2])


def now_stamp():
    t = time.time()
    return {"sec": int(t), "nanosec": int((t % 1) * 1e9)}


def tf(parent, child, p):
    return {"header": {"frame_id": parent, "stamp": {"sec": 0, "nanosec": 0}}, "child_frame_id": child,
            "transform": {"translation": {"x": p[0], "y": p[1], "z": 0.0}, "rotation": quat(p[2])}}


class Robot:
    def __init__(self):
        self.map = "conf3"
        self.grid = self.make_grid()
        self.truth = (0.0, 0.0, 0.0)
        self.nav_active = False
        self.loc_active = False
        self.localized = False
        self.goal = None
        self.status = None
        self.goal_stamp = 0
        self.goal_count = 0
        self.speed = 0.0
        self.cmd_vel_published = 0     # must stay 0: StorePilot never drives the wheels directly
        self.nomotion_calls = 0
        self.last_beat = None        # fake StorePilot watchdog (same rule as storepilot_watchdog.py)
        self.armed = False
        self.watchdog_cancels = 0

    def make_grid(self):
        ox, oy, w, h, boxes = MAPS[self.map]
        cw, ch = int(w / RES), int(h / RES)
        data = []
        for r in range(ch):
            y = oy + (r + 0.5) * RES
            for c in range(cw):
                x = ox + (c + 0.5) * RES
                wall = r < 2 or c < 2 or r >= ch - 2 or c >= cw - 2
                box = any(b[0] <= x <= b[2] and b[1] <= y <= b[3] for b in boxes)
                data.append(100 if wall or box else 0)
        return {"info": {"resolution": RES, "width": cw, "height": ch,
                         "origin": {"position": {"x": ox, "y": oy, "z": 0.0}, "orientation": quat(0)}},
                "data": data}

    def occupied(self, x, y):
        info = self.grid["info"]
        c = int((x - info["origin"]["position"]["x"]) / RES)
        r = int((y - info["origin"]["position"]["y"]) / RES)
        if not (0 <= c < info["width"] and 0 <= r < info["height"]):
            return True
        return self.grid["data"][r * info["width"] + c] >= 65

    def watchdog_check(self):
        if self.armed and self.status == 2 and self.last_beat and time.monotonic() - self.last_beat > 3.0:
            self.status = 5
            self.armed = False
            self.watchdog_cancels += 1

    def msg_costmap(self):
        """3 x 3 m local costmap in odom around the robot: walls lethal, 20 cm inflation."""
        odom_base = compose(inverse(MAP_ODOM), self.truth)
        res, n = 0.05, 60
        ox, oy = odom_base[0] - 1.5, odom_base[1] - 1.5
        data = []
        for r in range(n):
            for c in range(n):
                mx, my, _ = compose(MAP_ODOM, (ox + (c + 0.5) * res, oy + (r + 0.5) * res, 0.0))
                if self.occupied(mx, my):
                    data.append(100)
                elif any(self.occupied(mx + dx, my + dy) for dx, dy in ((0.15, 0), (-0.15, 0), (0, 0.15), (0, -0.15))):
                    data.append(60)
                else:
                    data.append(0)
        return {"header": {"frame_id": "odom", "stamp": now_stamp()},
                "info": {"resolution": res, "width": n, "height": n,
                         "origin": {"position": {"x": ox, "y": oy, "z": 0.0}, "orientation": quat(0)}}, "data": data}

    def msg_footprint(self):
        ob = compose(inverse(MAP_ODOM), self.truth)
        pts = [compose(ob, (x, y, 0.0)) for x, y in ((0.135, 0.12), (0.135, -0.12), (-0.135, -0.12), (-0.135, 0.12))]
        return {"header": {"frame_id": "odom", "stamp": now_stamp()},
                "polygon": {"points": [{"x": p[0], "y": p[1], "z": 0.0} for p in pts]}}

    def step(self, dt):
        self.watchdog_check()
        self.speed = 0.0
        if not (self.goal and self.status == 2 and self.nav_active):
            return
        x, y, t = self.truth
        gx, gy, gt = self.goal
        d = math.hypot(gx - x, gy - y)
        if d < 0.05:
            diff = (gt - t + math.pi) % (2 * math.pi) - math.pi
            if abs(diff) < 0.05:
                self.status = 4
                return
            self.truth = (x, y, t + max(-TURN * dt, min(TURN * dt, diff)))
            return
        heading = math.atan2(gy - y, gx - x)
        diff = (heading - t + math.pi) % (2 * math.pi) - math.pi
        if abs(diff) > 0.2:
            self.truth = (x, y, t + max(-TURN * dt, min(TURN * dt, diff)))
            return
        stepd = min(SPEED * dt, d)
        self.truth = (x + stepd * math.cos(heading), y + stepd * math.sin(heading), heading)
        self.speed = SPEED

    # ---------- messages ----------

    def msg_tf(self):
        odom_base = compose(inverse(MAP_ODOM), self.truth)
        transforms = [tf("odom", "base_footprint", odom_base)]
        if self.loc_active and self.localized:
            transforms.append(tf("map", "odom", MAP_ODOM))
        return {"transforms": transforms}

    def msg_tf_static(self):
        return {"transforms": [tf("base_footprint", "base_link", (0, 0, 0)), tf("base_link", "laser", (0.1, 0, 0))]}

    def msg_scan(self, rays=180):
        lx, ly, lt = compose(self.truth, (0.1, 0, 0))
        ranges = []
        for i in range(rays):
            a = lt - math.pi + i * 2 * math.pi / rays
            r = 0.1
            while r < 8.0 and not self.occupied(lx + r * math.cos(a), ly + r * math.sin(a)):
                r += 0.04
            ranges.append(round(r, 3) if r < 8.0 else None)
        return {"header": {"frame_id": "laser", "stamp": now_stamp()}, "angle_min": -math.pi,
                "angle_max": math.pi, "angle_increment": 2 * math.pi / rays, "range_min": 0.05, "range_max": 8.0,
                "ranges": ranges}

    def msg_odom(self):
        ob = compose(inverse(MAP_ODOM), self.truth)
        return {"header": {"frame_id": "odom", "stamp": now_stamp()}, "child_frame_id": "base_footprint",
                "pose": {"pose": {"position": {"x": ob[0], "y": ob[1], "z": 0.0}, "orientation": quat(ob[2])}},
                "twist": {"twist": {"linear": {"x": self.speed, "y": 0.0, "z": 0.0},
                                    "angular": {"x": 0.0, "y": 0.0, "z": 0.0}}}}

    def msg_amcl(self):
        if not (self.loc_active and self.localized):
            return None
        return {"header": {"frame_id": "map", "stamp": now_stamp()},
                "pose": {"pose": {"position": {"x": self.truth[0], "y": self.truth[1], "z": 0.0},
                                  "orientation": quat(self.truth[2])}, "covariance": [0.0] * 36}}

    def msg_plan(self):
        if not (self.goal and self.status == 2):
            return {"header": {"frame_id": "map"}, "poses": []}
        (x, y, _), (gx, gy, _) = self.truth, self.goal
        n = max(2, int(math.hypot(gx - x, gy - y) / 0.1))
        return {"header": {"frame_id": "map"}, "poses": [
            {"pose": {"position": {"x": x + (gx - x) * k / n, "y": y + (gy - y) * k / n, "z": 0.0}}} for k in range(n + 1)]}

    def msg_status(self):
        if self.status is None:
            return {"status_list": []}
        return {"status_list": [{"goal_info": {"goal_id": {"uuid": [self.goal_count % 256] * 16},
                                               "stamp": {"sec": self.goal_stamp, "nanosec": 0}}, "status": self.status}]}

    # ---------- requests ----------

    def on_publish(self, topic, msg):
        if topic == "/cmd_vel":
            self.cmd_vel_published += 1
        elif topic == "/storepilot/heartbeat":
            self.last_beat = time.monotonic()
            self.armed = bool(msg.get("data"))
        elif topic == "/initialpose" and self.loc_active:
            p = msg["pose"]["pose"]
            q = p["orientation"]
            self.truth = (p["position"]["x"], p["position"]["y"], math.atan2(2 * q["w"] * q["z"], 1 - 2 * q["z"] ** 2))
            self.localized = True
        elif topic == "/goal_pose":
            p = msg["pose"]
            q = p["orientation"]
            self.goal = (p["position"]["x"], p["position"]["y"], math.atan2(2 * q["w"] * q["z"], 1 - 2 * q["z"] ** 2))
            self.goal_stamp = int(time.time() * 1000) % 2_000_000_000
            self.goal_count += 1
            self.status = 2 if (self.nav_active and self.localized) else 6

    def on_service(self, name, args):
        if name.endswith("/is_active"):
            active = self.nav_active if "navigation" in name else self.loc_active
            return {"success": active, "message": ""}
        if name.endswith("/manage_nodes"):
            on = args.get("command") in (0, 2)
            if "navigation" in name:
                self.nav_active = on
                if not on and self.status == 2:
                    self.status = 5
            else:
                self.loc_active = on
            return {"success": True}
        if name == "/request_nomotion_update":
            self.nomotion_calls += 1
            return {}
        if name == "/navigate_to_pose/_action/cancel_goal":
            if self.status == 2:
                self.status = 5
            return {"return_code": 0, "goals_canceling": []}
        if name == "/map_server/load_map":
            mname = args.get("map_url", "").rsplit("/", 1)[-1].removesuffix(".yaml")
            if mname not in MAPS or not self.loc_active:
                return {"result": 1}
            self.map = mname
            self.grid, self.localized = self.make_grid(), False
            return {"result": 0}
        return None


ROBOT = Robot()
CLIENTS = {}   # ws -> {topic: [throttle_s, last_sent]}


async def send(ws, topic, msg):
    try:
        await ws.send(json.dumps({"op": "publish", "topic": topic, "msg": msg}))
    except Exception:
        pass


async def publish(topic, msg, force=False):
    now = time.monotonic()
    for ws, subs in list(CLIENTS.items()):
        sub = subs.get(topic)
        if sub and (force or now - sub[1] >= sub[0]):
            sub[1] = now
            await send(ws, topic, msg)


async def handler(ws):
    CLIENTS[ws] = {}
    try:
        async for raw in ws:
            req = json.loads(raw)
            op = req.get("op")
            if op == "subscribe":
                CLIENTS[ws][req["topic"]] = [req.get("throttle_rate", 0) / 1000.0, 0.0]
                if req["topic"] == "/map":
                    await send(ws, "/map", ROBOT.grid)
                if req["topic"] == "/tf_static":
                    await send(ws, "/tf_static", ROBOT.msg_tf_static())
            elif op == "unsubscribe":
                CLIENTS[ws].pop(req.get("topic"), None)
            elif op == "publish":
                had_map = ROBOT.map
                ROBOT.on_publish(req.get("topic"), req.get("msg") or {})
                if ROBOT.map != had_map:
                    await publish("/map", ROBOT.grid, force=True)
            elif op == "call_service":
                had_map = ROBOT.map
                values = ROBOT.on_service(req.get("service"), req.get("args") or {})
                await ws.send(json.dumps({"op": "service_response", "id": req.get("id"), "service": req.get("service"),
                                          "values": values or {}, "result": values is not None}))
                if ROBOT.map != had_map:
                    await publish("/map", ROBOT.grid, force=True)
    finally:
        CLIENTS.pop(ws, None)


async def loop():
    last = time.monotonic()
    tick = 0
    while True:
        await asyncio.sleep(0.05)
        now = time.monotonic()
        ROBOT.step(now - last)
        last = now
        tick += 1
        await publish("/tf", ROBOT.msg_tf())
        await publish("/odom", ROBOT.msg_odom())
        if tick % 2 == 0:
            await publish("/scan", ROBOT.msg_scan())
            await publish("/navigate_to_pose/_action/status", ROBOT.msg_status())
        if tick % 4 == 0 and ROBOT.msg_amcl():
            await publish("/amcl_pose", ROBOT.msg_amcl())
        if tick % 6 == 0:
            await publish("/local_costmap/published_footprint", ROBOT.msg_footprint())
        if tick % 20 == 0:
            await publish("/local_costmap/costmap", ROBOT.msg_costmap())
            await publish("/storepilot/watchdog", {"data": json.dumps({"armed": ROBOT.armed, "cancels": ROBOT.watchdog_cancels})})
        if tick % 10 == 0:
            await publish("/plan", ROBOT.msg_plan())
            await publish("/ros_robot_controller/battery", {"data": 11286})


async def main(host, port):
    async with serve(handler, host, port, max_size=None):
        print(f"[fake nav2 rosbridge] ws://{host}:{port}", flush=True)
        await loop()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=9091)
    args = parser.parse_args()
    asyncio.run(main(args.host, args.port))
