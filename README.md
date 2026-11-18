# Daily News Telegram bot — Vercel edition

The live Python Telegram super bot (`@MuseVishnu_bot`) running on Vercel's
**free Hobby plan** in **webhook mode**, with state in **Vercel KV**
(Upstash Redis over HTTPS).

Vendored from the live VM bot on 2026-11-18. The live bot keeps running on
the VM until cutover (see below) — this repo does not touch it.

## Architecture

```
Telegram ──POST update──> /api/webhook ──> api/index.py (Flask) ──> bot/superbot.py
                              │  load state (KV)                   (handle_update +
                              │  run actions (Telegram)             execute_actions,
                              │  save state (KV)                    unchanged logic)
                              └── 200 {"ok": true}

Vercel Cron 01:42 UTC ──> /api/cron-morning ──> send_daily() + send_story()
Vercel Cron 11:42 UTC ──> /api/cron-evening ──> send_evening()
VM research cron ──POST──> /api/post-news ──> send_news() (12:42 news post)
```

- **Webhook, not polling.** Vercel functions are short-lived; there is no
  `poll_forever()` in production. Telegram pushes each update to
  `/api/webhook`, the function loads state from KV, answers, saves state,
  and returns 200. Each invocation is stateless.
- **State is one JSON value.** The whole bot state (subscribers, scores,
  quiz polls, reminders, group settings, invite codes, custom quotes,
  users, stats — the exact same keys as the live bot's `state.json`) is
  stored as a single JSON string under the KV key `state`. Read with
  `GET /get/state`, write with one `POST /pipeline` call.
- **Images render on Vercel.** Quote/story/banner images are drawn with
  Pillow. The fonts the VM used (`/usr/share/fonts/...`) do not exist on
  Vercel, so DejaVu Sans + Noto Sans Khmer are vendored in `bot/fonts/` as
  base64 text (`*.ttf.b64`); the bot decodes them to the temp dir on first use.
  (both freely redistributable: DejaVu license, SIL OFL). Pillow's
  manylinux wheels include raqm, so Khmer shaping works there too.
- **One Flask entrypoint, not one file per route.** `api/index.py` is a
  Flask app serving all four routes, and `vercel.json` rewrites `/api/*`
  to it. The first deploy (2026-11-18) used one stdlib
  `BaseHTTPRequestHandler` per file under `api/` — the documented pattern
  — but `vercel build` (CLI 65+) rejected it with "No Flask entrypoint
  found in default locations" and demanded a single entrypoint. Do not
  split `api/index.py` back into per-route files; the build will fail
  again. Routing quirk, verified live the same day: a Vercel rewrite
  hands Flask the *destination* path (`/api/index`), not the original
  one, so the rewrite appends the route name as a query string
  (`/api/index?route=webhook`) and the catch-all in `api/index.py`
  dispatches on it. Direct `/api/*` paths still work (local tests).

## Project layout

| Path | What it is |
|---|---|
| `bot/superbot.py` | The bot, vendored from the live VM bot. Only hosting changes: token from `TELEGRAM_BOT_TOKEN`, KV/file state backends, vendored fonts. All commands, games, menu, inline, group admin, quote images, CLI modes unchanged. |
| `bot/vercel_common.py` | Shared cron-secret check for the `api/*` endpoints. |
| `bot/fonts/` | DejaVuSans, DejaVuSans-Bold, NotoSansKhmer-Regular as base64 text (`*.ttf.b64`), decoded to the temp dir at runtime. |
| `api/index.py` | The whole HTTP layer: a Flask app with `POST /api/webhook` (one Telegram update per call; `GET` returns a health payload; optional `TELEGRAM_WEBHOOK_SECRET` check), `GET /api/cron-morning` (daily quote + story image, cron-guarded), `GET /api/cron-evening` (evening love quote, cron-guarded), `POST /api/post-news` `{"title","summary","source"}` (guarded by `CRON_SECRET`). |
| `vercel.json` | Cron schedules (UTC) + `maxDuration: 60` + the `/api/*` → `/api/index` rewrite. |
| `requirements.txt` | `Pillow==10.2.0`, `Flask==3.1.0`. |
| `tools/migrate_state.py` | One-time copy of the live `state.json` into KV. |
| `tests/test_webhook.py` | Local webhook harness (21 checks, no real token, no Telegram calls). |
| `tests/test_cron.py` | Local cron/post-news harness (15 checks, real image rendering). |

## Environment variables

| Variable | Where | Purpose |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | Vercel dashboard (Production) | Bot token from @BotFather. Required everywhere. |
| `STATE_BACKEND` | Vercel dashboard | `kv` on Vercel, `file` for local tests. |
| `KV_REST_API_URL` | Auto-injected by the KV integration | Upstash REST endpoint. |
| `KV_REST_API_TOKEN` | Auto-injected by the KV integration | Upstash REST token. |
| `KV_STATE_KEY` | Optional, default `state` | KV key holding the whole state JSON. |
| `CRON_SECRET` | Vercel dashboard (or auto-generated) | Shared secret for the cron endpoints and `/api/post-news`. Vercel Cron sends it as `Authorization: Bearer <CRON_SECRET>`. |
| `TELEGRAM_WEBHOOK_SECRET` | Optional | If set, pass the same value as `secret_token` in the `setWebhook` call; Telegram then sends it back as `X-Telegram-Bot-Api-Secret-Token` on every update and the webhook rejects anything else. |

Secrets live **only** in the Vercel dashboard (Project Settings >
Environment Variables). Never in git — `.gitignore` excludes `.env`,
`state.json` and `heartbeat.json`, and `.env.example` holds placeholders only.

## Deploy (free Hobby plan)

### 1. Push to GitHub

```bash
cd daily-news-vercel
git init
git add .
git commit -m "Daily News Telegram bot on Vercel (webhook + KV)"
git branch -M main
# create the repo first at github.com/new (name: daily-news-vercel), then:
git remote add origin https://github.com/<your-user>/daily-news-vercel.git
git push -u origin main
```

### 2. Import into Vercel

1. [vercel.com](https://vercel.com) → **Add New → Project** → **Import** the
   `daily-news-vercel` repository.
2. Framework Preset: **Other**. Root Directory: `./` (default). No build
   command is needed — Vercel installs `requirements.txt` automatically.
3. **Environment Variables** (Production): add `TELEGRAM_BOT_TOKEN` (from
   @BotFather), `STATE_BACKEND=kv`, and `CRON_SECRET` (a long random string
   you invent — the VM research cron will need it later). Optionally add
   `TELEGRAM_WEBHOOK_SECRET` (same value you will use in the `setWebhook`
   call below).
4. **Deploy.**

### 3. Create the KV store

1. Vercel dashboard → **Storage** → **Create Database** → **Upstash for
   Redis** (Vercel KV was folded into Upstash in Dec 2024; new stores come
   from this Marketplace integration).
2. Pick the database and **connect it to this project**. Vercel injects
   `KV_REST_API_URL` and `KV_REST_API_TOKEN` into the project's env vars
   automatically.
3. **Redeploy** the project (env var changes need a redeploy to take effect).

### 4. Migrate the live state into KV

Stop the VM bot from writing first is not required for the copy itself —
but do this right before cutover, and do not let the VM bot keep running
afterwards (see step 6). From a machine with the live `state.json`:

```bash
export KV_REST_API_URL=https://your-db.upstash.io
export KV_REST_API_TOKEN=AX...
python3 tools/migrate_state.py /path/to/state.json
# prints the key count and verifies the value by reading it back
```

### 5. Point Telegram at the webhook

```bash
curl -s "https://api.telegram.org/bot<TOKEN>/setWebhook" \
  -H "Content-Type: application/json" \
  -d '{"url": "https://<DEPLOYED_URL>/api/webhook", "drop_pending_updates": true, "secret_token": "<TELEGRAM_WEBHOOK_SECRET>"}'
# omit "secret_token" if you did not set TELEGRAM_WEBHOOK_SECRET
```

`<DEPLOYED_URL>` is e.g. `daily-news-vercel.vercel.app`.
`drop_pending_updates: true` discards updates queued while the VM bot was
still polling, so nothing is answered twice. Verify with:

```bash
curl -s "https://api.telegram.org/bot<TOKEN>/getWebhookInfo"
# expect: "url": "https://<DEPLOYED_URL>/api/webhook", "pending_update_count": 0
```

### 6. Cutover — stop the VM bot (do this right after step 5)

**Never run the VM polling bot and the Vercel webhook at the same time:**
both would answer the same updates.

1. Kill the VM bot by exact pid (never `pkill -f superbot.py` — it matches
   your own shell too):
   ```bash
   ps aux | grep "[s]uperbot.py"
   kill <pid>
   ```
2. Disable the VM crons: `telegram-superbot-watchdog`,
   `telegram-superbot-daily-quote`, `telegram-superbot-evening-quote`,
   `telegram-superbot-daily-story`.
3. Re-point the daily AI-news research cron: its research step stays the
   same, but its post step changes from
   `superbot.py --post-news "Title | Summary | Source"` to:
   ```bash
   curl -s -X POST "https://<DEPLOYED_URL>/api/post-news" \
     -H "Authorization: Bearer <CRON_SECRET>" \
     -H "Content-Type: application/json" \
     -d '{"title": "…", "summary": "…", "source": "…"}'
   ```

### 7. Verify

- `GET https://<DEPLOYED_URL>/api/webhook` → `{"ok": true, ...}`.
- In Telegram: `/start`, `/quote`, `/quiz`, `/menu`, `/dice` to
  @MuseVishnu_bot — all should answer within a couple of seconds.
- Vercel dashboard → Deployments → Functions → check the `api/index`
  logs for errors.
- Rollback (if anything is wrong): `deleteWebhook` (or
  `setWebhook` with an empty URL), then restart the VM bot with the
  watchdog. The VM bot's local `state.json` is untouched by this project.

## Cron schedules (UTC)

Asia/Phnom_Penh is UTC+7 all year, so:

| Job | Local time | UTC (in `vercel.json`) |
|---|---|---|
| Morning quote + story image | 08:42 | `42 1 * * *` |
| Evening love quote | 18:42 | `42 11 * * *` |

The 12:42 AI-news post is **not** a Vercel cron — it is researched by the
existing VM cron and posted through `/api/post-news` (step 6.3).

## Local testing (no real token, no Telegram calls)

```bash
python3 bot/superbot.py --test          # the bot's own dry-run suite (must end TEST OK)
python3 tests/test_webhook.py           # 25 checks: fake Telegram POSTs against the Flask app (api/index.py), incl. the /api/index?route= rewrite path
python3 tests/test_cron.py              # 20 checks: real morning/evening/news flows, mocked transport, incl. the rewrite path
python3 -m py_compile bot/*.py api/*.py tools/*.py tests/*.py
```

The harnesses monkeypatch the bot's Telegram calls, use a temp `state.json`
(`STATE_BACKEND=file`), and render real quote/Khmer images with the
vendored fonts. To try the KV backend locally, set `STATE_BACKEND=kv` plus
`KV_REST_API_URL` / `KV_REST_API_TOKEN` in a local `.env` (never committed).

## Free-tier limits that matter

- **Vercel Hobby functions:** default max 10s per invocation; `vercel.json`
  raises this to **60s**, the Hobby maximum. The webhook answers after
  doing its Telegram calls synchronously, normally in 1–5s. Image
  generation adds ~1–2s per photo.
- **Vercel Hobby crons:** each cron job may run **at most once per day**.
  This project has two daily crons (morning, evening) — both allowed.
  That is also why reminders and the 12:42 news post cannot be Vercel
  crons: the news post stays on the VM cron via `/api/post-news`.
- **Upstash free tier** (checked Aug 2026; re-check before depending on
  it): 500,000 commands/month, 256 MB storage, 10 GB bandwidth. This bot
  uses ~2 KV commands per webhook update (one GET, one SET) plus a few per
  cron run — far below the limit at this traffic.
- **Cold starts:** the first request after idle takes a few seconds
  (Pillow import). Telegram tolerates this and retries if a webhook call
  ever times out.

## What could NOT be ported 1:1 (limitations)

- **No polling loop.** By design — Telegram pushes updates to the webhook.
  The CLI modes (`--daily`, `--evening`, `--story`, `--post-news`,
  `--setup-menu`, `--setup-channel`, `--test`) still work locally and from
  the VM; they are not Vercel functions.
- **Reminders fire late.** `/remind` stores the reminder in KV, but there
  is no timer on Vercel, so due reminders are delivered on the **next
  inbound update** (the webhook runs `check_reminders()` first). A 10-minute
  reminder can arrive late if nobody messages the bot in between. There is
  no Hobby cron frequent enough to fix this (once per day max).
- **Anti-spam flood mute does not trigger.** The flood tracker is in-memory
  and resets on every invocation, so it can never count 5 messages in 10s.
  Anti-link (stateless) and the 3-strike `/warn` mute (state-backed) still
  work.
- **Quote no-repeat memory resets.** The last-5-quotes list is in-memory,
  so repeats across invocations are purely random (same as a fresh process).
- **State race.** Two webhook invocations at the same instant can both read
  the same KV state and the second save can overwrite the first's changes
  (e.g. two quiz answers in the same second). Rare at this traffic; a
  proper fix would need a KV lock.
- **Stories still cannot auto-post.** `postStory` needs a business account
  connection — unchanged from the VM bot. The morning cron still delivers
  the story image to the owner to post in two taps.
- **`/remind` and `/ask` unknown-question queue** keep working (they are
  state-backed), and group admin commands work unchanged (they are
  stateless API calls + state-backed settings).
