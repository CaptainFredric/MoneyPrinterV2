#!/usr/bin/env python3
"""
scripts/open_x_login.py

Opens the exact Firefox profile for an account in a normal Firefox instance,
so X/Google login happens outside WebDriver automation.

Usage:
  python scripts/open_x_login.py EyeCatcher
"""

import argparse
import json
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parent.parent
TWITTER_CACHE = ROOT_DIR / ".mp" / "twitter.json"


def _load_accounts() -> list[dict]:
    try:
        with open(TWITTER_CACHE, "r", encoding="utf-8") as file:
            data = json.load(file)
        return data.get("accounts", [])
    except Exception:
        return []


def _resolve_account(identifier: str) -> dict:
    lowered = identifier.lower()
    for account in _load_accounts():
        if account.get("id", "").lower() == lowered or account.get("nickname", "").lower() == lowered:
            return account
    return {}


def _find_firefox_binary() -> str:
    candidates = []
    if platform.system() == "Darwin":
        candidates.extend(
            [
                "/Applications/Firefox.app/Contents/MacOS/firefox",
                str(Path.home() / "Applications/Firefox.app/Contents/MacOS/firefox"),
            ]
        )

    which_firefox = shutil.which("firefox")
    if which_firefox:
        candidates.append(which_firefox)

    for candidate in candidates:
        if candidate and os.path.exists(candidate):
            return candidate
    return ""


def main() -> None:
    parser = argparse.ArgumentParser(description="Open an account Firefox profile for native X login repair")
    parser.add_argument("identifier", help="nickname or uuid")
    parser.add_argument(
        "--url",
        default="https://x.com/i/flow/login",
        help="URL to open in the profile (default: X login)",
    )
    args = parser.parse_args()

    account = _resolve_account(args.identifier)
    if not account:
        print(f"No twitter account matched '{args.identifier}'.", file=sys.stderr)
        sys.exit(1)

    profile_path = str(account.get("firefox_profile", "")).strip()
    if not profile_path or not os.path.isdir(profile_path):
        print(f"Invalid Firefox profile path: {profile_path or 'missing'}", file=sys.stderr)
        sys.exit(1)

    firefox_binary = _find_firefox_binary()
    if not firefox_binary:
        print("Could not find Firefox binary on this machine.", file=sys.stderr)
        sys.exit(1)

    command = [
        firefox_binary,
        "-no-remote",
        "-profile",
        profile_path,
        args.url,
    ]
    subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    print("=" * 72)
    print(f"Account : {account.get('nickname', '?')}")
    if account.get("x_username"):
        print(f"Handle  : {account['x_username']}")
    print(f"Profile : {profile_path}")
    print(f"Opened  : {args.url}")
    print("")
    print("Use the normal Firefox window that just opened.")
    print("Important:")
    print("- Do not use the WebDriver-opened login tab for Google sign-in.")
    print("- Prefer logging into X directly with username/email + password.")
    print("- If Google SSO still blocks, complete login in normal Firefox and then rerun session checks.")


if __name__ == "__main__":
    main()
