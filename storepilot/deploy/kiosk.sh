#!/usr/bin/env bash
# Opens the StorePilot dashboard full screen on the LCD (Chromium kiosk, Wayland/labwc).
# Started at login by ~/.config/autostart/storepilot-kiosk.desktop (see install.sh).
# - waits until the backend answers
# - relaunches Chromium if it crashes or is closed
# - stops for good when ~/.config/storepilot/kiosk-disabled exists (storepilotctl kiosk-off)
set -u
URL="${STOREPILOT_URL:-http://127.0.0.1:8080}"
STATE_DIR="$HOME/.local/state/storepilot"
DISABLED="$HOME/.config/storepilot/kiosk-disabled"
PROFILE="$HOME/.config/storepilot/chromium-kiosk"
mkdir -p "$STATE_DIR" "$(dirname "$DISABLED")" "$PROFILE"
exec >>"$STATE_DIR/kiosk.log" 2>&1
echo "$(date '+%F %T') kiosk launcher started"

if [ -e "$DISABLED" ]; then echo "kiosk disabled ($DISABLED), exiting"; exit 0; fi

for i in $(seq 1 120); do
  curl -sf "$URL/api/health" >/dev/null && break
  sleep 1
done

# Chromium remembers "crashed last time" and shows a bubble: clear that flag
sed -i 's/"exited_cleanly":false/"exited_cleanly":true/; s/"exit_type":"Crashed"/"exit_type":"Normal"/' \
  "$PROFILE/Default/Preferences" 2>/dev/null || true

FLAGS=(
  --kiosk "$URL"
  --user-data-dir="$PROFILE"
  --ozone-platform=wayland
  --touch-events=enabled
  --noerrdialogs --disable-infobars --no-first-run --no-default-browser-check
  --disable-session-crashed-bubble --disable-features=TranslateUI
  --overscroll-history-navigation=0 --disable-pinch
  --password-store=basic --check-for-update-interval=31536000
)
if [ "${1:-}" = "--dry-run" ]; then echo chromium "${FLAGS[@]}"; exit 0; fi

while [ ! -e "$DISABLED" ]; do
  echo "$(date '+%F %T') starting chromium"
  chromium "${FLAGS[@]}"
  echo "$(date '+%F %T') chromium exited with $?, restarting in 3 s"
  sleep 3
done
echo "$(date '+%F %T') kiosk disabled, launcher stopped"
