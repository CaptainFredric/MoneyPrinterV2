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
#   phone_post.sh next         → run linear next-step sequence (performance + autotune preview + status)
#   phone_post.sh login <id>   → open the exact Firefox profile for X login repair
#   phone_post.sh session <id> → check whether the X session/profile is ready to post
#   phone_post.sh session-all  → check X session readiness for all accounts
#   phone_post.sh verify <id>  → verify recent cached posts against live X timeline
#   phone_post.sh verify-all   → verify recent cached posts for all accounts
#   phone_post.sh autotune      → preview ratio tuning changes (dry-run)
#   phone_post.sh autotune-apply→ apply ratio tuning changes
#   phone_post.sh autotune-unlocked-apply → apply tuning without phase lock (advanced)
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
    "$VENV_PYTHON" scripts/run_once.py twitter "$target"
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
    login)
        "$VENV_PYTHON" scripts/open_x_login.py "$TARGET"
        ;;
    session)
        "$VENV_PYTHON" scripts/check_x_session.py "$TARGET"
        ;;
    session-all)
        "$VENV_PYTHON" scripts/check_x_session.py all
        ;;
    verify)
        "$VENV_PYTHON" scripts/verify_twitter_posts.py "$TARGET"
        ;;
    verify-all)
        "$VENV_PYTHON" scripts/verify_twitter_posts.py all
        ;;
    next)
        "$VENV_PYTHON" scripts/performance_report.py
        echo ""
        "$VENV_PYTHON" scripts/auto_tune_ratios.py --dry-run
        echo ""
        "$VENV_PYTHON" scripts/report.py
        ;;
    autotune)
        "$VENV_PYTHON" scripts/auto_tune_ratios.py --dry-run
        ;;
    autotune-apply)
        "$VENV_PYTHON" scripts/auto_tune_ratios.py --apply
        ;;
    autotune-unlocked-apply)
        "$VENV_PYTHON" scripts/auto_tune_ratios.py --apply --no-phase-lock
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
