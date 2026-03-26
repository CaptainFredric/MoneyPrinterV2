#!/usr/bin/env python3
import shutil
import sqlite3
import tempfile
import os
import sys
from pathlib import Path
from datetime import datetime

ROOT = Path(__file__).resolve().parent.parent
FF_DIR = Path.home() / "Library" / "Application Support" / "Firefox" / "Profiles"
DESTS = [ROOT / "secrets" / "twitter_automation_profile", ROOT / "secrets" / "twitter_automation_profile_v3"]
LOG = ROOT / ".mp" / "manual_copy_minimal.log"
STATE_FILES = ["cookies.sqlite", "cookies.sqlite-shm", "cookies.sqlite-wal", "key4.db", "key3.db", "logins.json"]


def count_auth(dbpath: Path) -> int:
    try:
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
        print('count error', e, file=sys.stderr)
        return 0


def log(msg: str):
    ts = datetime.now().isoformat(timespec='seconds')
    with open(LOG, 'a', encoding='utf-8') as f:
        f.write(f"[{ts}] {msg}\n")
    print(msg)


def find_source():
    if not FF_DIR.exists():
        return None
    candidates = []
    for p in sorted(FF_DIR.glob('*')):
        if not p.is_dir():
            continue
        cnt = count_auth(p / 'cookies.sqlite')
        candidates.append((p, cnt))
    candidates = sorted(candidates, key=lambda x: x[1], reverse=True)
    for p, c in candidates:
        if c > 0:
            return p
    return None


def main():
    os.makedirs(LOG.parent, exist_ok=True)
    log('Starting manual minimal copy')
    src = find_source()
    if not src:
        log('No source profile with auth cookies found')
        sys.exit(2)
    log(f'Chosen source: {src}')

    for dest in DESTS:
        dest = Path(dest)
        if not dest.exists():
            log(f'dest profile not found: {dest}')
            continue
        # backup dest small (copy key files only)
        try:
            bdir = ROOT / '.mp' / 'profile_backups_manual' / dest.name
            bdir.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            for fname in STATE_FILES:
                srcf = src / fname
                if srcf.exists():
                    shutil.copy2(srcf, bdir / f'{fname}.precopy_{stamp}')
            log(f'Backed up key state for {dest} to {bdir}')
        except Exception as e:
            log(f'backup failed for {dest}: {e}')

        # copy minimal set
        for fname in STATE_FILES:
            srcf = src / fname
            dpf = dest / fname
            try:
                if srcf.exists():
                    shutil.copy2(srcf, dpf)
                    log(f'copied {fname} to {dest}')
                else:
                    log(f'missing {fname} in source')
            except Exception as e:
                log(f'failed copying {fname} to {dest}: {e}')

        # verify
        cnt = count_auth(dest / 'cookies.sqlite')
        log(f'dest auth cookie count after copy for {dest}: {cnt}')

    log('Done')

if __name__ == '__main__':
    main()
