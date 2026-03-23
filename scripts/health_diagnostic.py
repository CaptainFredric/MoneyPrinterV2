#!/usr/bin/env python3
"""
Comprehensive health diagnostic for MoneyPrinterV2 Twitter automation.

Checks:
- Firefox profile lock status
- Cache integrity
- Recent transaction history
- Cooldown state
- Session readiness
- Stale process cleanup needs

Usage:
    python3 scripts/health_diagnostic.py
    python3 scripts/health_diagnostic.py <account_nickname>
"""

import json
import os
import sys
import subprocess
from datetime import datetime, timedelta
from pathlib import Path

# Add src to path
ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT_DIR, 'src'))

from config import ROOT_DIR as CONFIG_ROOT_DIR
from cache import get_twitter_cache_path

# Define CONFIG_FILE path
CONFIG_FILE = os.path.join(ROOT_DIR, 'config.json')


def ok(msg: str) -> None:
    print(f"✅ {msg}")

def warn(msg: str) -> None:
    print(f"⚠️  {msg}")

def fail(msg: str) -> None:
    print(f"❌ {msg}")

def info(msg: str) -> None:
    print(f"ℹ️  {msg}")


def check_firefox_locks() -> dict:
    """Check Firefox lock status for all profiles."""
    results = {}
    
    if not os.path.exists(CONFIG_FILE):
        return results
    
    with open(CONFIG_FILE, 'r') as f:
        config = json.load(f)
    
    # Load from twitter.json for correct structure
    cache_path = get_twitter_cache_path()
    try:
        with open(cache_path, 'r') as f:
            cache_data = json.load(f)
    except (json.JSONDecodeError, IOError):
        cache_data = {}
    
    accounts = cache_data.get('accounts', [])
    for account in accounts:
        uuid = account.get('id')
        nickname = account.get('nickname')
        profile_path = account.get('firefox_profile')
        
        if not profile_path or not os.path.exists(profile_path):
            continue
        
        lock_files = ['.parentlock', 'parent.lock', 'lock']
        locks_found = []
        
        for lock_name in lock_files:
            lock_path = os.path.join(profile_path, lock_name)
            if os.path.exists(lock_path):
                locks_found.append(lock_name)
        
        # Try lsof to see if any process is using the locks
        has_active_process = False
        for lock_name in locks_found:
            lock_path = os.path.join(profile_path, lock_name)
            try:
                result = subprocess.run(
                    ['lsof', lock_path],
                    capture_output=True,
                    text=True,
                    timeout=2
                )
                lines = result.stdout.strip().split('\n')[1:]
                if lines and lines[0]:
                    has_active_process = True
            except (subprocess.TimeoutExpired, FileNotFoundError):
                pass
        
        results[nickname] = {
            'uuid': uuid,
            'profile_path': profile_path,
            'locks': locks_found,
            'active_process': has_active_process,
            'stale': bool(locks_found and not has_active_process)
        }
    
    return results


def check_cache_integrity() -> dict:
    """Check cache file integrity and structure."""
    cache_path = get_twitter_cache_path()
    
    if not os.path.exists(cache_path):
        return {'exists': False, 'valid': True}
    
    try:
        with open(cache_path, 'r') as f:
            cache = json.load(f)
        
        issues = []
        account_stats = {}
        
        # Cache structure: {"accounts": [{"id": "...", "posts": [...], ...}]}
        accounts = cache.get('accounts', [])
        
        for account in accounts:
            uuid = account.get('id', 'unknown')
            posts = account.get('posts', [])
            account_stats[uuid] = {
                'post_count': len(posts),
                'last_post': None
            }
            
            if posts:
                try:
                    last_post = posts[-1]
                    last_date_str = last_post.get('date', '')
                    last_dt = datetime.strptime(last_date_str, "%m/%d/%Y, %H:%M:%S")
                    account_stats[uuid]['last_post'] = last_dt
                except (ValueError, KeyError):
                    issues.append(f"Malformed date in last post of {uuid}")
        
        return {
            'exists': True,
            'valid': len(issues) == 0,
            'issues': issues,
            'accounts': account_stats
        }
    
    except json.JSONDecodeError as e:
        return {
            'exists': True,
            'valid': False,
            'corrupted': True,
            'error': str(e)
        }


def check_transaction_logs() -> dict:
    """Check recent transaction logs for errors and patterns."""
    log_dir = os.path.join(ROOT_DIR, 'logs', 'transaction_log')
    
    if not os.path.exists(log_dir):
        return {'exists': False}
    
    results = {'exists': True}
    
    for log_file in os.listdir(log_dir):
        if not log_file.endswith('.log'):
            continue
        
        nickname = log_file.replace('.log', '')
        log_path = os.path.join(log_dir, log_file)
        
        try:
            with open(log_path, 'r') as f:
                logs = json.load(f)
            
            # Last 20 attempts
            recent = logs[-20:] if len(logs) > 20 else logs
            
            success_count = sum(1 for log in recent if log.get('status') == 'success')
            failed_count = sum(1 for log in recent if log.get('status') == 'failed')
            skipped_count = sum(1 for log in recent if log.get('status') == 'skipped')
            
            last_attempt = None
            if logs:
                try:
                    last_attempt = datetime.fromisoformat(logs[-1]['timestamp'])
                except (ValueError, KeyError):
                    pass
            
            results[nickname] = {
                'total_logged': len(logs),
                'recent_20': {
                    'success': success_count,
                    'failed': failed_count,
                    'skipped': skipped_count
                },
                'last_attempt': last_attempt,
                'recent_errors': [
                    log.get('reason', log.get('status', 'unknown'))
                    for log in reversed(recent)
                    if log.get('status') != 'success'
                ][:5]
            }
        
        except (json.JSONDecodeError, IOError):
            results[nickname] = {'error': 'Could not read transaction log'}
    
    return results


def check_cooldown_status() -> dict:
    """Check cooldown status for all accounts."""
    cache = check_cache_integrity()
    if not cache.get('exists'):
        return {'status': 'no_cache'}
    
    results = {}
    min_gap = 1800  # 30 minutes
    now = datetime.now()
    
    for uuid, stats in cache.get('accounts', {}).items():
        last_post = stats.get('last_post')
        
        if last_post is None:
            results[uuid] = {'status': 'no_posts_yet'}
        else:
            elapsed = (now - last_post).total_seconds()
            remaining = max(0, min_gap - elapsed)
            
            if remaining > 0:
                results[uuid] = {
                    'status': 'cooldown_active',
                    'elapsed_minutes': int(elapsed / 60),
                    'remaining_minutes': int(remaining / 60)
                }
            else:
                results[uuid] = {
                    'status': 'ready_to_post',
                    'last_post_minutes_ago': int(elapsed / 60)
                }
    
    return results


def main():
    if len(sys.argv) > 1:
        target = sys.argv[1]
    else:
        target = None

    if target and str(target).strip().lower() == "all":
        target = None
    
    print("\n" + "="*60)
    print("MoneyPrinterV2 Health Diagnostic")
    print("="*60 + "\n")
    
    # 1. Firefox Locks
    print("1️⃣  Firefox Profile Locks")
    print("-" * 40)
    locks = check_firefox_locks()
    for nickname, info_dict in locks.items():
        if target and target != nickname:
            continue
        
        if info_dict['stale']:
            fail(f"{nickname}: STALE LOCKS DETECTED (can be cleaned)")
            print(f"      Profile: {info_dict['profile_path']}")
            print(f"      Locks: {', '.join(info_dict['locks'])}")
        elif info_dict['locks']:
            warn(f"{nickname}: Locked (Firefox session active)")
        else:
            ok(f"{nickname}: No locks")
    
    print()
    
    # 2. Cache Integrity
    print("2️⃣  Cache Integrity")
    print("-" * 40)
    cache_info = check_cache_integrity()
    if not cache_info.get('exists'):
        info("Cache file not yet created (normal on first run)")
    elif cache_info.get('corrupted'):
        fail(f"Cache CORRUPTED: {cache_info.get('error')}")
    elif cache_info.get('valid'):
        ok("Cache structure valid")
        print(f"   Accounts: {len(cache_info.get('accounts', {}))}")
        for uuid, stats in cache_info.get('accounts', {}).items():
            print(f"   - {uuid[:8]}...: {stats['post_count']} posts")
    else:
        warn(f"Cache has issues: {cache_info.get('issues')}")
    
    print()
    
    # 3. Transaction Logs
    print("3️⃣  Transaction History (Last 20 per account)")
    print("-" * 40)
    tx_logs = check_transaction_logs()
    if not tx_logs.get('exists'):
        info("No transaction logs yet (normal on first run)")
    else:
        for nickname, tx_info in tx_logs.items():
            if nickname == 'exists':
                continue
            if target and target != nickname:
                continue
            
            if 'error' in tx_info:
                fail(f"{nickname}: {tx_info['error']}")
            else:
                recent = tx_info['recent_20']
                success = recent['success']
                failed = recent['failed']
                skipped = recent['skipped']
                
                total_attempts = success + failed + skipped
                success_rate = (success / total_attempts * 100) if total_attempts > 0 else 0
                
                if success_rate >= 80:
                    ok(f"{nickname}: {success_rate:.0f}% success ({success}/{total_attempts} recent)")
                elif success_rate >= 50:
                    warn(f"{nickname}: {success_rate:.0f}% success ({success}/{total_attempts} recent)")
                else:
                    fail(f"{nickname}: {success_rate:.0f}% success ({success}/{total_attempts} recent)")
                
                if tx_info['recent_errors']:
                    print(f"   Recent errors: {', '.join(tx_info['recent_errors'][:3])}")
    
    print()
    
    # 4. Cooldown Status
    print("4️⃣  Cooldown Status")
    print("-" * 40)
    cooldown = check_cooldown_status()
    if cooldown.get('status') == 'no_cache':
        info("No cooldown info available yet")
    else:
        for uuid, cd_info in cooldown.items():
            if target and target != uuid:
                continue
            
            status = cd_info.get('status')
            if status == 'no_posts_yet':
                ok(f"{uuid[:8]}...: Ready (no posts yet)")
            elif status == 'cooldown_active':
                remaining = cd_info.get('remaining_minutes', 0)
                if remaining < 5:
                    warn(f"{uuid[:8]}...: Cooldown expires in {remaining}m")
                else:
                    info(f"{uuid[:8]}...: Cooldown active ({remaining}m remaining)")
            elif status == 'ready_to_post':
                ok(f"{uuid[:8]}...: Ready to post")
    
    print()
    print("="*60)


if __name__ == '__main__':
    main()
