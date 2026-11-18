#!/usr/bin/env python3
"""All-in-one Telegram super bot for @MuseVishnu_bot.

Vercel edition (vendored from the live VM bot, 2026-11-18):
- Token comes from the TELEGRAM_BOT_TOKEN environment variable.
- State lives in Vercel KV (Upstash REST) when STATE_BACKEND=kv,
  or in a local state.json file when STATE_BACKEND=file.
- Fonts are vendored in bot/fonts/ so quote images render on Vercel.
- Webhook mode: Vercel calls api/webhook.py once per update; there
  is no polling loop in production. The CLI modes (--daily,
  --evening, --story, --post-news, --setup-menu, --setup-channel,
  --test) still work locally and from the VM crons.

Step 1 quote images: /quoteimg, /loveimg, /friendimg send a 1080x1080
quote image with sendPhoto.
Step 2 channel auto-post: /setchannel, /channel, /postquote. The daily
sender also posts to the channel when one is set.
Step 3 games and polls: /quiz sends a quiz poll and tracks scores,
/poll <question> | <option1> | <option2> makes a poll, /score shows scores.
Step 4 reminders and smarter chat: /remind <10m|1h> <text>, /reminders,
/joke, /motivate, inline buttons on /start, and intent replies.

Commands:
  /start, /help, /about, /quote, /love, /friend, /khmer, /quoteimg, /loveimg, /friendimg, /khmerimg
  /daily, /stopdaily, /time, /echo, /rules, /info
  /setchannel, /channel, /postquote [love|friend|motivate|daily]
  /quiz, /poll, /score
  /remind, /reminders, /joke, /motivate
  /ask, /stats, /mystats, /top

Professional branding: Daily Quote, channel @dailynewsreporter.
Morning daily quote 8:42am, evening love quote 6pm.

Run:
  python3 superbot.py            # poll forever
  python3 superbot.py --daily    # send one daily quote to subscribers and channel, then exit
  python3 superbot.py --evening  # send one evening love quote to the channel, then exit
  python3 superbot.py --test     # dry-run fake updates, no API calls

State lives in Vercel KV (STATE_BACKEND=kv) or ./state.json (STATE_BACKEND=file).
"""
import argparse
import ast
import base64
import datetime
import json
import operator
import os
import random
import re
import sys
import tempfile
import time
import urllib.error
import urllib.request
import uuid

# --- Vercel hosting: configuration -------------------------------------------
# Project root is the parent of bot/ (this file lives in bot/superbot.py).
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# File backend path (local testing). Override with the STATE_PATH env var.
STATE_PATH = os.environ.get("STATE_PATH") or os.path.join(BASE_DIR, "state.json")
HEARTBEAT_PATH = os.path.join(BASE_DIR, "heartbeat.json")
# Fonts are vendored in bot/fonts/ so image rendering works on Vercel,
# where the VM's /usr/share/fonts paths do not exist.
FONT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fonts")
# State backend: "file" (local state.json) or "kv" (Vercel KV / Upstash REST).
STATE_BACKEND = os.environ.get("STATE_BACKEND", "file").strip().lower()
KV_STATE_KEY = os.environ.get("KV_STATE_KEY", "state")


def read_json_response(response):
    return json.loads(response.read().decode("utf-8"))


def get_token():
    """Bot token from the environment. Never from a file or a credential helper."""
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is not set")
    return token


_FONT_CACHE = {}


def font_file(name):
    """First existing copy of a font: vendored fonts/ first, then system fonts.

    The repo stores fonts as base64 text (bot/fonts/*.ttf.b64) because the
    GitHub push path only accepts UTF-8 text. When no .ttf file exists, the
    matching .b64 file is decoded once into the temp dir and that copy is used.
    """
    if name in _FONT_CACHE:
        return _FONT_CACHE[name]
    candidates = [
        os.path.join(FONT_DIR, name),
        f"/usr/share/fonts/truetype/dejavu/{name}",
        f"/usr/share/fonts/truetype/noto/{name}",
    ]
    for path in candidates:
        if os.path.exists(path):
            _FONT_CACHE[name] = path
            return path
    b64_path = os.path.join(FONT_DIR, name + ".b64")
    if os.path.exists(b64_path):
        cache_dir = os.path.join(tempfile.gettempdir(), "daily-news-fonts")
        os.makedirs(cache_dir, exist_ok=True)
        out = os.path.join(cache_dir, name)
        if not os.path.exists(out):
            with open(b64_path, "rb") as f:
                data = base64.b64decode(f.read())
            with open(out, "wb") as f:
                f.write(data)
        _FONT_CACHE[name] = out
        return out
    return candidates[0]


def write_heartbeat(state):
    try:
        data = {
            "pid": os.getpid(),
            "time": int(time.time()),
            "time_iso": phnom_penh_now().isoformat(),
            "offset": state.get("offset", 0),
            "subscribers": len(state.get("subscribers", [])),
        }
        tmp = HEARTBEAT_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp, HEARTBEAT_PATH)
    except Exception as e:
        print(f"heartbeat write failed: {e}", flush=True)

LOVE_QUOTES = [
    "Love isn't about finding someone perfect, it's about choosing each other, every day.",
    "The best kind of love is the one that feels like friendship, and the best kind of friendship feels like love.",
    "Distance means nothing when someone means everything.",
    "Love is not what you say, it's what you do, every day, in the small things.",
    "A true love stays, even when it's hard to.",
    "Love grows when you water it with patience, honesty and time.",
    "The right person will make you feel calm, not confused.",
    "Love is not about how many days you have been together, it is about how much you care each day.",
    "A loving heart is the truest kind of wisdom.",
    "You don't find love, you build it, one kind day at a time.",
    "Home is not a place. It is a person who makes you feel safe.",
    "Real love listens, even when the words are hard to hear.",
    "Love makes ordinary days feel like something special.",
    "The best love is the one that makes you a better person.",
    "To love someone is to see all of them, and choose them anyway.",
]
FRIEND_QUOTES = [
    "A true friend doesn't just stand beside you in the good times, they stay when it's hard to.",
    "Friends are the family we get to choose, and the home we find in each other.",
    "A real friend is someone who knows you and still likes you.",
    "Friendship is the quiet kind of love that never asks for anything back.",
    "Good friends make hard days lighter and good days brighter.",
    "A friend is someone who knows the song in your heart and can sing it back when you forget the words.",
    "True friends are never apart, maybe in distance but never in heart.",
    "A friend who understands your tears is worth more than many friends who only know your smile.",
    "Good friends are like stars. You don't always see them, but you know they are always there.",
    "Friendship is born when one person says to another: What! You too? I thought I was the only one.",
    "A loyal friend laughs at your jokes even when they are not so good, and sympathises with your problems even when they are not so bad.",
    "Friends show their love in times of trouble, not in happiness.",
    "A single rose can be my garden, a single friend my world.",
    "There is nothing on this earth more to be prized than true friendship.",
    "A friend is one who overlooks your broken fence and admires the flowers in your garden.",
]
DAILY_QUOTES = LOVE_QUOTES + FRIEND_QUOTES + [
    "Today is a new page. Write something kind on it.",
    "Small steps every day are still steps forward.",
    "Be the reason someone smiles today.",
    "You don't have to be perfect to be loved, and you don't have to be loud to be heard.",
    "Start where you are. Use what you have. Do what you can.",
    "The sun will rise again tomorrow, and so will you.",
    "Do one thing today that your future self will thank you for.",
    "Kindness is free. Sprinkle it everywhere.",
    "Every day is a chance to begin again.",
    "You are capable of more than you know.",
    "Believe in yourself, even when no one else does.",
    "A little progress each day adds up to big results.",
    "Choose joy, even on the hard days.",
    "Your only limit is the story you tell yourself.",
    "Today, be the light someone else needs.",
]
KHMER_QUOTES = [
    "សេចក្តីស្រឡាញ់ពិត មិនមែនជាការស្វែងរកមនុស្សល្អឥតខ្ចោះទេ គឺជាការជ្រើសរើសគ្នាទៅវិញទៅមកជារៀងរាល់ថ្ងៃ។",
    "មិត្តពិត គឺជាគ្រួសារដែលយើងជ្រើសរើសដោយខ្លួនឯង។",
    "ថ្ងៃនេះជាទំព័រថ្មី។ សរសេរអ្វីដែលល្អនៅលើវា។",
    "ជំហានតូចៗរាល់ថ្ងៃ ក៏នៅតែជាជំហានទៅមុខដែរ។",
    "អ្នកខ្លាំងជាងអ្វីដែលអ្នកគិត។",
    "ភាពសប្បុរសគឺឥតគិតថ្លៃ។ ចែករំលែកវាគ្រប់ទីកន្លែង។",
    "មិត្តល្អធ្វើឲ្យថ្ងៃលំបាកកាន់តែស្រាល និងថ្ងៃល្អកាន់តែភ្លឺ។",
    "ចាប់ផ្តើមពីកន្លែងដែលអ្នកនៅ។ ប្រើអ្វីដែលអ្នកមាន។ ធ្វើអ្វីដែលអ្នកអាចធ្វើបាន។",
]
JOKES = [
    "Why did the scarecrow win an award? Because he was outstanding in his field.",
    "I told my computer I needed a break, and now it won't stop sending me KitKat ads.",
    "Why don't scientists trust atoms? Because they make up everything.",
    "What do you call a fish without eyes? A fsh.",
]
MOTIVATION = [
    "You are stronger than you think, and today is proof you kept going.",
    "Don't wait for the perfect moment. Take this moment and make it perfect.",
    "One kind word can change someone's whole day. Start with yours.",
]
QUIZ_QUESTIONS = [
    {"q": "What is the capital of Cambodia?", "options": ["Phnom Penh", "Siem Reap", "Battambang", "Kampot"], "correct": 0, "explain": "Phnom Penh is the capital of Cambodia."},
    {"q": "Which planet is known as the Red Planet?", "options": ["Venus", "Mars", "Jupiter", "Saturn"], "correct": 1, "explain": "Mars is the Red Planet."},
    {"q": "How many days are there in a leap year?", "options": ["365", "366", "364", "360"], "correct": 1, "explain": "A leap year has 366 days."},
    {"q": "What is 7 x 8?", "options": ["54", "56", "48", "64"], "correct": 1, "explain": "7 x 8 = 56."},
    {"q": "Which animal is known as man's best friend?", "options": ["Cat", "Horse", "Dog", "Parrot"], "correct": 2, "explain": "Dogs are known as man's best friend."},
    {"q": "What does AI stand for?", "options": ["Automatic Internet", "Artificial Intelligence", "Advanced Information", "Auto Intelligence"], "correct": 1, "explain": "AI stands for Artificial Intelligence."},
    {"q": "Which company makes ChatGPT?", "options": ["Google", "Meta", "OpenAI", "Microsoft"], "correct": 2, "explain": "ChatGPT is made by OpenAI."},
    {"q": "What is the largest temple complex in the world, in Cambodia?", "options": ["Angkor Wat", "Bayon", "Ta Prohm", "Banteay Srei"], "correct": 0, "explain": "Angkor Wat is the largest religious monument in the world."},
    {"q": "How many minutes are in one hour?", "options": ["30", "60", "90", "100"], "correct": 1, "explain": "There are 60 minutes in one hour."},
    {"q": "What is the main language of Cambodia?", "options": ["Thai", "Vietnamese", "Khmer", "Lao"], "correct": 2, "explain": "Khmer is the official language of Cambodia."},
    {"q": "Which planet is closest to the Sun?", "options": ["Venus", "Earth", "Mercury", "Mars"], "correct": 2, "explain": "Mercury is closest to the Sun."},
    {"q": "What does www stand for?", "options": ["World Wide Web", "World Web Wide", "Wide World Web", "Web World Wide"], "correct": 0, "explain": "WWW stands for World Wide Web."},
    {"q": "Which of these is a free AI chatbot?", "options": ["ChatGPT Free", "A paid-only app", "A printer", "A calculator"], "correct": 0, "explain": "ChatGPT has a free tier."},
    {"q": "What is 15 + 15?", "options": ["25", "30", "35", "20"], "correct": 1, "explain": "15 + 15 = 30."},
    {"q": "Which river flows through Phnom Penh?", "options": ["Mekong", "Nile", "Amazon", "Danube"], "correct": 0, "explain": "The Mekong River flows through Phnom Penh."},
    {"q": "What is the capital of France?", "options": ["London", "Paris", "Berlin", "Madrid"], "correct": 1, "explain": "Paris is the capital of France."},
    {"q": "How many continents are there on Earth?", "options": ["5", "6", "7", "8"], "correct": 2, "explain": "There are 7 continents."},
    {"q": "What is the largest ocean on Earth?", "options": ["Pacific", "Atlantic", "Indian", "Arctic"], "correct": 0, "explain": "The Pacific is the largest ocean."},
    {"q": "Which gas do plants absorb from the air?", "options": ["Carbon dioxide", "Oxygen", "Nitrogen", "Hydrogen"], "correct": 0, "explain": "Plants absorb carbon dioxide."},
    {"q": "What is 9 x 9?", "options": ["72", "81", "99", "90"], "correct": 1, "explain": "9 x 9 = 81."},
    {"q": "Which instrument has 88 keys?", "options": ["Piano", "Guitar", "Violin", "Drum"], "correct": 0, "explain": "A piano has 88 keys."},
    {"q": "What is the chemical symbol for water?", "options": ["H2O", "CO2", "O2", "NaCl"], "correct": 0, "explain": "Water is H2O."},
    {"q": "How many legs does a spider have?", "options": ["6", "8", "10", "4"], "correct": 1, "explain": "A spider has 8 legs."},
    {"q": "What is the fastest land animal?", "options": ["Cheetah", "Lion", "Horse", "Gazelle"], "correct": 0, "explain": "The cheetah is the fastest land animal."},
    {"q": "Which country is home to the kangaroo?", "options": ["Australia", "India", "Brazil", "Kenya"], "correct": 0, "explain": "Kangaroos are from Australia."},
    {"q": "What is 100 divided by 4?", "options": ["20", "25", "30", "40"], "correct": 1, "explain": "100 / 4 = 25."},
    {"q": "What colour do you get mixing blue and yellow?", "options": ["Green", "Purple", "Orange", "Brown"], "correct": 0, "explain": "Blue + yellow = green."},
    {"q": "Which planet is famous for its rings?", "options": ["Saturn", "Mars", "Venus", "Mercury"], "correct": 0, "explain": "Saturn is famous for its rings."},
    {"q": "What is the main ingredient in guacamole?", "options": ["Avocado", "Tomato", "Onion", "Pepper"], "correct": 0, "explain": "Guacamole is made from avocado."},
    {"q": "How many seconds are in a minute?", "options": ["30", "60", "90", "120"], "correct": 1, "explain": "There are 60 seconds in a minute."},
]

SCRAMBLE_WORDS = [
    "telegram", "computer", "banana", "elephant", "rainbow", "guitar",
    "pizza", "monkey", "rocket", "flower", "dragon", "cheese",
    "mountain", "jungle", "pencil", "turtle", "castle", "pirate",
    "cookie", "garden",
]

RPS_CHOICES = ["rock", "paper", "scissors"]
RPS_EMOJI = {"rock": "🪨 Rock", "paper": "📄 Paper", "scissors": "✂️ Scissors"}

BRAND = "Daily Quote"
CHANNEL_HANDLE = "@dailynewsreporter"

HELP_TEXT = (
    "Welcome to Daily News — tech, AI and daily quotes.\n\n"
    "I'm @MuseVishnu_bot, the official bot for @dailynewsreporter.\n\n"
    "Tech & AI news\n"
    "/news — how to get AI and tech news here\n"
    "/ainews — latest AI model updates\n"
    "/models — AI models, what changed and who can use them\n"
    "/freeai — free AI tools you can use today\n"
    "/invite — free AI invite and referral codes we have shared\n"
    "/addinvite <service> <code> — owner only, add a code to share\n\n"
    "Quotes\n"
    "/quote — a daily quote\n"
    "/love — a love quote\n"
    "/friend — a friendship quote\n"
    "/khmer — a quote in Khmer\n"
    "/quoteimg — a quote as a shareable image\n"
    "/loveimg — a love quote as an image\n"
    "/friendimg — a friendship quote as an image\n"
    "/khmerimg — a Khmer quote as a shareable image\n"
    "/story — today's story image, ready to post\n"
    "/addquote <text> — owner only, add a quote to the library\n"
    "/daily — get a quote every morning at 8:42am\n"
    "/stopdaily — stop daily quotes\n\n"
    "Schedule\n"
    "/schedule — when the channel posts, every day\n\n"
    "Channel\n"
    "/channel — our channel, @dailynewsreporter\n"
    "/postquote — post a quote to the channel now\n"
    "/postnews <title> | <summary> | <source> — owner only, post news to the channel\n\n"
    "Games & fun\n"
    "/quiz — a quiz question (30 questions!)\n"
    "/guess — guess my number, 1 to 100\n"
    "/rps — rock, paper, scissors\n"
    "/dice — roll a dice 🎲\n"
    "/darts — throw a dart 🎯\n"
    "/bowling — bowl a strike 🎳\n"
    "/basketball — shoot a hoop 🏀\n"
    "/football — kick a goal ⚽\n"
    "/slots — spin the slot machine 🎰\n"
    "/scramble — unscramble the word\n"
    "/poll Question | Yes | No — make a poll\n"
    "/score — quiz scores\n"
    "/joke — a joke\n"
    "/motivate — motivation\n\n"
    "Smart chat & stats\n"
    "/ask <question> — ask me anything, e.g. /ask 12+8\n"
    "/stats — how the bot is doing\n"
    "/mystats — your messages, quotes and quiz score\n"
    "/top — top quiz players and most active chatters\n\n"
    "Tools\n"
    "/remind 10m drink water — remind you later\n"
    "/reminders — list your reminders\n"
    "/menu — show the quick menu buttons\n"
    "/hidemenu — hide the quick menu\n"
    "/time — current time in Phnom Penh\n"
    "/about — about Daily News\n"
    "/rules — group rules\n"
    "/info — info about this chat\n\n"
    "Group power (admins only, bot must be admin too)\n"
    "/admins, /ban, /unban, /kick, /mute, /unmute, /warn, /warns, /pin, /unpin, /del, /lock, /unlock, /setwelcome, /setrules, /antilink, /antispam\n"
    "/tban 1d — temporary ban, auto-unban\n"
    "/lock sticker — lock a message type, /locks to list, /locktypes to see them all\n"
    "/disable /quote — turn a command off here, /enable to bring it back\n"
    "/approve — exempt a member from locks, links and flood checks\n"
    "/setflood 8 — how many messages in 10 seconds counts as flooding\n"
    "/setgoodbye Bye {name} — message when a member leaves\n"
    "/fsub @DailyNewsReporter — members must join the channel to chat\n"
    "/id — your ID, this group's ID, or a replied user's ID\n"
    "/promote, /demote, /purge — manage admins and clean up fast\n"
    "/settings — settings panel with buttons\n"
    "/connect @Group — manage a group from this private chat\n\n"
    "Federation — share bans across your groups\n"
    "/newfed Name — create a federation\n"
    "/joinfed FED-ID — add this group to a federation\n"
    "/fban — ban a spammer in every fed group at once\n"
    "/unfban, /fedinfo, /fedlist, /fedbans\n\n"
    "Notes & filters\n"
    "/save name text — save a note, read it with /get name or #name\n"
    "/notes, /clear name — list and delete notes\n"
    "/filter word reply — auto-reply when someone says the word\n"
    "/filters, /stop word — list and remove filters\n\n"
    "Safety\n"
    "/captcha on — new members tap a button to prove they are human\n"
    "/report — reply to a message to report it to the admins\n"
    "/addblacklist word, /blacklist, /blacklistmode delete|warn|mute\n"
    "/addsticker — reply to a sticker to blacklist it\n\n"
    "Levels & fun\n"
    "/checkin — daily check-in, +20 XP and keep your streak alive\n"
    "/rank — your XP, level and streak\n"
    "/leaderboard — top players and longest streaks\n"
    "/tournament — 5-question trivia tournament in a group, winner +50 XP\n"
    "/digest — your personal daily digest\n\n"
    "Smart & safe\n"
    "/ask — ask me anything, now answered by real AI when I don't know\n"
    "/check — reply to a message, AI says if it's spam, scam, abuse or harmless\n"
    "/aimod on — AI checks link messages from non-admins and deletes the bad ones\n"
    "/setlog @Channel — log bans, mutes, warns, fbans and purges there\n"
    "/backup — group settings as JSON, /restore to bring them back\n\n"
    "Growth & Premium\n"
    "/referral — your invite link, friends get +20 XP, you get +50 XP each\n"
    "/topreferrers — who invited the most friends\n"
    "/premium — 150 Stars for lifetime Premium: double XP and a ⭐ badge\n\n"
    "Channel & content (owner)\n"
    "/broadcast — send a message to every subscriber\n"
    "/schedulepost 18:30 text — queue a channel post, /scheduled to list, /cancelschedule to cancel\n\n"
    "Growth\n"
    "/giveaway premium — start a giveaway in a group, /draw picks the winner\n"
    "/share — a ready-to-forward promo for Daily News\n"
    "/boost on — double referral rewards (auto on weekends)"
    "\n\nInline: type @MuseVishnu_bot in any chat to send a quote, the Mini App or the channel."
)

ABOUT_TEXT = (
    "Daily News — @dailynewsreporter\n\n"
    "Tech news, AI information, AI model updates, free AI tools and free invite codes — plus a daily quote.\n\n"
    "What you get here:\n"
    "• AI and tech news, researched and posted to the channel\n"
    "• A new quote every morning at 8:42am\n"
    "• A love quote every evening at about 6:42pm\n"
    "• Free AI tools in /freeai, and invite codes in /invite\n"
    "• Quizzes, polls and reminders from @MuseVishnu_bot\n\n"
    "Follow the channel: @dailynewsreporter\n"
    "Talk to the bot: @MuseVishnu_bot\n"
    "Send /daily to get the morning quote in your own chat."
)

SCHEDULE_TEXT = (
    "Daily News — posting schedule, Asia/Phnom_Penh\n\n"
    "08:42 — Morning quote\n"
    "12:42 — AI & tech news, 2-3 researched items\n"
    "18:42 — Evening love quote\n\n"
    "Plus, anytime: free AI tools, invite codes, quizzes and polls.\n"
    "Follow: @dailynewsreporter\n"
    "Bot: @MuseVishnu_bot"
)

WELCOME_POST_TEXT = (
    "Welcome to Daily News\n"
    "@dailynewsreporter\n\n"
    "Tech news, AI information, AI model updates, free AI tools and free invite codes — plus a daily quote.\n\n"
    "What you will get here:\n"
    "08:42 — Morning quote\n"
    "12:42 — AI & tech news\n"
    "18:42 — Evening love quote\n\n"
    "Commands in the bot @MuseVishnu_bot:\n"
    "/news — AI & tech news\n"
    "/freeai — free AI tools\n"
    "/invite — free invite codes\n"
    "/quote — a daily quote\n"
    "/khmer — a quote in Khmer\n"
    "/quiz — play a quiz\n\n"
    "Share this channel with a friend who loves tech and AI.\n"
    "#DailyNews #AINews #TechNews"
)

CHANNEL_DESCRIPTION = (
    "📰 Daily News — Tech, AI & Quotes\n"
    "🤖 AI model updates, free AI tools & invite codes\n"
    "💬 Quote 08:42 • AI news 12:42 • Love quote 18:42\n"
    "Bot: @MuseVishnu_bot"
)

BOT_SHORT_DESCRIPTION = "Daily News bot — tech, AI, free AI tools, invite codes and a daily quote. Try /news /freeai /invite /quote"

BOT_DESCRIPTION = (
    "Welcome to Daily News — @dailynewsreporter\n\n"
    "Tech news, AI model updates, free AI tools, free invite codes and a daily quote.\n\n"
    "/news — AI & tech news\n"
    "/freeai — free AI tools\n"
    "/invite — free invite codes\n"
    "/quote — a daily quote\n"
    "/khmer — a quote in Khmer\n"
    "/quiz — play a quiz\n"
    "/schedule — posting times\n\n"
    "Channel: @dailynewsreporter"
)

FREE_AI_TEXT = (
    "Free AI tools you can use today — checked November 2026, limits change often, so check the vendor page before you rely on one:\n\n"
    "• ChatGPT Free — unlimited everyday text chats, with limits on uploads, images and voice.\n"
    "• Gemini Free — chat, Deep Research, Gemini Live, image generation, 15 GB storage. Usage refreshes about every 5 hours, up to a weekly limit.\n"
    "• Claude Free — Sonnet and Haiku, with web search, files and voice. Limited every 5 hours and every week.\n"
    "• DeepSeek — free chatbot, no paid tier listed.\n"
    "• Perplexity Free — unlimited basic searches with linked sources, 3 Pro searches a day.\n"
    "• Muse — Meta's personal AI agent, free with usage limits. In the US, Canada and Japan you can join at muse.ai. Referral codes can give 1 billion bonus tokens — see /invite.\n\n"
    "Free is for everyday and learning use, not for confidential or client data."
)

MODELS_TEXT = (
    "AI model updates — November 2026, as reported by the vendors and roundups. Prices and access change fast:\n\n"
    "• OpenAI GPT-6.2 Astra and GPT-6.2 Sol — reported released 17 Nov 2026, via API and ChatGPT Work. Sol is the more efficient one, reported at about 80% less than Astra.\n"
    "• Alibaba Qwen4-Max — reported released 17 Nov 2026, 1 million token context, multimodal.\n"
    "• Anthropic Claude Fable 5.2 — released 9 Nov 2026, $8 in / $40 out per million tokens, 1M context.\n"
    "• Google Gemini 4 Carbon — generally available 10 Nov 2026, $2 / $10 per million tokens until 30 Nov, then $4 / $20.\n"
    "• Google Gemini 4 Hydrogen — generally available 12 Nov 2026, $1.50 / $7.50, with a free tier.\n"
    "• OpenAI GPT-6 Cyber — 5 Nov 2026, for approved security teams only.\n\n"
    "Ask me in this chat for the latest, and I will check before I answer."
)

NEWS_TEXT = (
    "AI and tech news — @dailynewsreporter\n\n"
    "I research the latest AI and tech news and post it to the channel, with the source named. You will see:\n"
    "• New AI model releases and price changes\n"
    "• Free AI tools and new features\n"
    "• Free invite and referral codes, when we have a real one to share\n\n"
    "Commands:\n"
    "/ainews — latest AI model updates\n"
    "/models — AI models, what changed and who can use them\n"
    "/freeai — free AI tools you can use today\n"
    "/invite — free AI invite codes we have shared\n\n"
    "News is researched by Muse before it is posted — the bot itself does not browse the web."
)


OWNER_ID = 5039926430
BOT_ID = 9085916191  # @MuseVishnu_bot (public id, not a secret)


def news_caption(title, summary, source=""):
    now = phnom_penh_now()
    date_str = now.strftime("%A, %d %B %Y")
    out = f"Daily News — Tech & AI\n{date_str}\n\n{title}\n\n{summary}\n"
    if source:
        out += f"\nSource: {source}\n"
    out += "\n#TechNews #AINews #DailyNews\nFollow: @dailynewsreporter\nBot: @MuseVishnu_bot"
    return out


def channel_caption(quote, kind="daily"):
    now = phnom_penh_now()
    date_str = now.strftime("%A, %d %B %Y")
    titles = {"daily": "Good morning — Daily Quote", "love": "Good evening — Love Quote", "friend": "Friendship Quote", "motivate": "Motivation"}
    tags = {"daily": "#DailyQuote #Motivation", "love": "#DailyQuote #Love", "friend": "#DailyQuote #Friendship", "motivate": "#DailyQuote #Motivation"}
    title = titles.get(kind, "Daily Quote")
    tag = tags.get(kind, "#DailyQuote")
    return f"{title}\n{date_str}\n\n{quote}\n\n{tag}\nFollow: @dailynewsreporter\nBot: @MuseVishnu_bot"

RULES_TEXT = (
    "Group rules:\n"
    "1. Be kind to everyone here.\n"
    "2. No spam, no ads, no links without permission.\n"
    "3. Keep it friendly, love and friendship first.\n"
    "4. Use /quote for a daily dose of kindness."
)


def _kv_get_state():
    """Whole state as one JSON string under KV_STATE_KEY (Upstash REST API)."""
    url = os.environ.get("KV_REST_API_URL", "").rstrip("/")
    token = os.environ.get("KV_REST_API_TOKEN", "")
    if not url or not token:
        raise RuntimeError("KV_REST_API_URL and KV_REST_API_TOKEN are required for STATE_BACKEND=kv")
    request = urllib.request.Request(
        f"{url}/get/{KV_STATE_KEY}",
        headers={"Authorization": f"Bearer {token}"},
        method="GET",
    )
    with urllib.request.build_opener(NoRedirect()).open(request, timeout=15) as response:
        raw = read_json_response(response).get("result")
    if not raw:
        return {}
    return json.loads(raw)


def _kv_set_state(state):
    """Write the whole state with one pipeline call (keeps the large JSON value out of the URL)."""
    url = os.environ.get("KV_REST_API_URL", "").rstrip("/")
    token = os.environ.get("KV_REST_API_TOKEN", "")
    if not url or not token:
        raise RuntimeError("KV_REST_API_URL and KV_REST_API_TOKEN are required for STATE_BACKEND=kv")
    payload = json.dumps([["SET", KV_STATE_KEY, json.dumps(state)]]).encode("utf-8")
    request = urllib.request.Request(
        f"{url}/pipeline", data=payload, method="POST",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
    )
    with urllib.request.build_opener(NoRedirect()).open(request, timeout=15) as response:
        # Upstash /pipeline returns a bare JSON array, not {"result": [...]}.
        body = read_json_response(response)
        results = body.get("result") if isinstance(body, dict) else body
        results = results or []
    if not results or results[0].get("result") != "OK":
        raise RuntimeError(f"KV state write failed: {results}")


def load_state():
    if STATE_BACKEND == "kv":
        # A KV failure must be loud: falling back to an empty state here
        # would let the next save overwrite the real state with nothing.
        state = _kv_get_state()
    else:
        try:
            with open(STATE_PATH, encoding="utf-8") as f:
                state = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            state = {}
    state.setdefault("offset", 0)
    state.setdefault("subscribers", [])
    state.setdefault("last_daily", "")
    state.setdefault("last_evening", "")
    state.setdefault("channel_id", "")
    state.setdefault("reminders", [])
    state.setdefault("scores", {})
    state.setdefault("quiz_polls", {})
    state.setdefault("group_settings", {})
    state.setdefault("federations", {})
    state.setdefault("connections", {})
    state.setdefault("pending_verify", {})
    state.setdefault("xp", {})
    state.setdefault("streaks", {})
    state.setdefault("tournaments", {})
    state.setdefault("tournament_polls", {})
    state.setdefault("log_channels", {})
    state.setdefault("referrals", {})
    state.setdefault("referred_by", {})
    state.setdefault("premium", {})
    state.setdefault("scheduled_posts", [])
    state.setdefault("giveaways", {})
    state.setdefault("users", {})
    state.setdefault("questions", [])
    state.setdefault("invite_codes", [])
    state.setdefault("custom_quotes", [])
    state.setdefault("stats", {"messages": 0, "commands": {}, "quotes_sent": 0, "photos_sent": 0, "quizzes_sent": 0, "started": int(time.time())})
    state["stats"].setdefault("messages", 0)
    state["stats"].setdefault("commands", {})
    state["stats"].setdefault("quotes_sent", 0)
    state["stats"].setdefault("photos_sent", 0)
    state["stats"].setdefault("quizzes_sent", 0)
    state["stats"].setdefault("started", int(time.time()))
    return state


KNOWLEDGE = {
    "capital of cambodia": "The capital of Cambodia is Phnom Penh.",
    "capital of thailand": "The capital of Thailand is Bangkok.",
    "capital of vietnam": "The capital of Vietnam is Hanoi.",
    "capital of france": "The capital of France is Paris.",
    "capital of japan": "The capital of Japan is Tokyo.",
    "who are you": "I'm MuseVishnu, a Telegram bot made for love, friendship and daily quotes. Try /help to see what I can do.",
    "what is your name": "My name is MuseVishnu, and my username is @MuseVishnu_bot.",
    "how old are you": "I was born on 18 November 2026, when Dailyquote connected me. So I'm brand new.",
    "what is love": "Love is choosing someone every day, in the small things, not just the big moments.",
    "what is friendship": "Friendship is the quiet kind of love that never asks for anything back.",
}


def safe_math(expr):
    """Evaluate a simple math expression safely, or return None."""
    expr = expr.strip()
    if not re.fullmatch(r"[0-9\s\.\+\-\*\/\(\)\%]+", expr):
        return None
    try:
        tree = ast.parse(expr, mode="eval")
    except Exception:
        return None
    ops = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv, ast.Mod: operator.mod, ast.USub: operator.neg, ast.UAdd: operator.pos}
    def eval_node(node):
        if isinstance(node, ast.Expression):
            return eval_node(node.body)
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
            return node.value
        if isinstance(node, ast.BinOp) and type(node.op) in ops:
            return ops[type(node.op)](eval_node(node.left), eval_node(node.right))
        if isinstance(node, ast.UnaryOp) and type(node.op) in ops:
            return ops[type(node.op)](eval_node(node.operand))
        raise ValueError("unsupported")
    try:
        result = eval_node(tree)
    except Exception:
        return None
    if isinstance(result, float) and result.is_integer():
        result = int(result)
    return result


def track_user(state, message, command=""):
    user = message.get("from") or {}
    uid = str(user.get("id", ""))
    if not uid:
        return None
    users = state.setdefault("users", {})
    record = users.setdefault(uid, {"name": user.get("first_name", "friend"), "messages": 0, "quotes": 0, "quizzes": 0, "first_seen": int(time.time()), "last_seen": int(time.time()), "last_command": "", "favourite": "", "lang": "en"})
    record["name"] = user.get("first_name", record["name"])
    record["messages"] += 1
    record["last_seen"] = int(time.time())
    if command:
        record["last_command"] = command
        if command in ("/quote", "/love", "/friend", "/khmer"):
            record["quotes"] += 1
            record["favourite"] = command
        if command == "/quiz":
            record["quizzes"] += 1
    stats = state.setdefault("stats", {})
    stats["messages"] = stats.get("messages", 0) + 1
    if command.startswith("/"):
        cmds = stats.setdefault("commands", {})
        cmds[command] = cmds.get(command, 0) + 1
    if command in ("/quote", "/love", "/friend", "/khmer", "/quoteimg", "/loveimg", "/friendimg", "/khmerimg"):
        stats["quotes_sent"] = stats.get("quotes_sent", 0) + 1
    if command in ("/quoteimg", "/loveimg", "/friendimg", "/khmerimg"):
        stats["photos_sent"] = stats.get("photos_sent", 0) + 1
    if command == "/quiz":
        stats["quizzes_sent"] = stats.get("quizzes_sent", 0) + 1
    return record


def get_group_settings(state, chat_id):
    settings = state.setdefault("group_settings", {}).setdefault(
        str(chat_id),
        {"welcome": "", "rules": "", "antilink": False, "antispam": True, "warnings": {},
         "captcha": False, "reports": True, "blacklist_words": [], "blacklist_mode": "delete",
         "blacklist_stickers": [], "notes": {}, "filters": {}, "aimod": False,
         "locks": {}, "approved": [], "disabled": [], "flood_limit": 5, "goodbye": "", "fsub": ""},
    )
    settings.setdefault("welcome", "")
    settings.setdefault("rules", "")
    settings.setdefault("antilink", False)
    settings.setdefault("antispam", True)
    settings.setdefault("warnings", {})
    settings.setdefault("captcha", False)
    settings.setdefault("reports", True)
    settings.setdefault("blacklist_words", [])
    settings.setdefault("blacklist_mode", "delete")
    settings.setdefault("blacklist_stickers", [])
    settings.setdefault("notes", {})
    settings.setdefault("filters", {})
    settings.setdefault("aimod", False)
    settings.setdefault("locks", {})
    settings.setdefault("approved", [])
    settings.setdefault("disabled", [])
    settings.setdefault("flood_limit", 5)
    settings.setdefault("goodbye", "")
    settings.setdefault("fsub", "")
    return settings


LOCK_TYPES = ("sticker", "photo", "video", "audio", "voice", "document", "gif", "url", "forward", "bots", "poll", "location", "contact", "game", "invite", "pin")


def message_lock_types(message):
    """Which granular lock types this message matches (Rose-style)."""
    types = set()
    if message.get("sticker"):
        types.add("sticker")
    if message.get("photo"):
        types.add("photo")
    if message.get("video"):
        types.add("video")
    if message.get("audio"):
        types.add("audio")
    if message.get("voice"):
        types.add("voice")
    if message.get("document"):
        types.add("document")
    if message.get("animation"):
        types.add("gif")
    if message.get("poll"):
        types.add("poll")
    if message.get("location"):
        types.add("location")
    if message.get("contact"):
        types.add("contact")
    if message.get("game"):
        types.add("game")
    if message.get("pinned_message"):
        types.add("pin")
    if message.get("forward_from") or message.get("forward_from_chat"):
        types.add("forward")
    text = (message.get("text") or "") + (message.get("caption") or "")
    entities = list(message.get("entities") or []) + list(message.get("caption_entities") or [])
    if any(e.get("type") in ("url", "text_link") for e in entities) or "http://" in text or "https://" in text or "t.me/" in text:
        types.add("url")
    if "t.me/joinchat" in text or "t.me/+" in text:
        types.add("invite")
    for m in message.get("new_chat_members", []):
        if m.get("is_bot"):
            types.add("bots")
    return types


def is_privileged(state, chat_id, user_id, dry_run=False, sender_chat_id=None):
    """Admins and approved users bypass group enforcement (locks, links, flood, blacklist, fsub)."""
    if is_admin(chat_id, user_id, dry_run=dry_run, sender_chat_id=sender_chat_id):
        return True
    settings = get_group_settings(state, chat_id)
    return str(user_id) in [str(u) for u in settings.get("approved", [])]


def locked_type_hit(state, chat_id, sender_id, message, dry_run=False):
    """First locked content type this message hits, or None (admins and approved users bypass)."""
    if is_privileged(state, chat_id, sender_id, dry_run=dry_run, sender_chat_id=(message.get("sender_chat") or {}).get("id")):
        return None
    locks = get_group_settings(state, chat_id).get("locks", {})
    if locks.get("all"):
        return "all"
    for t in message_lock_types(message):
        if locks.get(t):
            return t
    return None


def is_admin(chat_id, user_id, dry_run=False, sender_chat_id=None):
    # Anonymous group admins post as the group itself (sender_chat == the group, no "from").
    if not user_id and sender_chat_id and sender_chat_id == chat_id:
        return True
    if dry_run:
        # In tests, user 111 is the group admin, everyone else is not.
        return user_id == 111
    if not user_id:
        # Posting as the group's linked channel (sender_chat == linked_chat_id) is also an admin.
        if sender_chat_id:
            try:
                chat = api_call("getChat", {"chat_id": chat_id}).get("result", {})
            except Exception:
                chat = {}
            if chat.get("linked_chat_id") == sender_chat_id:
                return True
        return False
    try:
        result = api_call("getChatAdministrators", {"chat_id": chat_id})
    except Exception:
        return False
    for admin in result.get("result", []):
        if (admin.get("user") or {}).get("id") == user_id:
            return True
    return False


def bot_is_admin(chat_id, dry_run=False):
    """Is this bot an admin in the group? Used to tell the user to promote the bot instead of the misleading 'only admins' error."""
    if dry_run:
        return True
    try:
        result = api_call("getChatMember", {"chat_id": chat_id, "user_id": BOT_ID})
    except Exception:
        return False
    return (result.get("result") or {}).get("status") in ("administrator", "creator")


def target_user(message, args):
    """Target for an admin command: replied message first, then an id in args."""
    replied = message.get("reply_to_message") or {}
    sender = replied.get("from") or {}
    if sender.get("id"):
        return sender.get("id"), sender.get("first_name", "that member")
    parts = args.split(None, 1)
    if parts and parts[0].lstrip("-").isdigit():
        return int(parts[0]), parts[0]
    return None, None


# Flood tracking, in memory only: (chat_id, user_id) -> [timestamps]
FLOOD_TRACKER = {}


def save_state(state):
    if STATE_BACKEND == "kv":
        _kv_set_state(state)
        return
    tmp = STATE_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)
    os.replace(tmp, STATE_PATH)


def api_url(method):
    return f"https://api.telegram.org/bot{get_token()}/{method}"


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args):
        raise RuntimeError("redirect refused, verify the endpoint")


def api_call(method, payload=None, timeout=35):
    data = None
    headers = {}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(
        api_url(method), data=data, headers=headers,
        method="POST" if payload is not None else "GET",
    )
    with urllib.request.build_opener(NoRedirect()).open(request, timeout=timeout) as response:
        return read_json_response(response)


def send_message(chat_id, text, reply_markup=None):
    payload = {"chat_id": chat_id, "text": text}
    if reply_markup:
        payload["reply_markup"] = reply_markup
    result = api_call("sendMessage", payload)
    print(f"sent to {chat_id}: {text[:60]!r} ok={result.get('ok')}", flush=True)
    return result


def send_photo(chat_id, path, caption=""):
    boundary = "----musebot" + uuid.uuid4().hex
    body = b""
    body += f"--{boundary}\r\n".encode()
    body += b'Content-Disposition: form-data; name="chat_id"\r\n\r\n'
    body += str(chat_id).encode() + b"\r\n"
    if caption:
        body += f"--{boundary}\r\n".encode()
        body += b'Content-Disposition: form-data; name="caption"\r\n\r\n'
        body += caption.encode() + b"\r\n"
    with open(path, "rb") as f:
        data = f.read()
    body += f"--{boundary}\r\n".encode()
    body += b'Content-Disposition: form-data; name="photo"; filename="quote.png"\r\n'
    body += b"Content-Type: image/png\r\n\r\n"
    body += data + b"\r\n"
    body += f"--{boundary}--\r\n".encode()
    request = urllib.request.Request(
        api_url("sendPhoto"), data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        method="POST",
    )
    with urllib.request.build_opener(NoRedirect()).open(request, timeout=60) as response:
        result = read_json_response(response)
    print(f"sent photo to {chat_id} ok={result.get('ok')}", flush=True)
    return result


def send_poll(chat_id, question, options, is_quiz=False, correct=None, explanation=None):
    payload = {
        "chat_id": chat_id, "question": question, "options": options,
        "is_anonymous": False, "allows_multiple_answers": False,
    }
    if is_quiz:
        payload["type"] = "quiz"
        payload["correct_option_id"] = correct
        if explanation:
            payload["explanation"] = explanation
    result = api_call("sendPoll", payload)
    print(f"sent poll to {chat_id} ok={result.get('ok')}", flush=True)
    return result


def phnom_penh_now():
    tz = datetime.timezone(datetime.timedelta(hours=7))
    return datetime.datetime.now(tz)


_RECENT_QUOTES = []


def pick_quote(kind="any", state=None):
    if kind == "love":
        pool = list(LOVE_QUOTES)
    elif kind == "friend":
        pool = list(FRIEND_QUOTES)
    elif kind == "khmer":
        pool = list(KHMER_QUOTES)
    else:
        pool = list(DAILY_QUOTES)
    if state:
        pool.extend(state.get("custom_quotes", []))
    # No repeats: avoid the last 5 quotes sent in this process
    fresh = [q for q in pool if q not in _RECENT_QUOTES]
    chosen = random.choice(fresh if fresh else pool)
    _RECENT_QUOTES.append(chosen)
    del _RECENT_QUOTES[:-5]
    return chosen


def scramble_word(word):
    letters = list(word)
    for _ in range(20):
        random.shuffle(letters)
        scrambled = "".join(letters)
        if scrambled != word:
            return scrambled
    return "".join(letters)


def rps_result(player, bot):
    if player == bot:
        return "draw"
    wins = {"rock": "scissors", "paper": "rock", "scissors": "paper"}
    return "win" if wins[player] == bot else "lose"


def make_quote_image(quote, kind="daily"):
    from PIL import Image, ImageDraw, ImageFont
    size = 1080
    img = Image.new("RGB", (size, size))
    draw = ImageDraw.Draw(img)
    # Professional gradient: deep navy to teal
    top = (16, 24, 64)
    bottom = (14, 90, 110)
    for y in range(size):
        r = top[0] + (bottom[0] - top[0]) * y // size
        g = top[1] + (bottom[1] - top[1]) * y // size
        b = top[2] + (bottom[2] - top[2]) * y // size
        draw.line([(0, y), (size, y)], fill=(r, g, b))
    try:
        font_big = ImageFont.truetype(font_file("DejaVuSans-Bold.ttf"), 64)
        font_header = ImageFont.truetype(font_file("DejaVuSans-Bold.ttf"), 40)
        font_text = ImageFont.truetype(font_file("DejaVuSans.ttf"), 52)
        font_small = ImageFont.truetype(font_file("DejaVuSans.ttf"), 34)
    except Exception:
        font_big = ImageFont.load_default()
        font_header = ImageFont.load_default()
        font_text = ImageFont.load_default()
        font_small = ImageFont.load_default()
    # Khmer needs Noto Sans Khmer: DejaVu has no Khmer glyphs (U+1780-U+17FF)
    # and renders Khmer as boxes. Pillow here has raqm, so shaping works.
    if any(0x1780 <= ord(c) <= 0x17FF for c in quote):
        try:
            font_text = ImageFont.truetype(font_file("NotoSansKhmer-Regular.ttf"), 48)
        except Exception:
            pass
    # Header, centred, with a gold rule underneath
    header = "DAILY QUOTE"
    bbox = draw.textbbox((0, 0), header, font=font_header)
    draw.text(((size - (bbox[2] - bbox[0])) // 2, 80), header, fill=(255, 215, 130), font=font_header)
    draw.line([(340, 150), (740, 150)], fill=(255, 215, 130), width=3)
    draw.text((80, 180), "\u201c", fill=(255, 255, 255), font=font_big)
    # Wrap and centre the quote text
    words = quote.split()
    lines = []
    line = ""
    for word in words:
        test = (line + " " + word).strip()
        bbox = draw.textbbox((0, 0), test, font=font_text)
        if bbox[2] - bbox[0] > 860:
            lines.append(line)
            line = word
        else:
            line = test
    if line:
        lines.append(line)
    total_h = len(lines) * 78
    y = max(300, (size - total_h) // 2)
    for line_text in lines:
        bbox = draw.textbbox((0, 0), line_text, font=font_text)
        x = (size - (bbox[2] - bbox[0])) // 2
        draw.text((x, y), line_text, fill=(255, 255, 255), font=font_text)
        y += 78
    date_str = phnom_penh_now().strftime("%d %B %Y")
    footer = f"{date_str}  •  @dailynewsreporter"
    bbox = draw.textbbox((0, 0), footer, font=font_small)
    draw.text(((size - (bbox[2] - bbox[0])) // 2, 920), footer, fill=(200, 225, 240), font=font_small)
    bot_line = "@MuseVishnu_bot"
    bbox = draw.textbbox((0, 0), bot_line, font=font_small)
    draw.text(((size - (bbox[2] - bbox[0])) // 2, 965), bot_line, fill=(255, 215, 130), font=font_small)
    path = f"/tmp/quote_{uuid.uuid4().hex[:8]}.png"
    img.save(path)
    return path


def make_story_image(quote):
    """Vertical 1080x1920 story image with the daily quote, same navy/gold branding."""
    from PIL import Image, ImageDraw, ImageFont
    w, h = 1080, 1920
    img = Image.new("RGB", (w, h))
    draw = ImageDraw.Draw(img)
    top = (16, 24, 64)
    bottom = (14, 90, 110)
    for y in range(h):
        r = top[0] + (bottom[0] - top[0]) * y // h
        g = top[1] + (bottom[1] - top[1]) * y // h
        b = top[2] + (bottom[2] - top[2]) * y // h
        draw.line([(0, y), (w, y)], fill=(r, g, b))
    try:
        font_big = ImageFont.truetype(font_file("DejaVuSans-Bold.ttf"), 120)
        font_header = ImageFont.truetype(font_file("DejaVuSans-Bold.ttf"), 52)
        font_text = ImageFont.truetype(font_file("DejaVuSans.ttf"), 72)
        font_small = ImageFont.truetype(font_file("DejaVuSans.ttf"), 44)
    except Exception:
        font_big = ImageFont.load_default()
        font_header = ImageFont.load_default()
        font_text = ImageFont.load_default()
        font_small = ImageFont.load_default()
    if any(0x1780 <= ord(c) <= 0x17FF for c in quote):
        try:
            font_text = ImageFont.truetype(font_file("NotoSansKhmer-Regular.ttf"), 64)
        except Exception:
            pass
    header = "DAILY NEWS"
    bbox = draw.textbbox((0, 0), header, font=font_header)
    draw.text(((w - (bbox[2] - bbox[0])) // 2, 180), header, fill=(255, 215, 130), font=font_header)
    draw.line([(390, 270), (690, 270)], fill=(255, 215, 130), width=4)
    draw.text((90, 340), "\u201c", fill=(255, 255, 255), font=font_big)
    words = quote.split()
    lines = []
    line = ""
    for word in words:
        test = (line + " " + word).strip()
        bbox = draw.textbbox((0, 0), test, font=font_text)
        if bbox[2] - bbox[0] > 860:
            lines.append(line)
            line = word
        else:
            line = test
    if line:
        lines.append(line)
    total_h = len(lines) * 108
    y = max(560, (h - total_h) // 2)
    for line_text in lines:
        bbox = draw.textbbox((0, 0), line_text, font=font_text)
        x = (w - (bbox[2] - bbox[0])) // 2
        draw.text((x, y), line_text, fill=(255, 255, 255), font=font_text)
        y += 108
    date_str = phnom_penh_now().strftime("%d %B %Y")
    footer = f"{date_str}  •  @dailynewsreporter"
    bbox = draw.textbbox((0, 0), footer, font=font_small)
    draw.text(((w - (bbox[2] - bbox[0])) // 2, 1620), footer, fill=(200, 225, 240), font=font_small)
    bot_line = "@MuseVishnu_bot"
    bbox = draw.textbbox((0, 0), bot_line, font=font_small)
    draw.text(((w - (bbox[2] - bbox[0])) // 2, 1680), bot_line, fill=(255, 215, 130), font=font_small)
    path = f"/tmp/story_{uuid.uuid4().hex[:8]}.png"
    img.save(path)
    return path


def post_story(chat_id, path, caption=""):
    """Post a photo story. BUSINESS ACCOUNTS ONLY: postStory needs a
    business_connection_id and the can_manage_stories business right.
    It does NOT post stories to a channel — verified 2026-11-18, a regular
    bot gets "business connection not found" even as a channel admin with
    can_post_stories. Kept here for a future business-account setup.
    Content is an InputStoryContentPhoto via attach://, the same pattern
    as setMyProfilePhoto."""
    boundary = "----musebot" + uuid.uuid4().hex
    content = json.dumps({"type": "photo", "photo": "attach://story.png"})
    with open(path, "rb") as f:
        data = f.read()
    body = b""
    body += f"--{boundary}\r\n".encode()
    body += b'Content-Disposition: form-data; name="chat_id"\r\n\r\n' + str(chat_id).encode() + b"\r\n"
    body += f"--{boundary}\r\n".encode()
    body += b'Content-Disposition: form-data; name="active_period"\r\n\r\n86400\r\n'
    if caption:
        body += f"--{boundary}\r\n".encode()
        body += b'Content-Disposition: form-data; name="caption"\r\n\r\n' + caption.encode()[:200] + b"\r\n"
    body += f"--{boundary}\r\n".encode()
    body += b'Content-Disposition: form-data; name="content"\r\n\r\n' + content.encode() + b"\r\n"
    body += f"--{boundary}\r\n".encode()
    body += b'Content-Disposition: form-data; name="story.png"; filename="story.png"\r\nContent-Type: image/png\r\n\r\n'
    body += data + b"\r\n"
    body += f"--{boundary}--\r\n".encode()
    request = urllib.request.Request(
        api_url("postStory"), data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        method="POST",
    )
    with urllib.request.build_opener(NoRedirect()).open(request, timeout=60) as response:
        result = read_json_response(response)
    print(f"posted story to {chat_id} ok={result.get('ok')}", flush=True)
    return result


def send_story():
    """Daily story, as far as the Bot API allows. Verified 2026-11-18:
    postStory requires a business_connection_id and the can_manage_stories
    business right — a regular bot cannot post a story to a channel, even
    with can_post_stories admin rights ("business connection not found").
    So this makes the vertical 1080x1920 story image each morning and sends
    it to the owner, ready to post as the channel's story in two taps:
    open @dailynewsreporter, Post Story, pick this image."""
    state = load_state()
    today = phnom_penh_now().strftime("%Y-%m-%d")
    if state.get("last_story") == today:
        print(f"story already prepared for {today}", flush=True)
        return 0
    quote = pick_quote("any", state)
    path = make_story_image(quote)
    caption = (
        "📸 Your Daily News story for today — ready to post.\n\n"
        "Open @dailynewsreporter → Post Story → pick this image.\n"
        f"Quote: {quote}"
    )
    try:
        result = send_photo(OWNER_ID, path, caption=caption)
        if not result.get("ok"):
            print(f"story delivery failed: {result}", flush=True)
            return 0
        sent = 1
    except Exception as e:
        print(f"story delivery failed: {e}", flush=True)
        return 0
    finally:
        try:
            os.remove(path)
        except OSError:
            pass
    state["last_story"] = today
    save_state(state)
    return sent


def make_channel_banner():
    from PIL import Image, ImageDraw, ImageFont
    size = 1080
    img = Image.new("RGB", (size, size))
    draw = ImageDraw.Draw(img)
    top = (16, 24, 64)
    bottom = (14, 90, 110)
    for y in range(size):
        r = top[0] + (bottom[0] - top[0]) * y // size
        g = top[1] + (bottom[1] - top[1]) * y // size
        b = top[2] + (bottom[2] - top[2]) * y // size
        draw.line([(0, y), (size, y)], fill=(r, g, b))
    try:
        font_title = ImageFont.truetype(font_file("DejaVuSans-Bold.ttf"), 110)
        font_sub = ImageFont.truetype(font_file("DejaVuSans.ttf"), 44)
        font_small = ImageFont.truetype(font_file("DejaVuSans.ttf"), 38)
        font_gold = ImageFont.truetype(font_file("DejaVuSans-Bold.ttf"), 40)
    except Exception:
        font_title = ImageFont.load_default()
        font_sub = ImageFont.load_default()
        font_small = ImageFont.load_default()
        font_gold = ImageFont.load_default()
    def centre(text, y, font, fill):
        bbox = draw.textbbox((0, 0), text, font=font)
        draw.text(((size - (bbox[2] - bbox[0])) // 2, y), text, fill=fill, font=font)
    centre("DAILY NEWS", 260, font_title, (255, 255, 255))
    draw.line([(290, 400), (790, 400)], fill=(255, 215, 130), width=4)
    centre("Tech  •  AI  •  Free AI  •  Quotes", 440, font_sub, (200, 225, 240))
    centre("08:42  Morning quote", 560, font_small, (255, 255, 255))
    centre("12:42  AI & tech news", 620, font_small, (255, 255, 255))
    centre("18:42  Evening love quote", 680, font_small, (255, 255, 255))
    centre("@dailynewsreporter", 820, font_gold, (255, 215, 130))
    centre("@MuseVishnu_bot", 880, font_small, (200, 225, 240))
    path = f"/tmp/banner_{uuid.uuid4().hex[:8]}.png"
    img.save(path)
    return path


def set_channel_info(photo_path=None):
    """Auto-fill the channel via the Bot API. The bot must be an admin with can_change_info, which @MuseVishnu_bot is in @dailynewsreporter."""
    state = load_state()
    channel = state.get("channel_id")
    if not channel:
        print("no channel set", flush=True)
        return False
    def _set(method, payload):
        # Telegram returns HTTP 400 "not modified" when the value is already
        # what we set; that means the fill is already done, not a failure.
        try:
            return api_call(method, payload).get("ok")
        except Exception as e:
            body = ""
            if hasattr(e, "read"):
                try:
                    body = e.read().decode()
                except Exception:
                    pass
            if "not modified" in str(body):
                print(f"{method}: already set", flush=True)
                return True
            print(f"{method} failed: {e} {body}", flush=True)
            return False

    title_ok = _set("setChatTitle", {"chat_id": channel, "title": "Daily News"})
    desc_ok = _set("setChatDescription", {"chat_id": channel, "description": CHANNEL_DESCRIPTION})
    print(f"setChatTitle: {title_ok}, setChatDescription: {desc_ok}", flush=True)
    photo_ok = None
    if photo_path:
        boundary = uuid.uuid4().hex
        with open(photo_path, "rb") as f:
            data = f.read()
        body = b""
        body += f"--{boundary}\r\n".encode()
        body += b'Content-Disposition: form-data; name="chat_id"\r\n\r\n' + channel.encode() + b"\r\n"
        body += f"--{boundary}\r\n".encode()
        body += b'Content-Disposition: form-data; name="photo"; filename="daily-news-profile-photo.png"\r\nContent-Type: image/png\r\n\r\n'
        body += data + b"\r\n"
        body += f"--{boundary}--\r\n".encode()
        req = urllib.request.Request(api_url("setChatPhoto"), data=body, method="POST")
        req.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")
        try:
            with urllib.request.build_opener(NoRedirect()).open(req, timeout=35) as resp:
                photo_ok = json.loads(resp.read().decode()).get("ok")
        except Exception as e:
            print(f"setChatPhoto failed: {e}", flush=True)
        print(f"setChatPhoto: {photo_ok}", flush=True)
    return bool(title_ok and desc_ok and (photo_ok is not False))


def set_bot_profile_photo(photo_path):
    """Set the bot's own profile photo. The photo field must be JSON
    {"type":"static","photo":"attach://botpic.png"} with the file part named
    botpic.png; a raw file upload or a file_id both fail with HTTP 400."""
    boundary = uuid.uuid4().hex
    with open(photo_path, "rb") as f:
        data = f.read()
    photo_json = json.dumps({"type": "static", "photo": "attach://botpic.png"})
    body = b""
    body += f"--{boundary}\r\n".encode()
    body += b'Content-Disposition: form-data; name="photo"\r\n\r\n' + photo_json.encode() + b"\r\n"
    body += f"--{boundary}\r\n".encode()
    body += b'Content-Disposition: form-data; name="botpic.png"; filename="botpic.png"\r\nContent-Type: image/png\r\n\r\n'
    body += data + b"\r\n"
    body += f"--{boundary}--\r\n".encode()
    req = urllib.request.Request(api_url("setMyProfilePhoto"), data=body, method="POST")
    req.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")
    try:
        with urllib.request.build_opener(NoRedirect()).open(req, timeout=35) as resp:
            ok = json.loads(resp.read().decode()).get("ok")
    except Exception as e:
        print(f"setMyProfilePhoto failed: {e}", flush=True)
        ok = False
    print(f"setMyProfilePhoto: {ok}", flush=True)
    return bool(ok)


MINI_APP_URL = "https://muse.ai/s/daily-news-mini-app-nl6exp9ekxafr/GQF2XII7qd6J-cqexZJxXqGi6D4u336n8ZitQWaNnQk"

BOT_COMMANDS = [
    ("start", "Start Daily News"),
    ("menu", "Show the quick menu"),
    ("hidemenu", "Hide the quick menu"),
    ("app", "Open the Daily News Mini App"),
    ("help", "All commands"),
    ("about", "About Daily News"),
    ("news", "AI and tech news"),
    ("ainews", "Latest AI model updates"),
    ("tech", "Tech news"),
    ("models", "AI models and changes"),
    ("freeai", "Free AI tools"),
    ("invite", "Free AI invite codes"),
    ("quote", "A daily quote"),
    ("love", "A love quote"),
    ("friend", "A friendship quote"),
    ("khmer", "A quote in Khmer"),
    ("quoteimg", "A quote as an image"),
    ("loveimg", "A love quote as an image"),
    ("friendimg", "A friendship quote as an image"),
    ("khmerimg", "A Khmer quote as an image"),
    ("story", "Today's story image"),
    ("daily", "Daily quote every morning"),
    ("stopdaily", "Stop daily quotes"),
    ("quiz", "Play a quiz (30 questions)"),
    ("guess", "Guess my number, 1 to 100"),
    ("rps", "Rock, paper, scissors"),
    ("dice", "Roll a dice"),
    ("darts", "Throw a dart"),
    ("bowling", "Bowl a strike"),
    ("basketball", "Shoot a hoop"),
    ("football", "Kick a goal"),
    ("slots", "Spin the slot machine"),
    ("scramble", "Unscramble the word"),
    ("poll", "Make a poll"),
    ("score", "Quiz scores"),
    ("ask", "Ask me a question"),
    ("stats", "Bot stats"),
    ("mystats", "Your stats"),
    ("top", "Top quiz players"),
    ("schedule", "Channel posting schedule"),
    ("channel", "Our channel"),
    ("remind", "Set a reminder"),
    ("joke", "A joke"),
    ("motivate", "Motivation"),
    ("newfed", "Create a federation"),
    ("joinfed", "Join a federation"),
    ("fedinfo", "Federation info"),
    ("fedlist", "List federations"),
    ("fedbans", "Federally banned users"),
    ("fban", "Ban in all fed groups"),
    ("unfban", "Unban in all fed groups"),
    ("save", "Save a note"),
    ("get", "Read a note"),
    ("notes", "List notes"),
    ("filter", "Add a keyword reply"),
    ("filters", "List filters"),
    ("stop", "Remove a filter"),
    ("captcha", "New member captcha on/off"),
    ("report", "Report a message to admins"),
    ("addblacklist", "Blacklist a word"),
    ("blacklist", "List blacklisted words"),
    ("blacklistmode", "Blacklist mode"),
    ("settings", "Group settings panel"),
    ("connect", "Manage a group from DM"),
    ("disconnect", "Stop managing from DM"),
    ("promote", "Promote a member"),
    ("demote", "Demote an admin"),
    ("purge", "Delete messages in bulk"),
    ("checkin", "Daily check-in +20 XP"),
    ("rank", "Your XP, level and streak"),
    ("leaderboard", "Top players"),
    ("tournament", "Start a trivia tournament"),
    ("endtournament", "End the tournament"),
    ("digest", "Your personal daily digest"),
    ("aimod", "AI moderation on/off"),
    ("check", "AI check on a message"),
    ("setlog", "Set the moderation log channel"),
    ("backup", "Back up group settings"),
    ("restore", "Restore group settings"),
    ("referral", "Invite friends, earn XP"),
    ("topreferrers", "Top inviters"),
    ("premium", "Premium with Telegram Stars"),
    ("broadcast", "Broadcast to all subscribers (owner)"),
    ("schedulepost", "Schedule a channel post (owner)"),
    ("scheduled", "List scheduled posts (owner)"),
    ("giveaway", "Start a giveaway in a group"),
    ("draw", "Draw the giveaway winner"),
    ("share", "Share Daily News with friends"),
    ("boost", "Referral boost on/off/auto (owner)"),
    ("locks", "List locked message types"),
    ("tban", "Temporary ban, auto-unban"),
    ("id", "Show your ID or this group's ID"),
    ("disable", "Disable a command in this group"),
    ("enable", "Re-enable a command"),
    ("approve", "Exempt a member from enforcement"),
    ("unapprove", "Remove an approval"),
    ("setflood", "Set the flood limit"),
    ("setgoodbye", "Set the goodbye message"),
    ("fsub", "Force members to join a channel"),
]


def set_bot_commands():
    """Set the Telegram command menu from BOT_COMMANDS. This is the Commands
    screen in @BotFather, done via the API instead."""
    commands = [{"command": c, "description": d} for c, d in BOT_COMMANDS]
    result = api_call("setMyCommands", {"commands": commands})
    print(f"setMyCommands: {result.get('ok')} ({len(commands)} commands)", flush=True)
    return bool(result.get("ok"))


def set_bot_profile(photo_path=None):
    """Fill the bot's own profile: short description, description, photo."""
    short_ok = api_call("setMyShortDescription", {"short_description": BOT_SHORT_DESCRIPTION}).get("ok")
    desc_ok = api_call("setMyDescription", {"description": BOT_DESCRIPTION}).get("ok")
    print(f"setMyShortDescription: {short_ok}, setMyDescription: {desc_ok}", flush=True)
    photo_ok = set_bot_profile_photo(photo_path) if photo_path else None
    return bool(short_ok and desc_ok and (photo_ok is not False))


def send_design():
    """Post the channel banner and welcome post, and pin the welcome post."""
    state = load_state()
    channel = state.get("channel_id")
    if not channel:
        print("no channel set for design post", flush=True)
        return 0
    path = make_channel_banner()
    try:
        result = send_photo(channel, path, caption=WELCOME_POST_TEXT)
        print(f"banner posted: {result.get('ok')}", flush=True)
        message_id = (result.get("result") or {}).get("message_id")
        if message_id:
            pin = api_call("pinChatMessage", {"chat_id": channel, "message_id": message_id, "disable_notification": True})
            print(f"welcome pinned: {pin.get('ok')}", flush=True)
        return 1 if result.get("ok") else 0
    finally:
        try:
            os.remove(path)
        except OSError:
            pass


def parse_duration(text):
    m = re.match(r"^(\d+)(s|m|h|d)$", text.strip().lower())
    if not m:
        return None
    value = int(m.group(1))
    unit = m.group(2)
    seconds = {"s": 1, "m": 60, "h": 3600, "d": 86400}[unit]
    return value * seconds


def answer_question(question, state, user_record=None):
    q = question.strip()
    lower_q = q.lower().rstrip("?")
    # Maths: "what is 12+8" or "12*7"
    math_match = re.search(r"(\d[\d\s\.\+\-\*\/\(\)\%]*\d|\d)", q)
    if "what is" in lower_q and math_match:
        result = safe_math(math_match.group(1))
        if result is not None:
            return f"{math_match.group(1).strip()} = {result}"
    if re.fullmatch(r"[0-9\s\.\+\-\*\/\(\)\%]+", q):
        result = safe_math(q)
        if result is not None:
            return f"{q.strip()} = {result}"
    for key, answer in KNOWLEDGE.items():
        if key in lower_q:
            return answer
    if "capital of" in lower_q:
        return "I don't know that capital yet. I know Cambodia, Thailand, Vietnam, France and Japan. Try /ask what is the capital of Cambodia?"
    if "what day" in lower_q or "what is today" in lower_q or "what is the date" in lower_q:
        now = phnom_penh_now()
        return f"Today is {now.strftime('%A, %d %B %Y')} in Phnom Penh."
    if "what time" in lower_q:
        now = phnom_penh_now()
        return f"It's {now.strftime('%I:%M %p')} in Phnom Penh."
    if "how many days until" in lower_q:
        if "christmas" in lower_q:
            target = datetime.datetime(2026, 12, 25, tzinfo=datetime.timezone(datetime.timedelta(hours=7)))
            days = (target.date() - phnom_penh_now().date()).days
            return f"{days} days until Christmas."
        if "new year" in lower_q:
            target = datetime.datetime(2027, 1, 1, tzinfo=datetime.timezone(datetime.timedelta(hours=7)))
            days = (target.date() - phnom_penh_now().date()).days
            return f"{days} days until New Year."
    # Save unknown questions so Muse can answer them later in chat
    state.setdefault("questions", []).append({"question": q, "time": int(time.time()), "user": (user_record or {}).get("name", "someone")})
    return (
        "Good question! I don't know that one yet, so I've saved it for Muse to answer. "
        "In the meantime, try /ask what is the capital of Cambodia? or /ask 12+8, or send /quote for a quote."
    )


def start_keyboard():
    return {
        "inline_keyboard": [
            [{"text": "Get a quote", "callback_data": "quote"}, {"text": "Love quote", "callback_data": "love"}],
            [{"text": "Quote image", "callback_data": "quoteimg"}, {"text": "Daily quotes", "callback_data": "daily"}],
            [{"text": "Quiz", "callback_data": "quiz"}, {"text": "About", "callback_data": "about"}],
        ]
    }


def main_menu_keyboard():
    """Persistent chat menu: quick buttons under the message box.

    The first button opens the Daily News Mini App right from the
    keyboard; the rest send their label as a message, which the bot
    answers like the matching command.
    """
    return {
        "keyboard": [
            [{"text": "📱 Daily News App", "web_app": {"url": MINI_APP_URL}}],
            [{"text": "💬 Quote"}, {"text": "📰 News"}, {"text": "🎮 Games"}],
            [{"text": "❤️ Love"}, {"text": "🤝 Friend"}, {"text": "🇰🇭 Khmer"}],
            [{"text": "🎯 Quiz"}, {"text": "📅 Schedule"}, {"text": "ℹ️ Help"}],
        ],
        "resize_keyboard": True,
        "is_persistent": True,
    }


def hide_menu_keyboard():
    return {"remove_keyboard": True}


GAMES_MENU_TEXT = (
    "🎮 Games — pick one:\n\n"
    "/quiz — a quiz question (30 questions!)\n"
    "/guess — guess my number, 1 to 100\n"
    "/rps — rock, paper, scissors\n"
    "/dice 🎲  /darts 🎯  /bowling 🎳\n"
    "/basketball 🏀  /football ⚽  /slots 🎰\n"
    "/scramble — unscramble the word\n\n"
    "Or tap 🎯 Quiz on the menu below."
)


def build_inline_results(query, state=None):
    """Inline query results for @MuseVishnu_bot typed in any chat.

    Returns a list of InlineQueryResultArticle dicts for answerInlineQuery.
    Query keywords route the results: love / friend / khmer / news / app.
    """
    q = (query or "").strip().lower()

    def article(rid, title, description, text, keyboard=None):
        result = {
            "type": "article",
            "id": rid,
            "title": title,
            "description": description,
            "input_message_content": {"message_text": text},
        }
        if keyboard:
            result["reply_markup"] = keyboard
        return result

    app_keyboard = {"inline_keyboard": [[{"text": "📱 Open Daily News App", "web_app": {"url": MINI_APP_URL}}]]}
    channel_keyboard = {"inline_keyboard": [[{"text": "📰 Open channel", "url": "https://t.me/dailynewsreporter"}]]}

    quote = pick_quote("any", state)
    love = pick_quote("love", state)
    friend = pick_quote("friend", state)
    khmer = pick_quote("khmer", state)

    results = []
    if "love" in q:
        results.append(article("love", "❤️ Love quote", love, f"❤️ {love}\n\n— Daily News @dailynewsreporter"))
    elif "friend" in q:
        results.append(article("friend", "🤝 Friendship quote", friend, f"🤝 {friend}\n\n— Daily News @dailynewsreporter"))
    elif "khmer" in q:
        results.append(article("khmer", "🇰🇭 Khmer quote", khmer, f"🇰🇭 {khmer}\n\n— Daily News @dailynewsreporter"))
    elif "news" in q or "app" in q:
        results.append(article("app", "📱 Daily News Mini App", "Quotes, AI news, free AI tools, quiz and schedule", "📱 Daily News — quotes, AI news, free AI tools and quiz, all in one app.", app_keyboard))
        results.append(article("channel", "📰 Daily News channel", "Tech, AI and quotes every day", "📰 Daily News @dailynewsreporter\nhttps://t.me/dailynewsreporter", channel_keyboard))
    if not results or "quote" in q or q == "":
        results.append(article("quote", "💬 Daily quote", quote, f"💬 {quote}\n\n— Daily News @dailynewsreporter"))
    if len(results) < 5 and not any(r["id"] == "love" for r in results):
        results.append(article("love", "❤️ Love quote", love, f"❤️ {love}\n\n— Daily News @dailynewsreporter"))
    if len(results) < 5 and not any(r["id"] == "friend" for r in results):
        results.append(article("friend", "🤝 Friendship quote", friend, f"🤝 {friend}\n\n— Daily News @dailynewsreporter"))
    if len(results) < 5 and not any(r["id"] == "app" for r in results):
        results.append(article("app", "📱 Daily News Mini App", "Quotes, AI news, free AI tools, quiz and schedule", "📱 Daily News — quotes, AI news, free AI tools and quiz, all in one app.", app_keyboard))
    if len(results) < 5 and not any(r["id"] == "channel" for r in results):
        results.append(article("channel", "📰 Daily News channel", "Tech, AI and quotes every day", "📰 Daily News @dailynewsreporter\nhttps://t.me/dailynewsreporter", channel_keyboard))
    return results[:5]


def fed_new_id(state):
    """A short random federation id, unique in state."""
    while True:
        fid = "fed_" + "".join(random.choice("0123456789abcdef") for _ in range(6))
        if fid not in state.get("federations", {}):
            return fid


def group_fed(state, chat_id):
    """Return (fed_id, fed) for the federation this group belongs to, or (None, None)."""
    for fid, fed in state.get("federations", {}).items():
        if chat_id in fed.get("groups", []):
            return fid, fed
    return None, None


def fed_ban_lookup(state, chat_id, user_id):
    """Return (fed_id, fed, ban) if user_id is federally banned in chat_id's fed."""
    fid, fed = group_fed(state, chat_id)
    if not fed:
        return None, None, None
    ban = fed.get("banned", {}).get(str(user_id))
    return fid, fed, ban


def admin_list(chat_id, dry_run=False):
    """User ids of group admins (for /report). Fake list in dry-run mode."""
    if dry_run:
        return [111]
    try:
        result = api_call("getChatAdministrators", {"chat_id": chat_id})
    except Exception:
        return []
    return [(a.get("user") or {}).get("id") for a in result.get("result", []) if (a.get("user") or {}).get("id")]


def resolve_group(args, dry_run=False):
    """Resolve a /connect target: a chat id, or @username (live getChat)."""
    parts = args.split()
    if not parts:
        return None, None
    arg = parts[0]
    if arg.lstrip("-").isdigit():
        return int(arg), arg
    if arg.startswith("@"):
        if dry_run:
            return -100123, arg
        try:
            result = api_call("getChat", {"chat_id": arg})
            ch = result.get("result", {})
            return ch.get("id"), ch.get("title") or arg
        except Exception:
            return None, None
    return None, None


def settings_keyboard(gid, settings):
    def tick(on):
        return "✅" if on else "❌"
    return {"inline_keyboard": [
        [{"text": f"🔗 Anti-link: {tick(settings.get('antilink'))}", "callback_data": f"set:{gid}:antilink"}],
        [{"text": f"🛡 Anti-spam: {tick(settings.get('antispam'))}", "callback_data": f"set:{gid}:antispam"}],
        [{"text": f"🤖 Captcha: {tick(settings.get('captcha'))}", "callback_data": f"set:{gid}:captcha"}],
        [{"text": f"🚨 Reports: {tick(settings.get('reports'))}", "callback_data": f"set:{gid}:reports"}],
        [{"text": f"🚫 Blacklist: {settings.get('blacklist_mode', 'delete')}", "callback_data": f"set:{gid}:blacklist_mode"}],
        [{"text": "❌ Close", "callback_data": f"set:{gid}:close"}],
    ]}


def settings_text(gid, settings):
    lines = [
        f"⚙️ Group settings (chat {gid})",
        "",
        f"🔗 Anti-link: {'on' if settings.get('antilink') else 'off'}",
        f"🛡 Anti-spam: {'on' if settings.get('antispam') else 'off'}",
        f"🤖 Captcha: {'on' if settings.get('captcha') else 'off'} — new members tap a button to prove they are human",
        f"🚨 Reports: {'on' if settings.get('reports') else 'off'} — members can /report a message to admins",
        f"🚫 Blacklist mode: {settings.get('blacklist_mode', 'delete')} — what happens on a blacklisted word",
        f"📝 Notes: {len(settings.get('notes', {}))} saved",
        f"🔤 Filters: {len(settings.get('filters', {}))} saved",
        f"🚫 Blacklisted words: {len(settings.get('blacklist_words', []))}",
        f"🧩 Blacklisted stickers: {len(settings.get('blacklist_stickers', []))}",
        "",
        "Tap a button to change a setting.",
    ]
    return "\n".join(lines)


def xp_level(xp):
    """Level from XP: level n needs 50*(n-1)^2 XP. L1 at 0, L2 at 50, L3 at 200, L4 at 450."""
    return int((max(0, xp) / 50) ** 0.5) + 1


def xp_for_next_level(level):
    """XP needed to reach this level."""
    return 50 * level * level


def xp_amount(state, uid, base):
    """Premium members earn double XP."""
    return base * 2 if str(uid) in state.get("premium", {}) else base


def award_xp(state, uid, name, amount):
    """Add XP (doubled for Premium members); return the new level if the user just levelled up, else None."""
    if not uid:
        return None
    rec = state.setdefault("xp", {}).setdefault(str(uid), {"xp": 0, "name": name or "friend", "last_msg": 0})
    rec["name"] = name or rec.get("name", "friend")
    before = xp_level(rec["xp"])
    rec["xp"] += xp_amount(state, uid, amount)
    after = xp_level(rec["xp"])
    return after if after > before else None


def award_message_xp(state, uid, name):
    """+2 XP per message, at most once every 60 seconds (anti-farm). Returns new level on level-up."""
    if not uid:
        return None
    rec = state.setdefault("xp", {}).setdefault(str(uid), {"xp": 0, "name": name or "friend", "last_msg": 0})
    now_ts = int(time.time())
    if now_ts - rec.get("last_msg", 0) < 60:
        return None
    rec["last_msg"] = now_ts
    return award_xp(state, uid, name, 2)


def xp_record(state, uid):
    return state.get("xp", {}).get(str(uid), {"xp": 0, "name": "friend"})


def referral_boost_active(state):
    """Double referral rewards: forced on/off, otherwise automatic on weekends (Phnom Penh)."""
    flag = state.get("referral_boost")
    if flag is True:
        return True
    if flag is False:
        return False
    return phnom_penh_now().weekday() >= 5


def parse_prize(args):
    """Giveaway prize: 'premium' -> lifetime Premium, a number -> that much XP, anything else -> custom prize (+50 XP for the winner)."""
    text = args.strip()
    if text.lower() == "premium":
        return "⭐ Premium (lifetime)", "premium", 0
    if re.fullmatch(r"\d+", text):
        return f"+{text} XP", "xp", int(text)
    return text, "custom", 50


def referral_code(state, uid, name):
    """Each user's permanent invite code, created once and stored."""
    rec = state.setdefault("referrals", {}).setdefault(str(uid), {"code": "", "count": 0, "referred": [], "name": name or "friend"})
    if not rec.get("code"):
        alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
        rec["code"] = "DN" + "".join(random.choice(alphabet) for _ in range(6))
    rec["name"] = name or rec.get("name", "friend")
    return rec["code"]


def rank_text(state, uid, name):
    rec = xp_record(state, uid)
    xp = rec["xp"]
    level = xp_level(xp)
    nxt = xp_for_next_level(level)
    ranked = sorted(state.get("xp", {}).items(), key=lambda kv: kv[1]["xp"], reverse=True)
    rank = next((i + 1 for i, (k, _v) in enumerate(ranked) if k == str(uid)), len(ranked))
    streak = state.get("streaks", {}).get(str(uid), {"count": 0})
    premium = " ⭐ Premium" if str(uid) in state.get("premium", {}) else ""
    return (
        f"🏆 {name}'s rank{premium}\n\n"
        f"⭐ Level {level} — {xp} XP\n"
        f"Next level: {nxt - xp} XP to go (Level {level + 1} at {nxt} XP)\n"
        f"Global rank: #{rank} of {len(ranked)}\n"
        f"🔥 Daily streak: {streak.get('count', 0)} day(s) — /checkin every day to keep it alive"
    )


def leaderboard_text(state):
    xp = state.get("xp", {})
    if not xp:
        return "No XP yet. Chat, play games and answer quizzes to earn XP! /checkin gives +20 XP every day."
    ranked = sorted(xp.items(), key=lambda kv: kv[1]["xp"], reverse=True)[:10]
    lines = ["🏆 XP Leaderboard\n"]
    medals = ["🥇", "🥈", "🥉"]
    for i, (k, r) in enumerate(ranked):
        medal = medals[i] if i < 3 else f"{i + 1}."
        star = " ⭐" if k in state.get("premium", {}) else ""
        lines.append(f"{medal} {r.get('name', 'friend')}{star} — Level {xp_level(r['xp'])} ({r['xp']} XP)")
    streaks = state.get("streaks", {})
    if streaks:
        top_streak = sorted(streaks.values(), key=lambda s: s.get("count", 0), reverse=True)[:5]
        lines.append("\n🔥 Longest streaks:")
        for i, s in enumerate(top_streak):
            lines.append(f"{i + 1}. {s.get('name', 'friend')} — {s.get('count', 0)} day(s)")
    return "\n".join(lines)


def tournament_standings(t):
    players = sorted(t.get("players", {}).values(), key=lambda p: p["score"], reverse=True)
    if not players:
        return "No scores yet — be the first to answer!"
    return "\n".join(f"{i + 1}. {p['name']}: {p['score']} point(s)" for i, p in enumerate(players))


def ai_chat(prompt, dry_run=False):
    """Ask a real AI model. In dry-run (tests) returns a canned reply, no network.
    Live: if OPENAI_API_KEY is set, use an OpenAI-compatible chat API
    (OPENAI_BASE_URL, default https://api.openai.com/v1); otherwise use the
    free Pollinations text API (no key needed). Returns the answer text or None."""
    if dry_run:
        return "[test AI reply in Khmer]" if prompt.startswith("Reply in Khmer") else "[test AI reply]"
    if not prompt or not prompt.strip():
        return None
    key = os.environ.get("OPENAI_API_KEY", "").strip()
    if key:
        try:
            base = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/")
            model = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")
            req = urllib.request.Request(
                base + "/chat/completions",
                data=json.dumps({"model": model, "messages": [{"role": "user", "content": prompt[:2000]}]}).encode(),
                headers={"Content-Type": "application/json", "Authorization": "Bearer " + key},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=20) as resp:
                data = json.loads(resp.read().decode())
            return (data.get("choices") or [{}])[0].get("message", {}).get("content", "").strip() or None
        except Exception as e:
            print(f"openai chat failed: {e}", flush=True)
            return None
    try:
        url = "https://text.pollinations.ai/" + urllib.parse.quote(prompt[:2000])
        req = urllib.request.Request(url, headers={"Accept": "text/plain"})
        with urllib.request.urlopen(req, timeout=20) as resp:
            text = resp.read().decode(errors="replace").strip()
        return text or None
    except Exception as e:
        print(f"pollinations chat failed: {e}", flush=True)
        return None


def parse_schedule_time(args):
    """Parse 'HH:MM text' or 'tomorrow HH:MM text' (Asia/Phnom_Penh) -> (epoch, text) or (None, error)."""
    m = re.match(r"^(?:(tomorrow)\s+)?(\d{1,2}):(\d{2})\s+(.+)$", args.strip(), re.S)
    if not m:
        return None, "Use: /schedulepost 18:30 Your message — or /schedulepost tomorrow 09:00 Your message"
    tomorrow, hh, mm, text = m.group(1), int(m.group(2)), int(m.group(3)), m.group(4).strip()
    if hh > 23 or mm > 59 or not text:
        return None, "That time doesn't look right. Use HH:MM, like 18:30."
    now = phnom_penh_now()
    base = now + datetime.timedelta(days=1) if tomorrow else now
    target = base.replace(hour=hh, minute=mm, second=0, microsecond=0)
    if not tomorrow and target <= now:
        target += datetime.timedelta(days=1)
    return int(target.timestamp()), text


def check_scheduled_posts(state):
    """Posts whose time has come: send them to the channel and drop them from the queue."""
    now = int(time.time())
    channel_id = state.get("channel_id")
    actions = []
    due = [p for p in state.get("scheduled_posts", []) if p.get("at", 0) <= now]
    if not due or not channel_id:
        return actions
    state["scheduled_posts"] = [p for p in state.get("scheduled_posts", []) if p.get("at", 0) > now]
    for p in due:
        actions.append({"type": "text", "chat_id": channel_id, "text": p.get("text", "")})
    return actions


def send_scheduled():
    """Send any due scheduled channel posts. Returns the number sent."""
    state = load_state()
    actions = check_scheduled_posts(state)
    if not actions:
        return 0
    execute_actions(actions, state)
    save_state(state)
    return len(actions)


def ai_reply(state, uid, prompt, dry_run=False):
    """Ask the AI in the user's preferred language (Khmer if they asked for it)."""
    lang = (state.get("users", {}).get(str(uid), {}) or {}).get("lang", "en")
    if lang == "kh":
        prompt = "Reply in Khmer language. " + prompt
    return ai_chat(prompt, dry_run=dry_run)


def ai_moderate(text, dry_run=False):
    """Ask the AI whether a message is spam/scam/abuse. Returns (verdict, reason).
    Verdict is one of: spam, scam, abuse, harmless. Fail-open: on any error, harmless."""
    if dry_run:
        return ("spam", "test mode: contains the word spam") if "spam" in (text or "").lower() else ("harmless", "test mode")
    if not text or not text.strip():
        return ("harmless", "empty message")
    prompt = (
        "You moderate a Telegram group. Classify this message with exactly one word: "
        "spam (ads, crypto giveaways, repeated promotions), scam (fake giveaways, phishing, impersonation), "
        "abuse (insults, threats, hate), or harmless. Then a short reason after a dash.\n\nMessage: " + text[:1000]
    )
    answer = ai_chat(prompt)
    if not answer:
        return ("harmless", "AI check failed, allowed")
    first = answer.strip().split()[0].lower().strip(".,:") if answer.strip() else "harmless"
    verdict = first if first in ("spam", "scam", "abuse") else "harmless"
    return (verdict, answer.strip())


def log_event(state, actions, chat_id, text):
    """If a log channel is set for this group, queue a log message there."""
    log_id = state.get("log_channels", {}).get(str(chat_id))
    if log_id:
        actions.append({"type": "text", "chat_id": log_id, "text": text})


def handle_update(update, state, dry_run=False):
    """Return a list of action dicts for one update."""
    actions = []

    # Inline queries: @MuseVishnu_bot typed in any chat
    if "inline_query" in update:
        iq = update["inline_query"]
        results = build_inline_results(iq.get("query", ""), state)
        actions.append({"type": "inline", "inline_query_id": iq.get("id"), "results": results})
        return actions

    # Poll answers for quiz scoring (+ XP, + tournament points)
    if "poll_answer" in update:
        answer = update["poll_answer"]
        poll_id = answer.get("poll_id")
        user = answer.get("user", {})
        quiz = state.get("quiz_polls", {}).get(poll_id)
        if quiz:
            chosen = answer.get("option_ids", [])
            correct = chosen and chosen[0] == quiz["correct"]
            uid = str(user.get("id"))
            score = state["scores"].setdefault(uid, {"name": user.get("first_name", "Player"), "correct": 0, "total": 0})
            score["total"] += 1
            if correct:
                score["correct"] += 1
                leveled = award_xp(state, user.get("id"), user.get("first_name", "Player"), 10)
                amt = xp_amount(state, user.get("id"), 10)
                msg = f"Correct, {score['name']}! {quiz.get('explain', '')} Your score: {score['correct']}/{score['total']} (+{amt} XP)"
                if leveled:
                    msg += f"\n🎉 LEVEL UP! You are now Level {leveled}!"
                actions.append({"type": "text", "chat_id": quiz["chat_id"], "text": msg})
            else:
                actions.append({"type": "text", "chat_id": quiz["chat_id"], "text": f"Not quite, {score['name']}. The answer was: {quiz['options'][quiz['correct']]}. Your score: {score['correct']}/{score['total']}"})
            # Tournament: first correct answer per question scores a point and advances.
            tchat = state.get("tournament_polls", {}).get(poll_id)
            if tchat is not None and correct:
                t = state.get("tournaments", {}).get(str(tchat))
                if t and poll_id not in t.get("done_polls", []):
                    t.setdefault("done_polls", []).append(poll_id)
                    player = t.setdefault("players", {}).setdefault(uid, {"name": user.get("first_name", "Player"), "score": 0})
                    player["score"] += 1
                    player["name"] = user.get("first_name", "Player")
                    if t["round"] >= t["total"]:
                        winner_uid = max(t["players"], key=lambda k: t["players"][k]["score"])
                        winner = t["players"][winner_uid]
                        award_xp(state, winner_uid, winner["name"], 50)
                        actions.append({"type": "text", "chat_id": tchat, "text": f"🏆 Tournament over!\n\n{tournament_standings(t)}\n\nWinner: {winner['name']} — +{xp_amount(state, winner_uid, 50)} XP!"})
                        for pid in list(t.get("done_polls", [])):
                            state.get("tournament_polls", {}).pop(pid, None)
                        state.get("tournaments", {}).pop(str(tchat), None)
                    else:
                        t["round"] += 1
                        q = random.choice(QUIZ_QUESTIONS)
                        actions.append({"type": "text", "chat_id": tchat, "text": f"✅ {player['name']} got it! +1 point (Round {t['round'] - 1}/{t['total']}).\n\n{tournament_standings(t)}\n\nNext question — Round {t['round']}:"})
                        actions.append({"type": "quiz", "chat_id": tchat, "question": q, "tournament": True})
        return actions

    # Telegram Stars pre-checkout: approve every payment.
    if "pre_checkout_query" in update:
        pcq = update["pre_checkout_query"]
        actions.append({"type": "precheckout", "query_id": pcq.get("id"), "ok": True})
        return actions

    # Inline button taps
    if "callback_query" in update:
        cb = update["callback_query"]
        data = cb.get("data", "")
        chat_id = (cb.get("message") or {}).get("chat", {}).get("id")
        if not chat_id:
            return actions
        actions.append({"type": "callback", "callback_id": cb.get("id"), "text": "Got it!"})
        if data.startswith("giveaway_join:"):
            gid = data.split(":", 1)[1]
            g = state.get("giveaways", {}).get(gid)
            uid = (cb.get("from") or {}).get("id")
            if not g:
                actions[-1]["text"] = "This giveaway is over."
            elif uid and str(uid) not in [str(e) for e in g.get("entries", [])]:
                g.setdefault("entries", []).append(uid)
                g.setdefault("names", {})[str(uid)] = (cb.get("from") or {}).get("first_name", "friend")
                actions[-1]["text"] = "You're in! Good luck! 🍀"
            else:
                actions[-1]["text"] = "You're already in! 🍀"
        elif data == "quote":
            actions.append({"type": "text", "chat_id": chat_id, "text": pick_quote("any")})
        elif data == "love":
            actions.append({"type": "text", "chat_id": chat_id, "text": pick_quote("love")})
        elif data == "quoteimg":
            _q = pick_quote("any")
            actions.append({"type": "photo", "chat_id": chat_id, "quote": _q, "caption": channel_caption(_q, "daily")})
        elif data == "about":
            actions.append({"type": "text", "chat_id": chat_id, "text": ABOUT_TEXT})
        elif data == "daily":
            if chat_id not in state["subscribers"]:
                state["subscribers"].append(chat_id)
            actions.append({"type": "text", "chat_id": chat_id, "text": "You're in! I'll send you a daily quote every morning. Use /stopdaily anytime to stop."})
        elif data == "quiz":
            q = random.choice(QUIZ_QUESTIONS)
            actions.append({"type": "quiz", "chat_id": chat_id, "question": q})
        elif data.startswith("rps:"):
            player = data.split(":", 1)[1]
            bot = random.choice(RPS_CHOICES)
            result = rps_result(player, bot)
            if result == "win":
                verdict = "You win! 🎉"
            elif result == "lose":
                verdict = "I win! 😄"
            else:
                verdict = "It's a draw! 🤝"
            actions.append({"type": "text", "chat_id": chat_id, "text": f"{RPS_EMOJI[player]} vs {RPS_EMOJI[bot]}\n{verdict}\n\nPlay again with /rps."})
            if result == "win":
                winner_id = (cb.get("from") or {}).get("id")
                leveled = award_xp(state, winner_id, (cb.get("from") or {}).get("first_name", "Player"), 5)
                if leveled:
                    actions.append({"type": "text", "chat_id": chat_id, "text": f"🎉 LEVEL UP! You are now Level {leveled}! (+{xp_amount(state, winner_id, 5)} XP for the win)"})
        elif data.startswith("verify:"):
            # Captcha button: only the new member themself can tap it.
            parts = data.split(":")
            gid = int(parts[1]) if len(parts) > 1 and parts[1].lstrip("-").isdigit() else None
            uid = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else None
            tapper = (cb.get("from") or {}).get("id")
            msg_id = (cb.get("message") or {}).get("message_id")
            if gid is None or uid is None or tapper != uid:
                actions.append({"type": "text", "chat_id": chat_id, "text": "Only the new member can tap that button."})
            else:
                state.get("pending_verify", {}).pop(f"{gid}:{uid}", None)
                actions.append({"type": "restrict", "chat_id": gid, "user_id": uid, "until": None, "permissions": {"can_send_messages": True, "can_send_media_messages": True, "can_send_polls": True, "can_send_other_messages": True, "can_add_web_page_previews": True}})
                name = (cb.get("from") or {}).get("first_name", "friend")
                if msg_id:
                    actions.append({"type": "edit", "chat_id": chat_id, "message_id": msg_id, "text": f"✅ {name} is verified — welcome!"})
                custom = get_group_settings(state, gid).get("welcome", "")
                welcome = custom.replace("{name}", name) if custom else f"Welcome, {name}! So glad you're here. Try /quote for a little kindness, or /help to see what I can do."
                actions.append({"type": "text", "chat_id": gid, "text": welcome})
        elif data.startswith("set:"):
            # Settings panel button: set:<group id>:<key>
            parts = data.split(":")
            gid = int(parts[1]) if len(parts) > 1 and parts[1].lstrip("-").isdigit() else None
            key = parts[2] if len(parts) > 2 else ""
            tapper = (cb.get("from") or {}).get("id")
            msg_id = (cb.get("message") or {}).get("message_id")
            if gid is None or not key or not is_admin(gid, tapper, dry_run=dry_run):
                actions.append({"type": "text", "chat_id": chat_id, "text": "Only group admins can change settings."})
            elif key == "close":
                if msg_id:
                    actions.append({"type": "edit", "chat_id": chat_id, "message_id": msg_id, "text": "Settings closed. Send /settings to open them again."})
            else:
                settings = get_group_settings(state, gid)
                if key in ("antilink", "antispam", "captcha", "reports"):
                    settings[key] = not settings.get(key, False)
                elif key == "blacklist_mode":
                    modes = ["delete", "warn", "mute"]
                    cur = settings.get("blacklist_mode", "delete")
                    settings["blacklist_mode"] = modes[(modes.index(cur) + 1) % len(modes)] if cur in modes else "delete"
                if msg_id:
                    actions.append({"type": "edit", "chat_id": chat_id, "message_id": msg_id, "text": settings_text(gid, settings), "keyboard": settings_keyboard(gid, settings)})
        return actions

    message = update.get("message") or update.get("channel_post") or {}
    chat = message.get("chat", {})
    chat_id = chat.get("id")
    if not chat_id:
        return actions
    # Anonymous group admins post as the group itself (sender_chat == the group, no "from").
    _schat = (message.get("sender_chat") or {}).get("id")

    new_members = message.get("new_chat_members", [])
    for member in new_members:
        name = member.get("first_name", "friend")
        member_id = member.get("id")
        settings = get_group_settings(state, chat_id)
        # Federation ban: banned in one fed group = banned in all of them
        _fid, _fed, _ban = fed_ban_lookup(state, chat_id, member_id)
        if _ban:
            actions.append({"type": "ban", "chat_id": chat_id, "user_id": member_id})
            actions.append({"type": "text", "chat_id": chat_id, "text": f"🚫 {name} was banned on joining — federally banned ({_ban.get('reason') or 'no reason given'})."})
            continue
        # Bots lock: ban bots on join (admins, including this bot itself, are exempt)
        if member.get("is_bot") and settings.get("locks", {}).get("bots") and not is_admin(chat_id, member_id, dry_run=dry_run):
            actions.append({"type": "ban", "chat_id": chat_id, "user_id": member_id})
            actions.append({"type": "text", "chat_id": chat_id, "text": f"🤖 {name} was banned — bots are not allowed here."})
            continue
        # Captcha: mute until the new member taps the button
        if settings.get("captcha") and member_id:
            state.setdefault("pending_verify", {})[f"{chat_id}:{member_id}"] = {"name": name, "at": int(time.time())}
            actions.append({"type": "restrict", "chat_id": chat_id, "user_id": member_id, "until": None, "permissions": {"can_send_messages": False, "can_send_media_messages": False, "can_send_polls": False, "can_send_other_messages": False, "can_add_web_page_previews": False}})
            keyboard = {"inline_keyboard": [[{"text": "✅ I'm human — let me in", "callback_data": f"verify:{chat_id}:{member_id}"}]]}
            actions.append({"type": "text", "chat_id": chat_id, "text": f"👋 Hi {name}! Tap the button below to prove you're human and join the chat.", "keyboard": keyboard})
            continue
        custom = settings.get("welcome", "")
        welcome = custom.replace("{name}", name) if custom else f"Welcome, {name}! So glad you're here. Try /quote for a little kindness, or /help to see what I can do."
        actions.append({"type": "text", "chat_id": chat_id, "text": welcome})
    if new_members:
        return actions

    # Goodbye message when a member leaves
    left = message.get("left_chat_member") or {}
    if left:
        goodbye = get_group_settings(state, chat_id).get("goodbye", "")
        if goodbye:
            actions.append({"type": "text", "chat_id": chat_id, "text": goodbye.replace("{name}", left.get("first_name", "friend"))})
        return actions

    # Temporary bans: lift expired ones on the next update in the group.
    _tb = state.get("temp_bans", {}).get(str(chat_id), {})
    _now = int(time.time())
    for _uid in [u for u, until in _tb.items() if until <= _now]:
        actions.append({"type": "unban", "chat_id": chat_id, "user_id": int(_uid)})
        del _tb[_uid]

    # Granular content locks (Rose-style): run before the sticker/text gates so media is covered too.
    if chat.get("type", "") in ("group", "supergroup"):
        hit = locked_type_hit(state, chat_id, (message.get("from") or {}).get("id"), message, dry_run=dry_run)
        if hit:
            actions.append({"type": "delete", "chat_id": chat_id, "message_id": message.get("message_id")})
            return actions

    # Force subscribe (Rose-style): non-members get checked and their message removed.
    if chat.get("type", "") in ("group", "supergroup"):
        _fs = get_group_settings(state, chat_id).get("fsub", "")
        _fu = (message.get("from") or {}).get("id")
        if _fs and not is_privileged(state, chat_id, _fu, dry_run=dry_run, sender_chat_id=_schat):
            actions.append({"type": "fsub_check", "chat_id": chat_id, "user_id": _fu, "message_id": message.get("message_id"), "channel": _fs})
            return actions

    sticker = message.get("sticker") or {}
    if sticker:
        # Stickers have no text: only the blacklist can apply, then stop.
        _ctype = chat.get("type", "")
        _sid = (message.get("from") or {}).get("id")
        if _ctype in ("group", "supergroup"):
            settings = get_group_settings(state, chat_id)
            if not is_admin(chat_id, _sid, dry_run=dry_run, sender_chat_id=_schat) and sticker.get("file_unique_id") in settings.get("blacklist_stickers", []):
                actions.append({"type": "delete", "chat_id": chat_id, "message_id": message.get("message_id")})
        return actions

    # Telegram Stars payment completed: unlock Premium.
    if message.get("successful_payment"):
        pay = message["successful_payment"]
        buyer = message.get("from") or {}
        buyer_id = buyer.get("id")
        if pay.get("invoice_payload", "").startswith("premium_") and buyer_id:
            # Welcome bonus first (flat +50), then unlock Premium (double XP after).
            award_xp(state, buyer_id, buyer.get("first_name", "friend"), 50)
            state.setdefault("premium", {})[str(buyer_id)] = {"since": int(time.time()), "stars": pay.get("total_amount", 0)}
            actions.append({"type": "text", "chat_id": chat_id, "text": "⭐ Welcome to Premium! Lifetime access unlocked: double XP, a ⭐ badge, and early access to new features. Thank you for supporting Daily News! (+50 XP)"})
        return actions

    text = (message.get("text") or "").strip()
    if not text:
        return actions

    first_word_early = text.split()[0].lower().split("@")[0]
    user_record = track_user(state, message, command=first_word_early if first_word_early.startswith("/") else "")

    # Anti-spam and anti-link, groups only, non-admins only
    chat_type = chat.get("type", "")
    sender = message.get("from") or {}
    sender_id = sender.get("id")
    # Groups managed from a private chat: /connect stores the group here.
    connected_group = None
    if chat_type == "private" and sender_id:
        connected_group = state.get("connections", {}).get(str(sender_id))
    admin_chat = connected_group if connected_group else chat_id
    # +2 XP per message (60s cooldown); level-ups are announced at the end.
    msg_leveled = award_message_xp(state, sender_id, sender.get("first_name", "friend"))
    if chat_type in ("group", "supergroup") and not text.startswith("/"):
        settings = get_group_settings(state, chat_id)
        admin = is_privileged(state, chat_id, sender_id, dry_run=dry_run, sender_chat_id=_schat)
        if not admin and settings.get("antilink") and ("http://" in text or "https://" in text or "t.me/" in text):
            actions.append({"type": "delete", "chat_id": chat_id, "message_id": message.get("message_id")})
            actions.append({"type": "text", "chat_id": chat_id, "text": f"Links are not allowed here, {sender.get('first_name', 'friend')}."})
            return actions
        if not admin and settings.get("antispam"):
            now_ts = int(time.time())
            key = (chat_id, sender_id)
            times = [t for t in FLOOD_TRACKER.get(key, []) if now_ts - t < 10]
            times.append(now_ts)
            FLOOD_TRACKER[key] = times
            if len(times) > settings.get("flood_limit", 5):
                FLOOD_TRACKER[key] = []
                until = now_ts + 300
                actions.append({"type": "restrict", "chat_id": chat_id, "user_id": sender_id, "until": until, "permissions": {"can_send_messages": False}})
                actions.append({"type": "text", "chat_id": chat_id, "text": f"{sender.get('first_name', 'friend')} was muted for 5 minutes for flooding."})
                return actions
        # Menu taps and game answers must not be eaten by filters or blacklists.
        menu_taps = ("💬 Quote", "❤️ Love", "🤝 Friend", "🇰🇭 Khmer", "📰 News", "🎮 Games", "🎯 Quiz", "📅 Schedule", "ℹ️ Help")
        if text not in menu_taps:
            # AI moderation (opt-in): check link messages from non-admins
            if not admin and settings.get("aimod") and ("http://" in text or "https://" in text or "t.me/" in text):
                verdict, reason = ai_moderate(text, dry_run=dry_run)
                if verdict in ("spam", "scam", "abuse"):
                    actions.append({"type": "delete", "chat_id": chat_id, "message_id": message.get("message_id")})
                    actions.append({"type": "text", "chat_id": chat_id, "text": f"🤖 AI moderation removed this message ({verdict})."})
                    log_event(state, actions, chat_id, f"🤖 AI moderation removed a {verdict} message from {sender.get('first_name', 'someone')} in {chat.get('title', chat_id)}.")
                    return actions
            # Blacklisted words (non-admins only): delete, warn or mute per mode
            if not admin:
                words = settings.get("blacklist_words", [])
                hit = next((w for w in words if w.lower() in text.lower()), None)
                if hit:
                    actions.append({"type": "delete", "chat_id": chat_id, "message_id": message.get("message_id")})
                    mode = settings.get("blacklist_mode", "delete")
                    if mode == "warn":
                        actions.append({"type": "text", "chat_id": chat_id, "text": f"⚠️ {sender.get('first_name', 'friend')}, that word is not allowed here."})
                    elif mode == "mute":
                        actions.append({"type": "restrict", "chat_id": chat_id, "user_id": sender_id, "until": int(time.time()) + 3600, "permissions": {"can_send_messages": False}})
                        actions.append({"type": "text", "chat_id": chat_id, "text": f"{sender.get('first_name', 'friend')} was muted for 1 hour for a blacklisted word."})
                    return actions
            # Notes via #name
            if text.startswith("#"):
                note = settings.get("notes", {}).get(text[1:].split()[0].lower())
                if note:
                    actions.append({"type": "text", "chat_id": chat_id, "text": note.replace("{name}", sender.get("first_name", "friend"))})
                    return actions
            # Keyword filters: auto-reply when a saved word appears
            for word, response in settings.get("filters", {}).items():
                if word.lower() in text.lower():
                    actions.append({"type": "text", "chat_id": chat_id, "text": response.replace("{name}", sender.get("first_name", "friend"))})
                    return actions

    first_word = text.split()[0]
    command = first_word.lower()
    if "@" in command:
        command = command.split("@")[0]
    args = text[len(first_word):].strip()
    lower = text.lower()

    # Disabled commands (Rose-style): deleted in groups, admins bypass.
    if chat_type in ("group", "supergroup") and command.startswith("/"):
        if command in get_group_settings(state, chat_id).get("disabled", []) and not is_admin(chat_id, sender_id, dry_run=dry_run, sender_chat_id=_schat):
            actions.append({"type": "delete", "chat_id": chat_id, "message_id": message.get("message_id")})
            return actions

    def reply(msg, keyboard=None):
        action = {"type": "text", "chat_id": chat_id, "text": msg}
        if keyboard:
            action["keyboard"] = keyboard
        actions.append(action)

    if command == "/start":
        first = (message.get("from") or {}).get("first_name", "friend")
        my_code = referral_code(state, sender_id, first)
        extra = ""
        if args.startswith("ref_"):
            code = args[4:].strip()
            ref_uid = next((k for k, v in state.get("referrals", {}).items() if v.get("code") == code), None)
            if ref_uid and ref_uid != str(sender_id) and str(sender_id) not in state.get("referred_by", {}) and user_record and user_record.get("messages", 0) <= 1:
                state.setdefault("referred_by", {})[str(sender_id)] = ref_uid
                ref_rec = state["referrals"][ref_uid]
                ref_rec.setdefault("referred", []).append(str(sender_id))
                ref_rec["count"] = len(ref_rec["referred"])
                boost = referral_boost_active(state)
                base_ref = 100 if boost else 50
                base_new = 40 if boost else 20
                amt_ref = xp_amount(state, ref_uid, base_ref)
                amt_new = xp_amount(state, sender_id, base_new)
                award_xp(state, ref_uid, ref_rec.get("name", "friend"), base_ref)
                award_xp(state, sender_id, first, base_new)
                extra = f"\n\n🎉 You joined through {ref_rec.get('name', 'a friend')}'s invite! You got +{amt_new} XP and they got +{amt_ref} XP." + (" 🔥 Referral boost is ON — double rewards!" if boost else "")
                actions.append({"type": "text", "chat_id": int(ref_uid), "text": f"🎉 {first} joined with your invite! +{amt_ref} XP. You have invited {ref_rec['count']} friend(s)."})
        reply(f"Hi {first}, welcome to Daily News.\n\n{HELP_TEXT}\n\n🔗 Invite friends, earn XP: https://t.me/MuseVishnu_bot?start=ref_{my_code} (see /referral){extra}", keyboard=start_keyboard())
    elif command == "/help":
        reply(HELP_TEXT)
    elif command == "/about":
        reply(ABOUT_TEXT)
    elif command == "/news":
        reply(NEWS_TEXT)
    elif command in ("/ainews", "/tech"):
        reply(MODELS_TEXT)
    elif command == "/models":
        reply(MODELS_TEXT)
    elif command == "/freeai":
        reply(FREE_AI_TEXT)
    elif command == "/invite":
        codes = state.get("invite_codes", [])
        if not codes:
            reply(
                "Free AI invite codes\n\n"
                "No codes are saved in the bot right now. When the owner adds a real code, it will appear here.\n\n"
                "About Muse AI: Muse is Meta's personal AI agent, free with usage limits. In the US, Canada and Japan you can join at muse.ai without an invite code. A referral code, redeemed in Settings within 48 hours of joining, is reported to give you and the sharer 1 billion bonus tokens each. Each code is reported to work about 20 times, then it is used up.\n\n"
                "Never pay for a code. Codes shared online may already be used up — try another one if the first fails."
            )
        else:
            lines = ["Free AI invite codes — shared by the owner, try them fast, each code works a limited number of times:\n"]
            lines.extend(f"• {c.get('service', 'AI')}: {c.get('code', '')}" for c in codes)
            lines.append("\nRedeem a Muse code in the Muse app: Settings → Redeem invite code, within 48 hours of joining. Never pay for a code.")
            reply("\n".join(lines))
    elif command == "/addinvite":
        if sender_id != OWNER_ID:
            reply("Only the channel owner can add invite codes.")
        else:
            parts = args.split()
            if len(parts) < 2:
                reply("Add a code like this: /addinvite Muse 4VV2NY — only add a code you actually own or have verified.")
            else:
                state.setdefault("invite_codes", []).append({"service": parts[0], "code": parts[1]})
                reply(f"Saved invite code for {parts[0]}: {parts[1]}. People can see it with /invite.")
    elif command == "/postnews":
        if sender_id != OWNER_ID:
            reply("Only the channel owner can post news to the channel.")
        else:
            parts = [p.strip() for p in args.split("|")]
            if len(parts) < 2:
                reply("Post news like this: /postnews Title | Summary | Source")
            else:
                channel = state.get("channel_id")
                if not channel:
                    reply("No channel set yet.")
                else:
                    caption = news_caption(parts[0], parts[1], parts[2] if len(parts) > 2 else "")
                    actions.append({"type": "text", "chat_id": channel, "text": caption})
                    reply(f"Posted news to {channel}.")
    elif command == "/quote":
        reply(pick_quote("any", state))
    elif command == "/love":
        reply(pick_quote("love", state))
    elif command == "/friend":
        reply(pick_quote("friend", state))
    elif command == "/khmer":
        reply(pick_quote("khmer", state))
    elif command == "/addquote":
        if sender_id != OWNER_ID:
            reply("Only the channel owner can add quotes.")
        elif not args:
            reply("Add a quote like this: /addquote Be kind, always.")
        else:
            state.setdefault("custom_quotes", []).append(args)
            reply(f"Quote added. The library now has {len(DAILY_QUOTES) + len(state['custom_quotes'])} quotes.")
    elif command == "/schedule":
        reply(SCHEDULE_TEXT)
    elif command == "/story":
        _q = pick_quote("any")
        actions.append({"type": "story", "chat_id": chat_id, "quote": _q, "caption": "📸 Today's Daily News story — post it to @dailynewsreporter as a story.\n\n" + _q})
    elif command == "/app":
        actions.append({"type": "text", "chat_id": chat_id, "text": "📱 Daily News Mini App — quotes, AI news, free AI tools, invite codes, quiz and the schedule, all in one place.", "keyboard": {"inline_keyboard": [[{"text": "📱 Open Daily News App", "web_app": {"url": MINI_APP_URL}}]]}})
    elif command == "/menu":
        reply("Here's your menu 👇 Tap a button any time — it stays under the message box.", keyboard=main_menu_keyboard())
    elif command == "/hidemenu":
        reply("Menu hidden. Send /menu any time to bring it back.", keyboard=hide_menu_keyboard())
    elif command in ("/quoteimg", "/loveimg", "/friendimg", "/khmerimg"):
        kind = "love" if command == "/loveimg" else "friend" if command == "/friendimg" else "khmer" if command == "/khmerimg" else "any"
        _q = pick_quote(kind)
        actions.append({"type": "photo", "chat_id": chat_id, "quote": _q, "caption": channel_caption(_q, "love" if kind == "love" else "friend" if kind == "friend" else "daily")})
    elif command == "/daily":
        if chat_id not in state["subscribers"]:
            state["subscribers"].append(chat_id)
        reply("You're in! I'll send you a daily quote every morning. Use /stopdaily anytime to stop.")
    elif command == "/stopdaily":
        if chat_id in state["subscribers"]:
            state["subscribers"].remove(chat_id)
        reply("No more daily quotes. You can come back anytime with /daily.")
    elif command == "/time":
        now = phnom_penh_now()
        reply(f"It's {now.strftime('%A, %d %B %Y, %I:%M %p')} in Phnom Penh.")
    elif command == "/echo":
        reply(args or "You didn't give me anything to echo. Try /echo hello")
    elif command == "/rules":
        custom_rules = get_group_settings(state, admin_chat).get("rules", "")
        reply(custom_rules or RULES_TEXT)
    elif command == "/admins":
        if chat_type not in ("group", "supergroup") and not connected_group:
            reply("This only works in a group. Add me to a group and make me an admin.")
        elif dry_run:
            reply("Group admins:\nDailyquote (creator)\nMuseVishnu_bot")
        else:
            try:
                result = api_call("getChatAdministrators", {"chat_id": admin_chat})
                names = []
                for a in result.get("result", []):
                    u = a.get("user") or {}
                    names.append(f"{u.get('first_name', 'Admin')} ({a.get('status')})")
                reply("Group admins:\n" + "\n".join(names) if names else "I could not list the admins.")
            except Exception as e:
                reply(f"I could not list the admins. Make me an admin first. Error: {e}")
    elif command in ("/ban", "/unban", "/kick", "/mute", "/unmute", "/warn", "/warns", "/unwarn", "/pin", "/unpin", "/del", "/lock", "/unlock", "/locks", "/locktypes", "/tban", "/setwelcome", "/setrules", "/antilink", "/antispam"):
        gchat = admin_chat
        if chat_type not in ("group", "supergroup") and not connected_group:
            reply("Admin commands only work in a group. Add me to a group and make me an admin, or connect to one with /connect.")
        elif not is_admin(gchat, sender_id, dry_run=dry_run, sender_chat_id=_schat):
            if not dry_run and not bot_is_admin(gchat, dry_run=dry_run):
                reply("I need to be an admin in this group first — make me an admin (Group settings → Administrators → add @MuseVishnu_bot), then try again.")
            else:
                reply("Only group admins can use that command.")
        elif command == "/setwelcome":
            get_group_settings(state, gchat)["welcome"] = args
            reply(f"Welcome message saved. Use {{name}} for the new member's name.\n\n{args}" if args else "Tell me the welcome text, like /setwelcome Welcome {name}!")
        elif command == "/setrules":
            get_group_settings(state, gchat)["rules"] = args
            reply(f"Rules saved.\n\n{args}" if args else "Tell me the rules, like /setrules Be kind.")
        elif command == "/antilink":
            on = args.lower() in ("on", "yes", "true", "1")
            get_group_settings(state, gchat)["antilink"] = on
            reply(f"Anti-link is now {'on' if on else 'off'}.")
        elif command == "/antispam":
            on = args.lower() not in ("off", "no", "false", "0")
            get_group_settings(state, gchat)["antispam"] = on
            reply(f"Anti-spam is now {'on' if on else 'off'}.")
        elif command == "/warns":
            target_id, target_name = target_user(message, args)
            if not target_id:
                reply("Reply to a member's message, or give their user id, like /warns 123456")
            else:
                count = get_group_settings(state, gchat)["warnings"].get(str(target_id), 0)
                reply(f"{target_name} has {count} warning(s).")
        elif command == "/unwarn":
            target_id, target_name = target_user(message, args)
            if not target_id:
                reply("Reply to a member's message, or give their user id.")
            else:
                get_group_settings(state, gchat)["warnings"].pop(str(target_id), None)
                reply(f"Warnings cleared for {target_name}.")
        elif command == "/warn":
            target_id, target_name = target_user(message, args)
            if not target_id:
                reply("Reply to a member's message to warn them, like /warn, or /warn 123456")
            else:
                settings = get_group_settings(state, gchat)
                count = settings["warnings"].get(str(target_id), 0) + 1
                settings["warnings"][str(target_id)] = count
                if count >= 3:
                    settings["warnings"][str(target_id)] = 0
                    actions.append({"type": "restrict", "chat_id": gchat, "user_id": target_id, "until": int(time.time()) + 3600, "permissions": {"can_send_messages": False}})
                    reply(f"{target_name} reached 3 warnings and was muted for 1 hour.")
                    log_event(state, actions, gchat, f"⚠️ {target_name} reached 3 warnings and was muted for 1 hour in {chat.get('title', gchat)}.")
                else:
                    reply(f"Warning {count}/3 for {target_name}.")
        elif command == "/pin":
            replied_id = (message.get("reply_to_message") or {}).get("message_id")
            if not replied_id:
                reply("Reply to the message you want to pin, then send /pin.")
            else:
                actions.append({"type": "pin", "chat_id": gchat, "message_id": replied_id})
                reply("Pinned.")
        elif command == "/unpin":
            actions.append({"type": "unpin", "chat_id": gchat})
            reply("Unpinned the last pinned message.")
        elif command == "/del":
            replied_id = (message.get("reply_to_message") or {}).get("message_id")
            if not replied_id:
                reply("Reply to the message you want to delete, then send /del.")
            else:
                actions.append({"type": "delete", "chat_id": gchat, "message_id": replied_id})
        elif command == "/lock":
            kind = args.strip().lower().split()[0] if args.strip() else ""
            if not kind:
                actions.append({"type": "set_permissions", "chat_id": gchat, "permissions": {"can_send_messages": False}})
                reply("Group locked. Only admins can send messages now. Use /unlock to open it again.")
            elif kind == "all":
                get_group_settings(state, gchat)["locks"]["all"] = True
                reply("🔒 All message types are now locked — non-admins' messages will be deleted. /unlock all to open.")
            elif kind in LOCK_TYPES:
                get_group_settings(state, gchat)["locks"][kind] = True
                reply(f"🔒 {kind} is now locked. See /locks for everything locked.")
            else:
                reply("I don't know that lock type. See /locktypes — or /lock with no type to lock the whole group.")
        elif command == "/unlock":
            kind = args.strip().lower().split()[0] if args.strip() else ""
            if not kind:
                actions.append({"type": "set_permissions", "chat_id": gchat, "permissions": {"can_send_messages": True, "can_send_media_messages": True, "can_send_polls": True, "can_send_other_messages": True, "can_add_web_page_previews": True}})
                reply("Group unlocked. Everyone can send messages again.")
            elif kind == "all":
                get_group_settings(state, gchat)["locks"].pop("all", None)
                reply("🔓 All message types unlocked.")
            elif kind in LOCK_TYPES:
                get_group_settings(state, gchat)["locks"].pop(kind, None)
                reply(f"🔓 {kind} is now unlocked.")
            else:
                reply("I don't know that lock type. See /locktypes.")
        elif command == "/locks":
            locks = get_group_settings(state, gchat).get("locks", {})
            on = [t for t, v in locks.items() if v]
            reply(("🔒 Locked here:\n" + "\n".join(f"• {t}" for t in on)) if on else "Nothing is locked here. /lock <type> to lock something, /locktypes to see the types.")
        elif command == "/locktypes":
            reply("Lockable types:\n" + ", ".join(LOCK_TYPES) + ", all\n\nUse /lock <type> and /unlock <type>. /lock with no type locks the whole group.")
        else:
            target_id, target_name = target_user(message, args)
            if not target_id:
                reply("Reply to a member's message, or give their user id, like /ban 123456")
            elif command == "/ban":
                actions.append({"type": "ban", "chat_id": gchat, "user_id": target_id})
                reply(f"{target_name} was banned.")
                log_event(state, actions, gchat, f"🚫 {target_name} was banned in {chat.get('title', gchat)} by {sender.get('first_name', 'an admin')}.")
            elif command == "/unban":
                actions.append({"type": "unban", "chat_id": gchat, "user_id": target_id})
                reply(f"{target_name} was unbanned.")
                log_event(state, actions, gchat, f"✅ {target_name} was unbanned in {chat.get('title', gchat)} by {sender.get('first_name', 'an admin')}.")
            elif command == "/kick":
                actions.append({"type": "ban", "chat_id": gchat, "user_id": target_id})
                actions.append({"type": "unban", "chat_id": gchat, "user_id": target_id})
                reply(f"{target_name} was kicked.")
                log_event(state, actions, gchat, f"👢 {target_name} was kicked from {chat.get('title', gchat)} by {sender.get('first_name', 'an admin')}.")
            elif command == "/mute":
                seconds = parse_duration(args.split(None, 1)[0]) if args else None
                if seconds is None and args:
                    # /mute with no valid time after the user id, default 1 hour
                    seconds = 3600
                if seconds is None:
                    seconds = 3600
                actions.append({"type": "restrict", "chat_id": gchat, "user_id": target_id, "until": int(time.time()) + seconds, "permissions": {"can_send_messages": False}})
                reply(f"{target_name} was muted for {seconds // 60} minute(s).")
                log_event(state, actions, gchat, f"🔇 {target_name} was muted for {seconds // 60} minute(s) in {chat.get('title', gchat)} by {sender.get('first_name', 'an admin')}.")
            elif command == "/unmute":
                actions.append({"type": "restrict", "chat_id": gchat, "user_id": target_id, "until": None, "permissions": {"can_send_messages": True, "can_send_media_messages": True, "can_send_polls": True, "can_send_other_messages": True, "can_add_web_page_previews": True}})
                reply(f"{target_name} was unmuted.")
                log_event(state, actions, gchat, f"🔊 {target_name} was unmuted in {chat.get('title', gchat)} by {sender.get('first_name', 'an admin')}.")
            elif command == "/tban":
                tokens = args.split()
                dur_text = tokens[0] if message.get("reply_to_message") else (tokens[1] if len(tokens) > 1 else "")
                seconds = parse_duration(dur_text) if dur_text else None
                if seconds is None:
                    reply("Give a duration, like /tban 1d or /tban 2h (reply to the member or give their id).")
                else:
                    until = int(time.time()) + seconds
                    actions.append({"type": "ban", "chat_id": gchat, "user_id": target_id, "until": until})
                    state.setdefault("temp_bans", {}).setdefault(str(gchat), {})[str(target_id)] = until
                    reply(f"{target_name} was banned for {seconds // 60} minute(s). They'll be unbanned automatically.")
                    log_event(state, actions, gchat, f"⏱️ {target_name} was temp-banned for {seconds // 60} minute(s) in {chat.get('title', gchat)} by {sender.get('first_name', 'an admin')}.")
    elif command in ("/newfed", "/delfed", "/joinfed", "/leavefed", "/fedinfo", "/fedlist", "/fedbans", "/fban", "/unfban"):
        if command == "/newfed":
            if chat_type in ("group", "supergroup") and not is_admin(chat_id, sender_id, dry_run=dry_run, sender_chat_id=_schat):
                reply("Only group admins can create a federation.")
            elif not args:
                reply("Create a federation like this: /newfed Daily News Fed — groups that join it share bans.")
            else:
                fid = fed_new_id(state)
                groups = [chat_id] if chat_type in ("group", "supergroup") else []
                state.setdefault("federations", {})[fid] = {"name": args, "owner": sender_id, "groups": groups, "banned": {}}
                reply(f"Federation created: {args}\nID: {fid}\nOther groups can join with /joinfed {fid} — and /fban will ban a spammer in every group of the fed at once.")
        elif command == "/delfed":
            fid = args.split()[0] if args.split() else ""
            fed = state.get("federations", {}).get(fid)
            if not fed:
                reply("Give the federation ID, like /delfed fed_abc123. See /fedlist for your federations.")
            elif fed.get("owner") != sender_id and sender_id != OWNER_ID:
                reply("Only the federation owner can delete it.")
            else:
                del state["federations"][fid]
                reply(f"Federation {fed.get('name')} ({fid}) deleted.")
        elif command == "/joinfed":
            if chat_type not in ("group", "supergroup") and not connected_group:
                reply("Use /joinfed inside a group (or connect to one first with /connect).")
            elif not is_admin(admin_chat, sender_id, dry_run=dry_run, sender_chat_id=_schat):
                reply("Only group admins can join a federation.")
            else:
                fid = args.split()[0] if args.split() else ""
                fed = state.get("federations", {}).get(fid)
                if not fed:
                    reply("No federation with that ID. Ask the fed owner for the ID, or create one with /newfed Name.")
                elif admin_chat in fed.get("groups", []):
                    reply(f"This group is already in {fed.get('name')}.")
                else:
                    fed.setdefault("groups", []).append(admin_chat)
                    reply(f"This group joined the federation {fed.get('name')} ({fid}). Bans with /fban now apply here too.")
        elif command == "/leavefed":
            if chat_type not in ("group", "supergroup") and not connected_group:
                reply("Use /leavefed inside a group (or connect to one first).")
            elif not is_admin(admin_chat, sender_id, dry_run=dry_run, sender_chat_id=_schat):
                reply("Only group admins can leave a federation.")
            else:
                fid, fed = group_fed(state, admin_chat)
                if not fed:
                    reply("This group is not in any federation.")
                else:
                    fed["groups"].remove(admin_chat)
                    reply(f"This group left the federation {fed.get('name')}.")
        elif command == "/fedinfo":
            fid, fed = group_fed(state, admin_chat)
            if not fed:
                reply("This group is not in any federation yet. Create one with /newfed Name or join one with /joinfed FED-ID.")
            else:
                reply(f"Federation: {fed.get('name')}\nID: {fid}\nGroups: {len(fed.get('groups', []))}\nFederally banned users: {len(fed.get('banned', {}))}")
        elif command == "/fedlist":
            feds = state.get("federations", {})
            if not feds:
                reply("No federations exist yet. Create one with /newfed Name.")
            else:
                lines = ["Federations:\n"]
                for fid, fed in list(feds.items())[:20]:
                    lines.append(f"• {fed.get('name')} — {fid} ({len(fed.get('groups', []))} groups, {len(fed.get('banned', {}))} banned)")
                reply("\n".join(lines))
        elif command == "/fedbans":
            fid, fed = group_fed(state, admin_chat)
            if not fed:
                reply("This group is not in any federation.")
            elif not fed.get("banned"):
                reply(f"No federally banned users in {fed.get('name')}.")
            else:
                lines = [f"Federally banned in {fed.get('name')}:\n"]
                for uid, ban in list(fed["banned"].items())[:20]:
                    lines.append(f"• {ban.get('name', uid)} — {ban.get('reason') or 'no reason'}")
                reply("\n".join(lines))
        elif command in ("/fban", "/unfban"):
            fid, fed = group_fed(state, admin_chat)
            if not fed:
                reply("This group is not in any federation. Create one with /newfed Name or join one with /joinfed FED-ID.")
            elif fed.get("owner") != sender_id and not is_admin(admin_chat, sender_id, dry_run=dry_run, sender_chat_id=_schat):
                reply("Only the federation owner or a group admin can do that.")
            else:
                target_id, target_name = target_user(message, args)
                if not target_id:
                    reply("Reply to the spammer's message, or give their user id, like /fban 123456 spam links")
                else:
                    parts = args.split(None, 1)
                    replied = message.get("reply_to_message") or {}
                    if replied.get("from"):
                        reason = args
                    elif parts and parts[0].lstrip("-").isdigit():
                        reason = parts[1] if len(parts) > 1 else ""
                    else:
                        reason = args
                    if command == "/fban":
                        fed.setdefault("banned", {})[str(target_id)] = {"name": target_name, "reason": reason, "by": sender_id, "at": int(time.time())}
                        for gid in fed.get("groups", []):
                            actions.append({"type": "ban", "chat_id": gid, "user_id": target_id})
                        reply(f"🚫 {target_name} was federally banned in {len(fed.get('groups', []))} group(s). Reason: {reason or 'no reason given'}.")
                        log_event(state, actions, admin_chat, f"🌐 {target_name} was federally banned in {fed.get('name')} by {sender.get('first_name', 'an admin')}. Reason: {reason or 'no reason given'}.")
                    else:
                        fed.get("banned", {}).pop(str(target_id), None)
                        for gid in fed.get("groups", []):
                            actions.append({"type": "unban", "chat_id": gid, "user_id": target_id})
                        reply(f"✅ {target_name} was removed from the federation ban list and unbanned in {len(fed.get('groups', []))} group(s).")
                        log_event(state, actions, admin_chat, f"🌐 {target_name} was federally unbanned in {fed.get('name')} by {sender.get('first_name', 'an admin')}.")
    elif command in ("/save", "/get", "/clear", "/notes", "/filter", "/filters", "/stop"):
        if chat_type not in ("group", "supergroup") and not connected_group:
            reply("Notes and filters work in a group. Add me to a group, or connect with /connect.")
        else:
            settings = get_group_settings(state, admin_chat)
            if command in ("/save", "/clear", "/filter", "/stop") and not is_admin(admin_chat, sender_id, dry_run=dry_run, sender_chat_id=_schat):
                reply("Only group admins can do that.")
            elif command == "/save":
                parts = args.split(None, 1)
                if not parts:
                    reply("Save a note like this: /save rules Be kind to everyone — or reply to a message with /save name.")
                else:
                    name = parts[0].lower()
                    replied = message.get("reply_to_message") or {}
                    text_value = (replied.get("text") or "") or (parts[1] if len(parts) > 1 else "")
                    if not text_value:
                        reply("Give the note text too, like /save welcome Hello {name}!")
                    else:
                        settings.setdefault("notes", {})[name] = text_value
                        reply(f"Note #{name} saved. Anyone can read it with /get {name} or #{name}.")
            elif command == "/get":
                name = args.split()[0].lower() if args.split() else ""
                note = settings.get("notes", {}).get(name) if name else None
                if not note:
                    reply(f"No note named #{name}. See /notes for the list." if name else "Get a note like this: /get welcome")
                else:
                    reply(note.replace("{name}", sender.get("first_name", "friend")))
            elif command == "/clear":
                name = args.split()[0].lower() if args.split() else ""
                if name and name in settings.get("notes", {}):
                    del settings["notes"][name]
                    reply(f"Note #{name} deleted.")
                else:
                    reply("Give the note name, like /clear welcome.")
            elif command == "/notes":
                notes = settings.get("notes", {})
                reply("Saved notes:\n" + "\n".join(f"• #{n}" for n in sorted(notes)) if notes else "No notes saved yet. Admins can add one with /save name text.")
            elif command == "/filter":
                parts = args.split(None, 1)
                if len(parts) < 2:
                    reply("Add a filter like this: /filter hello Hello {name}, welcome! — I will reply whenever someone says that word.")
                else:
                    settings.setdefault("filters", {})[parts[0].lower()] = parts[1]
                    reply(f"Filter saved: whenever someone says “{parts[0]}”, I will reply with that text. Remove it with /stop {parts[0]}.")
            elif command == "/filters":
                filters = settings.get("filters", {})
                reply("Active filters:\n" + "\n".join(f"• {w} → {r[:60]}" for w, r in sorted(filters.items())) if filters else "No filters yet. Admins can add one with /filter word reply text.")
            elif command == "/stop":
                word = args.split()[0].lower() if args.split() else ""
                if word and word in settings.get("filters", {}):
                    del settings["filters"][word]
                    reply(f"Filter for “{word}” removed.")
                else:
                    reply("Give the filter word, like /stop hello.")
    elif command == "/captcha":
        if chat_type not in ("group", "supergroup") and not connected_group:
            reply("Use /captcha inside a group (or connect with /connect).")
        elif not is_admin(admin_chat, sender_id, dry_run=dry_run, sender_chat_id=_schat):
            reply("Only group admins can do that.")
        else:
            on = args.lower() in ("on", "yes", "true", "1")
            get_group_settings(state, admin_chat)["captcha"] = on
            reply(f"Captcha is now {'on' if on else 'off'}. New members must tap a button to prove they are human." if on else "Captcha is now off. New members join freely.")
    elif command == "/report":
        if chat_type not in ("group", "supergroup"):
            reply("/report only works in a group — reply to the message you want to report.")
        else:
            settings = get_group_settings(state, chat_id)
            if not settings.get("reports", True):
                reply("Reports are turned off in this group.")
            else:
                replied = message.get("reply_to_message") or {}
                if not replied:
                    reply("Reply to the message you want to report, then send /report.")
                else:
                    admins = [a for a in admin_list(chat_id, dry_run=dry_run) if a and a != sender_id]
                    reported = (replied.get("from") or {}).get("first_name", "someone")
                    reported_text = (replied.get("text") or "")[:300]
                    reporter = sender.get("first_name", "Someone")
                    title = chat.get("title", "this group")
                    for admin_id in admins:
                        actions.append({"type": "text", "chat_id": admin_id, "text": f"🚨 Report in {title}\n\n{reporter} reported {reported}'s message:\n“{reported_text}”"})
                    reply("Reported to the group admins. They'll take a look.")
    elif command in ("/addblacklist", "/rmblacklist", "/blacklist", "/blacklistmode", "/addsticker", "/rmsticker", "/stickers"):
        if chat_type not in ("group", "supergroup") and not connected_group:
            reply("Blacklists work in a group. Add me to a group, or connect with /connect.")
        elif not is_admin(admin_chat, sender_id, dry_run=dry_run, sender_chat_id=_schat):
            reply("Only group admins can do that.")
        else:
            settings = get_group_settings(state, admin_chat)
            if command == "/addblacklist":
                word = args.split()[0].lower() if args.split() else ""
                if not word:
                    reply("Add a word like this: /addblacklist spam")
                else:
                    words = settings.setdefault("blacklist_words", [])
                    if word not in words:
                        words.append(word)
                    reply(f"“{word}” is now blacklisted ({settings.get('blacklist_mode', 'delete')} mode).")
            elif command == "/rmblacklist":
                word = args.split()[0].lower() if args.split() else ""
                words = settings.setdefault("blacklist_words", [])
                if word in words:
                    words.remove(word)
                    reply(f"“{word}” is no longer blacklisted.")
                else:
                    reply(f"“{word}” was not in the blacklist.")
            elif command == "/blacklist":
                words = settings.get("blacklist_words", [])
                reply(f"Blacklisted words ({settings.get('blacklist_mode', 'delete')} mode):\n" + "\n".join(f"• {w}" for w in words) if words else "No blacklisted words yet. Add one with /addblacklist word.")
            elif command == "/blacklistmode":
                mode = args.split()[0].lower() if args.split() else ""
                if mode in ("delete", "warn", "mute"):
                    settings["blacklist_mode"] = mode
                    reply(f"Blacklist mode is now {mode}.")
                else:
                    reply("Pick a mode: /blacklistmode delete — just delete the message, /blacklistmode warn — delete and warn, /blacklistmode mute — delete and mute for 1 hour.")
            elif command in ("/addsticker", "/rmsticker"):
                replied = message.get("reply_to_message") or {}
                sticker = replied.get("sticker") or {}
                fid_unique = sticker.get("file_unique_id")
                if not fid_unique:
                    reply("Reply to the sticker you want to blacklist, then send /addsticker.")
                else:
                    stickers = settings.setdefault("blacklist_stickers", [])
                    if command == "/addsticker":
                        if fid_unique not in stickers:
                            stickers.append(fid_unique)
                        reply("That sticker is now blacklisted — I will delete it when someone sends it.")
                    else:
                        if fid_unique in stickers:
                            stickers.remove(fid_unique)
                        reply("That sticker is no longer blacklisted.")
            elif command == "/stickers":
                stickers = settings.get("blacklist_stickers", [])
                reply(f"{len(stickers)} blacklisted sticker(s)." if stickers else "No blacklisted stickers yet. Reply to a sticker with /addsticker to blacklist it.")
    elif command == "/settings":
        if chat_type not in ("group", "supergroup") and not connected_group:
            reply("Open settings inside a group, or connect to one first with /connect.")
        elif not is_admin(admin_chat, sender_id, dry_run=dry_run, sender_chat_id=_schat):
            reply("Only group admins can change settings.")
        else:
            settings = get_group_settings(state, admin_chat)
            actions.append({"type": "text", "chat_id": chat_id, "text": settings_text(admin_chat, settings), "keyboard": settings_keyboard(admin_chat, settings)})
    elif command == "/connect":
        if chat_type != "private":
            reply("/connect works in a private chat with me — tell me the group, like /connect @YourGroup or /connect -1001234567890.")
        elif not args:
            current = state.get("connections", {}).get(str(sender_id))
            reply(f"You are connected to {current}." if current else "You are not connected to any group. Connect with /connect @YourGroup — you must be an admin there.")
        else:
            gid, gname = resolve_group(args, dry_run=dry_run)
            if gid is None:
                reply("I couldn't find that group. Use the @username or the group id, and make sure I'm in the group.")
            else:
                admins = admin_list(gid, dry_run=dry_run)
                if sender_id not in admins and sender_id != OWNER_ID:
                    reply("Only a group admin can connect to that group.")
                else:
                    state.setdefault("connections", {})[str(sender_id)] = gid
                    reply(f"Connected to {gname}. Group admin commands like /ban, /warn, /settings and /fban now work from this chat. Use /disconnect to stop.")
    elif command == "/disconnect":
        if chat_type != "private":
            reply("/disconnect works in a private chat with me.")
        else:
            removed = state.get("connections", {}).pop(str(sender_id), None)
            reply("Disconnected." if removed else "You were not connected to any group.")
    elif command in ("/promote", "/demote", "/purge"):
        if chat_type not in ("group", "supergroup") and not connected_group:
            reply("Use that inside a group (or connect with /connect).")
        elif not is_admin(admin_chat, sender_id, dry_run=dry_run, sender_chat_id=_schat):
            reply("Only group admins can do that.")
        elif command == "/purge":
            replied_id = (message.get("reply_to_message") or {}).get("message_id")
            if not replied_id:
                reply("Reply to the message you want to purge from, then send /purge. I'll delete up to 50 messages.")
            else:
                start = replied_id
                end = message.get("message_id", replied_id)
                count = 0
                for mid in range(start, end + 1):
                    if count >= 50:
                        break
                    actions.append({"type": "delete", "chat_id": admin_chat, "message_id": mid})
                    count += 1
                reply(f"Purged {count} message(s).")
                log_event(state, actions, admin_chat, f"🧹 {sender.get('first_name', 'an admin')} purged {count} message(s) in {chat.get('title', admin_chat)}.")
        else:
            target_id, target_name = target_user(message, args)
            if not target_id:
                reply("Reply to a member's message, or give their user id.")
            else:
                actions.append({"type": "promote", "chat_id": admin_chat, "user_id": target_id, "promote": command == "/promote"})
                reply(f"{target_name} was {'promoted to admin' if command == '/promote' else 'demoted'}.")
                log_event(state, actions, admin_chat, f"{'⬆️' if command == '/promote' else '⬇️'} {target_name} was {'promoted to admin' if command == '/promote' else 'demoted'} in {chat.get('title', admin_chat)} by {sender.get('first_name', 'an admin')}.")
    elif command == "/checkin":
        today = phnom_penh_now().strftime("%Y-%m-%d")
        yesterday = (phnom_penh_now() - datetime.timedelta(days=1)).strftime("%Y-%m-%d")
        st = state.setdefault("streaks", {}).setdefault(str(sender_id), {"count": 0, "last": "", "name": sender.get("first_name", "friend")})
        st["name"] = sender.get("first_name", "friend")
        if st.get("last") == today:
            reply(f"Already checked in today! Your streak is {st['count']} day(s). Come back tomorrow. 🔥")
        else:
            if st.get("last") == yesterday:
                st["count"] += 1
            else:
                st["count"] = 1
            st["last"] = today
            leveled = award_xp(state, sender_id, sender.get("first_name", "friend"), 20)
            amt = xp_amount(state, sender_id, 20)
            msg = f"✅ Checked in! +{amt} XP\n🔥 Streak: {st['count']} day(s) — keep it alive tomorrow!"
            if leveled:
                msg += f"\n\n🎉 LEVEL UP! You are now Level {leveled}!"
            reply(msg)
    elif command == "/rank":
        reply(rank_text(state, sender_id, sender.get("first_name", "friend")))
    elif command == "/leaderboard":
        reply(leaderboard_text(state))
    elif command == "/tournament":
        if chat_type not in ("group", "supergroup"):
            reply("Tournaments run in a group — add me to a group and send /tournament there!")
        else:
            t = state.get("tournaments", {}).get(str(chat_id))
            if t:
                reply(f"🏆 Trivia tournament — Round {t['round']}/{t['total']}\n\n{tournament_standings(t)}\n\nAnswer the current question to score! Admins can end it early with /endtournament.")
            else:
                state.setdefault("tournaments", {})[str(chat_id)] = {"round": 1, "total": 5, "players": {}, "current_poll": None, "done_polls": [], "starter": sender_id}
                q = random.choice(QUIZ_QUESTIONS)
                reply("🏆 Trivia tournament started! 5 questions, first correct answer scores a point. Winner gets +50 XP!\n\nRound 1:")
                actions.append({"type": "quiz", "chat_id": chat_id, "question": q, "tournament": True})
    elif command == "/endtournament":
        t = state.get("tournaments", {}).get(str(chat_id))
        if not t:
            reply("No tournament running here. Start one with /tournament!")
        elif not is_admin(chat_id, sender_id, dry_run=dry_run, sender_chat_id=_schat) and sender_id != t.get("starter"):
            reply("Only a group admin or the player who started the tournament can end it.")
        else:
            if t.get("players"):
                winner_uid = max(t["players"], key=lambda k: t["players"][k]["score"])
                winner = t["players"][winner_uid]
                award_xp(state, winner_uid, winner["name"], 50)
                reply(f"🏆 Tournament ended early!\n\n{tournament_standings(t)}\n\nWinner: {winner['name']} — +{xp_amount(state, winner_uid, 50)} XP!")
            else:
                reply("Tournament ended — no scores yet. Start a new one with /tournament!")
            for pid in list(t.get("done_polls", [])):
                state.get("tournament_polls", {}).pop(pid, None)
            state.get("tournaments", {}).pop(str(chat_id), None)
    elif command == "/digest":
        quote = pick_quote("any", state)
        rec = xp_record(state, sender_id)
        level = xp_level(rec["xp"])
        streak = state.get("streaks", {}).get(str(sender_id), {"count": 0})
        reply(
            f"📰 Your Daily News digest\n\n"
            f"💬 {quote}\n\n"
            f"⭐ Level {level} — {rec['xp']} XP ({xp_for_next_level(level) - rec['xp']} XP to Level {level + 1})\n"
            f"🔥 Streak: {streak.get('count', 0)} day(s) — /checkin to keep it alive\n"
            f"📅 Channel posts: quote 08:42 • AI news 12:42 • love quote 18:42\n"
            f"🏆 See the top players with /leaderboard"
        )
    elif command == "/check":
        replied = message.get("reply_to_message") or {}
        rtext = (replied.get("text") or "").strip()
        if not rtext:
            reply("Reply to a message with /check and I'll ask the AI whether it's spam, a scam, abuse or harmless.")
        else:
            verdict, reason = ai_moderate(rtext, dry_run=dry_run)
            reply(f"🤖 AI verdict: {verdict}\n{reason}")
    elif command == "/aimod":
        if chat_type not in ("group", "supergroup") and not connected_group:
            reply("AI moderation is a group setting — use it in a group.")
        elif not is_admin(admin_chat, sender_id, dry_run=dry_run, sender_chat_id=_schat):
            reply("Only group admins can change AI moderation.")
        elif args.strip().lower() in ("on", "off"):
            settings = get_group_settings(state, admin_chat)
            settings["aimod"] = args.strip().lower() == "on"
            reply(f"AI moderation is now {'on' if settings['aimod'] else 'off'}. When on, I'll check messages with links from non-admins and delete spam, scams and abuse.")
        else:
            settings = get_group_settings(state, admin_chat)
            reply(f"AI moderation is currently {'on' if settings.get('aimod') else 'off'}. Use /aimod on or /aimod off.")
    elif command == "/setlog":
        if chat_type not in ("group", "supergroup") and not connected_group:
            reply("The log channel is a group setting — use /setlog in a group.")
        elif not is_admin(admin_chat, sender_id, dry_run=dry_run, sender_chat_id=_schat):
            reply("Only group admins can set the log channel.")
        elif not args or args.strip().lower() == "off":
            state.setdefault("log_channels", {}).pop(str(admin_chat), None)
            reply("Log channel cleared — moderation actions will no longer be logged.")
        else:
            gid, _label = resolve_group(args.split()[0], dry_run=dry_run)
            if not gid:
                reply("Give the log channel like /setlog @dailynewslogs — the bot must be an admin there to post.")
            else:
                state.setdefault("log_channels", {})[str(admin_chat)] = gid
                reply(f"Log channel set: {args.split()[0]}. Bans, mutes, warns, fbans, purges, promotes and demotes will be logged there.")
    elif command == "/backup":
        if chat_type not in ("group", "supergroup") and not connected_group:
            reply("Backups are for groups — use /backup in a group.")
        elif not is_admin(admin_chat, sender_id, dry_run=dry_run, sender_chat_id=_schat):
            reply("Only group admins can back up the group settings.")
        else:
            settings = get_group_settings(state, admin_chat)
            data = json.dumps(settings, indent=2, ensure_ascii=False)
            text_out = "```\n" + data + "\n```"
            for i in range(0, len(text_out), 4000):
                actions.append({"type": "text", "chat_id": chat_id, "text": text_out[i:i + 4000]})
            reply("Group settings backup above — save it. Restore it in any group with /restore and the JSON.")
    elif command == "/restore":
        if chat_type not in ("group", "supergroup") and not connected_group:
            reply("Restore is for groups — use /restore in a group.")
        elif not is_admin(admin_chat, sender_id, dry_run=dry_run, sender_chat_id=_schat):
            reply("Only group admins can restore the group settings.")
        elif not args:
            reply("Paste the backup JSON after /restore, like /restore {\"antilink\": true, ...}")
        else:
            try:
                data = json.loads(args)
                if not isinstance(data, dict):
                    raise ValueError("not an object")
            except Exception:
                reply("That doesn't look like a backup — I need the JSON from /backup.")
            else:
                settings = get_group_settings(state, admin_chat)
                allowed = ("welcome", "rules", "antilink", "antispam", "captcha", "reports", "blacklist_words", "blacklist_mode", "blacklist_stickers", "notes", "filters", "aimod", "locks", "approved", "disabled", "flood_limit", "goodbye", "fsub")
                restored = [k for k in allowed if k in data]
                for k in restored:
                    settings[k] = data[k]
                reply(f"Restored {len(restored)} setting(s): {', '.join(restored)}." if restored else "Nothing to restore — the backup had no settings I know.")
    elif command == "/referral":
        code = referral_code(state, sender_id, sender.get("first_name", "friend"))
        rec = state["referrals"][str(sender_id)]
        boost = referral_boost_active(state)
        amt = xp_amount(state, sender_id, 100 if boost else 50)
        new_amt = 40 if boost else 20
        reply(
            f"🔗 Your invite link\n\nhttps://t.me/MuseVishnu_bot?start=ref_{code}\n\n"
            f"Friends who join with it get +{new_amt} XP, you get +{amt} XP each.\n"
            f"You have invited {rec.get('count', 0)} friend(s).\n"
            f"Top inviters: /topreferrers"
            + ("\n\n🔥 Referral boost is ON — double rewards!" if boost else "")
        )
    elif command == "/topreferrers":
        refs = state.get("referrals", {})
        ranked = sorted(refs.values(), key=lambda r: r.get("count", 0), reverse=True)[:5]
        if not ranked or max(r.get("count", 0) for r in ranked) == 0:
            reply("No referrals yet. Get your link with /referral and share it!")
        else:
            lines = ["🔗 Top inviters:\n"]
            lines.extend(f"{i + 1}. {r.get('name', 'friend')} — {r.get('count', 0)} friend(s)" for i, r in enumerate(ranked) if r.get("count", 0) > 0)
            reply("\n".join(lines))
    elif command == "/premium":
        prem = state.get("premium", {}).get(str(sender_id))
        if prem:
            since = datetime.datetime.fromtimestamp(prem.get("since", int(time.time())), tz=datetime.timezone(datetime.timedelta(hours=7))).strftime("%d %b %Y")
            reply(f"⭐ You are Premium since {since}!\n\nPerks: double XP on everything, a ⭐ badge in /rank and /leaderboard, and early access to new features.\n\nThank you for supporting Daily News!")
        else:
            reply("⭐ Daily News Premium — 150 Stars, lifetime.\n\nPerks: double XP on everything, a ⭐ badge in /rank and /leaderboard, and early access to new features.\n\nTap below to pay with Telegram Stars.")
            actions.append({"type": "invoice", "chat_id": chat_id, "title": "Daily News Premium", "description": "Lifetime Premium: double XP, a ⭐ badge, and early access to new features.", "payload": f"premium_{sender_id}", "currency": "XTR", "prices": [{"label": "Premium", "amount": 150}], "keyboard": {"inline_keyboard": [[{"text": "⭐ Pay 150 Stars", "pay": True}]]}})
    elif command == "/broadcast":
        if sender_id != OWNER_ID:
            reply("Only the owner can broadcast.")
        elif not args:
            reply("Use: /broadcast Your message — I'll send it to every subscriber.")
        else:
            subs = state.get("subscribers", [])
            if not subs:
                reply("No subscribers yet — nobody has sent /daily. Your broadcast has nowhere to go.")
            else:
                for sub in subs:
                    actions.append({"type": "text", "chat_id": int(sub), "text": f"📣 From Daily News:\n\n{args}"})
                reply(f"Broadcast sent to {len(subs)} subscriber(s).")
    elif command == "/schedulepost":
        if sender_id != OWNER_ID:
            reply("Only the owner can schedule channel posts.")
        elif not args:
            reply("Use: /schedulepost 18:30 Your message — or /schedulepost tomorrow 09:00 Your message")
        else:
            at, text_or_err = parse_schedule_time(args)
            if at is None:
                reply(text_or_err)
            else:
                pid = "sp_" + "".join(random.choice("ABCDEFGHJKLMNPQRSTUVWXYZ23456789") for _ in range(6))
                state.setdefault("scheduled_posts", []).append({"id": pid, "text": text_or_err, "at": at, "by": sender_id})
                when = datetime.datetime.fromtimestamp(at, tz=datetime.timezone(datetime.timedelta(hours=7))).strftime("%d %b %Y %H:%M")
                reply(f"📅 Scheduled for the channel at {when} (Phnom Penh).\nID: {pid}\nSee /scheduled to list, /cancelschedule {pid} to cancel.")
    elif command == "/scheduled":
        if sender_id != OWNER_ID:
            reply("Only the owner can see scheduled posts.")
        else:
            posts = state.get("scheduled_posts", [])
            if not posts:
                reply("No scheduled posts. Use /schedulepost HH:MM Your message to queue one.")
            else:
                lines = ["📅 Scheduled channel posts:\n"]
                for p in posts:
                    when = datetime.datetime.fromtimestamp(p.get("at", 0), tz=datetime.timezone(datetime.timedelta(hours=7))).strftime("%d %b %H:%M")
                    lines.append(f"• {p.get('id')} — {when}\n  {p.get('text', '')[:80]}")
                reply("\n".join(lines))
    elif command == "/cancelschedule":
        if sender_id != OWNER_ID:
            reply("Only the owner can cancel scheduled posts.")
        elif not args:
            reply("Use: /cancelschedule sp_XXXXXX — see /scheduled for the IDs.")
        else:
            pid = args.strip().split()[0]
            before = len(state.get("scheduled_posts", []))
            state["scheduled_posts"] = [p for p in state.get("scheduled_posts", []) if p.get("id") != pid]
            reply(f"Cancelled {pid}." if len(state["scheduled_posts"]) < before else f"No scheduled post with ID {pid}. See /scheduled.")
    elif command == "/giveaway":
        if chat_type not in ("group", "supergroup"):
            reply("Giveaways run in groups — add me to a group and run /giveaway there.")
        elif not is_admin(chat_id, sender_id, dry_run=dry_run, sender_chat_id=_schat):
            reply("Only group admins can start a giveaway.")
        else:
            existing = state.get("giveaways", {}).get(str(chat_id))
            if existing and not args:
                reply(f"🎉 Giveaway is live! Prize: {existing['prize']}\n{len(existing['entries'])} entrant(s) so far. Tap the Join button on the giveaway post to enter!\nAdmins: /draw to pick a winner, /endgiveaway to cancel.")
            elif existing:
                reply("A giveaway is already running here. /draw to pick a winner or /endgiveaway to cancel it first.")
            elif not args:
                reply("Use: /giveaway premium — or /giveaway 100 — or /giveaway a shoutout on the channel\nPrize: Premium (lifetime), a number of XP, or any custom prize.")
            else:
                prize_text, kind, xp_amt = parse_prize(args)
                state.setdefault("giveaways", {})[str(chat_id)] = {"prize": prize_text, "prize_kind": kind, "prize_xp": xp_amt, "entries": [], "names": {}, "starter": sender_id, "created": int(time.time())}
                keyboard = {"inline_keyboard": [[{"text": "🎉 Join the giveaway", "callback_data": f"giveaway_join:{chat_id}"}]]}
                actions.append({"type": "text", "chat_id": chat_id, "text": f"🎉 GIVEAWAY! 🎉\n\nPrize: {prize_text}\n\nTap below to join — anyone in the group can enter!\nWinner picked with /draw.", "keyboard": keyboard})
    elif command == "/draw":
        if chat_type not in ("group", "supergroup"):
            reply("Giveaways run in groups.")
        else:
            g = state.get("giveaways", {}).get(str(chat_id))
            if not g:
                reply("No giveaway is running here. Start one with /giveaway.")
            elif not (is_admin(chat_id, sender_id, dry_run=dry_run, sender_chat_id=_schat) or sender_id == g.get("starter")):
                reply("Only a group admin or the giveaway starter can draw the winner.")
            elif not g.get("entries"):
                reply("Nobody entered yet — the giveaway is still open! Tap the Join button on the giveaway post to enter.")
            else:
                winner_id = random.choice(g["entries"])
                winner_name = g.get("names", {}).get(str(winner_id), "friend")
                if g["prize_kind"] == "premium":
                    state.setdefault("premium", {})[str(winner_id)] = {"since": int(time.time()), "stars": 0, "via": "giveaway"}
                    prize_line = "⭐ Premium (lifetime) unlocked!"
                elif g["prize_kind"] == "xp":
                    award_xp(state, winner_id, winner_name, g["prize_xp"])
                    prize_line = f"+{xp_amount(state, winner_id, g['prize_xp'])} XP!"
                else:
                    award_xp(state, winner_id, winner_name, 50)
                    prize_line = f"{g['prize']} (+50 XP)!"
                reply(f"🎉 GIVEAWAY WINNER! 🎉\n\n🏆 {winner_name} wins {prize_line}\n\n{len(g['entries'])} entrant(s). Thanks for playing — watch for the next giveaway!")
                del state["giveaways"][str(chat_id)]
    elif command == "/endgiveaway":
        if chat_type not in ("group", "supergroup"):
            reply("Giveaways run in groups.")
        else:
            g = state.get("giveaways", {}).get(str(chat_id))
            if not g:
                reply("No giveaway is running here.")
            elif not (is_admin(chat_id, sender_id, dry_run=dry_run, sender_chat_id=_schat) or sender_id == g.get("starter")):
                reply("Only a group admin or the giveaway starter can end it.")
            else:
                del state["giveaways"][str(chat_id)]
                reply(f"Giveaway ended — {len(g.get('entries', []))} entrant(s), no winner picked. Start a new one with /giveaway.")
    elif command == "/share":
        code = referral_code(state, sender_id, sender.get("first_name", "friend"))
        reply(
            "📣 Share Daily News with your friends!\n\n"
            "📰 Daily News — @dailynewsreporter\n"
            "Tech news, AI updates, free AI tools and a daily quote.\n\n"
            "📱 Mini App: https://t.me/MuseVishnu_bot/Dailynews\n"
            "🤖 Bot: @MuseVishnu_bot\n"
            f"🔗 Your invite link: https://t.me/MuseVishnu_bot?start=ref_{code}\n\n"
            "Forward this message to a friend or a group!"
        )
    elif command == "/boost":
        if sender_id != OWNER_ID:
            reply("Only the owner can change the referral boost.")
        elif not args:
            status = "ON" if referral_boost_active(state) else "OFF"
            reply(f"🔥 Referral boost is {status}.\n\nWeekends boost automatically. /boost on forces it on, /boost off forces it off, /boost auto returns to weekend-only.")
        elif args.strip().lower() == "on":
            state["referral_boost"] = True
            reply("🔥 Referral boost is ON — invites now earn double: +100 XP for you, +40 XP for each friend who joins!")
        elif args.strip().lower() == "off":
            state["referral_boost"] = False
            reply("Referral boost is off. Standard rewards: +50 XP for you, +20 XP per friend.")
        elif args.strip().lower() == "auto":
            state["referral_boost"] = None
            reply("Referral boost is back to auto — double rewards on Saturday and Sunday.")
        else:
            reply("Use: /boost on — /boost off — /boost auto")
    elif command == "/id":
        lines = [f"👤 Your ID: {sender_id}"]
        if chat_type in ("group", "supergroup"):
            lines.append(f"👥 This group: {chat_id} ({chat.get('title', '')})")
        replied = message.get("reply_to_message") or {}
        if replied and (replied.get("from") or {}).get("id"):
            lines.append(f"💬 Replied user: {(replied.get('from') or {}).get('first_name', 'friend')} — {(replied.get('from') or {}).get('id')}")
        reply("\n".join(lines))
    elif command in ("/disable", "/enable", "/disabled"):
        if chat_type not in ("group", "supergroup") and not connected_group:
            reply("Use this in a group (or connect to one with /connect).")
        elif not is_admin(admin_chat, sender_id, dry_run=dry_run, sender_chat_id=_schat):
            reply("Only group admins can change disabled commands.")
        elif command == "/disabled":
            disabled = get_group_settings(state, admin_chat).get("disabled", [])
            reply(("🚫 Disabled here:\n" + "\n".join(f"• {c}" for c in disabled)) if disabled else "No commands are disabled here.")
        else:
            name = args.strip().split()[0].lower() if args.strip() else ""
            if not name.startswith("/"):
                reply("Give a command, like /disable /quote")
            elif name in ("/disable", "/enable", "/disabled", "/connect", "/disconnect"):
                reply("That command can't be disabled.")
            else:
                disabled = get_group_settings(state, admin_chat).setdefault("disabled", [])
                if command == "/disable":
                    if name not in disabled:
                        disabled.append(name)
                    reply(f"{name} is now disabled here.")
                else:
                    if name in disabled:
                        disabled.remove(name)
                    reply(f"{name} is enabled again.")
    elif command in ("/approve", "/unapprove", "/approved"):
        if chat_type not in ("group", "supergroup") and not connected_group:
            reply("Use this in a group (or connect to one with /connect).")
        elif not is_admin(admin_chat, sender_id, dry_run=dry_run, sender_chat_id=_schat):
            reply("Only group admins can approve members.")
        elif command == "/approved":
            approved = get_group_settings(state, admin_chat).get("approved", [])
            reply((f"✅ Approved members ({len(approved)}):\n" + "\n".join(f"• {u}" for u in approved)) if approved else "Nobody is approved here yet. /approve (reply) to exempt someone from locks, links and flood checks.")
        else:
            target_id, target_name = target_user(message, args)
            if not target_id:
                reply("Reply to a member's message, or give their user id, like /approve 123456")
            else:
                approved = get_group_settings(state, admin_chat).setdefault("approved", [])
                if command == "/approve":
                    if str(target_id) not in [str(u) for u in approved]:
                        approved.append(target_id)
                    reply(f"{target_name} is now approved — locks, links, flood and blacklist checks won't touch them.")
                else:
                    approved[:] = [u for u in approved if str(u) != str(target_id)]
                    reply(f"{target_name} is no longer approved.")
    elif command == "/setflood":
        if chat_type not in ("group", "supergroup") and not connected_group:
            reply("Use this in a group (or connect to one with /connect).")
        elif not is_admin(admin_chat, sender_id, dry_run=dry_run, sender_chat_id=_schat):
            reply("Only group admins can set the flood limit.")
        elif not args.strip().isdigit():
            reply(f"Flood limit is {get_group_settings(state, admin_chat).get('flood_limit', 5)} messages in 10 seconds. Set it with /setflood 8")
        else:
            n = max(1, min(20, int(args.strip())))
            get_group_settings(state, admin_chat)["flood_limit"] = n
            reply(f"Flood limit set to {n} messages in 10 seconds — more than that mutes the sender for 5 minutes.")
    elif command == "/setgoodbye":
        if chat_type not in ("group", "supergroup") and not connected_group:
            reply("Use this in a group (or connect to one with /connect).")
        elif not is_admin(admin_chat, sender_id, dry_run=dry_run, sender_chat_id=_schat):
            reply("Only group admins can set the goodbye message.")
        else:
            get_group_settings(state, admin_chat)["goodbye"] = args
            reply(f"Goodbye message saved. Use {{name}} for the member's name.\n\n{args}" if args else "Goodbye message turned off. Set one like /setgoodbye Bye {name}, take care!")
    elif command == "/fsub":
        if chat_type not in ("group", "supergroup") and not connected_group:
            reply("Use this in a group (or connect to one with /connect).")
        elif not is_admin(admin_chat, sender_id, dry_run=dry_run, sender_chat_id=_schat):
            reply("Only group admins can set force-subscribe.")
        elif not args:
            cur = get_group_settings(state, admin_chat).get("fsub", "")
            reply(f"Force-subscribe is {'on for ' + cur if cur else 'off'}. Set it with /fsub @DailyNewsReporter — or /fsub off to turn it off.")
        elif args.strip().lower() in ("off", "no", "none"):
            get_group_settings(state, admin_chat)["fsub"] = ""
            reply("Force-subscribe is off.")
        else:
            channel = args.strip().split()[0]
            if not channel.startswith("@"):
                channel = "@" + channel
            get_group_settings(state, admin_chat)["fsub"] = channel
            reply(f"Force-subscribe is on for {channel}. Non-members' messages will be removed until they join.")
    elif command == "/info":
        title = chat.get("title") or chat.get("first_name") or "this chat"
        reply(f"This is {title} (chat id {chat_id}, type {chat.get('type')}). I'm @MuseVishnu_bot.")
    elif command == "/setchannel":
        if not args:
            reply("Tell me the channel, like /setchannel @YourChannel — and make me an admin there first so I can post.")
        else:
            state["channel_id"] = args
            reply(f"Channel set to {args}. Make sure I'm an admin there, then try /postquote to test it.")
    elif command == "/channel":
        reply(f"Our channel is @dailynewsreporter — Daily News: tech, AI, free AI tools and invite codes, plus a daily quote.\n\nFollow it here: https://t.me/dailynewsreporter\n\nCurrent channel set in the bot: {state.get('channel_id') or 'not set yet'}")
    elif command == "/postquote":
        channel = state.get("channel_id")
        if not channel:
            reply("No channel set yet. Use /setchannel @YourChannel first, and make me an admin there.")
        else:
            kind = args.lower() if args.lower() in ("love", "friend", "motivate", "daily") else "daily"
            _q = pick_quote(kind if kind != "daily" else "any")
            actions.append({"type": "photo", "chat_id": channel, "quote": _q, "caption": channel_caption(_q, kind)})
            reply(f"Posted a {kind} quote image to {channel}.")
    elif command == "/quiz":
        q = random.choice(QUIZ_QUESTIONS)
        actions.append({"type": "quiz", "chat_id": chat_id, "question": q})
    elif command == "/poll":
        parts = [p.strip() for p in args.split("|")]
        if len(parts) < 3:
            reply("Make a poll like this: /poll Do you like quotes? | Yes | No")
        else:
            actions.append({"type": "poll", "chat_id": chat_id, "question": parts[0], "options": parts[1:]})
    elif command == "/score":
        scores = state.get("scores", {})
        if not scores:
            reply("No quiz scores yet. Try /quiz to play!")
        else:
            lines = sorted(scores.values(), key=lambda s: s["correct"], reverse=True)
            text_out = "Quiz scores:\n" + "\n".join(f"{s['name']}: {s['correct']}/{s['total']}" for s in lines[:10])
            reply(text_out)
    elif command == "/guess":
        games = state.setdefault("guess_games", {})
        key = str(chat_id)
        if args and args.strip().lstrip("-").isdigit():
            guess = int(args.strip())
            game = games.get(key)
            if not game:
                reply("No guessing game running. Send /guess to start one!")
            else:
                game["attempts"] += 1
                if guess == game["number"]:
                    reply(f"🎉 Correct! The number was {game['number']}. You got it in {game['attempts']} tries! Play again with /guess.")
                    _lvl = award_xp(state, sender_id, sender.get("first_name", "friend"), 15)
                    if _lvl:
                        reply(f"🎉 LEVEL UP! You are now Level {_lvl}! (+{xp_amount(state, sender_id, 15)} XP for the win)")
                    del games[key]
                elif guess < game["number"]:
                    reply(f"Too low! Try a bigger number. (Attempt {game['attempts']})")
                else:
                    reply(f"Too high! Try a smaller number. (Attempt {game['attempts']})")
        else:
            games[key] = {"number": random.randint(1, 100), "attempts": 0}
            reply("🎲 I'm thinking of a number between 1 and 100. Just send a number to guess — or use /guess 42. Good luck!")
    elif command == "/rps":
        keyboard = {"inline_keyboard": [[
            {"text": "🪨 Rock", "callback_data": "rps:rock"},
            {"text": "📄 Paper", "callback_data": "rps:paper"},
            {"text": "✂️ Scissors", "callback_data": "rps:scissors"},
        ]]}
        reply("✊✋✌️ Rock, Paper, Scissors — pick one!", keyboard=keyboard)
    elif command in ("/dice", "/darts", "/bowling", "/basketball", "/football", "/slots"):
        emoji = {"/dice": "🎲", "/darts": "🎯", "/bowling": "🎳", "/basketball": "🏀", "/football": "⚽", "/slots": "🎰"}[command]
        actions.append({"type": "dice", "chat_id": chat_id, "emoji": emoji})
    elif command == "/scramble":
        games = state.setdefault("scramble_games", {})
        word = random.choice(SCRAMBLE_WORDS)
        games[str(chat_id)] = {"word": word, "scrambled": scramble_word(word)}
        reply(f"🔤 Unscramble this word: {games[str(chat_id)]['scrambled']}\n\nJust type your answer!")
    elif command == "/remind":
        parts = args.split(None, 1)
        if len(parts) < 2:
            reply("Remind me like this: /remind 10m drink water — use s, m, h or d, like 30s, 10m, 1h.")
        else:
            seconds = parse_duration(parts[0])
            if seconds is None:
                reply("I didn't understand that time. Try /remind 10m drink water")
            else:
                due = int(time.time()) + seconds
                state["reminders"].append({"chat_id": chat_id, "text": parts[1], "due": due, "user": (message.get("from") or {}).get("first_name", "friend")})
                reply(f"Got it, I'll remind you in {parts[0]}: {parts[1]}")
    elif command == "/reminders":
        mine = [r for r in state.get("reminders", []) if r["chat_id"] == chat_id]
        if not mine:
            reply("You have no reminders set. Try /remind 10m drink water")
        else:
            lines = []
            for r in mine:
                mins = max(0, (r["due"] - int(time.time())) // 60)
                lines.append(f"in about {mins}m: {r['text']}")
            reply("Your reminders:\n" + "\n".join(lines))
    elif command == "/ask":
        if not args:
            reply("Ask me like this: /ask what is the capital of Cambodia? I can answer facts, do maths, and tell you the time and date.")
        else:
            answer = answer_question(args, state, user_record)
            if answer.startswith("Good question!"):
                ai = ai_reply(state, sender_id, args, dry_run=dry_run)
                reply(ai if ai else answer)
            else:
                reply(answer)
    elif command == "/stats":
        stats = state.get("stats", {})
        users = state.get("users", {})
        started = datetime.datetime.fromtimestamp(stats.get("started", int(time.time())), tz=datetime.timezone(datetime.timedelta(hours=7)))
        uptime_hours = max(0, (int(time.time()) - stats.get("started", int(time.time()))) // 3600)
        top_cmd = ""
        if stats.get("commands"):
            top_cmd = max(stats["commands"], key=lambda k: stats["commands"][k])
        reply(
            "Bot stats:\n"
            f"Messages handled: {stats.get('messages', 0)}\n"
            f"People I have talked to: {len(users)}\n"
            f"Daily subscribers: {len(state.get('subscribers', []))}\n"
            f"Channel: {state.get('channel_id') or 'not set'}\n"
            f"Quotes sent: {stats.get('quotes_sent', 0)}\n"
            f"Quiz players: {len(state.get('scores', {}))}\n"
            f"Reminders waiting: {len(state.get('reminders', []))}\n"
            f"Most used command: {top_cmd or 'none yet'}\n"
            f"Running since: {started.strftime('%d %B %Y %I:%M %p')} (about {uptime_hours}h)"
        )
    elif command == "/mystats":
        if not user_record:
            reply("I don't know you yet. Send me a message first.")
        else:
            score = state.get("scores", {}).get(str((message.get("from") or {}).get("id", "")), {"correct": 0, "total": 0})
            rec = xp_record(state, (message.get("from") or {}).get("id", ""))
            streak = state.get("streaks", {}).get(str((message.get("from") or {}).get("id", "")), {"count": 0})
            reply(
                f"Your stats, {user_record.get('name', 'friend')}:\n"
                f"Messages: {user_record.get('messages', 0)}\n"
                f"Quotes you asked for: {user_record.get('quotes', 0)}\n"
                f"Quizzes played: {user_record.get('quizzes', 0)}\n"
                f"Quiz score: {score.get('correct', 0)}/{score.get('total', 0)}\n"
                f"⭐ Level {xp_level(rec['xp'])} — {rec['xp']} XP\n"
                f"🔥 Streak: {streak.get('count', 0)} day(s)\n"
                f"Favourite: {user_record.get('favourite') or 'not yet'}\n"
                f"Last command: {user_record.get('last_command') or 'none'}"
            )
    elif command == "/top":
        scores = state.get("scores", {})
        users = state.get("users", {})
        lines = []
        if scores:
            ranked = sorted(scores.values(), key=lambda s: s["correct"], reverse=True)[:5]
            lines.append("Top quiz players:")
            lines.extend(f"{i+1}. {s['name']}: {s['correct']}/{s['total']}" for i, s in enumerate(ranked))
        if users:
            active = sorted(users.values(), key=lambda u: u.get("messages", 0), reverse=True)[:5]
            lines.append("Most active chatters:")
            lines.extend(f"{i+1}. {u['name']}: {u.get('messages', 0)} messages" for i, u in enumerate(active))
        reply("\n".join(lines) if lines else "No stats yet. Try /quiz or send me a message!")
    elif command == "/joke":
        reply(random.choice(JOKES))
    elif command == "/motivate":
        reply(random.choice(MOTIVATION))
    elif text in ("💬 Quote", "❤️ Love", "🤝 Friend", "🇰🇭 Khmer"):
        kind = {"💬 Quote": "any", "❤️ Love": "love", "🤝 Friend": "friend", "🇰🇭 Khmer": "khmer"}[text]
        reply(pick_quote(kind))
    elif text == "📰 News":
        reply(NEWS_TEXT)
    elif text == "🎮 Games":
        reply(GAMES_MENU_TEXT)
    elif text == "🎯 Quiz":
        q = random.choice(QUIZ_QUESTIONS)
        actions.append({"type": "quiz", "chat_id": chat_id, "question": q})
    elif text == "📅 Schedule":
        reply(SCHEDULE_TEXT)
    elif text == "ℹ️ Help":
        reply(HELP_TEXT)
    elif not text.startswith("/") and text.strip().lstrip("-").isdigit() and str(chat_id) in state.get("guess_games", {}):
        guess = int(text.strip())
        game = state["guess_games"][str(chat_id)]
        game["attempts"] += 1
        if guess == game["number"]:
            reply(f"🎉 Correct! The number was {game['number']}. You got it in {game['attempts']} tries! Play again with /guess.")
            _lvl = award_xp(state, sender_id, sender.get("first_name", "friend"), 15)
            if _lvl:
                reply(f"🎉 LEVEL UP! You are now Level {_lvl}! (+{xp_amount(state, sender_id, 15)} XP for the win)")
            del state["guess_games"][str(chat_id)]
        elif guess < game["number"]:
            reply(f"Too low! Try a bigger number. (Attempt {game['attempts']})")
        else:
            reply(f"Too high! Try a smaller number. (Attempt {game['attempts']})")
    elif not text.startswith("/") and str(chat_id) in state.get("scramble_games", {}):
        game = state["scramble_games"][str(chat_id)]
        if text.strip().lower() == game["word"]:
            reply(f"🎉 Correct! The word was {game['word'].upper()}. Play again with /scramble.")
            _lvl = award_xp(state, sender_id, sender.get("first_name", "friend"), 10)
            if _lvl:
                reply(f"🎉 LEVEL UP! You are now Level {_lvl}! (+{xp_amount(state, sender_id, 10)} XP for the win)")
            del state["scramble_games"][str(chat_id)]
        else:
            reply(f"Not quite — the scrambled word is {game['scrambled']}. Try again!")
    elif lower.startswith(("hello", "hi", "hey", "សួស្តី")):
        reply("Hello! Lovely to hear from you. Want a quote? Just send /quote, or /quoteimg for a picture.")
    elif "joke" in lower:
        reply(random.choice(JOKES))
    elif "quote" in lower:
        reply(pick_quote("any"))
    elif "thank" in lower:
        reply("You're very welcome! Want another quote? Send /quote.")
    elif "how are you" in lower:
        reply("I'm doing well, thank you! Running on quotes and kindness. How are you?")
    elif lower.startswith(("who are you", "what can you do")):
        reply(HELP_TEXT)
    elif "good morning" in lower:
        reply("Good morning! Here's a little kindness to start your day: " + pick_quote("any"))
    elif "good night" in lower:
        reply("Good night! Sleep well. Here's a quote for tomorrow: " + pick_quote("any"))
    elif text.startswith("/"):
        reply("I don't know that command yet. Try /help to see what I can do.")
    elif any(w in lower for w in ("i am sad", "i'm sad", "i feel sad", "i am tired", "i'm tired", "i feel down", "i am lonely")):
        name = (message.get("from") or {}).get("first_name", "friend")
        reply(f"I'm sorry you're feeling that way, {name}. Here's something for you: {pick_quote('any')}")
    elif any(w in lower for w in ("i am happy", "i'm happy", "i feel great", "i am excited")):
        reply("That's wonderful to hear! I'm glad. Want to celebrate with a quote? Send /quote.")
    elif "what is" in lower or lower.endswith("?"):
        answer = answer_question(text, state, user_record)
        if answer.startswith("Good question!"):
            ai = ai_reply(state, sender_id, text, dry_run=dry_run)
            reply(ai if ai else answer)
        else:
            reply(answer)
    elif re.fullmatch(r"[0-9\s\.\+\-\*\/\(\)\%]+", text):
        result = safe_math(text)
        reply(f"{text.strip()} = {result}" if result is not None else "I couldn't work that out. Try something like 12+8.")
    elif "remember" in lower and "my name is" in lower:
        reply(f"Got it, I'll remember you. Nice to meet you, {(message.get('from') or {}).get('first_name', 'friend')}!")
    elif "khmer" in lower and any(w in lower for w in ("reply", "speak", "talk", "write", "language", "in khmer", "use khmer")):
        if user_record is not None:
            user_record["lang"] = "kh"
        reply("បាទ! ខ្ញុំអាចសរសេរ និងឆ្លើយជាភាសាខ្មែរបាន។ ចាប់ពីពេលនេះតទៅ ខ្ញុំនឹងឆ្លើយជាភាសាខ្មែរ។ សាកសួរខ្ញុំអ្វីមួយមក! 🇰🇭")
    elif "english" in lower and any(w in lower for w in ("reply", "speak", "talk", "write", "language", "in english", "use english")):
        if user_record is not None:
            user_record["lang"] = "en"
        reply("Sure! I'll reply in English from now on. Ask me anything!")
    else:
        # Private chat: talk with the AI instead of the canned thanks message.
        # Groups keep the short thanks reply so the bot doesn't chatter there.
        ai = ai_reply(state, sender_id, text, dry_run=dry_run) if chat_type == "private" else None
        if ai:
            reply(ai)
        elif user_record and user_record.get("messages", 0) > 3:
            reply(f"Thanks, {user_record.get('name', 'friend')}! You have sent me {user_record.get('messages', 0)} messages now. Send /quote for a quote, /quiz for a game, /ask to ask me something, or /help to see everything.")
        else:
            reply("Thanks for your message! Send /quote for a quote, /quoteimg for a picture, /quiz for a game, /ask to ask me something, or /help to see everything I can do.")
    if msg_leveled:
        actions.append({"type": "text", "chat_id": chat_id, "text": f"🎉 LEVEL UP! You are now Level {msg_leveled}! Keep chatting and playing to earn more XP."})
    return actions


def execute_actions(actions, state):
    for action in actions:
        if action["type"] == "text":
            send_message(action["chat_id"], action["text"], reply_markup=action.get("keyboard"))
        elif action["type"] == "photo":
            path = make_quote_image(action["quote"])
            try:
                send_photo(action["chat_id"], path, caption=action.get("caption", ""))
            finally:
                try:
                    os.remove(path)
                except OSError:
                    pass
        elif action["type"] == "story":
            path = make_story_image(action["quote"])
            try:
                send_photo(action["chat_id"], path, caption=action.get("caption", ""))
            finally:
                try:
                    os.remove(path)
                except OSError:
                    pass
        elif action["type"] == "quiz":
            q = action["question"]
            result = send_poll(action["chat_id"], q["q"], q["options"], is_quiz=True, correct=q["correct"], explanation=q["explain"])
            poll_id = (result.get("result") or {}).get("poll", {}).get("id")
            if poll_id:
                state["quiz_polls"][poll_id] = {"chat_id": action["chat_id"], "correct": q["correct"], "options": q["options"], "explain": q["explain"]}
                if action.get("tournament"):
                    state.setdefault("tournament_polls", {})[poll_id] = action["chat_id"]
                    t = state.get("tournaments", {}).get(str(action["chat_id"]))
                    if t:
                        t["current_poll"] = poll_id
                save_state(state)
        elif action["type"] == "poll":
            send_poll(action["chat_id"], action["question"], action["options"])
        elif action["type"] == "callback":
            try:
                api_call("answerCallbackQuery", {"callback_query_id": action["callback_id"], "text": action.get("text", "")})
            except Exception as e:
                print(f"answerCallbackQuery failed: {e}", flush=True)
        elif action["type"] == "inline":
            try:
                api_call("answerInlineQuery", {"inline_query_id": action["inline_query_id"], "results": action["results"], "cache_time": 60, "is_personal": True})
            except Exception as e:
                print(f"answerInlineQuery failed: {e}", flush=True)
        elif action["type"] == "dice":
            try:
                api_call("sendDice", {"chat_id": action["chat_id"], "emoji": action["emoji"]})
            except Exception as e:
                print(f"sendDice failed: {e}", flush=True)
        elif action["type"] == "ban":
            payload = {"chat_id": action["chat_id"], "user_id": action["user_id"]}
            if action.get("until"):
                payload["until_date"] = action["until"]
            api_call("banChatMember", payload)
            print(f"banned {action['user_id']} in {action['chat_id']}", flush=True)
        elif action["type"] == "unban":
            api_call("unbanChatMember", {"chat_id": action["chat_id"], "user_id": action["user_id"]})
            print(f"unbanned {action['user_id']} in {action['chat_id']}", flush=True)
        elif action["type"] == "fsub_check":
            try:
                res = api_call("getChatMember", {"chat_id": action["channel"], "user_id": action["user_id"]})
                status = (res.get("result") or {}).get("status")
                if status in ("left", "kicked"):
                    api_call("deleteMessage", {"chat_id": action["chat_id"], "message_id": action["message_id"]})
                    send_message(action["chat_id"], f"Join {action['channel']} to chat here — then send your message again.")
            except Exception as e:
                print(f"fsub check failed: {e}", flush=True)
        elif action["type"] == "restrict":
            payload = {"chat_id": action["chat_id"], "user_id": action["user_id"], "permissions": action["permissions"]}
            if action.get("until"):
                payload["until_date"] = action["until"]
            api_call("restrictChatMember", payload)
            print(f"restricted {action['user_id']} in {action['chat_id']}", flush=True)
        elif action["type"] == "delete":
            api_call("deleteMessage", {"chat_id": action["chat_id"], "message_id": action["message_id"]})
            print(f"deleted message {action['message_id']} in {action['chat_id']}", flush=True)
        elif action["type"] == "pin":
            api_call("pinChatMessage", {"chat_id": action["chat_id"], "message_id": action["message_id"]})
            print(f"pinned message {action['message_id']} in {action['chat_id']}", flush=True)
        elif action["type"] == "unpin":
            api_call("unpinChatMessage", {"chat_id": action["chat_id"]})
            print(f"unpinned in {action['chat_id']}", flush=True)
        elif action["type"] == "set_permissions":
            api_call("setChatPermissions", {"chat_id": action["chat_id"], "permissions": action["permissions"]})
            print(f"set chat permissions in {action['chat_id']}", flush=True)
        elif action["type"] == "promote":
            if action.get("promote"):
                perms = {"can_change_info": False, "can_delete_messages": True, "can_invite_users": True, "can_restrict_members": True, "can_pin_messages": True, "can_promote_members": False, "can_manage_chat": False, "can_manage_video_chats": False, "can_post_stories": False, "can_edit_stories": False, "can_delete_stories": False}
            else:
                perms = {"can_change_info": False, "can_delete_messages": False, "can_invite_users": False, "can_restrict_members": False, "can_pin_messages": False, "can_promote_members": False, "can_manage_chat": False, "can_manage_video_chats": False, "can_post_stories": False, "can_edit_stories": False, "can_delete_stories": False}
            api_call("promoteChatMember", {"chat_id": action["chat_id"], "user_id": action["user_id"], **perms})
            print(f"{'promoted' if action.get('promote') else 'demoted'} {action['user_id']} in {action['chat_id']}", flush=True)
        elif action["type"] == "edit":
            try:
                payload = {"chat_id": action["chat_id"], "message_id": action["message_id"], "text": action["text"]}
                if action.get("keyboard"):
                    payload["reply_markup"] = action["keyboard"]
                api_call("editMessageText", payload)
            except Exception as e:
                print(f"editMessageText failed: {e}", flush=True)
        elif action["type"] == "invoice":
            # Telegram Stars: provider_token must be empty for XTR.
            payload = {"chat_id": action["chat_id"], "title": action["title"], "description": action["description"], "payload": action["payload"], "provider_token": "", "currency": action.get("currency", "XTR"), "prices": action["prices"]}
            if action.get("keyboard"):
                payload["reply_markup"] = action["keyboard"]
            api_call("sendInvoice", payload)
            print(f"sent invoice to {action['chat_id']}", flush=True)
        elif action["type"] == "precheckout":
            api_call("answerPreCheckoutQuery", {"pre_checkout_query_id": action["query_id"], "ok": action.get("ok", True)})
            print(f"answered pre-checkout {action['query_id']}", flush=True)
        time.sleep(0.3)


def check_reminders(state):
    now = int(time.time())
    due = [r for r in state.get("reminders", []) if r["due"] <= now]
    if not due:
        return
    for r in due:
        try:
            send_message(r["chat_id"], f"Reminder: {r['text']}")
        except Exception as e:
            print(f"failed to send reminder to {r['chat_id']}: {e}", flush=True)
    state["reminders"] = [r for r in state.get("reminders", []) if r["due"] > now]
    save_state(state)


def poll_forever():
    state = load_state()
    print("Super bot polling started for @MuseVishnu_bot", flush=True)
    while True:
        write_heartbeat(state)
        check_reminders(state)
        try:
            result = api_call("getUpdates", {"offset": state["offset"], "limit": 100, "timeout": 20}, timeout=35)
        except urllib.error.HTTPError as e:
            print(f"getUpdates HTTP {e.code}, retrying in 10s", flush=True)
            time.sleep(10)
            continue
        except Exception as e:
            print(f"getUpdates error {e}, retrying in 10s", flush=True)
            time.sleep(10)
            continue
        if not result.get("ok"):
            print(f"getUpdates not ok: {result}, retrying in 10s", flush=True)
            time.sleep(10)
            continue
        for update in result.get("result", []):
            state["offset"] = update["update_id"] + 1
            save_state(state)
            try:
                actions = handle_update(update, state)
                execute_actions(actions, state)
                save_state(state)
            except Exception as e:
                print(f"error handling update {update.get('update_id')}: {e}", flush=True)


def send_daily():
    state = load_state()
    today = phnom_penh_now().strftime("%Y-%m-%d")
    if state["last_daily"] == today and (state["subscribers"] or state.get("channel_id")):
        print(f"daily quote already sent for {today}", flush=True)
        return 0
    targets = list(state["subscribers"])
    channel = state.get("channel_id")
    if channel and channel not in targets:
        targets.append(channel)
    if not targets:
        print("no daily subscribers or channel yet", flush=True)
        return 0
    quote = pick_quote("any", state)
    path = make_quote_image(quote)
    caption = channel_caption(quote, "daily")
    sent = 0
    try:
        for chat_id in targets:
            # Personal line for people (not the channel, not groups).
            cap = caption
            if isinstance(chat_id, int):
                rec = state.get("xp", {}).get(str(chat_id))
                st = state.get("streaks", {}).get(str(chat_id))
                if rec or st:
                    xp = (rec or {}).get("xp", 0)
                    streak = (st or {}).get("count", 0)
                    cap = caption + f"\n\n🔥 Your streak: {streak} day(s) • ⭐ Level {xp_level(xp)} ({xp} XP)\nCheck in with /checkin to keep your streak alive!"
            try:
                send_photo(chat_id, path, caption=cap)
                sent += 1
            except Exception as e:
                print(f"daily photo failed for {chat_id}: {e}, trying text", flush=True)
                try:
                    send_message(chat_id, cap)
                    sent += 1
                except Exception as e2:
                    print(f"failed to send daily quote to {chat_id}: {e2}", flush=True)
    finally:
        try:
            os.remove(path)
        except OSError:
            pass
    state["last_daily"] = today
    save_state(state)
    print(f"daily quote sent to {sent} target(s)", flush=True)
    return sent


def send_news(title, summary, source=""):
    """Post one researched news item to the channel. Used by Muse, not by the bot itself."""
    state = load_state()
    channel = state.get("channel_id")
    if not channel:
        print("no channel set for news", flush=True)
        return 0
    text = news_caption(title, summary, source)
    result = send_message(channel, text)
    print(f"news posted to {channel}: {result.get('ok')}", flush=True)
    return 1 if result.get("ok") else 0


def send_evening():
    """Evening love quote to the channel only, 6pm schedule."""
    state = load_state()
    today = phnom_penh_now().strftime("%Y-%m-%d")
    if state.get("last_evening") == today:
        print(f"evening quote already sent for {today}", flush=True)
        return 0
    channel = state.get("channel_id")
    if not channel:
        print("no channel set for evening quote", flush=True)
        return 0
    quote = pick_quote("love", state)
    path = make_quote_image(quote, kind="love")
    try:
        send_photo(channel, path, caption=channel_caption(quote, "love"))
        print(f"evening quote sent to {channel}", flush=True)
        sent = 1
    except Exception as e:
        print(f"evening photo failed: {e}, trying text", flush=True)
        send_message(channel, channel_caption(quote, "love"))
        sent = 1
    finally:
        try:
            os.remove(path)
        except OSError:
            pass
    state["last_evening"] = today
    save_state(state)
    return sent


def run_tests():
    state = load_state()
    fake = [
        {"update_id": 1, "message": {"chat": {"id": 111, "type": "private"}, "from": {"first_name": "Dailyquote"}, "text": "/start"}},
        {"update_id": 2, "message": {"chat": {"id": 111, "type": "private"}, "text": "/quoteimg"}},
        {"update_id": 3, "message": {"chat": {"id": 111, "type": "private"}, "text": "/setchannel @DailyQuotes"}},
        {"update_id": 4, "message": {"chat": {"id": 111, "type": "private"}, "text": "/quiz"}},
        {"update_id": 5, "message": {"chat": {"id": 111, "type": "private"}, "text": "/poll Do you like quotes? | Yes | No"}},
        {"update_id": 6, "message": {"chat": {"id": 111, "type": "private"}, "text": "/remind 10m drink water"}},
        {"update_id": 7, "message": {"chat": {"id": 111, "type": "private"}, "text": "/joke"}},
        {"update_id": 8, "message": {"chat": {"id": 111, "type": "private"}, "text": "how are you?"}},
        {"update_id": 9, "callback_query": {"id": "cb1", "data": "quote", "message": {"chat": {"id": 111, "type": "private"}}}},
        {"update_id": 10, "poll_answer": {"poll_id": "test123", "user": {"id": 999, "first_name": "Test"}, "option_ids": [0]}},
        {"update_id": 11, "message": {"chat": {"id": -100123, "type": "supergroup", "title": "Test Group"}, "from": {"id": 111, "first_name": "Dailyquote"}, "text": "/admins"}},
        {"update_id": 12, "message": {"chat": {"id": -100123, "type": "supergroup"}, "from": {"id": 111, "first_name": "Dailyquote"}, "text": "/warn", "reply_to_message": {"message_id": 5, "from": {"id": 222, "first_name": "Spammer"}}}},
        {"update_id": 13, "message": {"chat": {"id": -100123, "type": "supergroup"}, "from": {"id": 111, "first_name": "Dailyquote"}, "text": "/mute 10m", "reply_to_message": {"message_id": 6, "from": {"id": 222, "first_name": "Spammer"}}}},
        {"update_id": 14, "message": {"chat": {"id": -100123, "type": "supergroup"}, "from": {"id": 111, "first_name": "Dailyquote"}, "text": "/setwelcome Welcome {name} to our group!"}},
        {"update_id": 15, "message": {"chat": {"id": -100123, "type": "supergroup"}, "new_chat_members": [{"first_name": "Sophea"}]}},
        {"update_id": 16, "message": {"chat": {"id": -100123, "type": "supergroup"}, "from": {"id": 111, "first_name": "Dailyquote"}, "text": "/antilink on"}},
        {"update_id": 17, "message": {"chat": {"id": -100123, "type": "supergroup"}, "from": {"id": 333, "first_name": "Bad"}, "text": "check this https://spam.example"}},
        {"update_id": 18, "message": {"chat": {"id": -100123, "type": "supergroup"}, "from": {"id": 111, "first_name": "Dailyquote"}, "text": "/lock"}},
        {"update_id": 19, "message": {"chat": {"id": 111, "type": "private"}, "from": {"id": 111, "first_name": "Dailyquote"}, "text": "/ask what is the capital of Cambodia?"}},
        {"update_id": 20, "message": {"chat": {"id": 111, "type": "private"}, "from": {"id": 111, "first_name": "Dailyquote"}, "text": "/ask what is 12+8"}},
        {"update_id": 21, "message": {"chat": {"id": 111, "type": "private"}, "from": {"id": 111, "first_name": "Dailyquote"}, "text": "I am sad today"}},
        {"update_id": 22, "message": {"chat": {"id": 111, "type": "private"}, "from": {"id": 111, "first_name": "Dailyquote"}, "text": "/stats"}},
        {"update_id": 23, "message": {"chat": {"id": 111, "type": "private"}, "from": {"id": 111, "first_name": "Dailyquote"}, "text": "/mystats"}},
        {"update_id": 24, "message": {"chat": {"id": 111, "type": "private"}, "from": {"id": 111, "first_name": "Dailyquote"}, "text": "/top"}},
        {"update_id": 25, "message": {"chat": {"id": 111, "type": "private"}, "from": {"id": 111, "first_name": "Dailyquote"}, "text": "/ask what is the meaning of life?"}},
        {"update_id": 26, "message": {"chat": {"id": 111, "type": "private"}, "from": {"id": 111, "first_name": "Dailyquote"}, "text": "/about"}},
        {"update_id": 27, "message": {"chat": {"id": 111, "type": "private"}, "from": {"id": 111, "first_name": "Dailyquote"}, "text": "/channel"}},
        {"update_id": 28, "message": {"chat": {"id": 111, "type": "private"}, "from": {"id": 111, "first_name": "Dailyquote"}, "text": "/postquote love"}},
        {"update_id": 29, "message": {"chat": {"id": 111, "type": "private"}, "from": {"id": 111, "first_name": "Dailyquote"}, "text": "/news"}},
        {"update_id": 30, "message": {"chat": {"id": 111, "type": "private"}, "from": {"id": 111, "first_name": "Dailyquote"}, "text": "/ainews"}},
        {"update_id": 31, "message": {"chat": {"id": 111, "type": "private"}, "from": {"id": 111, "first_name": "Dailyquote"}, "text": "/freeai"}},
        {"update_id": 32, "message": {"chat": {"id": 111, "type": "private"}, "from": {"id": 111, "first_name": "Dailyquote"}, "text": "/invite"}},
        {"update_id": 33, "message": {"chat": {"id": 111, "type": "private"}, "from": {"id": 5039926430, "first_name": "Job Recruiter"}, "text": "/addinvite Muse TESTCODE"}},
        {"update_id": 34, "message": {"chat": {"id": 111, "type": "private"}, "from": {"id": 111, "first_name": "Dailyquote"}, "text": "/postnews Test title | Test summary | Test source"}},
        {"update_id": 35, "message": {"chat": {"id": 111, "type": "private"}, "from": {"id": 111, "first_name": "Dailyquote"}, "text": "/khmer"}},
        {"update_id": 36, "message": {"chat": {"id": 111, "type": "private"}, "from": {"id": 5039926430, "first_name": "Job Recruiter"}, "text": "/addquote Be kind, always."}},
        {"update_id": 37, "message": {"chat": {"id": 111, "type": "private"}, "from": {"id": 111, "first_name": "Dailyquote"}, "text": "/schedule"}},
        {"update_id": 38, "message": {"chat": {"id": 111, "type": "private"}, "from": {"id": 111, "first_name": "Dailyquote"}, "text": "/quote"}},
        {"update_id": 39, "message": {"chat": {"id": 111, "type": "private"}, "from": {"id": 111, "first_name": "Dailyquote"}, "text": "/khmerimg"}},
        {"update_id": 40, "inline_query": {"id": "iq-test-1", "from": {"id": 111, "first_name": "Dailyquote"}, "query": "love", "offset": ""}},
        {"update_id": 41, "inline_query": {"id": "iq-test-2", "from": {"id": 111, "first_name": "Dailyquote"}, "query": "", "offset": ""}},
        {"update_id": 42, "message": {"chat": {"id": 111, "type": "private"}, "from": {"id": 111, "first_name": "Dailyquote"}, "text": "/guess"}},
        {"update_id": 43, "message": {"chat": {"id": 111, "type": "private"}, "from": {"id": 111, "first_name": "Dailyquote"}, "text": "50"}},
        {"update_id": 44, "message": {"chat": {"id": 111, "type": "private"}, "from": {"id": 111, "first_name": "Dailyquote"}, "text": "/rps"}},
        {"update_id": 45, "callback_query": {"id": "cb-rps", "data": "rps:rock", "message": {"chat": {"id": 111, "type": "private"}}}},
        {"update_id": 46, "message": {"chat": {"id": 111, "type": "private"}, "from": {"id": 111, "first_name": "Dailyquote"}, "text": "/dice"}},
        {"update_id": 47, "message": {"chat": {"id": 111, "type": "private"}, "from": {"id": 111, "first_name": "Dailyquote"}, "text": "/slots"}},
        {"update_id": 48, "message": {"chat": {"id": 111, "type": "private"}, "from": {"id": 111, "first_name": "Dailyquote"}, "text": "/scramble"}},
        {"update_id": 49, "message": {"chat": {"id": 111, "type": "private"}, "from": {"id": 111, "first_name": "Dailyquote"}, "text": "wrongword"}},
        {"update_id": 50, "message": {"chat": {"id": 111, "type": "private"}, "from": {"id": 111, "first_name": "Dailyquote"}, "text": "/menu"}},
        {"update_id": 51, "message": {"chat": {"id": 111, "type": "private"}, "from": {"id": 111, "first_name": "Dailyquote"}, "text": "💬 Quote"}},
        {"update_id": 52, "message": {"chat": {"id": 111, "type": "private"}, "from": {"id": 111, "first_name": "Dailyquote"}, "text": "🎮 Games"}},
        {"update_id": 53, "message": {"chat": {"id": 111, "type": "private"}, "from": {"id": 111, "first_name": "Dailyquote"}, "text": "🎯 Quiz"}},
        {"update_id": 54, "message": {"chat": {"id": 111, "type": "private"}, "from": {"id": 111, "first_name": "Dailyquote"}, "text": "/hidemenu"}},
    ]
    for update in fake:
        actions = handle_update(update, state, dry_run=True)
        print(f"update {update['update_id']}: {actions}")

    # Phase 1 (Rose-style group power) tests. Group -100123 has admin 111,
    # spammer 222, bad user 333, new member 444 from the fake updates above.
    fails = []
    def check(label, cond):
        print(("PASS " if cond else "FAIL ") + label)
        if not cond:
            fails.append(label)
    def texts(actions):
        return [a.get("text", "") for a in actions if a.get("type") == "text"]
    group = {"id": -100123, "type": "supergroup", "title": "Test Group"}
    group2 = {"id": -100456, "type": "supergroup", "title": "Second Group"}
    admin = {"id": 111, "first_name": "Dailyquote"}

    # Federation: create, join a second group, fban across both, unfban.
    a = handle_update({"update_id": 101, "message": {"chat": group, "from": admin, "text": "/newfed TestFed"}}, state, dry_run=True)
    feds = state.get("federations", {})
    check("newfed creates one federation", len(feds) == 1)
    fid = list(feds.keys())[0] if feds else ""
    check("newfed reply shows the fed id", any(fid in t for t in texts(a)))
    a = handle_update({"update_id": 102, "message": {"chat": group2, "from": admin, "text": f"/joinfed {fid}"}}, state, dry_run=True)
    check("joinfed adds second group", len(feds[fid]["groups"]) == 2)
    a = handle_update({"update_id": 103, "message": {"chat": group, "from": admin, "text": "/fban spam", "reply_to_message": {"message_id": 9, "from": {"id": 222, "first_name": "Spammer"}}}}, state, dry_run=True)
    check("fban bans in both fed groups", len([x for x in a if x.get("type") == "ban"]) == 2)
    check("fban records the ban", "222" in feds[fid]["banned"])
    a = handle_update({"update_id": 104, "message": {"chat": group2, "new_chat_members": [{"id": 222, "first_name": "Spammer"}]}}, state, dry_run=True)
    check("fed-banned user is banned on joining another fed group", any(x.get("type") == "ban" and x.get("user_id") == 222 for x in a))
    a = handle_update({"update_id": 105, "message": {"chat": group, "from": admin, "text": "/unfban 222"}}, state, dry_run=True)
    check("unfban unbans in both fed groups", len([x for x in a if x.get("type") == "unban"]) == 2 and "222" not in feds[fid]["banned"])
    a = handle_update({"update_id": 106, "message": {"chat": group, "from": admin, "text": "/fedinfo"}}, state, dry_run=True)
    check("fedinfo shows the federation", any("TestFed" in t for t in texts(a)))

    # Notes and filters, including #name and keyword triggers.
    a = handle_update({"update_id": 107, "message": {"chat": group, "from": admin, "text": "/save welcome Hello {name}!"}}, state, dry_run=True)
    check("save stores a note", state["group_settings"]["-100123"]["notes"].get("welcome") == "Hello {name}!")
    a = handle_update({"update_id": 108, "message": {"chat": group, "from": admin, "text": "/get welcome"}}, state, dry_run=True)
    check("get reads the note with the name filled in", any(t == "Hello Dailyquote!" for t in texts(a)))
    a = handle_update({"update_id": 109, "message": {"chat": group, "from": {"id": 333, "first_name": "Bad"}, "text": "#welcome"}}, state, dry_run=True)
    check("#note trigger reads the note", any(t == "Hello Bad!" for t in texts(a)))
    a = handle_update({"update_id": 110, "message": {"chat": group, "from": admin, "text": "/filter hello Hello {name}, welcome!"}}, state, dry_run=True)
    check("filter is saved", "hello" in state["group_settings"]["-100123"]["filters"])
    a = handle_update({"update_id": 111, "message": {"chat": group, "from": {"id": 333, "first_name": "Bad"}, "text": "hello everyone"}}, state, dry_run=True)
    check("filter auto-replies to the keyword", any(t == "Hello Bad, welcome!" for t in texts(a)))

    # Captcha: join mutes + button, wrong user cannot verify, member can.
    a = handle_update({"update_id": 112, "message": {"chat": group, "from": admin, "text": "/captcha on"}}, state, dry_run=True)
    check("captcha turns on", state["group_settings"]["-100123"]["captcha"] is True)
    a = handle_update({"update_id": 113, "message": {"chat": group, "new_chat_members": [{"id": 444, "first_name": "Newbie"}]}}, state, dry_run=True)
    check("captcha join restricts the member", any(x.get("type") == "restrict" and x.get("user_id") == 444 for x in a))
    check("captcha join sends the verify button", any("verify:-100123:444" in str(x.get("keyboard", "")) for x in a))
    a = handle_update({"update_id": 114, "callback_query": {"id": "cbv-wrong", "data": "verify:-100123:444", "from": {"id": 555, "first_name": "Stranger"}, "message": {"chat": group, "message_id": 77}}}, state, dry_run=True)
    check("wrong user cannot verify", any("Only the new member" in t for t in texts(a)))
    a = handle_update({"update_id": 115, "callback_query": {"id": "cbv-ok", "data": "verify:-100123:444", "from": {"id": 444, "first_name": "Newbie"}, "message": {"chat": group, "message_id": 77}}}, state, dry_run=True)
    check("member verifies and is unresticted", any(x.get("type") == "restrict" and x.get("user_id") == 444 and (x.get("permissions") or {}).get("can_send_messages") for x in a))
    check("verify edits the captcha message", any(x.get("type") == "edit" and x.get("message_id") == 77 for x in a))
    check("pending verify cleared", f"-100123:444" not in state.get("pending_verify", {}))

    # Blacklist words (delete, then warn mode) and stickers.
    a = handle_update({"update_id": 116, "message": {"chat": group, "from": admin, "text": "/addblacklist spamword"}}, state, dry_run=True)
    check("blacklist word saved", "spamword" in state["group_settings"]["-100123"]["blacklist_words"])
    a = handle_update({"update_id": 117, "message": {"chat": group, "from": {"id": 333, "first_name": "Bad"}, "text": "this has spamword in it"}}, state, dry_run=True)
    check("blacklisted word deletes the message", any(x.get("type") == "delete" for x in a))
    a = handle_update({"update_id": 118, "message": {"chat": group, "from": admin, "text": "/blacklistmode warn"}}, state, dry_run=True)
    a = handle_update({"update_id": 119, "message": {"chat": group, "from": {"id": 333, "first_name": "Bad"}, "text": "spamword again"}}, state, dry_run=True)
    check("warn mode deletes and warns", any(x.get("type") == "delete" for x in a) and any("not allowed" in t for t in texts(a)))
    a = handle_update({"update_id": 120, "message": {"chat": group, "from": admin, "text": "/addsticker", "reply_to_message": {"message_id": 5, "sticker": {"file_unique_id": "STK1"}}}}, state, dry_run=True)
    check("sticker blacklisted", "STK1" in state["group_settings"]["-100123"]["blacklist_stickers"])
    a = handle_update({"update_id": 121, "message": {"chat": group, "from": {"id": 333, "first_name": "Bad"}, "message_id": 50, "sticker": {"file_unique_id": "STK1"}}}, state, dry_run=True)
    check("blacklisted sticker is deleted", any(x.get("type") == "delete" and x.get("message_id") == 50 for x in a))

    # Report forwards to the group admins.
    a = handle_update({"update_id": 122, "message": {"chat": group, "from": {"id": 333, "first_name": "Bad"}, "text": "/report", "reply_to_message": {"message_id": 6, "from": {"id": 222, "first_name": "Spammer"}, "text": "buy cheap stuff"}}}, state, dry_run=True)
    check("report reaches an admin DM", any(x.get("type") == "text" and x.get("chat_id") == 111 and "Report in" in x.get("text", "") for x in a))
    check("report confirms in the group", any("Reported to the group admins" in t for t in texts(a)))

    # Settings panel: open it, toggle anti-link off via the button.
    a = handle_update({"update_id": 123, "message": {"chat": group, "from": admin, "text": "/settings"}}, state, dry_run=True)
    check("settings panel has buttons", any(x.get("keyboard") and "Anti-link" in str(x.get("keyboard")) for x in a))
    a = handle_update({"update_id": 124, "callback_query": {"id": "cbs1", "data": "set:-100123:antilink", "from": admin, "message": {"chat": group, "message_id": 88}}}, state, dry_run=True)
    check("settings button toggles anti-link off", state["group_settings"]["-100123"]["antilink"] is False and any(x.get("type") == "edit" and "Anti-link: off" in x.get("text", "") for x in a))

    # Connect from DM: /ban works from the private chat, then disconnect.
    a = handle_update({"update_id": 125, "message": {"chat": {"id": 111, "type": "private"}, "from": admin, "text": "/connect -100123"}}, state, dry_run=True)
    check("connect stores the group", state.get("connections", {}).get("111") == -100123)
    a = handle_update({"update_id": 126, "message": {"chat": {"id": 111, "type": "private"}, "from": admin, "text": "/ban 222"}}, state, dry_run=True)
    check("ban from DM targets the connected group", any(x.get("type") == "ban" and x.get("chat_id") == -100123 and x.get("user_id") == 222 for x in a))
    a = handle_update({"update_id": 127, "message": {"chat": {"id": 111, "type": "private"}, "from": admin, "text": "/disconnect"}}, state, dry_run=True)
    check("disconnect clears the connection", "111" not in state.get("connections", {}))

    # Promote, demote, purge.
    a = handle_update({"update_id": 128, "message": {"chat": group, "from": admin, "text": "/promote", "reply_to_message": {"message_id": 7, "from": {"id": 222, "first_name": "Spammer"}}}}, state, dry_run=True)
    check("promote action", any(x.get("type") == "promote" and x.get("promote") is True and x.get("user_id") == 222 for x in a))
    a = handle_update({"update_id": 129, "message": {"chat": group, "from": admin, "text": "/demote", "reply_to_message": {"message_id": 7, "from": {"id": 222, "first_name": "Spammer"}}}}, state, dry_run=True)
    check("demote action", any(x.get("type") == "promote" and x.get("promote") is False and x.get("user_id") == 222 for x in a))
    a = handle_update({"update_id": 130, "message": {"chat": group, "from": admin, "text": "/purge", "message_id": 105, "reply_to_message": {"message_id": 100}}}, state, dry_run=True)
    check("purge deletes 6 messages", len([x for x in a if x.get("type") == "delete"]) == 6)
    print(f"phase 1 checks: {len(fails)} failures")
    if fails:
        print("FAILED: " + "; ".join(fails))
        return 1

    # Phase 2 (engagement: XP, streaks, tournaments, digest) tests.
    fails = []
    def check2(label, cond):
        print(("PASS " if cond else "FAIL ") + label)
        if not cond:
            fails.append(label)
    player = {"id": 777, "first_name": "Player"}
    dm = {"id": 777, "type": "private"}

    # Message XP: +2 once, then the 60s cooldown blocks the second message.
    a = handle_update({"update_id": 201, "message": {"chat": dm, "from": player, "text": "hello"}}, state, dry_run=True)
    check2("first message gives +2 XP", state.get("xp", {}).get("777", {}).get("xp") == 2)
    a = handle_update({"update_id": 202, "message": {"chat": dm, "from": player, "text": "hello again"}}, state, dry_run=True)
    check2("second message inside 60s gives no more XP", state.get("xp", {}).get("777", {}).get("xp") == 2)

    # Check-in: +20 XP and a 1-day streak; twice in one day is refused.
    a = handle_update({"update_id": 203, "message": {"chat": dm, "from": player, "text": "/checkin"}}, state, dry_run=True)
    check2("check-in gives +20 XP", state.get("xp", {}).get("777", {}).get("xp") == 22)
    check2("check-in starts a 1-day streak", state.get("streaks", {}).get("777", {}).get("count") == 1)
    a = handle_update({"update_id": 204, "message": {"chat": dm, "from": player, "text": "/checkin"}}, state, dry_run=True)
    check2("second check-in today refused", any("Already checked in" in t for t in texts(a)) and state.get("xp", {}).get("777", {}).get("xp") == 22)

    # Rank and leaderboard show the player.
    a = handle_update({"update_id": 205, "message": {"chat": dm, "from": player, "text": "/rank"}}, state, dry_run=True)
    check2("rank shows Level 1 and 22 XP", any("Level 1" in t and "22 XP" in t for t in texts(a)))
    a = handle_update({"update_id": 206, "message": {"chat": dm, "from": player, "text": "/leaderboard"}}, state, dry_run=True)
    check2("leaderboard lists the player", any("Player" in t for t in texts(a)))

    # Quiz correct answer: +10 XP.
    state.setdefault("quiz_polls", {})["qp1"] = {"chat_id": 777, "correct": 0, "options": ["A", "B"], "explain": "test"}
    a = handle_update({"update_id": 207, "poll_answer": {"poll_id": "qp1", "user": player, "option_ids": [0]}}, state, dry_run=True)
    check2("quiz correct gives +10 XP", state.get("xp", {}).get("777", {}).get("xp") == 32 and any("+10 XP" in t for t in texts(a)))

    # Guess win (+15) then scramble win (+10) crosses 50 XP: level 2.
    state.setdefault("guess_games", {})["777"] = {"number": 42, "attempts": 0}
    a = handle_update({"update_id": 208, "message": {"chat": dm, "from": player, "text": "/guess 42"}}, state, dry_run=True)
    check2("guess win gives +15 XP", state.get("xp", {}).get("777", {}).get("xp") == 47)
    state.setdefault("scramble_games", {})["777"] = {"word": "kind", "scrambled": "dnik"}
    a = handle_update({"update_id": 209, "message": {"chat": dm, "from": player, "text": "kind"}}, state, dry_run=True)
    check2("scramble win levels up to Level 2", state.get("xp", {}).get("777", {}).get("xp") == 57 and any("LEVEL UP! You are now Level 2" in t for t in texts(a)))

    # Tournament: 5 rounds, Bad (333) wins 3-2, gets +50 XP, tournament clears.
    a = handle_update({"update_id": 210, "message": {"chat": group, "from": admin, "text": "/tournament"}}, state, dry_run=True)
    check2("tournament starts with a quiz question", any(x.get("type") == "quiz" and x.get("tournament") for x in a))
    t = state.get("tournaments", {}).get("-100123", {})
    check2("tournament state created", t.get("round") == 1 and t.get("total") == 5)
    bad = {"id": 333, "first_name": "Bad"}
    spammer = {"id": 222, "first_name": "Spammer"}
    rounds = [(211, "tp1", bad), (212, "tp2", spammer), (213, "tp3", bad), (214, "tp4", spammer), (215, "tp5", bad)]
    for uid_, poll, who in rounds:
        state.setdefault("quiz_polls", {})[poll] = {"chat_id": -100123, "correct": 0, "options": ["A", "B"], "explain": "t"}
        state.setdefault("tournament_polls", {})[poll] = -100123
        a = handle_update({"update_id": uid_, "poll_answer": {"poll_id": poll, "user": who, "option_ids": [0]}}, state, dry_run=True)
    check2("tournament ends after 5 rounds", "-100123" not in state.get("tournaments", {}))
    check2("tournament winner announced with +50 XP", any("Tournament over!" in t and "Winner: Bad" in t for t in texts(a)))
    check2("winner got the +50 XP bonus", state.get("xp", {}).get("333", {}).get("xp", 0) >= 80)
    check2("tournament polls cleared", not state.get("tournament_polls", {}))

    # Standings mid-tournament, and early end with no scores.
    a = handle_update({"update_id": 216, "message": {"chat": group, "from": admin, "text": "/tournament"}}, state, dry_run=True)
    state.setdefault("quiz_polls", {})["tp6"] = {"chat_id": -100123, "correct": 0, "options": ["A", "B"], "explain": "t"}
    state.setdefault("tournament_polls", {})["tp6"] = -100123
    a = handle_update({"update_id": 217, "poll_answer": {"poll_id": "tp6", "user": bad, "option_ids": [0]}}, state, dry_run=True)
    a = handle_update({"update_id": 218, "message": {"chat": group, "from": admin, "text": "/tournament"}}, state, dry_run=True)
    check2("standings show the leader", any("Bad: 1 point(s)" in t for t in texts(a)))
    a = handle_update({"update_id": 219, "message": {"chat": group, "from": admin, "text": "/endtournament"}}, state, dry_run=True)
    check2("early end announces the winner", any("Tournament ended early!" in t and "Winner: Bad" in t for t in texts(a)))

    # Digest and updated mystats.
    a = handle_update({"update_id": 220, "message": {"chat": dm, "from": player, "text": "/digest"}}, state, dry_run=True)
    check2("digest shows quote, level and streak", any("digest" in t and "Level 2" in t and "Streak: 1" in t for t in texts(a)))
    a = handle_update({"update_id": 221, "message": {"chat": dm, "from": player, "text": "/mystats"}}, state, dry_run=True)
    check2("mystats shows XP and streak", any("Level 2" in t and "57 XP" in t and "Streak: 1" in t for t in texts(a)))
    print(f"phase 2 checks: {len(fails)} failures")
    if fails:
        print("FAILED: " + "; ".join(fails))
        return 1

    # Phase 3 (smart & safe: real AI chat, AI moderation, log channel, backup) tests.
    # ai_chat is canned in dry-run ("[test AI reply]"), no network is used.
    fails = []
    def check3(label, cond):
        print(("PASS " if cond else "FAIL ") + label)
        if not cond:
            fails.append(label)

    # /ask: known question answered locally, unknown question answered by AI.
    a = handle_update({"update_id": 301, "message": {"chat": dm, "from": player, "text": "/ask what is the capital of Cambodia?"}}, state, dry_run=True)
    check3("ask known question answered locally", any("Phnom Penh" in t for t in texts(a)))
    a = handle_update({"update_id": 302, "message": {"chat": dm, "from": player, "text": "/ask what is the meaning of life?"}}, state, dry_run=True)
    check3("ask unknown question answered by AI", any(t == "[test AI reply]" for t in texts(a)))
    a = handle_update({"update_id": 303, "message": {"chat": dm, "from": player, "text": "what is the meaning of life?"}}, state, dry_run=True)
    check3("plain question answered by AI", any(t == "[test AI reply]" for t in texts(a)))

    # /check: spam and harmless verdicts.
    a = handle_update({"update_id": 304, "message": {"chat": group, "from": admin, "text": "/check", "reply_to_message": {"message_id": 5, "from": {"id": 666, "first_name": "Spammer2"}, "text": "buy cheap spam now https://x.com"}}}, state, dry_run=True)
    check3("check flags a spam message", any("AI verdict: spam" in t for t in texts(a)))
    a = handle_update({"update_id": 305, "message": {"chat": group, "from": admin, "text": "/check", "reply_to_message": {"message_id": 6, "from": {"id": 333, "first_name": "Bad"}, "text": "hello everyone"}}}, state, dry_run=True)
    check3("check clears a harmless message", any("AI verdict: harmless" in t for t in texts(a)))
    a = handle_update({"update_id": 306, "message": {"chat": group, "from": admin, "text": "/check"}}, state, dry_run=True)
    check3("check without a reply explains itself", any("Reply to a message" in t for t in texts(a)))

    # /aimod: on, spam link deleted; clean link allowed.
    a = handle_update({"update_id": 307, "message": {"chat": group, "from": admin, "text": "/aimod on"}}, state, dry_run=True)
    check3("aimod turns on", state["group_settings"]["-100123"].get("aimod") is True)
    spammer2 = {"id": 666, "first_name": "Spammer2"}
    a = handle_update({"update_id": 308, "message": {"chat": group, "from": spammer2, "text": "check out this spam deal https://spam.example.com", "message_id": 60}}, state, dry_run=True)
    check3("aimod deletes a spam link", any(x.get("type") == "delete" and x.get("message_id") == 60 for x in a) and any("AI moderation removed" in t for t in texts(a)))
    a = handle_update({"update_id": 309, "message": {"chat": group, "from": spammer2, "text": "my blog https://example.com", "message_id": 61}}, state, dry_run=True)
    check3("aimod allows a clean link", not any(x.get("type") == "delete" for x in a))
    a = handle_update({"update_id": 310, "message": {"chat": group, "from": admin, "text": "/aimod off"}}, state, dry_run=True)
    check3("aimod turns off", state["group_settings"]["-100123"].get("aimod") is False)

    # /setlog: set it, a ban is logged there, then clear it.
    a = handle_update({"update_id": 311, "message": {"chat": group, "from": admin, "text": "/setlog -100999"}}, state, dry_run=True)
    check3("log channel set", state.get("log_channels", {}).get("-100123") == -100999)
    a = handle_update({"update_id": 312, "message": {"chat": group, "from": admin, "text": "/ban 222"}}, state, dry_run=True)
    check3("ban is logged to the log channel", any(x.get("type") == "text" and x.get("chat_id") == -100999 and "was banned" in x.get("text", "") for x in a))
    a = handle_update({"update_id": 313, "message": {"chat": group, "from": admin, "text": "/setlog off"}}, state, dry_run=True)
    check3("log channel cleared", "-100123" not in state.get("log_channels", {}))

    # /backup and /restore round-trip.
    a = handle_update({"update_id": 314, "message": {"chat": group, "from": admin, "text": "/backup"}}, state, dry_run=True)
    check3("backup contains the group settings", any("blacklist_words" in t and "aimod" in t for t in texts(a)))
    a = handle_update({"update_id": 315, "message": {"chat": group, "from": admin, "text": '/restore {"antilink": true, "aimod": true}'}}, state, dry_run=True)
    check3("restore applies the settings", state["group_settings"]["-100123"].get("antilink") is True and state["group_settings"]["-100123"].get("aimod") is True)
    check3("restore confirms what was restored", any("Restored 2 setting(s)" in t for t in texts(a)))
    a = handle_update({"update_id": 316, "message": {"chat": group, "from": admin, "text": "/restore not json at all"}}, state, dry_run=True)
    check3("restore rejects bad JSON", any("doesn't look like a backup" in t for t in texts(a)))
    print(f"phase 3 checks: {len(fails)} failures")
    if fails:
        print("FAILED: " + "; ".join(fails))
        return 1

    # Phase 3b (Khmer language switch + AI fallback for plain chat) tests.
    fails = []
    def check3b(label, cond):
        print(("PASS " if cond else "FAIL ") + label)
        if not cond:
            fails.append(label)
    a = handle_update({"update_id": 401, "message": {"chat": dm, "from": player, "text": "Reply me as Khmer language"}}, state, dry_run=True)
    check3b("khmer request switches language", state.get("users", {}).get("777", {}).get("lang") == "kh")
    check3b("khmer request answered in Khmer", any("ខ្មែរ" in t for t in texts(a)))
    a = handle_update({"update_id": 402, "message": {"chat": dm, "from": player, "text": "tell me something nice"}}, state, dry_run=True)
    check3b("plain chat answered by AI in Khmer", any(t == "[test AI reply in Khmer]" for t in texts(a)))
    a = handle_update({"update_id": 403, "message": {"chat": dm, "from": player, "text": "Reply in English please"}}, state, dry_run=True)
    check3b("english request switches back", state.get("users", {}).get("777", {}).get("lang") == "en")
    a = handle_update({"update_id": 404, "message": {"chat": dm, "from": player, "text": "tell me something nice"}}, state, dry_run=True)
    check3b("plain chat answered by AI in English", any(t == "[test AI reply]" for t in texts(a)))
    a = handle_update({"update_id": 405, "message": {"chat": dm, "from": player, "text": "Can you write Khmer"}}, state, dry_run=True)
    check3b("can you write khmer switches language", state.get("users", {}).get("777", {}).get("lang") == "kh")
    print(f"phase 3b checks: {len(fails)} failures")
    if fails:
        print("FAILED: " + "; ".join(fails))
        return 1

    # Phase 4 (growth & Premium: referrals, Stars, double XP) tests.
    fails = []
    def check4(label, cond):
        print(("PASS " if cond else "FAIL ") + label)
        if not cond:
            fails.append(label)
    newbie = {"id": 888, "first_name": "Newbie"}
    dm888 = {"id": 888, "type": "private"}

    # /referral issues a link with a DN code.
    a = handle_update({"update_id": 501, "message": {"chat": dm, "from": player, "text": "/referral"}}, state, dry_run=True)
    code = state.get("referrals", {}).get("777", {}).get("code", "")
    check4("referral issues a DN code", code.startswith("DN") and len(code) == 8)
    check4("referral link contains the code", any(f"start=ref_{code}" in t for t in texts(a)))

    # New user joins through the link: referrer +50, new user +20, counted once.
    a = handle_update({"update_id": 502, "message": {"chat": dm888, "from": newbie, "text": f"/start ref_{code}"}}, state, dry_run=True)
    check4("referral recorded", state.get("referred_by", {}).get("888") == "777")
    check4("referrer count is 1", state.get("referrals", {}).get("777", {}).get("count") == 1)
    check4("referrer got +50 XP", state.get("xp", {}).get("777", {}).get("xp") == 107)
    check4("new user got +20 XP", state.get("xp", {}).get("888", {}).get("xp") == 22)  # +20 referral, +2 for the /start message itself
    check4("welcome mentions the invite", any("You joined through" in t for t in texts(a)))
    check4("referrer is notified", any(x.get("chat_id") == 777 and "joined with your invite" in x.get("text", "") for x in a))
    a = handle_update({"update_id": 503, "message": {"chat": dm888, "from": newbie, "text": f"/start ref_{code}"}}, state, dry_run=True)
    check4("second join through the same link gives nothing", state.get("referrals", {}).get("777", {}).get("count") == 1 and state.get("xp", {}).get("888", {}).get("xp") == 22)
    a = handle_update({"update_id": 504, "message": {"chat": dm, "from": player, "text": f"/start ref_{code}"}}, state, dry_run=True)
    check4("self-referral gives nothing", state.get("referrals", {}).get("777", {}).get("count") == 1)
    a = handle_update({"update_id": 505, "message": {"chat": dm, "from": player, "text": "/topreferrers"}}, state, dry_run=True)
    check4("top referrers lists the inviter", any("Player" in t and "1 friend" in t for t in texts(a)))

    # /premium sends a Stars invoice; pre-checkout is approved.
    a = handle_update({"update_id": 506, "message": {"chat": dm, "from": player, "text": "/premium"}}, state, dry_run=True)
    check4("premium sends a Stars invoice", any(x.get("type") == "invoice" and x.get("currency") == "XTR" and x.get("prices", [{}])[0].get("amount") == 150 and x.get("payload") == "premium_777" for x in a))
    a = handle_update({"update_id": 507, "pre_checkout_query": {"id": "pcq1", "from": player, "currency": "XTR", "total_amount": 150, "invoice_payload": "premium_777"}}, state, dry_run=True)
    check4("pre-checkout approved", any(x.get("type") == "precheckout" and x.get("query_id") == "pcq1" and x.get("ok") is True for x in a))

    # Successful payment unlocks Premium with a flat +50 XP bonus.
    a = handle_update({"update_id": 508, "message": {"chat": dm, "from": player, "successful_payment": {"invoice_payload": "premium_777", "total_amount": 150, "currency": "XTR"}}}, state, dry_run=True)
    check4("payment unlocks Premium", "777" in state.get("premium", {}))
    check4("purchase bonus is a flat +50 XP", state.get("xp", {}).get("777", {}).get("xp") == 157)
    check4("welcome to Premium sent", any("Welcome to Premium" in t for t in texts(a)))

    # Premium doubles XP: a correct quiz answer gives +20 instead of +10.
    state.setdefault("quiz_polls", {})["qp2"] = {"chat_id": 777, "correct": 0, "options": ["A", "B"], "explain": "t"}
    a = handle_update({"update_id": 509, "poll_answer": {"poll_id": "qp2", "user": player, "option_ids": [0]}}, state, dry_run=True)
    check4("premium quiz win gives +20 XP", state.get("xp", {}).get("777", {}).get("xp") == 177 and any("+20 XP" in t for t in texts(a)))
    a = handle_update({"update_id": 510, "message": {"chat": dm, "from": player, "text": "/rank"}}, state, dry_run=True)
    check4("rank shows the Premium badge", any("Premium" in t for t in texts(a)))
    a = handle_update({"update_id": 511, "message": {"chat": dm, "from": player, "text": "/leaderboard"}}, state, dry_run=True)
    check4("leaderboard shows the Premium star", any("Player ⭐" in t for t in texts(a)))
    print(f"phase 4 checks: {len(fails)} failures")
    if fails:
        print("FAILED: " + "; ".join(fails))
        return 1

    # Phase 5 (broadcast + scheduled posts) tests.
    fails = []
    def check5(label, cond):
        print(("PASS " if cond else "FAIL ") + label)
        if not cond:
            fails.append(label)
    owner = {"id": 5039926430, "first_name": "Owner"}
    dm_owner = {"id": 5039926430, "type": "private"}

    a = handle_update({"update_id": 601, "message": {"chat": dm, "from": player, "text": "/broadcast hello everyone"}}, state, dry_run=True)
    check5("broadcast refused for non-owner", any("Only the owner" in t for t in texts(a)))
    a = handle_update({"update_id": 602, "message": {"chat": dm_owner, "from": owner, "text": "/broadcast hello everyone"}}, state, dry_run=True)
    check5("broadcast with no subscribers says so", any("No subscribers yet" in t for t in texts(a)))
    state.setdefault("subscribers", []).extend([777, 888])
    a = handle_update({"update_id": 603, "message": {"chat": dm_owner, "from": owner, "text": "/broadcast hello everyone"}}, state, dry_run=True)
    check5("broadcast reaches every subscriber", sum(1 for x in a if x.get("type") == "text" and "hello everyone" in x.get("text", "")) == 2)
    check5("broadcast confirms the count", any("2 subscriber" in t for t in texts(a)))

    a = handle_update({"update_id": 604, "message": {"chat": dm_owner, "from": owner, "text": "/schedulepost 23:59 Test channel post"}}, state, dry_run=True)
    check5("schedulepost queues a post", len(state.get("scheduled_posts", [])) == 1)
    pid = state["scheduled_posts"][0]["id"]
    check5("schedulepost confirms with the ID", any(pid in t for t in texts(a)))
    a = handle_update({"update_id": 605, "message": {"chat": dm_owner, "from": owner, "text": "/scheduled"}}, state, dry_run=True)
    check5("scheduled lists the post", any(pid in t and "Test channel post" in t for t in texts(a)))
    state["scheduled_posts"][0]["at"] = int(time.time()) - 10
    due = check_scheduled_posts(state)
    check5("due post is sent to the channel", len(due) == 1 and due[0].get("chat_id") == state.get("channel_id") and "Test channel post" in due[0].get("text", ""))
    check5("sent post is removed from the queue", len(state.get("scheduled_posts", [])) == 0)
    a = handle_update({"update_id": 606, "message": {"chat": dm_owner, "from": owner, "text": "/schedulepost tomorrow 09:00 Another post"}}, state, dry_run=True)
    pid2 = state["scheduled_posts"][0]["id"]
    a = handle_update({"update_id": 607, "message": {"chat": dm_owner, "from": owner, "text": f"/cancelschedule {pid2}"}}, state, dry_run=True)
    check5("cancelschedule removes the post", len(state.get("scheduled_posts", [])) == 0 and any("Cancelled" in t for t in texts(a)))
    a = handle_update({"update_id": 608, "message": {"chat": dm_owner, "from": owner, "text": "/schedulepost badtime oops"}}, state, dry_run=True)
    check5("bad schedule time is rejected", any("Use: /schedulepost" in t for t in texts(a)))
    print(f"phase 5 checks: {len(fails)} failures")
    if fails:
        print("FAILED: " + "; ".join(fails))
        return 1

    # Phase 6 (growth engine: giveaways, share, referral boost) tests.
    fails = []
    def check6(label, cond):
        print(("PASS " if cond else "FAIL ") + label)
        if not cond:
            fails.append(label)
    group = {"id": -100123, "type": "supergroup"}
    admin = {"id": 111, "first_name": "Admin"}
    spammer = {"id": 222, "first_name": "Spammer"}
    bad = {"id": 333, "first_name": "Bad"}

    # /share sends the promo kit with the referral link.
    a = handle_update({"update_id": 701, "message": {"chat": dm, "from": player, "text": "/share"}}, state, dry_run=True)
    check6("share sends the promo kit", any("@dailynewsreporter" in t and "start=ref_DN" in t for t in texts(a)))

    # /boost is owner-only; on doubles referral rewards.
    a = handle_update({"update_id": 702, "message": {"chat": dm, "from": player, "text": "/boost on"}}, state, dry_run=True)
    check6("boost refused for non-owner", any("Only the owner" in t for t in texts(a)))
    a = handle_update({"update_id": 703, "message": {"chat": dm_owner, "from": owner, "text": "/boost on"}}, state, dry_run=True)
    check6("boost turns on", state.get("referral_boost") is True)
    a = handle_update({"update_id": 704, "message": {"chat": dm888, "from": newbie, "text": "/referral"}}, state, dry_run=True)
    check6("boost doubles referral rewards", any("+100 XP" in t and "+40 XP" in t for t in texts(a)))
    # New user joins through 777's link during the boost: referrer +200 (premium doubles the 100), new user +40.
    code777 = state.get("referrals", {}).get("777", {}).get("code", "")
    rookie = {"id": 999, "first_name": "Rookie"}
    dm999 = {"id": 999, "type": "private"}
    a = handle_update({"update_id": 705, "message": {"chat": dm999, "from": rookie, "text": f"/start ref_{code777}"}}, state, dry_run=True)
    check6("boost referral gives referrer +200 XP", state.get("xp", {}).get("777", {}).get("xp") == 377)
    check6("boost referral gives new user +40 XP", state.get("xp", {}).get("999", {}).get("xp") == 42)
    a = handle_update({"update_id": 706, "message": {"chat": dm_owner, "from": owner, "text": "/boost auto"}}, state, dry_run=True)
    check6("boost back to auto", state.get("referral_boost") is None)

    # Giveaway: only admins start; join via button; draw picks the only entrant.
    a = handle_update({"update_id": 707, "message": {"chat": group, "from": spammer, "text": "/giveaway premium"}}, state, dry_run=True)
    check6("giveaway refused for non-admin", any("Only group admins" in t for t in texts(a)))
    a = handle_update({"update_id": 708, "message": {"chat": group, "from": admin, "text": "/giveaway premium"}}, state, dry_run=True)
    check6("giveaway starts with a join button", any(x.get("type") == "text" and x.get("keyboard") and "Join the giveaway" in str(x.get("keyboard")) for x in a))
    a = handle_update({"update_id": 709, "callback_query": {"id": "cb1", "from": spammer, "data": "giveaway_join:-100123", "message": {"chat": group, "message_id": 50}}}, state, dry_run=True)
    check6("joining adds the entrant", state.get("giveaways", {}).get("-100123", {}).get("entries") == [222])
    check6("join toast says you are in", any(x.get("type") == "callback" and "You're in!" in x.get("text", "") for x in a))
    a = handle_update({"update_id": 710, "callback_query": {"id": "cb2", "from": spammer, "data": "giveaway_join:-100123", "message": {"chat": group, "message_id": 50}}}, state, dry_run=True)
    check6("joining twice is refused", any(x.get("type") == "callback" and "already in" in x.get("text", "") for x in a) and state.get("giveaways", {}).get("-100123", {}).get("entries") == [222])
    a = handle_update({"update_id": 711, "message": {"chat": group, "from": admin, "text": "/draw"}}, state, dry_run=True)
    check6("draw picks the winner and grants Premium", "222" in state.get("premium", {}) and any("WINNER" in t and "Spammer" in t for t in texts(a)))
    check6("giveaway is cleared after the draw", "-100123" not in state.get("giveaways", {}))
    # XP giveaway: winner gets the XP.
    handle_update({"update_id": 712, "message": {"chat": group, "from": admin, "text": "/giveaway 100"}}, state, dry_run=True)
    handle_update({"update_id": 713, "callback_query": {"id": "cb3", "from": bad, "data": "giveaway_join:-100123", "message": {"chat": group, "message_id": 51}}}, state, dry_run=True)
    before = state.get("xp", {}).get("333", {}).get("xp", 0)
    a = handle_update({"update_id": 714, "message": {"chat": group, "from": admin, "text": "/draw"}}, state, dry_run=True)
    check6("XP giveaway pays the winner", state.get("xp", {}).get("333", {}).get("xp", 0) == before + 100 and any("+100 XP" in t for t in texts(a)))
    # Cancel flow.
    handle_update({"update_id": 715, "message": {"chat": group, "from": admin, "text": "/giveaway a shoutout"}}, state, dry_run=True)
    a = handle_update({"update_id": 716, "message": {"chat": group, "from": admin, "text": "/endgiveaway"}}, state, dry_run=True)
    check6("endgiveaway cancels the giveaway", "-100123" not in state.get("giveaways", {}) and any("ended" in t for t in texts(a)))
    print(f"phase 6 checks: {len(fails)} failures")
    if fails:
        print("FAILED: " + "; ".join(fails))
        return 1

    # Phase 7 (Rose Power Pack 2: granular locks, tban, id, disable, approve, flood, goodbye, fsub) tests.
    fails = []
    def check7(label, cond):
        print(("PASS " if cond else "FAIL ") + label)
        if not cond:
            fails.append(label)

    # Granular locks: lock stickers, a sticker is deleted; unlock, it passes.
    a = handle_update({"update_id": 801, "message": {"chat": group, "from": admin, "text": "/lock sticker"}}, state, dry_run=True)
    check7("lock sticker sets the lock", state.get("group_settings", {}).get("-100123", {}).get("locks", {}).get("sticker") is True)
    a = handle_update({"update_id": 802, "message": {"chat": group, "from": spammer, "sticker": {"file_unique_id": "abc123"}, "message_id": 60}}, state, dry_run=True)
    check7("locked sticker is deleted", any(x.get("type") == "delete" and x.get("message_id") == 60 for x in a))
    a = handle_update({"update_id": 803, "message": {"chat": group, "from": admin, "text": "/unlock sticker"}}, state, dry_run=True)
    check7("unlock sticker clears the lock", state.get("group_settings", {}).get("-100123", {}).get("locks", {}).get("sticker") is None)
    a = handle_update({"update_id": 804, "message": {"chat": group, "from": spammer, "sticker": {"file_unique_id": "abc123"}, "message_id": 61}}, state, dry_run=True)
    check7("unlocked sticker passes", not any(x.get("type") == "delete" for x in a))
    # Lock a URL: a link message is deleted; /locks lists it.
    handle_update({"update_id": 805, "message": {"chat": group, "from": admin, "text": "/lock url"}}, state, dry_run=True)
    a = handle_update({"update_id": 806, "message": {"chat": group, "from": spammer, "text": "check https://example.com", "message_id": 62}}, state, dry_run=True)
    check7("locked URL is deleted", any(x.get("type") == "delete" and x.get("message_id") == 62 for x in a))
    a = handle_update({"update_id": 807, "message": {"chat": group, "from": admin, "text": "/locks"}}, state, dry_run=True)
    check7("locks lists the locked type", any("url" in t for t in texts(a)))
    a = handle_update({"update_id": 808, "message": {"chat": group, "from": admin, "text": "/locktypes"}}, state, dry_run=True)
    check7("locktypes lists the types", any("sticker" in t and "photo" in t for t in texts(a)))
    # Approved users bypass locks.
    a = handle_update({"update_id": 809, "message": {"chat": group, "from": admin, "text": "/approve 222"}}, state, dry_run=True)
    check7("approve adds the member", 222 in state.get("group_settings", {}).get("-100123", {}).get("approved", []))
    a = handle_update({"update_id": 810, "message": {"chat": group, "from": spammer, "text": "check https://example.com", "message_id": 63}}, state, dry_run=True)
    check7("approved member bypasses the URL lock", not any(x.get("type") == "delete" for x in a))
    a = handle_update({"update_id": 811, "message": {"chat": group, "from": admin, "text": "/unapprove 222"}}, state, dry_run=True)
    check7("unapprove removes the member", 222 not in state.get("group_settings", {}).get("-100123", {}).get("approved", []))
    # Bots lock: a bot joining is banned.
    handle_update({"update_id": 812, "message": {"chat": group, "from": admin, "text": "/lock bots"}}, state, dry_run=True)
    a = handle_update({"update_id": 813, "message": {"chat": group, "new_chat_members": [{"id": 555, "first_name": "SpamBot", "is_bot": True}]}}, state, dry_run=True)
    check7("locked bots are banned on join", any(x.get("type") == "ban" and x.get("user_id") == 555 for x in a))
    # /tban: temp ban recorded, expired bans lifted on the next update.
    a = handle_update({"update_id": 814, "message": {"chat": group, "from": admin, "text": "/tban 1h", "reply_to_message": {"from": spammer, "message_id": 64}}}, state, dry_run=True)
    check7("tban records the temp ban", "222" in state.get("temp_bans", {}).get("-100123", {}) and any(x.get("type") == "ban" and x.get("until") for x in a))
    state["temp_bans"]["-100123"]["222"] = int(time.time()) - 5
    a = handle_update({"update_id": 815, "message": {"chat": group, "from": admin, "text": "/locks"}}, state, dry_run=True)
    check7("expired temp ban is lifted", any(x.get("type") == "unban" and x.get("user_id") == 222 for x in a) and "222" not in state.get("temp_bans", {}).get("-100123", {}))
    # /id, /disable, /setflood, /setgoodbye, /fsub.
    a = handle_update({"update_id": 816, "message": {"chat": group, "from": spammer, "text": "/id"}}, state, dry_run=True)
    check7("id shows user and group IDs", any("222" in t and "-100123" in t for t in texts(a)))
    a = handle_update({"update_id": 817, "message": {"chat": group, "from": admin, "text": "/disable /quote"}}, state, dry_run=True)
    check7("disable turns a command off", "/quote" in state.get("group_settings", {}).get("-100123", {}).get("disabled", []))
    a = handle_update({"update_id": 818, "message": {"chat": group, "from": spammer, "text": "/quote", "message_id": 65}}, state, dry_run=True)
    check7("disabled command is deleted", any(x.get("type") == "delete" and x.get("message_id") == 65 for x in a))
    a = handle_update({"update_id": 819, "message": {"chat": group, "from": admin, "text": "/enable /quote"}}, state, dry_run=True)
    check7("enable turns it back on", "/quote" not in state.get("group_settings", {}).get("-100123", {}).get("disabled", []))
    a = handle_update({"update_id": 820, "message": {"chat": group, "from": admin, "text": "/setflood 8"}}, state, dry_run=True)
    check7("setflood sets the limit", state.get("group_settings", {}).get("-100123", {}).get("flood_limit") == 8)
    a = handle_update({"update_id": 821, "message": {"chat": group, "from": admin, "text": "/setgoodbye Bye {name}, take care!"}}, state, dry_run=True)
    check7("setgoodbye saves the message", state.get("group_settings", {}).get("-100123", {}).get("goodbye") == "Bye {name}, take care!")
    a = handle_update({"update_id": 822, "message": {"chat": group, "left_chat_member": {"id": 222, "first_name": "Spammer"}}}, state, dry_run=True)
    check7("goodbye is sent on leave", any("Bye Spammer" in t for t in texts(a)))
    a = handle_update({"update_id": 823, "message": {"chat": group, "from": admin, "text": "/fsub @DailyNewsReporter"}}, state, dry_run=True)
    check7("fsub sets the channel", state.get("group_settings", {}).get("-100123", {}).get("fsub") == "@DailyNewsReporter")
    a = handle_update({"update_id": 824, "message": {"chat": group, "from": spammer, "text": "hello", "message_id": 66}}, state, dry_run=True)
    check7("fsub check runs for non-members", any(x.get("type") == "fsub_check" and x.get("channel") == "@DailyNewsReporter" for x in a))
    a = handle_update({"update_id": 825, "message": {"chat": group, "from": admin, "text": "/fsub off"}}, state, dry_run=True)
    check7("fsub turns off", state.get("group_settings", {}).get("-100123", {}).get("fsub") == "")
    # Non-admins get the plain "only admins" reply (the bot itself is admin in dry-run, so no promote hint).
    a = handle_update({"update_id": 826, "message": {"chat": group, "from": spammer, "text": "/lock sticker"}}, state, dry_run=True)
    check7("non-admin gets the admin-only reply", any("Only group admins" in t for t in texts(a)))
    # Anonymous admins post as the group itself (sender_chat == group, no "from") — they ARE admins.
    anon_lock = {"chat": group, "sender_chat": {"id": -100123}, "text": "/lock photo"}
    a = handle_update({"update_id": 827, "message": anon_lock}, state, dry_run=True)
    check7("anonymous admin can lock", state.get("group_settings", {}).get("-100123", {}).get("locks", {}).get("photo") is True)
    a = handle_update({"update_id": 828, "message": {"chat": group, "sender_chat": {"id": -100123}, "sticker": {"file_unique_id": "xyz"}, "message_id": 67}}, state, dry_run=True)
    check7("anonymous admin bypasses the sticker lock", not any(x.get("type") == "delete" for x in a))
    # A message sent on behalf of some OTHER chat is not an admin.
    a = handle_update({"update_id": 829, "message": {"chat": group, "sender_chat": {"id": -100999}, "text": "/lock photo"}}, state, dry_run=True)
    check7("other-chat sender is not an admin", any("Only group admins" in t for t in texts(a)))
    # Posting as the group's linked channel counts as admin (live getChat branch, faked here).
    _real_api = api_call
    def _fake_api(method, payload=None):
        if method == "getChat":
            return {"ok": True, "result": {"linked_chat_id": -100999}}
        return {"ok": False, "description": "fake"}
    globals()["api_call"] = _fake_api
    try:
        check7("linked channel counts as admin", is_admin(-100123, None, dry_run=False, sender_chat_id=-100999) is True)
        check7("unlinked channel does not", is_admin(-100123, None, dry_run=False, sender_chat_id=-100888) is False)
    finally:
        globals()["api_call"] = _real_api
    print(f"phase 7 checks: {len(fails)} failures")
    if fails:
        print("FAILED: " + "; ".join(fails))
        return 1
    # Image generation test
    path = make_quote_image("Test quote for the super bot.")
    print(f"quote image created: {path} size={os.path.getsize(path)} bytes")
    os.remove(path)
    # Khmer image test: must use Noto Sans Khmer, not boxes (DejaVu has no Khmer glyphs)
    khmer_path = make_quote_image(KHMER_QUOTES[0])
    print(f"khmer image created: {khmer_path} size={os.path.getsize(khmer_path)} bytes")
    os.remove(khmer_path)
    print(f"channel: {state.get('channel_id')}, reminders: {len(state.get('reminders', []))}, subscribers: {state['subscribers']}")
    print("TEST OK - all steps produced actions")
    return 0


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--daily", action="store_true")
    parser.add_argument("--evening", action="store_true")
    parser.add_argument("--design", action="store_true")
    parser.add_argument("--setup-channel", dest="setup_channel", default="")
    parser.add_argument("--setup-bot", dest="setup_bot", default="")
    parser.add_argument("--story", action="store_true")
    parser.add_argument("--setup-menu", dest="setup_menu", action="store_true")
    parser.add_argument("--post-news", dest="post_news", default="")
    parser.add_argument("--test", action="store_true")
    args = parser.parse_args()
    if args.test:
        return run_tests()
    if args.daily:
        return send_daily()
    if args.evening:
        return send_evening()
    if args.design:
        return 0 if send_design() else 1
    if args.setup_channel:
        return 0 if set_channel_info(args.setup_channel) else 1
    if args.setup_bot:
        return 0 if set_bot_profile(args.setup_bot) else 1
    if args.story:
        return 0 if send_story() else 1
    if args.setup_menu:
        return 0 if set_bot_commands() else 1
    if args.post_news:
        parts = [p.strip() for p in args.post_news.split("|")]
        if len(parts) < 2:
            print("usage: --post-news 'Title | Summary | Source'", flush=True)
            return 2
        return 0 if send_news(parts[0], parts[1], parts[2] if len(parts) > 2 else "") else 1
    poll_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
