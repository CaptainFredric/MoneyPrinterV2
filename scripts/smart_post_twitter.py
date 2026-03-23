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
import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
SRC_DIR = ROOT_DIR / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from cache import get_twitter_cache_path  # noqa: E402

VENV_PYTHON = ROOT_DIR / "venv" / "bin" / "python"
DOT_VENV_PYTHON = ROOT_DIR / ".venv" / "bin" / "python"
CRON_SCRIPT = ROOT_DIR / "src" / "cron.py"
CONFIG_PATH = ROOT_DIR / "config.json"


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
    candidates = [VENV_PYTHON, DOT_VENV_PYTHON]
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


def _account_precheck(account: dict) -> tuple[bool, str]:
    profile = account.get("firefox_profile", "")
    if not profile:
        return False, "missing-firefox-profile"
    if not os.path.isdir(profile):
        return False, "firefox-profile-not-found"
    return True, "ok"


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

        python_exec = _get_python_executable()
        cmd = [python_exec, str(CRON_SCRIPT), "twitter", account["id"], model]
        result = subprocess.run(
            cmd,
            env=env,
            timeout=300,
            capture_output=True,
            text=True,
        )

        if result.stdout:
            print(result.stdout, end="" if result.stdout.endswith("\n") else "\n")
        if result.stderr:
            print(result.stderr, end="" if result.stderr.endswith("\n") else "\n")

        post_status = ""
        for line in (result.stdout or "").splitlines():
            if line.startswith("MPV2_POST_STATUS:"):
                post_status = line.split("MPV2_POST_STATUS:", 1)[1].strip()

        if result.returncode == 0 and post_status.startswith("posted"):
            print(f"MPV2_SMART_STATUS:{nickname}:posted")
            return "posted", post_status

        if result.returncode == 0 and post_status.startswith("skipped"):
            print(f"MPV2_SMART_STATUS:{nickname}:skipped")
            return "skipped", post_status

        print(f"MPV2_SMART_STATUS:{nickname}:failed")
        if post_status:
            return "failed", post_status
        return "failed", f"exit:{result.returncode}"
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
    args = parser.parse_args()

    if args.headless:
        os.environ["MPV2_HEADLESS"] = "1"

    accounts = _sort_accounts_for_rotation(_load_accounts())
    if not accounts:
        print("No twitter accounts found in cache.", file=sys.stderr)
        sys.exit(1)

    posted_count = 0
    attempted = 0
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

    print(f"Smart attempts: {attempted} | posted: {posted_count}")
    sys.exit(0 if posted_count > 0 else 1)


if __name__ == "__main__":
    main()
