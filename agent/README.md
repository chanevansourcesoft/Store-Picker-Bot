# agent

Shared code for the store apps in `../storepilot` (QuickPick and the StorePilot dashboard),
and the robot API server that runs on the Jetson.

```
store_agent/    Nemotron agent served by Nebius Token Factory: order parsing, shelf route,
                planner loop with tools, offline autopilot (no API key), OrderTracker guards,
                robot client, inventory
robot_server/   Robot API (Contract v2): jetson_server.py (Jetson, Nav2) and mock_robot.py
                (same API, simulated). See robot_server/README.md
```

Install into an app's venv (the apps' install steps do this):

```bash
pip install -e agent            # from the repo root
```

Run the robot server on the Jetson, or the mock robot on a laptop:

```bash
cd agent
uvicorn robot_server.jetson_server:app --host 0.0.0.0 --port 8000
uvicorn robot_server.mock_robot:app --port 8000
```

Tests (no robot, no API key):

```bash
cd agent
python -m store_agent.test_agent
python -m store_agent.test_order_tracker
python -m store_agent.test_autopilot
python -m robot_server.test_stations
python -m robot_server.test_mock_robot
```
