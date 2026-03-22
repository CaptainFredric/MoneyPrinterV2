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
#   phone_post.sh performance  → show linear growth phase + next objective
#   phone_post.sh backup       → manual cache backup
#   phone_post.sh health       → daemon + report health check
#   phone_post.sh check <id>   → inspect one twitter account
#   phone_post.sh post <id>    → foreground post (waits until done)
#   phone_post.sh detach <id>  → background post (safe to close Termius)
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
TARGET="${2:-all}"

run_post_foreground() {
    local target="$1"
    "$VENV_PYTHON" scripts/run_once.py twitter "$target" --headless
}

run_post_detached() {
    local target="$1"
    mkdir -p "$ROOT_DIR/logs"
    local ts
    ts="$(date +%Y%m%d_%H%M%S)"
    local log_file="$ROOT_DIR/logs/manual_post_${target}_${ts}.log"

    nohup "$VENV_PYTHON" scripts/run_once.py twitter "$target" --headless >"$log_file" 2>&1 < /dev/null &
    local pid=$!
    disown "$pid" 2>/dev/null || true

    echo "✅ Detached post started."
    echo "   PID: $pid"
    echo "   Log: $log_file"
    echo "   You can now close Termius safely."
}

case "$MODE" in
    status)
        "$VENV_PYTHON" scripts/report.py
        ;;
    performance)
        "$VENV_PYTHON" scripts/performance_report.py
        ;;
    health)
        bash "$ROOT_DIR/scripts/health_check.sh"
        ;;
    backup)
        "$VENV_PYTHON" scripts/report.py --backup
        ;;
    list)
        "$VENV_PYTHON" scripts/run_once.py --list
        ;;
    check)
        "$VENV_PYTHON" scripts/account_check.py "$TARGET"
        ;;
    post)
        run_post_foreground "$TARGET"
        ;;
    detach)
        run_post_detached "$TARGET"
        ;;
    *)
        # MODE is either 'all' or a nickname/uuid
        run_post_foreground "$MODE"
        ;;
esac
