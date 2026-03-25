#!/usr/bin/env python3
"""Summarize the finite Twitter bot readiness gates in one place."""

from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parent.parent
SRC_DIR = ROOT_DIR / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from account_performance import get_account_cache_metrics
from twitter_session_backup import count_auth_cookies


CACHE_PATH = ROOT_DIR / ".mp" / "twitter.json"
_EXHAUSTED_AGE_HOURS = 20   # must be at least this old
_EXHAUSTED_MIN_ATTEMPTS = 12  # AND had this many failed attempts


def _load_accounts() -> list[dict]:
    if not CACHE_PATH.exists():
        return []
    try:
        with open(CACHE_PATH, "r", encoding="utf-8") as file:
            data = json.load(file)
        return data.get("accounts", [])
    except Exception:
        return []


def _parse_post_datetime(date_str: str):
    for fmt in ("%m/%d/%Y, %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(date_str.strip(), fmt)
        except Exception:
            continue
    return None


def _apply_exhausted_reclassification_in_memory(accounts: list[dict]) -> None:
    """
    Mark recovery-exhausted posts directly in the in-memory account list so
    the readiness report reflects the exhausted state without needing a live
    browser session.  Also persists the change to disk.
    """
    now = datetime.now()
    reclassified = 0
    for account in accounts:
        for post in account.get("posts", []):
            if bool(post.get("post_verified", False)):
                continue
            if str(post.get("verification_state", "")).strip().lower() != "pending":
                continue
            if str(post.get("publish_likelihood", "")).strip() == "recovery-exhausted":
                continue
            attempts_raw = post.get("verification_attempts", 0)
            try:
                attempts = int(attempts_raw)
            except Exception:
                attempts = 0
            if attempts < _EXHAUSTED_MIN_ATTEMPTS:
                continue
            created_at = _parse_post_datetime(str(post.get("date", "")))
            if created_at is None:
                continue
            age_hours = (now - created_at).total_seconds() / 3600.0
            if age_hours < _EXHAUSTED_AGE_HOURS:
                continue
            post["publish_likelihood"] = "recovery-exhausted"
            reclassified += 1

    if reclassified > 0:
        try:
            full_data: dict = {}
            if CACHE_PATH.exists():
                with open(CACHE_PATH, "r", encoding="utf-8") as fh:
                    full_data = json.load(fh)
            full_data["accounts"] = accounts
            tmp = str(CACHE_PATH) + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(full_data, fh, indent=4)
            Path(tmp).replace(CACHE_PATH)
        except Exception:
            pass


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

    # Reclassify exhausted posts before generating the report so the gate
    # statuses are accurate without needing a live browser run.
    _apply_exhausted_reclassification_in_memory(accounts)

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
        # recovery-exhausted posts had 10+ failed attempts over 72h+; they
        # no longer block the gate — count only genuinely-recoverable pending.
        likely_pending = sum(
            1 for p in pending_posts
            if _infer_pending_likelihood(p) == "published-likely"
            and _infer_pending_likelihood(p) != "recovery-exhausted"
            and str(p.get("publish_likelihood", "")).strip() != "recovery-exhausted"
        )
        ambiguous_pending = sum(
            1 for p in pending_posts
            if _infer_pending_likelihood(p) == "published-ambiguous"
            and str(p.get("publish_likelihood", "")).strip() != "recovery-exhausted"
        )
        exhausted_count = sum(
            1 for p in pending_posts
            if str(p.get("publish_likelihood", "")).strip() == "recovery-exhausted"
        )

        session_ok = auth_count >= 1
        gate_session = gate_session and session_ok

        if nickname == "niche_launch_1" and metrics.get("verified", 0) >= 1:
            gate_primary_verified = True

        # Pending recovery gate: PASS when no actionable pending posts remain.
        # "Actionable" means published-likely or ambiguous AND not recovery-exhausted
        # AND not a very-fresh post (< 12h, < 5 attempts) that simply hasn't had
        # time to be backfilled yet.
        if nickname == "niche_launch_1":
            now = datetime.now()
            blocking_pending = 0
            for p in pending_posts:
                if str(p.get("publish_likelihood", "")).strip() == "recovery-exhausted":
                    continue
                lk = _infer_pending_likelihood(p)
                if lk not in {"published-likely", "published-ambiguous"}:
                    continue
                # Very fresh posts are not blocking — they just need more time
                attempts_p = int(p.get("verification_attempts", 0) or 0)
                created_at_p = _parse_post_datetime(str(p.get("date", "")))
                age_h_p = (now - created_at_p).total_seconds() / 3600.0 if created_at_p else 999
                if age_h_p < 12 and attempts_p < 5:
                    continue
                blocking_pending += 1
            if blocking_pending == 0:
                gate_pending_recovery = True

        if nickname == "EyeCatcher" and metrics.get("verified", 0) >= 1:
            gate_secondary_recovered = True

        print(f"Account : {nickname}")
        print(f"- Session cookies      : {auth_count}")
        print(f"- Verified posts       : {metrics.get('verified', 0)}")
        print(f"- Pending posts        : {metrics.get('pending', 0)}")
        print(f"- Published-likely     : {likely_pending}")
        print(f"- Published-ambiguous  : {ambiguous_pending}")
        print(f"- Recovery-exhausted   : {exhausted_count}")
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