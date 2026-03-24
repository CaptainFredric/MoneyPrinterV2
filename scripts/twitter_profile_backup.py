#!/usr/bin/env python3

import argparse
import sys
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parent.parent
SRC_DIR = ROOT_DIR / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from twitter_session_backup import backup_profiles  # noqa: E402
from twitter_session_backup import list_backups  # noqa: E402
from twitter_session_backup import resolve_accounts  # noqa: E402
from twitter_session_backup import restore_account_profile  # noqa: E402


def _print_backup_results(results: list[dict]) -> int:
    failures = 0
    for result in results:
        print("=" * 72)
        print(f"Account : {result.get('account', '?')}")
        print(f"Created : {'YES' if result.get('created') else 'NO'}")
        print(f"Reason  : {result.get('reason', 'unknown')}")
        if result.get("auth_cookie_count") is not None:
            print(f"Cookies : auth={result['auth_cookie_count']}")
        if result.get("path"):
            print(f"Archive : {result['path']}")
        if result.get("reason") in {"profile-not-found"}:
            failures += 1
    print("=" * 72)
    return failures


def _print_status(accounts: list[dict]) -> int:
    failures = 0
    for account in accounts:
        backups = list_backups(account)
        nickname = account.get("nickname", account.get("id", "?"))
        print("=" * 72)
        print(f"Account : {nickname}")
        print(f"Backups : {len(backups)}")
        if backups:
            latest = backups[0]
            metadata = latest.get("metadata", {})
            print(f"Latest  : {latest['path'].name}")
            print(f"When    : {latest['modified_at']}")
            print(f"Cookies : auth={metadata.get('auth_cookie_count', 0)}")
        else:
            print("Latest  : none")
            failures += 1
    print("=" * 72)
    return failures


def main() -> None:
    parser = argparse.ArgumentParser(description="Back up and restore Firefox Twitter session profiles")
    subparsers = parser.add_subparsers(dest="command", required=True)

    backup_parser = subparsers.add_parser("backup", help="Create profile backups for one account or all accounts")
    backup_parser.add_argument("identifier", help="nickname, uuid, or 'all'")
    backup_parser.add_argument("--force", action="store_true", help="force a new backup even when unchanged")

    status_parser = subparsers.add_parser("status", help="List available backups")
    status_parser.add_argument("identifier", nargs="?", default="all", help="nickname, uuid, or 'all'")

    restore_parser = subparsers.add_parser("restore", help="Restore one account from a saved profile backup")
    restore_parser.add_argument("identifier", help="nickname or uuid")
    restore_parser.add_argument("archive", nargs="?", default="latest", help="archive name or 'latest'")
    restore_parser.add_argument("--allow-missing", action="store_true", help="exit 0 when no backup exists")

    args = parser.parse_args()

    if args.command == "backup":
        accounts = resolve_accounts(args.identifier)
        if not accounts:
            print(f"No twitter account matched '{args.identifier}'.", file=sys.stderr)
            sys.exit(1)
        failures = _print_backup_results(backup_profiles(args.identifier, force=args.force))
        sys.exit(1 if failures else 0)

    if args.command == "status":
        accounts = resolve_accounts(args.identifier)
        if not accounts:
            print(f"No twitter account matched '{args.identifier}'.", file=sys.stderr)
            sys.exit(1)
        failures = _print_status(accounts)
        sys.exit(1 if failures else 0)

    accounts = resolve_accounts(args.identifier)
    if not accounts:
        print(f"No twitter account matched '{args.identifier}'.", file=sys.stderr)
        sys.exit(1)
    account = accounts[0]
    result = restore_account_profile(account, archive_name=args.archive, allow_missing=args.allow_missing)
    print("=" * 72)
    print(f"Account : {result.get('account', '?')}")
    print(f"Restored: {'YES' if result.get('restored') else 'NO'}")
    print(f"Reason  : {result.get('reason', 'unknown')}")
    if result.get("path"):
        print(f"Archive : {result['path']}")
    if result.get("safety_path"):
        print(f"Safety  : {result['safety_path']}")
    if result.get("auth_cookie_count") is not None:
        print(f"Cookies : auth={result['auth_cookie_count']}")
    print("=" * 72)
    if result.get("restored"):
        sys.exit(0)
    if args.allow_missing and result.get("reason") == "backup-missing-allowed":
        sys.exit(0)
    sys.exit(1)


if __name__ == "__main__":
    main()
