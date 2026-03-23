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
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
SRC_DIR = ROOT_DIR / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from classes.Twitter import Twitter  # noqa: E402


TWITTER_CACHE = ROOT_DIR / ".mp" / "twitter.json"


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


def _check_account(account: dict) -> dict:
    twitter = Twitter(
        account["id"],
        account.get("nickname", account["id"][:8]),
        account["firefox_profile"],
        account.get("topic", ""),
    )
    try:
        status = twitter.check_session()
        status["account"] = account.get("nickname", "?")
        status["configured_handle"] = str(account.get("x_username", "")).lstrip("@")
        return status
    finally:
        try:
            twitter.browser.quit()
        except Exception:
            pass


def main() -> None:
    parser = argparse.ArgumentParser(description="Check whether a Twitter Firefox profile is ready to post")
    parser.add_argument("identifier", help="nickname, uuid, or 'all'")
    parser.add_argument("--headless", action="store_true", help="run browser headless for session check")
    args = parser.parse_args()

    if args.headless:
        import os
        os.environ["MPV2_HEADLESS"] = "1"

    accounts = _resolve_accounts(args.identifier)
    if not accounts:
        print(f"No twitter account matched '{args.identifier}'.", file=sys.stderr)
        sys.exit(1)

    failures = 0
    for account in accounts:
        status = _check_account(account)
        print("=" * 72)
        print(f"Account : {status.get('account', '?')}")
        if status.get("configured_handle"):
            print(f"Handle  : @{status['configured_handle']}")
        print(f"Ready   : {'YES' if status.get('ready') else 'NO'}")
        print(f"Reason  : {status.get('reason', 'unknown')}")
        if status.get("reason") == "profile-in-use":
            print("Hint    : Close Firefox windows using this profile, then rerun session check.")
        if status.get("current_url"):
            print(f"URL     : {status['current_url']}")
        if not status.get("ready"):
            failures += 1

    print("=" * 72)
    print(f"Accounts checked : {len(accounts)}")
    if failures:
        print(f"Profiles blocked : {failures}")
        sys.exit(1)
    print("Profiles blocked : 0")


if __name__ == "__main__":
    main()
