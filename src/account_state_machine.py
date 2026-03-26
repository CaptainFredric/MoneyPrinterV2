"""
Account State Machine for MoneyPrinterV2.

Purpose:
- Track per-account state: active, cooldown, blocked, degraded, paused
- Implement exponential backoff for blocked accounts
- Auto-pause accounts after 2+ consecutive low-confidence posts
- Recommend best account for next posting attempt

State transitions:
- active → cooldown (after post, retry cooldown)
- active → degraded (persistent errors, structure block)
- degraded → blocked (2+ consecutive failures)
- blocked → degraded (after timeout window, auto-retry)
- active/degraded → paused (2+ consecutive low-confidence, 30m pause)
- paused → active (after pause window expires)

Per-account state record:
{
    "account": "niche_launch_1",
    "state": "active|cooldown|blocked|degraded|paused",
    "state_entered_at": "2026-03-23T18:00:00",
    "consecutive_low_confidence": 0,
    "consecutive_failures": 0,
    "cooldown_expires_at": "2026-03-23T18:30:00",
    "pause_expires_at": null,
    "blocked_retry_count": 0,
    "blocked_retry_expires_at": null,
    "last_post_at": "2026-03-23T17:30:00",
    "last_post_status": "posted:confidence=42:level=low",
    "health_score": 85
}
"""

import json
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

SRC_DIR = Path(__file__).resolve().parent
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from account_performance import get_account_cache_metrics


class AccountStateMachine:
    """Manages per-account state and transitions."""

    def __init__(self, state_file: Path):
        self.state_file = state_file
        self.accounts: dict[str, dict] = {}
        self.load()

    def load(self) -> None:
        """Load account states from disk."""
        if self.state_file.exists():
            try:
                data = json.loads(self.state_file.read_text(encoding="utf-8"))
                if isinstance(data, dict) and "accounts" in data:
                    self.accounts = data["accounts"]
            except Exception:
                self.accounts = {}

    def save(self) -> None:
        """Persist account states to disk."""
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        temp_file = self.state_file.with_suffix(".tmp")
        temp_file.write_text(
            json.dumps({"accounts": self.accounts, "saved_at": datetime.now().isoformat()}, indent=2),
            encoding="utf-8",
        )
        temp_file.replace(self.state_file)

    def init_account(self, account: str) -> None:
        """Initialize account state if not present."""
        if account not in self.accounts:
            self.accounts[account] = {
                "account": account,
                "state": "active",
                "state_entered_at": datetime.now().isoformat(timespec="seconds"),
                "consecutive_low_confidence": 0,
                "consecutive_failures": 0,
                "cooldown_expires_at": None,
                "pause_expires_at": None,
                "blocked_retry_count": 0,
                "blocked_retry_expires_at": None,
                "last_post_at": None,
                "last_post_status": "",
                "health_score": 100,
            }

    def get_state(self, account: str) -> dict:
        """Get current state for an account."""
        self.init_account(account)
        return self.accounts[account]

    def record_post(self, account: str, post_status: str, confidence_score: int) -> None:
        """Record a post attempt and update state."""
        self.init_account(account)
        state = self.accounts[account]

        state["last_post_at"] = datetime.now().isoformat(timespec="seconds")
        state["last_post_status"] = post_status

        # Update cooldown state if posted
        if post_status.startswith("posted"):
            state["consecutive_failures"] = 0

            # Track low-confidence posts
            if confidence_score < 80:
                state["consecutive_low_confidence"] += 1
                if state["consecutive_low_confidence"] >= 2:
                    # Auto-pause after 2 consecutive low-confidence posts (instead of cooldown)
                    self.transition_to_paused(account, 30)
                    state = self.accounts[account]  # Re-fetch after transition
                    state["consecutive_low_confidence"] = 0
                else:
                    # First low-confidence post: still go to cooldown
                    state["state"] = "cooldown"
                    state["state_entered_at"] = datetime.now().isoformat(timespec="seconds")
                    state["cooldown_expires_at"] = (datetime.now() + timedelta(minutes=24)).isoformat(timespec="seconds")
            else:
                # High-confidence post: cooldown, reset counter
                state["state"] = "cooldown"
                state["state_entered_at"] = datetime.now().isoformat(timespec="seconds")
                state["cooldown_expires_at"] = (datetime.now() + timedelta(minutes=24)).isoformat(timespec="seconds")
                state["consecutive_low_confidence"] = 0

            # Update health score
            if confidence_score >= 100:
                state["health_score"] = min(100, state["health_score"] + 5)
            elif confidence_score >= 80:
                state["health_score"] = min(100, state["health_score"] + 2)
            elif confidence_score >= 50:
                state["health_score"] = max(30, state["health_score"] - 3)
            else:
                state["health_score"] = max(20, state["health_score"] - 8)

        # Update failure state
        elif post_status.startswith("failed") or post_status.startswith("error"):
            # Session/login failures are an operator issue, not account quality.
            # Don't penalise health or advance failure counter for them.
            session_failure = any(
                tag in post_status
                for tag in ("login-required", "session-not-ready", "sessionnotcreated", "profile-not-found")
            )
            if session_failure:
                print(f"[state-machine] Session failure detected for {account} — skipping health penalty.")
            else:
                state["consecutive_failures"] += 1
                state["health_score"] = max(20, state["health_score"] - 15)

                if state["consecutive_failures"] >= 2:
                    # Move to degraded after 2 consecutive non-session failures
                    self.transition_to_degraded(account)
            state["consecutive_low_confidence"] = 0

        # Reset consecutive counters on success/skip
        elif post_status.startswith("skipped"):
            # Skips don't reset failure count (ongoing issue)
            pass

        self.save()

    def is_eligible(self, account: str) -> tuple[bool, str]:
        """Check if account is eligible for posting attempt."""
        self.init_account(account)
        state = self.accounts[account]
        current = datetime.now()

        # Check pause state
        if state["state"] == "paused":
            pause_exp = state.get("pause_expires_at")
            if pause_exp:
                try:
                    exp_dt = datetime.fromisoformat(pause_exp)
                    if current < exp_dt:
                        remaining = (exp_dt - current).total_seconds() / 60
                        return False, f"paused:expires-in-{int(remaining)}m"
                    else:
                        # Pause expired, transition to active
                        self.transition_to_active(account)
                except Exception:
                    pass

        # Check cooldown state
        if state["state"] == "cooldown":
            cooldown_exp = state.get("cooldown_expires_at")
            if cooldown_exp:
                try:
                    exp_dt = datetime.fromisoformat(cooldown_exp)
                    if current < exp_dt:
                        remaining = (exp_dt - current).total_seconds() / 60
                        return False, f"cooldown:expires-in-{int(remaining)}m"
                    else:
                        # Cooldown expired, transition to active
                        self.transition_to_active(account)
                except Exception:
                    pass

        # Check blocked state with exponential backoff
        if state["state"] == "blocked":
            retry_exp = state.get("blocked_retry_expires_at")
            if retry_exp:
                try:
                    exp_dt = datetime.fromisoformat(retry_exp)
                    if current < exp_dt:
                        remaining = (exp_dt - current).total_seconds() / 60
                        return False, f"blocked:retry-in-{int(remaining)}m"
                    else:
                        # Retry window opened, transition to degraded for retry
                        self.transition_to_degraded(account)
                except Exception:
                    pass

        # Active, degraded are eligible
        if state["state"] in {"active", "degraded"}:
            return True, f"{state['state']}:health={state['health_score']}"

        return False, f"unknown-state:{state['state']}"

    def transition_to_active(self, account: str) -> None:
        """Transition account to active state."""
        self.init_account(account)
        state = self.accounts[account]
        state["state"] = "active"
        state["state_entered_at"] = datetime.now().isoformat(timespec="seconds")
        state["consecutive_failures"] = 0
        state["consecutive_low_confidence"] = 0
        state["cooldown_expires_at"] = None
        state["blocked_retry_expires_at"] = None
        state["pause_expires_at"] = None
        self.save()

    def transition_to_cooldown(self, account: str, minutes: int = 24) -> None:
        """Transition account to cooldown state."""
        self.init_account(account)
        state = self.accounts[account]
        state["state"] = "cooldown"
        state["state_entered_at"] = datetime.now().isoformat(timespec="seconds")
        state["cooldown_expires_at"] = (datetime.now() + timedelta(minutes=minutes)).isoformat(timespec="seconds")
        self.save()

    def transition_to_degraded(self, account: str) -> None:
        """Transition account to degraded state."""
        self.init_account(account)
        state = self.accounts[account]
        state["state"] = "degraded"
        state["state_entered_at"] = datetime.now().isoformat(timespec="seconds")
        state["consecutive_failures"] = min(state["consecutive_failures"], 2)
        self.save()

    def transition_to_blocked(self, account: str) -> None:
        """Transition account to blocked state with exponential backoff."""
        self.init_account(account)
        state = self.accounts[account]
        state["state"] = "blocked"
        state["state_entered_at"] = datetime.now().isoformat(timespec="seconds")
        state["blocked_retry_count"] = state.get("blocked_retry_count", 0) + 1

        # Exponential backoff: 1h, 6h, 24h, 72h
        backoff_hours = [1, 6, 24, 72]
        hours = backoff_hours[min(state["blocked_retry_count"] - 1, len(backoff_hours) - 1)]
        state["blocked_retry_expires_at"] = (datetime.now() + timedelta(hours=hours)).isoformat(timespec="seconds")
        self.save()

    def transition_to_paused(self, account: str, minutes: int = 30) -> None:
        """Transition account to paused state (low-confidence spam protection)."""
        self.init_account(account)
        state = self.accounts[account]
        state["state"] = "paused"
        state["state_entered_at"] = datetime.now().isoformat(timespec="seconds")
        state["pause_expires_at"] = (datetime.now() + timedelta(minutes=minutes)).isoformat(timespec="seconds")
        self.save()

    def _account_performance_bias(self, account: str) -> tuple[int, int, int]:
        """Return lightweight performance signals from cached Twitter results.

        Returns:
            tuple[int, int, int]: (verified_posts, pending_posts, recent_verified_posts)
        """
        metrics = get_account_cache_metrics(account)
        return (
            int(metrics.get("verified", 0) or 0),
            int(metrics.get("pending", 0) or 0),
            int(metrics.get("recent_verified", 0) or 0),
        )

    def get_best_eligible_account(self, accounts: list[str]) -> Optional[str]:
        """Select the best eligible account for posting attempt.
        
        Priority:
        1. Active accounts with highest health score
        2. Degraded accounts with highest health score
        3. None if no eligible accounts
        """
        eligible = []
        for account in accounts:
            is_elig, _reason = self.is_eligible(account)
            if is_elig:
                state = self.get_state(account)
                health = state.get("health_score", 50)
                verified_posts, pending_posts, recent_verified_posts = self._account_performance_bias(account)
                eligible.append((account, state["state"], health, verified_posts, pending_posts, recent_verified_posts))

        if not eligible:
            return None

        # Sort by: state (active first), then health descending
        state_priority = {"active": 0, "degraded": 1}
        eligible.sort(
            key=lambda x: (
                state_priority.get(x[1], 99),
                -x[2],  # negative health for descending sort
                -x[5],  # recent verified wins first
                -x[3],  # lifetime verified wins next
                x[4],   # fewer pending posts preferred on ties
            )
        )
        return eligible[0][0]

    def summary(self) -> str:
        """Generate a summary of all account states."""
        lines = []
        for account, state in self.accounts.items():
            s = state["state"].upper()
            h = state.get("health_score", 50)
            last_post = state.get("last_post_status", "none")
            lines.append(f"{account:20} | {s:10} | health={h:3} | last={last_post[:50]}")
        return "\n".join(lines)
