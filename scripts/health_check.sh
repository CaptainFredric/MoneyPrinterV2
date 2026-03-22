#!/usr/bin/env bash
# scripts/health_check.sh
# Quick daemon + posting health checks for phone SSH sessions.

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

echo "== MoneyPrinterV2 Health Check =="
echo "Root: $ROOT_DIR"
echo ""

launchagent_label="com.moneyprinterv2.daemon"
launchagent_domain="gui/$(id -u)"
launchagent_plist="$HOME/Library/LaunchAgents/com.moneyprinterv2.daemon.plist"

is_launchagent_loaded() {
  if launchctl print "$launchagent_domain/$launchagent_label" >/dev/null 2>&1; then
    return 0
  fi

  if launchctl list 2>/dev/null | awk '{print $3}' | grep -qx "$launchagent_label"; then
    return 0
  fi

  return 1
}

echo "[1] LaunchAgent status"
if is_launchagent_loaded; then
  echo "✅ LaunchAgent loaded: $launchagent_label"
else
  echo "⚠️ LaunchAgent not loaded"
  if [[ -f "$launchagent_plist" ]]; then
    echo "   Try: launchctl bootstrap $launchagent_domain $launchagent_plist"
    echo "   If already loaded but stuck: launchctl bootout $launchagent_domain/$launchagent_label"
  else
    echo "   Missing plist: $launchagent_plist"
  fi
fi

echo ""
echo "[2] Recent daemon log (last 20 lines)"
if [[ -f logs/daemon.log ]]; then
  tail -n 20 logs/daemon.log
else
  echo "⚠️ logs/daemon.log not found"
fi

echo ""
echo "[3] Posting/report summary"
if [[ -x venv/bin/python ]]; then
  venv/bin/python scripts/report.py
else
  echo "⚠️ venv/bin/python not found"
fi
