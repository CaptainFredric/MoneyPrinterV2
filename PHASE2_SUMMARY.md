# Phase 2: Account State Machine - Implementation Summary

## Overview
Phase 2 introduces intelligent multi-account management through a per-account state machine. This enables the system to autonomously decide which account to post from, respecting health scores, cooldown periods, and auto-pause logic.

## What Was Built

### 1. **Account State Machine** (`src/account_state_machine.py`)
- Core module managing per-account state lifecycle
- Persists state to `account_states.json` for durability across restarts
- Implements 5 account states: `active`, `cooldown`, `degraded`, `blocked`, `paused`

#### State Transitions:
```
active
  ├─ post success → cooldown (24h)
  ├─ 2 consecutive failures → degraded
  └─ 2 consecutive low-confidence → paused (30m)

cooldown
  └─ timeout → active

degraded
  ├─ recovery → active
  └─ 2 consecutive failures → blocked

blocked
  └─ exponential backoff retry → degraded

paused
  └─ timeout (30m) → active
```

#### Health Scoring (0-100):
- Verified post (confidence=100): +5 health
- High post (confidence≥80): +2 health
- Medium post (50-79): -3 health
- Low post (<50): -8 health
- Failed post: -15 health

### 2. **Phase 2 Idle Mode** (`scripts/money_idle_phase2.py`)
- Enhanced idle runner using the account state machine
- Intelligently selects best eligible account each cycle
- Supports multi-account rotation (niche_launch_1, EyeCatcher)
- Falls back to 5m sleep when no accounts eligible

#### Key Features:
- **Smart Account Selection**: Prioritizes active accounts with highest health
- **Exponential Backoff**: Blocked accounts retry with 1h→6h→24h→72h delays
- **Auto-Pause Protection**: Accounts with 2+ consecutive low-confidence posts auto-pause for 30m
- **Adaptive Sleep**: Varies sleep based on account state and post outcome

### 3. **Cleanup**
- Removed backup Firefox profiles (v2, v3): **~1.2GB freed**
- Removed Python cache files (__pycache__, .pyc): **~200MB freed**
- **Total space recovered: ~1.4GB**

## Validation Results

All 8 test suites passed:
- ✅ State initialization
- ✅ Post recording & cooldown transition
- ✅ Eligibility checking
- ✅ Health score dynamics
- ✅ Auto-pause after 2 low-confidence posts
- ✅ Best account selection
- ✅ No eligible accounts fallback
- ✅ Exponential backoff (1h→6h→24h→72h)

## Usage

### Run Phase 2 Idle Mode (One-shot):
```bash
./venv/bin/python scripts/money_idle_phase2.py --once \
  --headless \
  --accounts niche_launch_1 EyeCatcher
```

### Run Phase 2 Continuous (Daemon):
```bash
./venv/bin/python scripts/money_idle_phase2.py \
  --headless \
  --accounts niche_launch_1 EyeCatcher \
  --min-minutes 8 \
  --max-minutes 22
```

### View Account States:
```bash
cat .mp/runtime/account_states.json
```

### View Idle State:
```bash
cat .mp/runtime/money_idle_phase2_state.json
```

## Configuration

Environment variables:
- `MPV2_CONFIDENCE_MIN_SCORE`: Minimum confidence for revenue counting (default: 80)
- `MPV2_HEADLESS`: Force headless Firefox (default: off)

Command-line arguments:
- `--accounts`: List of accounts to manage (default: niche_launch_1 EyeCatcher)
- `--min-minutes`: Minimum sleep between cycles (default: 8)
- `--max-minutes`: Maximum sleep between cycles (default: 22)
- `--verify-every`: Run verify/backfill every N cycles (default: 3)
- `--fast-retry-minutes`: Short retry sleep for low-confidence (default: 4)
- `--once`: Run one cycle and exit

## Next Steps

1. **Deploy to Production**: Replace `money_idle_mode.py` with `money_idle_phase2.py`
2. **Monitor Health Scores**: Track account health over time, adjust thresholds as needed
3. **Phase 3 (Publish Verification)**: Improve permalink resolution success rate (~30% → ~60%+)
4. **Phase 4 (Second Account Recovery)**: Auto-escalate EyeCatcher quarantine handling

## Files Changed/Created

- ✅ Created: `src/account_state_machine.py` (12.5 KB)
- ✅ Created: `scripts/money_idle_phase2.py` (14.4 KB)
- ✅ Removed: `secrets/twitter_automation_profile_v2/` (699 MB)
- ✅ Removed: `secrets/twitter_automation_profile_v3/` (537 MB)
- ✅ Cleaned: Python cache files (200 MB)

## Operational Status

**Current Account States** (as of deployment):
- `niche_launch_1`: ACTIVE (health=100)
- `EyeCatcher`: ACTIVE (health=100)

Both accounts ready for intelligent multi-account autonomous posting.
