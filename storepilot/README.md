# StorePilot AI — robot LCD dashboard (Raspberry Pi)

Touchscreen control panel for the store-picking robot. It runs on the Raspberry Pi 5,
opens full screen on the Waveshare LCD at boot, and talks to the Jetson over a small,
explicit HTTP interface. The Jetson stays the source of truth and runs all robotics.

```
 Waveshare LCD (768×1024 portrait)
 └─ Chromium kiosk ──HTTP/WebSocket──► StorePilot backend (FastAPI, :8080, on the Pi)
                                         ├─ SQLite: mission history + events (survives disconnects)
                                         ├─ Nemotron via Nebius Token Factory (key stays on the Pi)
                                         │    order parsing + step planning (shared code in ../webapp/app)
                                         └─ Robot adapter
                                              ├─ mock: built-in simulator (labelled SIMULATED)
                                              └─ live: Jetson robot server, Contract v2 (HTTP)
```

## What it shows

| Page | Contents |
|---|---|
| **Operations** | Robot state (Idle · Planning · Navigating · Picking · Returning · Completed · Paused · Error · Disconnected), mission and "2 of 4 items picked", destination, live mini-map, battery / speed / position / heading / navigation / holding, alerts, latest planner and robot events |
| **Order** | Natural-language order box, tap-to-build product steppers (works without a keyboard), parsed items with shelf and qty, unknown products, items the AI *inferred* (flagged), route, estimate (only after 3+ completed missions), Start / Discard |
| **Map** | Stations, shelves, packing area, robot pose and heading, planned route, current leg, distance to destination, route progress |
| **Activity** | Every mission event, filterable by AI planner / robot / operator / system |
| **System** | Raspberry Pi health, Jetson link, robot telemetry, AI backend, mission history |

**Honest data rule:** a value that is not known is shown as *Unavailable*, never as a number.
In mock mode a striped banner, a "Simulation" badge, a "Simulated map" tag and "sim" tags on
every event make it impossible to mistake simulated data for the robot.

## Safety boundaries (built in)

- The dashboard can only request **high-level** actions (go to a named station, pick a named
  product, scan, place, stop, resume). There is no API for wheel velocities, PWM or joints.
- The LLM never touches actuators directly: its tool calls go through the shared OrderTracker
  guards (no over-picking, return only at the item's own shelf) and the robot adapter.
- **Every mission needs an operator confirmation.** In live mode the operator must also tick
  "path is clear", and starting is impossible unless `STOREPILOT_ENABLE_MOTION=1`.
- **STOP** is always on screen. It halts the planner immediately. It is sent to the Jetson only
  when `JETSON_STOP_CONFIRMED=1`; until then the button says *PLANNER ONLY* and the dashboard
  tells the operator to use the hardware E-stop. "Stopped" is shown only when the robot's
  telemetry confirms it.
- Nothing resumes by itself: Resume clears the stop state but never restarts a mission, and a
  mission that was running when the Pi restarted is marked *interrupted*.
- The hardware emergency stop stays independent of this dashboard.

## Install (Raspberry Pi OS 64-bit, no sudo)

```bash
cd ~/Documents/"Dark Store Robot"/Store-Picker-Bot/storepilot
./deploy/install.sh
```

The installer is safe to run again. It:
1. installs Node.js 22 LTS in `~/.local/opt/node` if missing (checksum verified),
2. creates `backend/.venv` and installs `backend/requirements.txt`,
3. creates `.env` from `.env.example` if missing,
4. builds the frontend (`frontend/dist`, served by the backend),
5. installs and starts the systemd **user** service `storepilot-backend` (starts at boot; this Pi
   already has lingering enabled, otherwise run `sudo loginctl enable-linger $USER` once),
6. adds `~/.config/autostart/storepilot-kiosk.desktop` so the LCD opens the dashboard at login,
7. links `storepilotctl` into `~/.local/bin`.

## Configure (`storepilot/.env`)

| Setting | Default | Meaning |
|---|---|---|
| `STOREPILOT_MODE` | `mock` | `mock` = simulator, `live` = Jetson |
| `JETSON_URL` | – | Jetson robot server, e.g. `http://192.168.2.50:8000` (live mode) |
| `STOREPILOT_ENABLE_MOTION` | `0` | `1` = operators may start missions that move the real robot |
| `JETSON_STOP_CONFIRMED` | `0` | `1` = you tested that `POST /stop` on the Jetson really halts the robot |
| `STOREPILOT_MANIPULATION_VERIFIED` | `0` | `1` = pick / scan / place on the Jetson are real and return verified results. Until then live missions cannot start and "Holding" is not shown |
| `STOREPILOT_VERIFIED_TELEMETRY` | (empty) | Live fields you checked against the robot: any of `pose,battery,speed`. Others show "Unavailable · not verified yet" |
| `NEBIUS_API_KEY` | – | Nemotron on Nebius Token Factory. Without it: keyword parser + offline autopilot |
| `STOREPILOT_PORT` | `8080` | Backend port (kiosk URL uses 8080) |

After editing: `storepilotctl restart`.

## Daily use

```bash
storepilotctl status       # service state + health
storepilotctl restart      # after changing .env
storepilotctl logs         # backend log (~/.local/state/storepilot/backend.log)
storepilotctl kiosk-logs   # kiosk launcher log
storepilotctl kiosk-off    # leave kiosk mode (stays off after reboot)
storepilotctl kiosk-on     # open the kiosk now and at every login
storepilotctl build        # rebuild the frontend after code changes, restart backend
storepilotctl test         # backend tests
```

To use the Pi desktop while the kiosk is open: plug in a keyboard, open a terminal (Ctrl+Alt+T)
and run `storepilotctl kiosk-off`, or run it over SSH.

## Development mode (no kiosk)

```bash
./dev.sh     # backend with auto-reload on :8081 + Vite on :5173 → open http://localhost:5173
```

The installed service on :8080 can keep running. Frontend checks: `cd frontend && npm run typecheck`.

## Tests

```bash
cd backend && .venv/bin/python -m tests.test_backend
```

Covers: simulator motion and stop, an offline mission end to end (nothing moves before confirm),
rejected / discarded orders, STOP aborting a mission without auto-resume, restart → *interrupted*,
live-mode switches (motion disabled, area-clear required, unconfirmed stop never sent),
the Jetson HTTP adapter against a real HTTP server, and disconnect / reconnect reporting.

## Troubleshooting

| Symptom | Check |
|---|---|
| LCD shows "Connecting to the StorePilot backend…" | `storepilotctl status`, then `storepilotctl logs` |
| Service restarts in a loop | `storepilotctl logs`; run the ExecStart command from `~/.config/systemd/user/storepilot-backend.service` by hand |
| Red "Dashboard backend unreachable" bar | Backend crashed or restarting; systemd restarts it in 3 s and the page reconnects by itself |
| "Robot disconnected" in live mode | `curl $JETSON_URL/health` from the Pi; same Wi-Fi? Jetson server running on `0.0.0.0:8000`? |
| "Jetson server runs in STUB mode" | `jetson_server.py` started without ROS 2 sourced: pose / speed / battery are hidden because they are not real |
| Kiosk does not open at login | `storepilotctl kiosk-logs`; is `~/.config/storepilot/kiosk-disabled` present? |
| Orders parsed by "offline keyword parser" | `NEBIUS_API_KEY` missing or Nebius unreachable (System page → AI backend) |

**Restore / remove:** `systemctl --user disable --now storepilot-backend`,
`rm ~/.config/systemd/user/storepilot-backend.service ~/.config/autostart/storepilot-kiosk.desktop ~/.local/bin/storepilotctl`.
Mission history is in `storepilot/data/storepilot.db` (delete it to start fresh).

## Live driving over rosbridge (this robot)

The robot's Nav2 is reached directly through `rosbridge_websocket` on the Jetson. Nothing is
installed or changed in `mecanum_nav`. The dashboard only sends what RViz sends:
`/initialpose` (Set position), `/goal_pose` (Send goal / station), cancel goal + Nav2 lifecycle
pause (STOP), lifecycle resume (Start Nav2 / Resume) and `/map_server/load_map` (switch map).
It never publishes `/cmd_vel`; rosbridge is started with an allow-list so it cannot.

**1. On the Jetson** (after `navigation.launch.py` is running), in a second terminal:

```bash
source ~/ros2_ws/.robotrc
ros2 launch rosbridge_server rosbridge_websocket_launch.xml port:=9090 \
  topics_glob:="[/map, /tf, /tf_static, /odom, /scan, /plan, /ros_robot_controller/battery, /navigate_to_pose/_action/status, /goal_pose, /initialpose]" \
  services_glob:="[/lifecycle_manager_navigation/*, /lifecycle_manager_localization/*, /navigate_to_pose/_action/cancel_goal, /map_server/load_map, /rosapi/get_param]" \
  params_glob:="[*map_server*]"
```

**2. On the Pi** (`storepilot/.env`), then `storepilotctl restart`:

```
STOREPILOT_MODE=live
JETSON_LINK=rosbridge
ROSBRIDGE_URL=ws://192.168.20.185:9090
STOREPILOT_ENABLE_MOTION=0      # 1 = allow Send goal / station drives from the dashboard
JETSON_STOP_CONFIRMED=0         # 1 only after the physical STOP test below
```

**3. On the dashboard → Map:** *Start Nav2* → *Set position* (press where the robot is, drag toward
its heading, release) → *Save stations* per map → *Send goal* or a station button (confirm the path
is clear). *Environment* switches maps with `load_map`; set the position again afterwards.

STOP over rosbridge = cancel all NavigateToPose goals + pause Nav2 (controller and recovery
behaviours deactivate); the base's 0.5 s `cmd_vel` watchdog then stops the wheels. It is shown as
*NOT VERIFIED* until you test it physically and set `JETSON_STOP_CONFIRMED=1`.

Develop without the robot: `cd backend && .venv/bin/python -m tests.fake_nav2_rosbridge` (fake
Nav2 on `ws://127.0.0.1:9091`, starts paused and unlocalized like the real robot), then run the
backend with `ROSBRIDGE_URL=ws://127.0.0.1:9091`.

Things to confirm on the first real connection: the map appears at once (`/map` is a latched
topic; if not, it appears after any *Environment* switch), and STOP reports "goal canceled"
(cancel uses the hidden action service; if rosbridge refuses it, the Nav2 pause still stops the robot).

## Collecting the Jetson facts (read-only)

Nothing here moves the robot or changes the Jetson.

**On the Pi** (finds the Jetson, then checks its API with GET requests only):

```bash
cd ~/Documents/"Dark Store Robot"/Store-Picker-Bot/storepilot
./deploy/probe_jetson.sh                 # lists hosts on this Wi-Fi with port 8000 open
./deploy/probe_jetson.sh <jetson-ip>     # /health, /status, /map, real endpoint list, stub-value check, latency
```

**On the Jetson** (copy the script over, run it where ROS 2 runs; inside the container too if ROS is in Docker):

```bash
scp deploy/jetson_collect_info.sh <user>@<jetson-ip>:~/          # from the Pi
bash ~/jetson_collect_info.sh ~/nav2_ws/install/setup.bash        # on the Jetson; add every setup.bash you normally source
```

It saves `~/storepilot_jetson_report_<time>.txt`: network, robot-server process and ports, `/openapi.json`,
the `jetson_server.py` checksum (repo `main` = `44d798191b7dc679…`, repo today with the `stopped` field =
`1379e7d8d7f7cfc4…`), `stations.yaml`, ROS 2 nodes / topics / actions, **who publishes and subscribes to
`/cmd_vel`**, battery, odometry, AMCL / TF pose, LiDAR rate, Nav2 lifecycle, arm and Teensy devices.

## Physical STOP verification (before `JETSON_STOP_CONFIRMED=1`)

What the current `jetson_server.py` `/stop` does: cancels the Nav2 goal and publishes **one** zero
`Twist` on `/cmd_vel`. It does **not** touch the SO-100 arm (the arm is driven from the LeRobot
policy container), and another `/cmd_vel` publisher can override the single zero message. So with
today's code the arm test below is expected to fail, and the flag must stay `0`.

Test only with the wheels off the ground, the arm in a safe pose, people clear, and a hand on the
hardware E-stop:

```bash
# Jetson, terminal 1: watch what reaches the wheels
ros2 topic echo /cmd_vel
# Jetson, terminal 2: watch the rate (should drop to nothing after stop)
ros2 topic hz /cmd_vel
# Pi: start a short navigation (this is a motion command)
curl -X POST http://<jetson-ip>:8000/navigate -H 'content-type: application/json' -d '{"station":"shelf_A"}' &
# Pi, while the wheels turn:
curl -X POST http://<jetson-ip>:8000/stop
curl http://<jetson-ip>:8000/status
# Pi, afterwards (clears the stop flag; does not move anything by itself):
curl -X POST http://<jetson-ip>:8000/resume
```

Pass only if: the wheels stop within a fraction of a second and stay stopped, no non-zero `/cmd_vel`
appears afterwards, a new `/navigate` is refused until `/resume`, **and** an arm motion in progress
(during teleop or a policy run) also stops. Record what you saw before setting the flag.

## Jetson integration: what is assumed, what is still needed

The live adapter (`backend/storepilot/adapters/jetson.py`) speaks the contract of
`webapp/robot_server/jetson_server.py` (Contract v2): `GET /health /status /map`,
`POST /navigate /pick /scan /place /stop /resume`. Before switching to live mode, please confirm:

1. **Address:** the Jetson's IP on the robot network and that `jetson_server.py` runs on port 8000
   and is reachable from the Pi (`curl http://<jetson>:8000/health`).
2. **Which server runs:** is it `jetson_server.py` from this repo, unchanged? Its `/health` says
   `"healthy"` (the adapter accepts that and `"ok"`).
3. **Stop:** does `POST /stop` really halt the base (Nav2 cancel + zero velocity) and the arm?
   Only then set `JETSON_STOP_CONFIRMED=1`.
4. **Pose source and frame:** `/status.pose` comes from Nav2 feedback only while navigating. Is
   there a continuous pose (AMCL / `map→base_link` TF) we can expose when idle?
5. **Battery and speed:** `/status.battery` stays at its default 100 unless `/battery_state` is
   published, and `speed` is a constant 0.3 while navigating. Which topics give real values?
6. **Pick / scan / place:** these are still timed stubs on the Jetson. Which service will run the
   GR00T pick and barcode / load-cell verification, and will it report failures in the same JSON?
7. **Stations:** is `robot_server/stations.yaml` filled with the real store positions? The map
   draws exactly what `/map` returns.
8. **Map image (optional):** to draw the real occupancy grid, the Jetson would need to serve it
   (for example `GET /map/image` + resolution / origin), or allow rosbridge from the Pi.

Note: the older Streamlit webapp (`../webapp`) checks `/health` for `"ok"` only, so against the real
Jetson (`"healthy"`) it would fall back to its simulator. StorePilot does not have this problem.
