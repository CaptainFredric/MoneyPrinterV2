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
#   phone_post.sh login-auto <id> → restore latest saved session, open profile, and verify readiness
#   phone_post.sh login-all     → open all configured Firefox profiles for X login repair
#   phone_post.sh login-prep    → stop automation + geckodriver, then open all login profiles
#   phone_post.sh session <id> → check whether the X session/profile is ready to post
#   phone_post.sh session-all  → check X session readiness for all accounts
#   phone_post.sh session-watch <id|all> [seconds] → passive watch mode for login progress
#   phone_post.sh session-backup <id|all> → save a restore point for Firefox login sessions
#   phone_post.sh session-backups [id|all] → list saved session restore points
#   phone_post.sh session-restore <id> [archive|latest] → restore a saved Firefox login session
#   phone_post.sh verify <id>  → verify recent cached posts against live X timeline
#   phone_post.sh verify-all   → verify recent cached posts for all accounts
#   phone_post.sh autotune      → preview ratio tuning changes (dry-run)
#   phone_post.sh autotune-apply→ apply ratio tuning changes
#   phone_post.sh autotune-unlocked-apply → apply tuning without phase lock (advanced)
#   phone_post.sh backup       → manual cache backup
#   phone_post.sh health|diag|diagnostic → comprehensive system health diagnostic
#   phone_post.sh daemon       → old daemon health check (deprecated)
#   phone_post.sh cleanup      → remove stale Firefox profile locks (safe, checks for active processes)
#   phone_post.sh cleanup --dry-run → preview what would be cleaned
#   phone_post.sh smart        → smart auto-rotate posting across accounts
#   phone_post.sh smart-all    → smart mode but attempt all accounts
#   phone_post.sh backfill <id>|all → backfill pending verification posts
#   phone_post.sh money        → productive cycle: smart post + verify + backfill on primary account
#   phone_post.sh idle-start   → start autonomous idle mode in background
#   phone_post.sh idle-stop    → stop autonomous idle mode
#   phone_post.sh idle-status  → show autonomous idle mode status
#   phone_post.sh stats        → show posting/idle/account health stats
#   phone_post.sh check <id>   → inspect one twitter account
#   phone_post.sh post <id>    → foreground post (waits until done)
#   phone_post.sh detach <id>  → background post (safe to close Termius)
#   phone_post.sh <nickname>   → post from one account by nickname
#
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

resolve_python() {
    local candidates=()
    candidates+=("${MPV2_PYTHON:-}")
    candidates+=("$ROOT_DIR/venv/bin/python")
    candidates+=("$ROOT_DIR/.venv/bin/python")
    candidates+=("$(command -v python3 2>/dev/null || true)")
    candidates+=("$(command -v python 2>/dev/null || true)")

    local candidate
    for candidate in "${candidates[@]}"; do
        [[ -n "$candidate" ]] || continue
        [[ -x "$candidate" ]] || continue
        printf '%s\n' "$candidate"
        return 0
    done
    return 1
}

VENV_PYTHON="$(resolve_python)"
if [[ -z "$VENV_PYTHON" ]]; then
    echo "Could not find a usable Python interpreter." >&2
    exit 1
fi

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
PRIMARY_ACCOUNT="${MPV2_PRIMARY_ACCOUNT:-niche_launch_1}"
RUNTIME_DIR="$ROOT_DIR/.mp/runtime"
IDLE_PID_FILE="$RUNTIME_DIR/money_idle.pid"
IDLE_STATE_FILE="$RUNTIME_DIR/money_idle_state.json"
IDLE_STOP_FILE="$RUNTIME_DIR/money_idle.stop"
IDLE_LOG_FILE="$ROOT_DIR/logs/idle_mode.log"

stop_automation_processes() {
    mkdir -p "$RUNTIME_DIR"

    touch "$RUNTIME_DIR/money_idle.stop" "$RUNTIME_DIR/money_idle_phase2.stop" 2>/dev/null || true

    if [[ -f "$RUNTIME_DIR/money_idle.pid" ]]; then
        kill "$(cat "$RUNTIME_DIR/money_idle.pid" 2>/dev/null || true)" >/dev/null 2>&1 || true
    fi

    if [[ -f "$RUNTIME_DIR/money_idle_phase2.pid" ]]; then
        kill "$(cat "$RUNTIME_DIR/money_idle_phase2.pid" 2>/dev/null || true)" >/dev/null 2>&1 || true
    fi

    pkill -f 'money_idle_phase2.py|money_idle_mode.py|smart_post_twitter.py|run_once.py twitter|geckodriver|marionette|selenium' >/dev/null 2>&1 || true
}

list_account_nicknames() {
    "$VENV_PYTHON" - <<'PY'
import json
from pathlib import Path

cache = Path('.mp/twitter.json')
if not cache.exists():
    raise SystemExit(0)

data = json.loads(cache.read_text(encoding='utf-8'))
for account in data.get('accounts', []):
    nickname = str(account.get('nickname', '')).strip()
    if nickname:
        print(nickname)
PY
}

idle_is_running() {
    if [[ ! -f "$IDLE_PID_FILE" ]]; then
        return 1
    fi
    local pid
    pid="$(cat "$IDLE_PID_FILE" 2>/dev/null || true)"
    [[ -n "$pid" ]] || return 1
    kill -0 "$pid" >/dev/null 2>&1
}

idle_cleanup_stale_pid() {
    if [[ ! -f "$IDLE_PID_FILE" ]]; then
        return
    fi
    if ! idle_is_running; then
        rm -f "$IDLE_PID_FILE"
    fi
}

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
        stop_automation_processes
        "$VENV_PYTHON" scripts/open_x_login.py "$TARGET"
        ;;
    login-auto)
        stop_automation_processes
        "$VENV_PYTHON" scripts/twitter_profile_backup.py restore "$TARGET" latest --allow-missing || true
        echo ""
        "$VENV_PYTHON" scripts/open_x_login.py "$TARGET" --url https://x.com/home
        echo ""
        "$VENV_PYTHON" scripts/check_x_session.py "$TARGET" --no-fail --backup-on-ready
        ;;
    login-all)
        stop_automation_processes
        found_any=0
        while IFS= read -r nickname; do
            [[ -n "$nickname" ]] || continue
            found_any=1
            "$VENV_PYTHON" scripts/open_x_login.py "$nickname"
            echo ""
        done < <(list_account_nicknames)
        if [[ "$found_any" -eq 0 ]]; then
            echo "No accounts found in .mp/twitter.json"
            exit 1
        fi
        ;;
    login-prep)
        echo "🧹 Stopping automation + geckodriver for clean manual login..."
        stop_automation_processes
        echo "✅ Automation processes stopped."
        echo ""
        found_any=0
        while IFS= read -r nickname; do
            [[ -n "$nickname" ]] || continue
            found_any=1
            "$VENV_PYTHON" scripts/open_x_login.py "$nickname"
            echo ""
        done < <(list_account_nicknames)
        if [[ "$found_any" -eq 0 ]]; then
            echo "No accounts found in .mp/twitter.json"
            exit 1
        fi
        ;;
    session)
        "$VENV_PYTHON" scripts/check_x_session.py "$TARGET" --no-fail --backup-on-ready
        ;;
    session-all)
        "$VENV_PYTHON" scripts/check_x_session.py all --no-fail --backup-on-ready
        ;;
    session-watch)
        watch_target="${TARGET:-all}"
        watch_seconds="${3:-10}"
        "$VENV_PYTHON" scripts/check_x_session.py "$watch_target" --watch "$watch_seconds" --no-fail
        ;;
    session-backup)
        "$VENV_PYTHON" scripts/twitter_profile_backup.py backup "$TARGET"
        ;;
    session-backups)
        "$VENV_PYTHON" scripts/twitter_profile_backup.py status "$TARGET"
        ;;
    session-restore)
        stop_automation_processes
        archive_name="${3:-latest}"
        "$VENV_PYTHON" scripts/twitter_profile_backup.py restore "$TARGET" "$archive_name"
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
    health|diag|diagnostic)
        "$VENV_PYTHON" scripts/health_diagnostic.py "$TARGET"
        ;;
    daemon)
        bash "$ROOT_DIR/scripts/health_check.sh"
        ;;
    cleanup)
        if [[ "$TARGET" == "--dry-run" ]]; then
            "$VENV_PYTHON" scripts/cleanup_stale_locks.py --dry-run
        else
            "$VENV_PYTHON" scripts/cleanup_stale_locks.py
        fi
        ;;
    smart)
        "$VENV_PYTHON" scripts/smart_post_twitter.py --headless
        ;;
    smart-all)
        "$VENV_PYTHON" scripts/smart_post_twitter.py --headless --all-attempts
        ;;
    money)
        "$VENV_PYTHON" scripts/smart_post_twitter.py --headless --allow-no-post --only-account "$PRIMARY_ACCOUNT"
        echo ""
        "$VENV_PYTHON" scripts/verify_twitter_posts.py "$PRIMARY_ACCOUNT" || true
        echo ""
        "$VENV_PYTHON" scripts/backfill_pending_twitter.py "$PRIMARY_ACCOUNT" --headless || true
        ;;
    idle-start)
        mkdir -p "$RUNTIME_DIR" "$ROOT_DIR/logs"
        idle_cleanup_stale_pid
        if idle_is_running; then
            echo "✅ Idle mode already running (PID: $(cat "$IDLE_PID_FILE"))."
            echo "   Log: $IDLE_LOG_FILE"
            exit 0
        fi

        rm -f "$IDLE_STOP_FILE"
        nohup "$VENV_PYTHON" -u scripts/money_idle_mode.py \
            --headless \
            --primary-account "$PRIMARY_ACCOUNT" \
            --min-minutes "${MPV2_IDLE_MIN_MINUTES:-8}" \
            --max-minutes "${MPV2_IDLE_MAX_MINUTES:-22}" \
            >"$IDLE_LOG_FILE" 2>&1 < /dev/null &
        idle_pid=$!
        disown "$idle_pid" 2>/dev/null || true

        echo "✅ Idle mode started."
        echo "   PID: $idle_pid"
        echo "   Primary account: $PRIMARY_ACCOUNT"
        echo "   Log: $IDLE_LOG_FILE"
        ;;
    idle-stop)
        mkdir -p "$RUNTIME_DIR"
        touch "$IDLE_STOP_FILE"
        idle_cleanup_stale_pid

        if idle_is_running; then
            pid="$(cat "$IDLE_PID_FILE")"
            kill "$pid" >/dev/null 2>&1 || true
            for _ in {1..10}; do
                if ! kill -0 "$pid" >/dev/null 2>&1; then
                    break
                fi
                sleep 1
            done

            if kill -0 "$pid" >/dev/null 2>&1; then
                kill -9 "$pid" >/dev/null 2>&1 || true
                echo "🛑 Idle mode force-stopped (PID: $pid)."
            else
                echo "🛑 Idle mode stopped (PID: $pid)."
            fi
            rm -f "$IDLE_PID_FILE"
        else
            echo "ℹ️ Idle mode is not currently running."
        fi
        ;;
    idle-status)
        idle_cleanup_stale_pid
        if idle_is_running; then
            echo "✅ Idle mode running (PID: $(cat "$IDLE_PID_FILE"))."
        else
            echo "ℹ️ Idle mode not running."
        fi

        if [[ -f "$IDLE_STATE_FILE" ]]; then
            echo ""
            echo "Last state:"
            cat "$IDLE_STATE_FILE"
        fi

        if [[ -f "$IDLE_LOG_FILE" ]]; then
            echo ""
            echo "Recent idle log:"
            tail -n 20 "$IDLE_LOG_FILE"
        fi
        ;;
    stats)
        "$VENV_PYTHON" scripts/stats_report.py
        ;;
    backfill)
        "$VENV_PYTHON" scripts/backfill_pending_twitter.py "$TARGET" --headless
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
