"""Run the dashboard server with the host and port from storepilot/.env:
    python -m storepilot        (from storepilot/backend)
STOREPILOT_HOST=127.0.0.1 = this Pi only, 0.0.0.0 = also other devices on the network.
"""
import uvicorn

from . import config

uvicorn.run("storepilot.main:app", host=config.HOST, port=config.PORT, access_log=False)
