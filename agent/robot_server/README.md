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
Pick ONE unit of an item and place it in the basket. This is one GR00T run: from the
arm's rest pose, pick, place in the basket, back to the rest pose. Send only the item id
and its shelf; the GR00T instruction is looked up in `policy/instructions.json` (an
`instruction` field in the request is ignored). The robot must be at that shelf.
```bash
curl -X POST http://localhost:8000/pick \
  -H "Content-Type: application/json" \
  -d '{"item": "chips_packet", "shelf": "shelf_A"}'
```
Success (the unit is in the basket, the arm is empty):
```json
{"success": true, "message": "placed chips_packet in basket", "item": "chips_packet", "shelf": "shelf_A",
 "target": "basket", "instruction": "pick up the chips packet and place it in the basket", "barcode": null}
```
Failure (nothing in the basket, the arm is empty, a retry is allowed):
`{"success": false, "message": "grasp failed", "item": "chips_packet"}`.
Callers count a unit as packed only from `"target": "basket"` and `"item"`, never from the message text.
`barcode` is always `null` for now (no scanning); it is there for a scanner later.

### POST /place
Return one unit of an item from the basket to its own shelf (the robot must be at that shelf).
Putting an item in the basket is part of `/pick`; there is no `/scan`.
```bash
curl -X POST http://localhost:8000/place \
  -H "Content-Type: application/json" \
  -d '{"target": "return", "item": "chips_packet"}'
```
The mock robot does it. The Jetson answers `"success": false` ("not available on the robot yet")
until there is a GR00T skill for it.

### POST /stop
Emergency stop:
```bash
curl -X POST http://localhost:8000/stop
```

### POST /resume
Resume after emergency stop:
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

## Connecting the GR00T policy (the one stub left)

Navigation is real (Nav2). The arm is the one stub: `arm.py`.

- `/pick` looks up the instruction for the item in `policy/instructions.json` (`instructions.py`),
  then calls `arm.run_pick(item, instruction)` in a worker thread.
- `run_pick` must run ONE GR00T episode (rest pose -> pick -> place in basket -> rest pose) and
  return `(ok, message)`. Today it waits 1.5 s and returns success without moving anything.
- Replace its body with the pick function from `policy/`, for example
  `from policy.pick import pick_item; return pick_item(item, instruction)` (name to be agreed).
- If it raises, `/pick` answers a failure and the server keeps running.
- Only after this is real and tested, set `STOREPILOT_MANIPULATION_VERIFIED=1` on the dashboard.

Test without the arm: `python -m robot_server.test_jetson_pick` (from `agent/`).

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
├── stations.example.yaml  # Example station positions (copy to stations.yaml)
├── test_stations.py   # Tests for loading stations.yaml
└── README.md          # This file
```
