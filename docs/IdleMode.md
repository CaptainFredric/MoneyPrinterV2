# Autonomous Idle Mode

`Idle Mode` runs MoneyPrinterV2 continuously with variable timing and minimal babysitting.

## What It Does Per Cycle

1. Smart post on primary account (`niche_launch_1` by default)
2. Verify primary account recent posts
3. Backfill primary pending posts
4. Uses adaptive delay:
   - Random window (`min..max` minutes)
   - Auto-extends when cooldown is detected

## Commands

```bash
bash scripts/phone_post.sh idle-start
bash scripts/phone_post.sh idle-status
bash scripts/phone_post.sh idle-stop
bash scripts/phone_post.sh stats
```

## Environment Controls

```bash
export MPV2_PRIMARY_ACCOUNT=niche_launch_1
export MPV2_IDLE_MIN_MINUTES=8
export MPV2_IDLE_MAX_MINUTES=22
```

Then:

```bash
bash scripts/phone_post.sh idle-start
```

## Operational Notes

- Logs: `logs/idle_mode.log`
- Runtime files:
  - `.mp/runtime/money_idle.pid`
  - `.mp/runtime/money_idle_state.json`
  - `.mp/runtime/money_idle.stop`
- Structural account issues (like `profile-posts-unavailable`) are skipped to avoid wasting cycles.
- Cooldown-only cycles are treated as healthy no-post cycles, not hard failures.

## One-Shot Test

```bash
./venv/bin/python scripts/money_idle_mode.py --once --headless --primary-account niche_launch_1 --min-minutes 1 --max-minutes 1
```
