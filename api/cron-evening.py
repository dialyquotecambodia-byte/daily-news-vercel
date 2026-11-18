"""Evening cron endpoint: evening love quote to the channel.

Runs at 11:42 UTC = 18:42 Asia/Phnom_Penh (vercel.json cron).
send_evening() posts a love quote image to @dailynewsreporter.

Guarded by Vercel's cron secret: Vercel Cron sends
`Authorization: Bearer <CRON_SECRET>`. GET and POST are both accepted
because Vercel Cron issues GET requests.
"""
import json
import os
import sys
from http.server import BaseHTTPRequestHandler

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import bot.superbot as sb
from bot.vercel_common import cron_authorized


class handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def _json(self, code, payload):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _run(self):
        if not cron_authorized(self.headers):
            self._json(401, {"ok": False, "error": "unauthorized"})
            return
        try:
            sent = sb.send_evening()
        except Exception as e:
            print(f"cron-evening error: {e}", flush=True)
            self._json(500, {"ok": False, "error": str(e)})
            return
        self._json(200, {"ok": True, "evening_sent": sent})

    def do_GET(self):
        self._run()

    def do_POST(self):
        self._run()
