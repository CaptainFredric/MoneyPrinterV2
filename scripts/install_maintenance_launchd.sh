#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")"/.. && pwd)"
LAUNCH_PLIST="$HOME/Library/LaunchAgents/com.mpv2.maintenance.plist"
PYTHON="$ROOT/.runtime-venv/bin/python"

cat > "$LAUNCH_PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
  <dict>
    <key>Label</key>
    <string>com.mpv2.maintenance</string>
    <key>ProgramArguments</key>
    <array>
      <string>$PYTHON</string>
      <string>$ROOT/scripts/maintenance_runner.py</string>
    </array>
    <key>StartInterval</key>
    <integer>1800</integer>
    <key>RunAtLoad</key>
    <true/>
    <key>StandardOutPath</key>
    <string>$ROOT/logs/maintenance.out.log</string>
    <key>StandardErrorPath</key>
    <string>$ROOT/logs/maintenance.err.log</string>
  </dict>
</plist>
EOF

mkdir -p "$HOME/Library/LaunchAgents"
mkdir -p "$ROOT/logs"

echo "Wrote $LAUNCH_PLIST"
echo "To load now: launchctl load -w $LAUNCH_PLIST"
echo "To unload: launchctl unload -w $LAUNCH_PLIST"
