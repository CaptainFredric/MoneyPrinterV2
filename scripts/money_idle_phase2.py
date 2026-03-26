#!/usr/bin/env python3
"""
Enhanced autonomous idle runner for MoneyPrinterV2 with account-state machine.

Purpose:
- Run productive posting cycles on smartly-selected accounts
- Track per-account state (active, cooldown, blocked, degraded, paused)
- Use exponential backoff for blocked accounts
- Auto-pause accounts after 2+ consecutive low-confidence posts
- Rotate between eligible accounts to maximize revenue

Cycle:
1) Use account state machine to select best eligible account
2) Smart post on selected account
3) Verify & backfill if qualified post
4) Record post result & update account state
5) Sleep with adaptive delay based on state/cooldown
"""

import argparse
import json
import os
import random
import re
import signal
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

# Add src/ to path for imports
ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR / "src"))

from account_state_machine import AccountStateMachine
from account_performance import recovery_mode_decision
from runtime_python import resolve_runtime_python

VENV_PYTHON = Path(resolve_runtime_python())

SMART_SCRIPT = ROOT_DIR / "scripts" / "smart_post_twitter.py"
VERIFY_SCRIPT = ROOT_DIR / "scripts" / "verify_twitter_posts.py"
VERIFY_SCRIPT_PHASE3 = ROOT_DIR / "scripts" / "verify_twitter_posts_phase3.py"
BACKFILL_SCRIPT = ROOT_DIR / "scripts" / "backfill_pending_twitter.py"
SESSION_SCRIPT = ROOT_DIR / "scripts" / "check_x_session.py"
CLEANUP_SCRIPT = ROOT_DIR / "scripts" / "cleanup_stale_locks.py"
TEMP_CLEANUP_SCRIPT = ROOT_DIR / "scripts" / "cleanup_temp_space.py"

RUNTIME_DIR = ROOT_DIR / ".mp" / "runtime"
ACCOUNT_STATE_FILE = RUNTIME_DIR / "account_states.json"
PID_FILE = RUNTIME_DIR / "money_idle_phase2.pid"
STATE_FILE = RUNTIME_DIR / "money_idle_phase2_state.json"
STOP_FILE = RUNTIME_DIR / "money_idle_phase2.stop"


_shutdown = False


def _handle_signal(sig, _frame):
    global _shutdown
    print(f"[idle-p2] Received signal {sig}; shutting down cleanly.")
    _shutdown = True


signal.signal(signal.SIGTERM, _handle_signal)
signal.signal(signal.SIGINT, _handle_signal)


@dataclass
class CmdResult:
    code: int
    stdout: str
    stderr: str


def _run_cmd(cmd: list[str], env: dict[str, str], timeout_seconds: int = 420) -> CmdResult:
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            env=env,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired as exc:
        raw_stdout = exc.stdout or ""
        raw_stderr = exc.stderr or ""
        stdout = raw_stdout.decode("utf-8", errors="ignore") if isinstance(raw_stdout, bytes) else str(raw_stdout)
        stderr_base = raw_stderr.decode("utf-8", errors="ignore") if isinstance(raw_stderr, bytes) else str(raw_stderr)
        stderr = stderr_base + f"\n[idle-p2] timeout after {timeout_seconds}s"
        if stdout:
            print(stdout, end="" if stdout.endswith("\n") else "\n")
        if stderr:
            print(stderr, end="" if stderr.endswith("\n") else "\n")
        return CmdResult(code=124, stdout=stdout, stderr=stderr)

    if result.stdout:
        print(result.stdout, end="" if result.stdout.endswith("\n") else "\n")
    if result.stderr:
        print(result.stderr, end="" if result.stderr.endswith("\n") else "\n")
    return CmdResult(code=result.returncode, stdout=result.stdout or "", stderr=result.stderr or "")


def _parse_posted_count(output: str) -> int:
    match = re.search(r"Smart attempts:\s*\d+\s*\|\s*posted:\s*(\d+)", output)
    if not match:
        return 0
    try:
        return int(match.group(1))
    except Exception:
        return 0


def _parse_post_status(output: str) -> str:
    status = ""
    for line in (output or "").splitlines():
        if line.startswith("MPV2_POST_STATUS:"):
            status = line.split("MPV2_POST_STATUS:", 1)[1].strip()
    return status


def _parse_confidence(post_status: str) -> tuple[int, str]:
    score = -1
    level = ""
    score_match = re.search(r"confidence=(\d+)", post_status or "")
    level_match = re.search(r"level=([a-zA-Z\-]+)", post_status or "")
    if score_match:
        try:
            score = int(score_match.group(1))
        except Exception:
            score = -1
    if level_match:
        level = str(level_match.group(1)).strip().lower()
    return score, level


def _parse_cooldown_minutes(output: str) -> int:
    minutes = 0
    for match in re.finditer(r"cooldown:(\d+)m", output):
        try:
            minutes = max(minutes, int(match.group(1)))
        except Exception:
            continue
    return minutes


def _save_state(payload: dict) -> None:
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    temp_path = STATE_FILE.with_suffix(".tmp")
    try:
        temp_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        temp_path.replace(STATE_FILE)
    except OSError as exc:
        # Disk-full or permission error — don't crash the daemon, just warn
        print(f"[idle-p2] WARNING: could not persist state ({exc}). Continuing.")
        try:
            temp_path.unlink(missing_ok=True)
        except Exception:
            pass


def _write_pid() -> None:
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    PID_FILE.write_text(str(os.getpid()), encoding="utf-8")


def _clear_pid() -> None:
    try:
        PID_FILE.unlink(missing_ok=True)
    except Exception:
        pass


def _stop_requested() -> bool:
    if _shutdown:
        return True
    return STOP_FILE.exists()


def main() -> None:
    parser = argparse.ArgumentParser(description="MoneyPrinterV2 autonomous idle mode (Phase 2: account-state machine)")
    parser.add_argument("--accounts", nargs="+", default=["niche_launch_1", "EyeCatcher"], help="accounts to manage")
    parser.add_argument("--min-minutes", type=int, default=8, help="minimum delay between cycles")
    parser.add_argument("--max-minutes", type=int, default=22, help="maximum delay between cycles")
    parser.add_argument("--headless", action="store_true", help="force headless mode")
    parser.add_argument("--cleanup-every", type=int, default=6, help="run stale lock cleanup every N cycles")
    parser.add_argument("--session-check-every", type=int, default=4, help="run session checks every N cycles")
    parser.add_argument("--verify-every", type=int, default=3, help="run verify/backfill every N cycles when no qualified post")
    parser.add_argument("--confidence-min-score", type=int, default=int(os.environ.get("MPV2_CONFIDENCE_MIN_SCORE", "80")))
    parser.add_argument("--fast-retry-minutes", type=int, default=4, help="short retry sleep when post confidence is below threshold")
    parser.add_argument(
        "--smart-timeout-seconds",
        type=int,
        default=int(os.environ.get("MPV2_IDLE_SMART_TIMEOUT_SECONDS", "900")),
        help="timeout for smart posting subprocess",
    )
    parser.add_argument("--use-phase3", action="store_true", help="use Phase 3 enhanced verification (better matching)")
    parser.add_argument("--once", action="store_true", help="run one cycle and exit")
    args = parser.parse_args()

    if args.min_minutes < 1 or args.max_minutes < args.min_minutes:
        print("Invalid timing window. Ensure 1 <= min <= max minutes.", file=sys.stderr)
        sys.exit(1)

    _write_pid()
    STOP_FILE.unlink(missing_ok=True)

    # Initialize account state machine
    state_machine = AccountStateMachine(ACCOUNT_STATE_FILE)
    for account in args.accounts:
        state_machine.init_account(account)

    _save_state(
        {
            "cycle": 0,
            "accounts": args.accounts,
            "started_at": datetime.now().isoformat(timespec="seconds"),
            "status": "running",
        }
    )

    env = os.environ.copy()
    if args.headless:
        env["MPV2_HEADLESS"] = "1"
    env["MPV2_SMART_TIMEOUT_SECONDS"] = str(args.smart_timeout_seconds)
    env["MPV2_CONFIDENCE_MIN_SCORE"] = str(args.confidence_min_score)

    cycle_index = 0

    try:
        while not _stop_requested():
            cycle_index += 1
            cycle_started_at = datetime.now().isoformat(timespec="seconds")
            print(f"\n[idle-p2] ========== Cycle {cycle_index} started at {cycle_started_at} ==========")

            # Select best eligible account
            best_account = state_machine.get_best_eligible_account(args.accounts)
            if not best_account:
                print("[idle-p2] No eligible accounts available. All in cooldown/blocked/paused state.")
                print(state_machine.summary())
                sleep_minutes = 5
                print(f"[idle-p2] Sleeping {sleep_minutes} minutes before retry.")
                for _ in range(sleep_minutes * 60):
                    if _stop_requested():
                        break
                    time.sleep(1)
                continue

            account_state = state_machine.get_state(best_account)
            is_elig, elig_reason = state_machine.is_eligible(best_account)
            print(f"[idle-p2] Selected account: {best_account} ({elig_reason})")

            _save_state(
                {
                    "cycle": cycle_index,
                    "accounts": args.accounts,
                    "started_at": cycle_started_at,
                    "selected_account": best_account,
                    "account_state": account_state["state"],
                    "status": "running",
                }
            )

            recovery_decision = recovery_mode_decision(best_account, cycle_index)
            recovery_mode = bool(recovery_decision.get("use_recovery_mode", False))
            if recovery_mode:
                print(
                    "[idle-p2] Recovery mode for "
                    f"{best_account}: pending={recovery_decision['pending']} verified={recovery_decision['verified']} "
                    f"(full post every {recovery_decision['post_every_cycles']} cycles)."
                )

            posted_count = 0
            post_status = "skipped:recovery-mode"
            confidence_score = -1
            confidence_level = ""
            pending_verification_post = False
            qualified_post = False
            smart_result = CmdResult(code=0, stdout="", stderr="")
            smart_output = ""
            cooldown_minutes = 0

            if not recovery_mode:
                # Run smart post on selected account
                smart_cmd = [
                    str(VENV_PYTHON),
                    str(SMART_SCRIPT),
                    "--headless",
                    "--allow-no-post",
                    "--only-account",
                    best_account,
                ]
                smart_result = _run_cmd(smart_cmd, env, timeout_seconds=args.smart_timeout_seconds)
                smart_output = f"{smart_result.stdout}\n{smart_result.stderr}"
                posted_count = _parse_posted_count(smart_output)
                post_status = _parse_post_status(smart_output)
                confidence_score, confidence_level = _parse_confidence(post_status)
                pending_verification_post = post_status.startswith("posted:pending-verification")
                qualified_post = (
                    posted_count > 0
                    and confidence_score >= args.confidence_min_score
                    and (not confidence_level or confidence_level in {"high", "verified"})
                )
                cooldown_minutes = _parse_cooldown_minutes(smart_output)
            else:
                print(f"[idle-p2] Skipping full smart post for {best_account}; prioritizing recovery verify/backfill.")

            # Record post result and update account state
            state_machine.record_post(best_account, post_status, confidence_score)
            updated_state = state_machine.get_state(best_account)
            print(f"[idle-p2] Account {best_account} transitioned to: {updated_state['state']}")
            print(f"[idle-p2] Account health score: {updated_state['health_score']}")

            # Run verify/backfill if qualified, or if X accepted the compose but permalink resolution lagged.
            run_verify_backfill = (
                recovery_mode
                or qualified_post
                or pending_verification_post
                or (args.verify_every > 0 and cycle_index % args.verify_every == 0)
            )

            verify_result = CmdResult(code=0, stdout="", stderr="")
            backfill_result = CmdResult(code=0, stdout="", stderr="")
            if run_verify_backfill:
                if recovery_mode:
                    print("[idle-p2] Recovery mode detected; running verify/backfill-first pass.")
                elif pending_verification_post:
                    print("[idle-p2] Pending verification detected; running immediate verify/backfill follow-through.")
                verify_cmd = [str(VENV_PYTHON), str(VERIFY_SCRIPT_PHASE3 if args.use_phase3 else VERIFY_SCRIPT), best_account]
                if args.use_phase3 and (pending_verification_post or recovery_mode):
                    verify_cmd.extend(["--limit", "8", "--passes", "3", "--pass-delay-seconds", "20", "--pending-only", "--headless"])
                verify_result = _run_cmd(verify_cmd, env)

                backfill_cmd = [str(VENV_PYTHON), str(BACKFILL_SCRIPT), best_account, "--headless"]
                if pending_verification_post or recovery_mode:
                    backfill_cmd.extend(["--limit", "20", "--passes", "3", "--pass-delay-seconds", "20"])
                backfill_result = _run_cmd(backfill_cmd, env)
            else:
                print("[idle-p2] Skipping verify/backfill this cycle (no qualified post, non-maintenance cycle).")

            # Periodic cleanup and session checks
            if args.cleanup_every > 0 and cycle_index % args.cleanup_every == 0:
                cleanup_cmd = [str(VENV_PYTHON), str(CLEANUP_SCRIPT)]
                _run_cmd(cleanup_cmd, env)
                temp_cleanup_cmd = [str(VENV_PYTHON), str(TEMP_CLEANUP_SCRIPT)]
                _run_cmd(temp_cleanup_cmd, env)

            if args.session_check_every > 0 and cycle_index % args.session_check_every == 0:
                session_cmd = [str(VENV_PYTHON), str(SESSION_SCRIPT), "all"]
                _run_cmd(session_cmd, env)

            # Compute adaptive sleep
            base_sleep = random.randint(args.min_minutes, args.max_minutes)
            adaptive_sleep = base_sleep

            if updated_state["state"] == "cooldown":
                # Already in cooldown from post; respect that
                if cooldown_minutes > 0:
                    adaptive_sleep = max(adaptive_sleep, cooldown_minutes + random.randint(1, 4))
            elif updated_state["state"] == "paused":
                # Account was auto-paused; use shorter sleep to check other accounts
                adaptive_sleep = min(adaptive_sleep, max(1, args.fast_retry_minutes))
            elif posted_count > 0 and not qualified_post:
                # Posted but low-confidence; fast retry to verify
                adaptive_sleep = min(adaptive_sleep, max(1, args.fast_retry_minutes))
            elif "cron-timeout:" in post_status:
                # Timeout detected; add buffer
                adaptive_sleep = max(adaptive_sleep, 6)

            # Print summary
            print(f"[idle-p2] Account states summary:")
            print(state_machine.summary())

            cycle_finished_at = datetime.now().isoformat(timespec="seconds")
            state = {
                "cycle": cycle_index,
                "accounts": args.accounts,
                "started_at": cycle_started_at,
                "finished_at": cycle_finished_at,
                "selected_account": best_account,
                "recovery_mode": recovery_mode,
                "posted_count": posted_count,
                "post_status": post_status,
                "confidence_score": confidence_score,
                "confidence_level": confidence_level,
                "pending_verification_post": pending_verification_post,
                "qualified_post": qualified_post,
                "verify_backfill_ran": run_verify_backfill,
                "account_state_after": updated_state["state"],
                "account_health": updated_state["health_score"],
                "next_sleep_minutes": 0 if args.once else adaptive_sleep,
                "smart_exit_code": smart_result.code,
                "verify_exit_code": verify_result.code,
                "backfill_exit_code": backfill_result.code,
                "status": "completed",
            }
            _save_state(state)

            if args.once:
                print("[idle-p2] One-shot cycle complete.")
                break

            print(f"[idle-p2] Sleeping {adaptive_sleep} minute(s) before next cycle.")
            for _ in range(adaptive_sleep * 60):
                if _stop_requested():
                    break
                time.sleep(1)

    finally:
        last_state = {}
        try:
            if STATE_FILE.exists():
                last_state = json.loads(STATE_FILE.read_text(encoding="utf-8"))
                if not isinstance(last_state, dict):
                    last_state = {}
        except Exception:
            last_state = {}

        final_state = {
            **last_state,
            "cycle": cycle_index,
            "accounts": args.accounts,
            "stopped_at": datetime.now().isoformat(timespec="seconds"),
            "status": "stopped",
        }
        _save_state(final_state)
        _clear_pid()
        STOP_FILE.unlink(missing_ok=True)
        print("[idle-p2] Exited cleanly.")


if __name__ == "__main__":
    main()
