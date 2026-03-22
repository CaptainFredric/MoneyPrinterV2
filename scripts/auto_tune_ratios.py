#!/usr/bin/env python3
"""
scripts/auto_tune_ratios.py

Phase-aware auto tuning for account ratio settings in `.mp/twitter.json`.

What it does:
  - Reads `scripts/performance_report.py` JSON output
  - Adjusts `link_post_ratio`, `media_post_ratio`, `citation_post_ratio`
  - Uses small bounded changes to avoid oscillation
  - Applies a cooldown per account to prevent over-tuning

Usage:
  python scripts/auto_tune_ratios.py --dry-run
  python scripts/auto_tune_ratios.py --apply
    python scripts/auto_tune_ratios.py --apply --no-phase-lock
"""

import argparse
import json
from datetime import datetime, timedelta
from pathlib import Path

from performance_report import build_performance_report


ROOT_DIR = Path(__file__).resolve().parent.parent
TWITTER_CACHE = ROOT_DIR / ".mp" / "twitter.json"
TUNING_STATE = ROOT_DIR / ".mp" / "ratio_tuning_state.json"


RATIO_KEYS = ("link_post_ratio", "media_post_ratio", "citation_post_ratio")
RATIO_BOUNDS = {
    "link_post_ratio": (0.05, 0.45),
    "media_post_ratio": (0.05, 0.35),
    "citation_post_ratio": (0.05, 0.35),
}

DEFAULTS = {
    "link_post_ratio": 0.20,
    "media_post_ratio": 0.12,
    "citation_post_ratio": 0.15,
}


def _load_json(path: Path, fallback: dict) -> dict:
    try:
        with open(path, "r", encoding="utf-8") as f:
            parsed = json.load(f)
        if isinstance(parsed, dict):
            return parsed
    except Exception:
        pass
    return fallback


def _save_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = Path(str(path) + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=4)
        f.write("\n")
    tmp.replace(path)


def _clamp(value: float, lower: float, upper: float) -> float:
    return max(lower, min(upper, value))


def _phase_index(phase: str) -> int:
    mapping = {
        "Phase 1: Baseline Quality": 1,
        "Phase 2: Format Mix": 2,
        "Phase 3: Credibility & Novelty": 3,
        "Phase 4: Scale": 4,
    }
    return mapping.get(phase, 1)


def _delta_for_phase(phase: str) -> float:
    idx = _phase_index(phase)
    if idx == 1:
        return 0.01
    if idx == 2:
        return 0.02
    if idx == 3:
        return 0.015
    return 0.01


def _target_ranges_for_phase(phase: str) -> dict:
    idx = _phase_index(phase)
    if idx == 1:
        return {
            "link_post_ratio": (0.10, 0.25),
            "media_post_ratio": (0.08, 0.18),
            "citation_post_ratio": (0.08, 0.18),
        }
    if idx == 2:
        return {
            "link_post_ratio": (0.12, 0.30),
            "media_post_ratio": (0.10, 0.22),
            "citation_post_ratio": (0.08, 0.20),
        }
    if idx == 3:
        return {
            "link_post_ratio": (0.10, 0.28),
            "media_post_ratio": (0.10, 0.24),
            "citation_post_ratio": (0.12, 0.28),
        }
    return {
        "link_post_ratio": (0.12, 0.30),
        "media_post_ratio": (0.12, 0.25),
        "citation_post_ratio": (0.10, 0.22),
    }


def _cooldown_ready(account_id: str, state: dict, min_hours: int = 12) -> bool:
    last = state.get("accounts", {}).get(account_id, {}).get("last_tuned_at", "")
    if not last:
        return True
    try:
        last_dt = datetime.fromisoformat(last)
    except Exception:
        return True
    return datetime.now() - last_dt >= timedelta(hours=min_hours)


def _apply_phase_lock(
    phase: str,
    current: dict,
    updated: dict,
    recent: dict,
    notes: list[str],
) -> dict:
    """
    Enforces single-objective ratio changes based on current phase.

    Phase policy:
      - Phase 1: baseline quality → do not increase ratios
      - Phase 2: format mix       → tune link/media only
      - Phase 3: credibility      → tune citation only
      - Phase 4: scale            → allow all
    """
    idx = _phase_index(phase)

    if idx == 1:
        for key in RATIO_KEYS:
            updated[key] = min(updated[key], current[key])
        notes.append("phase-lock: Phase 1 (no ratio increases)")
        return updated

    if idx == 2:
        updated["citation_post_ratio"] = current["citation_post_ratio"]
        notes.append("phase-lock: Phase 2 (freeze citation ratio)")
        return updated

    if idx == 3:
        updated["link_post_ratio"] = current["link_post_ratio"]
        updated["media_post_ratio"] = current["media_post_ratio"]
        notes.append("phase-lock: Phase 3 (tune citation only)")
        return updated

    notes.append("phase-lock: Phase 4 (all ratios eligible)")
    return updated


def _propose_updates_for_account(account: dict, summary: dict, phase_lock: bool = True) -> tuple[dict, list[str]]:
    phase = summary.get("phase", "Phase 1: Baseline Quality")
    recent = summary.get("recent", {})
    delta = _delta_for_phase(phase)
    ranges = _target_ranges_for_phase(phase)

    current = {
        key: float(account.get(key, DEFAULTS[key]))
        for key in RATIO_KEYS
    }
    updated = dict(current)
    notes: list[str] = []

    # Link ratio adjustment
    link_ratio_observed = float(recent.get("link_ratio", 0.0))
    lo, hi = ranges["link_post_ratio"]
    if link_ratio_observed < lo:
        updated["link_post_ratio"] += delta
        notes.append("increase link ratio")
    elif link_ratio_observed > hi:
        updated["link_post_ratio"] -= delta
        notes.append("decrease link ratio")

    # Media ratio adjustment
    media_ratio_observed = float(recent.get("media_ratio", 0.0))
    lo, hi = ranges["media_post_ratio"]
    if media_ratio_observed < lo:
        updated["media_post_ratio"] += delta
        notes.append("increase media ratio")
    elif media_ratio_observed > hi:
        updated["media_post_ratio"] -= delta
        notes.append("decrease media ratio")

    # Citation ratio adjustment
    cite_ratio_observed = float(recent.get("citation_ratio", 0.0))
    lo, hi = ranges["citation_post_ratio"]
    if cite_ratio_observed < lo:
        updated["citation_post_ratio"] += delta
        notes.append("increase citation ratio")
    elif cite_ratio_observed > hi:
        updated["citation_post_ratio"] -= delta
        notes.append("decrease citation ratio")

    # Stability safeguard: if hooks are weak, avoid aggressive format increases.
    hook_rate = float(recent.get("hook_rate", 0.0))
    if hook_rate < 0.70:
        updated["link_post_ratio"] = min(updated["link_post_ratio"], current["link_post_ratio"])
        updated["media_post_ratio"] = min(updated["media_post_ratio"], current["media_post_ratio"])
        notes.append("hook safeguard: hold link/media growth")

    if phase_lock:
        updated = _apply_phase_lock(phase, current, updated, recent, notes)

    # Clamp to global hard bounds.
    for key, value in updated.items():
        lower, upper = RATIO_BOUNDS[key]
        updated[key] = round(_clamp(value, lower, upper), 3)

    return updated, notes


def main() -> None:
    parser = argparse.ArgumentParser(description="Auto tune posting ratios by phase")
    parser.add_argument("--apply", action="store_true", help="Write changes to .mp/twitter.json")
    parser.add_argument("--dry-run", action="store_true", help="Preview changes only")
    parser.add_argument("--no-phase-lock", action="store_true", help="Allow cross-phase ratio changes")
    args = parser.parse_args()

    do_apply = args.apply and not args.dry_run
    phase_lock = not args.no_phase_lock

    cache = _load_json(TWITTER_CACHE, {"accounts": []})
    state = _load_json(TUNING_STATE, {"accounts": {}})
    perf = build_performance_report()
    summaries = {entry.get("nickname"): entry for entry in perf.get("accounts", [])}

    print("=" * 72)
    print(f"Auto Tuning Ratios — {datetime.now().isoformat(timespec='seconds')}")
    print(f"Mode: {'APPLY' if do_apply else 'DRY-RUN'}")
    print("=" * 72)

    changed = 0
    for account in cache.get("accounts", []):
        nickname = account.get("nickname", "?")
        account_id = account.get("id", "")
        summary = summaries.get(nickname)
        if not summary:
            print(f"- {nickname}: skipped (no performance summary)")
            continue

        if not _cooldown_ready(account_id, state):
            print(f"- {nickname}: skipped (tuning cooldown active)")
            continue

        proposed, notes = _propose_updates_for_account(account, summary, phase_lock=phase_lock)

        before = {key: round(float(account.get(key, DEFAULTS[key])), 3) for key in RATIO_KEYS}
        after = proposed

        if before == after:
            print(f"- {nickname}: no change needed")
            continue

        print(f"- {nickname}: {before} -> {after}")
        if notes:
            print(f"  notes: {', '.join(notes)}")

        if do_apply:
            for key in RATIO_KEYS:
                account[key] = after[key]
            state.setdefault("accounts", {}).setdefault(account_id, {})["last_tuned_at"] = datetime.now().isoformat(timespec="seconds")
            state["accounts"][account_id]["phase_at_tune"] = summary.get("phase", "")
        changed += 1

    if do_apply and changed:
        _save_json(TWITTER_CACHE, cache)
        _save_json(TUNING_STATE, state)
        print(f"\n✅ Applied tuning updates for {changed} account(s).")
    elif do_apply:
        print("\nℹ️ No ratio changes applied.")
    else:
        print(f"\nℹ️ Dry-run complete. Proposed changes for {changed} account(s).")
        print("   Run with --apply to persist updates.")


if __name__ == "__main__":
    main()
