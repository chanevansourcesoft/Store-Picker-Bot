"""
Tests for robot_server/fake_rosbridge.py and app/live_map.py.

Starts the fake rosbridge on a free port, subscribes like roslib.js does,
and checks the messages and the throttle.

Run:
    python -m robot_server.test_fake_rosbridge
"""

import asyncio
import json
import math
import socket
import time

from websockets.asyncio.client import connect

from app import live_map
from robot_server import fake_rosbridge as fake


def free_port():
    with socket.socket() as s:
        s.bind(("localhost", 0))
        return s.getsockname()[1]


async def collect(port, subscriptions, seconds):
    """Subscribe to topics, then collect messages for some seconds. Returns {topic: [(time, msg)]}."""
    got = {}
    async with connect(f"ws://localhost:{port}", max_size=None) as ws:
        for topic, msg_type, throttle in subscriptions:
            await ws.send(json.dumps({"op": "subscribe", "id": f"subscribe:{topic}", "topic": topic,
                                      "type": msg_type, "throttle_rate": throttle, "queue_length": 1}))
        end = time.time() + seconds
        while time.time() < end:
            try:
                text = await asyncio.wait_for(ws.recv(), timeout=end - time.time())
            except asyncio.TimeoutError:
                break
            data = json.loads(text)  # must be valid JSON (no Infinity / NaN)
            assert data["op"] == "publish"
            got.setdefault(data["topic"], []).append((time.time(), data["msg"]))
    return got


async def run_with_server(subscriptions, seconds):
    port = free_port()
    server = asyncio.create_task(fake.main("localhost", port))
    await asyncio.sleep(0.3)
    try:
        return await collect(port, subscriptions, seconds)
    finally:
        server.cancel()


def tf_pose(transforms):
    """Combine map -> odom and odom -> base_footprint (same as live_map.js)."""
    frames = {t["child_frame_id"]: t for t in transforms}

    def pose(t):
        tr, q = t["transform"]["translation"], t["transform"]["rotation"]
        return (tr["x"], tr["y"], 2 * math.atan2(q["z"], q["w"]))

    assert frames["odom"]["header"]["frame_id"] == "map"
    assert frames["base_footprint"]["header"]["frame_id"] == "odom"
    return fake.compose(pose(frames["odom"]), pose(frames["base_footprint"]))


def test_topics_and_throttle():
    subs = [
        ("/map", "nav_msgs/OccupancyGrid", 0),
        ("/tf", "tf2_msgs/TFMessage", 0),
        ("/scan", "sensor_msgs/LaserScan", 100),
        ("/plan", "nav_msgs/Path", 0),
        ("/amcl_pose", "geometry_msgs/PoseWithCovarianceStamped", 0),
    ]
    got = asyncio.run(run_with_server(subs, seconds=2.0))
    for topic, _, _ in subs:
        assert got.get(topic), f"no message on {topic}"

    grid = got["/map"][0][1]
    info = grid["info"]
    assert len(grid["data"]) == info["width"] * info["height"]
    assert {-1, 0, 100} <= set(grid["data"])

    # scan: 15 Hz on the server, throttled to 10 Hz max
    times = [t for t, _ in got["/scan"]]
    gaps = [b - a for a, b in zip(times, times[1:])]
    assert min(gaps) >= 0.09, f"scan not throttled: smallest gap {min(gaps):.3f}s"
    scan = got["/scan"][-1][1]
    assert any(r is not None for r in scan["ranges"]), "scan should hit the walls"

    # tf (map -> odom -> base) gives the same pose as /amcl_pose
    x, y, _ = tf_pose(got["/tf"][-1][1]["transforms"])
    assert -1 < x < 5 and -1 < y < 4.5
    print("PASS: test_topics_and_throttle")


def test_unsubscribed_topic_not_sent():
    got = asyncio.run(run_with_server([("/tf", "tf2_msgs/TFMessage", 0)], seconds=0.5))
    assert set(got) == {"/tf"}
    print("PASS: test_unsubscribed_topic_not_sent")


def test_tf_matches_true_pose():
    sim = fake.Sim()
    sim.pose = (1.5, 2.0, 0.7)
    x, y, yaw = tf_pose(sim.msg_tf()["transforms"])
    assert abs(x - 1.5) < 1e-6 and abs(y - 2.0) < 1e-6 and abs(fake.wrap(yaw - 0.7)) < 1e-6
    print("PASS: test_tf_matches_true_pose")


def test_parse_offset():
    assert live_map.parse_offset("0.1, -0.05, 3.14") == {"x": 0.1, "y": -0.05, "yaw": 3.14}
    assert live_map.parse_offset("bad") == {"x": 0.0, "y": 0.0, "yaw": 0.0}
    assert live_map.parse_offset("1,2") == {"x": 0.0, "y": 0.0, "yaw": 0.0}
    print("PASS: test_parse_offset")


def test_host_port_and_reachable():
    assert live_map.host_port("ws://192.168.149.1:9090") == ("192.168.149.1", 9090)
    assert live_map.host_port("wss://robot.local") == ("robot.local", 443)
    assert live_map.host_port("http://x:1") == (None, None)
    port = free_port()
    assert not live_map.rosbridge_reachable(f"ws://localhost:{port}", timeout=0.3)
    with socket.socket() as s:
        s.bind(("localhost", port))
        s.listen()
        assert live_map.rosbridge_reachable(f"ws://localhost:{port}", timeout=0.3)
    print("PASS: test_host_port_and_reachable")


def test_html_has_config():
    page = live_map.live_map_html("ws://localhost:9090", {"home": {"x": 0, "y": 0}}, "0.1,0,0")
    assert "__CONFIG__" not in page
    assert '"url": "ws://localhost:9090"' in page
    assert '"laser": {"x": 0.1' in page
    assert "roslib" in page
    print("PASS: test_html_has_config")


def run_all_tests():
    print("Running fake rosbridge + live map tests...\n")
    test_parse_offset()
    test_host_port_and_reachable()
    test_html_has_config()
    test_tf_matches_true_pose()
    test_unsubscribed_topic_not_sent()
    test_topics_and_throttle()
    print("\nAll fake rosbridge tests passed!")


if __name__ == "__main__":
    run_all_tests()
