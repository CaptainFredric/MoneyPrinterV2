#!/usr/bin/env python3
"""
scripts/session_restore.py

Restores a Twitter bot account session by transplanting fresh auth cookies
from any Firefox profile that has a valid login for that account.

Usage:
  python scripts/session_restore.py niche_launch_1
  python scripts/session_restore.py EyeCatcher
  python scripts/session_restore.py all          # tries all accounts

How it works:
  1. Scans every Firefox profile on this machine for x.com auth cookies.
  2. Matches each profile to the bot account by comparing the `twid` user ID
     against the user ID stored in the bot's own profile backup.
  3. If a fresh (non-invalidated) session is found, transplants auth cookies
     into the bot's profile and verifies the session works headlessly.
  4. If no matching profile exists, opens Firefox Dev Edition to the login page
     so the user can log in manually, then re-runs the scan automatically.
"""

from __future__ import annotations

import json
import os
import shutil
import signal
import sqlite3
import sys
import tempfile
import time
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
SRC_DIR = ROOT_DIR / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

TWITTER_CACHE = ROOT_DIR / ".mp" / "twitter.json"
FF_PROFILES_ROOT = Path.home() / "Library/Application Support/Firefox/Profiles"

# Columns present in Firefox 149+ moz_cookies table
_COOKIE_COLS = (
    "originAttributes, name, value, host, path, expiry, "
    "lastAccessed, creationTime, isSecure, isHttpOnly, "
    "inBrowserElement, sameSite, schemeMap, isPartitionedAttributeSet, updateTime"
)
_AUTH_NAMES = {"auth_token", "ct0", "twid", "kdt", "att"}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_accounts() -> list[dict]:
    try:
        return json.loads(TWITTER_CACHE.read_text())["accounts"]
    except Exception:
        return []


def _resolve_accounts(identifier: str) -> list[dict]:
    accounts = _load_accounts()
    if identifier.lower() == "all":
        return accounts
    lo = identifier.lower()
    return [a for a in accounts if a.get("id","").lower() == lo or a.get("nickname","").lower() == lo]


def _safe_copy_db(src: Path, dst: str) -> bool:
    """Copy a SQLite DB (+ WAL) to a tmp location so we can read without locks."""
    try:
        shutil.copy2(str(src), dst)
        wal = Path(str(src) + "-wal")
        if wal.exists():
            shutil.copy2(str(wal), dst + "-wal")
        return True
    except Exception:
        return False


def _read_auth_cookies(db_path: str) -> list[tuple]:
    """Return all x.com/twitter cookies from a (tmp copy) DB."""
    try:
        con = sqlite3.connect(db_path, timeout=5)
        con.execute("PRAGMA wal_checkpoint(FULL)")
        rows = con.execute(
            f"SELECT {_COOKIE_COLS} FROM moz_cookies "
            "WHERE host LIKE '%x.com%' OR host LIKE '%twitter%'"
        ).fetchall()
        con.close()
        return rows
    except Exception:
        return []


def _get_twid(rows: list[tuple]) -> str:
    """Extract twid value (u%3D<user_id>) from cookie rows."""
    # name is index 1
    for r in rows:
        if r[1] == "twid":
            return r[2]
    return ""


def _has_auth_token(rows: list[tuple]) -> bool:
    return any(r[1] == "auth_token" for r in rows)


def _scan_all_ff_profiles() -> list[dict]:
    """Return list of {profile_dir, twid, has_auth, rows} for every FF profile."""
    results = []
    if not FF_PROFILES_ROOT.exists():
        return results
    for p in FF_PROFILES_ROOT.iterdir():
        db = p / "cookies.sqlite"
        if not db.exists():
            continue
        tmp = f"/tmp/mpv2_scan_{p.name}.sqlite"
        if not _safe_copy_db(db, tmp):
            continue
        rows = _read_auth_cookies(tmp)
        if not rows:
            continue
        twid = _get_twid(rows)
        has_auth = _has_auth_token(rows)
        results.append({"profile_dir": p, "twid": twid, "has_auth": has_auth, "rows": rows})
    return results


def _get_bot_profile_twid(bot_profile_path: Path) -> str:
    """Read twid from the bot profile's own cookie DB (or backup)."""
    for db_candidate in [
        bot_profile_path / "cookies.sqlite",
        bot_profile_path / "cookies.sqlite.bak",
        bot_profile_path / "cookies.sqlite.pre_transplant_bak",
    ]:
        if not db_candidate.exists():
            continue
        tmp = f"/tmp/mpv2_bot_twid_{bot_profile_path.name}.sqlite"
        if _safe_copy_db(db_candidate, tmp):
            rows = _read_auth_cookies(tmp)
            twid = _get_twid(rows)
            if twid:
                return twid
    return ""


def _transplant_cookies(rows: list[tuple], bot_profile_path: Path) -> bool:
    """Write fresh auth cookies into the bot profile's cookies.sqlite."""
    dst_db  = bot_profile_path / "cookies.sqlite"
    dst_shm = bot_profile_path / "cookies.sqlite-shm"
    dst_wal = bot_profile_path / "cookies.sqlite-wal"

    # Clear stale locks
    for lf in (".parentlock", "parent.lock", "lock"):
        lp = bot_profile_path / lf
        if lp.exists():
            try:
                lp.unlink()
            except Exception:
                pass

    # Backup before touching
    try:
        shutil.copy2(str(dst_db), str(dst_db) + ".pre_transplant_bak")
    except Exception:
        pass

    try:
        con = sqlite3.connect(str(dst_db), timeout=10)
        con.execute("DELETE FROM moz_cookies WHERE host LIKE '%x.com%' OR host LIKE '%twitter%'")
        placeholders = ",".join(["?"] * len(_COOKIE_COLS.split(",")))
        con.executemany(
            f"INSERT OR REPLACE INTO moz_cookies ({_COOKIE_COLS}) VALUES ({placeholders})",
            rows,
        )
        con.commit()
        con.execute("PRAGMA wal_checkpoint(FULL)")
        con.close()
    except Exception as e:
        print(f"  ❌ Transplant DB error: {e}")
        return False

    # Reset WAL/SHM
    for f in (dst_shm, dst_wal):
        try:
            if f.exists():
                f.write_bytes(b"")
        except Exception:
            pass

    return True


def _verify_session(account: dict) -> bool:
    """Open Firefox headlessly and confirm session is ready."""
    try:
        from classes.Twitter import Twitter
        os.environ["MPV2_HEADLESS"] = "1"
        t = Twitter(
            account["id"],
            account.get("nickname", "?"),
            account["firefox_profile"],
            account.get("topic", ""),
            account.get("browser_binary", ""),
        )
        result = t.check_session()
        t.quit()
        return bool(result.get("ready"))
    except Exception as e:
        print(f"  ⚠️  Session check error: {e}")
        return False


def _open_login_window(account: dict) -> None:
    """Open Firefox Dev Edition to the X login page for manual login."""
    from firefox_runtime import resolve_firefox_binary, clear_profile_locks
    fp = account.get("firefox_profile", "")
    binary = resolve_firefox_binary("", profile_path=fp)
    clear_profile_locks(fp)
    import subprocess
    subprocess.Popen(
        [binary, "-no-remote", "-profile", fp, "https://x.com/i/flow/login"],
        start_new_session=True,
    )


# ---------------------------------------------------------------------------
# Main restore logic
# ---------------------------------------------------------------------------

def restore_account(account: dict) -> bool:
    nick = account.get("nickname", "?")
    bot_profile = Path(account.get("firefox_profile", ""))
    print(f"\n{'='*60}")
    print(f"Restoring session for: {nick}")
    print(f"{'='*60}")

    if not bot_profile.exists():
        print(f"  ❌ Bot profile path missing: {bot_profile}")
        return False

    # Step 1: Check if session is already healthy
    print("  Checking current session...")
    if _verify_session(account):
        print(f"  ✅ Session already healthy — nothing to do.")
        return True

    # Step 2: Determine the bot account's user ID from its backup
    print("  Reading bot account user ID from profile backup...")
    bot_twid = _get_bot_profile_twid(bot_profile)
    print(f"  Bot twid: {bot_twid or '(not found)'}")

    # Step 3: Scan all Firefox profiles for a matching fresh session
    print("  Scanning all Firefox profiles for a matching session...")
    profiles = _scan_all_ff_profiles()

    match = None
    if bot_twid:
        for p in profiles:
            if p["twid"] == bot_twid and p["has_auth"]:
                match = p
                print(f"  ✅ Found match: {p['profile_dir'].name}")
                break

    if match is None:
        # Fall back: any profile with auth_token (user must confirm it's right)
        auth_profiles = [p for p in profiles if p["has_auth"]]
        if auth_profiles and not bot_twid:
            # Only one account — use it
            if len(auth_profiles) == 1:
                match = auth_profiles[0]
                print(f"  ⚠️  No twid match, using only available auth profile: {match['profile_dir'].name}")

    if match is None:
        print(f"  ⚠️  No matching logged-in profile found.")
        print(f"  Opening Firefox to login page — please log into @{account.get('x_username','?')} then rerun this script.")
        _open_login_window(account)
        return False

    # Step 4: Transplant cookies
    print(f"  Transplanting {len(match['rows'])} cookies into bot profile...")
    if not _transplant_cookies(match["rows"], bot_profile):
        return False

    # Step 5: Verify the transplanted session works
    print("  Verifying restored session...")
    time.sleep(1)
    if _verify_session(account):
        print(f"  ✅ Session restored and verified for {nick}!")
        return True
    else:
        print(f"  ❌ Cookie transplant done but session check failed — token may be server-invalidated.")
        return False


def main() -> None:
    identifier = sys.argv[1] if len(sys.argv) > 1 else "all"
    accounts = _resolve_accounts(identifier)
    if not accounts:
        print(f"No accounts found for: {identifier}")
        sys.exit(1)

    results = {}
    for account in accounts:
        ok = restore_account(account)
        results[account.get("nickname", "?")] = ok

    print(f"\n{'='*60}")
    print("Restore Summary")
    print(f"{'='*60}")
    for nick, ok in results.items():
        print(f"  {'✅' if ok else '❌'} {nick}")

    if not all(results.values()):
        sys.exit(1)


if __name__ == "__main__":
    main()
