"""Settings only the Streamlit app uses (the shared ones are in store_agent.config)."""

from store_agent.config import get_setting

# rosbridge on the Jetson, for the live map on the owner dashboard (browser connects to it)
ROSBRIDGE_URL = get_setting("ROSBRIDGE_URL", "ws://192.168.149.1:9090")
# Laser position on the robot: "x,y,yaw" (meters, radians) from base_link. "0,0,0" = ignore.
LASER_OFFSET = get_setting("LASER_OFFSET", "0,0,0")
