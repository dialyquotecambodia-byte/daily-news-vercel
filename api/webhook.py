"""Telegram webhook endpoint for the Daily News bot (@MuseVishnu_bot).

Vercel Python runtime: one BaseHTTPRequestHandler subclass named `handler`
per file under api/. Telegram POSTs each update here; we load state from
KV, run the bot's handle_update + execute_actions, save state, and answer
200 fast. No polling loop runs on Vercel.

Optional hardening: set TELEGRAM_WEBHOOK_SECRET and pass the same value as
`secret_token` in the setWebhook call — Telegram then sends it back as the
X-Telegram-Bot-Api-Secret-Token header on every update, and requests
without it are rejected with 401.

GET returns a small health payload so the deployment can be verified
without sending a Telegram update.
"""
import json
import os
import sys
from http.server import BaseHTTPRequestHandler

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import bot.superbot as sb

WEBHOOK_SECRET = os.environ.get("TELEGRAM_WEBHOOK_SECRET", "")


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
        self._json(200, {"ok": True, "service": "daily-news-bot", "mode": "webhook"})

    def do_POST(self):
        if WEBHOOK_SECRET:
            if self.headers.get("X-Telegram-Bot-Api-Secret-Token") != WEBHOOK_SECRET:
                self._json(401, {"ok": False, "error": "bad webhook secret"})
                return
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""
        try:
            update = json.loads(raw.decode("utf-8"))
        except Exception:
            self._json(400, {"ok": False, "error": "invalid JSON"})
            return
        try:
            state = sb.load_state()
            # Reminders have no timer on Vercel (no polling loop), so due
            # reminders are delivered on the next incoming update instead.
            sb.check_reminders(state)
            actions = sb.handle_update(update, state)
            sb.execute_actions(actions, state)
            sb.save_state(state)
        except Exception as e:
            print(f"webhook error: {e}", flush=True)
            self._json(500, {"ok": False, "error": str(e)})
            return
        self._json(200, {"ok": True, "actions": len(actions)})
