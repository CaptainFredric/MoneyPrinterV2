"""Helpers for reading lightweight account performance signals from cache."""

from __future__ import annotations

import json
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parent.parent
TWITTER_CACHE_PATH = ROOT_DIR / ".mp" / "twitter.json"


def get_account_cache_metrics(account_nickname: str) -> dict[str, int]:
    """Return basic cached conversion metrics for an account nickname."""
    if not TWITTER_CACHE_PATH.exists():
        return {"verified": 0, "pending": 0, "recent_verified": 0}

    try:
        data = json.loads(TWITTER_CACHE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {"verified": 0, "pending": 0, "recent_verified": 0}

    for account in data.get("accounts", []):
        nickname = str(account.get("nickname", "")).strip()
        if nickname != account_nickname:
            continue

        posts = account.get("posts", []) or []
        verified = sum(1 for post in posts if bool(post.get("post_verified", False)))
        pending = sum(
            1 for post in posts if str(post.get("verification_state", "")).strip().lower() == "pending"
        )
        recent_verified = sum(1 for post in posts[-6:] if bool(post.get("post_verified", False)))
        return {
            "verified": verified,
            "pending": pending,
            "recent_verified": recent_verified,
        }

    return {"verified": 0, "pending": 0, "recent_verified": 0}


def recovery_mode_decision(account_nickname: str, cycle_index: int, post_every_cycles: int = 3) -> dict[str, object]:
    """Return whether an account should use verify/backfill-first recovery mode.

    Recovery mode is intentionally light:
    - only triggers for accounts with no verified wins and a meaningful pending backlog
    - still allows a full post attempt every N cycles to avoid fully freezing the account
    """
    metrics = get_account_cache_metrics(account_nickname)
    verified = int(metrics.get("verified", 0) or 0)
    pending = int(metrics.get("pending", 0) or 0)
    recent_verified = int(metrics.get("recent_verified", 0) or 0)

    underperforming = verified == 0 and pending >= 5 and recent_verified == 0
    cadence = max(1, int(post_every_cycles or 1))
    allow_post_this_cycle = (cycle_index % cadence) == 0
    use_recovery_mode = underperforming and not allow_post_this_cycle

    return {
        "use_recovery_mode": use_recovery_mode,
        "allow_post_this_cycle": allow_post_this_cycle,
        "verified": verified,
        "pending": pending,
        "recent_verified": recent_verified,
        "post_every_cycles": cadence,
    }