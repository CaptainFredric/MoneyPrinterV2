#!/usr/bin/env bash
# scripts/phone_post.sh
#
# One-tap script for posting from a phone over SSH (Termius / Blink Shell).
#
# From your phone terminal:
#   ssh user@your-mac 'bash /path/to/MoneyPrinterV2/scripts/phone_post.sh'
#
# Or set it as a Termius Snippet for one tap:
#   bash ~/Documents/GitHub/PromisesFrontend/MoneyPrinterV2/scripts/phone_post.sh
#
# Options:
#   phone_post.sh              → post from all twitter accounts (headless)
#   phone_post.sh status       → show status report (no browser, instant)
#   phone_post.sh backup       → manual cache backup
#   phone_post.sh <nickname>   → post from one account by nickname
#
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV_PYTHON="$ROOT_DIR/venv/bin/python"

# Load .env if present (picks up GEMINI_API_KEY etc.)
if [[ -f "$ROOT_DIR/.env" ]]; then
    set -a
    # shellcheck disable=SC1090
    source "$ROOT_DIR/.env"
    set +a
fi

cd "$ROOT_DIR"

MODE="${1:-all}"

case "$MODE" in
    status)
        "$VENV_PYTHON" scripts/report.py
        ;;
    backup)
        "$VENV_PYTHON" scripts/report.py --backup
        ;;
    list)
        "$VENV_PYTHON" scripts/run_once.py --list
        ;;
    *)
        # MODE is either 'all' or a nickname/uuid
        "$VENV_PYTHON" scripts/run_once.py twitter "$MODE" --headless
        ;;
esac
