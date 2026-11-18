"""Morning cron endpoint: daily quote + story image.

Runs at 01:42 UTC = 08:42 Asia/Phnom_Penh (vercel.json cron).
- send_daily(): quote image to every subscriber and to @dailynewsreporter.
- send_story(): story image to the owner, ready to post as the channel story.
  (A bot cannot post a story to a channel via the Bot API — verified
  2026-11-18 — so the image is delivered to the owner instead.)

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
            daily = sb.send_daily()
            story = sb.send_story()
        except Exception as e:
            print(f"cron-morning error: {e}", flush=True)
            self._json(500, {"ok": False, "error": str(e)})
            return
        self._json(200, {"ok": True, "daily_sent": daily, "story_sent": story})

    def do_GET(self):
        self._run()

    def do_POST(self):
        self._run()
