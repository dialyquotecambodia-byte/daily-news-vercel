"""Post-news endpoint: lets the VM research cron post AI news to the channel.

POST /api/post-news with:
  Authorization: Bearer <CRON_SECRET>
  {"title": "...", "summary": "...", "source": "..."}

Calls the bot's send_news(), which posts the formatted item to
@dailynewsreporter. This replaces the VM cron's direct
`superbot.py --post-news "Title | Summary | Source"` call after cutover.

Guarded by the shared CRON_SECRET env var (same secret the Vercel cron
endpoints use). Without the right Bearer token it answers 401.
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

    def do_GET(self):
        self._json(200, {"ok": True, "service": "daily-news-bot", "endpoint": "post-news"})

    def do_POST(self):
        if not cron_authorized(self.headers):
            self._json(401, {"ok": False, "error": "unauthorized"})
            return
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""
        try:
            data = json.loads(raw.decode("utf-8"))
        except Exception:
            self._json(400, {"ok": False, "error": "invalid JSON"})
            return
        title = (data.get("title") or "").strip()
        summary = (data.get("summary") or "").strip()
        source = (data.get("source") or "").strip()
        if not title or not summary:
            self._json(400, {"ok": False, "error": "title and summary are required"})
            return
        try:
            posted = sb.send_news(title, summary, source)
        except Exception as e:
            print(f"post-news error: {e}", flush=True)
            self._json(500, {"ok": False, "error": str(e)})
            return
        self._json(200, {"ok": True, "posted": posted})
