#!/usr/bin/env python3
"""
scripts/run_once.py — Fire a single post/upload job right now.

Designed for:
  • Phone SSH sessions (Termius / Blink) where you just want one tap to post
  • Manual one-off posts outside the daemon schedule
  • Quick cron testing

Usage:
    python scripts/run_once.py twitter <account_uuid_or_nickname>
    python scripts/run_once.py youtube <account_uuid_or_nickname>
    python scripts/run_once.py twitter all   # post from every twitter account
    python scripts/run_once.py --list        # show available accounts

No interactive prompts — exits 0 on success, 1 on failure.
"""
import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT_DIR    = Path(__file__).resolve().parent.parent
VENV_PYTHON = ROOT_DIR / "venv" / "bin" / "python"
CRON_SCRIPT = ROOT_DIR / "src" / "cron.py"
CONFIG_PATH = ROOT_DIR / "config.json"
MP_DIR      = ROOT_DIR / ".mp"


def _load_json(path: Path) -> dict:
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return {}


def _load_config() -> dict:
    return _load_json(CONFIG_PATH)


def _get_model(cfg: dict) -> str:
    return cfg.get("ollama_model", "llama3.2:3b")


def _get_accounts(provider: str) -> list[dict]:
    cache_file = MP_DIR / f"{provider}.json"
    data = _load_json(cache_file)
    return data.get("accounts", [])


def _resolve_account(provider: str, identifier: str) -> list[dict]:
    """
    Accepts a UUID, a nickname (case-insensitive), or 'all'.
    Returns list of matching account dicts.
    """
    accounts = _get_accounts(provider)
    if identifier.lower() == "all":
        return accounts
    matches = [
        a for a in accounts
        if a.get("id") == identifier
        or a.get("nickname", "").lower() == identifier.lower()
    ]
    return matches


def _run(provider: str, account: dict, model: str, headless: bool) -> bool:
    nickname = account.get("nickname", account["id"][:8])
    print(f"▶  Posting: {provider}/{nickname}")
    env = os.environ.copy()
    if headless:
        env["MPV2_HEADLESS"] = "1"
    cmd = [str(VENV_PYTHON), str(CRON_SCRIPT), provider, account["id"], model]
    result = subprocess.run(cmd, env=env, timeout=300)
    if result.returncode == 0:
        print(f"✅ Done: {provider}/{nickname}")
        return True
    else:
        print(f"❌ Failed (exit {result.returncode}): {provider}/{nickname}", file=sys.stderr)
        return False


def do_list():
    print("\nAvailable accounts:\n")
    for provider in ("twitter", "youtube"):
        accounts = _get_accounts(provider)
        if accounts:
            print(f"  [{provider}]")
            for a in accounts:
                print(f"    nickname={a.get('nickname','?'):20s} id={a.get('id','?')}")
    print()


def main():
    parser = argparse.ArgumentParser(
        description="MoneyPrinterV2 — run a single post job immediately"
    )
    parser.add_argument("provider",   nargs="?", help="twitter | youtube")
    parser.add_argument("account",    nargs="?", help="UUID, nickname, or 'all'")
    parser.add_argument("--headless", action="store_true", help="Force headless browser")
    parser.add_argument("--list",     action="store_true", help="List available accounts")
    args = parser.parse_args()

    if args.list or not args.provider:
        do_list()
        return

    provider   = args.provider.lower()
    identifier = args.account or "all"

    if provider not in ("twitter", "youtube"):
        print(f"Unknown provider '{provider}'. Use 'twitter' or 'youtube'.", file=sys.stderr)
        sys.exit(1)

    cfg   = _load_config()
    model = _get_model(cfg)

    accounts = _resolve_account(provider, identifier)
    if not accounts:
        print(f"No {provider} account matching '{identifier}'.", file=sys.stderr)
        print("Run: python scripts/run_once.py --list")
        sys.exit(1)

    failures = 0
    for acc in accounts:
        ok = _run(provider, acc, model, args.headless)
        if not ok:
            failures += 1

    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
