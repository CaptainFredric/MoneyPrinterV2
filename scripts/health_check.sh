#!/usr/bin/env bash
# scripts/health_check.sh
# Quick daemon + posting health checks for phone SSH sessions.

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

echo "== MoneyPrinterV2 Health Check =="
echo "Root: $ROOT_DIR"
echo ""

echo "[1] LaunchAgent status"
if launchctl list | grep -q "com.moneyprinterv2.daemon"; then
  echo "✅ LaunchAgent loaded: com.moneyprinterv2.daemon"
else
  echo "⚠️ LaunchAgent not loaded"
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
