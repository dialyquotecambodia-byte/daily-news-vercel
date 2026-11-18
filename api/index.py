"""Single Flask entrypoint for the Daily News bot on Vercel.

Why one file: the Vercel Python runtime (build CLI 65+, Nov 2026) no
longer accepts one BaseHTTPRequestHandler per file under api/ — the
first `vercel deploy` failed at build time with "No Flask entrypoint
found in default locations" and demanded a single entrypoint. So all
four routes live in this Flask app, and vercel.json rewrites /api/* to
/api/index. The URL paths, auth rules and response shapes are unchanged
from the old per-file handlers.

Routing quirk (verified live 2026-11-18): a Vercel rewrite hands Flask
the DESTINATION path (/api/index), not the original one, so the app
cannot tell /api/webhook from /api/cron-morning by request.path alone.
The rewrite therefore appends the original route name as a query string
(`/api/index?route=webhook`), and the catch-all below dispatches on it.
Direct /api/* paths (local tests, Flask test client) still work through
the explicit routes.

Routes:
- GET  /api/webhook      health check
- POST /api/webhook      one Telegram update (optional
                         X-Telegram-Bot-Api-Secret-Token check)
- GET|POST /api/cron-morning   daily quote + story image (Bearer CRON_SECRET)
- GET|POST /api/cron-evening   evening love quote (Bearer CRON_SECRET)
- GET  /api/post-news    health check
- POST /api/post-news    post one AI news item (Bearer CRON_SECRET)
- GET  /api/leaderboard  public XP leaderboard + top streaks (JSON)
- GET  /api/stats        public bot stats for the Mini App (JSON)

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


@app.after_request
def _cors(response):
    # The Mini App fetches /api/leaderboard from another origin.
    response.headers["Access-Control-Allow-Origin"] = "*"
    return response


def leaderboard_get():
    try:
        state = sb.load_state()
    except Exception as e:
        print(f"leaderboard error: {e}", flush=True)
        return {"ok": False, "error": str(e)}, 500
    xp = state.get("xp", {})
    ranked = sorted(xp.items(), key=lambda kv: kv[1].get("xp", 0), reverse=True)[:10]
    board = [{"name": r.get("name", "friend"), "xp": r.get("xp", 0), "level": sb.xp_level(r.get("xp", 0)), "premium": k in state.get("premium", {})} for k, r in ranked]
    streaks = state.get("streaks", {})
    top_streaks = sorted(streaks.values(), key=lambda s: s.get("count", 0), reverse=True)[:5]
    streak_board = [{"name": s.get("name", "friend"), "days": s.get("count", 0)} for s in top_streaks]
    return {"ok": True, "leaderboard": board, "streaks": streak_board}, 200


def stats_get():
    try:
        state = sb.load_state()
    except Exception as e:
        print(f"stats error: {e}", flush=True)
        return {"ok": False, "error": str(e)}, 500
    stats = state.get("stats", {})
    return {"ok": True, "stats": {
        "users": len(state.get("users", {})),
        "messages": stats.get("messages", 0),
        "quotes_sent": stats.get("quotes_sent", 0),
        "photos_sent": stats.get("photos_sent", 0),
        "quizzes_sent": stats.get("quizzes_sent", 0),
        "premium": len(state.get("premium", {})),
        "subscribers": len(state.get("subscribers", [])),
        "xp_players": len(state.get("xp", {})),
        "referrals": sum(r.get("count", 0) for r in state.get("referrals", {}).values()),
    }}, 200


def _err(code, message):
    return jsonify({"ok": False, "error": message}), code


def webhook_get():
    return {"ok": True, "service": "daily-news-bot", "mode": "webhook"}, 200


def webhook_post():
    if WEBHOOK_SECRET:
        if request.headers.get("X-Telegram-Bot-Api-Secret-Token") != WEBHOOK_SECRET:
            return {"ok": False, "error": "bad webhook secret"}, 401
    update = request.get_json(silent=True)
    if not isinstance(update, dict):
        return {"ok": False, "error": "invalid JSON"}, 400
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
        return {"ok": False, "error": str(e)}, 500
    return {"ok": True, "actions": len(actions)}, 200


def cron_morning():
    if not cron_authorized(request.headers):
        return {"ok": False, "error": "unauthorized"}, 401
    try:
        daily = sb.send_daily()
        story = sb.send_story()
        scheduled = sb.send_scheduled()
    except Exception as e:
        print(f"cron-morning error: {e}", flush=True)
        return {"ok": False, "error": str(e)}, 500
    return {"ok": True, "daily_sent": daily, "story_sent": story, "scheduled_sent": scheduled}, 200


def cron_evening():
    if not cron_authorized(request.headers):
        return {"ok": False, "error": "unauthorized"}, 401
    try:
        sent = sb.send_evening()
        scheduled = sb.send_scheduled()
    except Exception as e:
        print(f"cron-evening error: {e}", flush=True)
        return {"ok": False, "error": str(e)}, 500
    return {"ok": True, "evening_sent": sent, "scheduled_sent": scheduled}, 200


def post_news_get():
    return {"ok": True, "service": "daily-news-bot", "endpoint": "post-news"}, 200


def post_news_post():
    if not cron_authorized(request.headers):
        return {"ok": False, "error": "unauthorized"}, 401
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return {"ok": False, "error": "invalid JSON"}, 400
    title = (data.get("title") or "").strip()
    summary = (data.get("summary") or "").strip()
    source = (data.get("source") or "").strip()
    if not title or not summary:
        return {"ok": False, "error": "title and summary are required"}, 400
    try:
        posted = sb.send_news(title, summary, source)
    except Exception as e:
        print(f"post-news error: {e}", flush=True)
        return {"ok": False, "error": str(e)}, 500
    return {"ok": True, "posted": posted}, 200


ROUTES = {
    "webhook": {"GET": webhook_get, "POST": webhook_post},
    "cron-morning": {"GET": cron_morning, "POST": cron_morning},
    "cron-evening": {"GET": cron_evening, "POST": cron_evening},
    "post-news": {"GET": post_news_get, "POST": post_news_post},
    "leaderboard": {"GET": leaderboard_get},
    "stats": {"GET": stats_get},
}


def _dispatch(name):
    methods = ROUTES.get(name)
    if not methods or request.method not in methods:
        return _err(404, "unknown endpoint")
    payload, code = methods[request.method]()
    return jsonify(payload), code


@app.route("/api/webhook", methods=["GET", "POST"])
def webhook_route():
    return _dispatch("webhook")


@app.route("/api/cron-morning", methods=["GET", "POST"])
def cron_morning_route():
    return _dispatch("cron-morning")


@app.route("/api/cron-evening", methods=["GET", "POST"])
def cron_evening_route():
    return _dispatch("cron-evening")


@app.route("/api/post-news", methods=["GET", "POST"])
def post_news_route():
    return _dispatch("post-news")


@app.route("/api/leaderboard", methods=["GET"])
def leaderboard_route():
    return _dispatch("leaderboard")


@app.route("/api/stats", methods=["GET"])
def stats_route():
    return _dispatch("stats")


@app.route("/", methods=["GET"])
def root():
    return jsonify({"ok": True, "service": "daily-news-bot"})


@app.route("/api/<path:_any>", methods=["GET", "POST"])
def unknown(_any):
    # Vercel rewrites /api/* to /api/index?route=<name>; Flask sees the
    # destination path, so dispatch on the route query parameter.
    route = request.args.get("route") or ""
    if route in ROUTES:
        return _dispatch(route)
    return _err(404, "unknown endpoint")
