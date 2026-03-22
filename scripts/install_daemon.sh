#!/usr/bin/env bash
# scripts/install_daemon.sh
#
# Installs the MoneyPrinterV2 daemon as a macOS LaunchAgent so it:
#   • Starts automatically at login
#   • Restarts if it crashes
#   • Logs stdout/stderr to logs/daemon.log
#
# Usage:
#   bash scripts/install_daemon.sh           # install & load
#   bash scripts/install_daemon.sh uninstall # unload & remove
#
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PLIST_LABEL="com.moneyprinterv2.daemon"
PLIST_PATH="$HOME/Library/LaunchAgents/${PLIST_LABEL}.plist"
LOG_DIR="$ROOT_DIR/logs"
VENV_PYTHON="$ROOT_DIR/venv/bin/python"
DAEMON_SCRIPT="$ROOT_DIR/scripts/daemon.py"

mkdir -p "$LOG_DIR"

# ── Uninstall ────────────────────────────────────────────────────────────
if [[ "${1:-}" == "uninstall" ]]; then
    if launchctl list | grep -q "$PLIST_LABEL" 2>/dev/null; then
        launchctl unload "$PLIST_PATH" 2>/dev/null || true
        echo "[daemon] Unloaded LaunchAgent."
    fi
    if [[ -f "$PLIST_PATH" ]]; then
        rm "$PLIST_PATH"
        echo "[daemon] Removed plist: $PLIST_PATH"
    fi
    echo "[daemon] Uninstalled."
    exit 0
fi

# ── Sanity checks ────────────────────────────────────────────────────────
if [[ ! -x "$VENV_PYTHON" ]]; then
    echo "[daemon] ERROR: venv Python not found at $VENV_PYTHON"
    echo "[daemon] Run: bash scripts/setup_local.sh"
    exit 1
fi

if [[ ! -f "$DAEMON_SCRIPT" ]]; then
    echo "[daemon] ERROR: daemon script not found at $DAEMON_SCRIPT"
    exit 1
fi

# ── Write plist ──────────────────────────────────────────────────────────
cat > "$PLIST_PATH" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>${PLIST_LABEL}</string>

    <key>ProgramArguments</key>
    <array>
        <string>${VENV_PYTHON}</string>
        <string>${DAEMON_SCRIPT}</string>
    </array>

    <!-- Restart if the process exits for any reason -->
    <key>KeepAlive</key>
    <true/>

    <!-- Start immediately when loaded, and at every login -->
    <key>RunAtLoad</key>
    <true/>

    <!-- Working directory (important for relative paths in the app) -->
    <key>WorkingDirectory</key>
    <string>${ROOT_DIR}</string>

    <!-- Inherit the user environment (PATH, GEMINI_API_KEY, etc.) -->
    <key>EnvironmentVariables</key>
    <dict>
        <key>PATH</key>
        <string>/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin</string>
        <key>HOME</key>
        <string>${HOME}</string>
    </dict>

    <key>StandardOutPath</key>
    <string>${LOG_DIR}/daemon.log</string>

    <key>StandardErrorPath</key>
    <string>${LOG_DIR}/daemon.log</string>

    <!-- Throttle restarts — wait 30 s before restarting on crash -->
    <key>ThrottleInterval</key>
    <integer>30</integer>
</dict>
</plist>
PLIST

echo "[daemon] Wrote plist to $PLIST_PATH"

# ── Load (unload first if already loaded) ───────────────────────────────
launchctl unload "$PLIST_PATH" 2>/dev/null || true
launchctl load   "$PLIST_PATH"

echo ""
echo "✅  MoneyPrinterV2 daemon installed and running."
echo ""
echo "   Status : launchctl list | grep moneyprinter"
echo "   Logs   : tail -f $LOG_DIR/daemon.log"
echo "   Stop   : launchctl unload $PLIST_PATH"
echo "   Remove : bash scripts/install_daemon.sh uninstall"
