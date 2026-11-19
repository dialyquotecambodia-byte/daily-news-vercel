#!/usr/bin/env python3
"""Local webhook harness: simulates Telegram POSTs against api/index.py
(the Flask app) using Flask's test client — no real HTTP server.

No real token, no network calls to Telegram. The bot's api_call and
send_photo are monkeypatched to record calls and return canned responses;
quote images are really rendered with Pillow + the vendored fonts.

Run from the project root:
    python3 tests/test_webhook.py

Exits 0 when every case passes, 1 otherwise.
"""
import importlib.util
import json
import os
import sys
import tempfile

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

TMP = tempfile.mkdtemp(prefix="daily-news-vercel-test-")
STATE_PATH = os.path.join(TMP, "state.json")

# Env must be set BEFORE importing the bot (it reads them at import time).
os.environ["TELEGRAM_BOT_TOKEN"] = "test-token-not-real"
os.environ["STATE_BACKEND"] = "file"
os.environ["STATE_PATH"] = STATE_PATH
os.environ["TELEGRAM_WEBHOOK_SECRET"] = "test-secret"
os.environ["CRON_SECRET"] = "test-secret"

import bot.superbot as sb  # noqa: E402

# --- Monkeypatch the Telegram API surface ------------------------------------
CALLS = []  # (method, payload) for api_call, ("sendPhoto", chat_id, caption, existed) for photos


def fake_api_call(method, payload=None, timeout=35):
    CALLS.append((method, payload))
    if method == "sendMessage":
        return {"ok": True, "result": {"message_id": 1001}}
    if method == "sendPoll":
        return {"ok": True, "result": {"poll": {"id": "poll-harness-1"}}}
    if method == "getChatAdministrators":
        return {"ok": True, "result": []}
    return {"ok": True}


def fake_send_photo(chat_id, path, caption=""):
    CALLS.append(("sendPhoto", chat_id, caption, os.path.exists(path)))
    return {"ok": True, "result": {"message_id": 1002}}


sb.api_call = fake_api_call
sb.send_photo = fake_send_photo

# --- Load the real Flask app -------------------------------------------------
spec = importlib.util.spec_from_file_location(
    "flask_app", os.path.join(PROJECT_ROOT, "api", "index.py")
)
app_mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(app_mod)
client = app_mod.app.test_client()

PASSED = []
FAILED = []


def check(name, ok, detail=""):
    (PASSED if ok else FAILED).append(name)
    print(f"  {'PASS' if ok else 'FAIL'} {name}" + (f" — {detail}" if detail and not ok else ""))


def post_update(update, headers=None):
    h = {"X-Telegram-Bot-Api-Secret-Token": "test-secret"}
    h.update(headers or {})
    resp = client.post("/api/webhook", json=update, headers=h)
    return resp.status_code, resp.get_json(silent=True) or {}


def post_raw(data, headers=None):
    resp = client.post("/api/webhook", data=data,
                       content_type="application/json", headers=headers or {})
    return resp.status_code, resp.get_json(silent=True) or {}


def sends(method="sendMessage"):
    return [p for m, p in CALLS if m == method]


def read_state():
    with open(STATE_PATH, encoding="utf-8") as f:
        return json.load(f)


def msg(chat_id=111, text="", update_id=1, from_id=111, name="Dailyquote"):
    return {
        "update_id": update_id,
        "message": {
            "message_id": update_id,
            "chat": {"id": chat_id, "type": "private"},
            "from": {"id": from_id, "first_name": name},
            "text": text,
        },
    }


print("— webhook harness —")
print(f"  state file: {STATE_PATH}")

# 0. Health check
resp = client.get("/api/webhook")
body = resp.get_json(silent=True) or {}
check("GET /api/webhook returns 200 health", resp.status_code == 200 and body.get("ok") is True)

# 1. /start
CALLS.clear()
status, body = post_update(msg(text="/start", update_id=101))
check("/start returns 200", status == 200 and body.get("ok") is True, f"{status} {body}")
check("/start sends welcome with inline buttons",
      any("Welcome to Daily News" in (p.get("text") or "") and p.get("reply_markup") for p in sends()))

# 2. /quote
CALLS.clear()
status, body = post_update(msg(text="/quote", update_id=102))
quote_texts = [p.get("text") for p in sends()]
check("/quote returns a quote from the library",
      status == 200 and any(t in sb.DAILY_QUOTES for t in quote_texts), f"{quote_texts}")

# 3. /guess then the exact number -> Correct!
CALLS.clear()
status, _ = post_update(msg(text="/guess", update_id=103))
check("/guess starts a game", any("thinking of a number" in (p.get("text") or "") for p in sends()))
secret = read_state()["guess_games"]["111"]["number"]
CALLS.clear()
status, _ = post_update(msg(text=str(secret), update_id=104))
check("/guess correct number wins",
      any("Correct!" in (p.get("text") or "") for p in sends()))
check("guess game cleared from state after win", "111" not in read_state().get("guess_games", {}))

# 4. /rps + callback
CALLS.clear()
status, _ = post_update(msg(text="/rps", update_id=105))
check("/rps shows the three buttons",
      any("rps:rock" in json.dumps(p.get("reply_markup") or {}) for p in sends()))
CALLS.clear()
status, body = post_update({
    "update_id": 106,
    "callback_query": {"id": "cb-harness", "data": "rps:rock",
                       "message": {"chat": {"id": 111, "type": "private"}}},
})
check("rps callback answers the query",
      any(m == "answerCallbackQuery" for m, _ in CALLS))
check("rps callback sends the result",
      any(" vs " in (p.get("text") or "") for p in sends()), f"{sends()}")

# 5. /menu + button tap
CALLS.clear()
status, _ = post_update(msg(text="/menu", update_id=107))
check("/menu sends a keyboard with the app button",
      any("web_app" in json.dumps(p.get("reply_markup") or {}) for p in sends()))
CALLS.clear()
status, _ = post_update(msg(text="💬 Quote", update_id=108))
check("menu button tap returns a quote",
      any((p.get("text") or "") in sb.DAILY_QUOTES for p in sends()))

# 6. /quoteimg and /khmerimg render real images with the vendored fonts
CALLS.clear()
status, _ = post_update(msg(text="/quoteimg", update_id=109))
photos = [c for c in CALLS if c[0] == "sendPhoto"]
check("/quoteimg renders and sends a photo",
      status == 200 and len(photos) == 1 and photos[0][3] is True, f"{photos}")
CALLS.clear()
status, _ = post_update(msg(text="/khmerimg", update_id=110))
photos = [c for c in CALLS if c[0] == "sendPhoto"]
check("/khmerimg renders Khmer with the vendored font",
      status == 200 and len(photos) == 1 and photos[0][3] is True, f"{photos}")

# 7. /quiz round-trip + poll answer scoring, persisted in state
CALLS.clear()
status, _ = post_update(msg(text="/quiz", update_id=111))
check("/quiz sends a quiz poll",
      any(m == "sendPoll" for m, _ in CALLS))
check("quiz poll recorded in state",
      "poll-harness-1" in read_state().get("quiz_polls", {}))
CALLS.clear()
status, _ = post_update({
    "update_id": 112,
    "poll_answer": {"poll_id": "poll-harness-1",
                    "user": {"id": 999, "first_name": "Test"},
                    "option_ids": [0]},
})
check("poll answer scores and replies",
      any("Your score:" in (p.get("text") or "") for p in sends()))
check("score persisted in state",
      read_state().get("scores", {}).get("999", {}).get("total") == 1)

# 8. /daily subscribes, persisted across requests
CALLS.clear()
status, _ = post_update(msg(text="/daily", update_id=113))
check("/daily subscribes the chat", 111 in read_state().get("subscribers", []))

# 9. Guards: bad secret -> 401, invalid JSON -> 400
status, _ = post_raw(json.dumps(msg(text="/quote", update_id=114)).encode(),
                     headers={"X-Telegram-Bot-Api-Secret-Token": "wrong-secret"})
check("wrong webhook secret is rejected", status == 401, f"got {status}")
status, _ = post_raw(b"{not json",
                     headers={"X-Telegram-Bot-Api-Secret-Token": "test-secret"})
check("invalid JSON is rejected", status == 400, f"got {status}")

# 10. Vercel rewrite path: /api/index?route=<name> must dispatch the same way
resp = client.get("/api/index?route=webhook")
check("rewrite GET /api/index?route=webhook returns health",
      resp.status_code == 200 and (resp.get_json(silent=True) or {}).get("ok") is True)
CALLS.clear()
resp = client.post("/api/index?route=webhook", json=msg(text="/quote", update_id=116),
                   headers={"X-Telegram-Bot-Api-Secret-Token": "test-secret"})
check("rewrite POST /api/index?route=webhook returns a quote",
      resp.status_code == 200 and any((p.get("text") or "") in sb.DAILY_QUOTES for p in sends()),
      f"{resp.status_code} {resp.get_json(silent=True)}")
resp = client.get("/api/index")
check("GET /api/index without a route is 404", resp.status_code == 404)
resp = client.get("/api/bogus")
check("GET /api/bogus is 404", resp.status_code == 404)

# 11. /api/feed: public GET, CRON_SECRET-guarded POST, validation
resp = client.get("/api/feed")
body = resp.get_json(silent=True) or {}
check("GET /api/feed returns an empty feed at first",
      resp.status_code == 200 and body.get("ok") is True and body.get("feed", {}).get("articles") == [])
feed_payload = {"date": "2026-11-19", "articles": [
    {"category": "AI", "headline": "Test AI story", "publisher": "Test News", "time": "1h ago", "read_time": 3,
     "tldr": ["One.", "Two.", "Three."], "body": ["P1.", "P2.", "P3.", "P4."]},
]}
resp = client.post("/api/feed", json=feed_payload)
check("POST /api/feed without auth is 401", resp.status_code == 401, f"got {resp.status_code}")
resp = client.post("/api/feed", json=feed_payload,
                   headers={"Authorization": "Bearer test-secret"})
body = resp.get_json(silent=True) or {}
check("POST /api/feed with the cron secret saves it",
      resp.status_code == 200 and body.get("ok") is True and body.get("articles") == 1, f"{resp.status_code} {body}")
resp = client.get("/api/feed")
body = resp.get_json(silent=True) or {}
check("GET /api/feed returns the saved articles",
      body.get("feed", {}).get("date") == "2026-11-19" and len(body.get("feed", {}).get("articles", [])) == 1)
bad_payload = {"date": "2026-11-19", "articles": [
    {"category": "AI", "headline": "Bad story", "tldr": ["Only one bullet."], "body": ["P1."]},
]}
resp = client.post("/api/feed", json=bad_payload,
                   headers={"Authorization": "Bearer test-secret"})
check("POST /api/feed rejects articles without 3 TL;DR bullets and 4 paragraphs",
      resp.status_code == 400, f"got {resp.status_code}")

print(f"\n{len(PASSED)} passed, {len(FAILED)} failed")
if FAILED:
    print("failed:", FAILED)
    raise SystemExit(1)
