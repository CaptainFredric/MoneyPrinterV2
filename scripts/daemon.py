#!/usr/bin/env python3
"""
scripts/daemon.py — MoneyPrinterV2 persistent scheduler daemon.

Reads schedule definitions from config.json["schedules"] (or falls back to
a sensible default) and runs the Twitter / YouTube cron jobs on time,
indefinitely, with:

  • Exponential back-off on repeated consecutive failures
  • Crash-log rotation (logs/daemon_crash.log)
  • Cache backup before every job run
  • Graceful SIGTERM / SIGINT shutdown (no half-run jobs left dangling)
  • Headless=true forced when invoked with --headless

Usage (from repo root, venv active):
    python scripts/daemon.py
    python scripts/daemon.py --headless          # force headless mode
    python scripts/daemon.py --dry-run           # print schedule, don't run

Schedule config format in config.json (optional — defaults apply if absent):
    "schedules": [
        {"provider": "twitter", "account_id": "<uuid>", "times": ["09:00", "15:00", "21:00"]},
        {"provider": "youtube", "account_id": "<uuid>", "times": ["11:00"]}
    ]
"""
import argparse
import json
import logging
import os
import signal
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

# ── Resolve repo root regardless of CWD ───────────────────────────────────
SCRIPT_DIR = Path(__file__).resolve().parent
ROOT_DIR   = SCRIPT_DIR.parent
VENV_PYTHON = ROOT_DIR / "venv" / "bin" / "python"
CRON_SCRIPT = ROOT_DIR / "src" / "cron.py"
CONFIG_PATH = ROOT_DIR / "config.json"
LOG_DIR     = ROOT_DIR / "logs"
CRASH_LOG   = LOG_DIR / "daemon_crash.log"
BACKUP_DIR  = ROOT_DIR / ".mp" / "backups"

# Max consecutive failures before exponential back-off kicks in
MAX_CONSECUTIVE_FAILURES = 3
# Seconds to sleep in the main loop tick
TICK_SECONDS = 30

_shutdown_requested = False


# ── Logging ───────────────────────────────────────────────────────────────
LOG_DIR.mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(CRASH_LOG, encoding="utf-8"),
    ],
)
log = logging.getLogger("mpv2.daemon")


# ── Signal handling ────────────────────────────────────────────────────────
def _handle_signal(sig, frame):
    global _shutdown_requested
    log.info(f"Received signal {sig} — shutting down after current tick.")
    _shutdown_requested = True

signal.signal(signal.SIGTERM, _handle_signal)
signal.signal(signal.SIGINT,  _handle_signal)


# ── Config helpers ─────────────────────────────────────────────────────────
def _load_config() -> dict:
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def _get_model(cfg: dict) -> str:
    return cfg.get("ollama_model", "llama3.2:3b")


def _get_schedules(cfg: dict) -> list[dict]:
    """
    Returns schedule definitions.  Falls back to posting every account
    3× / day at 09:00, 15:00, 21:00 if no explicit config is present.
    """
    if "schedules" in cfg:
        return cfg["schedules"]

    # Auto-build from cached twitter accounts
    try:
        twitter_cache = ROOT_DIR / ".mp" / "twitter.json"
        with open(twitter_cache, "r") as f:
            data = json.load(f)
        schedules = []
        for acc in data.get("accounts", []):
            schedules.append({
                "provider": "twitter",
                "account_id": acc["id"],
                "nickname": acc.get("nickname", acc["id"][:8]),
                "times": ["09:00", "15:00", "21:00"],
            })
        if schedules:
            return schedules
    except Exception:
        pass

    return []


# ── Cache backup ───────────────────────────────────────────────────────────
def _backup_caches():
    """Rotates a timestamped backup of twitter.json and afm.json (keeps 7)."""
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    for name in ("twitter.json", "afm.json", "youtube.json"):
        src = ROOT_DIR / ".mp" / name
        if src.exists():
            dst = BACKUP_DIR / f"{name}.{stamp}"
            try:
                import shutil
                shutil.copy2(src, dst)
            except Exception as exc:
                log.warning(f"Backup of {name} failed: {exc}")

    # Prune — keep only the 7 most recent backups per file
    for name in ("twitter.json", "afm.json", "youtube.json"):
        pattern = f"{name}.*"
        old_backups = sorted(BACKUP_DIR.glob(pattern))
        for old in old_backups[:-7]:
            try:
                old.unlink()
            except Exception:
                pass


# ── Job runner ─────────────────────────────────────────────────────────────
def _last_post_preview(account_id: str, max_len: int = 120) -> str | None:
    """
    Reads .mp/twitter.json and returns a truncated preview of the most recent
    post for *account_id*, or None if nothing can be found.
    """
    try:
        with open(ROOT_DIR / ".mp" / "twitter.json", "r") as fh:
            data = json.load(fh)
        for acc in data.get("accounts", []):
            if acc.get("id") == account_id:
                posts = acc.get("posts", [])
                if posts:
                    text = posts[-1].get("content", "").strip()
                    return text[:max_len] + ("…" if len(text) > max_len else "")
    except Exception:
        pass
    return None


def _run_job(provider: str, account_id: str, model: str,
             headless: bool, dry_run: bool, nickname: str = "") -> bool:
    """
    Invokes cron.py for the given provider+account.
    Returns True on success, False on failure.
    """
    label = f"{provider}/{nickname or account_id[:8]}"

    if dry_run:
        log.info(f"[DRY-RUN] Would run: {label}")
        return True

    env = os.environ.copy()
    if headless:
        # Inject into config at runtime rather than mutating config.json
        env["MPV2_HEADLESS"] = "1"

    cmd = [str(VENV_PYTHON), str(CRON_SCRIPT), provider, account_id, model]
    log.info(f"▶  Starting job: {label}")
    try:
        result = subprocess.run(
            cmd,
            env=env,
            capture_output=True,
            text=True,
            timeout=300,  # 5-minute hard timeout per job
        )
        combined_output = "\n".join(
            part for part in [result.stdout or "", result.stderr or ""] if part
        )

        post_status = "unknown"
        for line in combined_output.splitlines():
            if line.startswith("MPV2_POST_STATUS:"):
                post_status = line.split(":", 1)[1].strip().lower() or "unknown"
                continue
            if line.strip():
                log.info(line.strip())

        if result.returncode == 0:
            log.info(f"✅ Job completed: {label}")
            if post_status == "posted":
                preview = _last_post_preview(account_id)
                if preview:
                    log.info(f"📝 Posted: {preview}")
            elif post_status.startswith("skipped:"):
                reason = post_status.split(":", 1)[1]
                log.info(f"⏭️ Job skipped: {label} ({reason})")
            elif provider == "twitter":
                log.warning(
                    f"Job status unknown for {label}; cron did not emit post marker."
                )
            return True
        else:
            log.error(f"❌ Job exited with code {result.returncode}: {label}")
            return False
    except subprocess.TimeoutExpired:
        log.error(f"⏰ Job timed out (5m): {label}")
        return False
    except Exception as exc:
        log.error(f"💥 Job crashed: {label} — {exc}")
        return False


# ── Scheduler state ────────────────────────────────────────────────────────
class JobState:
    """Tracks per-job failure streak and back-off state."""
    def __init__(self):
        self.consecutive_failures: int = 0
        self.backoff_until: float = 0.0  # epoch seconds

    def record_success(self):
        self.consecutive_failures = 0
        self.backoff_until = 0.0

    def record_failure(self):
        self.consecutive_failures += 1
        if self.consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
            delay = min(3600, 60 * (2 ** (self.consecutive_failures - MAX_CONSECUTIVE_FAILURES)))
            self.backoff_until = time.time() + delay
            log.warning(
                f"Back-off: {self.consecutive_failures} consecutive failures. "
                f"Pausing this job for {delay // 60}m."
            )

    def is_backed_off(self) -> bool:
        return time.time() < self.backoff_until


# ── Main loop ──────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="MoneyPrinterV2 daemon scheduler")
    parser.add_argument("--headless", action="store_true", help="Force headless browser mode")
    parser.add_argument("--dry-run",  action="store_true", help="Print schedule only, don't run jobs")
    args = parser.parse_args()

    log.info("=" * 60)
    log.info("MoneyPrinterV2 Daemon starting up")
    log.info(f"Root:      {ROOT_DIR}")
    log.info(f"Headless:  {args.headless}")
    log.info(f"Dry-run:   {args.dry_run}")
    log.info("=" * 60)

    # Track which (provider, account_id, time_str) combos have already
    # been run in the current minute so we don't double-fire on slow ticks
    fired_this_minute: set = set()
    job_states: dict[str, JobState] = {}

    while not _shutdown_requested:
        try:
            cfg = _load_config()
        except Exception as exc:
            log.error(f"Could not load config.json: {exc}. Retrying in 60s.")
            time.sleep(60)
            continue

        model     = _get_model(cfg)
        schedules = _get_schedules(cfg)

        if not schedules:
            log.warning("No schedules found.  Add 'schedules' to config.json or ensure .mp/twitter.json exists.")
            time.sleep(60)
            continue

        now       = datetime.now()
        now_hhmm  = now.strftime("%H:%M")
        now_min   = now.strftime("%Y-%m-%d %H:%M")   # unique per calendar minute

        # Expire fired_this_minute when the minute rolls over
        fired_this_minute = {k for k in fired_this_minute if k.startswith(now_min)}

        for sched in schedules:
            provider   = sched.get("provider", "twitter")
            account_id = sched.get("account_id", "")
            times      = sched.get("times", [])
            nickname   = sched.get("nickname", account_id[:8])

            if not account_id:
                continue

            for t in times:
                fire_key = f"{now_min}|{provider}|{account_id}|{t}"
                if now_hhmm != t or fire_key in fired_this_minute:
                    continue

                job_key = f"{provider}|{account_id}"
                state = job_states.setdefault(job_key, JobState())

                if state.is_backed_off():
                    log.warning(f"Skipping {provider}/{nickname} — in back-off period.")
                    fired_this_minute.add(fire_key)
                    continue

                _backup_caches()
                ok = _run_job(provider, account_id, model, args.headless, args.dry_run, nickname)
                fired_this_minute.add(fire_key)

                if ok:
                    state.record_success()
                else:
                    state.record_failure()

        if args.dry_run:
            log.info("Dry-run complete. Scheduled times:")
            for sched in schedules:
                log.info(f"  {sched.get('provider')} / {sched.get('nickname', sched.get('account_id','?')[:8])} @ {sched.get('times')}")
            break

        time.sleep(TICK_SECONDS)

    log.info("Daemon stopped.")


if __name__ == "__main__":
    main()
