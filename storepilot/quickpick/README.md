# QuickPick: Dark Store Order Picker

A mobile robot (mecanum base + SO-100 arm on Jetson AGX) picks and packs online grocery orders.
**NVIDIA Nemotron** (via Nebius Token Factory) reads the order, plans the route, tells the robot
what to do and writes the bill. **NVIDIA GR00T** runs the pick skill on the robot.

Built for the Nebius x NVIDIA Global AI Hackathon 2026 (Physical AI track).

- **Customer kiosk**: pick products or type an order ("2 chips and a frooti"), pay (fake), watch the robot pack it.
- **Store control**: needs-attention alerts, KPIs (pack time, pick success), live robot map,
  step log for any order, orders per hour, orders (filter, cancel, CSV), stock, emergency STOP.

No robot? The app runs in **demo mode** with a simulated robot inside the app.

## How to start the app

### Step 1: Install (only the first time)

Needs Python 3.10 or newer. Run all commands from this folder (`storepilot/quickpick`).

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt   # also installs the shared agent from ../../agent
```

### Step 2: Add your Nebius API key (only the first time)

Nemotron runs on Nebius Token Factory, so the app needs a key. Choose ONE way:

- **`.env` file** in this folder or in `storepilot/` (shared with the StorePilot dashboard):
  ```
  NEBIUS_API_KEY=your-key
  ```
- **or Streamlit secrets:**
  ```bash
  cp .streamlit/secrets.toml.example .streamlit/secrets.toml
  # then open .streamlit/secrets.toml and put your key there
  ```

Never commit the key. `.env` and `.streamlit/secrets.toml` are in `.gitignore`.

**No key?** The app still works. Orders are planned by a built-in offline **autopilot**
(same robot commands and code checks as Nemotron), typed orders use a simple keyword
reader, and the shift summary is rule-based. The dashboard shows a note when this is on.

### Step 3: Start the app

Every time, first activate the venv:

```bash
source venv/bin/activate
```

Then pick ONE of these 3 ways:

**A. Demo mode (no robot, easiest).** The simulated robot runs inside the app.

```bash
DEMO_MODE=1 streamlit run Home.py
```

**B. Mock robot server (no robot, same HTTP API as the real robot).** Use 2 terminals.

```bash
# Terminal 1: the mock robot on http://localhost:8000
python -m robot_server.mock_robot

# Terminal 2: the app
streamlit run Home.py
```

**C. Real robot (Jetson).** Start the robot server on the Jetson first
(see [agent/robot_server/README.md](../../agent/robot_server/README.md)), then:

```bash
ROBOT_URL=http://<jetson-ip>:8000 streamlit run Home.py
```

You can also put `ROBOT_URL=...` in `.env` instead of typing it each time.

### Step 4: Open the app

Open http://localhost:8501 in the browser.

- **Home**: choose a page.
- **Customer Kiosk**: pick products, pay (fake), watch the order.
- **Owner Dashboard**: orders, robot status, step log, stock.

On a second device (for example the Raspberry Pi kiosk), open
`http://<this-pc-ip>:8501`.

### Good to know

- The app checks the robot only once, when it starts. If the robot does not
  answer, the app uses demo mode, and both pages show the
  "Demo mode: simulated robot" badge. Turn the robot on first, then restart the app.
- To stop the app, press `Ctrl+C` in the terminal.
- Orders are saved in `storepilot/data/orders.db`. The app creates it by itself.
  To start with no orders, stop the app and delete that file.

### Optional: live map without the robot

```bash
# Terminal 1: fake rosbridge on ws://localhost:9090
python -m quickpick.fake_rosbridge   # run from storepilot/

# Terminal 2: the app
ROSBRIDGE_URL=ws://localhost:9090 DEMO_MODE=1 streamlit run Home.py
```

### All settings

Put them in `.env`, in `.streamlit/secrets.toml`, or before the command.

| Setting | Default | What it does |
|---|---|---|
| `NEBIUS_API_KEY` | (none, needed) | Key for Nebius Token Factory (Nemotron) |
| `DEMO_MODE` | `0` | `1` = always use the simulated robot |
| `ROBOT_URL` | `http://localhost:8000` | Address of the robot server |
| `ROSBRIDGE_URL` | `ws://192.168.149.1:9090` | rosbridge on the Jetson, for the live map |
| `LASER_OFFSET` | `0,0,0` | Laser position from base_link: x,y,yaw (m, rad) |
| `STOCK_FILE` | `<repo>/data/stock.json` | Live stock (runtime, not in Git). Products come from `agent/store_agent/catalog.json` |

## Run the tests

The tests need no robot and no API key.

```bash
source venv/bin/activate
cd ..                                  # storepilot/
python -m quickpick.test_store
python -m quickpick.test_worker
python -m quickpick.test_step_log
python -m quickpick.test_metrics
python -m quickpick.test_fake_rosbridge
cd ../agent                            # shared agent and robot server
python -m store_agent.test_agent
python -m store_agent.test_order_tracker
python -m store_agent.test_autopilot
python -m robot_server.test_stations
python -m robot_server.test_mock_robot
```

## Deploy (Streamlit Community Cloud)

1. Push this repo to GitHub. Do not commit `.env` or `.streamlit/secrets.toml`
   (they are in `.gitignore`).
2. Go to https://share.streamlit.io, click **Create app**, and choose your repo and branch.
3. Main file path: `storepilot/quickpick/Home.py`. In **Advanced settings**, choose Python 3.10 or newer.
   (Not tested since the move: `requirements.txt` installs the shared agent with `-e ../../agent`.)
4. In **Secrets**, paste:
   ```toml
   NEBIUS_API_KEY = "your-key"
   DEMO_MODE = "1"
   ```
5. Click **Deploy**. The app creates `storepilot/data/orders.db` and the stock by itself on first start.

Note: on Community Cloud the disk is not permanent. Orders and stock go back to the
starting values when the app restarts.

## License

Apache 2.0. See [LICENSE](../../LICENSE) in the repo root.
