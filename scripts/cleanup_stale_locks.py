#!/usr/bin/env python3
"""
Stale lock cleanup utility for Firefox profiles.

Detects and safely removes stale Firefox profile lock files that indicate
crashed or hung processes, preventing profile-in-use errors.

Usage:
    python3 scripts/cleanup_stale_locks.py
    python3 scripts/cleanup_stale_locks.py --dry-run
"""

import json
import os
import sys
import subprocess
from pathlib import Path

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT_DIR, 'src'))

from cache import get_twitter_cache_path


def ok(msg: str) -> None:
    print(f"✅ {msg}")

def warn(msg: str) -> None:
    print(f"⚠️  {msg}")

def fail(msg: str) -> None:
    print(f"❌ {msg}")

def info(msg: str) -> None:
    print(f"ℹ️  {msg}")


def is_process_using_lock(lock_path: str) -> bool:
    """Check if any process is currently using this lock file."""
    try:
        result = subprocess.run(
            ['lsof', lock_path],
            capture_output=True,
            text=True,
            timeout=2
        )
        lines = result.stdout.strip().split('\n')[1:]  # Skip header
        return bool(lines and lines[0])
    except (subprocess.TimeoutExpired, FileNotFoundError):
        # lsof not available or timeout; assume process active to be safe
        return True


def cleanup_profile_locks(profile_path: str, dry_run: bool = False) -> dict:
    """
    Clean stale locks from a Firefox profile.
    
    Returns:
        dict: {'cleaned': count, 'skipped': count, 'errors': [str, ...]}
    """
    if not os.path.exists(profile_path):
        return {'cleaned': 0, 'skipped': 1, 'errors': [f"Profile not found: {profile_path}"]}
    
    lock_files = ['.parentlock', 'parent.lock', 'lock']
    result = {'cleaned': 0, 'skipped': 0, 'errors': []}
    
    for lock_name in lock_files:
        lock_path = os.path.join(profile_path, lock_name)
        
        if not os.path.exists(lock_path):
            continue
        
        # Check if process is using it
        if is_process_using_lock(lock_path):
            result['skipped'] += 1
            info(f"Skipped (active): {lock_name}")
            continue
        
        # Lock is stale; safe to remove
        if dry_run:
            info(f"Would remove (dry-run): {lock_name}")
            result['cleaned'] += 1
        else:
            try:
                os.remove(lock_path)
                ok(f"Removed stale lock: {lock_name}")
                result['cleaned'] += 1
            except OSError as e:
                fail(f"Failed to remove {lock_name}: {e}")
                result['errors'].append(str(e))
    
    return result


def main():
    dry_run = '--dry-run' in sys.argv
    
    if dry_run:
        info("Running in DRY-RUN mode (no files will be modified)")
    
    cache_path = get_twitter_cache_path()
    if not os.path.exists(cache_path):
        info("No cache file found; nothing to clean")
        return
    
    try:
        with open(cache_path, 'r') as f:
            cache = json.load(f)
    except (json.JSONDecodeError, IOError) as e:
        fail(f"Cannot read cache: {e}")
        return
    
    accounts = cache.get('accounts', [])
    
    if not accounts:
        info("No accounts configured")
        return
    
    print("\n" + "="*60)
    print("Firefox Profile Lock Cleanup Utility")
    print("="*60 + "\n")
    
    total_cleaned = 0
    total_skipped = 0
    
    for account in accounts:
        nickname = account.get('nickname', 'unknown')
        profile_path = account.get('firefox_profile')
        
        if not profile_path:
            continue
        
        print(f"\n📁 {nickname}")
        print(f"   Profile: {profile_path}")
        
        if not os.path.exists(profile_path):
            warn("Profile directory not found")
            total_skipped += 1
            continue
        
        result = cleanup_profile_locks(profile_path, dry_run=dry_run)
        total_cleaned += result['cleaned']
        total_skipped += result['skipped']
        
        if result['errors']:
            for error in result['errors']:
                fail(f"   Error: {error}")
    
    print("\n" + "="*60)
    print(f"Summary: Cleaned {total_cleaned} | Skipped {total_skipped}")
    if dry_run:
        print("(DRY-RUN mode: no files were actually deleted)")
    print("="*60 + "\n")


if __name__ == '__main__':
    main()
