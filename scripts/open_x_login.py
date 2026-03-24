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
SRC_DIR = ROOT_DIR / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from firefox_runtime import resolve_firefox_binary


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


def _is_profile_already_open(profile_path: str) -> bool:
    lock_files = [".parentlock", "parent.lock", "lock"]
    for lock_name in lock_files:
        if os.path.exists(os.path.join(profile_path, lock_name)):
            return True
    return False


def _is_firefox_process_running_for_profile(profile_path: str) -> bool:
    try:
        result = subprocess.run(
            ["pgrep", "-af", "Firefox.app/Contents/MacOS/firefox|/firefox"],
            capture_output=True,
            text=True,
            check=False,
        )
        for line in (result.stdout or "").splitlines():
            if profile_path in line:
                return True
        return False
    except Exception:
        return False


def _clear_profile_locks(profile_path: str) -> int:
    removed = 0
    for lock_name in [".parentlock", "parent.lock", "lock"]:
        lock_path = os.path.join(profile_path, lock_name)
        if os.path.exists(lock_path):
            try:
                os.remove(lock_path)
                removed += 1
            except OSError:
                pass
    return removed


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

    firefox_binary = resolve_firefox_binary(str(account.get("browser_binary", "")).strip())
    if not firefox_binary:
        print("Could not find Firefox binary on this machine.", file=sys.stderr)
        sys.exit(1)

    if _is_profile_already_open(profile_path):
        if _is_firefox_process_running_for_profile(profile_path):
            print("=" * 72)
            print(f"Account : {account.get('nickname', '?')}")
            if account.get("x_username"):
                print(f"Handle  : {account['x_username']}")
            print(f"Profile : {profile_path}")
            print("Opened  : already running")
            print("")
            print("This profile already has an open Firefox window.")
            print("Reusing existing window to avoid duplicate profile instances.")
            return
        removed_locks = _clear_profile_locks(profile_path)
        if removed_locks > 0:
            print(f"Cleared {removed_locks} stale profile lock file(s).")

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
    print(f"Binary  : {firefox_binary}")
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
