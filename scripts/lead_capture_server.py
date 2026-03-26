#!/usr/bin/env python3
"""Simple Flask server to capture emails from the landing page and persist to `.mp/leads.json`.

Usage: python scripts/lead_capture_server.py --host 127.0.0.1 --port 8080
"""
import argparse
import json
import os
from datetime import datetime
from pathlib import Path

from flask import Flask, request, send_from_directory, redirect

ROOT = Path(__file__).resolve().parent.parent
MP_DIR = ROOT / '.mp'
LEADS_FILE = MP_DIR / 'leads.json'
LANDING_DIR = ROOT / 'assets' / 'landing'

app = Flask(__name__, static_folder=str(LANDING_DIR))


def persist_lead(email: str, utm: str):
    MP_DIR.mkdir(parents=True, exist_ok=True)
    entry = {'email': email, 'utm': utm, 'captured_at': datetime.utcnow().isoformat()}
    leads = []
    if LEADS_FILE.exists():
        try:
            leads = json.loads(LEADS_FILE.read_text(encoding='utf-8'))
        except Exception:
            leads = []
    leads.append(entry)
    LEADS_FILE.write_text(json.dumps(leads, indent=2), encoding='utf-8')


@app.route('/')
def index():
    return send_from_directory(str(LANDING_DIR), 'index.html')


@app.route('/submit', methods=['POST'])
def submit():
    email = request.form.get('email','').strip()
    utm = request.form.get('utm','').strip()
    if not email:
        return 'Missing email', 400
    try:
        persist_lead(email, utm)
    except Exception as e:
        return f'Error saving lead: {e}', 500
    return redirect('/')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=8080)
    args = parser.parse_args()
    app.run(host=args.host, port=args.port)


if __name__ == '__main__':
    main()
