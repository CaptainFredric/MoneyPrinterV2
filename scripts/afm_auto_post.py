#!/usr/bin/env python3
"""Auto-post affiliate pitches using `src/classes/AFM.py` and Twitter posting.

Usage:
  python scripts/afm_auto_post.py --links-file .mp/affiliate_links.json --account EyeCatcher --count 1 [--dry-run]

The script is conservative by default (`--dry-run`).
"""
import argparse
import json
import os
import time
from pathlib import Path
from urllib.parse import urlparse, urlunparse, parse_qsl, urlencode

ROOT = Path(__file__).resolve().parent.parent
TWITTER_CACHE = ROOT / ".mp" / "twitter.json"


def add_utm(url: str, params: dict) -> str:
    try:
        parsed = urlparse(url)
        q = dict(parse_qsl(parsed.query, keep_blank_values=True))
        q.update(params)
        new_query = urlencode(q, doseq=True)
        return urlunparse((parsed.scheme, parsed.netloc, parsed.path, parsed.params, new_query, parsed.fragment))
    except Exception:
        return url


def load_links(links_file: Path):
    if links_file.exists():
        try:
            return json.loads(links_file.read_text(encoding='utf-8'))
        except Exception:
            return []
    return []


def load_accounts():
    try:
        data = json.loads(TWITTER_CACHE.read_text(encoding='utf-8'))
        return data.get('accounts', [])
    except Exception:
        return []


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--links-file', default=str(ROOT / '.mp' / 'affiliate_links.json'))
    parser.add_argument('--account', default='all')
    parser.add_argument('--count', type=int, default=1)
    parser.add_argument('--dry-run', action='store_true', default=True)
    args = parser.parse_args()

    links = load_links(Path(args.links_file))
    if not links:
        print('No affiliate links found in', args.links_file)
        return

    accounts = load_accounts()
    selected = []
    if args.account.lower() == 'all':
        selected = accounts
    else:
        for a in accounts:
            if a.get('nickname','').lower() == args.account.lower() or a.get('id','').lower() == args.account.lower():
                selected = [a]
                break

    if not selected:
        print('No matching accounts found for', args.account)
        return

    # Lazy imports (avoid startup cost if dry-run and not used)
    from src.classes.AFM import AffiliateMarketing
    from src.classes.Twitter import Twitter

    posted = 0
    for account in selected:
        nickname = account.get('nickname') or account.get('id')[:8]
        profile = account.get('firefox_profile')
        topic = account.get('topic','')

        for idx, link in enumerate(links[: args.count]):
            print(f'[{nickname}] Preparing affiliate post for: {link}')
            # Scrape & generate pitch
            try:
                afm = AffiliateMarketing(link, profile, account.get('id'), nickname, topic)
                pitch = afm.generate_pitch()
                # use UTM'd link for sharing
                utm_link = add_utm(link, {
                    'utm_source': 'twitter',
                    'utm_medium': 'social',
                    'utm_campaign': f'mpv2_{nickname.lower()}'
                })
                share_text = pitch.replace(link, utm_link)
                afm.quit()
            except Exception as e:
                print('  AFM generation failed:', e)
                continue

            print('  Generated pitch (truncated):', share_text[:200].replace('\n',' '))

            if args.dry_run:
                print('  Dry-run enabled; not posting.')
                continue

            try:
                t = Twitter(account.get('id'), nickname, profile, topic, account.get('browser_binary',''))
                status = t.post(share_text)
                print('  Post result:', status)
                posted += 1
                t.quit()
            except Exception as e:
                print('  Post failed:', e)

            # polite delay
            time.sleep(6)

    print('Done. Posted count:', posted)


if __name__ == '__main__':
    main()
