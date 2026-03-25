#!/usr/bin/env python3
"""
Autonomous idle runner for MoneyPrinterV2.

Purpose:
- Run productive posting cycles without manual intervention
- Use variable timing (random jitter + cooldown-aware adaptive delay)
- Keep state/pid files for start/stop/status control

Cycle:
1) smart post on primary account
2) verify primary account
3) backfill primary pending posts
4) periodic lock cleanup + session checks
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

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR / "src"))

from runtime_python import resolve_runtime_python

VENV_PYTHON = Path(resolve_runtime_python())

SMART_SCRIPT = ROOT_DIR / "scripts" / "smart_post_twitter.py"
VERIFY_SCRIPT = ROOT_DIR / "scripts" / "verify_twitter_posts.py"
BACKFILL_SCRIPT = ROOT_DIR / "scripts" / "backfill_pending_twitter.py"
SESSION_SCRIPT = ROOT_DIR / "scripts" / "check_x_session.py"
CLEANUP_SCRIPT = ROOT_DIR / "scripts" / "cleanup_stale_locks.py"
TEMP_CLEANUP_SCRIPT = ROOT_DIR / "scripts" / "cleanup_temp_space.py"

RUNTIME_DIR = ROOT_DIR / ".mp" / "runtime"
PID_FILE = RUNTIME_DIR / "money_idle.pid"
STATE_FILE = RUNTIME_DIR / "money_idle_state.json"
STOP_FILE = RUNTIME_DIR / "money_idle.stop"


_shutdown = False


def _handle_signal(sig, _frame):
    global _shutdown
    print(f"[idle] Received signal {sig}; shutting down cleanly.")
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
        stderr = stderr_base + f"\n[idle] timeout after {timeout_seconds}s"
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
    temp_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    temp_path.replace(STATE_FILE)


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
    parser = argparse.ArgumentParser(description="MoneyPrinterV2 autonomous idle mode")
    parser.add_argument("--primary-account", default=os.environ.get("MPV2_PRIMARY_ACCOUNT", "niche_launch_1"))
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
    parser.add_argument("--once", action="store_true", help="run one cycle and exit")
    args = parser.parse_args()

    if args.min_minutes < 1 or args.max_minutes < args.min_minutes:
        print("Invalid timing window. Ensure 1 <= min <= max minutes.", file=sys.stderr)
        sys.exit(1)

    _write_pid()
    STOP_FILE.unlink(missing_ok=True)
    _save_state(
        {
            "cycle": 0,
            "primary_account": args.primary_account,
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
            print(f"[idle] Cycle {cycle_index} started at {cycle_started_at}")
            _save_state(
                {
                    "cycle": cycle_index,
                    "primary_account": args.primary_account,
                    "started_at": cycle_started_at,
                    "status": "running",
                }
            )

            smart_cmd = [
                str(VENV_PYTHON),
                str(SMART_SCRIPT),
                "--headless",
                "--allow-no-post",
                "--prefer-primary",
                "--only-account",
                args.primary_account,
            ]
            smart_result = _run_cmd(smart_cmd, env, timeout_seconds=args.smart_timeout_seconds)
            smart_output = f"{smart_result.stdout}\n{smart_result.stderr}"
            posted_count = _parse_posted_count(smart_output)
            post_status = _parse_post_status(smart_output)
            confidence_score, confidence_level = _parse_confidence(post_status)
            qualified_post = (
                posted_count > 0
                and confidence_score >= args.confidence_min_score
                and (not confidence_level or confidence_level in {"high", "verified"})
            )
            cooldown_minutes = _parse_cooldown_minutes(smart_output)

            run_verify_backfill = qualified_post or (args.verify_every > 0 and cycle_index % args.verify_every == 0)

            verify_result = CmdResult(code=0, stdout="", stderr="")
            backfill_result = CmdResult(code=0, stdout="", stderr="")
            if run_verify_backfill:
                verify_cmd = [str(VENV_PYTHON), str(VERIFY_SCRIPT), args.primary_account]
                verify_result = _run_cmd(verify_cmd, env)

                backfill_cmd = [str(VENV_PYTHON), str(BACKFILL_SCRIPT), args.primary_account, "--headless"]
                backfill_result = _run_cmd(backfill_cmd, env)
            else:
                print("[idle] Skipping verify/backfill this cycle (no qualified post, non-maintenance cycle).")

            if args.cleanup_every > 0 and cycle_index % args.cleanup_every == 0:
                cleanup_cmd = [str(VENV_PYTHON), str(CLEANUP_SCRIPT)]
                _run_cmd(cleanup_cmd, env)
                temp_cleanup_cmd = [str(VENV_PYTHON), str(TEMP_CLEANUP_SCRIPT)]
                _run_cmd(temp_cleanup_cmd, env)

            if args.session_check_every > 0 and cycle_index % args.session_check_every == 0:
                session_cmd = [str(VENV_PYTHON), str(SESSION_SCRIPT), "all"]
                _run_cmd(session_cmd, env)

            base_sleep = random.randint(args.min_minutes, args.max_minutes)
            adaptive_sleep = base_sleep
            if cooldown_minutes > 0:
                adaptive_sleep = max(adaptive_sleep, cooldown_minutes + random.randint(1, 4))
            elif post_status.startswith("posted") and not qualified_post:
                adaptive_sleep = min(adaptive_sleep, max(1, args.fast_retry_minutes))
            elif "cron-timeout:" in post_status:
                adaptive_sleep = max(adaptive_sleep, 6)

            cycle_finished_at = datetime.now().isoformat(timespec="seconds")
            state = {
                "cycle": cycle_index,
                "primary_account": args.primary_account,
                "started_at": cycle_started_at,
                "finished_at": cycle_finished_at,
                "posted_count": posted_count,
                "post_status": post_status,
                "confidence_score": confidence_score,
                "confidence_level": confidence_level,
                "qualified_post": qualified_post,
                "verify_backfill_ran": run_verify_backfill,
                "cooldown_minutes_detected": cooldown_minutes,
                "next_sleep_minutes": 0 if args.once else adaptive_sleep,
                "smart_exit_code": smart_result.code,
                "verify_exit_code": verify_result.code,
                "backfill_exit_code": backfill_result.code,
                "status": "completed",
            }
            _save_state(state)

            if args.once:
                print("[idle] One-shot cycle complete.")
                break

            print(f"[idle] Sleeping {adaptive_sleep} minute(s) before next cycle.")
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
            "primary_account": args.primary_account,
            "stopped_at": datetime.now().isoformat(timespec="seconds"),
            "status": "stopped",
        }
        _save_state(final_state)
        _clear_pid()
        STOP_FILE.unlink(missing_ok=True)
        print("[idle] Exited cleanly.")


if __name__ == "__main__":
    main()
