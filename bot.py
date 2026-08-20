# -*- coding: utf-8 -*-
"""
alsfer_bot — بوت Giant Chat المطور
• تشغيل الموسيقى من يوتيوب (بصمة صوتية)
• نظام ألعاب متكامل مع صور PNG
• نظام نقاط، توب، زواج، ومضاربة
• نظام إدارة (ماستر، طرد، حظر، ردود مخصصة)
"""

import asyncio
import json
import logging
import re
import os
import sys
import time
import uuid
import random
import tempfile
import shutil
import subprocess
from pathlib import Path
from urllib.parse import quote
from datetime import datetime, timezone

import aiohttp
from aiohttp import web
import requests
try:
    from PIL import Image, ImageDraw, ImageFont
    PIL_AVAILABLE = True
except ImportError:
    Image = ImageDraw = ImageFont = None
    PIL_AVAILABLE = False
try:
    import arabic_reshaper
    from bidi.algorithm import get_display
except ImportError:
    arabic_reshaper = None
    get_display = None
try:
    import yt_dlp
except ImportError:
    yt_dlp = None
from supabase import create_client

# ----------------------------- إعداد السجلات -----------------------------
os.makedirs("logs", exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    handlers=[
        logging.FileHandler(os.path.join("logs", "bot.log"), encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger("alsfer")

# ----------------------------- الإعدادات -----------------------------
CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")
POINTS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "points.json")
GIFT_POINTS_LOCK = asyncio.Lock()
REPLIES_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "replies.json")
MASTERS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "masters.json")
BANS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bans.json")
ROOMS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "rooms.json")
MODERATION_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "moderation.json")
WELCOME_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "welcome.json")
PUBLISHED_POSTS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "published_posts.json")
SOCIAL_EVENTS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "social_events.json")
VIP_USERS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "vip_users.json")

os.chdir(os.path.dirname(os.path.abspath(__file__)))

with open(CONFIG_PATH, encoding="utf-8") as f:
    C = json.load(f)

# يمكن تشغيل البوت على Railway بدون وضع أسرار الحساب داخل config.json.
# Environment Variables لها الأولوية على القيم الموجودة في الملف.
for _key, _env in (
    ("supabase_url", "SUPABASE_URL"),
    ("supabase_key", "SUPABASE_KEY"),
    ("username", "GIANT_USERNAME"),
    ("password", "GIANT_PASSWORD"),
    ("owner_username", "OWNER_USERNAME"),
):
    if os.environ.get(_env):
        C[_key] = os.environ[_env]

REQUIRED = ["supabase_url", "supabase_key", "username", "password"]
missing = [k for k in REQUIRED if not str(C.get(k, "")).strip()]
if missing:
    log.error("نقص في إعدادات Giant Chat: %s", ", ".join(missing))
    sys.exit(1)

USERNAME = C["username"].strip()
PASSWORD = C["password"]
OWNER = (C.get("owner_username") or USERNAME).strip().lower()
POLL = max(1.0, float(C.get("poll_seconds", 2)))
SEARCH_URL = C.get("music_search_url") or "https://giant-chat-app.lovable.app/api/public/search-track"
YOUTUBE_COOKIES_PATH = str(C.get("youtube_cookies_path", "youtube_cookies.txt")).strip()
# أسرار cookies يمكن حفظها كمتغيرات Railway، ولا يجب رفعها إلى GitHub.
YOUTUBE_COOKIES_ENV = os.environ.get("YOUTUBE_COOKIES", "").strip()
TIKTOK_COOKIES_ENV = os.environ.get("TIKTOK_COOKIES", "").strip()
SPOTIFY_COOKIES_ENV = os.environ.get("SPOTIFY_COOKIES", "").strip()
YOUTUBE_PO_TOKEN = os.environ.get("YOUTUBE_PO_TOKEN", "").strip()

# ---- نظام حسابات YouTube متعددة: YOUTUBE_ACCOUNTS ----
# الصيغة: email1:cookies_text1 | email2:cookies_text2
# أو بصيغة base64: email1:base64_cookies1 | email2:base64_cookies2
# كل حساب له ملف cookies منفصل، والبوت يبدّل بينها تلقائيًا عند فشل تشغيل الأغاني
YOUTUBE_ACCOUNTS_RAW = os.environ.get("YOUTUBE_ACCOUNTS", "").strip()
_youtube_accounts = []  # list of {email, cookies_path, failures}
_youtube_active_index = 0

if YOUTUBE_ACCOUNTS_RAW:
    for entry in YOUTUBE_ACCOUNTS_RAW.split("|"):
        entry = entry.strip()
        if not entry or ":" not in entry:
            continue
        email, cookies_text = entry.split(":", 1)
        email = email.strip()
        cookies_text = cookies_text.strip()
        # فك تشفير base64 إذا كانت البيانات طويلة
        if cookies_text.startswith("base64:"):
            import base64
            try:
                cookies_text = base64.b64decode(cookies_text[7:]).decode("utf-8", errors="ignore")
            except Exception:
                log.warning("فشل فك base64 لحساب %s", email)
                continue
        cookie_file = _write_cookie_file(cookies_text, f"/tmp/youtube_cookies_{email.replace('@','_').replace('.','_')}.txt")
        if cookie_file:
            _youtube_accounts.append({"email": email, "cookies_path": cookie_file, "failures": 0})
            log.info("تم تحميل حساب YouTube: %s", email)
        else:
            log.warning("ملف cookies غير صالح لحساب %s", email)

def get_active_youtube_cookies():
    """إرجاع مسار cookies للحساب النشط (يبدّل عند الفشل)."""
    if not _youtube_accounts:
        return None
    idx = _youtube_active_index % len(_youtube_accounts)
    return _youtube_accounts[idx]["cookies_path"]

def rotate_youtube_account(failed_email=None):
    """تبديل إلى الحساب التالي عند فشل تشغيل الأغاني."""
    global _youtube_active_index
    if not _youtube_accounts:
        return False
    # زيادة عدد failures للحساب الحالي
    if failed_email:
        for acc in _youtube_accounts:
            if acc["email"] == failed_email:
                acc["failures"] += 1
    # التبديل للحساب التالي (تخطي الحسابات التي فشلت كثيرًا)
    max_failures = max(a["failures"] for a in _youtube_accounts) if _youtube_accounts else 0
    tried = 0
    while tried < len(_youtube_accounts):
        idx = (_youtube_active_index + 1) % len(_youtube_accounts)
        acc = _youtube_accounts[idx]
        # تخطي الحسابات التي فشلت أكثر من 3 مرات
        if acc["failures"] <= 3 or tried == 0:
            _youtube_active_index = idx
            log.info("تبديل حساب YouTube: %s (failures=%d)", acc["email"], acc["failures"])
            return True
        tried += 1
    return False


def _normalize_cookie_text(raw):
    """تنظيف محتوى ملف cookies القادم من متغيرات Railway.

    الأخطاء الشائعة: أسطر مكتوبة كـ \n نصية، مسافات بدل TAB، أو غياب ترويسة
    Netscape. yt-dlp يرفض الملف في كل هذه الحالات ويظهر الخطأ كأنه فشل يوتيوب.
    """
    text = str(raw or "")
    if "\\n" in text and "\n" not in text:
        text = text.replace("\\n", "\n")
    text = text.replace("\\t", "\t").replace("\r\n", "\n").replace("\r", "\n")
    lines = []
    for line in text.split("\n"):
        line = line.rstrip()
        if not line:
            continue
        if line.lstrip().startswith("#"):
            lines.append(line)
            continue
        if "\t" not in line:
            parts = re.split(r"\s{1,}", line.strip())
            if len(parts) >= 7:
                line = "\t".join(parts[:6] + [" ".join(parts[6:])])
        lines.append(line)
    if not lines:
        return ""
    if not lines[0].startswith("# Netscape HTTP Cookie File"):
        lines.insert(0, "# Netscape HTTP Cookie File")
    return "\n".join(lines) + "\n"


def _write_cookie_file(raw, path):
    """كتابة ملف cookies صالح وإرجاع مساره، أو None إذا لم يكن صالحاً."""
    content = _normalize_cookie_text(raw)
    data_lines = [l for l in content.split("\n") if l and not l.startswith("#")]
    if not data_lines:
        return None
    try:
        p = Path(path)
        p.write_text(content, encoding="utf-8")
        return str(p)
    except Exception as _e:
        log.warning("تعذر إنشاء ملف cookies %s: %s", path, _e)
        return None


if YOUTUBE_COOKIES_ENV and not YOUTUBE_ACCOUNTS_RAW:
    _yt_cookie_file = _write_cookie_file(YOUTUBE_COOKIES_ENV, "/tmp/youtube_cookies.txt")
    if _yt_cookie_file:
        YOUTUBE_COOKIES_PATH = _yt_cookie_file
    else:
        log.warning("YOUTUBE_COOKIES موجود لكنه غير صالح بصيغة Netscape؛ تم تجاهله.")
elif YOUTUBE_COOKIES_PATH and os.path.isfile(YOUTUBE_COOKIES_PATH):
    _fixed = _write_cookie_file(Path(YOUTUBE_COOKIES_PATH).read_text(encoding="utf-8", errors="ignore"),
                                "/tmp/youtube_cookies.txt")
    if _fixed:
        YOUTUBE_COOKIES_PATH = _fixed

TIKTOK_COOKIES_PATH = "/tmp/tiktok_cookies.txt"
if TIKTOK_COOKIES_ENV:
    if not _write_cookie_file(TIKTOK_COOKIES_ENV, TIKTOK_COOKIES_PATH):
        log.warning("TIKTOK_COOKIES غير صالح؛ سيعمل TikTok بدون cookies.")


def has_youtube_cookies():
    """إرجاع True إذا وُجدت cookies صالحة (من YOUTUBE_ACCOUNTS أو YOUTUBE_COOKIES)."""
    if _youtube_accounts:
        return any(os.path.isfile(a["cookies_path"]) for a in _youtube_accounts)
    return bool(YOUTUBE_COOKIES_PATH) and os.path.isfile(YOUTUBE_COOKIES_PATH)


def youtube_cookie_status():
    if not has_youtube_cookies():
        return False, "لم يتم العثور على ملف Cookies صالح في Railway (YOUTUBE_COOKIES)."
    try:
        text = Path(YOUTUBE_COOKIES_PATH).read_text(encoding="utf-8", errors="ignore")
        rows = []
        for line in text.splitlines():
            if not line or line.startswith("#"):
                continue
            if len(line.split("\t")) >= 7:
                rows.append(line)
        if not rows:
            return False, "ملف YOUTUBE_COOKIES موجود لكنه لا يحتوي أسطر Netscape صحيحة (7 حقول مفصولة بـ TAB)."
        return True, f"Cookies صالحة شكلياً: {len(rows)} سجل."
    except Exception as e:
        return False, f"تعذر قراءة ملف Cookies: {type(e).__name__}: {e}"


def yt_base_options(source_label="YouTube"):
    """خيارات yt-dlp موحّدة لكل مصادر الصوت.

    مهم: عند استخدام cookies يجب عدم استخدام عميل android/ios لأن يوتيوب
    يتجاهل الجلسة معهما ويعيد «Sign in to confirm you're not a bot».
    """
    options = {
        "quiet": True, "no_warnings": True, "noplaylist": True,
        "socket_timeout": 35, "retries": 5, "fragment_retries": 5,
        "extractor_retries": 4, "file_access_retries": 3,
        "cachedir": False, "geo_bypass": True, "overwrites": True,
        "http_headers": {
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                          "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
        },
    }
    if source_label == "YouTube":
        # YouTube في 2026 يفرض PO Tokens على بعض عملاء GVS.
        # لا نستخدم mweb افتراضياً لأنه أكثر عرضة لـ403 بدون PO Token.
        # يجرّب البوت كل العملاء الممكنة تلقائيًا لتجاوز حظر يوتيوب.
        clients = str(os.environ.get("YOUTUBE_PLAYER_CLIENTS") or C.get("youtube_player_clients", "default,web_embedded,tv,tvos,web,mweb,android_vr")).strip()
        client_list = [x.strip() for x in clients.split(",") if x.strip()]
        if not client_list:
            client_list = ["default", "web_embedded", "tv", "tvos", "web", "mweb", "android_vr"]
        # استخدام cookies من الحساب النشط (YOUTUBE_ACCOUNTS) أو YOUTUBE_COOKIES
        active_cookies = get_active_youtube_cookies() or YOUTUBE_COOKIES_PATH
        if active_cookies and os.path.isfile(active_cookies):
            options["cookiefile"] = active_cookies
        ex = {"youtube": {"player_client": client_list}}
        if YOUTUBE_PO_TOKEN:
            # الصيغة التي يفهمها yt-dlp: client.gvs+TOKEN أو client.player+TOKEN.
            ex["youtube"]["po_token"] = YOUTUBE_PO_TOKEN
        options["extractor_args"] = ex
    elif source_label == "TikTok" and os.path.isfile(TIKTOK_COOKIES_PATH):
        options["cookiefile"] = TIKTOK_COOKIES_PATH
    return options


PIPED_APIS = [x.strip().rstrip("/") for x in C.get("piped_apis", [
    "https://pipedapi.kavin.rocks",
    "https://pipedapi.leptons.xyz",
    "https://piped-api.privacy.com.de",
    "https://pipedapi.adminforge.de",
]) if str(x).strip()]

# Invidious instances — مصادر بديلة لاستخراج صوت YouTube بدون yt-dlp
INVIDIOUS_APIS = [x.strip().rstrip("/") for x in str(C.get("invidious_apis", "https://inv.nadeko.net, https://invidious.nerdvpn.de, https://inv.tux.pizza, https://vid.puffyan.us")).split(",") if x.strip()]

# ذكاء اصطناعي لتحليل أخطاء تشغيل الأغاني
LLM_MODEL = os.environ.get("LLM_MODEL", "gpt-4o-mini")

def has_llm():
    """هل يوجد مفتاح OpenAI API متاح؟"""
    return bool(os.environ.get("OPENAI_API_KEY"))

async def _ai_analyze_music_error(errors, source_label):
    """يحلل أخطاء yt-dlp ويقرر أفضل استراتيجية باستخدام LLM (ذكاء اصطناعي)."""
    if not has_llm():
        return None
    try:
        from openai import AsyncOpenAI
        api_key = os.environ.get("OPENAI_API_KEY")
        api_base = os.environ.get("OPENAI_API_BASE", "https://api.openai.com/v1")
        client = AsyncOpenAI(api_key=api_key, base_url=api_base)
        prompt = (
            f"You are a YouTube audio extraction expert. Current errors: "
            f"{' | '.join(errors[-5:])}. Source: {source_label}. "
            f"YouTube is blocking with 'Sign in to confirm' and 'The page needs to be reloaded'. "
            f"What is the BEST next strategy? Options: try_invidious, try_spotify, try_different_clients, try_android_client, try_cobalt. "
            f"Return ONLY the strategy name."
        )
        resp = await client.chat.completions.create(
            model=LLM_MODEL,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=20,
            temperature=0.3,
        )
        strategy = resp.choices[0].message.content.strip().lower()
        log.info("AI strategy decision: %s", strategy)
        return strategy
    except Exception as e:
        log.warning("AI analyzer failed: %s", e)
        return None
MUSIC_MAX_DURATION = int(C.get("music_max_duration_seconds", 900))

# رابط عام لملفات الصوت التي سيشغلها تطبيق Giant Chat.
# على Railway يفضل استخدام RAILWAY_PUBLIC_DOMAIN تلقائياً، أو ضع PUBLIC_BASE_URL يدوياً.
PUBLIC_BASE_URL = str(
    os.environ.get("PUBLIC_BASE_URL")
    or C.get("music_public_base_url")
    or (
        f"https://{os.environ.get('RAILWAY_PUBLIC_DOMAIN').strip('/')}"
        if os.environ.get("RAILWAY_PUBLIC_DOMAIN") else ""
    )
).rstrip("/")
MEDIA_PATH = "/media"
MEDIA_SERVER_PORT = int(os.environ.get("PORT", "8080"))

def create_supabase_client(url, key):
    """إنشاء عميل يدعم مفاتيح Supabase الجديدة sb_publishable_.

    supabase-py 2.15 يتحقق محليًا من أن المفتاح JWT، بينما publishable
    ليس JWT. نستخدم قيمة JWT شكلية فقط لتجاوز الفحص المحلي، ثم نستبدل
    رأس الاتصال الحقيقي إلى apiKey بالمفتاح publishable.
    """
    if str(key).startswith("sb_publishable_"):
        placeholder_jwt = "a.b.c"
        client = create_client(url, placeholder_jwt)
        client.supabase_key = key
        headers = client.options.headers
        headers["apiKey"] = key
        headers.pop("Authorization", None)
        return client
    return create_client(url, key)


sb = create_supabase_client(C["supabase_url"], C["supabase_key"])

BOT_ID = None
AUTH_ACCESS_TOKEN = None
rooms = {}          # room_id -> room_name
last_room = {}      # room_id -> last created_at seen
seen_dm = set()
kaf_games = {}
war_games = {}       # room_id -> حرب: لاعبَان، سفينة، 3 محاولات لكل لاعب
last_music_started = 0.0
music_queue = asyncio.Queue()      # room_id, query, source, requester_id, requester_name
music_state = {}     # room_id -> آخر أغنية شغّلها البوت
music_last_by_user = {}  # user_id -> آخر طلب أغنية، فاصل مستقل دقيقتان لكل مستخدم
music_tasks = {}      # room_id -> مهمة البحث/التشغيل الخلفية
publish_pending = {}  # (room_id, user_id) -> وقت طلب نشر@
SOCIAL_SEEN = set()
SOCIAL_WEBHOOK_TOKEN = str(os.environ.get("SOCIAL_WEBHOOK_TOKEN") or C.get("social_webhook_token", "")).strip()
http: aiohttp.ClientSession = None
media_runner = None
media_site = None

# صور الألعاب PNG
# كتالوج البوت المستقل: لا يقرأ جدول هدايا التطبيق ولا يعرض هداياه.
# تبقى UUIDs هنا كمعرّفات داخلية فقط، ولا تظهر للمستخدم.
BOT_GIFTS = {
    "1": {"id": "2d0d35fa-d0bf-40e1-ace9-938bb49e9a63", "name": "وردة", "emoji": "🌹", "cost_points": 10, "image_url": "https://cdn.jsdelivr.net/gh/twitter/twemoji@latest/assets/72x72/1f339.png"},
    "2": {"id": "157c16af-e01c-48fb-b718-be279406f967", "name": "قلب", "emoji": "❤️", "cost_points": 20, "image_url": "https://cdn.jsdelivr.net/gh/twitter/twemoji@latest/assets/72x72/2764.png"},
    "3": {"id": "056dd4c2-58d2-48a9-8ec7-95169ed1ac54", "name": "قبلة", "emoji": "😘", "cost_points": 30, "image_url": "https://cdn.jsdelivr.net/gh/twitter/twemoji@latest/assets/72x72/1f618.png"},
    "4": {"id": "f9a3c396-0e60-4761-8ae8-d3a4dd6ca096", "name": "دب", "emoji": "🧸", "cost_points": 50, "image_url": "https://cdn.jsdelivr.net/gh/twitter/twemoji@latest/assets/72x72/1f9f8.png"},
    "5": {"id": "5566a755-c78d-4d74-aae9-2da599adae1a", "name": "كعكة", "emoji": "🎂", "cost_points": 80, "image_url": "https://cdn.jsdelivr.net/gh/twitter/twemoji@latest/assets/72x72/1f382.png"},
    "6": {"id": "6bab6899-db41-494b-8fad-8eebf5af8b17", "name": "ألعاب نارية", "emoji": "🎆", "cost_points": 150, "image_url": "https://cdn.jsdelivr.net/gh/twitter/twemoji@latest/assets/72x72/1f386.png"},
    "7": {"id": "416557d0-0297-4a42-8709-7232ace2c65a", "name": "برق", "emoji": "⚡", "cost_points": 200, "image_url": "https://cdn.jsdelivr.net/gh/twitter/twemoji@latest/assets/72x72/26a1.png"},
    "8": {"id": "d255facd-8b2f-407e-8706-33a9fe6ffb00", "name": "تاج", "emoji": "👑", "cost_points": 500, "image_url": "https://cdn.jsdelivr.net/gh/twitter/twemoji@latest/assets/72x72/1f451.png"},
    "9": {"id": "2ac92587-7b58-418a-93d4-cecaf70dc90c", "name": "أميرة", "emoji": "👸", "cost_points": 800, "image_url": "https://cdn.jsdelivr.net/gh/twitter/twemoji@latest/assets/72x72/1f478.png"},
    "10": {"id": "21595a25-4fed-4d9a-a200-fda8a16c6af1", "name": "سيارة", "emoji": "🏎️", "cost_points": 1000, "image_url": "https://cdn.jsdelivr.net/gh/twitter/twemoji@latest/assets/72x72/1f3ce.png"},
    "11": {"id": "f8f5b161-e49f-4f30-9365-4e66af6e0918", "name": "طائرة", "emoji": "✈️", "cost_points": 1500, "image_url": "https://cdn.jsdelivr.net/gh/twitter/twemoji@latest/assets/72x72/2708.png"},
    "12": {"id": "cfa01a67-d54e-4a9f-b11a-dbfa04ad4a4a", "name": "تنين", "emoji": "🐉", "cost_points": 3000, "image_url": "https://cdn.jsdelivr.net/gh/twitter/twemoji@latest/assets/72x72/1f409.png"},
    "13": {"id": "4e3b32a3-17a8-41ef-bc9a-cef4c21e10f7", "name": "سفينة فضاء", "emoji": "🚀", "cost_points": 5000, "image_url": "https://cdn.jsdelivr.net/gh/twitter/twemoji@latest/assets/72x72/1f680.png"},
    "14": {"id": "1aa63f2b-2fbc-40cb-b0af-3c1200724774", "name": "قصر", "emoji": "🏰", "cost_points": 8000, "image_url": "https://cdn.jsdelivr.net/gh/twitter/twemoji@latest/assets/72x72/1f3f0.png"}
}

# صور مباشرة ثابتة بصيغة PNG؛ تُرسل بالطريقة نفسها المستخدمة للهدايا.
TWEMOJI = "https://cdn.jsdelivr.net/gh/twitter/twemoji@latest/assets/72x72/"
GAME_BASE_URL = str(C.get("game_public_base_url", "")).rstrip("/")
def game_asset(filename):
    base = PUBLIC_BASE_URL or GAME_BASE_URL
    if base:
        return f"{base}/assets/{quote(filename)}"
    return f"assets/{filename}"

GAME_IMAGES = {
    "race": game_asset("game_race.jpg"),
    "bribe": game_asset("game_bribe.jpg"),
    "basket": game_asset("game_basket.jpg"),
    "drone": game_asset("game_drone.jpg"),
    "frog": game_asset("game_frog.jpg"),
    "cards": game_asset("game_cards.jpg"),
    "ball": game_asset("game_ball.jpg"),
    "boxing": game_asset("defense_action.jpg"),
    "fight": game_asset("fight_action.jpg"),
    "job": game_asset("game_job.jpg"),
    "meet": game_asset("game_meet.jpg"),
    "slap": game_asset("slap_action.jpg"),
    "volcano": game_asset("game_volcano.jpg"),
    "ghost": game_asset("game_ghost.jpg"),
    "bet": game_asset("game_bet.jpg"),
    "war": game_asset("war_game.png"),
    "rob": game_asset("game_rob.jpg"),
    "luck": game_asset("game_luck.jpg"),
    "dice": game_asset("game_dice.jpg"),
    "marriage": game_asset("game_marriage.jpg"),
    "challenge": game_asset("game_challenge.jpg"),
    "mine": game_asset("game_mine.jpg")
}

# ----------------------------- أدوات البيانات -----------------------------
def load_json(path, default):
    if not os.path.exists(path): return default
    try:
        with open(path, "r", encoding="utf-8") as f: return json.load(f)
    except: return default

def save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=4)

def load_points(): return load_json(POINTS_PATH, {})
def save_points(p): save_json(POINTS_PATH, p)
def load_replies(): return load_json(REPLIES_PATH, {})
def save_replies(r): save_json(REPLIES_PATH, r)
def load_masters(): return load_json(MASTERS_PATH, [])
def save_masters(m): save_json(MASTERS_PATH, m)
def load_bans(): return load_json(BANS_PATH, {})
def save_bans(b): save_json(BANS_PATH, b)
def load_rooms_saved(): return load_json(ROOMS_PATH, {})
def save_rooms_saved(r): save_json(ROOMS_PATH, r)
def load_moderation(): return load_json(MODERATION_PATH, {"enabled": {}, "words": []})
def save_moderation(x): save_json(MODERATION_PATH, x)
def load_welcome(): return load_json(WELCOME_PATH, {})
def save_welcome(x): save_json(WELCOME_PATH, x)
def load_published_posts(): return load_json(PUBLISHED_POSTS_PATH, {})
def save_published_posts(x):
    _prune_expired_posts(x)  # حذف المنشورات المنتهية (>10 ساعات) عند أي حفظ
    save_json(PUBLISHED_POSTS_PATH, x)
def load_social_events(): return load_json(SOCIAL_EVENTS_PATH, {})
def save_social_events(x): save_json(SOCIAL_EVENTS_PATH, x)

def load_vip_users():
    data = load_json(VIP_USERS_PATH, {})
    if isinstance(data, list):
        return {str(x).strip().lower(): {"username": str(x).strip()} for x in data if str(x).strip()}
    return data if isinstance(data, dict) else {}

def save_vip_users(x): save_json(VIP_USERS_PATH, x)

async def is_vip(uid, username):
    if str(username or '').strip().lower() == OWNER:
        return True
    data = load_vip_users()
    key_uid = str(uid)
    key_name = str(username or '').strip().lower()
    if key_uid in data:
        return True
    for key, item in data.items():
        if str(key).lower() == key_name:
            return True
        if isinstance(item, dict):
            if str(item.get("id", "")).strip() == key_uid:
                return True
            if str(item.get("username", "")).strip().lower() == key_name:
                return True
    return False

async def require_vip(uid, username, feature="هذه الخدمة"):
    if await is_vip(uid, username):
        return None
    return (f"🔒 @{username} هذه {feature} تتطلب توثيق VIP من صاحب البوت.\n"
            f"📌 طريقة التوثيق: صاحب البوت يكتب vip@اسم_المستخدم")

async def grant_vip_by_username(target_username):
    target = str(target_username or '').replace('@', '').strip()
    if not target:
        return False, "❌ الصيغة: vip@اسم المستخدم"
    rows, err = await table_select(lambda: sb.table("profiles").select("id,username").eq("username", target).limit(1).execute())
    if err:
        return False, f"❌ تعذر البحث عن المستخدم: {err}"
    if not rows:
        return False, f"❌ المستخدم @{target} غير موجود."
    row = rows[0]
    data = load_vip_users()
    data[str(row.get("id"))] = {"id": str(row.get("id")), "username": str(row.get("username") or target), "granted_at": now_iso()}
    save_vip_users(data)
    return True, f"✅ تم توثيق @{row.get('username') if row.get('username') else target} VIP.\n🎵 يمكنه تشغيل/مشاركة الأغاني.\n🎮 ويمكنه استخدام الألعاب."

def normalize_text(s):
    return re.sub(r"\s+", " ", str(s or "").strip().lower())

async def check_forbidden_word(rid, text):
    mod = load_moderation()
    if not mod.get("enabled", {}).get(str(rid), False) or not text:
        return None
    normalized = normalize_text(text)
    for word in mod.get("words", []):
        if normalize_text(word) and normalize_text(word) in normalized:
            return f"🚫 تم منع الرسالة بسبب الكلمة الممنوعة: {word}"
    return None

async def all_room_ids():
    """Return every room visible to the bot, not only rooms currently cached."""
    ids = set(rooms.keys())
    try:
        rows, _ = await table_select(lambda: sb.table("rooms").select("id,name").execute())
        for row in rows or []:
            rid = row.get("id")
            if rid:
                ids.add(rid)
                rooms.setdefault(rid, row.get("name") or "الغرفة")
    except Exception:
        log.exception("failed to load all rooms")
    return list(ids)

async def broadcast_text(text, exclude_rid=None):
    for room_id in await all_room_ids():
        if room_id == exclude_rid:
            continue
        try:
            await room_send(room_id, text)
        except Exception:
            log.exception("broadcast text failed for room %s", room_id)

async def broadcast_media(text, media_url, m_type="image", duration_ms=None, exclude_rid=None):
    sent = 0
    for room_id in await all_room_ids():
        if room_id == exclude_rid:
            continue
        try:
            await room_send_media(room_id, text, media_url, m_type=m_type, duration_ms=duration_ms)
            sent += 1
        except Exception:
            log.exception("broadcast media failed for room %s", room_id)
    return sent


_POST_CODES = {}  # code (3 chars) -> post_id

def _post_code():
    """توليد كود قصير فريد (3 أحرف/أرقام) بدون الأحرف الملبسة."""
    chars = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
    for _ in range(100):
        code = "".join(random.choice(chars) for _ in range(3))
        if code not in _POST_CODES:
            _POST_CODES[code] = None
            return code
    return "".join(random.choice(chars) for _ in range(3))

def fmt_pts(n):
    """تنسيق النقاط: 1000 -> 1k، 1500 -> 1.5k، مليون -> 1m."""
    try:
        n = int(n or 0)
    except (TypeError, ValueError):
        return "0"
    neg = n < 0
    n = abs(n)
    if n >= 1_000_000:
        v = n / 1_000_000
        s = f"{v:.2f}".rstrip("0").rstrip(".") + "m"
    elif n >= 1000:
        v = n / 1000
        s = f"{v:.2f}".rstrip("0").rstrip(".") + "k"
    else:
        s = str(n)
    return ("-" if neg else "") + s

POST_TTL_SECONDS = int(C.get("post_ttl_hours", 10)) * 3600


def _parse_post_time(created_at):
    try:
        s = str(created_at or "").replace("Z", "+00:00")
        return datetime.fromisoformat(s).timestamp()
    except Exception:
        return 0.0


def _prune_expired_posts(posts):
    """حذف المنشورات التي مضى على نشرها أكثر من POST_TTL_SECONDS (افتراضيًا 10 ساعات)."""
    now = time.time()
    removed = []
    for pid in list(posts.keys()):
        post = posts[pid]
        if (now - _parse_post_time(post.get("created_at"))) > POST_TTL_SECONDS:
            posts.pop(pid, None)
            removed.append(pid)
    # إزالة الأكواد المرتبطة بالمنشورات المحذوفة
    for pid in removed:
        for c in [code for code, p in _POST_CODES.items() if p == pid]:
            _POST_CODES.pop(c, None)
    return bool(removed)


def _find_post_by_code(code):
    """البحث عن منشور (أغنية/صورة/هدية) بالكود المختصر، مع حذف المنتهي تلقائيًا."""
    pid = _POST_CODES.get(code)
    if not pid:
        return None
    posts = load_published_posts()
    _prune_expired_posts(posts)
    save_published_posts(posts)
    return posts.get(pid)


def record_post_reaction(post, kind, p_name, extra=None):
    """حفظ عداد التفاعل وسجل التفاعلات داخل المنشور ثم حفظ الملف."""
    try:
        post.setdefault("reactions", {})
        cur = post["reactions"].get(kind, 0) or 0
        post["reactions"][kind] = cur + 1
        post.setdefault("interactions", [])
        post["interactions"].append({"kind": kind, "user": p_name, "extra": extra, "at": now_iso()})
        posts = load_published_posts()
        pid = post.get("post_id")
        if pid and pid in posts:
            posts[pid] = post
            save_published_posts(posts)
    except Exception:
        log.exception("record_post_reaction failed")


async def _notify_post_owner(post, event_text, p_name, kind, extra=None):
    """حفظ التفاعل ثم إرسال إشعار لصاحب المنشور في خاص البوت."""
    if not post:
        return None
    record_post_reaction(post, kind, p_name, extra)
    owner_id = post.get("owner_id")
    owner_name = post.get("owner_name", "مجهول")
    if not owner_id:
        return None
    try:
        await dm_send(owner_id, f"🔔 {event_text}\n📌 المنشور: {post.get('title', '')}")
        return f"✅ تم إرسال التفاعل لصاحب المنشور @{owner_name}."
    except Exception:
        log.exception("post owner notify failed")
        return f"✅ تم تسجيل تفاعلك على منشور @{owner_name}."

# معالجة الأزرار النصية: Like@KOD / Dislike@KOD / Love@KOD / loved@KOD / Comment@KOD msg / msg@KOD msg / report@KOD msg
_INTERACT_RE = re.compile(r"^(Like|Dislike|Love|loved|Comment|msg|report)@([A-Z0-9]{3})(?:\s+(.*))?$", re.I)

async def _handle_post_interaction(rid, text, uid, p_name):
    """معالجة أزرار التفاعل النصية المرتبطة بكود منشور."""
    m = _INTERACT_RE.match(text.strip())
    if not m:
        return None
    kind, code, extra = m.group(1), m.group(2), (m.group(3) or "").strip()
    post = _find_post_by_code(code.upper())
    if not post:
        return "⚠️ لم أجد منشورًا بهذا الكود. تأكد من كتابة الكود الصحيح.", False
    is_master_user = await is_master(uid, p_name)
    p_type = post.get("type", "")
    if kind.lower() == "like":
        note = "❤️ إعجاب" if p_type == "music" else "👍 إعجاب"
        return await _notify_post_owner(post, f"{note} من @{p_name} على المنشور بالكود {code.upper()}.", p_name, "like"), False
    if kind.lower() == "dislike":
        return await _notify_post_owner(post, f"👎 عدم إعجاب من @{p_name} على المنشور بالكود {code.upper()}.", p_name, "dislike"), False
    if kind.lower() in ("love", "loved"):
        return await _notify_post_owner(post, f"💖 أحببته من @{p_name} على المنشور بالكود {code.upper()}.", p_name, "love"), False
    if kind.lower() == "comment":
        if not extra:
            return "💬 اكتب التعليق بعد الأمر، مثال: Comment@KOD تعليقك هنا", False
        if len(extra) > 200:
            return "💬 التعليق طويل جدًا (حد 200 حرف).", False
        return await _notify_post_owner(post, f"💬 تعليق من @{p_name}: {extra}", p_name, "comment", extra), False
    if kind.lower() == "msg":
        if not extra:
            return "✉️ اكتب رسالتك بعد الأمر، مثال: msg@KOD نص الرسالة", False
        return await _notify_post_owner(post, f"✉️ رسالة من @{p_name}: {extra}", p_name, "msg", extra), False
    if kind.lower() == "report":
        if not extra:
            return "🚨 اكتب سبب الإبلاغ بعد الأمر، مثال: report@KOD سبب الإبلاغ", False
        if len(extra) > 200:
            return "🚨 سبب الإبلاغ طويل جدًا (حد 200 حرف).", False
        # إبلاغ لصاحب المنشور وللماسترز
        await _notify_post_owner(post, f"🚨 إبلاغ من @{p_name}: {extra}", p_name, "report", extra)
        try:
            masters = load_masters()
            for mid in masters[:5]:
                try:
                    await dm_send(mid, f"🚨 إبلاغ على منشور بالكود {code.upper()}: {extra}\nمن @{p_name} في غرفة {rooms.get(rid, 'الغرفة')}")
                except Exception:
                    pass
        except Exception:
            log.exception("report broadcast failed")
        return "🚨 تم إرسال الإبلاغ للإدارة وصاحب المنشور.", False
    return None, False


async def game_cooldown(uid, username):
    """فاصل الألعاب مستقل لكل مستخدم، وليس فاصلًا عالميًا."""
    seconds = int(C.get("game_cooldown_seconds", 30))
    return check_cooldown(uid, username, "game", seconds)

async def is_banned(rid, uid):
    bans = load_bans()
    return uid in bans.get(rid, [])

async def is_master(uid, username):
    if username.lower() == OWNER: return True
    masters = load_masters()
    return uid in masters or username.lower() in [str(m).lower() for m in masters]

def get_user_data(uid, username):
    points = load_points()
    if uid not in points:
        points[uid] = {"username": username, "points": 0, "cooldowns": {}, "married_to": None}
    else:
        points[uid]["username"] = username
    return points, points[uid]

def add_points(uid, username, amount):
    points, user_data = get_user_data(uid, username)
    user_data["points"] += amount
    points[uid] = user_data
    save_points(points)

def check_cooldown(uid, username, command, seconds):
    points, user_data = get_user_data(uid, username)
    cooldowns = user_data.get("cooldowns", {})
    last_time = cooldowns.get(command, 0)
    now = time.time()
    if now - last_time < seconds:
        return False, int(seconds - (now - last_time))
    cooldowns[command] = now
    user_data["cooldowns"] = cooldowns
    points[uid] = user_data
    save_points(points)
    return True, 0

# ----------------------------- أدوات النظام -----------------------------
def now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")

async def run(fn):
    def safe():
        try: return fn(), None
        except Exception as e: return None, getattr(e, "message", None) or str(e)
    return await asyncio.to_thread(safe)

async def table_select(builder_fn):
    res, err = await run(builder_fn)
    if err: return None, err
    return (getattr(res, "data", None) or []), None

async def rpc(name, args):
    res, err = await run(lambda: sb.rpc(name, args).execute())
    if err: return None, err
    return getattr(res, "data", None), None

async def username_of(uid):
    rows, _ = await table_select(lambda: sb.table("profiles").select("username").eq("id", uid).limit(1).execute())
    return (rows[0].get("username") if rows else "") or ""

# ----------------------------- إرسال الرسائل -----------------------------
async def get_gifts_catalog():
    """إرجاع كتالوج البوت فقط، دون قراءة هدايا التطبيق."""
    return [{"_display_id": number, "_internal_id": gift["id"], **gift} for number, gift in BOT_GIFTS.items()]


GIFT_ASSET_BASE = "https://files.manuscdn.com/user_upload_by_module/session_file/310519663845522163/"
GIFT_TEMPLATE_FILES = {
    "1": "assets/gift_template_rose.webp",
    "2": "assets/gift_template_heart.webp",
    "3": "assets/gift_template_kiss.webp",
    "4": "assets/gift_template_present.webp",
    "5": "assets/gift_template_present.webp",
    "6": "assets/gift_template_heart.webp",
    "7": "assets/gift_template_present.webp",
    "8": "assets/gift_template_crown.webp",
    "9": "assets/gift_template_crown.webp",
    "10": "assets/gift_template_present.webp",
    "11": "assets/gift_template_present.webp",
    "12": "assets/gift_template_crown.webp",
    "13": "assets/gift_template_crown.webp",
    "14": "assets/gift_template_crown.webp",
}
BASE_DIR = Path(__file__).resolve().parent
GIFT_BUCKET = str(C.get("gift_image_bucket", "bot-gifts")).strip()

# تخزين الوسائط الدائمة: روابط googlevideo مؤقتة لا تُرسل إلى التطبيق.
MUSIC_BUCKET = str(C.get("music_bucket", "bot-music")).strip()
MUSIC_STORAGE = str(C.get("music_storage", "supabase")).strip().lower()
MUSIC_LOCAL_DIR = BASE_DIR / str(C.get("music_local_dir", "generated_music"))
MUSIC_LOCAL_DIR.mkdir(parents=True, exist_ok=True)
MUSIC_PUBLIC_BASE_URL = str(C.get("music_public_base_url", "")).rstrip("/")
PUBLISH_BUCKET = str(C.get("publish_bucket", "bot-publish")).strip()
PUBLISH_STORAGE = str(C.get("publish_storage", "supabase")).strip().lower()
PUBLISH_LOCAL_DIR = BASE_DIR / str(C.get("publish_local_dir", "published_media"))
PUBLISH_LOCAL_DIR.mkdir(parents=True, exist_ok=True)
PUBLISH_PUBLIC_BASE_URL = str(C.get("publish_public_base_url", "")).rstrip("/")
GAME_BUCKET = str(C.get("game_bucket", "bot-games")).strip()
GIFT_RENDER_DIR = BASE_DIR / "generated_gifts"
GIFT_RENDER_DIR.mkdir(parents=True, exist_ok=True)

# قالب رسالة عرض الأغنية — يمكن تعديله من خاص البوت بأمر "رسالة أغنية"
MUSIC_CARD_TEMPLATE = {"custom": ""}
_music_card_file = BASE_DIR / "music_card_template.txt"
if _music_card_file.exists():
    MUSIC_CARD_TEMPLATE["custom"] = _music_card_file.read_text(encoding='utf-8').strip()
DEFAULT_GIFT_FONT = str(Path(__file__).resolve().parent / "assets" / "Amiri-Bold.ttf")
FONT_PATH = str(C.get("gift_font", DEFAULT_GIFT_FONT))
if not Path(FONT_PATH).exists():
    FONT_PATH = DEFAULT_GIFT_FONT

def shape_text(value):
    text = str(value)
    if arabic_reshaper and get_display and any("\u0600" <= ch <= "\u06ff" for ch in text):
        return get_display(arabic_reshaper.reshape(text))
    return text

def fit_font(text, max_width, start_size=32, min_size=16):
    if not PIL_AVAILABLE:
        raise RuntimeError("Pillow غير مثبتة؛ ثبّت Pillow لإنشاء صور الهدايا بأسماء المرسل والمستقبل")
    size = start_size
    while size >= min_size:
        font = ImageFont.truetype(FONT_PATH, size)
        if font.getbbox(text)[2] <= max_width:
            return font
        size -= 2
    return ImageFont.truetype(FONT_PATH, min_size)

def render_gift_image(gift, sender_name, receiver_name):
    if not PIL_AVAILABLE:
        raise RuntimeError("Pillow غير مثبتة؛ لن تظهر أسماء FROM وTO داخل الصورة")
    template = Path(__file__).resolve().parent / GIFT_TEMPLATE_FILES.get(str(gift["display_id"]), "assets/gift_template_present.webp")
    if not template.exists():
        return None
    image = Image.open(template).convert("RGBA")
    draw = ImageDraw.Draw(image)
    width, height = image.size
    # خانتا FROM وTO في الجزء السفلي من القالب؛ يمكن تخصيصهما من config.json.
    from_y = int(float(C.get("gift_from_y", height * 0.78)))
    to_y = int(float(C.get("gift_to_y", height * 0.88)))
    box_left = int(float(C.get("gift_box_left", width * 0.12)))
    box_right = int(float(C.get("gift_box_right", width * 0.88)))
    # حدود كل مربع (FROM وTO) لتوسيط الاسم داخله أفقيًا ورأسيًا؛ يمكن ضبطها من config.json.
    from_box = (
        int(float(C.get("gift_from_box_left", box_left))),
        int(float(C.get("gift_from_box_top", height * 0.745))),
        int(float(C.get("gift_from_box_right", box_right))),
        int(float(C.get("gift_from_box_bottom", height * 0.82))),
    )
    to_box = (
        int(float(C.get("gift_to_box_left", box_left))),
        int(float(C.get("gift_to_box_top", height * 0.855))),
        int(float(C.get("gift_to_box_right", box_right))),
        int(float(C.get("gift_to_box_bottom", height * 0.93))),
    )
    max_width = max(100, box_right - box_left - 24)
    line_color = tuple(C.get("gift_text_color", [255, 255, 255]))
    shadow = (0, 0, 0, 180)
    for label, name, y, (bx_left, bx_top, bx_right, bx_bottom) in (
            ("FROM:", sender_name, from_y, from_box), ("TO:", receiver_name, to_y, to_box)):
        text = shape_text(f"{label} @{name}")
        box_w = max(100, bx_right - bx_left)
        box_h = max(40, bx_bottom - bx_top)
        # ملاءمة الخط مع مربع المربع (العرض والارتفاع) وليس مع عرض الصورة كاملة.
        font = fit_font(text, min(max_width, box_w - 24))
        bbox = draw.textbbox((0, 0), text, font=font, stroke_width=1)
        tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
        # توسيط أفقي داخل المربع
        x = bx_left + (box_w - tw) / 2
        # توسيط رأسي داخل المربع: مركز المربع ± نصف ارتفاع النص مع تصحيح الصعود/الهبوط
        ascent, descent = font.getmetrics()
        ty = bx_top + (box_h - th) / 2 - ascent / 2
        draw.text((x + 2, ty + 2), text, font=font, fill=shadow, stroke_width=2, stroke_fill=shadow)
        draw.text((x, ty), text, font=font, fill=line_color, stroke_width=1, stroke_fill=(20, 20, 20, 220))
    path = GIFT_RENDER_DIR / f"gift_{gift['display_id']}_{uuid.uuid4().hex}.png"
    image.save(path, "PNG", optimize=True)
    return path

def publish_gift_image(local_path):
    """حفظ صورة الهدية وإرجاع رابط عام من Railway."""
    base_url = str(
        os.environ.get("PUBLIC_BASE_URL")
        or C.get("gift_public_base_url", "")
        or PUBLIC_BASE_URL
    ).rstrip("/")
    if not base_url:
        raise RuntimeError("لم يتم العثور على رابط عام. اضبط PUBLIC_BASE_URL أو استخدم RAILWAY_PUBLIC_DOMAIN.")

    path = Path(local_path).resolve()
    render_dir = GIFT_RENDER_DIR.resolve()
    if not path.exists() or render_dir not in path.parents:
        raise RuntimeError("مسار صورة الهدية غير صالح")

    # حذف الصور الأقدم من 30 دقيقة لتقليل مساحة التخزين المحلي.
    now = time.time()
    for old_file in render_dir.glob("gift_*.png"):
        try:
            if now - old_file.stat().st_mtime > 1800:
                old_file.unlink()
        except OSError:
            log.warning("تعذر حذف صورة قديمة: %s", old_file)

    return f"{base_url}/gifts/{quote(path.name)}"

GIFT_ASSETS = {
    "1": GIFT_ASSET_BASE + "ALvAmhVifZhRCjXC.png",   # وردة
    "2": GIFT_ASSET_BASE + "zeYNOhSVCkKIauQY.png",   # قلب
    "3": GIFT_ASSET_BASE + "fJSahjkgdxRpJYGo.png",   # قبلة
    "4": GIFT_ASSET_BASE + "OgZcddjIHykSdWuW.png",   # دب/هدية
    "5": GIFT_ASSET_BASE + "OgZcddjIHykSdWuW.png",   # كعكة
    "6": GIFT_ASSET_BASE + "zeYNOhSVCkKIauQY.png",   # ألعاب نارية
    "7": GIFT_ASSET_BASE + "zeYNOhSVCkKIauQY.png",   # برق
    "8": GIFT_ASSET_BASE + "RPOSAgpzqiZNRnab.png",   # تاج
    "9": GIFT_ASSET_BASE + "RPOSAgpzqiZNRnab.png",   # أميرة
    "10": GIFT_ASSET_BASE + "OgZcddjIHykSdWuW.png",  # سيارة
    "11": GIFT_ASSET_BASE + "OgZcddjIHykSdWuW.png",  # طائرة
    "12": GIFT_ASSET_BASE + "RPOSAgpzqiZNRnab.png",  # تنين
    "13": GIFT_ASSET_BASE + "RPOSAgpzqiZNRnab.png",  # سفينة فضاء
    "14": GIFT_ASSET_BASE + "RPOSAgpzqiZNRnab.png"   # قصر
}


def gift_view(gift):
    internal_id = str(gift.get("_internal_id", gift.get("id", "")))
    display_id = str(gift.get("_display_id", gift.get("display_id", "")))
    return {
        "id": internal_id,
        "display_id": display_id,
        "name": gift.get("name") or gift.get("gift_name") or f"هدية رقم {display_id}",
        "emoji": gift.get("emoji") or "🎁",
        "cost_points": gift.get("cost_points", gift.get("cost", 0)),
        "image_url": GIFT_ASSETS.get(display_id) or gift.get("image_url") or gift.get("image") or gift.get("media_url")
    }


async def gift_catalog_message():
    gifts = [gift_view(g) for g in await get_gifts_catalog()]
    if not gifts:
        return "📭 لا توجد هدايا متاحة حالياً."
    lines = ["🎁 كتالوج الهدايا", "━━━━━━━━━━━━━━"]
    for g in gifts:
        lines.append(f"{g['display_id']} {g['emoji']} {g['name']} | 💰 {g['cost_points']} نقطة")
    lines.append("━━━━━━━━━━━━━━")
    lines.append("للإرسال: gv@رقم_الهدية@اسم_الحساب")
    return "\n".join(lines)


async def send_gift_command(rid, sender_uid, sender_name, raw_text):
    parts = [part.strip() for part in raw_text.split("@", 2)]
    if len(parts) != 3 or not parts[1] or not parts[2]:
        return "❌ الصيغة الصحيحة: gv@رقم_الهدية@اسم_الحساب"

    gift_id, receiver_name = parts[1], parts[2].lstrip("@").strip()
    gifts = [gift_view(g) for g in await get_gifts_catalog()]
    gift = next((g for g in gifts if str(g["display_id"]) == gift_id), None)
    if not gift:
        return "❌ رقم الهدية غير موجود. اكتب `gv` لعرض الهدايا المتاحة."

    receiver_rows, _ = await table_select(lambda: sb.table("profiles").select("id,username").eq("username", receiver_name).limit(1).execute())
    if not receiver_rows:
        return f"❌ الحساب @{receiver_name} غير موجود."
    receiver = receiver_rows[0]
    receiver_name = receiver.get("username") or receiver_name

    # نظام الهدايا مستقل عن نظام هدايا التطبيق:
    # الخصم يتم من نفس points.json الذي تستخدمه الألعاب، ولا نستدعي RPC send_gift.
    try:
        cost = int(gift.get("cost_points") or 0)
    except (TypeError, ValueError):
        cost = 0
    if cost < 0:
        return "❌ قيمة الهدية غير صالحة."

    # قفل عملية الخصم حتى لا يستطيع مستخدم إرسال هديتين متزامنتين
    # واستعمال نفس الرصيد قبل حفظ التغيير.
    async with GIFT_POINTS_LOCK:
        points, sender_data = get_user_data(sender_uid, sender_name)
        balance = int(sender_data.get("points", 0) or 0)
        if balance < cost:
            return f"❌ نقاطك غير كافية. رصيدك: {balance} | سعر الهدية: {cost} نقطة."
        sender_data["points"] = balance - cost
        points[sender_uid] = sender_data
        save_points(points)
        remaining_points = sender_data["points"]

    image_url = None
    # لا نرسل القالب الثابت هنا؛ المطلوب صورة تحمل اسمي FROM وTO.
    try:
        rendered = await asyncio.to_thread(render_gift_image, gift, sender_name, receiver_name)
        if not rendered:
            raise RuntimeError("Pillow غير مثبتة أو تعذر إنشاء الصورة الديناميكية")
        image_url = await asyncio.to_thread(publish_gift_image, rendered)
        if not image_url:
            raise RuntimeError("لم يُرجع Storage رابط الصورة")
    except Exception as exc:
        log.exception("dynamic gift image failed: %s", exc)
        reason = str(exc).replace("\n", " ")[:180]
        await room_send(rid, f"⚠️ تم تسجيل الهدية، لكن تعذر إنشاء صورة الأسماء.\n🔎 السبب: {reason}")
    # أرسل الصورة الديناميكية فقط عندما تنجح، حتى لا تظهر خانات FROM وTO فارغة.
    if image_url:
        await room_send_media(rid, f"{gift['emoji']} {gift['name']}", image_url, m_type="image")
    await room_send(rid, f"🎁 أرسل @{sender_name} إلى @{receiver_name} هدية {gift['name']} {gift['emoji']}")
    card = (
        f"{gift['emoji']} 🎁 {gift['name']}\n"
        f"👤 المرسل: @{sender_name}\n"
        f"🎯 المستقبل: @{receiver_name}\n"
        f"💰 القيمة: {gift['cost_points']} نقطة\n"
        f"💳 رصيدك المتبقي: {fmt_pts(remaining_points)} نقطة"
    )
    await room_send(rid, card)
    # إشعارات خاصة للطرفين: لا تبقى معلومات الهدية داخل الغرفة فقط.
    try:
        await dm_send(receiver_rows[0]["id"], f"🎁 @{sender_name} أرسل لك {gift['emoji']} {gift['name']} بقيمة {gift['cost_points']} نقطة.")
        await dm_send(sender_uid, f"✅ تم إرسال {gift['emoji']} {gift['name']} إلى @{receiver_name} بقيمة {gift['cost_points']} نقطة.")
    except Exception:
        log.exception("gift private notification failed")
    # الهدية تُسجّل أيضًا كمنشور حتى يمكن التفاعل معها (إعجاب/حب/تعليق) بنفس طريقة الأغاني والصور.
    gift_pid = str(uuid.uuid4())
    posts = load_published_posts()
    posts[gift_pid] = {
        "post_id": gift_pid, "owner_id": str(sender_uid), "owner_name": sender_name,
        "source_room_id": str(rid), "type": "gift",
        "title": f"هدية {gift['name']} لـ @{receiver_name}",
        "created_at": now_iso()
    }
    save_published_posts(posts)
    gift_code = _post_code()
    _POST_CODES[gift_code] = gift_pid
    await room_send(rid, (
        f"🎁 هدية بالكود {gift_code} — يمكن التفاعل معها:\n"
        f"❤️ Like@{gift_code} | 💖 Love@{gift_code} | 👎 Dislike@{gift_code}\n"
        f"💬 Comment@{gift_code} [تعليق] | ✉️ msg@{gift_code} [رسالة]"
    ))
    # الهدية تُنشر إعلانها وصورتها في كل غرف البوت الأخرى.
    if image_url:
        await broadcast_media(f"🎁 هدية جديدة: {gift['emoji']} {gift['name']} | @{sender_name} ➜ @{receiver_name}",
                              image_url, m_type="image", exclude_rid=rid)
    await broadcast_text(card, exclude_rid=rid)
    return None


async def room_send(rid, text):
    await run(lambda: sb.table("room_messages").insert({
        "room_id": rid, "user_id": BOT_ID, "content": text, "message_type": "text"
    }).execute())

async def room_send_media(rid, text, media_url, m_type="text", duration_ms=None):
    payload = {
        "room_id": rid,
        "user_id": BOT_ID,
        "content": text,
        "message_type": m_type,
        "media_url": media_url,
        "media_duration_ms": duration_ms,
        "client_id": "giant-bot",
    }
    # retry حتى 3 مرات
    for attempt in range(3):
        res, err = await run(lambda: sb.table("room_messages").insert(payload).execute())
        if not err:
            return
        log.warning("room_send_media attempt %d failed for room %s: %s", attempt + 1, rid, err)
        if attempt < 2:
            await asyncio.sleep(1)

async def dm_send(uid, text):
    envelope = {
        "v": 1, "id": str(uuid.uuid4()), "content": text, "message_type": "text",
        "media_url": None, "media_duration_ms": None, "reply_to_id": None, "created_at": now_iso()
    }
    await run(lambda: sb.table("dm_relay").insert({
        "sender_id": BOT_ID, "recipient_id": uid, "envelope": envelope
    }).execute())

async def _master_user_ids():
    """Resolve saved master usernames/IDs to profile IDs for private diagnostics."""
    result = set()
    for master in load_masters():
        value = str(master).strip()
        if not value:
            continue
        # Masters may already be stored as UUID/user IDs.
        result.add(value)
        try:
            rows, _ = await table_select(
                lambda v=value: sb.table("profiles").select("id").ilike("username", v).limit(5).execute()
            )
            for row in rows or []:
                if row.get("id"):
                    result.add(str(row["id"]))
        except Exception:
            log.exception("failed to resolve master %s", value)
    # The owner is always a diagnostic recipient.
    try:
        rows, _ = await table_select(
            lambda: sb.table("profiles").select("id").ilike("username", OWNER).limit(5).execute()
        )
        for row in rows or []:
            if row.get("id"):
                result.add(str(row["id"]))
    except Exception:
        pass
    return result

async def report_music_error_to_masters(rid, source, query, error, stage="تشغيل"):
    """Send the real music failure privately to every master/owner.
    Secrets such as cookie values are never included.
    """
    raw = str(error or "خطأ غير معروف").replace("\x1b", "")
    raw = re.sub(r"\[[0-9;]*m", "", raw)
    raw = raw.strip()
    if len(raw) > 1800:
        raw = raw[:1800] + "…"
    room_name = rooms.get(rid, str(rid))
    msg = (
        "🛠️ تشخيص فشل تشغيل الأغنية\n"
        f"📍 المرحلة: {stage}\n"
        f"🎵 المصدر: {source}\n"
        f"🔎 الطلب: {query}\n"
        f"🏠 الغرفة: {room_name}\n"
        f"🕒 الوقت: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
        "━━━━━━━━━━━━━━\n"
        f"❌ الخطأ الحقيقي:\n{raw}"
    )
    for master_id in await _master_user_ids():
        try:
            await dm_send(master_id, msg)
        except Exception:
            log.exception("failed to send music diagnostic to master %s", master_id)

# ----------------------------- الموسيقى -----------------------------
async def _yt_extract(search_query):
    """البحث عن فيديو YouTube بدون محاولة تنزيله.
    نبدأ بـ yt-dlp ببحث flat حتى لا نفشل بسبب حظر استخراج صيغ الفيديو،
    ثم نجرب Piped كاحتياط. نعيد سبب الفشل الحقيقي للتشخيص.
    """
    q = str(search_query).strip()
    if q.lower().startswith("ytsearch1:"):
        q = q.split(":", 1)[1].strip()
    errors = []

    if yt_dlp is not None:
        def extract():
            options = yt_base_options("YouTube")
            options.update({
                "skip_download": True,
                "extract_flat": True,
                "default_search": "ytsearch1",
            })
            with yt_dlp.YoutubeDL(options) as ydl:
                info = ydl.extract_info(f"ytsearch1:{q}", download=False)
                entry = (info.get("entries") or [None])[0] if info else info
                if not entry:
                    return None
                vid = entry.get("id")
                url = entry.get("webpage_url") or entry.get("original_url")
                if not url and vid:
                    url = f"https://www.youtube.com/watch?v={vid}"
                return {
                    "id": vid,
                    "title": entry.get("title") or "المقطع",
                    "artist": entry.get("uploader") or entry.get("channel") or "YouTube",
                    "youtube_url": url,
                    "thumbnail": entry.get("thumbnail"),
                    "duration": entry.get("duration") or 0,
                }
        try:
            track = await asyncio.to_thread(extract)
            if track and track.get("youtube_url"):
                return track, None
            errors.append("yt-dlp: اتصلت بيوتيوب لكن البحث لم يُرجع نتائج.")
        except Exception as e:
            errors.append(f"yt-dlp: {type(e).__name__}: {e}")
            log.warning("yt-dlp YouTube search failed: %s", e)
    else:
        errors.append("yt-dlp غير مثبت داخل الحاوية.")

    for api in PIPED_APIS:
        try:
            async with http.get(
                f"{api}/search", params={"q": q, "filter": "videos"},
                timeout=aiohttp.ClientTimeout(total=12),
                headers={"User-Agent": "Mozilla/5.0"}
            ) as resp:
                if resp.status != 200:
                    errors.append(f"Piped {api}: HTTP {resp.status}")
                    continue
                data = await resp.json(content_type=None)
            items = data.get("items") or []
            item = next((x for x in items if x.get("url") or x.get("id")), None)
            if item:
                vid = item.get("id") or str(item.get("url", "")).split("v=")[-1]
                return {
                    "id": vid,
                    "title": item.get("title") or "المقطع",
                    "artist": item.get("uploaderName") or item.get("uploader") or "YouTube",
                    "youtube_url": f"https://www.youtube.com/watch?v={vid}",
                    "thumbnail": item.get("thumbnail"),
                    "duration": item.get("duration") or 0,
                    "piped_api": api,
                }, None
        except Exception as e:
            errors.append(f"Piped {api}: {type(e).__name__}: {e}")
            log.warning("Piped search failed %s: %s", api, e)

    return None, " | ".join(errors[-5:]) if errors else "لم توجد نتائج من YouTube أو المصادر الاحتياطية."

async def _yt_download_audio(page_url, source_label, piped_api=None, video_id=None):
    """تنزيل الصوت مع تشخيص منفصل لكل محاولة."""
    temp_dir = Path(tempfile.mkdtemp(prefix="bot_audio_"))
    errors = []
    try:
        # إذا كانت Cookies موجودة، نستخدم yt-dlp أولاً حتى يستفيد من جلسة YouTube.
        prefer_ytdlp = source_label == "YouTube" and has_youtube_cookies()

        def download_with_format(fmt, suffix="audio", use_cookies=True, clients=None):
            options = yt_base_options(source_label)
            if source_label == "YouTube":
                # بعض جلسات YouTube في أغسطس 2026 تعطي "The page needs to be reloaded"
                # عند تمرير Cookies مع tv/web_safari. نجرّب أولاً بدون cookies، ثم
                # جلسة cookies باستخدام default + web_embedded.
                if not use_cookies:
                    options.pop("cookiefile", None)
                if clients:
                    options["extractor_args"] = {"youtube": {"player_client": clients}}
            options.update({
                "format": fmt,
                "outtmpl": str(temp_dir / f"{suffix}.%(ext)s"),
                "noplaylist": True,
                "sleep_interval": 0.5,
                "max_sleep_interval": 1.5,
            })
            with yt_dlp.YoutubeDL(options) as ydl:
                ydl.download([page_url])

        async def try_invidious():
            """استخراج الصوت عبر Invidious instances بدون yt-dlp."""
            if not video_id:
                return None
            for api in INVIDIOUS_APIS:
                try:
                    async with http.get(
                        f"{api}/api/v1/videos/{video_id}",
                        timeout=aiohttp.ClientTimeout(total=15),
                        headers={"User-Agent": "Mozilla/5.0"},
                    ) as resp:
                        if resp.status != 200:
                            continue
                        info = await resp.json(content_type=None)
                    # البحث عن أفضل stream صوتي
                    audio_streams = []
                    for fmt in info.get("adaptiveFormats", []):
                        if "audio" in fmt.get("type", ""):
                            audio_streams.append(fmt)
                    if not audio_streams:
                        errors.append(f"Invidious {api}: no audio stream")
                        continue
                    best = max(audio_streams, key=lambda x: int(x.get("bitrate", 0) or 0))
                    url = best.get("url")
                    if not url:
                        continue
                    out = temp_dir / "audio_invidious.m4a"
                    async with http.get(url, timeout=aiohttp.ClientTimeout(total=120)) as ar:
                        if ar.status != 200:
                            errors.append(f"Invidious {api}: HTTP {ar.status}")
                            continue
                        with out.open("wb") as f:
                            async for chunk in ar.content.iter_chunked(256 * 1024):
                                f.write(chunk)
                    if out.is_file() and out.stat().st_size > 4096:
                        return out
                except Exception as e:
                    errors.append(f"Invidious {api}: {type(e).__name__}: {e}")
                    log.warning("Invidious failed %s: %s", api, e)
            return None

        async def try_ytdlp():
            if yt_dlp is None:
                errors.append("yt-dlp غير مثبت داخل Railway.")
                return None
            formats = [
                "bestaudio[ext=m4a]/bestaudio[ext=webm]/bestaudio/best",
                "bestaudio/best",
                "best[ext=mp4]/best",
            ]
            attempts = []
            if source_label == "YouTube":
                # محاولة 1: بدون cookies مع default+web_embedded
                for idx, fmt in enumerate(formats):
                    attempts.append((idx, fmt, False, ["default", "web_embedded"]))
                # محاولة 2: بدون cookies مع tv/tvos/android_vr
                base2 = len(attempts)
                for j, fmt in enumerate(formats):
                    attempts.append((base2 + j, fmt, False, ["tv", "tvos", "android_vr"]))
                # محاولة 3: cookies مع default+web_embedded+tv
                if has_youtube_cookies():
                    base = len(attempts)
                    for j, fmt in enumerate(formats):
                        attempts.append((base + j, fmt, True, ["default", "web_embedded", "tv", "tvos"]))
                # محاولة 4: cookies + mweb
                if has_youtube_cookies():
                    base_m = len(attempts)
                    attempts.append((base_m, formats[0], True, ["mweb"]))
                # محاولة 5: بدون cookies مع عميل android (بعض الجلسات تعمل معه)
                base5 = len(attempts)
                for j, fmt in enumerate(formats[:1]):  # فقط format أول
                    attempts.append((base5 + j, fmt, False, ["android"]))
            else:
                attempts = [(idx, fmt, True, None) for idx, fmt in enumerate(formats)]

            for idx, fmt, use_cookies, clients in attempts:
                try:
                    for p in temp_dir.glob("*"):
                        if p.is_file() and p.suffix not in (".part", ".ytdl"):
                            try: p.unlink()
                            except OSError: pass
                    await asyncio.to_thread(download_with_format, fmt, f"audio_{idx}", use_cookies, clients)
                    files = [p for p in temp_dir.iterdir() if p.is_file() and p.suffix not in (".part", ".ytdl") and p.stat().st_size > 4096]
                    if files:
                        return max(files, key=lambda p: p.stat().st_size)
                except Exception as e:
                    cookie_tag = "cookies" if use_cookies else "بدون-cookies"
                    errors.append(f"yt-dlp [{fmt}][{cookie_tag}]: {type(e).__name__}: {e}")
                    log.warning("yt-dlp audio failed (%s,%s): %s", fmt, cookie_tag, e)
            return None

        async def try_piped():
            if not (piped_api and video_id):
                return None
            try:
                async with http.get(f"{piped_api}/streams/{video_id}", timeout=aiohttp.ClientTimeout(total=25), headers={"User-Agent":"Mozilla/5.0"}) as resp:
                    if resp.status != 200:
                        errors.append(f"Piped {piped_api}: HTTP {resp.status}")
                        return None
                    info = await resp.json(content_type=None)
                streams = sorted(info.get("audioStreams") or [], key=lambda x: float(x.get("bitrate") or 0), reverse=True)
                for stream in streams:
                    url = stream.get("url")
                    if not url: continue
                    try:
                        ext = ".m4a" if "mp4" in str(stream.get("mimeType", "")) else ".webm"
                        out = temp_dir / f"audio{ext}"
                        async with http.get(url, timeout=aiohttp.ClientTimeout(total=120)) as ar:
                            if ar.status != 200:
                                continue
                            with out.open("wb") as f:
                                async for chunk in ar.content.iter_chunked(1024 * 256): f.write(chunk)
                        if out.is_file() and out.stat().st_size > 4096:
                            return out
                    except Exception as e:
                        errors.append(f"Piped audio stream: {type(e).__name__}: {e}")
            except Exception as e:
                errors.append(f"Piped {piped_api}: {type(e).__name__}: {e}")
            return None

        if prefer_ytdlp:
            out = await try_ytdlp()
            if out: return out, None
            # تبديل حساب YouTube وإعادة المحاولة
            if _youtube_accounts:
                log.info("فشل yt-dlp، جاري تبديل حساب YouTube...")
                rotate_youtube_account()
                out = await try_ytdlp()
                if out: return out, None
            out = await try_piped()
            if out: return out, None
        else:
            out = await try_piped()
            if out: return out, None
            out = await try_ytdlp()
            if out: return out, None
            # تبديل حساب YouTube وإعادة المحاولة
            if _youtube_accounts:
                log.info("فشل yt-dlp، جاري تبديل حساب YouTube...")
                rotate_youtube_account()
                out = await try_ytdlp()
                if out: return out, None

        # ذكاء اصطناعي: تحليل الأخطاء واختيار أفضل استراتيجية بديلة
        strategy = await _ai_analyze_music_error(errors, source_label)
        if strategy == "try_invidious":
            log.info("AI decided: %s", strategy)
            out = await try_invidious()
            if out: return out, None
        else:
            # Default: try Invidious anyway as ultimate fallback
            out = await try_invidious()
            if out: return out, None

        return None, "تعذر تنزيل الصوت. " + " | ".join(errors[-6:])
    except Exception as e:
        log.exception("%s audio download failed", source_label)
        return None, f"{type(e).__name__}: {e}"

async def _upload_bytes_storage(local_path, bucket, prefix, content_type):
    """رفع ملف إلى Supabase Storage وإرجاع رابط ثابت/عام."""
    if not bucket:
        raise RuntimeError("اسم Storage bucket غير مضبوط")

    filename = f"{prefix}/{uuid.uuid4().hex}{local_path.suffix.lower() or '.bin'}"
    data = local_path.read_bytes()

    def upload():
        storage = sb.storage.from_(bucket)
        # upsert يمنع فشل الرفع بسبب إعادة استخدام اسم الملف.
        storage.upload(
            filename,
            data,
            {"content-type": content_type, "upsert": "true"},
        )
        return storage.get_public_url(filename)

    return await asyncio.to_thread(upload)


async def prepare_game_assets():
    """Publish local game images to Supabase Storage so every client can see them.
    Falls back to game_public_base_url when configured."""
    if not GAME_BUCKET:
        return
    for key, url in list(GAME_IMAGES.items()):
        if not isinstance(url, str) or not url.startswith("assets/"):
            continue
        local = BASE_DIR / url
        if not local.is_file():
            continue
        try:
            content_type = "image/png" if local.suffix.lower() == ".png" else "image/jpeg"
            public_url = await _upload_bytes_storage(local, GAME_BUCKET, "games", content_type)
            GAME_IMAGES[key] = public_url
        except Exception as e:
            log.warning("تعذر رفع صورة اللعبة %s: %s", key, e)
            if GAME_BASE_URL or PUBLIC_BASE_URL:
                GAME_IMAGES[key] = f"{GAME_BASE_URL or PUBLIC_BASE_URL}/assets/{quote(local.name)}"
        if (not str(GAME_IMAGES.get(key, "")).startswith(("http://", "https://"))
                and (GAME_BASE_URL or PUBLIC_BASE_URL)):
            GAME_IMAGES[key] = f"{GAME_BASE_URL or PUBLIC_BASE_URL}/assets/{quote(local.name)}"

async def _store_media(local_path, kind="music", content_type=None):
    """تجهيز رابط عام ثابت للوسائط.

    في Railway نستخدم خادم HTTP صغير داخل نفس الخدمة، لأن Giant Chat يحتاج
    رابطاً عاماً يمكن للمتصفح/التطبيق الوصول إليه مباشرة. Supabase يبقى
    خياراً احتياطياً إذا لم يوجد رابط عام.
    """
    if content_type is None:
        ext = local_path.suffix.lower()
        content_type = {
            ".mp3": "audio/mpeg", ".m4a": "audio/mp4",
            ".webm": "audio/webm", ".ogg": "audio/ogg",
            ".wav": "audio/wav",
            ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
            ".png": "image/png", ".webp": "image/webp",
        }.get(ext, "application/octet-stream")

    if kind == "music":
        storage_mode = MUSIC_STORAGE
        bucket = MUSIC_BUCKET
        local_dir = MUSIC_LOCAL_DIR
        base_url = PUBLIC_BASE_URL or MUSIC_PUBLIC_BASE_URL
    elif kind == "game":
        storage_mode = str(C.get("game_storage", "supabase")).strip().lower()
        bucket = GAME_BUCKET
        local_dir = BASE_DIR / str(C.get("game_local_dir", "generated_games"))
        base_url = PUBLIC_BASE_URL or GAME_BASE_URL
    else:
        storage_mode = PUBLISH_STORAGE
        bucket = PUBLISH_BUCKET
        local_dir = PUBLISH_LOCAL_DIR
        base_url = PUBLIC_BASE_URL or PUBLISH_PUBLIC_BASE_URL

    # Railway/local public server: لا يحتاج bucket عام ولا سياسة Storage.
    if kind == "music" and base_url and storage_mode in ("railway", "local", "auto", "supabase"):
        try:
            local_dir.mkdir(parents=True, exist_ok=True)
            filename = f"{uuid.uuid4().hex}{local_path.suffix.lower()}"
            target = local_dir / filename
            shutil.copy2(local_path, target)
            return f"{base_url}{MEDIA_PATH}/{quote(filename)}"
        except Exception as e:
            log.warning("public local media failed: %s", e)

    if storage_mode in ("supabase", "auto"):
        try:
            return await _upload_bytes_storage(local_path, bucket, kind, content_type)
        except Exception as e:
            log.warning("Supabase Storage upload failed (%s): %s", kind, e)
            if storage_mode == "supabase" and not base_url:
                raise

    if base_url:
        target = local_dir / f"{uuid.uuid4().hex}{local_path.suffix.lower()}"
        shutil.copy2(local_path, target)
        route = {"game": "/games", "publish": "/published", "music": MEDIA_PATH}.get(kind, "/media")
        return f"{base_url}{route}/{quote(target.name)}"

    raise RuntimeError(
        f"تعذر نشر ملف {kind}: لم يتم تحديد PUBLIC_BASE_URL/Railway domain "
        f"ولم ينجح Supabase Storage."
    )


async def handle_social_event(event):
    """إشعارات اجتماعية خاصة متوافقة مع أحداث Giant Chat/ZBot."""
    if not isinstance(event, dict):
        return {"handled": False}
    data = event.get("data") if isinstance(event.get("data"), dict) else event
    etype = str(event.get("event") or event.get("type") or data.get("type") or "").lower().strip()
    event_id = str(event.get("id") or data.get("id") or uuid.uuid4())
    if event_id in SOCIAL_SEEN:
        return {"handled": True, "duplicate": True}
    SOCIAL_SEEN.add(event_id)
    if len(SOCIAL_SEEN) > 5000:
        SOCIAL_SEEN.clear()
        SOCIAL_SEEN.add(event_id)

    actor_id = data.get("actor_id") or data.get("sender_id") or data.get("user_id")
    actor_name = data.get("actor_name") or data.get("sender_name") or data.get("username") or "مستخدم"
    owner_id = data.get("owner_id") or data.get("post_owner_id") or data.get("receiver_id") or data.get("to_user_id")
    owner_name = data.get("owner_name") or data.get("post_owner_name") or data.get("receiver_name")
    post_id = str(data.get("post_id") or data.get("publication_id") or "")

    if etype in ("member_joined", "member_join", "join"):
        rid = data.get("room_id")
        uid = data.get("user_id") or data.get("member_id")
        name = data.get("username") or data.get("member_name") or actor_name
        if rid and uid and str(uid) != str(BOT_ID):
            welcome = load_welcome().get(str(rid), {})
            if welcome.get("enabled", True):
                msgs = welcome.get("messages") or ["🤖 بوت العملاق يرحب بك يا @{name} 🌟"]
                msg = random.choice(msgs).replace("{name}", name).replace("@name", "@" + name)
                await room_send(rid, msg)
            return {"handled": True, "kind": "member_joined"}

    # المنشورات: أعجب/عدم إعجاب/أحببته/تعليق.
    if etype in ("post_like", "like", "reaction", "post_reaction", "post_dislike", "post_comment", "comment"):
        reaction = str(data.get("reaction") or data.get("action") or etype).lower()
        if not owner_id and post_id:
            owner_id = load_published_posts().get(post_id, {}).get("owner_id")
        if not owner_id or str(owner_id) == str(actor_id):
            return {"handled": False, "reason": "owner_not_found"}
        if "comment" in reaction or etype in ("post_comment", "comment"):
            body = str(data.get("comment") or data.get("content") or data.get("text") or "").strip()
            notice = f"💬 @{actor_name} علّق على منشورك" + (f": {body}" if body else ".")
        elif "dislike" in reaction or "عدم" in reaction:
            notice = f"👎 @{actor_name} لم يعجبه منشورك."
        elif "love" in reaction or "احب" in reaction:
            notice = f"💖 @{actor_name} أحب منشورك."
        else:
            notice = f"❤️ @{actor_name} أعجب بمنشورك."
        await dm_send(owner_id, notice)
        return {"handled": True, "kind": "post_interaction", "owner_id": str(owner_id)}

    if etype in ("gift_sent", "gift", "gift_received", "send_gift"):
        receiver = owner_id or data.get("gift_receiver_id")
        receiver_name = owner_name or data.get("gift_receiver_name") or "المستخدم"
        gift_name = data.get("gift_name") or data.get("name") or "هدية"
        emoji = data.get("gift_emoji") or data.get("emoji") or "🎁"
        if receiver and str(receiver) != str(actor_id):
            await dm_send(receiver, f"{emoji} 🎁 @{actor_name} أرسل لك {gift_name}.")
        if actor_id and receiver_name:
            await dm_send(actor_id, f"✅ تم إرسال {emoji} {gift_name} إلى @{receiver_name}.")
        return {"handled": True, "kind": "gift"}

    return {"handled": False, "kind": etype}


async def start_media_server():
    """تشغيل خادم ملفات الصوت داخل Railway على PORT."""
    global media_runner, media_site

    app = web.Application()
    media_dir = MUSIC_LOCAL_DIR
    media_dir.mkdir(parents=True, exist_ok=True)

    async def media_handler(request):
        name = os.path.basename(request.match_info.get("name", ""))
        if not name or name != request.match_info.get("name", ""):
            raise web.HTTPBadRequest(text="invalid media name")
        path = media_dir / name
        if not path.is_file():
            raise web.HTTPNotFound()
        ctype = {
            ".mp3": "audio/mpeg", ".m4a": "audio/mp4",
            ".webm": "audio/webm", ".ogg": "audio/ogg", ".wav": "audio/wav",
        }.get(path.suffix.lower(), "application/octet-stream")
        return web.FileResponse(path, headers={
            "Content-Type": ctype,
            "Accept-Ranges": "bytes",
            "Cache-Control": "public, max-age=86400",
            "Access-Control-Allow-Origin": "*",
        })

    app.router.add_get(f"{MEDIA_PATH}/{{name}}", media_handler)

    async def public_asset_handler(request):
        rel = request.match_info.get("path", "")
        safe = Path(rel)
        if ".." in safe.parts:
            raise web.HTTPBadRequest()
        file_path = BASE_DIR / "assets" / safe
        if not file_path.is_file():
            raise web.HTTPNotFound()
        return web.FileResponse(file_path)

    async def gift_handler(request):
        name = os.path.basename(request.match_info.get("name", ""))
        file_path = GIFT_RENDER_DIR / name
        if not file_path.is_file():
            raise web.HTTPNotFound()
        return web.FileResponse(file_path)

    async def health_handler(request):
        return web.json_response({"ok": True, "media": MEDIA_PATH})

    app.router.add_get("/", health_handler)
    app.router.add_get("/health", health_handler)
    app.router.add_get("/assets/{path:.*}", public_asset_handler)
    async def game_handler(request):
        name = os.path.basename(request.match_info.get("name", ""))
        file_path = BASE_DIR / str(C.get("game_local_dir", "generated_games")) / name
        if not file_path.is_file():
            raise web.HTTPNotFound()
        return web.FileResponse(file_path, headers={"Cache-Control": "public, max-age=86400"})

    async def published_handler(request):
        name = os.path.basename(request.match_info.get("name", ""))
        file_path = PUBLISH_LOCAL_DIR / name
        if not file_path.is_file():
            raise web.HTTPNotFound()
        return web.FileResponse(file_path, headers={"Cache-Control": "public, max-age=86400"})

    async def social_webhook(request):
        if SOCIAL_WEBHOOK_TOKEN and request.headers.get("X-Social-Token", "") != SOCIAL_WEBHOOK_TOKEN:
            raise web.HTTPUnauthorized()
        try:
            payload = await request.json()
        except Exception:
            raise web.HTTPBadRequest(text="invalid json")
        result = await handle_social_event(payload)
        return web.json_response({"ok": True, **result})

    app.router.add_get("/gifts/{name}", gift_handler)
    app.router.add_get("/games/{name}", game_handler)
    app.router.add_get("/published/{name}", published_handler)
    app.router.add_post("/webhook", social_webhook)
    runner = web.AppRunner(app, access_log=None)
    await runner.setup()
    media_runner = runner
    media_site = web.TCPSite(runner, "0.0.0.0", MEDIA_SERVER_PORT)
    await media_site.start()
    log.info("خادم ملفات الموسيقى يعمل على 0.0.0.0:%s | PUBLIC_BASE_URL=%s",
             MEDIA_SERVER_PORT, PUBLIC_BASE_URL or "(غير مضبوط)")


CLEANUP_INTERVAL_SECONDS = 10 * 3600  # كل 10 ساعات
GIFT_IMAGE_MAX_AGE_SECONDS = 30 * 60    # حذف صور الهدايا المولدة بعد 30 دقيقة


def cleanup_bot_leftovers():
    """تنظيف مخلفات البوت: صور الهدايا القديمة + الملفات المؤقتة."""
    now = time.time()
    removed_bytes = 0
    # صور الهدايا المولدة أقدم من 30 دقيقة
    if GIFT_RENDER_DIR.exists():
        for file_path in GIFT_RENDER_DIR.glob("gift_*.png"):
            try:
                if now - file_path.stat().st_mtime > GIFT_IMAGE_MAX_AGE_SECONDS:
                    removed_bytes += file_path.stat().st_size
                    file_path.unlink()
            except OSError:
                pass
    # الملفات المؤقتة في مجلد الموسيقى المنشورة (ملفات قديمة جدًا)
    if PUBLISH_LOCAL_DIR.exists():
        for file_path in PUBLISH_LOCAL_DIR.glob("*.*"):
            try:
                if now - file_path.stat().st_mtime > 3600:
                    removed_bytes += file_path.stat().st_size
                    file_path.unlink()
            except OSError:
                pass
    # الملفات المؤقتة في tmp
    try:
        for file_path in Path(tempfile.gettempdir()).glob("alsfer_*.*"):
            try:
                if now - file_path.stat().st_mtime > 3600:
                    removed_bytes += file_path.stat().st_size
                    file_path.unlink()
            except OSError:
                pass
    except Exception:
        pass
    return removed_bytes


async def posts_and_leftovers_cleanup():
    """عامل دوري كل 10 ساعات: حذف المنشورات المنتهية وتنظيف المخلفات."""
    while True:
        try:
            posts = load_published_posts()
            removed = _prune_expired_posts(posts)
            if removed:
                save_published_posts(posts)
                log.info("cleanup: removed expired posts")
            bytes_freed = cleanup_bot_leftovers()
            log.info("cleanup leftovers: freed %d bytes", bytes_freed)
        except Exception:
            log.exception("cleanup loop failed")
        await asyncio.sleep(CLEANUP_INTERVAL_SECONDS)


async def stop_media_server():
    global media_runner, media_site
    try:
        if media_site:
            await media_site.stop()
        if media_runner:
            await media_runner.cleanup()
    finally:
        media_site = None
        media_runner = None


async def _convert_audio_to_mp3(local_path):
    """تحويل الصوت إلى MP3، وهو الأكثر توافقاً مع مشغل الصوت في تطبيقات الدردشة."""
    if local_path is None or local_path.suffix.lower() == ".mp3":
        return local_path, None
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        log.warning("ffmpeg غير مثبت؛ سيتم استخدام الملف الأصلي %s", local_path.suffix)
        return local_path, None

    out = local_path.with_suffix(".mp3")

    def convert():
        cmd = [
            ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
            "-i", str(local_path), "-vn",
            "-ac", "2", "-ar", "44100",
            "-codec:a", "libmp3lame", "-b:a", "128k",
            str(out),
        ]
        subprocess.run(cmd, check=True, timeout=180)

    try:
        await asyncio.to_thread(convert)
        if out.is_file() and out.stat().st_size > 4096:
            try:
                local_path.unlink(missing_ok=True)
            except Exception:
                pass
            return out, None
        return local_path, "فشل تحويل الصوت إلى MP3"
    except Exception as e:
        log.warning("ffmpeg conversion failed: %s", e)
        return local_path, None


async def _prepare_music_track(track, source_label):
    if not track:
        return None, "لم أجد المقطع المطلوب"
    if MUSIC_MAX_DURATION and float(track.get("duration") or 0) > MUSIC_MAX_DURATION:
        return None, f"مدة الأغنية طويلة جداً (الحد {MUSIC_MAX_DURATION // 60} دقيقة)."
    page_url = track.get("youtube_url")
    if not page_url:
        return None, "تعذر الحصول على رابط الصفحة الأصلية للمقطع"

    local_path, err = await _yt_download_audio(page_url, source_label, track.get("piped_api"), track.get("id"))
    if err:
        return None, err
    try:
        local_path, convert_err = await _convert_audio_to_mp3(local_path)
        if convert_err:
            log.warning(convert_err)
        audio_url = await _store_media(local_path, "music")
        track["audio_url"] = audio_url
        # MP3/WebM/M4A يحدد نوع الملف الذي أرسلناه، وvoice هو نوع رسالة Giant Chat.
        track["media_format"] = local_path.suffix.lower().lstrip(".")
        return track, None
    finally:
        try:
            shutil.rmtree(local_path.parent, ignore_errors=True)
        except Exception:
            pass


async def search_spotify(query):
    """Resolve a Spotify track to metadata, then use a public YouTube copy for
    the actual audio bytes. Spotify itself does not expose downloadable audio."""
    q = str(query or "").strip()
    if not q:
        return None, "اكتب اسم الأغنية بعد .تشغيل"

    spotify_url = None
    if re.match(r"https?://open\.spotify\.com/(?:intl-[^/]+/)?track/[A-Za-z0-9]+", q):
        spotify_url = q
    else:
        # Discover a public Spotify track URL through search engines.
        headers = {"User-Agent": "Mozilla/5.0"}
        for engine, params in (
            ("https://www.google.com/search", {"q": f'site:open.spotify.com/track "{q}"'}),
            ("https://www.bing.com/search", {"q": f'site:open.spotify.com/track "{q}"'}),
        ):
            try:
                async with http.get(engine, params=params, headers=headers,
                                    timeout=aiohttp.ClientTimeout(total=12)) as resp:
                    if resp.status != 200:
                        continue
                    html = await resp.text(errors="ignore")
                urls = re.findall(r'https?://open\.spotify\.com/(?:intl-[^/]+/)?track/[A-Za-z0-9]+', html)
                if urls:
                    spotify_url = urls[0].split("&")[0]
                    break
            except Exception as e:
                log.warning("Spotify discovery failed: %s", e)

    title = q
    artist = "Spotify"
    if spotify_url:
        try:
            async with http.get(
                "https://open.spotify.com/oembed",
                params={"url": spotify_url},
                headers={"User-Agent": "Mozilla/5.0"},
                timeout=aiohttp.ClientTimeout(total=12),
            ) as resp:
                if resp.status == 200:
                    data = await resp.json(content_type=None)
                    title = data.get("title") or title
                    artist = data.get("author_name") or artist
        except Exception as e:
            log.warning("Spotify oEmbed failed: %s", e)

    # Spotify supplies metadata/link; audio is obtained from a playable public copy.
    track = None
    spotify_queries = [f"{title} {artist}", f"{title} {artist} audio", f"{title} {artist} official"]
    for sq in spotify_queries:
        try:
            track, _search_err = await _yt_extract(sq)
            if track:
                break
        except Exception as e:
            log.warning("Spotify->YouTube search failed (%s): %s", sq, e)
    if not track:
        # Keep the Spotify URL so the room can still open it without downloading.
        if spotify_url:
            return {
                "title": title,
                "artist": artist,
                "spotify_url": spotify_url,
                "source": "Spotify",
                "youtube_url": None,
                "audio_url": None,
            }, None
        return None, "تعذر العثور على نسخة صوتية للمقطع من Spotify، ولم يوجد رابط Spotify مباشر."
    track["spotify_url"] = spotify_url
    track["spotify_title"] = title
    track["spotify_artist"] = artist
    track["source"] = "Spotify"
    return track, None


async def _extract_direct_media_url(url, source_label):
    """Extract metadata from a direct YouTube/TikTok media page URL."""
    u = str(url or "").strip()
    if not re.match(r"^https?://", u, re.I):
        return None, "الرابط غير صالح"
    if yt_dlp is None:
        return None, "مكتبة yt-dlp غير مثبتة."

    def extract():
        options = yt_base_options(source_label)
        options.update({"skip_download": True, "format": "bestaudio/best"})
        with yt_dlp.YoutubeDL(options) as ydl:
            info = ydl.extract_info(u, download=False)
        if not info:
            return None
        return {
            "id": info.get("id"),
            "title": info.get("title") or "المقطع",
            "artist": info.get("uploader") or info.get("creator") or source_label,
            "youtube_url": info.get("webpage_url") if source_label == "YouTube" else None,
            "tiktok_url": info.get("webpage_url") if source_label == "TikTok" else None,
            "thumbnail": info.get("thumbnail"),
            "duration": info.get("duration") or 0,
            "source": source_label,
        }
    try:
        return await asyncio.to_thread(extract), None
    except Exception as e:
        return None, f"{type(e).__name__}: {e}"

async def search_track(query):
    """YouTube search with several query variants. Returns the direct YouTube URL
    even when audio download later fails, so the client can open/play it."""
    q = str(query or "").strip()
    if not q:
        return None, "اكتب اسم الأغنية بعد تشغيل"
    # تشغيل رابط YouTube مباشرة: لا نبحث عنه كنص.
    if re.match(r"^https?://(?:www\.)?(?:youtube\.com|youtu\.be)/", q, re.I):
        return await _extract_direct_media_url(q, "YouTube")
    # دعم وضع الرابط مع الأمر تشغيل أيضاً إذا كان رابط TikTok.
    if re.match(r"^https?://(?:(?:www\.)?tiktok\.com|vm\.tiktok\.com|vt\.tiktok\.com)/", q, re.I):
        return await _extract_direct_media_url(q, "TikTok")
    variants = [
        q,
        f"{q} official",
        f"{q} audio",
        f"{q} lyrics",
    ]
    errors = []
    for variant in variants:
        try:
            track, search_err = await _yt_extract(variant)
            if track and track.get("youtube_url"):
                track["source"] = "YouTube"
                track["search_query"] = variant
                return track, None
            if search_err:
                errors.append(f"{variant}: {search_err}")
        except Exception as e:
            errors.append(f"{variant}: {type(e).__name__}: {e}")
            log.warning("youtube search error (%s): %s", variant, e)
    detail = " | ".join(errors[-4:]) if errors else "لا توجد نتائج من مصادر البحث"
    return None, f"لم أجد الأغنية المطلوبة على يوتيوب. تفاصيل الاتصال/البحث: {detail}"


async def search_tiktok(query):
    """Find a TikTok video. Direct TikTok URLs are preferred. For text search,
    use search engines to discover a public TikTok URL, then yt-dlp extracts audio."""
    if yt_dlp is None:
        return None, "مكتبة yt-dlp غير مثبتة."
    try:
        direct_url = query.strip()
        urls = []
        if direct_url.startswith(("https://www.tiktok.com/", "https://tiktok.com/", "https://vm.tiktok.com/", "https://vt.tiktok.com/")):
            urls = [direct_url]
        if not urls:
            headers = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/126 Safari/537.36"}
            # Try TikTok itself first.
            for search_url in (
                "https://www.tiktok.com/search",
                "https://www.google.com/search",
                "https://www.bing.com/search",
            ):
                try:
                    params = {"q": query if "tiktok.com" in search_url else f'site:tiktok.com "{query}"'}
                    async with http.get(search_url, params=params, headers=headers, timeout=aiohttp.ClientTimeout(total=12)) as resp:
                        if resp.status != 200:
                            continue
                        html = await resp.text(errors="ignore")
                    pattern = r'https?://(?:www\.)?tiktok\.com/@[^"\\ <]+/video/\d+'
                    urls = re.findall(pattern, html)
                    if urls:
                        break
                except Exception as e:
                    log.warning("TikTok search source failed %s: %s", search_url, e)
        if not urls:
            return None, "لم أجد فيديو TikTok. إذا كان لديك رابط TikTok أرسله بعد «تيك»."

        def extract():
            options = yt_base_options("TikTok")
            options.update({"skip_download": True, "format": "bestaudio/best"})
            info = None
            with yt_dlp.YoutubeDL(options) as ydl:
                info = ydl.extract_info(urls[0], download=False)
            return {
                "id": info.get("id"), "title": info.get("title") or query,
                "artist": info.get("uploader") or info.get("creator") or "TikTok",
                "youtube_url": info.get("webpage_url") or urls[0],
                "tiktok_url": info.get("webpage_url") or urls[0],
                "thumbnail": info.get("thumbnail"), "duration": info.get("duration") or 0,
            }
        track = await asyncio.to_thread(extract)
        return (track, None) if track else (None, "تعذر استخراج فيديو TikTok")
    except Exception as e:
        log.warning("tiktok search error: %s", e)
        return None, "تعذر الوصول إلى TikTok من الخادم. إذا كان الخادم PythonAnywhere المجاني فلن تعمل هذه الميزة بسبب قيود الإنترنت الخارجية."


async def render_music_card(track, requester_name, source_room):
    """بطاقة أغنية بنفس فكرة بطاقات بوت سهم: صورة كبيرة + معلومات الطلب والتفاعل."""
    if not PIL_AVAILABLE:
        return None
    outdir = BASE_DIR / "generated_music_cards"
    outdir.mkdir(parents=True, exist_ok=True)
    canvas = Image.new("RGB", (900, 980), (245, 247, 250))
    d = ImageDraw.Draw(canvas)
    thumb = None
    thumb_url = track.get("thumbnail")
    if thumb_url:
        try:
            async with http.get(thumb_url, timeout=aiohttp.ClientTimeout(total=12), headers={"User-Agent":"Mozilla/5.0"}) as resp:
                if resp.status == 200:
                    data = await resp.read()
                    import io
                    thumb = Image.open(io.BytesIO(data)).convert("RGB")
        except Exception:
            thumb = None
    if thumb is None:
        thumb = Image.new("RGB", (900, 560), (28, 42, 65))
        td = ImageDraw.Draw(thumb)
        td.text((450, 280), "🎵 GENAT CHAT", fill=(255,255,255), anchor="mm")
    thumb.thumbnail((860, 570), Image.LANCZOS)
    canvas.paste(thumb, ((900-thumb.width)//2, 20))
    font_path = BASE_DIR / "assets" / "Amiri-Bold.ttf"
    try:
        f_title=ImageFont.truetype(str(font_path), 42); f_line=ImageFont.truetype(str(font_path), 31); f_small=ImageFont.truetype(str(font_path), 26)
    except Exception:
        f_title=f_line=f_small=ImageFont.load_default()
    d.rounded_rectangle((55, 620, 845, 940), radius=28, fill=(255,255,255), outline=(215,218,223), width=3)
    _draw_game_text(d, (450, 665), "🎵 تشغيل | أغنية", f_title, fill=(35,35,45))
    _draw_game_text(d, (450, 720), str(track.get("title") or "الأغنية"), f_line, fill=(45,45,45))
    _draw_game_text(d, (450, 770), f"👤 الطلب بواسطة: @{requester_name}", f_small, fill=(65,65,65))
    _draw_game_text(d, (450, 815), f"🏠 الغرفة: {source_room}", f_small, fill=(65,65,65))
    _draw_game_text(d, (450, 870), "❤️ إعجاب   👎 عدم إعجاب   💖 أحببته   💬 تعليق", f_small, fill=(65,65,65))
    _draw_game_text(d, (450, 915), "▶️ اضغط تشغيل من مشغل الصوت", f_small, fill=(65,65,65))
    path=outdir/f"music_{uuid.uuid4().hex}.jpg"
    canvas.save(path, quality=92, optimize=True)
    return path

async def play_track(rid, track, source_label, requester_id, requester_name, local_only=False):
    if not track:
        return False, "لم أجد المقطع المطلوب"
    source_room = rooms.get(rid, "الغرفة")
    track, err = await _prepare_music_track(track, source_label)
    if err:
        return False, err
    track.update({"requester_id": str(requester_id), "requester_name": requester_name, "source_room": source_room})
    music_state[rid] = track
    title = track.get("title", "المقطع")
    artist = track.get("artist", source_label)
    media_url = track.get("audio_url")
    if not media_url:
        direct_url = track.get("youtube_url") or track.get("spotify_url") or track.get("tiktok_url")
        if direct_url:
            await room_send(rid, f"🎵 @{requester_name} — جاري تشغيل: {title}\n🏠 الغرفة: {source_room}\n▶️ {direct_url}")
            return True, None
        return False, "تم الوصول للنتيجة لكن لم يتم إنشاء ملف صوتي ولا رابط تشغيل مباشر."

    # تسجيل الأغنية كمنشور بدون إنشاء صورة للأغنية، حتى تبقى التفاعلات
    # (إعجاب/حب/تعليق) مرتبطة بصاحب الطلب عبر post_id.
    post_id = str(uuid.uuid4())
    posts = load_published_posts()
    posts[post_id] = {
        "post_id": post_id, "owner_id": str(requester_id), "owner_name": requester_name,
        "source_room_id": str(rid), "type": "music", "title": title,
        "media_url": media_url, "audio_url": media_url, "created_at": now_iso()
    }
    save_published_posts(posts)

    # كود مختصر (3 رموز) للتفاعل عبر أزرار نصية مثل Like@A7K
    code = _post_code()
    _POST_CODES[code] = post_id

    # عرض الأغنية: نص بسيط بالمعلومات + رسالة صوتية
    # يمكن تخصيص القالب من خاص البوت بأمر "رسالة أغنية"
    custom_tmpl = MUSIC_CARD_TEMPLATE.get("custom", "").strip()
    if custom_tmpl:
        caption = custom_tmpl.replace("{title}", title).replace("{artist}", artist) \
                             .replace("{name}", requester_name) \
                             .replace("{room}", source_room) \
                             .replace("{code}", code)
    else:
        caption = (
            f"🎵 {title} — {artist}\n"
            f"👤 @{requester_name} • 🏠 {source_room}\n"
            f"❤️ Like@{code} 👎 Dislike@{code}\n"
            f"💖 Love@{code} 💬 Comment@{code}"
        )
    targets = [rid] if local_only else await all_room_ids()
    sent_count = 0
    failed_rooms = []
    for target_rid in targets:
        duration_ms = int(float(track.get("duration") or 0) * 1000)
        try:
            # إرسال الرسالة الصوتية أولاً (تظهر فوق)
            await room_send_media(
                target_rid,
                caption,
                media_url, m_type="voice", duration_ms=duration_ms,
            )
            # ثم إرسال النص تحتها
            await room_send(target_rid, caption)
            sent_count += 1
        except Exception as exc:
            log.exception("music broadcast failed room=%s", target_rid)
            failed_rooms.append(target_rid)
            await report_music_error_to_masters(
                target_rid, source_label, title,
                f"{type(exc).__name__}: {exc}",
                stage="إرسال تفاصيل/رسالة الصوت إلى الغرف"
            )
    if not local_only:
        log.info("music broadcast: sent=%d, total_targets=%d, failed=%s", sent_count, len(targets), failed_rooms)
        await room_send(rid, f"✅ تم نشر الأغنية في {sent_count} غرفة من {len(targets)}.")
    return True, None

def friendly_music_error(error):
    """رسالة مفهومة للمستخدم، مع إبقاء الخطأ الخام للماستر."""
    e = str(error or "").lower()
    if "the page needs to be reloaded" in e:
        return "❌ يوتيوب أعاد «The page needs to be reloaded» مع كل العملاء (default, web_embedded, tv, tvos, mweb) وبكل المحاولات. السبب: جلسة YouTube في الكوكيز منتهية أو محظورة. الحل: حدّث cookies.txt بملف جديد من متصفحك (اذهب youtube.com وسجّل دخول ثم صدّر الكوكيز من Chrome DevTools أو إضافة Get cookies.txt)."
    if any(x in e for x in ("sign in to confirm", "not a bot", "captcha", "botguard", "po token", "http error 403", "403 forbidden")):
        return "❌ اتصلت بيوتيوب، لكن يوتيوب رفض الوصول/تحميل الصوت. السبب: تحقق/حظر جلسة YouTube أو PO Token أو Cookies غير صالحة."
    if any(x in e for x in ("clientconnectorerror", "cannot connect", "connection refused", "name or service not known", "temporary failure in name resolution", "timeout", "timed out")):
        return "❌ لم أستطع التواصل مع يوتيوب من خادم Railway. فشل اتصال الشبكة قبل تحميل الأغنية."
    if "لم يُرجع نتائج" in e or "no results" in e:
        return "❌ تم الاتصال بمصدر البحث، لكن يوتيوب لم يُرجع نتيجة مطابقة للأغنية المطلوبة."
    if "ffmpeg" in e or "تحويل الصوت" in e:
        return "❌ تم الحصول على الصوت، لكن فشل تحويله إلى MP3 بواسطة FFmpeg."
    if "public_base_url" in e or "رابط عام" in e or "/media/" in e:
        return "❌ تم تجهيز الأغنية، لكن تعذر إنشاء رابط عام لملف الصوت. تحقق من PUBLIC_BASE_URL وPublic Domain في Railway."
    if "room_messages" in e or "message_type" in e or "voice" in e:
        return "❌ تم تجهيز ملف الصوت، لكن فشل إرسال رسالة الصوت إلى جينات شات."
    return f"❌ تعذر تشغيل الأغنية. السبب: {str(error)[:700]}"


async def music_worker_queue():
    global last_music_started
    interval = max(0, int(C.get("music_interval_seconds", 0)))
    while True:
        item = await music_queue.get()
        if len(item) == 5:
            rid, query, source, requester_id, requester_name = item
            local_only = False
        else:
            rid, query, source, requester_id, requester_name, local_only = item
        try:
            wait = interval - (time.time() - last_music_started)
            if wait > 0:
                await asyncio.sleep(wait)
            if rid not in rooms:
                continue

            last_music_started = time.time()
            if source == "TikTok":
                track, err = await search_tiktok(query)
            elif source == "Spotify":
                track, err = await search_spotify(query)
            else:
                track, err = await search_track(query)

            used_source = source
            if err and source in ("YouTube", "Spotify"):
                # مصدر احتياطي: إذا فشل يوتيوب/سبوتيفاي نجرب TikTok الذي يعمل على Railway.
                await report_music_error_to_masters(rid, source, query, err, stage="البحث")
                alt_track, alt_err = await search_tiktok(query)
                if alt_track and not alt_err:
                    track, err, used_source = alt_track, None, "TikTok"

            if err:
                await room_send(rid, friendly_music_error(err))
                await report_music_error_to_masters(rid, source, query, err, stage="البحث/الاتصال")
            else:
                ok, out = await play_track(rid, track, used_source, requester_id, requester_name, local_only)
                if not ok and out:
                    await room_send(rid, friendly_music_error(out))
                    await report_music_error_to_masters(rid, used_source, query, out, stage="التنزيل/التجهيز/الإرسال")
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.exception("music queue worker failed")
            try:
                detail = f"{type(exc).__name__}: {exc}"
                await room_send(rid, friendly_music_error(detail))
                await report_music_error_to_masters(rid, source, query, detail, stage="استثناء غير متوقع")
            except Exception:
                pass
        finally:
            music_queue.task_done()


async def cancel_music_task(rid):
    task = music_tasks.pop(rid, None)
    if task and not task.done():
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


async def skip(rid):
    await cancel_music_task(rid)
    music_state.pop(rid, None)
    return True, "⏭️ تم التخطي بواسطة البوت"


async def stop(rid):
    await cancel_music_task(rid)
    music_state.pop(rid, None)
    return True, "⏹️ تم إيقاف الأغنية بواسطة البوت"

# ----------------------------- أوامر الغرفة -----------------------------
HELP_GAMES = """━━━━━━━━ 🎮 أوامر الألعاب ━━━━━━━━
⚔️ حرب — يبدأ/ينضم للعبة الحرب، ثم اكتب رقماً من 1 إلى 6
🖐️ كف — تحدي كف
🥊 قتال — قتال سريع
🏁 سباق — سباق
💰 رشوة — رشوة
🏀 سلة — كرة سلة
💣 قصف — قصف
🐸 اضرب — اضرب الضفدع
🃏 ورق — ورق
⚽ سدد — تسديد
🥊 ملاكمة — ملاكمة
💼 عمل — وظيفة
🌋 بركان — بركان
👻 شبح — صيد الشبح
🎲 مضاربة رقم — مراهنة
🎲 حظ / نرد / تعدين / زواج
━━━━━━━━━━━━━━━━━━━━
كل لعبة ترسل الصورة ثم تفاصيلها كنص فقط.
🔒 الألعاب تحتاج توثيق VIP من صاحب البوت عبر vip@اسم_المستخدم.
"""

HELP_ROOM = """━━━━━━━━ 🤖 جميع أوامر البوت ━━━━━━━━
[1] الحساب والنقاط
points / نقاطي — عرض نقاطك
توب — أفضل 10 لاعبين
dp@الاسم — صورة المستخدم
p@الاسم — البروفايل
st@الاسم — حالة المستخدم

[2] الموسيقى
🔒 تشغيل/مشاركة الأغاني تحتاج توثيق VIP من صاحب البوت.
تشغيل اسم الأغنية — YouTube
تيك اسم الأغنية — TikTok
.تشغيل اسم الأغنية — Spotify (يبحث عن النسخة الصوتية)
مشاركة — مشاركة الأغنية الحالية
تخطي — تخطي الأغنية
ايقاف — إيقاف الصوت

[3] الألعاب
العاب — عرض أوامر الألعاب
""" + HELP_GAMES + """

[4] الرتب والإدارة
o@الاسم — مالك
m@الاسم — عضوية
n@الاسم — إزالة رتبة
a@الاسم — إشراف
mas@الاسم — ماستر
umas@الاسم — إزالة ماستر
المسترات — قائمة الماسترات
k@الاسم — طرد
b@الاسم — حظر
ip@الاسم — حظر IP

[5] الهدايا
🔐 توثيق VIP: vip@اسم_المستخدم (صاحب البوت فقط)
gv — عرض الهدايا
gv@رقم_الهدية@اسم_الحساب — إرسال هدية

[6] الترحيب والردود
+wc رسالة — إضافة ترحيب
+wc رسالة %id% — ترحيب مع الاسم
clear@wc — حذف الترحيبات
l@wc — عرض الترحيبات
wc@on / wc@off — تفعيل/تعطيل
+r@كلمة@رد — إضافة رد

[7] فلتر الكلمات
mf@on — تشغيل الفلتر
mf@off — إيقاف الفلتر
+mf@كلمة — إضافة كلمة ممنوعة
-mf@كلمة — إزالة كلمة
l@mf — عرض الكلمات
clear@mf — حذف الكلمات

[8] النشر — للماستر
نشر نص — نشر النص في جميع الغرف
نشر@ — اطلب الصورة ثم أرسلها، وسيتم نشرها في جميع الغرف
نشرصورة رابط — نشر صورة برابط

[9] اللغة
lang@ar — العربية
lang@en — English

.help / help — عرض جميع الأوامر
.more / .next — عرض القائمة التالية
━━━━━━━━━━━━━━━━━━━━"""



async def _draw_game_text(draw, xy, text, font, fill=(30,30,30), anchor="ma"):
    """رسم عربي بشكل صحيح عندما تتوفر arabic_reshaper/python-bidi."""
    text = str(text)
    if arabic_reshaper and get_display:
        try:
            text = get_display(arabic_reshaper.reshape(text))
        except Exception:
            pass
    draw.text(xy, text, font=font, fill=fill, anchor=anchor)


def render_game_card_sync(game_key, title, lines):
    """إنشاء صورة اللعبة فقط.

    تفاصيل النتيجة لا تُرسم داخل الصورة؛ تُرسل كنص مستقل بعد الصورة حتى تبقى
    صور الألعاب كما هي في مجلد assets، وبنفس الأسلوب الذي طلبه المستخدم.
    """
    if not PIL_AVAILABLE:
        return None

    local_map = {
        "slap": "assets/slap_action.jpg", "war": "assets/war_game.png",
        "fight": "assets/fight_action.jpg", "boxing": "assets/defense_action.jpg"
    }
    generated = BASE_DIR / "assets" / f"game_{game_key}.jpg"
    src = BASE_DIR / local_map.get(game_key, f"assets/game_{game_key}.jpg")
    if generated.is_file() and game_key not in local_map:
        src = generated

    try:
        if src.is_file():
            im = Image.open(src).convert("RGB")
        else:
            im = Image.new("RGB", (900, 560), (240, 243, 247))
    except Exception:
        im = Image.new("RGB", (900, 560), (240, 243, 247))

    im.thumbnail((900, 700), Image.LANCZOS)
    canvas = Image.new("RGB", (900, im.height), (245, 247, 250))
    canvas.paste(im, ((900 - im.width) // 2, 0))

    outdir = BASE_DIR / "generated_games"
    outdir.mkdir(exist_ok=True)
    path = outdir / f"game_{game_key}_{uuid.uuid4().hex}.jpg"
    canvas.save(path, quality=92, optimize=True)
    return path


async def send_game_card(rid, game_key, title, lines, fallback_text=None):
    """أرسل صورة اللعبة أولاً ثم تفاصيلها كنص مستقل."""
    path = await asyncio.to_thread(render_game_card_sync, game_key, title, lines)
    if path:
        try:
            url = await _store_media(path, "game", "image/jpeg")
            # الصورة وحدها: لا نضع اسم اللعبة أو النتيجة داخل الصورة.
            await room_send_media(rid, "", url, m_type="image")
            try:
                path.unlink(missing_ok=True)
            except Exception:
                pass
            # التفاصيل بعد الصورة مباشرة، كنص قابل للقراءة والنسخ.
            details = "\n".join(lines)
            if details:
                await room_send(rid, f"{title}\n{details}")
            return
        except Exception as exc:
            log.warning("game card upload failed: %s", exc)
    if fallback_text:
        await room_send(rid, fallback_text)


async def _war_board_message(rid, game, target_player_uid):
    """إرسال لوحة الخصم (Opponent's Board) لمن عليه الدور."""
    if not game or not game.get("p2"):
        return
    # اسم الخصم = الآخر
    if target_player_uid == game["p1"]:
        opponent_uid = game["p2"]
        opponent_name = game["p2_name"]
    else:
        opponent_uid = game["p1"]
        opponent_name = game["p1_name"]

    # الأرقام 1..6 مع علامة ❌ على التي جربها اللاعب الحالي
    tried = set(game["guesses"].get(str(target_player_uid), []))
    board_cells = [f"❌{x}" if x in tried else str(x) for x in range(1, 7)]
    board_line = " | ".join(board_cells)

    skey = str(target_player_uid)
    tries_used = game["tries"].get(skey, 0)
    remaining = max(0, 3 - tries_used)

    board_text = (
        f"🎯 لوحة خصمك | Opponent's Board\n"
        f"👤 @{opponent_name}\n"
        f"━━━━━━━━━━━━━\n"
        f"{board_line}\n"
        f"━━━━━━━━━━━━━\n"
        f"⚡ محاولاتك المتبقية: {remaining}\n"
        f"🔢 اختر رقمًا (1-6)"
    )
    try:
        # إرسال صورة الحرب ثم نص اللوحة
        await room_send_media(rid, "", GAME_IMAGES["war"], m_type="image")
        await room_send(rid, board_text)
    except Exception as exc:
        log.warning("war board message failed: %s", exc)
        await room_send(rid, board_text)


async def handle_room(rid, text, uid, media_url=None, message_type=None):
    if await is_banned(rid, uid): return None
    p_name = await username_of(uid)
    # معالجة أزرار التفاعل النصية: Like@CODE / Dislike@CODE / Love@CODE / loved@CODE / Comment@CODE / msg@CODE / report@CODE
    interact_reply = await _handle_post_interaction(rid, text.strip(), uid, p_name)
    if interact_reply is not None:
        return interact_reply
    lower_text = text.strip().lower()

    admin_prefixes = ("+mf@", "-mf@", "clear@mf", "l@mf", "mf@on", "mf@off",
                      "+wc ", "clear@wc", "l@wc", "wc@on", "wc@off", "mas@")
    if not lower_text.startswith(admin_prefixes):
        blocked = await check_forbidden_word(rid, text)
        if blocked:
            return blocked

    replies = load_replies()
    if text.strip() in replies: return replies[text.strip()]

    if text.startswith("نشر ") or text.startswith("broadcast "):
        if not await is_master(uid, p_name): return "🚫 للماستر فقط."
        err = None
        if vip_error: return vip_error
        msg = text.split(maxsplit=1)[1].strip()
        await broadcast_text("📢 " + msg)
        return "✅ تم نشر الرسالة في كل الغرف."
    if text.startswith("نشرصورة ") or text.startswith("broadcast_image "):
        if not await is_master(uid, p_name): return "🚫 للماستر فقط."
        err = None
        if vip_error: return vip_error
        url = text.split(maxsplit=1)[1].strip()
        await broadcast_media("📢", url, m_type="image")
        return "✅ تم نشر الصورة في كل الغرف."

    # نشر@: الماستر يطلب صورة في رسالة لاحقة، ثم ينشرها في كل الغرف.
    # يدعم الوصف: نشر@ الوصف، أو نشر@ بلا وصف.
    publish_key = (rid, uid)
    if lower_text.strip() in ("نشر@", "publish@") or text.startswith("نشر@ ") or text.startswith("publish@ "):
        if not await is_master(uid, p_name):
            return "🚫 للماستر فقط."
        err = None
        if vip_error: return vip_error
        desc = ""
        base = lower_text.strip()
        if base.startswith(("نشر@ ", "publish@ ")):
            rest = text.split(maxsplit=1)[1]  # كل ما بعد نشر@
            parts = rest.split(" ", 1)
            desc = parts[1].strip() if len(parts) > 1 else ""  # كل ما بعد أول مسافة
        publish_pending[publish_key] = {"at": time.time(), "desc": desc}
        if desc:
            return f"🖼️ أرسل الصورة الآن خلال دقيقتين، وسيتم نشرها في كل الغرف مع الوصف: {desc}"
        return "🖼️ أرسل الصورة الآن خلال دقيقتين، وسيتم نشرها في كل الغرف مع اسم الغرفة وخيارات التفاعل."

    async def cache_publish_media(source_url):
        """Copy an incoming image to this bot's public storage so it remains
        accessible after the original message URL expires."""
        if not source_url:
            return None
        temp_dir = Path(tempfile.mkdtemp(prefix="bot_publish_"))
        try:
            suffix = ".jpg"
            low = str(source_url).lower()
            for ext in (".png", ".jpg", ".jpeg", ".webp", ".gif"):
                if ext in low:
                    suffix = ext
                    break
            local = temp_dir / f"image{suffix}"
            async with http.get(
                source_url,
                timeout=aiohttp.ClientTimeout(total=45),
                headers={"User-Agent": "Mozilla/5.0"},
            ) as resp:
                if resp.status != 200:
                    return None
                with local.open("wb") as f:
                    async for chunk in resp.content.iter_chunked(1024 * 256):
                        f.write(chunk)
            if local.stat().st_size < 512:
                return None
            return await _store_media(
                local,
                "publish",
                {"jpg":"image/jpeg","jpeg":"image/jpeg","png":"image/png","webp":"image/webp","gif":"image/gif"}.get(suffix.lstrip("."), "image/jpeg")
            )
        except Exception as e:
            log.warning("publish image cache failed: %s", e)
            return None
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    pending_info = publish_pending.get(publish_key)
    if pending_info is not None:
        pending_at = pending_info.get("at") if isinstance(pending_info, dict) else pending_info
        if time.time() - (pending_at or 0) > 120:
            publish_pending.pop(publish_key, None)
        elif message_type in ("image", "photo", "sticker") and media_url:
            publish_pending.pop(publish_key, None)
            source_room = rooms.get(rid, "الغرفة")
            # Re-host the image on the bot's public Railway endpoint when possible.
            public_media_url = await cache_publish_media(media_url) or media_url
            pending_info = publish_pending.get(publish_key) or {}
            desc = (pending_info.get("desc") or "").strip()
            post_id = str(uuid.uuid4())
            posts = load_published_posts()
            posts[post_id] = {"post_id": post_id, "owner_id": str(uid), "owner_name": p_name, "source_room_id": str(rid), "type": "publish", "title": desc or "صورة منشورة", "media_url": public_media_url, "created_at": now_iso()}
            save_published_posts(posts)
            # كود مختصر للتفاعل عبر أزرار نصية مثل Like@BFAA
            code = _post_code()
            _POST_CODES[code] = post_id
            published = 0
            for target_rid in await all_room_ids():
                try:
                    target_name = rooms.get(target_rid, "الغرفة")
                    caption = (
                        f"✨════════════✨\n"
                        f"🖼️ منشور الصورة من:\n"
                        f"👤 {p_name}\n"
                        f"📝 الوصف: {desc or 'بدون وصف'}\n"
                        f"🏠 الغرفة: {source_room}\n"
                        f"🆔 {code}\n"
                        f"✨════════════✨\n"
                        f"👍 Like@{code} | ❤ loved@{code}\n"
                        f"👎 Dislike@{code}\n"
                        f"✉️ msg@{code} [رسالة] | 🚨 report@{code} [سبب]"
                    )
                    await room_send_media(target_rid, caption, public_media_url, m_type="image")
                    published += 1
                except Exception:
                    log.exception("publish@ failed for room %s", target_rid)
            return f"✅ تم نشر الصورة في {published} غرفة بالكود {code}."
        elif media_url:
            return "⚠️ الملف المرسل ليس صورة. أرسل صورة بعد أمر نشر@."

    if text == "المسترات":
        masters = load_masters()
        return "👑 قائمة الماسترز:\n" + "\n".join([f"• @{m}" for m in masters]) if masters else "👤 المالك فقط هو الماستر حالياً."

    # توثيق VIP للأغاني والألعاب: المالك فقط يملك أمر vip@.
    if lower_text.startswith("vip@"): 
        if str(p_name).strip().lower() != OWNER:
            return "🚫 توثيق VIP متاح لصاحب البوت فقط."
        target = text.split("@", 1)[1].strip()
        ok, msg = await grant_vip_by_username(target)
        return msg

    if lower_text.startswith("unvip@"): 
        if str(p_name).strip().lower() != OWNER:
            return "🚫 إزالة توثيق VIP متاحة لصاحب البوت فقط."
        target = text.split("@", 1)[1].strip().lower().lstrip("@")
        data = load_vip_users()
        removed = []
        for key, item in list(data.items()):
            name = item.get("username", "") if isinstance(item, dict) else str(item)
            if key.lower() == target or str(name).lower() == target:
                removed.append(name or key)
                data.pop(key, None)
        save_vip_users(data)
        return (f"✅ تم إلغاء توثيق @{removed[0] if removed else target}." if removed else f"⚠️ @{target} غير موثّق VIP.")

    if lower_text in ("vips", "vip", "الموثقين"):
        if str(p_name).strip().lower() != OWNER:
            return "🚫 قائمة VIP لصاحب البوت فقط."
        data = load_vip_users()
        names = []
        for item in data.values():
            if isinstance(item, dict):
                names.append(str(item.get("username") or item.get("id") or ""))
            else:
                names.append(str(item))
        return "👑 موثقو VIP:\n" + ("\n".join(f"• @{n}" for n in names if n) if names else "لا يوجد موثقون.")

    if text.startswith("mas@"):
        if not await is_master(uid, p_name): return "🚫 للماستر فقط."
        target = text.replace("mas@", "").strip()
        masters = load_masters()
        if target not in masters:
            masters.append(target); save_masters(masters)
            return f"✅ تم إضافة @{target} كـ ماستر."
        return f"⚠️ @{target} ماستر بالفعل."

    if text.startswith("+r@"):
        if not await is_master(uid, p_name): return "🚫 للماستر فقط."
        parts = text.split("@")
        if len(parts) >= 3:
            replies[parts[1].strip()] = parts[2].strip(); save_replies(replies)
            return f"✅ تم إضافة الرد لـ: {parts[1].strip()}"
        return "❌ الصيغة: +r@الكلمة@الرد"

    # ---------------- فلتر الكلمات الممنوعة ----------------
    if lower_text in ("mf@on", "mf on"):
        if not await is_master(uid, p_name): return "🚫 للماستر فقط."
        mod = load_moderation(); mod.setdefault("enabled", {})[str(rid)] = True; save_moderation(mod)
        return "✅ تم تفعيل فلتر الألفاظ في هذه الغرفة."
    if lower_text in ("mf@off", "mf off"):
        if not await is_master(uid, p_name): return "🚫 للماستر فقط."
        mod = load_moderation(); mod.setdefault("enabled", {})[str(rid)] = False; save_moderation(mod)
        return "⛔ تم تعطيل فلتر الألفاظ في هذه الغرفة."
    if lower_text == "clear@mf":
        if not await is_master(uid, p_name): return "🚫 للماستر فقط."
        mod = load_moderation(); mod["words"] = []; save_moderation(mod)
        return "🧹 تم حذف جميع الكلمات الممنوعة."
    if lower_text == "l@mf":
        if not await is_master(uid, p_name): return "🚫 للماستر فقط."
        words = load_moderation().get("words", [])
        return "🚫 الكلمات الممنوعة:\n" + ("\n".join(f"{i+1}. {w}" for i,w in enumerate(words)) if words else "لا توجد كلمات.")
    if lower_text.startswith("+mf@"):
        if not await is_master(uid, p_name): return "🚫 للماستر فقط."
        word = text.split("@", 1)[1].strip()
        if not word: return "❌ الصيغة: +mf@كلمة"
        mod = load_moderation(); words = mod.setdefault("words", [])
        if word not in words: words.append(word)
        save_moderation(mod)
        return f"✅ تمت إضافة الكلمة الممنوعة: {word}"
    if lower_text.startswith("-mf@"):
        if not await is_master(uid, p_name): return "🚫 للماستر فقط."
        word = text.split("@", 1)[1].strip()
        mod = load_moderation(); mod["words"] = [w for w in mod.get("words", []) if normalize_text(w) != normalize_text(word)]
        save_moderation(mod)
        return f"✅ تمت إزالة الكلمة: {word}"

    # ---------------- رسائل الترحيب ----------------
    if lower_text.startswith("+wc "):
        if not await is_master(uid, p_name): return "🚫 للماستر فقط."
        msg = text.split(" ", 1)[1].strip()
        data = load_welcome(); item = data.setdefault(str(rid), {"enabled": False, "messages": []})
        if msg not in item["messages"]: item["messages"].append(msg)
        save_welcome(data)
        return "✅ تمت إضافة رسالة الترحيب."
    if lower_text == "clear@wc":
        if not await is_master(uid, p_name): return "🚫 للماستر فقط."
        data = load_welcome(); data.pop(str(rid), None); save_welcome(data)
        return "🧹 تم حذف رسائل الترحيب."
    if lower_text == "l@wc":
        if not await is_master(uid, p_name): return "🚫 للماستر فقط."
        msgs = load_welcome().get(str(rid), {}).get("messages", [])
        return "👋 رسائل الترحيب:\n" + ("\n".join(f"{i+1}. {m}" for i,m in enumerate(msgs)) if msgs else "لا توجد رسائل.")
    if lower_text in ("wc@on", "wc on"):
        if not await is_master(uid, p_name): return "🚫 للماستر فقط."
        data = load_welcome(); data.setdefault(str(rid), {"enabled": False, "messages": []})["enabled"] = True; save_welcome(data)
        return "✅ تم تفعيل رسائل الترحيب."
    if lower_text in ("wc@off", "wc off"):
        if not await is_master(uid, p_name): return "🚫 للماستر فقط."
        data = load_welcome(); data.setdefault(str(rid), {"enabled": False, "messages": []})["enabled"] = False; save_welcome(data)
        return "⛔ تم تعطيل رسائل الترحيب."

    if text.strip().lower() in ("العاب", "ألعاب", "games", "gamehelp"):
        return HELP_GAMES

    if text.strip().lower() in ("gv", "هدايا", "الهدايا", "gifts"):
        return await gift_catalog_message()

    if text.strip().lower().startswith("gv@"):
        return await send_gift_command(rid, uid, p_name, text.strip())

    parts = text.split(maxsplit=1)
    cmd, arg = parts[0].lower(), (parts[1].strip() if len(parts) > 1 else "")

    GAME_COMMANDS = {"عمل","job","كف","slap","مضاربة","bet","حرب","war","سرقة","rob","قتال","fight",
                     "سباق","race","رشوة","سلة","قصف","اضرب","ورق","سدد","ملاكمة","بركان","شبح","حظ","نرد","تعدين","زواج","marriage"}

    async def require_game_cooldown(game_command):
        ok_cd, rem_cd = check_cooldown(uid, p_name, f"game:{game_command}", int(C.get("game_cooldown_seconds", 30)))
        if not ok_cd:
            return f"⏳ @{p_name} انتظر {rem_cd} ثانية قبل إعادة لعبة «{game_command}». الفاصل 30 ثانية لهذه اللعبة فقط."
        return None

    async def require_music_cooldown():
        now = time.time(); last = music_last_by_user.get(str(uid), 0.0); interval = int(C.get("music_interval_seconds", 120))
        remaining = int(interval - (now-last)) if now-last < interval else 0
        if remaining > 0:
            return f"⏳ @{p_name} انتظر {remaining} ثانية قبل طلب أغنية أخرى. فاصل الأغاني دقيقتان لك."
        music_last_by_user[str(uid)] = now
        return None

    # كل أوامر الألعاب محمية بتوثيق VIP من صاحب البوت.
    if cmd in GAME_COMMANDS:
        err = None
        if vip_error:
            return vip_error

    # sa: تشغيل الأغنية ونشرها لكل الغرف
    if cmd == "sa":
        err = None
        if vip_error: return vip_error
        if not arg: return "❌ اكتب: sa اسم الأغنية"
        cd = await require_music_cooldown()
        if cd: return cd
        await music_queue.put((rid, arg, "YouTube", uid, p_name, False))
        return f"🎵 @{p_name} جاري تشغيل الأغنية ونشرها لكل الغرف…\n🔎 البحث عن: {arg}\n🏠 الغرفة: {rooms.get(rid, 'الغرفة')}"

    if cmd in ("تشغيل", "play", "شغل"):
        err = None
        if vip_error: return vip_error
        if not arg: return "❌ اكتب: تشغيل اسم الأغنية"
        cd = await require_music_cooldown()
        if cd: return cd
        await music_queue.put((rid, arg, "YouTube", uid, p_name, True))
        return f"🎵 @{p_name} جاري تشغيل الأغنية في هذه الغرفة فقط…\n🔎 البحث عن: {arg}\n🏠 الغرفة: {rooms.get(rid, 'الغرفة')}"

    if cmd in ("مشاركة", "share"):
        err = None
        if vip_error: return vip_error
        current = music_state.get(rid)
        if not current:
            return "❌ لا توجد أغنية حالياً للمشاركة."
        return f"🎵 مشاركة الأغنية\n🎶 {current.get('title','المقطع')} — {current.get('artist','')}\n🔗 {current.get('spotify_url') or current.get('youtube_url') or ''}"

    if cmd in (".تشغيل", "spotify", "سبوتيفاي"):
        err = None
        if vip_error: return vip_error
        if not arg:
            return "❌ اكتب: .تشغيل اسم الأغنية أو .تشغيل رابط Spotify"
        cd = await require_music_cooldown()
        if cd: return cd
        await music_queue.put((rid, arg, "Spotify", uid, p_name, False))
        return f"🎵 @{p_name} جاري تنفيذ طلبك من Spotify…\n🏠 الغرفة: {rooms.get(rid, 'الغرفة')}"

    if cmd in ("تيك", ".تيك", "tiktok", "tik"):
        err = None
        if vip_error: return vip_error
        if not arg: return "❌ اكتب: تيك اسم الأغنية"
        cd = await require_music_cooldown()
        if cd: return cd
        await music_queue.put((rid, arg, "TikTok", uid, p_name, False))
        return f"🎵 @{p_name} جاري تنفيذ طلبك من TikTok…\n🏠 الغرفة: {rooms.get(rid, 'الغرفة')}"

    # لعبة الحرب: لاعبان، سفينة في 1..6، 3 محاولات لكل لاعب، مع انتهاء تلقائي.
    if cmd in ("حرب", "war"):
        key = f"war_{rid}"
        game = war_games.get(key)
        now = time.time()

        # اللعبة مفتوحة المدة — لا تنتهي إلا عند تخمين السفينة أو نفاد المحاولات.
        if game and now >= game.get("expires_at", 0):
            game["expires_at"] = now + 600  # مدّ المهلة بدل إنهائها (مفتوحة)

        if not game:
            cd_error = await require_game_cooldown(cmd)
            if cd_error:
                return cd_error
            war_games[key] = {
                "p1": uid, "p1_name": p_name, "p2": None, "p2_name": None,
                "ship": random.randint(1, 6),
                "tries": {str(uid): 0},
                "guesses": {str(uid): []},
                "turn": uid,
                "created_at": now,
                "turn_started_at": now,
                "expires_at": now + 600,
            }
            await room_send(
                rid,
                f"⚔️ @{p_name} بدأ لعبة الحرب!\n🔍 جاري البحث عن منافس…\n⏳ اكتب «حرب» للانضمام.\n🎯 لكل لاعب 3 محاولات من 1 إلى 6.\n🕐 اللعبة مفتوحة المدة — لن تنتهي حتى تخمّن السفينة!"
            )
            return None

        if game["p1"] == uid:
            return "⚠️ أنت داخل لعبة حرب بالفعل وتنتظر الخصم." if game.get("p2") is None else "⚠️ أنت داخل لعبة حرب بالفعل."

        if game.get("p2") is None:
            game["p2"], game["p2_name"] = uid, p_name
            game["tries"][str(uid)] = 0
            game["guesses"][str(uid)] = []
            game["turn"] = game["p1"]
            game["turn_started_at"] = now
            game["expires_at"] = now + 120
            await _war_board_message(rid, game, game["p1"])
            return None
        return "⚠️ الحرب ممتلئة. انتظر انتهاء اللعبة."

    # تخمين الحرب يكون برقم منفصل 1..6.
    if game := war_games.get(f"war_{rid}"):
        now = time.time()
        # مفتوحة المدة — لا تنتهي تلقائيًا
        if now >= game.get("expires_at", 0):
            game["expires_at"] = now + 600  # مدّ المهلة بدل إنهائها

        if text.strip().lower() in ("رادار", "radar"):
            if game.get("p2") is None:
                return "⏳ انتظر اللاعب الثاني."
            if uid not in (game["p1"], game["p2"]):
                return "🚫 هذه اللعبة بين لاعبين آخرين."
            skey = str(uid)
            tried = set(game["guesses"].get(skey, []))
            available = sorted(set(range(1, 7)) - tried)
            await room_send(rid, f"🛰️ Radar | الرادار\n🚢 السفينة في أحد هذه الأرقام:\n{' '.join(str(x) for x in available)}")
            return None

        if text.isdigit() and 1 <= int(text) <= 6:
            if game.get("p2") is None:
                return "⏳ انتظر اللاعب الثاني."
            if uid not in (game["p1"], game["p2"]):
                return "🚫 هذه اللعبة بين لاعبين آخرين."
            if game["turn"] != uid:
                return "⏳ انتظر دور خصمك."

            n = int(text)
            skey = str(uid)
            if n in game["guesses"].setdefault(skey, []):
                return "⚠️ لقد اخترت هذا الرقم من قبل."

            game["guesses"][skey].append(n)
            game["tries"][skey] += 1

            if n == game["ship"]:
                add_points(uid, p_name, 5000)
                other = game["p2"] if uid == game["p1"] else game["p1"]
                await send_game_card(rid, "war", "⚔️ حرب | Battle", [f"🏆 Winner | الفائز: @{p_name} (+{fmt_pts(5000)})", f"💥 🚢 السفينة دُمّرت بواسطة @{p_name}", f"🚢 موقع السفينة: {game['ship']}", f"🎁 الجائزة: +{fmt_pts(5000)} نقطة"], f"💥🚢 تم تدمير السفينة! الفائز @{p_name} (+{fmt_pts(5000)} نقطة)")
                war_games.pop(f"war_{rid}", None)
                return None

            other = game["p2"] if uid == game["p1"] else game["p1"]
            other_key = str(other)
            current_tries = game["tries"].get(skey, 0)
            other_tries = game["tries"].get(other_key, 0)

            if current_tries >= 3 and other_tries >= 3:
                await send_game_card(rid, "war", "⚔️ حرب | Battle", ["🤝 انتهت المحاولات لكلا اللاعبين", f"🚢 السفينة كانت في {game['ship']} ولم تُدمر"], "🤝 انتهت الحرب ولم تُدمر السفينة.")
                war_games.pop(f"war_{rid}", None)
                return None

            if other_tries >= 3:
                game["turn"] = uid
                next_name = p_name
                remaining = 3 - current_tries
            else:
                game["turn"] = other
                next_name = game["p2_name"] if uid == game["p1"] else game["p1_name"]
                remaining = 3 - other_tries

            game["turn_started_at"] = now
            game["expires_at"] = now + 600
            await room_send(
                rid,
                f"👤 @{p_name} ❌ الرقم {n} ليس السفينة | Missed\n🔄 دور @{next_name} — بقيت له {remaining} محاولات."
            )
            await _war_board_message(rid, game, game["turn"])
            return None

        if uid in (game.get("p1"), game.get("p2")):
            return "⚠️ اكتب رقماً من 1 إلى 6 للتخمين، أو «رادار» لتلميح."
        return None

    if cmd in ("سرقة", "rob"):
        cd_error = await require_game_cooldown(cmd)
        if cd_error: return cd_error
        win = random.randint(1, 100) <= 40
        add_points(uid, p_name, 25 if win else -15)
        await send_game_card(rid, "rob", "💰 Rob | سرقة", [f"👤 اللاعب: @{p_name}", f"🏅 {'Winner | الفائز' if win else 'Loser | الخاسر'}: @{p_name}", f"💰 النتيجة: {'+25' if win else '-15'} نقطة"], f"💰 {'نجحت السرقة!' if win else 'فشلت السرقة..'} @{p_name}")
        return None

    if cmd in ("قتال", "fight"):
        cd_error = await require_game_cooldown(cmd)
        if cd_error: return cd_error
        win = random.choice([True, False])
        add_points(uid, p_name, 15 if win else -5)
        await send_game_card(rid, "fight", "🥊 Fight | قتال", [f"👤 اللاعب: @{p_name}", f"🏅 {'Winner | الفائز' if win else 'Loser | الخاسر'}: @{p_name}", f"💰 النتيجة: {'+15' if win else '-5'} نقطة"], f"🥊 {'هزمت خصمك!' if win else 'تلقيت ضربة قاضية..'} @{p_name}")
        return None

    if cmd in ("عمل", "job"):
        cd_error = await require_game_cooldown(cmd)
        if cd_error: return cd_error
        salary = random.randint(50, 150); add_points(uid, p_name, salary)
        await send_game_card(rid, "job", "💼 Work | عمل", [f"👤 اللاعب: @{p_name}", f"💵 الراتب: +{salary} نقطة", "🏆 النتيجة: فوز"], f"💼 عمل @{p_name} +{salary} نقطة")
        return None

    if cmd in ("سباق", "race"):
        cd_error = await require_game_cooldown(cmd)
        if cd_error: return cd_error
        win = random.choice([True, False])
        add_points(uid, p_name, 30 if win else -10)
        await send_game_card(rid, "race", "🏁 Race | سباق", [f"👤 اللاعب: @{p_name}", f"🏅 {'Winner | الفائز' if win else 'Loser | الخاسر'}: @{p_name}", f"💰 النتيجة: {'+30' if win else '-10'} نقطة"], f"🏁 {'فزت بالسباق!' if win else 'تعطلت سيارتك..'} @{p_name}")
        return None

    if cmd in ("كف", "slap"):
        game = kaf_games.get(f"slap_{rid}")
        if not game:
            cd_error = await require_game_cooldown(cmd)
            if cd_error:
                return cd_error
            kaf_games[f"slap_{rid}"] = {"player1": uid, "p1_name": p_name}
            await send_game_card(rid, "slap", "👏💢 Slap | كف 💢👏", [f"👤 @{p_name}", "⏳ جاري انتظار الخصم", "🎮 اكتب كف للانضمام"], f"⏳ @{p_name} جاري انتظار الخصم...")
        else:
            if game["player1"] == uid: return "⚠️ أنت تنتظر منافس!"
            p1_name = game["p1_name"]
            winner = random.choice([p1_name, p_name])
            kaf_games.pop(f"slap_{rid}")
            add_points(uid if winner == p_name else game["player1"], winner, 15)
            await send_game_card(rid, "slap", "👏💢 Slap | كف 💢👏", [f"🥊 @{p1_name} × @{p_name}", f"🏅 Winner | الفائز: @{winner} (+15)", "💔 Loser | الخاسر: اللاعب الآخر (-10)"], f"👏💢 Slap | كف 💢👏\n🏆 الفائز: @{winner}")
        return None

    if cmd in ("مضاربة", "bet"):
        try: amount = int(arg)
        except: return "❌ اكتب: مضاربة [عدد النقاط]"
        points, user_data = get_user_data(uid, p_name)
        if user_data["points"] < amount: return f"⚠️ نقاطك لا تكفي ({user_data['points']})"
        game_key = f"bet_{rid}"
        game = kaf_games.get(game_key)
        if not game:
            cd_error = await require_game_cooldown(cmd)
            if cd_error:
                return cd_error
            kaf_games[game_key] = {"player1": uid, "p1_name": p_name, "amount": amount}
            await send_game_card(rid, "bet", "🎲 Bet | مضاربة", [f"👤 اللاعب: @{p_name}", f"💰 الرهان: {fmt_pts(amount)} نقطة", "⏳ جاري انتظار الخصم"], f"🎲 @{p_name} يراهن بـ {fmt_pts(amount)} نقطة")
            async def bot_bet():
                await asyncio.sleep(30)
                g = kaf_games.get(game_key)
                if g and g["player1"] == uid:
                    win = random.choice([True, False])
                    kaf_games.pop(game_key)
                    add_points(uid, p_name, amount if win else -amount)
                    await send_game_card(rid, "bet", "🎲 Bet | مضاربة", [f"👤 اللاعب: @{p_name}", f"🏅 {'Winner | الفائز' if win else 'Loser | الخاسر'}: @{p_name}", f"💰 النتيجة: {amount if win else -amount} نقطة"], f"🤖 {'فزت على البوت!' if win else 'خسرت ضد البوت..'} @{p_name}")
            asyncio.create_task(bot_bet())
        else:
            if game["player1"] == uid: return "⚠️ أنت صاحب الرهان!"
            if amount != game["amount"]: return f"❌ الرهان هو {game['amount']} ن."
            p1_name = game["p1_name"]
            winner = random.choice([p1_name, p_name])
            kaf_games.pop(game_key)
            add_points(uid if winner == p_name else game["player1"], winner, amount)
            add_points(game["player1"] if winner == p_name else uid, p1_name if winner == p_name else p_name, -amount)
            await send_game_card(rid, "bet", "🎲 Bet | مضاربة", [f"🥊 @{p1_name} × @{p_name}", f"🏆 Winner | الفائز: @{winner}", f"💰 الرهان: {amount} نقطة"], f"🎲 تمت المضاربة بين @{p1_name} و @{p_name}..\n🏆 الفائز: @{winner}")
        return None

    if cmd in ("طرد", "kick"):
        if not await is_master(uid, p_name): return "🚫 للماستر فقط."
        target = arg.replace("@", "").strip()
        rows, _ = await table_select(lambda: sb.table("profiles").select("id").eq("username", target).limit(1).execute())
        if not rows: return "❌ المستخدم غير موجود."
        await rpc("room_leave", {"_room": rid, "_user": rows[0]["id"]})
        return f"👞 تم طرد @{target}."

    if cmd in ("حظر", "ban"):
        if not await is_master(uid, p_name): return "🚫 للماستر فقط."
        target = arg.replace("@", "").strip()
        rows, _ = await table_select(lambda: sb.table("profiles").select("id").eq("username", target).limit(1).execute())
        if not rows: return "❌ المستخدم غير موجود."
        tid = rows[0]["id"]; bans = load_bans()
        if rid not in bans: bans[rid] = []
        if tid not in bans[rid]:
            bans[rid].append(tid); save_bans(bans)
            await rpc("room_leave", {"_room": rid, "_user": tid})
            return f"🚫 تم حظر @{target}."
        return "⚠️ محظور بالفعل."

    if cmd == "نقاطي":
        p, d = get_user_data(uid, p_name)
        return f"👤 @{p_name} ➔ ✨ {fmt_pts(d['points'])} نقطة"

    if cmd == "توب":
        pts = load_points()
        sorted_u = sorted(pts.items(), key=lambda x: x[1].get("points", 0), reverse=True)[:10]
        if not sorted_u: return "📭 القائمة فارغة."
        msg = "🏆 ━━━━━━ TOP 10 ━━━━━━ 🏆\n"
        emojis = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣", "🔟"]
        for i, (u, d) in enumerate(sorted_u):
            msg += f"{emojis[i]} @{d['username']} ➔ {fmt_pts(d['points'])} ن\n"
        return msg + "━━━━━━━━━━━━━━━━━━━━"

    if cmd in ("تفاعل", "interact", "تفاعلات"):
        # أكثر المستخدمين تفاعلاً خلال آخر 30 يومًا من published_posts.json
        posts = load_published_posts()
        now = time.time()
        month_seconds = 30 * 24 * 3600
        scores = {}
        for post in posts.values():
            created = post.get("created_at", "")
            try:
                t = datetime.fromisoformat(str(created).replace("Z", "+00:00")).timestamp()
            except Exception:
                t = 0.0
            if not t or (now - t) > month_seconds:
                continue
            for entry in post.get("interactions", []) or []:
                user = str(entry.get("user") or "").strip()
                if user:
                    scores[user] = scores.get(user, 0) + 1
        if not scores:
            return "📭 لا توجد تفاعلات خلال آخر 30 يومًا."
        sorted_users = sorted(scores.items(), key=lambda x: x[1], reverse=True)[:10]
        emojis = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣", "🔟"]
        msg = "📊 ━━━━ الأكثر تفاعلاً (30 يوم) ━━━━ 📊\n"
        for i, (uname, count) in enumerate(sorted_users):
            msg += f"{emojis[i] if i < 10 else str(i+1)} @{uname} ➔ {count} تفاعل\n"
        return msg + "━━━━━━━━━━━━━━━━━━━━"

    # بقية الألعاب مع صور
    games_map = {
        "رشوة": ("bribe", 100, -50, 30, "💰 نجحت الرشوة!", "👮 تم القبض عليك!"),
        "سلة": ("basket", 15, 0, 50, "🏀 رمية ثلاثية!", "🏀 ضاعت الكرة.."),
        "قصف": ("drone", 20, 0, 100, "💣 انفجار هائل!", ""),
        "اضرب": ("frog", 10, 0, 50, "🐸 ضربة موفقة!", "🐸 هرب الضفدع.."),
        "ورق": ("cards", 40, 0, 20, "🃏 ورقة الجوكر!", "🃏 ورقة ضعيفة.."),
        "سدد": ("ball", 20, 0, 50, "⚽ جـووووول!", "⚽ ضاعت الكرة.."),
        "ملاكمة": ("boxing", 30, -10, 50, "🥊 ضربة قاضية!", "🥊 سقطت في الحلبة.."),
        "بركان": ("volcano", 0, -20, 0, "", "🌋 ثوران بركاني!"),
        "شبح": ("ghost", 50, 0, 50, "👻 أمسكت بالشبح!", "👻 أخافك الشبح.."),
        "حظ": ("luck", 50, -30, 50, "🎲 حظ سعيد!", "📉 حظ سيء.."),
        "نرد": ("dice", 15, -10, 50, "🎲 فوز بالنرد!", "🎲 خسارة بالنرد..")
    }
    
    if cmd in games_map:
        cd_error = await require_game_cooldown(cmd)
        if cd_error:
            return cd_error
        key, win_p, lose_p, chance, win_m, lose_m = games_map[cmd]
        win = random.randint(1, 100) <= chance
        add_points(uid, p_name, win_p if win else lose_p)
        await send_game_card(rid, key, f"🎮 {cmd}", [f"👤 اللاعب: @{p_name}", f"🏅 {'Winner | الفائز' if win else 'Loser | الخاسر'}: @{p_name}", f"💰 النتيجة: {win_p if win else -abs(lose_p)} نقطة"], f"{win_m if win else lose_m} @{p_name}\n💰 النتيجة: {fmt_pts(win_p) if win else fmt_pts(lose_p)} ن.")
        return None

    if cmd == "تعدين":
        cd_error = await require_game_cooldown(cmd)
        if cd_error:
            return cd_error
        found = random.randint(200, 500); add_points(uid, p_name, found)
        await send_game_card(rid, "mine", "⛏️ Mine | تعدين", [f"👤 اللاعب: @{p_name}", "🏆 Winner | الفائز", f"💰 النتيجة: +{found} نقطة"], f"⛏️ وجدت ذهباً! @{p_name} +{found} ن.")
        return None

    if cmd == "زواج":
        cd_error = await require_game_cooldown(cmd)
        if cd_error: return cd_error
        pts, d = get_user_data(uid, p_name)
        if d.get("married_to"): return f"💍 متزوج من @{d['married_to']}"
        others = [u["username"] for i, u in pts.items() if i != uid]
        if not others: return "💔 لا أحد للزواج."
        partner = random.choice(others); d["married_to"] = partner
        pts[uid] = d; save_json(POINTS_PATH, pts)
        await send_game_card(rid, "marriage", "💍 Marriage | زواج", [f"👤 اللاعب: @{p_name}", f"❤️ الشريك: @{partner}", "🏆 تمت العملية بنجاح"], f"❤️ مبروك زواج @{p_name} من @{partner} 💍")
        return None

    if cmd in ("تخطي", "skip"):
        ok, out = await skip(rid); return out
    if cmd in ("ايقاف", "stop"):
        ok, out = await stop(rid); return out
    if cmd in ("مساعدة", "help", ".help"): return HELP_ROOM
    
    return None

async def run_cleanup():
    """تنظيف فوري للمخلفات عند طلب الماستر من الخاص."""
    try:
        files_deleted = 0
        dirs_cleaned = 0
        now = time.time()
        if GIFT_RENDER_DIR.exists():
            for fp in GIFT_RENDER_DIR.glob("gift_*.png"):
                try:
                    if now - fp.stat().st_mtime > 300:
                        fp.unlink(); files_deleted += 1
                except OSError:
                    pass
            dirs_cleaned += 1
        if PUBLISH_LOCAL_DIR.exists():
            for fp in PUBLISH_LOCAL_DIR.glob("*.*"):
                try:
                    if now - fp.stat().st_mtime > 600:
                        fp.unlink(); files_deleted += 1
                except OSError:
                    pass
            dirs_cleaned += 1
        try:
            for fp in Path(tempfile.gettempdir()).glob("alsfer_*.*"):
                try:
                    if now - fp.stat().st_mtime > 600:
                        fp.unlink(); files_deleted += 1
                except OSError:
                    pass
        except Exception:
            pass
        # حذف المنشورات المنتهية
        posts = load_published_posts()
        removed = _prune_expired_posts(posts)
        if removed:
            save_published_posts(posts)
        return {"files_deleted": files_deleted, "dirs_cleaned": dirs_cleaned}
    except Exception:
        log.exception("manual cleanup failed")
        return "فشل التنظيف"


async def create_backup_and_send(sender_uid):
    """إنشاء نسخة احتياطية ZIP بكل ملفات البوت وإرسالها في خاص الماستر."""
    try:
        bot_dir = Path(os.path.dirname(os.path.abspath(__file__)))
        backup_name = f"bot_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.zip"
        backup_path = Path(tempfile.gettempdir()) / backup_name

        import zipfile
        with zipfile.ZipFile(backup_path, 'w', zipfile.ZIP_DEFLATED) as zf:
            # إضافة ملفات البوت الرئيسية
            for pattern in ["bot_vip.py", "bot_no_vip.py", "bot.py", "cleanup.py",
                           "config.json", "points.json", "published_posts.json",
                           "vip_users.json", "bans.json", "masters.json",
                           "rooms_saved.json", "spotify_cookies.txt",
                           "youtube_cookies.txt"]:
                fp = bot_dir / pattern
                if fp.exists():
                    zf.write(fp, pattern)

            # إضافة مجلدات (assets, generated_gifts, logs) إذا وجدت
            for dir_name in ["assets", "generated_gifts", "logs", "media_cache"]:
                d = bot_dir / dir_name
                if d.exists():
                    for f in d.rglob("*"):
                        if f.is_file():
                            zf.write(f, f"{dir_name}/{f.relative_to(d)}")

        size_mb = backup_path.stat().st_size / (1024 * 1024)
        await dm_send(sender_uid, f"📦 النسخة الاحتياطية جاهزة:\n📄 الاسم: {backup_name}\n📏 الحجم: {size_mb:.1f} MB\n\n⚠️ الملف كبير — أرسلته كملف ZIP في خاصك.\nاحفظه فورًا.")

        # إرسال الملف كـ attachment
        try:
            url = await _store_media(backup_path, "backup", "application/zip")
            await room_send_media(str(sender_uid), "📦 نسخة احتياطية للبوت", url, m_type="file")
        except Exception:
            log.exception("failed to send backup file, sending as text")
            # إذا فشل إرسال الملف، أرسل المحتوى كرسائل نصية مقسمة
            with open(backup_path, 'rb') as f:
                content = f.read().decode('utf-8', errors='ignore')
            chunk_size = 3000
            for i in range(0, len(content), chunk_size):
                chunk = content[i:i+chunk_size]
                await dm_send(sender_uid, chunk)

        backup_path.unlink(missing_ok=True)
        return "✅ تم إرسال النسخة الاحتياطية في خاصك."
    except Exception as exc:
        log.exception("backup creation failed")
        return f"❌ فشل إنشاء النسخة الاحتياطية: {exc}"


async def delete_old_backups():
    """حذف النسخ الاحتياطية السابقة المحفوظة في مجلد /tmp."""
    try:
        deleted = 0
        temp_dir = Path(tempfile.gettempdir())
        for fp in temp_dir.glob("bot_backup_*.zip"):
            try:
                fp.unlink()
                deleted += 1
            except OSError:
                pass
        return f"✅ تم حذف {deleted} نسخة احتياطية سابقة."
    except Exception:
        log.exception("delete old backups failed")
        return "❌ فشل حذف النسخ الاحتياطية."


async def send_backup_via_telegram(sender_uid):
    """إرسال النسخة الاحتياطية عبر تلجرام باستخدام TELEGRAM_BOT_TOKEN."""
    try:
        tg_token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
        if not tg_token:
            return "❌ لا يوجد TELEGRAM_BOT_TOKEN — أضفه في متغيرات Railway أولاً."

        bot_dir = Path(os.path.dirname(os.path.abspath(__file__)))
        backup_name = f"bot_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.zip"
        backup_path = Path(tempfile.gettempdir()) / backup_name

        import zipfile
        with zipfile.ZipFile(backup_path, 'w', zipfile.ZIP_DEFLATED) as zf:
            for pattern in ["bot_vip.py", "bot_no_vip.py", "bot.py", "cleanup.py",
                           "config.json", "points.json", "published_posts.json",
                           "vip_users.json", "bans.json", "masters.json",
                           "rooms_saved.json", "spotify_cookies.txt",
                           "youtube_cookies.txt"]:
                fp = bot_dir / pattern
                if fp.exists():
                    zf.write(fp, pattern)

            for dir_name in ["assets", "generated_gifts", "logs", "media_cache"]:
                d = bot_dir / dir_name
                if d.exists():
                    for f in d.rglob("*"):
                        if f.is_file():
                            zf.write(f, f"{dir_name}/{f.relative_to(d)}")

        size_mb = backup_path.stat().st_size / (1024 * 1024)
        if size_mb > 50:
            backup_path.unlink(missing_ok=True)
            return f"❌ النسخة كبيرة ({size_mb:.0f} MB) — تلجرام يقبل 50 MB كحد أقصى. جرب: تنظيف ثم backup"

        # إرسال الملف عبر Telegram Bot API (sendDocument)
        async with aiohttp.ClientSession() as session:
            # أولاً نرسل رسالة للماستر على تلجرام ليحصل على chat_id
            get_updates_url = f"https://api.telegram.org/bot{tg_token}/getUpdates"
            async with session.get(get_updates_url) as resp:
                data = await resp.json()
            
            chat_id = None
            if data.get("ok") and data.get("result"):
                for update in data["result"]:
                    msg = update.get("message")
                    if msg and msg.get("chat", {}).get("type") == "private":
                        chat_id = msg["chat"]["id"]
                        break

            if not chat_id:
                backup_path.unlink(missing_ok=True)
                return "❌ لم أجد محادثة سابقة مع البوت على تلجرام. أرسل رسالة لبوت تلجرام أولاً ثم اكتب تلجرام هنا."

            # إرسال الملف
            url = f"https://api.telegram.org/bot{tg_token}/sendDocument"
            with open(backup_path, 'rb') as f:
                form_data = aiohttp.FormData()
                form_data.add_field('chat_id', str(chat_id))
                form_data.add_field('document', f, filename=backup_name)
                async with session.post(url, data=form_data) as resp:
                    result = await resp.json()

            backup_path.unlink(missing_ok=True)
            if result.get("ok"):
                return f"✅ تم إرسال النسخة الاحتياطية ({size_mb:.1f} MB) على تلجرام."
            else:
                return f"❌ فشل إرسال تلجرام: {result.get('description', 'unknown error')}"
    except Exception as exc:
        log.exception("telegram backup failed")
        return f"❌ فشل إرسال تلجرام: {exc}"


async def update_music_card_template(sender_uid, new_template):
    """تعديل رسالة عرض الأغنية في المجموعات — يحفظ القالب الجديد في ملف ويستخدمه عند كل عرض."""
    try:
        bot_dir = Path(os.path.dirname(os.path.abspath(__file__)))
        template_file = bot_dir / "music_card_template.txt"
        
        if not new_template:
            # إذا لم يُرسل قالب جديد، يعرض الحالي
            if template_file.exists():
                return f"📄 قالب رسالة الأغنية الحالي:\n{template_file.read_text(encoding='utf-8')}\n\nلتعديله اكتب:\nرسالة أغنية (القالب الجديد)"
            return "📄 لا يوجد قالب مخصص — يستخدم القالب الافتراضي. اكتب:\nرسالة أغنية (القالب الجديد)"
        
        template_file.write_text(new_template, encoding='utf-8')
        return f"✅ تم تحديث قالب رسالة الأغنية:\n{new_template}"
    except Exception as exc:
        log.exception("music card template update failed")
        return f"❌ فشل تحديث القالب: {exc}"


async def apply_self_edits(sender_uid):
    """البوت يبني نفسه: يقرأ ملف bot_edits.txt وينفذ التعديلات الموجودة فيه ثم يعيد تشغيل نفسه."""
    try:
        bot_dir = Path(os.path.dirname(os.path.abspath(__file__)))
        edits_file = bot_dir / "bot_edits.txt"
        
        if not edits_file.exists():
            return "❌ لم أجد ملف bot_edits.txt\n\n📝 طريقة الاستخدام:\nأنشئ ملف bot_edits.txt في مجلد البوت واكتب فيه التعليمات مثل:\n- عدّل رسالة عرض الأغنية: (النص الجديد)\n- أضف أمر جديد: (الأمر) (الوظيفة)\n- احذف أمر: (اسم الأمر)\n\nثم اكتب: عدل"
        
        edits_content = edits_file.read_text(encoding='utf-8').strip()
        if not edits_content:
            return "❌ ملف bot_edits.txt فارغ."
        
        log.info("self-edit: reading instructions from bot_edits.txt")
        log.info("self-edit: content = %s", edits_content)
        
        bot_file = bot_dir / os.path.basename(__file__)
        current_code = bot_file.read_text(encoding='utf-8')
        
        changes = []
        
        # تحليل التعليمات — تنسيق مرن
        lines = edits_content.split('\n')
        for line in lines:
            line = line.strip()
            if not line or line.startswith('#') or line.startswith('//'):
                continue
            
            # 1) تعديل رسالة عرض الأغنية
            if "رسالة عرض الأغنية" in line or "رسالة الأغنية" in line or "music card" in line.lower():
                # استخراج النص الجديد بعد النقطتين
                if ':' in line:
                    new_text = line.split(':', 1)[1].strip()
                    if new_text:
                        MUSIC_CARD_TEMPLATE["custom"] = new_text
                        changes.append(f"✅ تم تحديث رسالة عرض الأغنية")
                        log.info("self-edit: music card template updated to: %s", new_text)
            
            # 2) إضافة/تعديل نص أو استبدال
            elif "استبدل" in line or "replace" in line.lower() or "بدّل" in line:
                if ':' in line:
                    parts = line.split(':', 1)[1].strip()
                    if '=>' in parts:
                        old, new = parts.split('=>', 1)
                        old, new = old.strip(), new.strip()
                        if old in current_code:
                            current_code = current_code.replace(old, new, 1)
                            changes.append(f"✅ استبدل: {old[:30]}... → {new[:30]}...")
                            log.info("self-edit: replaced %s → %s", old[:30], new[:30])
                        else:
                            changes.append(f"⚠️ لم أجد: {old[:30]}...")
            
            # 3) إضافة أمر جديد
            elif "أضف أمر" in line or "أضف" in line or "add command" in line.lower():
                changes.append(f"✅ تم تسجيل إضافة أمر: {line[:50]}")
                log.info("self-edit: new command noted: %s", line[:50])
            
            # 4) حذف أمر
            elif "احذف أمر" in line or "حذف" in line or "delete" in line.lower():
                changes.append(f"✅ تم تسجيل حذف: {line[:50]}")
                log.info("self-edit: deletion noted: %s", line[:50])
        
        # حفظ القالب المخصص
        template_file = bot_dir / "music_card_template.txt"
        if "custom" in MUSIC_CARD_TEMPLATE:
            template_file.write_text(MUSIC_CARD_TEMPLATE["custom"], encoding='utf-8')
            changes.append("📄 تم حفظ القالب في music_card_template.txt")
        
        # إعادة كتابة ملف البوت إذا تغيّر
        if current_code != bot_file.read_text(encoding='utf-8'):
            bot_file.write_text(current_code, encoding='utf-8')
            changes.append("📝 تم حفظ التعديلات في ملف البوت")
        
        # إفراغ ملف التعليمات بعد التنفيذ
        edits_file.write_text("", encoding='utf-8')
        changes.append("🗑️ تم إفراغ ملف bot_edits.txt")
        
        result = "✅ تم تطبيق التعديلات:\n" + "\n".join(changes) if changes else "✅ تم قراءة التعليمات بدون تغييرات."
        
        # إعادة تشغيل البوت بعد 3 ثوانٍ
        try:
            import threading
            def restart_after():
                time.sleep(3)
                os._exit(42)  # Railway سيعيد التشغيل تلقائيًا
            threading.Thread(target=restart_after, daemon=True).start()
            result += "\n\n🔄 البوت سيعيد تشغيل نفسه خلال 3 ثوانٍ..."
        except Exception:
            result += "\n\n⚠️ أعد تشغيل البوت يدويًا لتطبيق التعديلات."
        
        return result
    except Exception as exc:
        log.exception("self-edit failed")
        return f"❌ فشل تطبيق التعديلات: {exc}"


# ----------------------------- الحلقات -----------------------------
async def broadcast_to_all_users(sender, message):
    """إرسال رسالة خاصة لكل المستخدمين في كل الغرف (جماعية/برودكاست)."""
    targets = set()
    try:
        for rid in await all_room_ids():
            try:
                data, err = await rpc("room_members", {"_room": rid})
                members = data or []
                for m in members:
                    uid = m.get("user_id") or m.get("id")
                    if uid and uid != BOT_ID and uid != sender:
                        targets.add(str(uid))
            except Exception:
                log.warning("failed to get members of room %s", rid)
    except Exception as e:
        log.exception("broadcast_to_all_users failed: %s", e)
        return f"❌ تعذر جمع المشتركين: {e}"

    if not targets:
        return "📭 لا يوجد مشتركون في الغرف."

    sent = 0
    failed = 0
    for uid in targets:
        try:
            await dm_send(uid, message)
            sent += 1
        except Exception:
            failed += 1
        await asyncio.sleep(0.5)  # تأخير بسيط لتجنب الـ rate limit

    return f"✅ تم إرسال الرسالة الجماعية:\n📤 {sent} مستخدم\n❌ {failed} فشل"


async def add_new_game(sender, full_text):
    """أضف لعبة جديدة — يقرأ ملف games_to_add.json ويضيف الألعاب ثم يعيد التشغيل."""
    games_file = Path(__file__).resolve().parent / "games_to_add.json"
    if not games_file.exists():
        return (
            "📝 لإنشاء ملف games_to_add.json، اكتب فيه:\n"
            '{"games": [{"name": "اسم_اللعبة", "key": "game_key", "description": "وصف"}]}\n'
            "ثم اكتب 'أضف لعبة' مرة أخرى."
        )
    try:
        import json
        data = json.loads(games_file.read_text(encoding='utf-8'))
        games = data.get("games", [])
        if not games:
            return "❌ الملف فارغ. أضف ألعاب في 'games'."
        count = 0
        for g in games:
            key = g.get("key", g.get("name", "")).strip()
            name = g.get("name", key)
            desc = g.get("description", "")
            # إضافة صورة اللعبة
            img_name = f"game_{key}.jpg"
            img_path = Path(__file__).resolve().parent / "assets" / img_name
            if img_path.exists():
                GAME_IMAGES[key] = game_asset(img_name)
                count += 1
                log.info("added game: %s (%s)", name, key)
            else:
                # إذا لا توجد صورة، استخدم صورة افتراضية
                GAME_IMAGES[key] = game_asset("game_luck.jpg")
                count += 1
        games_file.unlink()  # حذف الملف بعد التنفيذ
        # إعادة تشغيل البوت لتفعيل الألعاب
        await restart_self()
        return f"✅ تمت إضافة {count} لعبة جديدة. جاري إعادة التشغيل..."
    except Exception as e:
        log.exception("add_new_game failed: %s", e)
        return f"❌ تعذر إضافة الألعاب: {e}"


async def restart_self():
    """إعادة تشغيل البوت بعد 3 ثوانٍ (بعد تعديل الكود)."""
    async def _restart():
        await asyncio.sleep(3)
        import sys
        log.info("self-restart: restarting bot")
        os.execl(sys.executable, sys.executable, *sys.argv)
    asyncio.create_task(_restart())


async def update_libraries(sender):
    """تحديث مكتبات البوت (yt-dlp, Pillow, arabic-reshaper, etc.) وإعادة التشغيل."""
    libs_to_update = [
        "yt-dlp", "yt-dlp-ejs", "Pillow", "arabic-reshaper", "python-bidi",
        "supabase", "aiohttp", "requests", "flask"
    ]
    results = []
    for lib in libs_to_update:
        try:
            result = await asyncio.to_thread(
                subprocess.run,
                [sys.executable, "-m", "pip", "install", "--upgrade", lib, "--quiet"],
                capture_output=True, text=True, timeout=120
            )
            if result.returncode == 0:
                results.append(f"✅ {lib}")
            else:
                results.append(f"⚠️ {lib}: {result.stderr[:80] if result.stderr else 'خطأ غير معروف'}")
        except Exception as e:
            results.append(f"❌ {lib}: {str(e)[:80]}")

    reply = "📦 نتيجة تحديث المكاتب:\n" + "\n".join(results)
    # إعادة تشغيل البوت بعد التحديث
    await restart_self()
    return reply + "\n\n🔄 جاري إعادة تشغيل البوت..."


async def dm_loop():
    while True:
        try:
            rows, err = await table_select(lambda: sb.table("dm_relay").select("*").eq("recipient_id", BOT_ID).limit(50).execute())
            for row in rows or []:
                env, sender = row.get("envelope") or {}, row.get("sender_id")
                text = (env.get("content") or "").strip()
                if sender and sender != BOT_ID and text:
                    parts = text.split(maxsplit=1)
                    cmd, arg = parts[0].lower(), (parts[1].strip() if len(parts) > 1 else "")
                    is_owner = (await username_of(sender)).lower() == OWNER
                    reply = ""
                    if cmd in ("دخول", "join") and is_owner:
                        ok, m = await join(arg); reply = ("✅ " if ok else "❌ ") + m
                    elif cmd in ("خروج", "leave") and is_owner:
                        ok, m = await leave(arg); reply = ("✅ " if ok else "❌ ") + m
                    elif cmd in ("غرفي", "rooms"):
                        reply = "🏠 " + (", ".join(rooms.values()) if rooms else "لا توجد غرف")
                    elif cmd in ("تنظيف", "clean") and is_owner:
                        cleaned = await run_cleanup()
                        reply = f"✅ تم التنظيف:\n🗑️ {cleaned['files_deleted']} ملف محذوف\n📦 تم إفراغ {cleaned['dirs_cleaned']} مجلد" if isinstance(cleaned, dict) else f"✅ {cleaned}"
                    elif cmd in ("نسخة احتياطية", "backup") and is_owner:
                        reply = await create_backup_and_send(sender)
                    elif cmd in ("حفظ", "save") and is_owner:
                        reply = await create_backup_and_send(sender)
                    elif cmd in ("حذف نسخ", "حذف النسخ", "clear_backups", "delete_backups") and is_owner:
                        reply = await delete_old_backups()
                    elif cmd in ("تلغرام", "تلجرام", "telegram_backup") and is_owner:
                        reply = await send_backup_via_telegram(sender)
                    elif cmd in ("عدل", "edit", "update") and is_owner:
                        reply = await apply_self_edits(sender)
                    elif cmd in ("رسالة أغنية", "music_card") and is_owner:
                        reply = await update_music_card_template(sender, arg)
                    elif cmd in ("جماعية", "mass", "برودكاست", "broadcast_dm") and is_owner:
                        if not arg:
                            reply = "✍️ استخدم: جماعية نص الرسالة (ستُرسل بخاص لكل المشتركين)"
                        else:
                            reply = await broadcast_to_all_users(sender, arg)
                    elif cmd.startswith(("أضف لعبة", "add_game")) and is_owner:
                        reply = await add_new_game(sender, text)
                    elif cmd in ("تحديث المكاتب", "تحديث", "update_libs", "upgrade") and is_owner:
                        reply = await update_libraries(sender)
                    if reply: await dm_send(sender, reply)
                await run(lambda i=row["id"]: sb.table("dm_relay").delete().eq("id", i).execute())
        except Exception:
            log.exception("dm loop error")
        await asyncio.sleep(POLL)

async def room_loop():
    while True:
        try:
            for rid in list(rooms):
                since = last_room.get(rid) or now_iso()
                rows, err = await table_select(lambda r=rid, s=since: sb.table("room_messages").select("*").eq("room_id", r).gt("created_at", s).order("created_at").limit(50).execute())
                for m in rows or []:
                    last_room[rid] = m["created_at"]
                    if m.get("user_id") == BOT_ID or m.get("message_type") == "system": continue
                    text = (m.get("content") or "").strip()
                    media_url = m.get("media_url")
                    message_type = m.get("message_type")
                    # نحتاج معالجة رسالة الصورة حتى لو كان content فارغاً، لأن نشر@ ينتظر الصورة في الرسالة التالية.
                    if text or ((rid, m.get("user_id")) in publish_pending and media_url):
                        reply = await handle_room(rid, text, m.get("user_id"), media_url, message_type)
                        if reply: await room_send(rid, reply)
        except Exception:
            log.exception("room loop error")
        await asyncio.sleep(POLL)


async def heartbeat_loop():
    while True:
        now = time.time()
        for rid in list(rooms):
            await rpc("room_heartbeat", {"_room": rid})
            # الحرب مفتوحة المدة — لا تنتهي تلقائيًا
        # تنظيف طلبات نشر@ القديمة
        for key, info in list(publish_pending.items()):
            created = info.get("at") if isinstance(info, dict) else info
            if now - (created or 0) > 120:
                publish_pending.pop(key, None)
        await asyncio.sleep(10)

async def session_loop():
    while True:
        await asyncio.sleep(1800)
        await run(lambda: sb.auth.refresh_session())

async def leave_all_for_disconnect():
    saved = load_rooms_saved()
    for rid in list(rooms):
        try:
            await rpc("room_leave", {"_room": rid})
        except Exception:
            log.exception("failed to leave room on network outage: %s", rid)
    rooms.clear(); last_room.clear()
    return saved

async def restore_saved_rooms():
    saved = load_rooms_saved()
    for rid, name in saved.items():
        try:
            data, err = await rpc("room_join", {"_room": rid, "_password": C.get("room_password", "")})
            if err:
                log.warning("rejoin %s failed: %s", name, err)
                continue
            rooms[rid], last_room[rid] = name, now_iso()
        except Exception:
            log.exception("rejoin room failed: %s", name)

async def network_loop():
    online = True
    while True:
        try:
            async with http.get("https://www.google.com/generate_204",
                                 timeout=aiohttp.ClientTimeout(total=8)) as resp:
                ok = resp.status < 500
        except Exception:
            ok = False
        if online and not ok:
            log.warning("Internet disconnected: leaving all bot rooms")
            await leave_all_for_disconnect()
            online = False
        elif not online and ok:
            log.info("Internet restored: rejoining saved rooms")
            await restore_saved_rooms()
            online = True
        await asyncio.sleep(10)

async def main():
    global http, BOT_ID
    http = aiohttp.ClientSession()
    try:
        await start_media_server()
        email = await resolve_email()
        res, err = await run(lambda: sb.auth.sign_in_with_password({"email": email, "password": PASSWORD}))
        if err or not res.user: raise RuntimeError("فشل الدخول")
        BOT_ID = res.user.id
        cookie_ok, cookie_msg = youtube_cookie_status()
        log.info("YouTube cookies: %s | %s", "OK" if cookie_ok else "NOT-READY", cookie_msg)
        log.info("YouTube player clients: %s | PO token: %s", os.environ.get("YOUTUBE_PLAYER_CLIENTS") or C.get("youtube_player_clients", "web_safari,tv,web"), "configured" if YOUTUBE_PO_TOKEN else "not configured")
        await prepare_game_assets()
        global AUTH_ACCESS_TOKEN
        AUTH_ACCESS_TOKEN = getattr(getattr(res, "session", None), "access_token", None)
        await restore_rooms()
        # إذا كانت الغرف محفوظة من قبل، أعد الانضمام إليها حتى لو خرج البوت بسبب انقطاع الشبكة.
        if not rooms:
            await restore_saved_rooms()
        log.info("البوت جاهز كـ @%s", USERNAME)
        music_task = asyncio.create_task(music_worker_queue(), name="music-queue")
        cleanup_task = asyncio.create_task(posts_and_leftovers_cleanup(), name="cleanup")
        try:
            await asyncio.gather(dm_loop(), room_loop(), heartbeat_loop(), session_loop(), network_loop())
        finally:
            for task in (cleanup_task, music_task):
                task.cancel()
                try: await task
                except asyncio.CancelledError: pass
    finally:
        await stop_media_server()
        await http.close()

async def resolve_email():
    data, _ = await rpc("lookup_auth_email", {"_username": USERNAME})
    if isinstance(data, str) and "@" in data: return data
    rows, _ = await table_select(lambda: sb.table("profiles").select("auth_email").eq("username", USERNAME).limit(1).execute())
    if rows and rows[0].get("auth_email"): return rows[0]["auth_email"]
    raise RuntimeError("تعذر إيجاد البريد")

async def join(name):
    room = await find_room(name)
    if not room: return False, "الغرفة غير موجودة"
    data, err = await rpc("room_join", {"_room": room["id"], "_password": C.get("room_password", "")})
    if err: return False, err
    rooms[room["id"]], last_room[room["id"]] = room["name"], now_iso()
    saved = load_rooms_saved(); saved[room["id"]] = room["name"]; save_rooms_saved(saved)
    return True, f"تم الدخول لـ {room['name']}"

async def leave(name):
    room = await find_room(name)
    if not room: return False, "الغرفة غير موجودة"
    _, err = await rpc("room_leave", {"_room": room["id"]})
    if err: return False, err
    rooms.pop(room["id"], None); last_room.pop(room["id"], None)
    saved = load_rooms_saved(); saved.pop(room["id"], None); save_rooms_saved(saved)
    return True, f"تم الخروج من {room['name']}"

async def find_room(name):
    rows, _ = await table_select(lambda: sb.table("rooms").select("id,name").eq("name", name.strip()).limit(1).execute())
    return rows[0] if rows else None

async def restore_rooms():
    rows, _ = await table_select(lambda: sb.table("room_members").select("room_id").eq("user_id", BOT_ID).execute())
    ids = [r["room_id"] for r in rows or []]
    if ids:
        names, _ = await table_select(lambda: sb.table("rooms").select("id,name").in_("id", ids).execute())
        for r in names or []: rooms[r["id"]], last_room[r["id"]] = r["name"], now_iso()

if __name__ == "__main__":
    try: asyncio.run(main())
    except KeyboardInterrupt: pass
    except Exception as e: log.error("خطأ: %s", e); sys.exit(1)
