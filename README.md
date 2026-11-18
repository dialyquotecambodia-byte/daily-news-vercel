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

## Phase 1 — Rose-style group power (2026-11-18)

- **Federation:** `/newfed`, `/joinfed`, `/leavefed`, `/fedinfo`, `/fedlist`,
  `/fedbans`, `/fban`, `/unfban`. A fed ban bans a user in every group of the
  federation at once, and a federally banned user is banned again if they
  join any fed group. Fed state lives in KV under `federations`.
- **Notes & filters:** `/save`, `/get`, `/clear`, `/notes` (also `#name` in
  chat), `/filter`, `/filters`, `/stop`. `{name}` is replaced with the
  sender's first name.
- **Captcha:** `/captcha on` mutes new members until they tap the
  "I'm human" button (callback `verify:<gid>:<uid>`); only the member
  themself can tap it. Pending verifications live in `pending_verify`.
- **Reports:** `/report` (reply to a message) forwards the report to every
  group admin's DM. Toggle with `/settings` or `reports` in group settings.
- **Blacklists:** `/addblacklist`, `/rmblacklist`, `/blacklist`,
  `/blacklistmode delete|warn|mute`; `/addsticker`, `/rmsticker`, `/stickers`
  (reply to a sticker). Enforced on non-admin messages and stickers.
- **Settings panel:** `/settings` sends an inline-button panel
  (`set:<gid>:<key>` callbacks) for anti-link, anti-spam, captcha, reports
  and blacklist mode; the panel edits itself in place.
- **Connect:** `/connect @Group` / `/disconnect` lets a group admin run admin
  commands (including `/ban`, `/warn`, `/settings`, `/fban`) from a private
  chat. Connections live in KV under `connections`.
- **Promote/demote/purge:** `/promote`, `/demote` (via `promoteChatMember`),
  `/purge` (reply to a message, deletes up to 50 messages).
- Command menu is now 68 commands. Dry-run suite: 54 original fake updates
  + 35 Phase 1 checks, all passing (`python3 bot/superbot.py --test`).

## Phase 2 — Engagement: XP, streaks, tournaments (2026-11-18)

- **XP & levels:** level n needs `50*(n-1)^2` XP (L1 at 0, L2 at 50, L3 at
  200, L4 at 450). Earn +2 per message (60s cooldown), +10 per correct quiz
  answer, +15 for winning /guess, +10 for /scramble, +5 for winning /rps,
  +20 for /checkin, +50 for winning a tournament. Level-ups are announced
  in chat. XP lives in KV under `xp`.
- **Streaks:** `/checkin` once a day (Phnom Penh date) keeps the streak
  alive; a missed day resets it to 1. Stored under `streaks`.
- **Rank & leaderboard:** `/rank` (level, XP to next level, global rank,
  streak), `/leaderboard` (top 10 XP + top 5 streaks). Public
  `GET /api/leaderboard` returns the same board as JSON (CORS open) and
  feeds a live Leaderboard panel in the Mini App Profile tab.
- **Tournaments:** `/tournament` in a group starts 5 quiz rounds; the first
  correct answer scores a point and advances to the next question; the
  winner gets +50 XP. `/endtournament` ends early (admin or starter).
  Tournament state lives under `tournaments` / `tournament_polls`.
- **Digest:** `/digest` sends a personal digest (quote, level, streak,
  posting schedule), and the morning daily quote now carries a personal
  streak/level line for each subscriber.
- Command menu is now 74 commands. Dry-run suite: 54 original + 35 Phase 1
  + 20 Phase 2 checks, all passing (`python3 bot/superbot.py --test`).

## Phase 3 — Smart & safe: real AI chat, AI moderation, logs, backup (2026-11-18)

- **Real AI chat:** `/ask` and plain "what is …?" questions now ask a real
  AI model when the local brain doesn't know the answer. No key needed: it
  uses the free Pollinations text API. If `OPENAI_API_KEY` is set in Vercel
  (with optional `OPENAI_BASE_URL` / `OPENAI_MODEL`), it uses that
  OpenAI-compatible API instead. Failures fall back to the local brain.
- **AI moderation:** `/check` (reply to a message) asks the AI for a
  spam/scam/abuse/harmless verdict. `/aimod on` (off by default) makes the
  bot check link messages from non-admins and delete spam, scams and abuse.
  Fail-open: if the AI check fails, the message is allowed.
- **Log channel:** `/setlog @Channel` (or an id) logs bans, unban, kicks,
  mutes, 3-warning mutes, fbans, unfbans, purges, promotes and demotes, plus
  AI-moderation deletions. `/setlog off` clears it. The bot must be an admin
  in the log channel to post.
- **Backup/restore:** `/backup` sends the group settings as JSON (chunked
  under Telegram's message limit); `/restore <json>` brings them back into
  any group. Only known setting keys are restored.
- Command menu is now 79 commands. Dry-run suite: 54 original + 35 Phase 1
  + 20 Phase 2 + 15 Phase 3 checks, all passing
  (`python3 bot/superbot.py --test`).

## Phase 4 — Growth & Premium: referrals, Telegram Stars, stats (2026-11-18)

- **Referrals:** every user gets a permanent invite code and link
  (`https://t.me/MuseVishnu_bot?start=ref_DNXXXXXX`). `/referral` shows the
  link and how many friends joined; `/topreferrers` lists the top 5
  inviters. A new user who joins through a link gets +20 XP and the
  referrer gets +50 XP, counted once, never for self-referrals or repeat
  joins; the referrer is notified in a DM.
- **Premium with Telegram Stars:** `/premium` shows status, or sends a
  Stars invoice — 150 Stars, lifetime, XTR currency, empty provider token,
  payload `premium_<uid>`, with a pay button. Pre-checkout queries are
  approved; a successful payment awards a flat +50 XP welcome bonus and
  then unlocks Premium. Perks: double XP on everything and a ⭐ badge in
  `/rank`, `/leaderboard` and the Mini App leaderboard (the
  `/api/leaderboard` response carries `"premium": true/false` per player).
- **Language switch (Khmer fix):** saying "Reply in Khmer" / "Reply in
  English" stores the preference per user; the AI answers in that language.
  Plain messages in private chats now ask the AI instead of the canned
  "Thanks, you have sent me N messages" reply; groups keep the short
  thanks reply.
- **Stats endpoint:** public `GET /api/stats` returns users, messages,
  quotes sent, photos sent, quizzes sent, Premium count, subscribers, XP
  players and total referrals. The Mini App Profile tab has a "Bot stats"
  panel that fetches it.
- **Inline mode is still NOT enabled:** `getMe` reports
  `supports_inline_queries: false` (checked 2026-11-18 23:43). The owner
  still needs @BotFather → `/setinline` → @MuseVishnu_bot → placeholder.
- Command menu is now 82 commands. Dry-run suite: 54 + 35 + 20 + 15 + 6
  Khmer + 17 Phase 4 checks, all passing.

## Phase 5 — Channel & content: broadcast, scheduled posts, briefings (2026-11-18)

- **Broadcast:** `/broadcast <text>` (owner only) sends a message to every
  `/daily` subscriber. Says so clearly when there are no subscribers yet.
- **Scheduled posts:** `/schedulepost HH:MM <text>` or
  `/schedulepost tomorrow HH:MM <text>` (owner only, Asia/Phnom_Penh)
  queues a channel post; `/scheduled` lists the queue;
  `/cancelschedule <id>` removes one. Due posts are sent by the Vercel
  morning (08:42) and evening (18:42) crons via `send_scheduled()` /
  `check_scheduled_posts()`; a post is only dropped from the queue after
  it is sent, and stays queued if the channel is not set.
- **Morning briefing + evening roundup:** two VM research crons
  (`telegram-superbot-morning-briefing` ~09:42,
  `telegram-superbot-evening-roundup` ~19:42) research the top 3 verified
  AI/tech stories and post one briefing each to the channel via
  `/api/post-news`, same pattern as the 12:42 AI-news cron.
- Command menu is now 86 commands. Dry-run suite: 54 + 35 + 20 + 15 + 6
  Khmer + 17 Phase 4 + 12 Phase 5 checks, all passing.

## Phase 6 — Growth engine: giveaways, share, referral boost (2026-11-19)

- **Giveaways:** `/giveaway premium` / `/giveaway 100` / `/giveaway <custom
  prize>` (group only, admin only) posts an announcement with a "🎉 Join
  the giveaway" inline button. Tapping it adds the user once (toast:
  "You're in! Good luck! 🍀", "You're already in!" on repeats,
  "This giveaway is over." when gone). `/draw` (admin or starter) picks
  a random entrant: Premium prize unlocks lifetime Premium, an XP prize
  pays that much XP, a custom prize pays +50 XP; the giveaway is then
  cleared. `/endgiveaway` cancels without a winner. `/giveaway` with no
  args shows the live giveaway's status.
- **Share kit:** `/share` sends a ready-to-forward promo (channel, Mini
  App, bot, and the sender's referral link).
- **Referral boost:** `referral_boost_active()` — double referral rewards
  when forced on, forced off, or automatically on Saturday and Sunday
  (Phnom Penh). `/boost on|off|auto` (owner only) controls it. Boosted
  rewards: +100 XP referrer / +40 XP new user (before Premium doubling);
  standard: +50 / +20. `/referral` and the `/start` welcome show the
  current rewards and a boost line when active.
- A welcome campaign post announcing giveaways, referrals and Premium
  was posted live to @dailynewsreporter (message_id 14).
- Command menu is now 91 commands. Dry-run suite: 54 + 35 + 20 + 15 + 6
  Khmer + 17 Phase 4 + 12 Phase 5 + 18 Phase 6 checks, all passing.

## Rose Power Pack 2 (2026-11-19)

- **Granular locks:** `/lock <type>` / `/unlock <type>` for sticker, photo,
  video, audio, voice, document, gif, url, forward, bots, poll, location,
  contact, game, invite, pin, or all — `/locks` lists what's locked,
  `/locktypes` lists the types. Locked content is deleted (bots are
  banned on join). `/lock` / `/unlock` with no type still lock/unlock
  the whole group as before.
- **/tban 1d** (reply or id): temporary ban with `until_date`; expired
  temp bans are lifted automatically on the next update in the group.
- **/id**: your ID, the group's ID, or a replied user's ID.
- **/disable /quote** / **/enable** / **/disabled**: turn commands off in a
  group (deleted on sight, admins bypass; /disable, /enable, /connect
  can never be disabled).
- **/approve** / **/unapprove** / **/approved**: approved members bypass
  locks, links, flood, blacklist and force-subscribe checks.
- **/setflood 8**: how many messages in 10 seconds counts as flooding
  (default 5, range 1-20).
- **/setgoodbye Bye {name}**: message sent when a member leaves.
- **/fsub @Channel**: members must have joined the channel to chat;
  non-members' messages are removed with a join prompt (checked via
  getChatMember, fail-open).
- New group settings (`locks`, `approved`, `disabled`, `flood_limit`,
  `goodbye`, `fsub`) are covered by /backup and /restore.
- **Menu limit found live:** Telegram caps the command menu at 100 —
  the first setMyCommands with 104 commands failed 400. Five commands
  (/locktypes, /disabled, /approved, /endgiveaway, /cancelschedule) stay
  fully usable but are left out of the menu; the menu now shows 99.
- Command menu is now 99 commands (104 commands work in total).
  Dry-run suite: 54 + 35 + 20 + 15 + 6 Khmer + 17 Phase 4 + 12 Phase 5
  + 18 Phase 6 + 22 Phase 7 checks, all passing.

## Rose Power Pack 3 + the real anonymous-admin fix (2026-11-19)

The admin-check incident had a third layer. Anonymous group admins do **not** send messages with an empty `from` — Telegram sends them from **GroupAnonymousBot (id 1087968824)** with `sender_chat` == the group. The first fix (which only handled an absent `from`) therefore did not cover the user's real messages. Fixed in `is_admin` (main 08679c8f): a message counts as admin when `sender_chat == chat_id` and the sender is GroupAnonymousBot (or absent). Posting as the group's linked channel also counts (via `getChat` → `linked_chat_id`). Verified live by replaying the user's exact anonymous message shape through the webhook — `/lock photo` was accepted and the live state now shows `locks: {'sticker': True, 'photo': True}` in "Daily News chat". A sticker from a regular member was also replayed live and returned a delete action, proving the lock enforces.

Two cleanups came with the fix: `track_user` and `award_message_xp` now ignore GroupAnonymousBot, so it can no longer pollute the user count, message stats, XP or the leaderboard (its records were removed from live KV state). And `execute_actions` now wraps every action in its own try/except — one failing action (e.g. deleting an already-deleted message) can no longer crash the whole webhook and lose the state save.

New group powers in this pack:

- **Anti-raid** — `/antiraid on|off` (also in the /settings panel). When on, 5 joins within 60 seconds mute the newcomers (an admin can /unmute them). Join timestamps live in state `join_tracker`.
- **Welcome buttons** — the welcome message now carries a 📜 **Rules** button (callback `rules:<gid>` shows the group's rules, or a "no rules yet" note) and a 📱 **Daily News App** button.
- **Clean service messages** — `/cleanservice on|off` (also in /settings). When on, the "X joined" / "X left" service messages are deleted.
- **Anti-channel** — `/antichannel on|off` (also in /settings). When on, messages sent as a channel are deleted, except the group's linked channel and admins.
- **Settings panel** — anti-raid, clean service and anti-channel toggles added alongside the existing ones.

New settings defaults and backup/restore keys: `antiraid`, `cleanservice`, `antichannel` (all False by default). New commands: `/antiraid`, `/cleanservice`, `/antichannel`. To keep the Telegram menu at 99 (the cap is 100), `/setflood`, `/setgoodbye` and `/fsub` were removed from the menu — they still work when typed and are documented in /help. Total working commands: 107, menu shows 99.

Dry-run suite: 54 + 35 + 20 + 15 + 6 + 17 + 12 + 18 + 26 + 10 Phase 8 checks, all passing; webhook/cron harnesses still 25/25 + 20/20.

## Phase 7 — Khmer everywhere (2026-11-19)

- **Khmer quotes doubled**: `KHMER_QUOTES` grew from 8 to **16**.
- **/khmerquiz**: a quiz in Khmer — 10 new `KHMER_QUIZ_QUESTIONS` (capital, language, Angkor Wat, Mekong, maths, animals, leap year, Red Planet, AI). It reuses the normal quiz poll + scoring + XP, but the poll is flagged `khmer` and the correct/wrong replies are in Khmer ("ត្រឹមត្រូវ …", "មិនទាន់ត្រឹមត្រូវទេ …", level-up "ឡើងកម្រិត!"). `/khmerquiz` counts toward quiz stats like `/quiz`.
- **Mini App language toggle**: an EN / ខ្មែរ switch in the header translates the main UI labels (tabs, search, hero, TL;DR button, card and reader buttons, AI Brief, Saved, Profile) into Khmer; article content stays English. Choice persists in localStorage and defaults to Khmer when the Telegram user's language is Khmer.
- **Khmer morning briefing**: VM cron `telegram-superbot-khmer-briefing` runs daily ~10:42 Asia/Phnom_Penh, researches the top 3 AI/tech stories and posts one Khmer briefing to @dailynewsreporter via `/api/post-news` (title "🇰🇭 ព័ត៌មានពេលព្រឹក — <date>").
- Command menu is now exactly **100** (the Telegram cap — verified live); **108 commands work in total**.

Dry-run suite: 54 + 35 + 20 + 15 + 6 + 17 + 12 + 18 + 26 + 10 + 6 Phase 9 checks, all passing; webhook/cron harnesses still 25/25 + 20/20.

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
