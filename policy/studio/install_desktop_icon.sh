#!/usr/bin/env bash
# Add "Store Picker Bot" to the applications menu and the desktop of the current user.
# Run it from your own terminal (not over ssh), so the desktop icon can be marked as trusted.
set -euo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
chmod +x "$DIR/run_studio.sh"
NAME="store-picker-bot.desktop"
ENTRY="[Desktop Entry]
Type=Application
Name=Store Picker Bot
Comment=Record and review robot demonstrations
Exec=$DIR/run_studio.sh
Icon=$DIR/web/icon.svg
Terminal=false
Categories=Science;
StartupWMClass=store-picker-bot"
APPS="$HOME/.local/share/applications"
DESKTOP="$(xdg-user-dir DESKTOP 2>/dev/null || echo "$HOME/Desktop")"

mkdir -p "$APPS"
echo "$ENTRY" > "$APPS/$NAME"
# Make the app drawer pick the new entry up right away.
update-desktop-database "$APPS" 2>/dev/null || true

if [[ -d "$DESKTOP" ]]; then
  echo "$ENTRY" > "$DESKTOP/$NAME"
  chmod +x "$DESKTOP/$NAME"
  if ! gio set "$DESKTOP/$NAME" metadata::trusted true 2>/dev/null; then
    echo "Note: right-click the desktop icon and choose 'Allow Launching' once."
  fi
fi
echo "Installed: look for 'Store Picker Bot' in the applications menu${DESKTOP:+ and on the desktop}."
