#!/usr/bin/env python3
"""Maintenance runner: session checks, backups, and light cleanup.

This script is safe to run from cron/launchd. It uses the project's Python venv
when invoked via the wrapper installed by the operator (see install script).
"""
import os
import subprocess
from pathlib import Path
from datetime import datetime

ROOT = Path(__file__).resolve().parent.parent
VENV_PY = ROOT / '.runtime-venv' / 'bin' / 'python'


def run(cmd):
    print(f'[{datetime.now().isoformat()}] RUN: {cmd}')
    try:
        out = subprocess.check_output(cmd, shell=True, stderr=subprocess.STDOUT, text=True)
        print(out)
    except subprocess.CalledProcessError as e:
        print('ERROR:', e.output)


def main():
    if VENV_PY.exists():
        python = str(VENV_PY)
    else:
        python = 'python3'

    # 1) Passive session check + backup when ready
    run(f"{python} scripts/check_x_session.py all --no-fail --backup-on-ready")

    # 2) Rotate small logs older than 30 days
    run(f"find logs -type f -mtime +30 -print -delete || true")

    # 3) Lightweight cleanup of .mp tmp files older than 7 days
    run(f"find ./.mp -type f -name '*.tmp' -mtime +7 -print -delete || true")


if __name__ == '__main__':
    main()
