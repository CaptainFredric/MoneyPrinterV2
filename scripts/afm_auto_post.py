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
import sys
from pathlib import Path
from urllib.parse import urlparse, urlunparse, parse_qsl, urlencode

ROOT = Path(__file__).resolve().parent.parent
TWITTER_CACHE = ROOT / ".mp" / "twitter.json"
SRC_DIR = ROOT / 'src'
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))


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

    # allow a small affiliate helper import from scripts/
    import sys as _sys
    _sys.path.insert(0, str(ROOT / 'scripts'))
    try:
        import affiliate_utils as _affiliate_utils
    except Exception:
        _affiliate_utils = None

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

    # Import classes using package-style import so relative imports inside
    # the modules (e.g. `from .Twitter import Twitter`) work correctly.
    import importlib
    AffiliateMarketing = None
    Twitter = None
    try:
        # Ensure `src` is on sys.path (set at top of this script)
        mod_afm = importlib.import_module('classes.AFM')
        AffiliateMarketing = getattr(mod_afm, 'AffiliateMarketing', None)
    except Exception as e:
        print('AFM import error:', e)
        AffiliateMarketing = None
    try:
        mod_tw = importlib.import_module('classes.Twitter')
        Twitter = getattr(mod_tw, 'Twitter', None)
    except Exception as e:
        print('Twitter import error:', e)
        Twitter = None

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

                # figure affiliate tag (config.json or env)
                affiliate_tag = None
                try:
                    cfg_path = ROOT / 'config.json'
                    if cfg_path.exists():
                        cfg = json.loads(cfg_path.read_text(encoding='utf-8'))
                        affiliate_tag = cfg.get('affiliate_tag') or cfg.get('amazon_affiliate_tag') or os.environ.get('MPV2_AFFILIATE_TAG')
                except Exception:
                    affiliate_tag = os.environ.get('MPV2_AFFILIATE_TAG')

                # prefer affiliate_utils when available (adds 'tag' param only for amazon domains)
                aff_link = link
                if affiliate_tag:
                    if _affiliate_utils:
                        try:
                            aff_link = _affiliate_utils.add_affiliate_tag(link, affiliate_tag)
                        except Exception:
                            aff_link = link
                    else:
                        # fallback: naïvely append tag param if missing
                        from urllib.parse import urlparse, parse_qsl, urlencode, urlunparse
                        p = urlparse(link)
                        q = dict(parse_qsl(p.query, keep_blank_values=True))
                        if 'tag' not in q:
                            q['tag'] = affiliate_tag
                            new_q = urlencode(q, doseq=True)
                            aff_link = urlunparse((p.scheme, p.netloc, p.path, p.params, new_q, p.fragment))

                # Attach UTM params (after affiliate tag so both exist)
                utm_link = add_utm(aff_link, {
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
