#!/usr/bin/env python3
"""Local cron/post-news harness: exercises the real send_daily / send_story /
send_evening / send_news flows through api/index.py (the Flask app) using
Flask's test client — no real HTTP server.

No real token, no network calls to Telegram. The bot's api_call and
send_photo are monkeypatched to record calls and return canned responses;
quote/story images are really rendered with Pillow + the vendored fonts.

Run from the project root:
    python3 tests/test_cron.py

Exits 0 when every case passes, 1 otherwise.
"""
import importlib.util
import json
import os
import sys
import tempfile

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

TMP = tempfile.mkdtemp(prefix="daily-news-vercel-cron-")
STATE_PATH = os.path.join(TMP, "state.json")

os.environ["TELEGRAM_BOT_TOKEN"] = "test-token-not-real"
os.environ["STATE_BACKEND"] = "file"
os.environ["STATE_PATH"] = STATE_PATH
os.environ["CRON_SECRET"] = "test-secret"

import bot.superbot as sb  # noqa: E402

CALLS = []


def fake_api_call(method, payload=None, timeout=35):
    CALLS.append((method, payload))
    return {"ok": True, "result": {"message_id": 2001}}


def fake_send_photo(chat_id, path, caption=""):
    CALLS.append(("sendPhoto", chat_id, caption, os.path.exists(path)))
    return {"ok": True, "result": {"message_id": 2002}}


sb.api_call = fake_api_call
sb.send_photo = fake_send_photo

# Seed state: one subscriber + the channel, nothing sent today yet.
with open(STATE_PATH, "w", encoding="utf-8") as f:
    json.dump({"channel_id": "@dailynewsreporter", "subscribers": [111],
               "last_daily": "", "last_evening": "", "last_story": ""}, f)

# --- Load the real Flask app -------------------------------------------------
spec = importlib.util.spec_from_file_location(
    "flask_app", os.path.join(PROJECT_ROOT, "api", "index.py")
)
app_mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(app_mod)
client = app_mod.app.test_client()


def request(path, method="GET", data=None, auth=True):
    headers = {}
    if auth:
        headers["Authorization"] = "Bearer test-secret"
    if method == "GET":
        resp = client.get(path, headers=headers)
    else:
        resp = client.post(path, data=data, content_type="application/json",
                           headers=headers)
    return resp.status_code, resp.get_json(silent=True) or {}


def read_state():
    with open(STATE_PATH, encoding="utf-8") as f:
        return json.load(f)


PASSED, FAILED = [], []


def check(name, ok, detail=""):
    (PASSED if ok else FAILED).append(name)
    print(f"  {'PASS' if ok else 'FAIL'} {name}" + (f" — {detail}" if detail and not ok else ""))


print("— cron + post-news harness —")

# 1. Morning cron: daily quote (subscriber + channel) + story (owner).
status, body = request("/api/cron-morning", auth=False)
check("cron-morning without secret is rejected", status == 401, f"got {status}")
CALLS.clear()
status, body = request("/api/cron-morning")
check("cron-morning with secret returns 200",
      status == 200 and body.get("ok") is True, f"{status} {body}")
check("cron-morning sent daily to 2 targets", body.get("daily_sent") == 2, f"{body}")
check("cron-morning sent the story to the owner", body.get("story_sent") == 1, f"{body}")
photos = [c for c in CALLS if c[0] == "sendPhoto"]
check("morning cron rendered and sent 3 photos (daily x2 + story)",
      len(photos) == 3 and all(p[3] for p in photos), f"{len(photos)} photos")
st = read_state()
check("last_daily and last_story recorded in state",
      st.get("last_daily") != "" and st.get("last_story") != "")

# Second morning run the same day must be a no-op (idempotency).
status, body = request("/api/cron-morning")
check("second morning run same day sends nothing (idempotent)",
      body.get("daily_sent") == 0 and body.get("story_sent") == 0, f"{body}")

# 2. Evening cron: love quote to the channel only.
CALLS.clear()
status, body = request("/api/cron-evening")
check("cron-evening with secret returns 200",
      status == 200 and body.get("ok") is True, f"{status} {body}")
check("cron-evening sent 1 evening quote", body.get("evening_sent") == 1, f"{body}")
check("evening photo went to the channel",
      any(c[0] == "sendPhoto" and c[1] == "@dailynewsreporter" for c in CALLS))
check("last_evening recorded in state", read_state().get("last_evening") != "")

# 3. Post-news endpoint.
status, _ = request("/api/post-news", method="POST",
                    data=json.dumps({"title": "T", "summary": "S"}).encode(), auth=False)
check("post-news without secret is rejected", status == 401, f"got {status}")
status, body = request("/api/post-news", method="POST",
                       data=json.dumps({"title": "", "summary": "S"}).encode())
check("post-news without a title is rejected", status == 400, f"got {status}")
CALLS.clear()
status, body = request("/api/post-news", method="POST", data=json.dumps({
    "title": "Test AI news", "summary": "Something happened.",
    "source": "Test Source"}).encode())
check("post-news with secret posts to the channel",
      status == 200 and body.get("posted") == 1, f"{status} {body}")
check("news caption reached sendMessage",
      any(m == "sendMessage" and "Test AI news" in (p.get("text") or "")
          and "Test Source" in (p.get("text") or "") for m, p in CALLS))

# 4. Vercel rewrite path: /api/index?route=<name> must dispatch the same way.
status, body = request("/api/index?route=cron-morning")
check("rewrite GET /api/index?route=cron-morning returns 200",
      status == 200 and body.get("ok") is True, f"{status} {body}")
status, body = request("/api/index?route=cron-evening")
check("rewrite GET /api/index?route=cron-evening returns 200",
      status == 200 and body.get("ok") is True, f"{status} {body}")
CALLS.clear()
status, body = request("/api/index?route=post-news", method="POST", data=json.dumps({
    "title": "Rewrite news", "summary": "Via the rewrite path.",
    "source": "Harness"}).encode())
check("rewrite POST /api/index?route=post-news posts to the channel",
      status == 200 and body.get("posted") == 1, f"{status} {body}")
status, body = request("/api/index?route=bogus")
check("rewrite with an unknown route is 404", status == 404, f"got {status}")
status, body = request("/api/index")
check("rewrite target without a route is 404", status == 404, f"got {status}")

print(f"\n{len(PASSED)} passed, {len(FAILED)} failed")
if FAILED:
    print("failed:", FAILED)
    raise SystemExit(1)
