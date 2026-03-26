#!/usr/bin/env python3
import shutil
import sqlite3
import tempfile
import sys
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FF = Path.home() / "Library" / "Application Support" / "Firefox" / "Profiles"

from time import sleep


def count_auth(dbpath: Path) -> int:
    try:
        if not dbpath.exists():
            return 0
        tmp = tempfile.mktemp(suffix='.sqlite')
        shutil.copy2(dbpath, tmp)
        conn = sqlite3.connect(tmp)
        cur = conn.cursor()
        cur.execute(
            "SELECT COUNT(*) FROM moz_cookies WHERE (host LIKE '%x.com%' OR host LIKE '%twitter.com%') AND name IN ('auth_token','ct0')"
        )
        row = cur.fetchone()
        conn.close()
        try:
            os.remove(tmp)
        except Exception:
            pass
        return int(row[0]) if row else 0
    except Exception as e:
        print('count error', e, file=sys.stderr)
        return 0


if not FF.exists():
    print('Firefox profiles dir not found:', FF)
    raise SystemExit(1)

from classes.Twitter import Twitter

candidates = []
for p in sorted(FF.glob('*')):
    if not p.is_dir():
        continue
    cnt = count_auth(p / 'cookies.sqlite')
    if cnt > 0:
        candidates.append((p, cnt))

if not candidates:
    print('No signed-in profiles found')
    raise SystemExit(0)

print('Found candidate profiles:')
for p, cnt in candidates:
    print(' -', p, 'auth=', cnt)

for p, cnt in candidates:
    print('\nProbing', p)
    try:
        t = Twitter('probe', 'probe', str(p), '', '')
        s = t.check_session()
        print('  check_session reason:', s.get('reason'))
        print('  handle (status[handle]):', s.get('handle'))
        print('  configured_handle:', s.get('configured_handle'))
        try:
            live = t.get_live_account_handle()
            print('  get_live_account_handle():', live)
        except Exception as e:
            print('  live handle error', e)
        t.quit()
    except Exception as e:
        print('  failed to probe', p, e)
