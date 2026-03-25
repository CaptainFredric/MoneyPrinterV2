#!/usr/bin/env python3
"""Summarize the finite Twitter bot readiness gates in one place."""

from __future__ import annotations

import json
import sys
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parent.parent
SRC_DIR = ROOT_DIR / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from account_performance import get_account_cache_metrics
from twitter_session_backup import count_auth_cookies


CACHE_PATH = ROOT_DIR / ".mp" / "twitter.json"


def _load_accounts() -> list[dict]:
    if not CACHE_PATH.exists():
        return []
    try:
        with open(CACHE_PATH, "r", encoding="utf-8") as file:
            data = json.load(file)
        return data.get("accounts", [])
    except Exception:
        return []


def _infer_pending_likelihood(post: dict) -> str:
    explicit = str(post.get("publish_likelihood", "")).strip()
    if explicit and explicit != "pending-unclassified":
        return explicit

    signals = post.get("confidence_signals") or {}
    compose_candidates = int(signals.get("compose_candidates", 0) or 0)
    compose_matching_candidates = int(signals.get("compose_matching_candidates", 0) or 0)
    timeline_items = int(signals.get("timeline_items", 0) or 0)

    if compose_matching_candidates > 0:
        return "published-likely"
    if compose_candidates >= 3 and timeline_items >= 3:
        return "published-likely"
    if compose_candidates > 0 or timeline_items > 0:
        return "published-ambiguous"
    return "pending-unclassified"


def main() -> None:
    accounts = _load_accounts()
    if not accounts:
        print("No Twitter accounts found in cache.")
        return

    gate_session = True
    gate_primary_verified = False
    gate_pending_recovery = False
    gate_secondary_recovered = False

    print("=" * 72)
    print("Twitter Bot Readiness Report")
    print("=" * 72)

    for account in accounts:
        nickname = str(account.get("nickname", "unknown"))
        posts = account.get("posts", []) or []
        metrics = get_account_cache_metrics(nickname)
        profile_path = Path(str(account.get("firefox_profile", "")).strip())
        auth_count = count_auth_cookies(profile_path) if profile_path.exists() else 0
        pending_posts = [p for p in posts if str(p.get("verification_state", "")).strip().lower() == "pending"]
        likely_pending = sum(1 for p in pending_posts if _infer_pending_likelihood(p) == "published-likely")
        ambiguous_pending = sum(1 for p in pending_posts if _infer_pending_likelihood(p) == "published-ambiguous")

        session_ok = auth_count >= 1
        gate_session = gate_session and session_ok

        if nickname == "niche_launch_1" and metrics.get("verified", 0) >= 1:
            gate_primary_verified = True
        if nickname == "niche_launch_1" and likely_pending == 0 and ambiguous_pending == 0:
            gate_pending_recovery = True
        if nickname == "EyeCatcher" and metrics.get("verified", 0) >= 1:
            gate_secondary_recovered = True

        print(f"Account : {nickname}")
        print(f"- Session cookies      : {auth_count}")
        print(f"- Verified posts       : {metrics.get('verified', 0)}")
        print(f"- Pending posts        : {metrics.get('pending', 0)}")
        print(f"- Published-likely     : {likely_pending}")
        print(f"- Published-ambiguous  : {ambiguous_pending}")
        print("- Status               : " + ("ready-enough" if session_ok else "session-fail"))
        print("-" * 72)

    print("Finite Gates")
    print("------------")
    print(f"- Session health stable      : {'PASS' if gate_session else 'FAIL'}")
    print(f"- Primary account proven     : {'PASS' if gate_primary_verified else 'FAIL'}")
    print(f"- Pending recovery backlog   : {'PASS' if gate_pending_recovery else 'FAIL'}")
    print(f"- Secondary account proven   : {'PASS' if gate_secondary_recovered else 'FAIL'}")

    overall = gate_session and gate_primary_verified and gate_pending_recovery
    print(f"- Fine for now overall       : {'YES' if overall else 'NO'}")


if __name__ == "__main__":
    main()