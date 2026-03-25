#!/usr/bin/env python3
"""
Safe local temp cleanup for MoneyPrinterV2 workstations.

Removes only high-confidence disposable temp artifacts that have repeatedly
accumulated during local automation and editor usage.

Usage:
    python3 scripts/cleanup_temp_space.py
    python3 scripts/cleanup_temp_space.py --dry-run
"""

from __future__ import annotations

import argparse
import os
import shutil
from pathlib import Path


SAFE_TEMP_NAMES = {
    "CMM5Download",
    "codespaces_logs",
    "node-compile-cache",
}

SAFE_TEMP_PREFIXES = (
    "gkinstall",
    "mpv2_ff_profile_",
    "exthost-",
)

SAFE_TEMP_SUFFIXES = (
    ".zip",
    ".cpuprofile",
)


def _info(message: str) -> None:
    print(f"ℹ️  {message}")


def _ok(message: str) -> None:
    print(f"✅ {message}")


def _warn(message: str) -> None:
    print(f"⚠️  {message}")


def _candidate_temp_root() -> Path:
    env_tmpdir = os.environ.get("TMPDIR", "").strip()
    if env_tmpdir:
        return Path(env_tmpdir).expanduser().resolve()
    return Path("/tmp").resolve()


def _is_safe_target(path: Path) -> bool:
    name = path.name
    if name in SAFE_TEMP_NAMES:
        return True
    if any(name.startswith(prefix) for prefix in SAFE_TEMP_PREFIXES):
        return True
    if any(name.endswith(suffix) for suffix in SAFE_TEMP_SUFFIXES):
        return True
    return False


def _path_size_bytes(path: Path) -> int:
    try:
        if path.is_symlink() or path.is_file():
            return path.stat().st_size
        total = 0
        for child in path.rglob("*"):
            try:
                if child.is_file() and not child.is_symlink():
                    total += child.stat().st_size
            except OSError:
                continue
        return total
    except OSError:
        return 0


def _format_size(size_bytes: int) -> str:
    units = ["B", "KB", "MB", "GB", "TB"]
    value = float(size_bytes)
    for unit in units:
        if value < 1024 or unit == units[-1]:
            return f"{value:.1f}{unit}"
        value /= 1024
    return f"{size_bytes}B"


def _remove_path(path: Path, dry_run: bool) -> tuple[bool, int]:
    size_bytes = _path_size_bytes(path)
    if dry_run:
        _info(f"Would remove {path} ({_format_size(size_bytes)})")
        return True, size_bytes

    try:
        if path.is_dir() and not path.is_symlink():
            shutil.rmtree(path)
        else:
            path.unlink(missing_ok=True)
        _ok(f"Removed {path} ({_format_size(size_bytes)})")
        return True, size_bytes
    except OSError as exc:
        _warn(f"Skipped {path}: {exc}")
        return False, 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Remove safe local temp clutter")
    parser.add_argument("--dry-run", action="store_true", help="preview removals without deleting")
    args = parser.parse_args()

    temp_root = _candidate_temp_root()
    if not temp_root.exists() or not temp_root.is_dir():
        _warn(f"Temp root not found: {temp_root}")
        return

    _info(f"Scanning temp root: {temp_root}")

    removed_count = 0
    reclaimed_bytes = 0
    for child in sorted(temp_root.iterdir(), key=lambda entry: entry.name.lower()):
        if not _is_safe_target(child):
            continue
        removed, size_bytes = _remove_path(child, dry_run=args.dry_run)
        if removed:
            removed_count += 1
            reclaimed_bytes += size_bytes

    mode = "Previewed" if args.dry_run else "Removed"
    _info(f"{mode} {removed_count} temp item(s) | reclaimable {_format_size(reclaimed_bytes)}")


if __name__ == "__main__":
    main()