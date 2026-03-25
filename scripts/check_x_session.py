#!/usr/bin/env python3
"""
scripts/check_x_session.py

Checks whether each Twitter Firefox profile is currently able to post on X.

Usage:
  python scripts/check_x_session.py EyeCatcher
  python scripts/check_x_session.py all
  python scripts/check_x_session.py EyeCatcher --headless
"""

import argparse
import json
import sys
import time
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
SRC_DIR = ROOT_DIR / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))


TWITTER_CACHE = ROOT_DIR / ".mp" / "twitter.json"

from twitter_session_backup import backup_account_profile
from twitter_session_backup import count_auth_cookies


def _load_accounts() -> list[dict]:
    try:
        with open(TWITTER_CACHE, "r", encoding="utf-8") as file:
            data = json.load(file)
        return data.get("accounts", [])
    except Exception:
        return []


def _resolve_accounts(identifier: str) -> list[dict]:
    accounts = _load_accounts()
    if identifier.lower() == "all":
        return accounts
    lowered = identifier.lower()
    return [
        account
        for account in accounts
        if account.get("id", "").lower() == lowered or account.get("nickname", "").lower() == lowered
    ]


def _check_account_active(account: dict) -> dict:
    from classes.Twitter import Twitter  # noqa: E402

    twitter = Twitter(
        account["id"],
        account.get("nickname", account["id"][:8]),
        account["firefox_profile"],
        account.get("topic", ""),
        account.get("browser_binary", ""),
    )
    try:
        status = twitter.check_session()
        status["account"] = account.get("nickname", "?")
        status["configured_handle"] = str(account.get("x_username", "")).lstrip("@")
        auth_cookie_count = count_auth_cookies(Path(str(account.get("firefox_profile", "")).strip()))
        status["auth_cookie_count"] = auth_cookie_count

        lowered_url = str(status.get("current_url", "")).lower()
        if (
            not status.get("ready")
            and status.get("reason") == "compose-ui-missing"
            and auth_cookie_count >= 1
            and "/home" in lowered_url
        ):
            status["ready"] = True
            status["reason"] = "ready-home-cookie-fallback"
        return status
    finally:
        try:
            twitter.quit()
        except Exception:
            pass


def _check_account_passive(account: dict) -> dict:
    profile_path = Path(str(account.get("firefox_profile", "")).strip())
    if not profile_path.exists() or not profile_path.is_dir():
        return {
            "account": account.get("nickname", "?"),
            "configured_handle": str(account.get("x_username", "")).lstrip("@"),
            "ready": False,
            "reason": "profile-not-found",
            "current_url": "",
        }

    auth_cookie_count = count_auth_cookies(profile_path)
    ready = auth_cookie_count >= 1
    return {
        "account": account.get("nickname", "?"),
        "configured_handle": str(account.get("x_username", "")).lstrip("@"),
        "ready": ready,
        "reason": "ready-cookie-auth" if ready else "login-required",
        "current_url": "",
        "auth_cookie_count": auth_cookie_count,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Check whether a Twitter Firefox profile is ready to post")
    parser.add_argument("identifier", help="nickname, uuid, or 'all'")
    parser.add_argument("--headless", action="store_true", help="run browser headless for session check")
    parser.add_argument("--active", action="store_true", help="use webdriver-based session check (can interrupt manual login)")
    parser.add_argument("--no-fail", action="store_true", help="always exit 0 (status reporting only)")
    parser.add_argument("--watch", type=int, default=0, help="repeat passive checks every N seconds (0 = once)")
    parser.add_argument("--backup-on-ready", action="store_true", help="create or refresh a profile backup when the session is ready")
    args = parser.parse_args()

    if args.active and args.watch > 0:
        print("--watch is only supported in passive mode (without --active).", file=sys.stderr)
        sys.exit(1)

    if args.headless:
        import os
        os.environ["MPV2_HEADLESS"] = "1"

    accounts = _resolve_accounts(args.identifier)
    if not accounts:
        print(f"No twitter account matched '{args.identifier}'.", file=sys.stderr)
        sys.exit(1)

    def run_check_iteration() -> int:
        failures_local = 0
        for account in accounts:
            if args.active:
                status = _check_account_active(account)
            else:
                status = _check_account_passive(account)
            backup_result = None
            if status.get("ready") and args.backup_on_ready:
                backup_result = backup_account_profile(account)
            print("=" * 72)
            print(f"Account : {status.get('account', '?')}")
            if status.get("configured_handle"):
                print(f"Handle  : @{status['configured_handle']}")
            print(f"Ready   : {'YES' if status.get('ready') else 'NO'}")
            print(f"Reason  : {status.get('reason', 'unknown')}")
            if "auth_cookie_count" in status:
                print(f"Cookies : auth={status['auth_cookie_count']}")
            if status.get("reason") == "profile-in-use":
                print("Hint    : Close Firefox windows using this profile, then rerun session check.")
            if status.get("current_url"):
                print(f"URL     : {status['current_url']}")
            if backup_result:
                print(f"Backup  : {'created' if backup_result.get('created') else 'skipped'}:{backup_result.get('reason', 'unknown')}")
                if backup_result.get("path"):
                    print(f"Archive : {backup_result['path']}")
            if not status.get("ready"):
                failures_local += 1

        print("=" * 72)
        print(f"Accounts checked : {len(accounts)}")
        print(f"Profiles blocked : {failures_local}")
        return failures_local

    if args.watch > 0:
        try:
            while True:
                print(f"\n[{time.strftime('%Y-%m-%d %H:%M:%S')}] Passive session watch")
                run_check_iteration()
                time.sleep(max(1, args.watch))
        except KeyboardInterrupt:
            print("\nStopped session watch.")
            sys.exit(0)

    failures = run_check_iteration()
    if failures and not args.no_fail:
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()
