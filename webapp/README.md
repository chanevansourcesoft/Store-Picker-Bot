# QuickPick: Dark Store Order Picker

A mobile robot (mecanum base + SO-100 arm on Jetson AGX) picks and packs online grocery orders.
**NVIDIA Nemotron** (via Nebius Token Factory) reads the order, plans the route, tells the robot
what to do and writes the bill. **NVIDIA GR00T** runs the pick skill on the robot.

Built for the Nebius x NVIDIA Global AI Hackathon 2026 (Physical AI track).

- **Customer kiosk**: pick products or type an order ("2 soups and chips"), pay (fake), watch the robot pack it.
- **Store control**: live robot map, Nemotron step log, orders, stock, emergency STOP.

No robot? The app runs in **demo mode** with a simulated robot inside the app.

## Run locally

Needs Python 3.10 or newer.

```bash
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt

cp .streamlit/secrets.toml.example .streamlit/secrets.toml
# put your NEBIUS_API_KEY in .streamlit/secrets.toml (or in a .env file)

DEMO_MODE=1 streamlit run app/Home.py
```

Open http://localhost:8501. With `DEMO_MODE=1` the app uses the simulated robot.

To use a robot server, leave out `DEMO_MODE` and set `ROBOT_URL`
(default `http://localhost:8000`). If the robot does not answer when the app
starts, the app switches to demo mode by itself. Restart the app after the robot is on.

Optional, a mock robot server over HTTP (same API as the real robot):

```bash
pip install uvicorn
python robot_server/mock_robot.py   # runs on :8000
```

Tests:

```bash
python -m app.test_store
python -m app.test_agent
python -m app.test_worker
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

MIT. See [LICENSE](LICENSE).
