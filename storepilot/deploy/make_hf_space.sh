#!/usr/bin/env bash
# Copy the StorePilot AI demo into a Hugging Face Space folder (a git clone of the Space).
#
#   git clone https://huggingface.co/spaces/<user>/storepilot-ai ~/storepilot-ai-space
#   ./deploy/make_hf_space.sh ~/storepilot-ai-space
#   cd ~/storepilot-ai-space && git add -A && git commit -m "Update StorePilot AI" && git push
#
# The Space gets: agent/, storepilot/ (backend, frontend source, deploy), the Dockerfile at its root and a
# README.md with the Space settings. Secrets (NEBIUS_API_KEY, STOREPILOT_UPLOAD_PASSWORD) are set in the
# Space settings, never copied.
set -eu
DEST="${1:?usage: $0 <space folder>}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"      # storepilot/
REPO="$(dirname "$ROOT")"
[ -d "$DEST/.git" ] || { echo "$DEST is not a git clone of the Space"; exit 1; }

rm -rf "$DEST/agent" "$DEST/storepilot"
copy() {  # copy a folder without local / generated files
    (cd "$REPO" && tar cf - --exclude='node_modules' --exclude='.venv' --exclude='venv' --exclude='__pycache__' \
        --exclude='*.egg-info' --exclude='dist' --exclude='data' --exclude='logs' --exclude='.env' --exclude='.env.*' \
        --exclude='tsconfig.tsbuildinfo' "$1") | (cd "$DEST" && tar xf -)
}
copy agent
mkdir -p "$DEST/storepilot"
for part in backend frontend deploy; do copy "storepilot/$part"; done
cp "$ROOT/Dockerfile" "$DEST/Dockerfile"
cp "$REPO/.dockerignore" "$DEST/.dockerignore"
cp "$REPO/LICENSE" "$DEST/LICENSE" 2>/dev/null || true
cat > "$DEST/README.md" <<'MD'
---
title: StorePilot AI
emoji: 🤖
colorFrom: purple
colorTo: green
sdk: docker
app_port: 8080
pinned: true
license: apache-2.0
short_description: Dark-store picking robot planned by NVIDIA Nemotron
---

# StorePilot AI

A dark-store picking robot: a customer orders in plain language, **NVIDIA Nemotron** (Nano, Super, Ultra on
Nebius Token Factory) understands the order, plans every step and writes the reports, and **NVIDIA GR00T N1.7**
picks the items with an SO-100 arm on a Jetson AGX Orin.

This Space runs the dashboard with a **simulated robot**: try an order like *"2 biscuits and one fevicol"*,
press Start and watch the Live mission tab. Videos of the real robot are on the Real robot tab.

Built for the Nebius x NVIDIA Global AI Hackathon 2026 (Physical AI track).
MD
echo "Space folder ready: $DEST"
echo "Next: cd $DEST && git add -A && git commit -m 'Update StorePilot AI' && git push"
