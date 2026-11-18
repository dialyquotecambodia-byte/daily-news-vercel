"""Single Flask entrypoint for the Daily News bot on Vercel.

Why one file: the Vercel Python runtime (build CLI 65+, Nov 2026) no
longer accepts one BaseHTTPRequestHandler per file under api/ — the
first `vercel deploy` failed at build time with "No Flask entrypoint
found in default locations" and demanded a single entrypoint. So all
four routes live in this Flask app, and vercel.json rewrites /api/* to
/api/index. The URL paths, auth rules and response shapes are unchanged
from the old per-file handlers.

Routes:
- GET  /api/webhook      health check
- POST /api/webhook      one Telegram update (optional
                         X-Telegram-Bot-Api-Secret-Token check)
- GET|POST /api/cron-morning   daily quote + story image (Bearer CRON_SECRET)
- GET|POST /api/cron-evening   evening love quote (Bearer CRON_SECRET)
- GET  /api/post-news    health check
- POST /api/post-news    post one AI news item (Bearer CRON_SECRET)

No polling loop runs on Vercel: reminders are delivered on the next
incoming update (check_reminders runs inside the webhook handler).
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from flask import Flask, jsonify, request

import bot.superbot as sb
from bot.vercel_common import cron_authorized

WEBHOOK_SECRET = os.environ.get("TELEGRAM_WEBHOOK_SECRET", "")

app = Flask(__name__)


def _err(code, message):
    return jsonify({"ok": False, "error": message}), code


@app.route("/api/webhook", methods=["GET"])
def webhook_health():
    return jsonify({"ok": True, "service": "daily-news-bot", "mode": "webhook"})


@app.route("/api/webhook", methods=["POST"])
def webhook():
    if WEBHOOK_SECRET:
        if request.headers.get("X-Telegram-Bot-Api-Secret-Token") != WEBHOOK_SECRET:
            return _err(401, "bad webhook secret")
    update = request.get_json(silent=True)
    if not isinstance(update, dict):
        return _err(400, "invalid JSON")
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
        return _err(500, str(e))
    return jsonify({"ok": True, "actions": len(actions)})


@app.route("/api/cron-morning", methods=["GET", "POST"])
def cron_morning():
    if not cron_authorized(request.headers):
        return _err(401, "unauthorized")
    try:
        daily = sb.send_daily()
        story = sb.send_story()
    except Exception as e:
        print(f"cron-morning error: {e}", flush=True)
        return _err(500, str(e))
    return jsonify({"ok": True, "daily_sent": daily, "story_sent": story})


@app.route("/api/cron-evening", methods=["GET", "POST"])
def cron_evening():
    if not cron_authorized(request.headers):
        return _err(401, "unauthorized")
    try:
        sent = sb.send_evening()
    except Exception as e:
        print(f"cron-evening error: {e}", flush=True)
        return _err(500, str(e))
    return jsonify({"ok": True, "evening_sent": sent})


@app.route("/api/post-news", methods=["GET"])
def post_news_health():
    return jsonify({"ok": True, "service": "daily-news-bot", "endpoint": "post-news"})


@app.route("/api/post-news", methods=["POST"])
def post_news():
    if not cron_authorized(request.headers):
        return _err(401, "unauthorized")
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return _err(400, "invalid JSON")
    title = (data.get("title") or "").strip()
    summary = (data.get("summary") or "").strip()
    source = (data.get("source") or "").strip()
    if not title or not summary:
        return _err(400, "title and summary are required")
    try:
        posted = sb.send_news(title, summary, source)
    except Exception as e:
        print(f"post-news error: {e}", flush=True)
        return _err(500, str(e))
    return jsonify({"ok": True, "posted": posted})


@app.route("/", methods=["GET"])
def root():
    return jsonify({"ok": True, "service": "daily-news-bot"})


@app.route("/api/<path:_any>", methods=["GET", "POST"])
def unknown(_any):
    return _err(404, "unknown endpoint")
