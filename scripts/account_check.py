#!/usr/bin/env python3
"""
scripts/account_check.py

Quick account inspection for phone use:
- list all twitter accounts
- show one account by nickname/uuid
- display profile path, topic, post count, last post

Usage:
    python scripts/account_check.py --list
    python scripts/account_check.py niche_launch_1
    python scripts/account_check.py 028a8895-0d13-40d7-9a49-a19749f4cd5b
"""
import argparse
import json
from datetime import datetime
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
TWITTER_CACHE = ROOT_DIR / ".mp" / "twitter.json"


def load_accounts() -> list[dict]:
    try:
        with open(TWITTER_CACHE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data.get("accounts", [])
    except Exception:
        return []


def fmt_last_post(posts: list[dict]) -> str:
    if not posts:
        return "never"
    try:
        dt = datetime.strptime(posts[-1]["date"], "%m/%d/%Y, %H:%M:%S")
        delta = datetime.now() - dt
        minutes = int(delta.total_seconds() // 60)
        return f"{minutes}m ago ({posts[-1]['date']})"
    except Exception:
        return posts[-1].get("date", "unknown")


def print_account(acc: dict):
    posts = acc.get("posts", [])
    print("=" * 60)
    print(f"Nickname      : {acc.get('nickname', '?')}")
    if acc.get("display_name"):
        print(f"Display Name  : {acc.get('display_name')}")
    if acc.get("x_username"):
        print(f"X Username    : {acc.get('x_username')}")
    if acc.get("login_email"):
        print(f"Login Email   : {acc.get('login_email')}")
    print(f"UUID          : {acc.get('id', '?')}")
    print(f"Topic         : {acc.get('topic', '?')}")
    print(f"Firefox profile: {acc.get('firefox_profile', '?')}")
    if acc.get("bio_draft"):
        print(f"Bio Draft     : {acc.get('bio_draft')}")
    if acc.get("avatar_idea"):
        print(f"Avatar Idea   : {acc.get('avatar_idea')}")
    if acc.get("banner_idea"):
        print(f"Banner Idea   : {acc.get('banner_idea')}")
    if isinstance(acc.get("link_post_ratio"), (int, float)):
        print(f"Link Ratio    : {acc.get('link_post_ratio')}")
    if isinstance(acc.get("media_post_ratio"), (int, float)):
        print(f"Media Ratio   : {acc.get('media_post_ratio')}")
    if isinstance(acc.get("citation_post_ratio"), (int, float)):
        print(f"Citation Ratio: {acc.get('citation_post_ratio')}")
    trusted_links = acc.get("trusted_links") or acc.get("link_pool") or acc.get("source_links") or []
    if isinstance(trusted_links, list) and trusted_links:
        print(f"Trusted Links : {len(trusted_links)} configured")
    print(f"Posts         : {len(posts)}")
    print(f"Last Post     : {fmt_last_post(posts)}")
    if posts:
        category = posts[-1].get("category", "")
        if category:
            print(f"Last Category : {category}")
        citation_source = posts[-1].get("citation_source", "")
        if citation_source:
            print(f"Last Source   : {citation_source}")
        angle_signature = posts[-1].get("angle_signature", "")
        if angle_signature:
            print(f"Last Angle    : {angle_signature}")
        preview = posts[-1].get("content", "").replace("\n", " ")[:120]
        print(f"Last Preview  : {preview}...")
    print("=" * 60)


def main():
    parser = argparse.ArgumentParser(description="Inspect Twitter account status from cache")
    parser.add_argument("identifier", nargs="?", help="nickname or uuid")
    parser.add_argument("--list", action="store_true", help="list accounts")
    args = parser.parse_args()

    accounts = load_accounts()
    if not accounts:
        print("No accounts found in .mp/twitter.json")
        return

    if args.list or not args.identifier:
        print("Available Twitter accounts:\n")
        for a in accounts:
            username = a.get("x_username", "")
            display_name = a.get("display_name", "")
            suffix = f"  {display_name} {username}".rstrip()
            print(f"- {a.get('nickname','?'):20s}  {a.get('id','?')}{suffix}")
        return

    ident = args.identifier.lower()
    for a in accounts:
        if a.get("id", "").lower() == ident or a.get("nickname", "").lower() == ident:
            print_account(a)
            return

    print(f"No account found matching '{args.identifier}'.")


if __name__ == "__main__":
    main()
