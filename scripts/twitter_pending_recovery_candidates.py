#!/usr/bin/env python3
"""Show the highest-priority pending Twitter posts to recover next."""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parent.parent
CACHE_PATH = ROOT_DIR / ".mp" / "twitter.json"
SRC_DIR = ROOT_DIR / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from publish_verification_hardener import PublishVerificationHardener


def _load_accounts() -> list[dict]:
    if not CACHE_PATH.exists():
        return []
    try:
        data = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
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
        if account.get("id", "").lower() == lowered
        or account.get("nickname", "").lower() == lowered
    ]


def _normalize_tweet(text: str) -> str:
    text = text.lower()
    text = re.sub(r"https?://\S+", " ", text)
    text = re.sub(r"[@#]", "", text)
    text = re.sub(r"[^a-z0-9\s]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _parse_cached_post_datetime(value: str) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None

    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(raw, fmt)
        except Exception:
            continue

    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except Exception:
        return None


def _pending_publish_likelihood(post: dict) -> str:
    explicit = str(post.get("publish_likelihood", "")).strip()
    if explicit and explicit != "pending-unclassified":
        return explicit

    if str(post.get("tweet_url", "")).strip():
        return "published-confirmed"

    signals = post.get("confidence_signals") or {}
    compose_candidates = int(signals.get("compose_candidates", 0) or 0)
    compose_matching_candidates = int(signals.get("compose_matching_candidates", 0) or 0)
    timeline_items = int(signals.get("timeline_items", 0) or 0)

    if compose_matching_candidates > 0:
        return "published-likely"
    if compose_candidates >= 3 and timeline_items >= 3:
        return "published-likely"
    if compose_candidates > 0 or timeline_items > 0:
        return "published-ambiguous"
    return "pending-unclassified"


def _pending_priority_key(post: dict) -> tuple[int, int, float]:
    rank_map = {
        "published-confirmed": 0,
        "published-likely": 1,
        "published-ambiguous": 2,
        "publish-signal-weak": 3,
        "pending-unclassified": 4,
    }
    likelihood = _pending_publish_likelihood(post)
    attempts = int(post.get("verification_attempts", 0) or 0)
    created_at = _parse_cached_post_datetime(str(post.get("date", "")))
    timestamp_score = 0.0 if created_at is None else -created_at.timestamp()
    return (rank_map.get(likelihood, 9), attempts, timestamp_score)


def _candidate_posts(account: dict) -> list[dict]:
    return [
        post
        for post in (account.get("posts", []) or [])
        if (not bool(post.get("post_verified", False)))
        or (str(post.get("verification_state", "")).strip().lower() == "pending")
        or (not str(post.get("tweet_url", "")).strip())
    ]


def _print_account_candidates(account: dict, limit: int) -> None:
    nickname = str(account.get("nickname", account.get("id", "unknown")))
    candidates = sorted(_candidate_posts(account), key=_pending_priority_key)[: max(limit, 1)]

    print("=" * 84)
    print(f"Account : {nickname}")
    print(f"Candidates shown : {len(candidates)}")
    for index, post in enumerate(candidates, start=1):
        content = str(post.get("content", "") or "")
        preview = content.replace("\n", " ")[:78]
        likelihood = _pending_publish_likelihood(post)
        attempts = int(post.get("verification_attempts", 0) or 0)
        normalized = _normalize_tweet(content)
        queries = PublishVerificationHardener.build_search_queries(content, max_queries=2)

        print(f"{index:>2}. {post.get('date', '')} | {likelihood} | attempts={attempts}")
        print(f"    Preview : {preview}")
        print(f"    Norm    : {normalized[:70]}")
        if queries:
            print(f"    Query 1 : {queries[0]}")
            if len(queries) > 1:
                print(f"    Query 2 : {queries[1]}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Show top pending Twitter recovery candidates")
    parser.add_argument("identifier", help="nickname, uuid, or 'all'")
    parser.add_argument("--limit", type=int, default=8, help="candidate posts to show per account")
    args = parser.parse_args()

    accounts = _resolve_accounts(args.identifier)
    if not accounts:
        print(f"No twitter account matched '{args.identifier}'.", file=sys.stderr)
        sys.exit(1)

    for account in accounts:
        _print_account_candidates(account, args.limit)


if __name__ == "__main__":
    main()
