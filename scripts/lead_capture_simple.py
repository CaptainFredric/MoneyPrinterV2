#!/usr/bin/env python3
"""Minimal lead capture server using only the standard library.

Serves `assets/landing/index.html` on GET / and accepts POST /submit form submissions.
Persists leads to `.mp/leads.json`.
"""
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import parse_qs
from pathlib import Path
import json
from datetime import datetime

ROOT = Path(__file__).resolve().parent.parent
LANDING = ROOT / 'assets' / 'landing' / 'index.html'
MP_DIR = ROOT / '.mp'
LEADS_FILE = MP_DIR / 'leads.json'


class Handler(BaseHTTPRequestHandler):
    def _serve_index(self):
        if not LANDING.exists():
            self.send_response(404)
            self.end_headers()
            return
        content = LANDING.read_bytes()
        self.send_response(200)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.send_header('Content-Length', str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def do_GET(self):
        if self.path.startswith('/'):
            self._serve_index()
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        if not self.path.startswith('/submit'):
            self.send_response(404)
            self.end_headers()
            return
        length = int(self.headers.get('Content-Length', 0))
        body = self.rfile.read(length).decode('utf-8')
        params = parse_qs(body)
        email = params.get('email', [''])[0].strip()
        utm = params.get('utm', [''])[0].strip()
        if not email:
            self.send_response(400)
            self.end_headers()
            self.wfile.write(b'Missing email')
            return

        MP_DIR.mkdir(parents=True, exist_ok=True)
        leads = []
        if LEADS_FILE.exists():
            try:
                leads = json.loads(LEADS_FILE.read_text(encoding='utf-8'))
            except Exception:
                leads = []
        leads.append({'email': email, 'utm': utm, 'captured_at': datetime.utcnow().isoformat()})
        LEADS_FILE.write_text(json.dumps(leads, indent=2), encoding='utf-8')

        # Redirect back to index
        self.send_response(302)
        self.send_header('Location', '/')
        self.end_headers()


def run(host='127.0.0.1', port=8080):
    server = HTTPServer((host, port), Handler)
    print(f'Listening on http://{host}:{port}')
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.server_close()


if __name__ == '__main__':
    run()
