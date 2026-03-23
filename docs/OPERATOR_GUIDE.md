# Quick Reference: Daily Operator Loop

## Objective: Maintain stable, quality Twitter posting from two X/Twitter accounts

### Accounts

| Nickname | Handle | Profile | Status |
|----------|--------|---------|--------|
| niche_launch_1 | @NicheNewton | v3 | ✅ Ready |
| EyeCatcher | @EyeCaughtThat2 | v2 | ✅ Ready |

---

## One-Minute Health Check

```bash
bash scripts/phone_post.sh diag
```

Shows:
- ✅ Firefox profile lock status
- ✅ Cache integrity
- ✅ Recent post success rate
- ✅ Cooldown status for each account

---

## Before Posting

```bash
bash scripts/phone_post.sh session-all
```

Both should show `Ready: YES`.

If any show `profile-in-use`:
```bash
bash scripts/phone_post.sh cleanup
bash scripts/phone_post.sh session-all
```

---

## Post From One Account

**Foreground (see output):**
```bash
bash scripts/phone_post.sh post niche_launch_1
```

**Background (safe to close Termius):**
```bash
bash scripts/phone_post.sh detach niche_launch_1
```

---

## Post From All Accounts

```bash
python3 scripts/run_once.py --headless twitter all
```

---

## After Posting

```bash
bash scripts/phone_post.sh verify-all
```

Check:
- ✅ URLs are correct (matching account handles)
- ✅ Post content appears on live timeline
- ✅ No cross-posting (e.g., @NicheNewton posts appearing under @EyeCaughtThat2)

---

## Troubleshooting

### Session shows "profile-in-use"

```bash
bash scripts/phone_post.sh cleanup
```

This safely removes stale Firefox locks from crashed sessions.

### Posts won't verify

```bash
bash scripts/phone_post.sh verify niche_launch_1
```

If shows `MISS` with wrong handle in URL, post wasn't captured correctly. Safeguards prevent saving wrong URLs.

### Need manual login repair

```bash
bash scripts/phone_post.sh login niche_launch_1
```

Opens Firefox with that account's profile for you to complete X login flow (handles Google SSO, 2FA, etc.).

---

## Key Stats to Monitor

From `diag` output:

1. **Success Rate** - Should be ≥80% for stable operation
2. **Cooldown** - Respects 30-min minimum between posts per account
3. **Stale Locks** - Clean regularly to prevent profile conflicts
4. **Cache Integrity** - Must always show "valid"

---

## Transaction Logs Location

Post attempt history saved in:
```
logs/transaction_log/{nickname}.log
```

Each entry records:
- Timestamp
- Status (success/failed/skipped)
- Reason (if skipped)
- Text snippet
- Tweet URL (if successful)
- Category

**Last 100 entries kept per account.**

---

## System Safeguards Active

✅ **Profile Lock Detection** - Prevents duplicate Firefox windows  
✅ **Cooldown Enforcement** - Blocks posts within 30-minute gap  
✅ **URL Verification** - Only saves posts with matching account URLs  
✅ **Cache Integrity** - Validates structure before use  
✅ **Transaction Logging** - Full audit trail of all attempts  
✅ **Stale Lock Cleanup** - Safely removes crashed process locks  

---

## Emergency Commands

```bash
# See recent manual post logs
ls -t logs/manual_post_* | head -n 3 | xargs tail -n 5

# Check daemon status
bash scripts/phone_post.sh daemon

# View all system stats
bash scripts/phone_post.sh status

# Backup cache
bash scripts/phone_post.sh backup
```

---

## Normal Daily Workflow

1. **Morning check** (1 minute):
   ```bash
   bash scripts/phone_post.sh diag
   ```

2. **Post if ready** (5 minutes):
   ```bash
   bash scripts/phone_post.sh post niche_launch_1
   ```

3. **Post from other account** (5 minutes):
   ```bash
   bash scripts/phone_post.sh post EyeCatcher
   ```

4. **Verify posts** (2 minutes):
   ```bash
   bash scripts/phone_post.sh verify-all
   ```

**Total time: ~13 minutes for stable, verified posting from two accounts.**

---

**Last Updated:** 2026-03-23  
**Safeguards Version:** 1.0  
**Status:** Production Ready
