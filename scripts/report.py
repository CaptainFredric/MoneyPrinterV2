#!/usr/bin/env python3
"""
scripts/report.py — MoneyPrinterV2 post-history & health report.

Usage (from repo root, venv active):
    python scripts/report.py              # full report
    python scripts/report.py --json       # machine-readable JSON output
    python scripts/report.py --backup     # trigger a manual cache backup
    python scripts/report.py --restore    # list and restore a cache backup

Shows:
  • Posts per account (count, last post time, last content preview)
  • Cooldown status (time until next post is allowed)
  • Cache backup inventory
  • Daemon crash log tail (last 20 lines)
"""
import argparse
import glob
import json
import os
import shutil
import sys
from datetime import datetime
from pathlib import Path

ROOT_DIR   = Path(__file__).resolve().parent.parent
MP_DIR     = ROOT_DIR / ".mp"
BACKUP_DIR = MP_DIR / "backups"
LOG_DIR    = ROOT_DIR / "logs"
CRASH_LOG  = LOG_DIR / "daemon_crash.log"
COOLDOWN_SECONDS = 1800  # must match Twitter.py


def _load_json(path: Path) -> dict:
    try:
        with open(path, "r") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}


def _fmt_ago(dt: datetime) -> str:
    delta = datetime.now() - dt
    s = int(delta.total_seconds())
    if s < 60:
        return f"{s}s ago"
    if s < 3600:
        return f"{s // 60}m ago"
    return f"{s // 3600}h {(s % 3600) // 60}m ago"


def _cooldown_remaining(last_dt: datetime) -> str:
    elapsed = (datetime.now() - last_dt).total_seconds()
    remaining = COOLDOWN_SECONDS - elapsed
    if remaining <= 0:
        return "✅ Ready to post"
    return f"⏳ {int(remaining // 60)}m {int(remaining % 60)}s until next post"


def do_backup():
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backed = []
    for name in ("twitter.json", "afm.json", "youtube.json"):
        src = MP_DIR / name
        if src.exists():
            dst = BACKUP_DIR / f"{name}.{stamp}"
            shutil.copy2(src, dst)
            backed.append(str(dst))
    if backed:
        print(f"✅ Backed up {len(backed)} cache file(s):")
        for b in backed:
            print(f"   {b}")
    else:
        print("⚠️  No cache files found to back up.")
    return backed


def do_restore():
    if not BACKUP_DIR.exists():
        print("No backups found.")
        return
    backups = sorted(BACKUP_DIR.glob("*.json.*"))
    if not backups:
        print("No backups found.")
        return
    print("\nAvailable backups:")
    for i, b in enumerate(backups):
        size = b.stat().st_size
        mtime = datetime.fromtimestamp(b.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S")
        print(f"  {i + 1:>3}. [{mtime}] {b.name}  ({size} bytes)")
    raw = input("\nEnter number to restore (or Enter to cancel): ").strip()
    if not raw:
        print("Cancelled.")
        return
    try:
        chosen = backups[int(raw) - 1]
    except (ValueError, IndexError):
        print("Invalid selection.")
        return
    # Derive target — strip the timestamp suffix
    target_name = chosen.name.rsplit(".", 1)[0]  # e.g. "twitter.json"
    target_path = MP_DIR / target_name
    # Safety backup of current
    if target_path.exists():
        safety = target_path.with_suffix(".json.pre_restore")
        shutil.copy2(target_path, safety)
        print(f"Saved current file to {safety.name}")
    shutil.copy2(chosen, target_path)
    print(f"✅ Restored {chosen.name} → {target_path}")


def build_report() -> dict:
    report = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "twitter": [],
        "afm_products": 0,
        "backups": [],
        "daemon_crash_log_tail": [],
    }

    # ── Twitter ────────────────────────────────────────────────────────────
    tw_data = _load_json(MP_DIR / "twitter.json")
    for acc in tw_data.get("accounts", []):
        posts = acc.get("posts", [])
        last_dt = None
        last_content = ""
        last_category = ""
        if posts:
            try:
                last_dt = datetime.strptime(posts[-1]["date"], "%m/%d/%Y, %H:%M:%S")
                last_content = posts[-1].get("content", "")[:80]
                last_category = posts[-1].get("category", "")
            except (ValueError, KeyError):
                pass
        report["twitter"].append({
            "nickname": acc.get("nickname", "?"),
            "id": acc.get("id", "?")[:8] + "...",
            "topic": acc.get("topic", "?"),
            "post_count": len(posts),
            "last_post_ago": _fmt_ago(last_dt) if last_dt else "never",
            "cooldown_status": _cooldown_remaining(last_dt) if last_dt else "✅ Ready to post",
            "last_content_preview": last_content,
            "last_category": last_category,
        })

    # ── AFM ────────────────────────────────────────────────────────────────
    afm_data = _load_json(MP_DIR / "afm.json")
    report["afm_products"] = len(afm_data.get("products", []))

    # ── Backups ────────────────────────────────────────────────────────────
    if BACKUP_DIR.exists():
        backups = sorted(BACKUP_DIR.glob("*.json.*"))
        report["backups"] = [b.name for b in backups[-10:]]  # last 10

    # ── Crash log tail ─────────────────────────────────────────────────────
    if CRASH_LOG.exists():
        with open(CRASH_LOG, "r") as f:
            lines = f.readlines()
        report["daemon_crash_log_tail"] = [l.rstrip() for l in lines[-20:]]

    return report


def print_report(report: dict):
    W = 62
    print("=" * W)
    print(f"  MoneyPrinterV2 Status Report — {report['generated_at']}")
    print("=" * W)

    print("\n📊 Twitter Accounts")
    print("-" * W)
    for acc in report["twitter"]:
        print(f"  Account  : {acc['nickname']} ({acc['id']})")
        print(f"  Topic    : {acc['topic']}")
        print(f"  Posts    : {acc['post_count']}")
        print(f"  Last Post: {acc['last_post_ago']}")
        print(f"  Cooldown : {acc['cooldown_status']}")
        if acc.get("last_category"):
            print(f"  Category : {acc['last_category']}")
        if acc["last_content_preview"]:
            print(f"  Preview  : \"{acc['last_content_preview']}...\"")
        print()

    if not report["twitter"]:
        print("  No Twitter accounts found.\n")

    print(f"📦 AFM Products cached: {report['afm_products']}")
    print()

    print("💾 Recent Backups")
    print("-" * W)
    if report["backups"]:
        for b in report["backups"]:
            print(f"  {b}")
    else:
        print("  No backups yet. Run: python scripts/report.py --backup")
    print()

    if report["daemon_crash_log_tail"]:
        print("🔴 Daemon Crash Log (last 20 lines)")
        print("-" * W)
        for line in report["daemon_crash_log_tail"]:
            print(f"  {line}")
        print()

    print("=" * W)
    print("  To start daemon: python scripts/daemon.py")
    print("  To post now:     python scripts/run_once.py twitter <uuid>")
    print("=" * W)


def main():
    parser = argparse.ArgumentParser(description="MoneyPrinterV2 report & backup tool")
    parser.add_argument("--json",    action="store_true", help="Output as JSON")
    parser.add_argument("--backup",  action="store_true", help="Trigger a manual cache backup")
    parser.add_argument("--restore", action="store_true", help="Interactively restore a cache backup")
    args = parser.parse_args()

    if args.backup:
        do_backup()
        return

    if args.restore:
        do_restore()
        return

    report = build_report()

    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print_report(report)


if __name__ == "__main__":
    main()
