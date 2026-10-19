#!/usr/bin/env bash
# One-time setup on Raspberry Pi OS 64-bit (no sudo needed). Safe to run again.
#   1. Node.js 22 LTS in ~/.local/opt/node (if missing)  2. backend venv  3. frontend build
#   4. systemd user service (starts at boot)  5. kiosk autostart  6. storepilotctl in ~/.local/bin
set -euo pipefail
ROOT="$(cd "$(dirname "$(readlink -f "$0")")/.." && pwd)"
NODE_DIR="$HOME/.local/opt/node"
export PATH="$NODE_DIR/bin:$PATH"

echo "==> StorePilot in $ROOT"
if ! command -v node >/dev/null; then
  V=$(curl -s https://nodejs.org/dist/index.json | python3 -c "import json,sys; print(next(r['version'] for r in json.load(sys.stdin) if r['version'].startswith('v22.') and r['lts']))")
  echo "==> Installing Node.js $V to $NODE_DIR"
  mkdir -p "$HOME/.local/opt" && cd "$HOME/.local/opt"
  curl -sSfLO "https://nodejs.org/dist/$V/node-$V-linux-arm64.tar.xz"
  curl -sSfL "https://nodejs.org/dist/$V/SHASUMS256.txt" | grep "node-$V-linux-arm64.tar.xz" | sha256sum -c -
  tar xf "node-$V-linux-arm64.tar.xz" && rm "node-$V-linux-arm64.tar.xz" && ln -sfn "node-$V-linux-arm64" node
fi

echo "==> Backend venv"
[ -d "$ROOT/backend/.venv" ] || python3 -m venv "$ROOT/backend/.venv"
"$ROOT/backend/.venv/bin/pip" install -q -r "$ROOT/backend/requirements.txt"
"$ROOT/backend/.venv/bin/pip" install -q -e "$ROOT/../agent"   # shared agent (planner, parser, guards)
[ -f "$ROOT/.env" ] || { cp "$ROOT/.env.example" "$ROOT/.env"; chmod 600 "$ROOT/.env"; echo "   created .env (mock mode)"; }

echo "==> Frontend build"
(cd "$ROOT/frontend" && npm ci --no-audit --no-fund && npm run build)

echo "==> systemd user service"
mkdir -p "$HOME/.config/systemd/user" "$HOME/.local/state/storepilot"
sed "s|@ROOT@|$ROOT|g" "$ROOT/deploy/storepilot-backend.service" > "$HOME/.config/systemd/user/storepilot-backend.service"
systemctl --user daemon-reload
systemctl --user enable storepilot-backend
systemctl --user restart storepilot-backend
loginctl show-user "$USER" -p Linger | grep -q yes || echo "   NOTE: run 'sudo loginctl enable-linger $USER' so it starts before login"

echo "==> Kiosk autostart"
chmod +x "$ROOT/deploy/kiosk.sh" "$ROOT/deploy/storepilotctl"
mkdir -p "$HOME/.config/autostart" "$HOME/.local/bin"
sed "s|@ROOT@|$ROOT|g" "$ROOT/deploy/storepilot-kiosk.desktop" > "$HOME/.config/autostart/storepilot-kiosk.desktop"
ln -sfn "$ROOT/deploy/storepilotctl" "$HOME/.local/bin/storepilotctl"

echo "==> Done. Backend: http://127.0.0.1:8080  ·  kiosk opens at next login (or: storepilotctl kiosk-on)"
