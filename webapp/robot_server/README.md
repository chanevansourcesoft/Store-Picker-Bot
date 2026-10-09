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
cd robot_server
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
   cd robot_server
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
  "last_error": null
}
```

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
Pick an item from a shelf:
```bash
curl -X POST http://localhost:8000/pick \
  -H "Content-Type: application/json" \
  -d '{"item": "soup_can", "shelf": "shelf_A", "instruction": "Pick the red can from bin A1"}'
```

### POST /scan
Scan the barcode of held item:
```bash
curl -X POST http://localhost:8000/scan
```

### POST /place
Place item in basket or return to shelf:
```bash
# Put in basket
curl -X POST http://localhost:8000/place \
  -H "Content-Type: application/json" \
  -d '{"target": "basket"}'

# Return to shelf
curl -X POST http://localhost:8000/place \
  -H "Content-Type: application/json" \
  -d '{"target": "return"}'
```

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
cd robot_server
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
- Test the file: `python -m robot_server.test_stations` (from the project root).

## Implementing the Stubs

The `jetson_server.py` has working Nav2 code but stubs for:

1. **Pick item** - Integrate your GR00T policy or arm controller
2. **Scan barcode** - Integrate your barcode scanner
3. **Place item** - Integrate your arm controller

Look for `# TODO:` comments in the code.

### Example: Integrating GR00T Policy

```python
@app.post("/pick")
async def pick_item(req: PickRequest):
    # ... existing checks ...

    # Call your GR00T policy
    from groot_client import execute_pick_skill
    success = await execute_pick_skill(
        item=req.item,
        shelf=req.shelf,
        instruction=req.instruction  # Natural language for the policy
    )

    if success:
        robot_state.holding = req.item
        return {"success": True, "message": f"Picked {req.item}"}
    else:
        return {"success": False, "message": "Pick failed"}
```

### Example: Integrating Barcode Scanner

```python
@app.post("/scan")
async def scan_item():
    # ... existing checks ...

    # Call your barcode scanner
    import cv2
    from pyzbar import pyzbar

    cap = cv2.VideoCapture(0)
    ret, frame = cap.read()
    barcodes = pyzbar.decode(frame)
    cap.release()

    if barcodes:
        barcode = barcodes[0].data.decode("utf-8")
        return {"success": True, "barcode": barcode, "product": item}
    else:
        return {"success": False, "message": "No barcode found"}
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
   cd robot_server
   uvicorn mock_robot:app --port 8000
   ```

2. In another terminal, run the Streamlit app:
   ```bash
   cd ..
   streamlit run app/Home.py
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
