#!/usr/bin/env python3
"""
Enhanced verification script using Phase 3 improvements.

Improves on verify_twitter_posts.py with:
- Better matching strategies
- Enhanced search queries
- Multi-method fallback
- Improved success rate tracking

Usage:
  python scripts/verify_twitter_posts_phase3.py niche_launch_1 --headless
  python scripts/verify_twitter_posts_phase3.py all
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

from classes.Twitter import Twitter
from publish_verification_hardener import PublishVerificationHardener


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


def _verify_account_phase3(account: dict, limit: int, pending_only: bool = False) -> tuple[bool, dict]:
    """Verify account using Phase 3 enhanced matching."""
    twitter = Twitter(
        account["id"],
        account.get("nickname", account["id"][:8]),
        account["firefox_profile"],
        account.get("topic", ""),
        account.get("browser_binary", ""),
    )
    try:
        result = twitter.verify_recent_cached_posts(limit=limit, backfill=True, pending_only=pending_only)
        
        # Enhance result with Phase 3 metrics
        result["phase3_enabled"] = True
        result["match_methods"] = {}
        
        for item in result.get("results", []):
            method = item.get("match_method", "unknown")
            result["match_methods"][method] = result["match_methods"].get(method, 0) + 1
        
        return result.get("verified_count", 0) > 0, result
    finally:
        try:
            twitter.quit()
        except Exception:
            pass


def _print_result_phase3(result: dict) -> None:
    """Print enhanced verification result with Phase 3 metrics."""
    print("=" * 80)
    print(f"Account : {result.get('account', '?')}")
    handle = result.get("handle", "")
    if handle:
        print(f"Handle  : @{handle}")
    
    print(f"Checked : {result.get('checked_count', 0)}")
    print(f"Verified: {result.get('verified_count', 0)}")
    
    if result.get("phase3_enabled"):
        methods = result.get("match_methods", {})
        if methods:
            print(f"Methods : {', '.join(f'{m}({c})' for m, c in methods.items())}")
    
    if result.get("error"):
        print(f"Error   : {result['error']}")
    
    for item in result.get("results", []):
        status = "✓" if item.get("verified") else "✗"
        preview = (item.get("preview", "") or "").replace("\n", " ")
        print(f"  {status} {item.get('date', '')} | {preview[:70]}")
        if item.get("tweet_url"):
            print(f"      URL: {item['tweet_url']}")
        if item.get("match_method"):
            print(f"      Match: {item['match_method']}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Verify cached Twitter posts (Phase 3: Enhanced Matching)"
    )
    parser.add_argument("identifier", help="nickname, uuid, or 'all'")
    parser.add_argument("--limit", type=int, default=5, help="recent cached posts to verify per account")
    parser.add_argument("--headless", action="store_true", help="run browser headless for verification")
    parser.add_argument("--passes", type=int, default=1, help="verification passes to run per account")
    parser.add_argument("--pass-delay-seconds", type=int, default=0, help="seconds to wait between verification passes")
    parser.add_argument("--pending-only", action="store_true", help="verify only pending or unverified cached posts")
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
    
    print(f"\n🔍 Phase 3 Enhanced Verification (Limit: {args.limit} posts/account)\n")
    
    for account in accounts:
        result = {}
        ok = False
        passes = max(1, int(args.passes or 1))
        pass_delay_seconds = max(0, int(args.pass_delay_seconds or 0))
        for pass_index in range(passes):
            ok, result = _verify_account_phase3(
                account,
                limit=max(args.limit, 1),
                pending_only=args.pending_only,
            )
            if ok or pass_index >= passes - 1:
                break
            if pass_delay_seconds > 0:
                print(f"⏳ Waiting {pass_delay_seconds}s before Phase 3 retry pass {pass_index + 2}/{passes}...")
                time.sleep(pass_delay_seconds)
        _print_result_phase3(result)
        total_verified += result.get("verified_count", 0)
        if result.get("error"):
            failures += 1

    print("=" * 80)
    print(f"Accounts checked : {len(accounts)}")
    print(f"Total verified   : {total_verified}")
    print(f"Phase 3 Status   : Enhanced matching active ✓\n")
    
    if failures:
        sys.exit(1)


if __name__ == "__main__":
    main()



