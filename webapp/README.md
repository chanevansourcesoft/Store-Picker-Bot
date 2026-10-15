# QuickPick: Dark Store Order Picker

A mobile robot (mecanum base + SO-100 arm on Jetson AGX) picks and packs online grocery orders.
**NVIDIA Nemotron** (via Nebius Token Factory) reads the order, plans the route, tells the robot
what to do and writes the bill. **NVIDIA GR00T** runs the pick skill on the robot.

Built for the Nebius x NVIDIA Global AI Hackathon 2026 (Physical AI track).

- **Customer kiosk**: pick products or type an order ("2 soups and chips"), pay (fake), watch the robot pack it.
- **Store control**: live robot map, Nemotron step log, orders, stock, emergency STOP.

No robot? The app runs in **demo mode** with a simulated robot inside the app.

## How to start the app

### Step 1: Install (only the first time)

Needs Python 3.10 or newer. Run all commands from the project folder.

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### Step 2: Add your Nebius API key (only the first time)

Nemotron runs on Nebius Token Factory, so the app needs a key. Choose ONE way:

- **`.env` file** in the project folder:
  ```
  NEBIUS_API_KEY=your-key
  ```
- **or Streamlit secrets:**
  ```bash
  cp .streamlit/secrets.toml.example .streamlit/secrets.toml
  # then open .streamlit/secrets.toml and put your key there
  ```

Never commit the key. `.env` and `.streamlit/secrets.toml` are in `.gitignore`.

### Step 3: Start the app

Every time, first activate the venv:

```bash
source venv/bin/activate
```

Then pick ONE of these 3 ways:

**A. Demo mode (no robot, easiest).** The simulated robot runs inside the app.

```bash
DEMO_MODE=1 streamlit run app/Home.py
```

**B. Mock robot server (no robot, same HTTP API as the real robot).** Use 2 terminals.

```bash
# Terminal 1: the mock robot on http://localhost:8000
python robot_server/mock_robot.py

# Terminal 2: the app
streamlit run app/Home.py
```

**C. Real robot (Jetson).** Start the robot server on the Jetson first
(see [robot_server/README.md](robot_server/README.md)), then:

```bash
ROBOT_URL=http://<jetson-ip>:8000 streamlit run app/Home.py
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
- Orders are saved in `data/orders.db`. The app creates it by itself.
  To start with no orders, stop the app and delete `data/orders.db`.

### Optional: live map without the robot

```bash
# Terminal 1: fake rosbridge on ws://localhost:9090
python -m robot_server.fake_rosbridge

# Terminal 2: the app
ROSBRIDGE_URL=ws://localhost:9090 DEMO_MODE=1 streamlit run app/Home.py
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

## Run the tests

The tests need no robot and no API key.

```bash
source venv/bin/activate
python -m app.test_store
python -m app.test_agent
python -m app.test_worker
python -m app.test_order_tracker
python -m app.test_step_log
python -m robot_server.test_stations
python -m robot_server.test_mock_robot
python -m robot_server.test_fake_rosbridge
```

## Deploy (Streamlit Community Cloud)

1. Push this repo to GitHub. Do not commit `.env` or `.streamlit/secrets.toml`
   (they are in `.gitignore`).
2. Go to https://share.streamlit.io, click **Create app**, and choose your repo and branch.
3. Main file path: `app/Home.py`. In **Advanced settings**, choose Python 3.10 or newer.
4. In **Secrets**, paste:
   ```toml
   NEBIUS_API_KEY = "your-key"
   DEMO_MODE = "1"
   ```
5. Click **Deploy**. The app creates `data/orders.db` and the stock by itself on first start.

Note: on Community Cloud the disk is not permanent. Orders and stock go back to the
starting values when the app restarts.

## License

Apache 2.0. See [LICENSE](../LICENSE) in the repo root.
