#!/usr/bin/env python3
"""
MoneyPrinterV2 operational stats report.

Provides:
- Idle runner status and latest cycle metrics
- Per-account cache and transaction health summary
"""

from __future__ import annotations

import json
import os
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parent.parent
CACHE_PATH = ROOT_DIR / ".mp" / "twitter.json"
RUNTIME_DIR = ROOT_DIR / ".mp" / "runtime"
PID_FILE = RUNTIME_DIR / "money_idle.pid"
STATE_FILE = RUNTIME_DIR / "money_idle_state.json"
LOG_DIR = ROOT_DIR / "logs" / "transaction_log"

STRUCTURAL_REASONS = {
    "profile-posts-unavailable",
    "x-error-page",
    "login-required",
    "handle-mismatch",
    "handle-unresolved",
    "profile-in-use",
}


def _load_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _parse_iso(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts)
    except Exception:
        return None


def _is_idle_running() -> tuple[bool, str]:
    if not PID_FILE.exists():
        return False, ""

    pid_text = (PID_FILE.read_text(encoding="utf-8") or "").strip()
    if not pid_text.isdigit():
        return False, ""

    pid = int(pid_text)
    try:
        os.kill(pid, 0)
        return True, str(pid)
    except Exception:
        return False, str(pid)


def _fmt_time(value: str | None) -> str:
    if not value:
        return "-"
    dt_value = _parse_iso(value)
    if dt_value is None:
        return value
    return dt_value.isoformat(timespec="seconds")


def _load_account_logs(nickname: str) -> list[dict]:
    path = LOG_DIR / f"{nickname}.log"
    raw = _load_json(path)
    return raw if isinstance(raw, list) else []


def _health_label(entries: list[dict], now: datetime) -> str:
    if not entries:
        return "unknown:no-history"

    last = entries[-1]
    last_status = str(last.get("status", "")).strip().lower()
    last_reason = str(last.get("reason", "")).strip()

    if last_status == "success":
        return "working"

    six_hours_ago = now - timedelta(hours=6)
    structural_hit = False
    pending_recent = False
    for item in reversed(entries[-40:]):
        ts = _parse_iso(str(item.get("timestamp", "")))
        if ts and ts < six_hours_ago:
            break
        reason = str(item.get("reason", "")).strip()
        status = str(item.get("status", "")).strip().lower()
        if reason in STRUCTURAL_REASONS:
            structural_hit = True
        if status == "pending":
            pending_recent = True

    if structural_hit:
        return "blocked:structural"
    if last_status == "failed":
        if last_reason == "unverified" or pending_recent:
            return "degraded:unverified"
        return f"degraded:{last_reason or 'failed'}"
    if last_status == "pending":
        return "degraded:pending-verification"
    if last_status == "skipped":
        if last_reason.startswith("cooldown:"):
            return "working:cooldown"
        if last_reason.startswith("quarantine:"):
            return "blocked:quarantine"
        return "degraded:skipped"
    return "unknown"


def main() -> None:
    now = datetime.now()
    cache_data = _load_json(CACHE_PATH)
    accounts = cache_data.get("accounts", []) if isinstance(cache_data, dict) else []

    idle_running, idle_pid = _is_idle_running()
    state_data = _load_json(STATE_FILE)

    print("=" * 72)
    print("MoneyPrinterV2 Stats")
    print("=" * 72)
    print(f"Generated at    : {now.isoformat(timespec='seconds')}")
    print(f"Idle running    : {'yes' if idle_running else 'no'}" + (f" (pid {idle_pid})" if idle_pid else ""))

    if isinstance(state_data, dict) and state_data:
        print(f"Idle cycle      : {state_data.get('cycle', '-')}")
        print(f"Idle status     : {state_data.get('status', '-')}")
        print(f"Idle started    : {_fmt_time(state_data.get('started_at'))}")
        print(f"Idle finished   : {_fmt_time(state_data.get('finished_at') or state_data.get('stopped_at'))}")
        print(f"Idle posted     : {state_data.get('posted_count', '-')}")
        print(f"Idle cooldown   : {state_data.get('cooldown_minutes_detected', '-')}m")
        print(f"Idle next sleep : {state_data.get('next_sleep_minutes', '-')}m")

    print("=" * 72)
    print(f"Accounts loaded : {len(accounts)}")
    print("=" * 72)

    twenty_four_hours_ago = now - timedelta(hours=24)
    for account in accounts:
        nickname = account.get("nickname", account.get("id", "unknown"))
        handle = account.get("x_username", "")
        posts = account.get("posts", []) or []

        verified = sum(1 for item in posts if item.get("post_verified") is True)
        pending = sum(1 for item in posts if str(item.get("verification_state", "")).strip() == "pending")
        with_url = sum(1 for item in posts if str(item.get("tweet_url", "")).strip())

        confidence_scores: list[int] = []
        for item in posts:
            raw = item.get("confidence_score", None)
            if raw is not None:
                try:
                    confidence_scores.append(int(raw))
                    continue
                except Exception:
                    pass

            if item.get("post_verified") is True:
                confidence_scores.append(100)
            elif str(item.get("tweet_url", "")).strip():
                confidence_scores.append(85)
            elif str(item.get("verification_state", "")).strip().lower() == "pending":
                confidence_scores.append(35)

        avg_conf = round(sum(confidence_scores) / len(confidence_scores), 1) if confidence_scores else "-"
        high_conf = sum(1 for score in confidence_scores if score >= 80)
        verified_conf = sum(1 for score in confidence_scores if score >= 100)

        entries = _load_account_logs(nickname)
        recent_entries = []
        for item in entries:
            ts = _parse_iso(str(item.get("timestamp", "")))
            if ts and ts >= twenty_four_hours_ago:
                recent_entries.append(item)

        status_counts = Counter(str(item.get("status", "unknown")).strip().lower() for item in recent_entries)
        health = _health_label(entries, now)
        last = entries[-1] if entries else {}
        last_ts = _fmt_time(last.get("timestamp"))
        last_status = str(last.get("status", "-"))
        last_reason = str(last.get("reason", "")).strip() or "-"

        print(f"Account         : {nickname}")
        print(f"Handle          : @{handle}" if handle else "Handle          : -")
        print(f"Health          : {health}")
        print(f"Cache posts     : total={len(posts)} verified={verified} pending={pending} with_url={with_url}")
        print(
            "Confidence     : "
            f"avg={avg_conf} "
            f"high(>=80)={high_conf} "
            f"verified(100)={verified_conf}"
        )
        print(
            "Tx (24h)       : "
            f"attempts={len(recent_entries)} "
            f"success={status_counts.get('success', 0)} "
            f"pending={status_counts.get('pending', 0)} "
            f"skipped={status_counts.get('skipped', 0)} "
            f"failed={status_counts.get('failed', 0)}"
        )
        print(f"Last attempt    : {last_ts} | status={last_status} | reason={last_reason}")
        print("-" * 72)


if __name__ == "__main__":
    main()
