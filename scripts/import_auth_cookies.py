#!/usr/bin/env python3
import json
import shutil
import sqlite3
import tempfile
import tarfile
import os
import sys
from pathlib import Path
from datetime import datetime

ROOT = Path(__file__).resolve().parent.parent
TWITTER_CACHE = ROOT / ".mp" / "twitter.json"
BACKUP_ROOT = ROOT / ".mp" / "profile_backups_manual"
FF_PROFILES_DIR = Path.home() / "Library" / "Application Support" / "Firefox" / "Profiles"

STATE_FILES = [
    "cookies.sqlite",
    "cookies.sqlite-shm",
    "cookies.sqlite-wal",
    "key4.db",
    "key3.db",
    "logins.json",
    "sessionstore.jsonlz4",
    "sessionCheckpoints.json",
    "storage/default",
    "storage/permanent",
    "storage-sync-v2.sqlite",
    "storage-sync-v2.sqlite-shm",
    "storage-sync-v2.sqlite-wal",
    "webappsstore.sqlite",
    "webappsstore.sqlite-shm",
    "webappsstore.sqlite-wal",
    "prefs.js",
    "user.js",
    "containers.json",
    "permissions.sqlite",
    "permissions.sqlite-shm",
    "permissions.sqlite-wal",
    "xulstore.json",
]


def count_auth(dbpath: Path) -> int:
    try:
        dbpath = Path(dbpath)
        if not dbpath.exists():
            return 0
        tmp = Path(tempfile.mktemp(suffix='.sqlite'))
        shutil.copy2(dbpath, tmp)
        conn = sqlite3.connect(str(tmp))
        cur = conn.cursor()
        cur.execute(
            """
            SELECT COUNT(*)
            FROM moz_cookies
            WHERE (host LIKE '%x.com%' OR host LIKE '%twitter.com%')
              AND name IN ('auth_token', 'ct0')
            """
        )
        row = cur.fetchone()
        conn.close()
        try:
            tmp.unlink()
        except Exception:
            pass
        return int(row[0]) if row else 0
    except Exception as e:
        print('count error', dbpath, e, file=sys.stderr)
        return 0


# Load accounts
try:
    with open(TWITTER_CACHE, 'r', encoding='utf-8') as f:
        accounts = json.load(f).get('accounts', [])
except Exception as e:
    print('failed to read .mp/twitter.json', e, file=sys.stderr)
    sys.exit(1)

# Scan local Firefox profiles
sources = []
if FF_PROFILES_DIR.exists():
    for p in sorted(FF_PROFILES_DIR.glob('*')):
        if not p.is_dir():
            continue
        cnt = count_auth(p / 'cookies.sqlite')
        sources.append((p, cnt))
else:
    print('Firefox profiles dir not found:', FF_PROFILES_DIR, file=sys.stderr)

sources = sorted(sources, key=lambda x: x[1], reverse=True)
print('Found source profiles with auth cookie counts:')
for p, c in sources:
    print(f'  {p} -> {c}')

# For each account, pick best source and copy state files
for account in accounts:
    nickname = account.get('nickname') or account.get('id')[:8]
    dest = Path(account.get('firefox_profile', '')).expanduser()
    print('\nProcessing account', nickname)
    if not dest.exists():
        print('  dest profile not found:', dest)
        continue

    # Backup dest profile
    BACKUP_ROOT.mkdir(parents=True, exist_ok=True)
    acc_backup_dir = BACKUP_ROOT / nickname
    acc_backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    archive = acc_backup_dir / f'{nickname}_pre_copy_{stamp}.tar.gz'
    try:
        with tarfile.open(archive, 'w:gz') as tar:
            tar.add(dest, arcname='profile')
        print('  backed up dest profile to', archive)
    except Exception as e:
        print('  failed to backup dest profile', e, file=sys.stderr)

    # Choose a source profile with at least 1 auth cookie
    chosen = None
    for p, c in sources:
        if c > 0:
            chosen = p
            break
    if not chosen:
        print('  no source profile with auth cookies found; skipping')
        continue
    print('  chosen source profile', chosen)

    # Copy listed state files
    for rel in STATE_FILES:
        sp = chosen / rel
        dp = dest / rel
        try:
            dp.parent.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass
        if sp.exists():
            try:
                if sp.is_dir():
                    shutil.copytree(sp, dp, dirs_exist_ok=True)
                else:
                    shutil.copy2(sp, dp)
                print('   copied', rel)
            except Exception as e:
                print('   failed copy', rel, e, file=sys.stderr)
        else:
            pass

    # Verify auth cookies in destination
    cnt = count_auth(dest / 'cookies.sqlite')
    print('  dest auth cookie count after copy:', cnt)

print('\nDone.')
