# Robot Server - Contract v2

This folder contains the robot API servers for the Dark Store Order Picker.

## Two Servers

| File | Purpose | Where to run |
|------|---------|--------------|
| `mock_robot.py` | Simulates the robot for testing | Your laptop |
| `jetson_server.py` | Real robot with Nav2 | NVIDIA Jetson |

Both servers have **identical endpoints**, so the AI agent works with either.

## Quick Start

### Mock Robot (for testing)

```bash
cd agent/robot_server     # from the repo root
pip install fastapi uvicorn pyyaml
uvicorn mock_robot:app --host 0.0.0.0 --port 8000
```

Test it:
```bash
curl http://localhost:8000/status
curl http://localhost:8000/map
```

### Jetson Server (for real robot)

1. **Install ROS 2 Humble** (if not already):
   ```bash
   # Follow: https://docs.ros.org/en/humble/Installation/Ubuntu-Install-Debians.html
   sudo apt install ros-humble-desktop ros-humble-nav2-bringup
   ```

2. **Source ROS 2**:
   ```bash
   source /opt/ros/humble/setup.bash
   source ~/your_nav2_ws/install/setup.bash  # If you have a custom workspace
   ```

3. **Install Python dependencies**:
   ```bash
   pip install fastapi uvicorn pyyaml
   ```

4. **Run the server**:
   ```bash
   cd agent/robot_server     # from the repo root
   python jetson_server.py
   ```

5. **Test endpoints**:
   ```bash
   curl http://<jetson-ip>:8000/health
   curl http://<jetson-ip>:8000/status
   ```

## API Endpoints (Contract v2)

### GET /status
Returns robot state:
```json
{
  "busy": false,
  "holding": null,
  "station": "home",
  "pose": {"x": 0.0, "y": 0.0, "theta": 0.0},
  "battery": 95.5,
  "speed": 0.0,
  "nav_state": "idle",
  "obstacle": false,
  "last_error": null,
  "stopped": false
}
```
`stopped` is true after `POST /stop` until `POST /resume` (the dashboard shows "Stopped").

### GET /map
Returns station positions:
```json
{
  "stations": {
    "home": {"x": 0.0, "y": 0.0, "theta": 0.0},
    "shelf_A": {"x": 2.0, "y": 1.0, "theta": 1.57},
    "shelf_B": {"x": 2.0, "y": 2.0, "theta": 1.57},
    "shelf_C": {"x": 2.0, "y": 3.0, "theta": 1.57},
    "customer": {"x": 0.0, "y": 3.0, "theta": 3.14}
  }
}
```

### POST /navigate
Navigate to a station:
```bash
curl -X POST http://localhost:8000/navigate \
  -H "Content-Type: application/json" \
  -d '{"station": "shelf_A"}'
```

Stations: `home`, `shelf_A`, `shelf_B`, `shelf_C`, `customer`

### POST /pick
Pick ONE unit of an item and place it in the basket. This is one GR00T run on the policy
worker: from the arm's rest pose, pick, place in the basket, back to the rest pose. Send only
the item id and its station (`shelf`); the GR00T instruction is looked up in
`policy/instructions.json` (an `instruction` field in the request is ignored).
```bash
curl -X POST http://localhost:8000/pick \
  -H "Content-Type: application/json" \
  -d '{"item": "ink_box", "shelf": "shelf_A"}'
```
Before the arm moves, the server checks (and answers `"retryable": false` otherwise):
- `item` is a product id (`ink_box`: lowercase, no spaces; no empty id, no free text) with an
  instruction in `policy/instructions.json`;
- the catalog (`store_agent/catalog.json`) puts the item at the station `shelf`, and the robot
  is parked there (see Parking below);
- the arm is ready (the policy worker runs, its model loaded), no pick runs, the robot is not stopped.

Then the base is held still for `PICK_SETTLE_SEC` after arriving, and the episode runs.

Success (the unit is in the basket, the arm is empty):
```json
{"success": true, "message": "placed ink_box in basket", "item": "ink_box", "shelf": "shelf_A",
 "level": "upper_shelf", "target": "basket", "instruction": "pick up the ink box and place it in the basket",
 "barcode": null, "pick_try": 1, "outcome": "placed"}
```
Failure: `{"success": false, "message": "no grasp seen: ink_box was not picked", "item": "ink_box",
"pick_try": 1, "outcome": "retry", "retryable": true, "needs_operator": false}`.
- `retryable: true`: an episode ran and failed (no grasp seen, time limit); nothing is in the
  basket and the arm is empty. The agent retries (`PICK_RETRIES`, default 2), then reports the
  item as not picked and goes on with the order.
- `needs_operator: true`: the gripper still holds something. No retry: the server refuses every
  `/pick` and `/navigate` until a person has checked the arm and pressed Resume (`/resume`).
- `outcome`: `placed`, `retry`, `holding`, `stopped`, `refused`, `not_ready` (see `arm.classify`).

Callers count a unit as packed only from `"target": "basket"` and `"item"`, never from the message text.
`barcode` is always `null` for now (no scanning); it is there for a scanner later.

Several units or products on the same rack: call `/pick` again without `/navigate`. While a
pick runs, `/navigate` is refused.

### POST /place
Return one unit of an item from the basket to its own shelf (the robot must be at that shelf).
Putting an item in the basket is part of `/pick`; there is no `/scan`.
```bash
curl -X POST http://localhost:8000/place \
  -H "Content-Type: application/json" \
  -d '{"target": "return", "item": "ink_box"}'
```
The mock robot does it. The Jetson answers `"success": false` ("not available on the robot yet")
until there is a GR00T skill for it.

### POST /stop
Emergency stop: cancels the Nav2 goal, sends zero velocity, and halts the arm where it is
(`stop_arm()` on the policy worker, also mid-episode). The arm then holds where it is; the next
pick first returns to rest by itself. If a pick runs and the worker does not confirm the arm stop,
the answer is `"success": false` with `ARM STOP NOT CONFIRMED` in the message.
```bash
curl -X POST http://localhost:8000/stop
```

### GET /health
`{"status": "ok", "ros_available": ..., "ros_connected": ..., "arm_ready": false, "arm": {"state": "loading", ...}}`.
`arm_ready` is true only when the policy worker answers and its model is loaded. The dashboard
and QuickPick start no order until it is true.

### POST /resume
Resume after emergency stop, or after "the gripper still holds something" (only once a person
has checked the arm):
```bash
curl -X POST http://localhost:8000/resume
```

## Station Map

Positions are (x, y) in meters. They match `STATION_MAP` in `mock_robot.py`.

```
customer (0, 3) ------------- shelf_C (2, 3)
    |                              |
    |                         shelf_B (2, 2)
    |                              |
    |                         shelf_A (2, 1)
    |                              |
home (0, 0) -----------------------+
```

> **Note:** These are example positions. The real positions come from the
> Nav2 map and must be set in stations.yaml by the navigation team.

### Setting the real positions (navigation team)

`jetson_server.py` reads `robot_server/stations.yaml` when it starts:

```bash
cd agent/robot_server     # from the repo root
cp stations.example.yaml stations.yaml
# edit stations.yaml: x, y (meters, Nav2 "map" frame) and theta (radians) for each station
pip install pyyaml        # usually already installed with ROS 2
```

```yaml
stations:
  home:     {x: 0.0, y: 0.0, theta: 0.0}
  shelf_A:  {x: 1.5, y: 0.0, theta: 1.57}
  shelf_B:  {x: 1.5, y: 1.0, theta: 1.57}
  shelf_C:  {x: 1.5, y: 2.0, theta: 1.57}
  customer: {x: 0.0, y: 2.0, theta: 3.14}
```

- All 5 stations are required, with these exact names. `theta` is optional (default 0).
- At start the server prints where the positions come from, for example
  `[Stations] Using stations.yaml`.
- If the file is missing, has a mistake, or PyYAML is not installed, the server prints a
  warning and uses `STATION_MAP` inside `jetson_server.py` (never a mix of both).
- Restart the server after you change the file.
- Test the file: `python -m robot_server.test_stations` (from `agent/`).

## The arm: GR00T policy worker

`arm.py` is the one place that moves the arm. It calls the **policy worker**
(`policy/inference/pick_worker.py`, owned by the policy team; guide: `policy/docs/INFERENCE.md`,
section 5) through `policy/inference/pick_client.py` (standard library only). The worker keeps
GR00T loaded and answers on `PICK_WORKER_URL` (default `http://127.0.0.1:8790`); the arm never
moves unless a pick (or home) is asked for.

- `pick_client.py` is imported from `policy/inference` as it is (never copied):
  `POLICY_INFERENCE_DIR` if set, else `<repo>/policy/inference`. If it is missing, every pick
  fails with a clear message and `/health` says the arm is not ready.
- The worker refuses unknown ids; the robot server never sends them (see `/pick` above).
- Item -> station (rack) comes from the catalog (`catalog.py` reads `store_agent/catalog.json`).
  `policy/instructions.json` `"shelf"` is the shelf LEVEL on the rack (`upper_shelf`,
  `lower_shelf`) and is never compared with the station.
- Things we would like changed in `policy/`: [POLICY_REQUESTS.md](POLICY_REQUESTS.md).

Start the worker first (Jetson): `policy/inference/jetson/pick_worker.sh --model <run>_060000`.

Test without the arm:
- `python -m robot_server.test_jetson_pick` (from `agent/`): fakes the client, and checks the
  real `pick_client.py` against a fake worker.
- Or run the worker in a dry run: `policy/inference/pc/pick_worker.sh --model <name> --dry_run --max_s 10`.
  The model runs but the arm does not move, so every pick ends at the time limit with ok=false
  (the agent retries, then reports the item as not picked).

Keep `STOREPILOT_MANIPULATION_VERIFIED=0` on the dashboard until real picks are tested end to end.

## Parking at a rack

GR00T only works close to where it was recorded: the base within a few cm of the position in
front of the rack, straight or turned slightly. For a rack goal (`shelf_*`) the server sets a
tight goal tolerance on the Nav2 goal checker, checks the parked pose after Nav2 reports
success, and sends the goal again if it is off (up to `PARK_ATTEMPTS`). If it is still off,
`/navigate` fails and no pick is allowed there. Other goals get Nav2's own tolerance back.

| Variable | Default | Meaning |
|---|---|---|
| `PARK_XY_TOLERANCE` | `0.03` | metres from the station position |
| `PARK_YAW_TOLERANCE` | `0.05` | radians (about 3 degrees) |
| `PARK_ATTEMPTS` | `2` | Nav2 goals to park within the tolerance |
| `PICK_SETTLE_SEC` | `1.0` | the base stays still this long after arriving before the arm moves |
| `NAV2_CONTROLLER` | `/controller_server` | node whose goal checker gets the tolerance |
| `NAV2_GOAL_CHECKER` | `general_goal_checker` | goal checker plugin name in the Nav2 params |

The rack's station in `stations.yaml` must be the base position used when recording the
GR00T episodes. `/status` shows the last measured parking error (`parked`).

## Running in Docker (Jetson)

The robot server runs in the ROS image (it needs rclpy and Nav2), with the whole repo mounted
read-only, so it sees `agent/`, `policy/inference/pick_client.py` and `policy/instructions.json`.
Host network: the policy worker (policy container, also host network) is `127.0.0.1:8790`.

```bash
cd ros2_ws
docker compose build                 # once, and after the Dockerfile changes (adds fastapi, uvicorn)
docker compose up -d robot_server    # http://<jetson>:8000
```

## Troubleshooting

### Nav2 not available
```
[WARN] ROS 2 not available. Running in stub mode.
```
Make sure you sourced ROS 2 before running:
```bash
source /opt/ros/humble/setup.bash
```

### Goal rejected by Nav2
Check that:
1. Nav2 is running (`ros2 launch nav2_bringup navigation_launch.py`)
2. You have a valid map loaded
3. The robot is localized (check in RViz)

### Connection refused
Make sure the server is running and listening on the correct port:
```bash
netstat -tlnp | grep 8000
```

## Testing with the AI Agent

1. Start the mock robot on your laptop:
   ```bash
   cd agent
   uvicorn robot_server.mock_robot:app --port 8000
   ```

2. In another terminal, run the Streamlit app:
   ```bash
   cd ../storepilot/quickpick
   streamlit run Home.py
   ```

3. Place an order from the Customer page and watch the Owner dashboard.

## Files

```
robot_server/
├── mock_robot.py      # Mock server for testing
├── jetson_server.py   # Real server for Jetson + Nav2
├── arm.py             # The arm: policy worker client (policy/inference/pick_client.py)
├── instructions.py    # GR00T instruction per item id (policy/instructions.json)
├── catalog.py         # Station (rack) per item (store_agent/catalog.json)
├── stations.example.yaml  # Example station positions (copy to stations.yaml)
├── POLICY_REQUESTS.md # What we would like changed in policy/
├── test_*.py          # Tests (see agent/README.md)
└── README.md          # This file
```
