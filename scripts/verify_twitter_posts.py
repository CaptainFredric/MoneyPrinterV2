#!/usr/bin/env python3
"""
scripts/verify_twitter_posts.py

Verifies recent cached Twitter posts against the live X account timeline.
Backfills permalink metadata into `.mp/twitter.json` when matches are found.

Usage:
  python scripts/verify_twitter_posts.py EyeCatcher
  python scripts/verify_twitter_posts.py all
  python scripts/verify_twitter_posts.py EyeCatcher --limit 5 --headless
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

    matches = []
    lowered = identifier.lower()
    for account in accounts:
        if account.get("id", "").lower() == lowered or account.get("nickname", "").lower() == lowered:
            matches.append(account)
    return matches


def _verify_account(account: dict, limit: int) -> tuple[bool, dict]:
    twitter = Twitter(
        account["id"],
        account.get("nickname", account["id"][:8]),
        account["firefox_profile"],
        account.get("topic", ""),
        account.get("browser_binary", ""),
    )
    try:
        result = twitter.verify_recent_cached_posts(limit=limit, backfill=True)
        return result.get("verified_count", 0) > 0, result
    finally:
        try:
            twitter.quit()
        except Exception:
            pass


def _print_result(result: dict) -> None:
    print("=" * 72)
    print(f"Account : {result.get('account', '?')}")
    handle = result.get("handle", "")
    if handle:
        print(f"Handle  : @{handle}")
    print(f"Checked : {result.get('checked_count', 0)}")
    print(f"Verified: {result.get('verified_count', 0)}")
    if result.get("error"):
        print(f"Error   : {result['error']}")
    for item in result.get("results", []):
        status = "OK" if item.get("verified") else "MISS"
        preview = (item.get("preview", "") or "").replace("\n", " ")
        print(f"- {status:<4} {item.get('date', '')} | {preview[:70]}")
        if item.get("tweet_url"):
            print(f"       URL: {item['tweet_url']}")
        if item.get("match_method"):
            print(f"       Match: {item['match_method']}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Verify cached Twitter posts against live X timeline")
    parser.add_argument("identifier", help="nickname, uuid, or 'all'")
    parser.add_argument("--limit", type=int, default=3, help="recent cached posts to verify per account")
    parser.add_argument("--headless", action="store_true", help="run browser headless for verification")
    args = parser.parse_args()

    if args.headless:
        import os
        os.environ["MPV2_HEADLESS"] = "1"

    accounts = _resolve_accounts(args.identifier)
    if not accounts:
        print(f"No twitter account matched '{args.identifier}'.", file=sys.stderr)
        sys.exit(1)

    failures = 0
    total_verified = 0
    for account in accounts:
        ok, result = _verify_account(account, limit=max(args.limit, 1))
        _print_result(result)
        total_verified += result.get("verified_count", 0)
        if result.get("error"):
            failures += 1

    print("=" * 72)
    print(f"Accounts checked : {len(accounts)}")
    print(f"Verified posts   : {total_verified}")
    if failures:
        sys.exit(1)


if __name__ == "__main__":
    main()
