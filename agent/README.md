# agent

Shared code for the store apps in `../storepilot` (QuickPick and the StorePilot dashboard),
and the robot API server that runs on the Jetson.

```
store_agent/    Nemotron agent served by Nebius Token Factory: order parsing, shelf route,
                planner loop with tools, offline autopilot (no API key), OrderTracker guards,
                robot client, catalog.json (products, in Git; same ids as
                policy/instructions.json) and inventory.py (live stock in STOCK_FILE, not in Git)
robot_server/   Robot API (Contract v2): jetson_server.py (Jetson, Nav2, GR00T policy worker)
                and mock_robot.py (same API, simulated). See robot_server/README.md
testdata/       A fixed test store (catalog + instructions, two racks) for the tests
```

In `catalog.json` a product's `"shelf"` is the Nav2 **station** of its rack (`shelf_A`, where
the robot parks) and `"level"` the shelf level on that rack (`upper_shelf`, `lower_shelf`).
`policy/instructions.json` calls the level `"shelf"`; `check_catalog()` compares it with
`"level"` only. Today's store: one rack, `shelf_A`, with `ink_box` (upper) and `biscuit` (lower).

The planner (Nemotron or the offline autopilot) only chooses product ids. Before a pick reaches
the robot, the code refuses empty ids, free text, unknown products, a pick at the wrong station
and more units than ordered. A failed pick is retried by the code (`PICK_RETRIES`, default 2),
then the item is reported as not picked and the order goes on. If the gripper still holds
something, the order stops at once (no more robot commands) and a person must check the arm.

| Setting | Default | Meaning |
|---|---|---|
| `PICK_RETRIES` | `2` | retries of a failed pick (an episode ran but nothing is in the basket) |
| `STORE_CATALOG_FILE` | `store_agent/catalog.json` | another catalog (the tests use `testdata/`) |
| `POLICY_INSTRUCTIONS_FILE` | `<repo>/policy/instructions.json` | GR00T instructions per item id |
| `PICK_WORKER_URL` | `http://127.0.0.1:8790` | policy worker (robot server only) |

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

On the Jetson with Docker: `cd ros2_ws && docker compose up -d robot_server` (see
robot_server/README.md, "Running in Docker"). Start the policy worker first; `/health` reports
`"arm_ready": false` until its model is loaded, and no order starts before that.

Tests (no robot, no API key):

```bash
cd agent
python -m store_agent.test_inventory
python -m store_agent.test_robot_client
python -m store_agent.test_agent
python -m store_agent.test_order_tracker
python -m store_agent.test_autopilot
python -m robot_server.test_stations
python -m robot_server.test_mock_robot
python -m robot_server.test_nav2_client
python -m robot_server.test_jetson_pick
```
