#!/usr/bin/env python3
"""
Pending verification backfill sweep for Twitter accounts.

Purpose:
- Scan pending/unverified cached posts
- Attempt live timeline verification and permalink backfill
- Persist recovered URLs and verification states

Usage:
  python scripts/backfill_pending_twitter.py all
  python scripts/backfill_pending_twitter.py EyeCatcher --limit 20 --headless
"""

import argparse
import json
import os
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
SRC_DIR = ROOT_DIR / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from classes.Twitter import Twitter  # noqa: E402
from cache import get_twitter_cache_path  # noqa: E402


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


def _resolve_accounts(identifier: str) -> list[dict]:
    accounts = _load_accounts()
    if identifier.lower() == "all":
        return accounts

    needle = identifier.lower()
    return [
        account
        for account in accounts
        if account.get("id", "").lower() == needle
        or account.get("nickname", "").lower() == needle
    ]


def _run_backfill_for_account(account: dict, limit: int) -> dict:
    nickname = account.get("nickname", account.get("id", "unknown")[:8])
    twitter = Twitter(
        account["id"],
        nickname,
        account["firefox_profile"],
        account.get("topic", ""),
        account.get("browser_binary", ""),
    )
    try:
        return twitter.verify_pending_cached_posts(limit=limit, backfill=True)
    finally:
        try:
            twitter.browser.quit()
        except Exception:
            pass


def _print_result(result: dict) -> None:
    print("=" * 72)
    print(f"Account : {result.get('account', '?')}")
    handle = result.get("handle", "")
    if handle:
        print(f"Handle  : @{handle}")
    print(f"Pending Checked : {result.get('checked_count', 0)}")
    print(f"Backfilled OK   : {result.get('verified_count', 0)}")
    if result.get("error"):
        print(f"Error          : {result['error']}")

    for item in result.get("results", []):
        status = "OK" if item.get("verified") else "MISS"
        preview = (item.get("preview", "") or "").replace("\n", " ")
        print(f"- {status:<4} {item.get('date', '')} | {preview[:70]}")
        if item.get("tweet_url"):
            print(f"       URL: {item['tweet_url']}")
        if item.get("match_method"):
            print(f"       Match: {item['match_method']}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill pending Twitter post verifications")
    parser.add_argument("identifier", help="nickname, uuid, or 'all'")
    parser.add_argument("--limit", type=int, default=20, help="pending posts to inspect per account")
    parser.add_argument("--headless", action="store_true", help="run headless")
    args = parser.parse_args()

    if args.headless:
        os.environ["MPV2_HEADLESS"] = "1"

    accounts = _resolve_accounts(args.identifier)
    if not accounts:
        print(f"No twitter account matched '{args.identifier}'.", file=sys.stderr)
        sys.exit(1)

    total_checked = 0
    total_verified = 0
    failures = 0

    for account in accounts:
        result = _run_backfill_for_account(account, max(args.limit, 1))
        _print_result(result)
        total_checked += result.get("checked_count", 0)
        total_verified += result.get("verified_count", 0)
        if result.get("error"):
            failures += 1

    print("=" * 72)
    print(f"Accounts checked : {len(accounts)}")
    print(f"Pending checked  : {total_checked}")
    print(f"Backfilled total : {total_verified}")

    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
