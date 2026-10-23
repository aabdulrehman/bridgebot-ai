#!/usr/bin/env bash
# BridgeBot one-line installer (Linux + macOS).
# Usage:
#   curl -fsSL https://bridgebot-ai.onrender.com/downloads/install.sh | bash
#   curl -fsSL …/install.sh | bash -s -- bbk_YOUR_KEY
#   BRIDGEBOT_API_KEY=bbk_… bash install.sh
set -euo pipefail

BASE="${BRIDGEBOT_BASE:-https://bridgebot-ai.onrender.com}"
KEY="${1:-${BRIDGEBOT_API_KEY:-}}"
NAME="${BRIDGEBOT_MACHINE_NAME:-$(hostname -s 2>/dev/null || hostname)}"
DIR="${BRIDGEBOT_DIR:-$HOME/bridgebot}"
OS="$(uname -s)"

say() { printf '\n\033[1;32m▸ %s\033[0m\n' "$*"; }
die() { printf '\n\033[1;31m✖ %s\033[0m\n' "$*" >&2; exit 1; }

if [[ -z "$KEY" ]]; then
  printf 'BridgeBot connector key (bbk_… from Dashboard → Add machine): '
  read -r KEY
fi
[[ "$KEY" == bbk_* ]] || die "Key should start with bbk_. Got: ${KEY:0:8}…"

# Python
if command -v python3 >/dev/null 2>&1; then PY=python3
elif command -v python >/dev/null 2>&1; then PY=python
else die "Python 3.9+ is required. Install it, then re-run."; fi
$PY -c 'import sys; raise SystemExit(0 if sys.version_info >= (3,9) else 1)' \
  || die "Need Python 3.9+. Found: $($PY --version)"

say "Installing into $DIR"
mkdir -p "$DIR"
cd "$DIR"

say "Downloading connector"
curl -fsSL "$BASE/downloads/local_connector.py" -o local_connector.py
curl -fsSL "$BASE/downloads/requirements.txt" -o requirements.txt
curl -fsSL "$BASE/downloads/connector_config.example.json" -o connector_config.example.json || true

say "Creating venv + deps"
$PY -m venv .venv
# shellcheck disable=SC1091
source .venv/bin/activate
pip install -q --upgrade pip
pip install -q -r requirements.txt

# Optional Linux system helpers for remote desktop (best effort)
if [[ "$OS" == "Linux" ]] && command -v apt-get >/dev/null 2>&1; then
  if command -v sudo >/dev/null 2>&1; then
    say "Installing xdotool + xclip (sudo; skip with Ctrl+C if you prefer not to)"
    sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq xdotool xclip 2>/dev/null || true
  fi
  # Optional pynput fallback (needs python3-dev / a compiler). Safe to skip — xdotool is enough.
  pip install -q pynput 2>/dev/null || true
fi

cat > connector_config.json <<JSON
{
  "server_url": "$BASE",
  "api_key": "$KEY",
  "machine_name": "$NAME",
  "remote_desktop": true,
  "skills_dir": "./skills",
  "agents": [{"name": "default", "type": "generic"}]
}
JSON
chmod 600 connector_config.json
mkdir -p skills ~/.bridgebot

# Autostart
if [[ "$OS" == "Darwin" ]]; then
  say "Installing macOS LaunchAgent (runs at login)"
  PLIST="$HOME/Library/LaunchAgents/ai.bridgebot.connector.plist"
  mkdir -p "$(dirname "$PLIST")"
  cat > "$PLIST" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>ai.bridgebot.connector</string>
  <key>ProgramArguments</key>
  <array>
    <string>$DIR/.venv/bin/python</string>
    <string>$DIR/local_connector.py</string>
    <string>--config</string>
    <string>$DIR/connector_config.json</string>
    <string>--remote-desktop</string>
  </array>
  <key>WorkingDirectory</key><string>$DIR</string>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>StandardOutPath</key><string>$DIR/connector.log</string>
  <key>StandardErrorPath</key><string>$DIR/connector.log</string>
</dict></plist>
PLIST
  launchctl unload "$PLIST" 2>/dev/null || true
  launchctl load "$PLIST"
  say "Starting connector"
  launchctl start ai.bridgebot.connector || true
  cat <<MAC

════════════════════════════════════════════════════════════
  macOS permissions (required once for Remote Desktop)
════════════════════════════════════════════════════════════
  System Settings → Privacy & Security:

  1) Screen Recording  → enable for Terminal (or iTerm)
                         AND for Python / the BridgeBot venv
  2) Accessibility     → enable the same apps (mouse/keyboard)

  Then log out/in (or reboot), open:
    $BASE/remote/
════════════════════════════════════════════════════════════
MAC
else
  say "Installing Linux systemd user service"
  UNIT_DIR="$HOME/.config/systemd/user"
  mkdir -p "$UNIT_DIR"
  # Guess DISPLAY / XAUTHORITY for the logged-in desktop
  DISP="${DISPLAY:-:0}"
  XAUTH="${XAUTHORITY:-$HOME/.Xauthority}"
  cat > "$UNIT_DIR/bridgebot-connector.service" <<UNIT
[Unit]
Description=BridgeBot AI local connector
After=network-online.target default.target

[Service]
Type=simple
WorkingDirectory=$DIR
ExecStart=$DIR/.venv/bin/python $DIR/local_connector.py --config $DIR/connector_config.json --remote-desktop
Restart=always
RestartSec=5
Environment=BRIDGEBOT_REMOTE_DESKTOP=1
Environment=DISPLAY=$DISP
Environment=XAUTHORITY=$XAUTH

[Install]
WantedBy=default.target
UNIT
  systemctl --user daemon-reload
  systemctl --user enable --now bridgebot-connector.service
  # Keep running after logout (best-effort)
  loginctl enable-linger "$(id -un)" 2>/dev/null || true
fi

say "Done. Machine should show ONLINE in the dashboard within ~15s."
echo "  Dashboard:  $BASE/dashboard/"
echo "  Remote:     $BASE/remote/"
echo "  Logs:       $DIR/connector.log  (or: journalctl --user -u bridgebot-connector -f)"
echo "  Kill switch: touch ~/.bridgebot/remote_desktop_off"
