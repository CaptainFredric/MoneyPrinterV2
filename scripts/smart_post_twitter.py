#!/usr/bin/env python3
"""
Smart Twitter post runner.

Purpose:
- Automatically rotate across configured Twitter accounts
- Skip accounts that are not session-ready or are in cooldown
- Attempt posting on the next viable account until one succeeds

Usage:
  python scripts/smart_post_twitter.py
  python scripts/smart_post_twitter.py --headless
  python scripts/smart_post_twitter.py --all-attempts
"""

import argparse
import json
import os
import re
import signal
import subprocess
import sys
from datetime import datetime
from datetime import timedelta
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
SRC_DIR = ROOT_DIR / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from cache import get_twitter_cache_path  # noqa: E402
from runtime_python import resolve_runtime_python  # noqa: E402

VENV_PYTHON = ROOT_DIR / "venv" / "bin" / "python"
DOT_VENV_PYTHON = ROOT_DIR / ".venv" / "bin" / "python"
RUNTIME_VENV_PYTHON = ROOT_DIR / ".runtime-venv" / "bin" / "python"
CRON_SCRIPT = ROOT_DIR / "src" / "cron.py"
CONFIG_PATH = ROOT_DIR / "config.json"
TRANSACTION_LOG_DIR = ROOT_DIR / "logs" / "transaction_log"


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


def _is_confidence_qualified(post_status: str, min_score: int) -> tuple[bool, str]:
    score, level = _parse_confidence(post_status)
    if score < 0:
        return False, "missing-confidence"
    # A pending-verification post means compose was accepted by X but the
    # permalink lookup timed-out/failed — the tweet IS live, just not confirmed
    # yet.  Treat it as qualified so the orchestrator counts it as "posted" and
    # a later backfill sweep can recover the URL.
    if "pending-verification" in (post_status or ""):
        return True, f"pending-verification-accepted:score={score}"
    if score < min_score:
        return False, f"low-confidence:score={score}:level={level or 'unknown'}"
    if level and level not in {"high", "verified"}:
        return False, f"low-confidence-level:{level}:score={score}"
    return True, f"confidence-ok:score={score}:level={level or 'n/a'}"


def _load_accounts() -> list[dict]:
    cache_path = Path(get_twitter_cache_path())
    if not cache_path.exists():
        return []
    try:
        with open(cache_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data.get("accounts", [])
    except Exception:
        return []


def _load_json(path: Path) -> dict:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _get_model() -> str:
    cfg = _load_json(CONFIG_PATH)
    return cfg.get("ollama_model", "llama3.2:3b")


def _get_python_executable() -> str:
    resolved = resolve_runtime_python()
    if resolved:
        return resolved

    candidates = [RUNTIME_VENV_PYTHON, VENV_PYTHON, DOT_VENV_PYTHON]
    for candidate in candidates:
        if not candidate.exists():
            continue
        try:
            probe = subprocess.run(
                [str(candidate), "-c", "import termcolor"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=5,
            )
            if probe.returncode == 0:
                return str(candidate)
        except Exception:
            continue

    for candidate in candidates:
        if candidate.exists():
            return str(candidate)
    return sys.executable


def _last_post_dt(account: dict):
    posts = account.get("posts", []) or []
    if not posts:
        return datetime.min
    try:
        return datetime.strptime(posts[-1].get("date", ""), "%m/%d/%Y, %H:%M:%S")
    except Exception:
        return datetime.min


def _sort_accounts_for_rotation(accounts: list[dict]) -> list[dict]:
    # Oldest recent post first => natural alternation
    return sorted(accounts, key=_last_post_dt)


def _filter_accounts(accounts: list[dict], identifier: str) -> list[dict]:
    needle = (identifier or "").strip().lower()
    if not needle:
        return accounts

    return [
        account
        for account in accounts
        if account.get("nickname", "").lower() == needle
        or account.get("id", "").lower() == needle
    ]


def _account_precheck(account: dict) -> tuple[bool, str]:
    profile = account.get("firefox_profile", "")
    if not profile:
        return False, "missing-firefox-profile"
    if not os.path.isdir(profile):
        return False, "firefox-profile-not-found"

    nickname = account.get("nickname", account.get("id", "unknown")[:8])
    quarantine_reason = _recent_quarantine_reason(nickname)
    if quarantine_reason:
        return False, quarantine_reason

    return True, "ok"


def _recent_quarantine_reason(nickname: str) -> str:
    """
    Skips accounts that recently failed for structural session reasons.

    Args:
        nickname (str): Account nickname

    Returns:
        reason (str): Non-empty reason when account should be temporarily quarantined
    """
    log_path = TRANSACTION_LOG_DIR / f"{nickname}.log"
    if not log_path.exists():
        return ""

    try:
        logs = json.loads(log_path.read_text(encoding="utf-8"))
    except Exception:
        return ""

    if not isinstance(logs, list) or not logs:
        return ""

    now = datetime.now()
    quarantine_until = now - timedelta(hours=6)
    quarantine_reasons = {
        "profile-posts-unavailable",
        "x-error-page",
        "login-required",
        "handle-mismatch",
        "handle-unresolved",
        "profile-in-use",
    }

    for entry in reversed(logs[-50:]):
        if not isinstance(entry, dict):
            continue
        timestamp_raw = str(entry.get("timestamp", "")).strip()
        if not timestamp_raw:
            continue
        try:
            timestamp = datetime.fromisoformat(timestamp_raw)
        except Exception:
            continue
        if timestamp < quarantine_until:
            break

        reason = str(entry.get("reason", "")).strip()
        if reason in quarantine_reasons:
            mins = int((now - timestamp).total_seconds() // 60)
            return f"quarantine:{reason}:{mins}m"

    return ""


def _try_account(account: dict, headless: bool) -> tuple[str, str]:
    nickname = account.get("nickname", account.get("id", "unknown")[:8])
    ok, reason = _account_precheck(account)
    if not ok:
        print(f"⏭️  Skip {nickname}: {reason}")
        return "skipped", reason

    try:
        model = _get_model()
        env = os.environ.copy()
        if headless:
            env["MPV2_HEADLESS"] = "1"
        timeout_seconds = int(os.environ.get("MPV2_SMART_TIMEOUT_SECONDS", "300"))
        min_confidence_score = int(os.environ.get("MPV2_CONFIDENCE_MIN_SCORE", "80"))
        strict_confidence_gate = os.environ.get("MPV2_STRICT_CONFIDENCE_GATE", "1").strip() not in {"0", "false", "False"}

        python_exec = _get_python_executable()
        cmd = [python_exec, str(CRON_SCRIPT), "twitter", account["id"], model]
        # Shield the parent from SIGINT while the child runs so Ctrl-C in the
        # terminal doesn't kill the wrapper before the child finishes.
        _old_sigint = signal.signal(signal.SIGINT, signal.SIG_IGN)
        try:
            result = subprocess.run(
                cmd,
                env=env,
                timeout=timeout_seconds,
                capture_output=True,
                text=True,
                start_new_session=True,
            )
        finally:
            signal.signal(signal.SIGINT, _old_sigint)

        if result.stdout:
            print(result.stdout, end="" if result.stdout.endswith("\n") else "\n")
        if result.stderr:
            print(result.stderr, end="" if result.stderr.endswith("\n") else "\n")

        post_status = ""
        for line in (result.stdout or "").splitlines():
            if line.startswith("MPV2_POST_STATUS:"):
                post_status = line.split("MPV2_POST_STATUS:", 1)[1].strip()

        if result.returncode == 0 and post_status.startswith("posted"):
            if strict_confidence_gate:
                qualified, gate_reason = _is_confidence_qualified(post_status, min_confidence_score)
                if not qualified:
                    print(f"⏭️  Skip {nickname}: {gate_reason}")
                    print(f"MPV2_SMART_STATUS:{nickname}:skipped")
                    return "skipped", gate_reason
            print(f"MPV2_SMART_STATUS:{nickname}:posted")
            return "posted", post_status

        if result.returncode == 0 and post_status.startswith("skipped"):
            print(f"MPV2_SMART_STATUS:{nickname}:skipped")
            return "skipped", post_status

        skippable_failures = {
            "failed:profile-posts-unavailable",
            "failed:profile-in-use",
            "failed:handle-mismatch",
            "failed:handle-unresolved",
            "failed:login-required",
            "failed:x-error-page",
        }
        if post_status in skippable_failures:
            print(f"MPV2_SMART_STATUS:{nickname}:skipped")
            return "skipped", post_status

        print(f"MPV2_SMART_STATUS:{nickname}:failed")
        if post_status:
            return "failed", post_status
        return "failed", f"exit:{result.returncode}"
    except subprocess.TimeoutExpired as exc:
        partial_out = exc.stdout or ""
        partial_err = exc.stderr or ""
        if isinstance(partial_out, bytes):
            partial_out = partial_out.decode("utf-8", errors="ignore")
        if isinstance(partial_err, bytes):
            partial_err = partial_err.decode("utf-8", errors="ignore")
        if partial_out:
            print(partial_out, end="" if partial_out.endswith("\n") else "\n")
        if partial_err:
            print(partial_err, end="" if partial_err.endswith("\n") else "\n")

        reason = f"cron-timeout:{int(getattr(exc, 'timeout', timeout_seconds) or timeout_seconds)}s"
        print(f"⏭️  Skip {nickname}: {reason}")
        print(f"MPV2_SMART_STATUS:{nickname}:skipped")
        return "skipped", reason
    except Exception as exc:
        message = str(exc)
        if "No such file or directory" in message or "Process unexpectedly closed" in message:
            print(f"⏭️  Skip {nickname}: browser-init-failed")
            return "skipped", "browser-init-failed"
        print(f"❌ Exception on {nickname}: {exc}")
        return "failed", str(exc)


def main() -> None:
    parser = argparse.ArgumentParser(description="Smart rotate-and-post for Twitter accounts")
    parser.add_argument("--headless", action="store_true", help="force headless browser")
    parser.add_argument(
        "--all-attempts",
        action="store_true",
        help="try all accounts even after first successful post",
    )
    parser.add_argument(
        "--allow-no-post",
        action="store_true",
        help="exit 0 when no post occurs (useful for cooldown/quarantine cycles)",
    )
    parser.add_argument(
        "--only-account",
        type=str,
        default="",
        help="limit attempts to one account nickname or uuid",
    )
    parser.add_argument(
        "--prefer-primary",
        action="store_true",
        help="prioritize MPV2_PRIMARY_ACCOUNT when no --only-account is supplied",
    )
    args = parser.parse_args()

    if args.headless:
        os.environ["MPV2_HEADLESS"] = "1"

    accounts = _sort_accounts_for_rotation(_load_accounts())
    if args.only_account:
        accounts = _filter_accounts(accounts, args.only_account)
    elif args.prefer_primary:
        primary_account = os.environ.get("MPV2_PRIMARY_ACCOUNT", "").strip()
        if primary_account:
            primary_matches = _filter_accounts(accounts, primary_account)
            if primary_matches:
                primary_id = primary_matches[0].get("id", "")
                accounts = sorted(accounts, key=lambda a: 0 if a.get("id", "") == primary_id else 1)
    if not accounts:
        print("No twitter accounts found in cache.", file=sys.stderr)
        sys.exit(1)

    posted_count = 0
    attempted = 0
    try:
        for account in accounts:
            attempted += 1
            nickname = account.get("nickname", account.get("id", "unknown")[:8])
            print(f"▶ Smart attempt: {nickname}")
            outcome, _ = _try_account(account, headless=args.headless)
            if outcome == "posted":
                posted_count += 1
                print(f"✅ Smart post success on {nickname}")
                if not args.all_attempts:
                    break
    except KeyboardInterrupt:
        print("\n⚠️  Interrupted — partial run completed.", file=sys.stderr)

    print(f"Smart attempts: {attempted} | posted: {posted_count}")
    if posted_count > 0:
        sys.exit(0)
    if args.allow_no_post:
        sys.exit(0)
    sys.exit(1)


if __name__ == "__main__":
    main()
