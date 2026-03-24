#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

VENV_PYTHON="$ROOT_DIR/venv/bin/python"
ACCOUNT="${1:-niche_launch_1}"
VERIFY_LIMIT="${MPV2_VERIFY_LIMIT:-5}"
SMART_TIMEOUT="${MPV2_SMART_TIMEOUT_SECONDS:-900}"

if [[ ! -x "$VENV_PYTHON" ]]; then
  echo "Missing python env at $VENV_PYTHON"
  exit 1
fi

echo "=== Mission Advance: $ACCOUNT ==="
echo "Time: $(date '+%Y-%m-%d %H:%M:%S')"

echo
echo "[1/5] Session readiness (passive)"
bash scripts/phone_post.sh session "$ACCOUNT" || true

echo
echo "[2/5] Cookie auth proof"
"$VENV_PYTHON" - "$ACCOUNT" <<'PY'
import json, sqlite3, tempfile, shutil
from pathlib import Path
import sys

root = Path('.')
account = sys.argv[1] if len(sys.argv) > 1 else 'niche_launch_1'
cache = json.loads((root / '.mp/twitter.json').read_text())
match = next((a for a in cache.get('accounts', []) if a.get('nickname') == account or a.get('id') == account), None)
if not match:
    print(f'account_not_found={account}')
    raise SystemExit(0)
prof = Path(match.get('firefox_profile', ''))
print(f'profile={prof}')
if not prof.exists():
    print('profile_exists=no')
    raise SystemExit(0)
db = prof / 'cookies.sqlite'
if not db.exists():
    print('cookies_db=missing')
    raise SystemExit(0)
with tempfile.NamedTemporaryFile(suffix='.sqlite', delete=False) as f:
    tmp = Path(f.name)
shutil.copy2(db, tmp)
con = sqlite3.connect(tmp)
cur = con.cursor()
cur.execute("select count(*) from moz_cookies where (host like '%x.com%' or host like '%twitter.com%') and name in ('auth_token','ct0')")
print(f"auth_cookie_count={cur.fetchone()[0]}")
con.close()
tmp.unlink(missing_ok=True)
PY

echo
echo "[3/5] Controlled post attempt"
MPV2_SMART_TIMEOUT_SECONDS="$SMART_TIMEOUT" "$VENV_PYTHON" scripts/smart_post_twitter.py --headless --allow-no-post --only-account "$ACCOUNT" || true

echo
echo "[4/5] Phase 3 verify"
"$VENV_PYTHON" scripts/verify_twitter_posts_phase3.py "$ACCOUNT" --limit "$VERIFY_LIMIT" || true

echo
echo "[5/5] Backfill pending"
"$VENV_PYTHON" scripts/backfill_pending_twitter.py "$ACCOUNT" --headless --limit "$VERIFY_LIMIT" || true

echo
echo "[cleanup] Keep disk usage lean"
find logs -type f -name 'session_watch_*.log' -delete 2>/dev/null || true
find logs -type f -name 'manual_post_*.log' -mtime +14 -delete 2>/dev/null || true

echo
echo "=== Mission Advance Complete: $ACCOUNT ==="
