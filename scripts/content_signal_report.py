#!/usr/bin/env python3
"""
Analyze cached Twitter post outcomes to surface conversion signals.

Purpose:
- Show which categories, formats, and angles produce verified posts
- Highlight accounts with large pending backlogs
- Give a small evidence-based strategy summary for the next posting cycle

Usage:
  python scripts/content_signal_report.py
  python scripts/content_signal_report.py --top 5
"""

from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parent.parent
TWITTER_CACHE = ROOT_DIR / ".mp" / "twitter.json"


def _load_accounts() -> list[dict]:
    if not TWITTER_CACHE.exists():
        return []
    try:
        with open(TWITTER_CACHE, "r", encoding="utf-8") as file:
            data = json.load(file)
        return data.get("accounts", [])
    except Exception:
        return []


def _outcome(post: dict) -> str:
    if bool(post.get("post_verified", False)):
        return "verified"
    if str(post.get("verification_state", "")).strip().lower() == "pending":
        return "pending"
    return "other"


def _safe_label(value: object, fallback: str = "(none)") -> str:
    text = str(value or "").strip()
    return text if text else fallback


def _print_metric_table(title: str, stats: dict[str, dict[str, int]], top: int) -> None:
    print("\n" + title)
    print("-" * len(title))
    ranked = sorted(
        stats.items(),
        key=lambda item: (item[1].get("verified", 0), -item[1].get("pending", 0), item[0]),
        reverse=True,
    )
    for label, values in ranked[:top]:
        total = values.get("total", 0)
        verified = values.get("verified", 0)
        pending = values.get("pending", 0)
        rate = (verified / total * 100.0) if total else 0.0
        print(f"- {label}: total={total} verified={verified} pending={pending} verify-rate={rate:.0f}%")


def main() -> None:
    parser = argparse.ArgumentParser(description="Report content conversion signals from cached Twitter posts")
    parser.add_argument("--top", type=int, default=8, help="number of top rows to show per section")
    args = parser.parse_args()

    accounts = _load_accounts()
    if not accounts:
        print("No cached accounts found.")
        return

    category_stats: dict[str, dict[str, int]] = defaultdict(lambda: {"total": 0, "verified": 0, "pending": 0})
    format_stats: dict[str, dict[str, int]] = defaultdict(lambda: {"total": 0, "verified": 0, "pending": 0})
    angle_stats: dict[str, dict[str, int]] = defaultdict(lambda: {"total": 0, "verified": 0, "pending": 0})
    source_stats: dict[str, dict[str, int]] = defaultdict(lambda: {"total": 0, "verified": 0, "pending": 0})

    print("=" * 72)
    print("Money Dream Content Signal Report")
    print("=" * 72)

    for account in accounts:
        nickname = _safe_label(account.get("nickname"), "unknown")
        posts = account.get("posts", []) or []
        outcomes = Counter(_outcome(post) for post in posts)
        total = len(posts)
        verified = outcomes.get("verified", 0)
        pending = outcomes.get("pending", 0)
        verify_rate = (verified / total * 100.0) if total else 0.0

        print(f"\nAccount: {nickname}")
        print(f"- Total posts: {total}")
        print(f"- Verified: {verified}")
        print(f"- Pending: {pending}")
        print(f"- Verify rate: {verify_rate:.0f}%")

        for post in posts:
            outcome = _outcome(post)
            category = _safe_label(post.get("category"))
            post_format = _safe_label(post.get("format"))
            angle = _safe_label(post.get("angle_signature"), "(missing-angle)")
            source = _safe_label(post.get("citation_source"))

            for bucket, label in (
                (category_stats, category),
                (format_stats, post_format),
                (angle_stats, angle),
                (source_stats, source),
            ):
                bucket[label]["total"] += 1
                if outcome == "verified":
                    bucket[label]["verified"] += 1
                elif outcome == "pending":
                    bucket[label]["pending"] += 1

    _print_metric_table("Top Categories", category_stats, max(1, args.top))
    _print_metric_table("Top Formats", format_stats, max(1, args.top))
    _print_metric_table("Top Angle Signatures", angle_stats, max(1, args.top))
    _print_metric_table("Top Citation Sources", source_stats, max(1, args.top))

    winning_categories = [label for label, values in category_stats.items() if values.get("verified", 0) > 0]
    heavy_pending_categories = sorted(
        [
            (label, values.get("pending", 0))
            for label, values in category_stats.items()
            if values.get("pending", 0) > values.get("verified", 0)
        ],
        key=lambda item: item[1],
        reverse=True,
    )

    print("\nStrategy Notes")
    print("--------------")
    if winning_categories:
        print(f"- Favor categories with verified wins first: {', '.join(sorted(winning_categories)[:5])}")
    if heavy_pending_categories:
        print(
            "- Reduce blind repetition in high-pending categories: "
            + ", ".join(f"{label}({pending})" for label, pending in heavy_pending_categories[:5])
        )
    print("- Treat pending backlog as a conversion problem: keep verifying before increasing post volume.")


if __name__ == "__main__":
    main()