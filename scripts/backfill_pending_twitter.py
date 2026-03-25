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
import signal
import sys
import time
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
    # Shield this process from SIGINT while the browser is active so that
    # Ctrl-C in the parent terminal does not kill a mid-flight verification.
    _old_sigint = signal.signal(signal.SIGINT, signal.SIG_IGN)
    try:
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
                twitter.quit()
            except Exception:
                pass
    finally:
        signal.signal(signal.SIGINT, _old_sigint)


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
        if item.get("publish_likelihood"):
            print(f"       Likelihood: {item['publish_likelihood']} | attempts={item.get('verification_attempts', 0)}")
        if item.get("tweet_url"):
            print(f"       URL: {item['tweet_url']}")
        if item.get("match_method"):
            print(f"       Match: {item['match_method']}")
        recovery_debug = item.get("recovery_debug") or {}
        if recovery_debug and not item.get("verified"):
            print(
                "       Debug: "
                f"method={recovery_debug.get('match_method', '') or 'none'} "
                f"pages={recovery_debug.get('pages_tried', 0)} "
                f"searches={recovery_debug.get('search_queries_tried', 0)} "
                f"compose={recovery_debug.get('compose_candidates', 0)} "
                f"profile={recovery_debug.get('profile_candidates', 0)} "
                f"timeline={recovery_debug.get('timeline_items', 0)}"
            )


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill pending Twitter post verifications")
    parser.add_argument("identifier", help="nickname, uuid, or 'all'")
    parser.add_argument("--limit", type=int, default=20, help="pending posts to inspect per account")
    parser.add_argument("--headless", action="store_true", help="run headless")
    parser.add_argument("--passes", type=int, default=1, help="backfill passes to run per account")
    parser.add_argument("--pass-delay-seconds", type=int, default=0, help="seconds to wait between backfill passes")
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
        result = {}
        passes = max(1, int(args.passes or 1))
        pass_delay_seconds = max(0, int(args.pass_delay_seconds or 0))
        for pass_index in range(passes):
            result = _run_backfill_for_account(account, max(args.limit, 1))
            if result.get("verified_count", 0) > 0 or pass_index >= passes - 1:
                break
            if pass_delay_seconds > 0:
                print(f"⏳ Waiting {pass_delay_seconds}s before backfill pass {pass_index + 2}/{passes}...")
                time.sleep(pass_delay_seconds)
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
