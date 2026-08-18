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


if YOUTUBE_COOKIES_ENV:
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
    return bool(YOUTUBE_COOKIES_PATH) and os.path.isfile(YOUTUBE_COOKIES_PATH)


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
        if has_youtube_cookies():
            options["cookiefile"] = YOUTUBE_COOKIES_PATH
            options["extractor_args"] = {"youtube": {"player_client": ["web", "mweb", "tv"]}}
        else:
            options["extractor_args"] = {
                "youtube": {"player_client": ["tv", "ios", "web_safari", "web"]}
            }
    elif source_label == "TikTok" and os.path.isfile(TIKTOK_COOKIES_PATH):
        options["cookiefile"] = TIKTOK_COOKIES_PATH
    return options


PIPED_APIS = [x.strip().rstrip("/") for x in C.get("piped_apis", [
    "https://pipedapi.kavin.rocks",
    "https://pipedapi.leptons.xyz",
    "https://piped-api.privacy.com.de",
    "https://pipedapi.adminforge.de",
]) if str(x).strip()]
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
global_game_cooldown_until = 0.0
last_music_started = 0.0
music_queue = asyncio.Queue()      # room_id -> game data
music_state = {}     # room_id -> آخر أغنية شغّلها البوت
music_tasks = {}      # room_id -> مهمة البحث/التشغيل الخلفية
publish_pending = {}  # (room_id, user_id) -> وقت طلب نشر@
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
    "race": TWEMOJI + "1f3c1.png",
    "bribe": TWEMOJI + "1f4b0.png",
    "basket": TWEMOJI + "1f3c0.png",
    "drone": TWEMOJI + "1f681.png",
    "frog": TWEMOJI + "1f438.png",
    "cards": TWEMOJI + "1f0cf.png",
    "ball": TWEMOJI + "26bd.png",
    "boxing": game_asset("defense_action.jpg"),
    "fight": game_asset("fight_action.jpg"),
    "job": TWEMOJI + "1f477.png",
    "meet": TWEMOJI + "1f91d.png",
    "slap": game_asset("slap_action.jpg"),
    "volcano": TWEMOJI + "1f30b.png",
    "ghost": TWEMOJI + "1f47b.png",
    "bet": TWEMOJI + "1f3b2.png",
    "war": game_asset("fight_action.jpg"),
    "rob": TWEMOJI + "1f4b0.png",
    "luck": TWEMOJI + "1f340.png",
    "dice": TWEMOJI + "1f3b2.png",
    "marriage": TWEMOJI + "1f48d.png",
    "challenge": TWEMOJI + "1f4aa.png",
    "mine": TWEMOJI + "26cf.png",
    # ألعاب جديدة بصور PNG مولّدة داخل مجلد assets
    "sniper": game_asset("game_sniper.png"),
    "fishing": game_asset("game_fishing.png"),
    "treasure": game_asset("game_treasure.png"),
    "sword": game_asset("game_sword.png"),
    "guess": game_asset("game_treasure.png"),
    "quiz": game_asset("game_sword.png")
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


async def game_cooldown():
    global global_game_cooldown_until
    now = time.time()
    if now < global_game_cooldown_until:
        return False, int(global_game_cooldown_until - now)
    global_game_cooldown_until = now + 30
    return True, 0

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
    max_width = max(100, box_right - box_left - 24)
    line_color = tuple(C.get("gift_text_color", [255, 255, 255]))
    shadow = (0, 0, 0, 180)
    for label, name, y in (("FROM:", sender_name, from_y), ("TO:", receiver_name, to_y)):
        text = shape_text(f"{label} @{name}")
        font = fit_font(text, max_width)
        bbox = draw.textbbox((0, 0), text, font=font, stroke_width=1)
        x = (width - (bbox[2] - bbox[0])) // 2
        draw.text((x + 2, y + 2), text, font=font, fill=shadow, stroke_width=2, stroke_fill=shadow)
        draw.text((x, y), text, font=font, fill=line_color, stroke_width=1, stroke_fill=(20, 20, 20, 220))
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
        f"💳 رصيدك المتبقي: {remaining_points} نقطة"
    )
    await room_send(rid, card)
    # الهدية تُنفّذ مرة واحدة في الغرفة الأصلية، ثم يُنشر إعلانها وصورتها في كل غرف البوت الأخرى.
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
    }
    await run(lambda: sb.table("room_messages").insert(payload).execute())

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
    """Search YouTube metadata without requiring a playable format.
    Playback/download is handled separately with several format fallbacks."""
    q = str(search_query).strip()
    if q.lower().startswith("ytsearch1:"):
        q = q.split(":", 1)[1].strip()

    for api in PIPED_APIS:
        try:
            async with http.get(
                f"{api}/search", params={"q": q, "filter": "videos"},
                timeout=aiohttp.ClientTimeout(total=12),
                headers={"User-Agent": "Mozilla/5.0"}
            ) as resp:
                if resp.status != 200:
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
                }
        except Exception as e:
            log.warning("Piped search failed %s: %s", api, e)

    if yt_dlp is None:
        return None

    def extract():
        options = yt_base_options("YouTube")
        options.update({"skip_download": True, "default_search": "ytsearch1"})
        with yt_dlp.YoutubeDL(options) as ydl:
            info = ydl.extract_info(f"ytsearch1:{q}", download=False)
            entry = (info.get("entries") or [None])[0] if info else info
            if not entry:
                return None
            return {
                "id": entry.get("id"), "title": entry.get("title") or "المقطع",
                "artist": entry.get("uploader") or entry.get("artist") or "YouTube",
                "youtube_url": entry.get("webpage_url") or entry.get("original_url"),
                "thumbnail": entry.get("thumbnail"), "duration": entry.get("duration") or 0,
            }
    try:
        return await asyncio.to_thread(extract)
    except Exception as e:
        log.warning("yt-dlp metadata search failed: %s", e)
        return None
async def _yt_download_audio(page_url, source_label, piped_api=None, video_id=None):
    """Download audio using Piped first, then multiple yt-dlp fallbacks.
    This avoids failing when YouTube removes a particular requested format."""
    temp_dir = Path(tempfile.mkdtemp(prefix="bot_audio_"))
    try:
        if piped_api and video_id:
            try:
                async with http.get(
                    f"{piped_api}/streams/{video_id}",
                    timeout=aiohttp.ClientTimeout(total=25),
                    headers={"User-Agent": "Mozilla/5.0"}
                ) as resp:
                    if resp.status == 200:
                        info = await resp.json(content_type=None)
                        streams = info.get("audioStreams") or []
                        streams = sorted(
                            streams,
                            key=lambda x: float(x.get("bitrate") or 0),
                            reverse=True,
                        )
                        for stream in streams:
                            url = stream.get("url")
                            if not url:
                                continue
                            try:
                                ext = ".m4a" if "mp4" in str(stream.get("mimeType", "")) else ".webm"
                                out = temp_dir / f"audio{ext}"
                                async with http.get(url, timeout=aiohttp.ClientTimeout(total=120)) as ar:
                                    if ar.status == 200:
                                        with out.open("wb") as f:
                                            async for chunk in ar.content.iter_chunked(1024 * 256):
                                                f.write(chunk)
                                        if out.stat().st_size > 4096:
                                            return out, None
                            except Exception:
                                continue
            except Exception as e:
                log.warning("Piped audio download failed: %s", e)

        if yt_dlp is None:
            return None, "مكتبة yt-dlp غير مثبتة، ولم يتوفر مصدر Piped."

        def download_with_format(fmt, suffix="audio"):
            options = yt_base_options(source_label)
            options.update({
                "format": fmt,
                "outtmpl": str(temp_dir / f"{suffix}.%(ext)s"),
            })
            with yt_dlp.YoutubeDL(options) as ydl:
                ydl.download([page_url])

        # Try several selectors because available formats vary by video/client.
        formats = [
            "bestaudio[ext=m4a]/bestaudio[ext=webm]/bestaudio/best",
            "bestaudio/best",
            "best[ext=mp4]/best",
        ]
        last_error = None
        for idx, fmt in enumerate(formats):
            try:
                for p in temp_dir.glob("*"):
                    if p.is_file() and p.suffix not in (".part", ".ytdl"):
                        try:
                            p.unlink()
                        except OSError:
                            pass
                await asyncio.to_thread(download_with_format, fmt, f"audio_{idx}")
                files = [
                    p for p in temp_dir.iterdir()
                    if p.is_file() and p.suffix not in (".part", ".ytdl") and p.stat().st_size > 4096
                ]
                if files:
                    return max(files, key=lambda p: p.stat().st_size), None
            except Exception as e:
                last_error = e
                log.warning("yt-dlp audio format failed (%s): %s", fmt, e)

        if last_error:
            log.warning("All audio download formats failed: %s", last_error)
        return None, f"{type(last_error).__name__}: {last_error}" if last_error else f"تعذر تنزيل الصوت من {source_label}: سبب غير معروف"
    except Exception as e:
        log.warning("%s audio download failed: %s", source_label, e)
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
        return f"{base_url}/{quote(target.name)}"

    raise RuntimeError(
        f"تعذر نشر ملف {kind}: لم يتم تحديد PUBLIC_BASE_URL/Railway domain "
        f"ولم ينجح Supabase Storage."
    )


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
    app.router.add_get("/gifts/{name}", gift_handler)
    runner = web.AppRunner(app, access_log=None)
    await runner.setup()
    media_runner = runner
    media_site = web.TCPSite(runner, "0.0.0.0", MEDIA_SERVER_PORT)
    await media_site.start()
    log.info("خادم ملفات الموسيقى يعمل على 0.0.0.0:%s | PUBLIC_BASE_URL=%s",
             MEDIA_SERVER_PORT, PUBLIC_BASE_URL or "(غير مضبوط)")


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
            track = await _yt_extract(sq)
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
            track = await _yt_extract(variant)
            if track and track.get("youtube_url"):
                track["source"] = "YouTube"
                track["search_query"] = variant
                return track, None
        except Exception as e:
            errors.append(f"{variant}: {type(e).__name__}: {e}")
            log.warning("youtube search error (%s): %s", variant, e)
    detail = " | ".join(errors[-2:]) if errors else "لا توجد نتائج من مصادر البحث"
    return None, f"لم أجد الأغنية المطلوبة على يوتيوب. تفاصيل البحث: {detail}"


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


def music_caption(requester_name):
    """رسالة مختصرة: اسم من طلب الأغنية فقط + أزرار الإعجاب وإعادة النشر."""
    return f"🎵 @{requester_name}\n❤️ إعجاب | 🔁 إعادة نشر"


async def play_track(rid, track, source_label, requester_name=None):
    if not track:
        return False, "لم أجد المقطع المطلوب"

    track, err = await _prepare_music_track(track, source_label)
    if err:
        return False, err

    requester_name = requester_name or USERNAME
    track.update({"requester_id": BOT_ID, "requester_name": requester_name})
    music_state[rid] = track
    title = track.get("title", "المقطع")
    caption = music_caption(requester_name)
    media_url = track.get("audio_url")
    if not media_url:
        # لا نُخفي الرابط إذا فشل التنزيل: إرسال الرابط المباشر يسمح للعميل
        # بفتحه/تشغيله إذا كان Giant Chat يدعم تشغيل روابط الوسائط.
        direct_url = track.get("youtube_url") or track.get("spotify_url") or track.get("tiktok_url")
        if direct_url:
            for room_id in await all_room_ids():
                try:
                    await room_send(room_id, f"{caption}\n▶️ {direct_url}")
                except Exception:
                    log.exception("music link broadcast failed for room %s", room_id)
            await report_music_error_to_masters(
                rid, source_label, title,
                "تعذر تنزيل ملف الصوت، تم إرسال الرابط المباشر بدل البصمة الصوتية.",
                stage="بديل الرابط",
            )
            return True, None
        return False, "تعذر تجهيز ملف الصوت ولا يوجد رابط تشغيل مباشر"

    # لا تُرسل صورة الأغنية إطلاقاً — الرسالة مختصرة باسم الطالب فقط.
    duration_ms = int(float(track.get("duration") or 0) * 1000)
    log.info("إرسال صوت Giant Chat: room=%s type=voice format=%s url=%s",
             rid, track.get("media_format", ""), media_url)

    sent = 0
    for room_id in await all_room_ids():
        try:
            music_state[room_id] = track
            await room_send_media(
                room_id,
                caption,
                media_url,
                m_type="voice",
                duration_ms=duration_ms,
            )
            sent += 1
        except Exception:
            log.exception("music broadcast failed for room %s", room_id)

    if not sent:
        return False, "تعذر إرسال الصوت إلى أي غرفة"
    return True, None



async def music_worker_queue():
    global last_music_started
    interval = max(0, int(C.get("music_interval_seconds", 0)))
    while True:
        item = await music_queue.get()
        rid, query, source = item[0], item[1], item[2]
        requester_name = item[3] if len(item) > 3 else USERNAME
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
                await room_send(rid, "❌ لم أتمكن من تشغيل هذه الأغنية الآن، جرّب اسماً آخر.")
                await report_music_error_to_masters(rid, source, query, err, stage="البحث")
            else:
                ok, out = await play_track(rid, track, used_source, requester_name)
                if not ok and out:
                    await room_send(rid, "❌ تعذر تجهيز الصوت، حاول مرة أخرى.")
                    await report_music_error_to_masters(rid, used_source, query, out, stage="التنزيل/التجهيز/الإرسال")
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.exception("music queue worker failed")
            try:
                await room_send(rid, "❌ حدث خطأ أثناء تجهيز الصوت.")
                await report_music_error_to_masters(rid, source, query, f"{type(exc).__name__}: {exc}", stage="استثناء غير متوقع")
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
🎯 قنص — قنص الهدف
🎣 صيد — صيد السمك
🗺️ كنز — البحث عن الكنز
⚔️ سيف — مبارزة سيوف
🔢 تخمين رقم — خمن رقماً من 1 إلى 10
🧠 ذكاء — سؤال سريع، ثم: ذكاء الإجابة
━━━━━━━━━━━━━━━━━━━━
كل لعبة ترسل صورة اللعبة مع النتيجة والنقاط.
"""

HELP_ROOM = """━━━━━━━━ 🤖 جميع أوامر البوت ━━━━━━━━
[1] الحساب والنقاط
points / نقاطي — عرض نقاطك
توب — أفضل 10 لاعبين
dp@الاسم — صورة المستخدم
p@الاسم — البروفايل
st@الاسم — حالة المستخدم

[2] الموسيقى
تشغيل اسم الأغنية — YouTube
تيك اسم الأغنية — TikTok
.تشغيل اسم الأغنية — Spotify (يبحث عن النسخة الصوتية)
مشاركة — مشاركة الأغنية الحالية
تخطي — تخطي الأغنية
ايقاف — إيقاف الصوت
📢 تُنشر الأغنية في جميع الغرف باسم من طلبها مع ❤️ إعجاب | 🔁 إعادة نشر

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



async def handle_room(rid, text, uid, media_url=None, message_type=None):
    if await is_banned(rid, uid): return None
    p_name = await username_of(uid)
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
        msg = text.split(maxsplit=1)[1].strip()
        await broadcast_text("📢 " + msg)
        return "✅ تم نشر الرسالة في كل الغرف."
    if text.startswith("نشرصورة ") or text.startswith("broadcast_image "):
        if not await is_master(uid, p_name): return "🚫 للماستر فقط."
        url = text.split(maxsplit=1)[1].strip()
        await broadcast_media("📢", url, m_type="image")
        return "✅ تم نشر الصورة في كل الغرف."

    # نشر@: الماستر يطلب صورة في رسالة لاحقة، ثم ينشرها في كل الغرف.
    publish_key = (rid, uid)
    if text.strip() in ("نشر@", "publish@"):
        if not await is_master(uid, p_name):
            return "🚫 للماستر فقط."
        publish_pending[publish_key] = time.time()
        return "🖼️ أرسل الصورة الآن خلال دقيقتين، وسيتم نشرها في كل الغرف مع اسم الغرفة وخيارات: ❤️ إعجاب | 👎 مااعجاب | ↩️ رد."

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

    pending_at = publish_pending.get(publish_key)
    if pending_at is not None:
        if time.time() - pending_at > 120:
            publish_pending.pop(publish_key, None)
        elif message_type in ("image", "photo", "sticker") and media_url:
            publish_pending.pop(publish_key, None)
            source_room = rooms.get(rid, "الغرفة")
            # Re-host the image on the bot's public Railway endpoint when possible.
            public_media_url = await cache_publish_media(media_url) or media_url
            published = 0
            for target_rid in await all_room_ids():
                try:
                    target_name = rooms.get(target_rid, "الغرفة")
                    caption = (
                        f"📢 نشر@ من غرفة: {source_room}\n"
                        f"👤 بواسطة: @{p_name}\n"
                        f"🏠 {target_name}\n"
                        f"❤️ إعجاب   👎 عدم إعجاب   ↩️ رد"
                    )
                    await room_send_media(target_rid, caption, public_media_url, m_type="image")
                    await room_send(target_rid, "❤️ إعجاب | 👎 عدم إعجاب | ↩️ رد على الصورة")
                    published += 1
                except Exception:
                    log.exception("publish@ failed for room %s", target_rid)
            return f"✅ تم نشر الصورة في {published} غرفة."
        elif media_url:
            return "⚠️ الملف المرسل ليس صورة. أرسل صورة بعد أمر نشر@."

    if text == "المسترات":
        masters = load_masters()
        return "👑 قائمة الماسترز:\n" + "\n".join([f"• @{m}" for m in masters]) if masters else "👤 المالك فقط هو الماستر حالياً."

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
                     "سباق","race","رشوة","سلة","قصف","اضرب","ورق","سدد","ملاكمة","بركان","شبح","حظ","نرد","تعدين",
                     "قنص","صيد","كنز","سيف","تخمين","guess","ذكاء","quiz"}

    async def require_game_cooldown():
        ok_cd, rem_cd = await game_cooldown()
        if not ok_cd:
            return f"⏳ انتظر {rem_cd} ثانية قبل تشغيل لعبة أخرى. الفاصل 30 ثانية ومشترك بين جميع المستخدمين."
        return None

    if cmd in ("تشغيل", "play", "شغل"):

        if not arg: return "❌ اكتب: تشغيل اسم الأغنية"
        await music_queue.put((rid, arg, "YouTube", p_name))
        return "🎵 جاري تجهيز الأغنية من المصدر المتاح..."

    if cmd in ("مشاركة", "share"):
        current = music_state.get(rid)
        if not current:
            return "❌ لا توجد أغنية حالياً للمشاركة."
        return f"🎵 مشاركة الأغنية\n🎶 {current.get('title','المقطع')} — {current.get('artist','')}\n🔗 {current.get('spotify_url') or current.get('youtube_url') or ''}"

    if cmd in (".تشغيل", "spotify", "سبوتيفاي"):
        if not arg:
            return "❌ اكتب: .تشغيل اسم الأغنية أو .تشغيل رابط Spotify"
        await music_queue.put((rid, arg, "Spotify", p_name))
        return "🎵 جاري تجهيز الأغنية من Spotify..."

    if cmd in ("تيك", ".تيك", "tiktok", "tik"):
        if not arg: return "❌ اكتب: تيك اسم الأغنية"
        await music_queue.put((rid, arg, "TikTok", p_name))
        return "🎵 جاري تجهيز صوت TikTok..."

    # لعبة الحرب: لاعبان، سفينة في 1..6، 3 محاولات لكل لاعب، مع انتهاء تلقائي.
    if cmd in ("حرب", "war"):
        key = f"war_{rid}"
        game = war_games.get(key)
        now = time.time()

        if game and now >= game.get("expires_at", 0):
            war_games.pop(key, None)
            await room_send(rid, "⌛ انتهت لعبة الحرب تلقائياً بسبب عدم وجود حركة خلال المهلة. اكتب «حرب» لبدء لعبة جديدة.")
            game = None

        if not game:
            war_games[key] = {
                "p1": uid, "p1_name": p_name, "p2": None, "p2_name": None,
                "ship": random.randint(1, 6),
                "tries": {str(uid): 0},
                "guesses": {str(uid): []},
                "turn": uid,
                "created_at": now,
                "turn_started_at": now,
                "expires_at": now + 90,
            }
            await room_send_media(
                rid,
                f"⚔️ @{p_name} بدأ لعبة الحرب!\n⏳ جاري الانتظار: اكتب «حرب» للانضمام.\n🎯 لكل لاعب 3 محاولات من 1 إلى 6.\n⌛ تنتهي اللعبة تلقائياً إذا لم ينضم خصم.",
                GAME_IMAGES["war"],
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
            await room_send_media(
                rid,
                f"⚔️ بدأت الحرب!\n👤 @{game['p1_name']} ضد @{p_name}\n🎯 دور @{game['p1_name']} — اكتب رقماً من 1 إلى 6.\n🔥 لكل لاعب 3 محاولات.\n⌛ المهلة دقيقتان لكل حركة.",
                GAME_IMAGES["war"],
            )
            return None
        return "⚠️ الحرب ممتلئة. انتظر انتهاء اللعبة."

    # تخمين الحرب يكون برقم منفصل 1..6.
    if game := war_games.get(f"war_{rid}"):
        now = time.time()
        if now >= game.get("expires_at", 0):
            war_games.pop(f"war_{rid}", None)
            return "⌛ انتهت الحرب بسبب انتهاء المهلة. اكتب «حرب» لبدء لعبة جديدة."

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
                add_points(uid, p_name, 60)
                await room_send_media(
                    rid,
                    f"💥🚢 تم تدمير السفينة!\n🏆 الفائز: @{p_name} (+60 نقطة)\n🚢 كانت السفينة في الرقم {game['ship']}.",
                    GAME_IMAGES["war"],
                )
                war_games.pop(f"war_{rid}", None)
                return None

            other = game["p2"] if uid == game["p1"] else game["p1"]
            other_key = str(other)
            current_tries = game["tries"].get(skey, 0)
            other_tries = game["tries"].get(other_key, 0)

            if current_tries >= 3 and other_tries >= 3:
                await room_send_media(
                    rid,
                    f"🤝 انتهت المحاولات لكلا اللاعبين.\n🚢 السفينة كانت في {game['ship']} ولم تُدمر.",
                    GAME_IMAGES["war"],
                )
                war_games.pop(f"war_{rid}", None)
                return None

            # إذا كان الخصم استنفد محاولاته، لا نعطيه الدور؛ يستمر اللاعب الحالي.
            if other_tries >= 3:
                game["turn"] = uid
                next_name = p_name
                remaining = 3 - current_tries
            else:
                game["turn"] = other
                next_name = game["p2_name"] if uid == game["p1"] else game["p1_name"]
                remaining = 3 - other_tries

            game["turn_started_at"] = now
            game["expires_at"] = now + 120
            await room_send(
                rid,
                f"❌ الرقم {n} ليس السفينة.\n🔄 دور @{next_name} — بقيت له {remaining} محاولات."
            )
            return None

    if cmd in ("سرقة", "rob"):
        win = random.randint(1, 100) <= 40
        add_points(uid, p_name, 25 if win else -15)
        await room_send_media(rid, f"💰 {'نجحت السرقة!' if win else 'فشلت السرقة..'} @{p_name}\n💵 النتيجة: {'+25' if win else '-15'} نقطة.", GAME_IMAGES["rob"])
        return None

    if cmd in ("قتال", "fight"):
        win = random.choice([True, False])
        add_points(uid, p_name, 15 if win else -5)
        await room_send_media(rid, f"🥊 {'هزمت خصمك!' if win else 'تلقيت ضربة قاضية..'} @{p_name}\n💰 النتيجة: {'+15' if win else '-5'} نقطة.", GAME_IMAGES["fight"])
        return None

    if cmd in ("عمل", "job"):
        ok, rem = check_cooldown(uid, p_name, "work", 3600)
        if not ok: return f"⏳ | عد للعمل بعد {rem // 60} دقيقة."
        cd_error = await require_game_cooldown()
        if cd_error:
            return cd_error
        salary = random.randint(50, 150)
        add_points(uid, p_name, salary)
        await room_send_media(rid, f"👷 | عملت بجد يا @{p_name}.\n💵 راتبك: {salary} نقطة.", GAME_IMAGES["job"])
        return None

    if cmd in ("سباق", "race"):
        win = random.choice([True, False])
        add_points(uid, p_name, 30 if win else -10)
        await room_send_media(rid, f"🏁 {'فزت بالسباق!' if win else 'تعطلت سيارتك..'} @{p_name}\n💰 النتيجة: {'+30' if win else '-10'} نقطة.", GAME_IMAGES["race"])
        return None

    if cmd in ("كف", "slap"):
        game = kaf_games.get(f"slap_{rid}")
        if not game:
            cd_error = await require_game_cooldown()
            if cd_error:
                return cd_error
            kaf_games[f"slap_{rid}"] = {"player1": uid, "p1_name": p_name}
            await room_send_media(rid, f"✅ {p_name}\nwaiting for an opponent for automatic slap game...", GAME_IMAGES["slap"])
        else:
            if game["player1"] == uid: return "⚠️ أنت تنتظر منافس!"
            p1_name = game["p1_name"]
            winner = random.choice([p1_name, p_name])
            kaf_games.pop(f"slap_{rid}")
            add_points(uid if winner == p_name else game["player1"], winner, 15)
            await room_send_media(rid, f"👋 💥 Slap | الضربة 💥 👋\n🥊 المنافسة بين @{p1_name} و @{p_name}\n🏆 الفائز: @{winner} (+15 ن)", GAME_IMAGES["slap"])
        return None

    if cmd in ("مضاربة", "bet"):
        try: amount = int(arg)
        except: return "❌ اكتب: مضاربة [عدد النقاط]"
        points, user_data = get_user_data(uid, p_name)
        if user_data["points"] < amount: return f"⚠️ نقاطك لا تكفي ({user_data['points']})"
        game_key = f"bet_{rid}"
        game = kaf_games.get(game_key)
        if not game:
            cd_error = await require_game_cooldown()
            if cd_error:
                return cd_error
            kaf_games[game_key] = {"player1": uid, "p1_name": p_name, "amount": amount}
            await room_send_media(rid, f"🎲 | @{p_name} يراهن بـ {amount} نقطة!\nاكتب مضاربة {amount} للقبول أو انتظر البوت.", GAME_IMAGES["bet"])
            async def bot_bet():
                await asyncio.sleep(30)
                g = kaf_games.get(game_key)
                if g and g["player1"] == uid:
                    win = random.choice([True, False])
                    kaf_games.pop(game_key)
                    add_points(uid, p_name, amount if win else -amount)
                    await room_send_media(rid, f"🤖 | {'فزت على البوت!' if win else 'خسرت ضد البوت..'} @{p_name}\n💰 النتيجة: {amount if win else -amount} ن.", GAME_IMAGES["bet"])
            asyncio.create_task(bot_bet())
        else:
            if game["player1"] == uid: return "⚠️ أنت صاحب الرهان!"
            if amount != game["amount"]: return f"❌ الرهان هو {game['amount']} ن."
            p1_name = game["p1_name"]
            winner = random.choice([p1_name, p_name])
            kaf_games.pop(game_key)
            add_points(uid if winner == p_name else game["player1"], winner, amount)
            add_points(game["player1"] if winner == p_name else uid, p1_name if winner == p_name else p_name, -amount)
            await room_send_media(rid, f"🎲 | تمت المضاربة بين @{p1_name} و @{p_name}..\n🏆 الفائز: @{winner}!", GAME_IMAGES["bet"])
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
        return f"👤 @{p_name} ➔ ✨ {d['points']} نقطة"

    if cmd == "توب":
        pts = load_points()
        sorted_u = sorted(pts.items(), key=lambda x: x[1].get("points", 0), reverse=True)[:10]
        if not sorted_u: return "📭 القائمة فارغة."
        msg = "🏆 ━━━━━━ TOP 10 ━━━━━━ 🏆\n"
        emojis = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣", "🔟"]
        for i, (u, d) in enumerate(sorted_u):
            msg += f"{emojis[i]} @{d['username']} ➔ {d['points']} ن\n"
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
        "نرد": ("dice", 15, -10, 50, "🎲 فوز بالنرد!", "🎲 خسارة بالنرد.."),
        "قنص": ("sniper", 45, -15, 45, "🎯 طلقة في المنتصف!", "🎯 أخطأت الهدف.."),
        "صيد": ("fishing", 35, -5, 55, "🎣 اصطدت سمكة كبيرة!", "🎣 هرب الصيد.."),
        "كنز": ("treasure", 80, -25, 35, "🏆 عثرت على الكنز!", "🗺️ الخريطة كانت مزيفة.."),
        "سيف": ("sword", 40, -20, 50, "⚔️ ضربة سيف قاتلة!", "⚔️ كُسر سيفك..")
    }
    
    if cmd in games_map:
        cd_error = await require_game_cooldown()
        if cd_error:
            return cd_error
        key, win_p, lose_p, chance, win_m, lose_m = games_map[cmd]
        win = random.randint(1, 100) <= chance
        add_points(uid, p_name, win_p if win else lose_p)
        await room_send_media(rid, f"{win_m if win else lose_m} @{p_name}\n💰 النتيجة: {win_p if win else lose_p} ن.", GAME_IMAGES[key])
        return None

    # 🎯 لعبة تخمين رقم من 1 إلى 10
    if cmd in ("تخمين", "guess"):
        cd_error = await require_game_cooldown()
        if cd_error:
            return cd_error
        try:
            n = int(arg)
        except Exception:
            return "❌ اكتب: تخمين رقم من 1 إلى 10"
        if not 1 <= n <= 10:
            return "❌ الرقم يجب أن يكون بين 1 و 10."
        secret = random.randint(1, 10)
        win = (n == secret)
        add_points(uid, p_name, 90 if win else -10)
        await room_send_media(
            rid,
            f"{'🎉 تخمين صحيح!' if win else '❌ تخمين خاطئ..'} @{p_name}\n🔢 الرقم كان {secret}\n💰 النتيجة: {'+90' if win else '-10'} ن.",
            GAME_IMAGES["guess"],
        )
        return None

    # 🧠 لعبة سؤال سريع (حساب)
    if cmd in ("ذكاء", "quiz"):
        key = f"quiz_{rid}"
        game = kaf_games.get(key)
        if game and time.time() - game.get("at", 0) > 60:
            kaf_games.pop(key, None)
            game = None
        if not game:
            cd_error = await require_game_cooldown()
            if cd_error:
                return cd_error
            a, b = random.randint(2, 12), random.randint(2, 12)
            kaf_games[key] = {"answer": a * b, "at": time.time()}
            await room_send_media(
                rid,
                f"🧠 سؤال سريع: كم يساوي {a} × {b}؟\n⌛ لديك دقيقة — اكتب: ذكاء الإجابة\n🏅 الجائزة 70 نقطة.",
                GAME_IMAGES["quiz"],
            )
            return None
        try:
            ans = int(arg)
        except Exception:
            return "❌ اكتب: ذكاء ثم الإجابة."
        if ans == game["answer"]:
            kaf_games.pop(key, None)
            add_points(uid, p_name, 70)
            await room_send_media(rid, f"🧠 إجابة صحيحة! @{p_name}\n💰 +70 نقطة.", GAME_IMAGES["quiz"])
        else:
            return "❌ إجابة خاطئة، حاول مرة أخرى."
        return None

    if cmd == "تعدين":
        cd_error = await require_game_cooldown()
        if cd_error:
            return cd_error
        ok, rem = check_cooldown(uid, p_name, "mine", 14400)
        if not ok: return f"⛏️ عد بعد {rem // 3600} ساعة."
        found = random.randint(200, 500); add_points(uid, p_name, found)
        await room_send_media(rid, f"⛏️ وجدت ذهباً! @{p_name}\n💰 كسبت {found} ن.", GAME_IMAGES["mine"])
        return None

    if cmd == "زواج":
        pts, d = get_user_data(uid, p_name)
        if d.get("married_to"): return f"💍 متزوج من @{d['married_to']}"
        others = [u["username"] for i, u in pts.items() if i != uid]
        if not others: return "💔 لا أحد للزواج."
        partner = random.choice(others); d["married_to"] = partner
        pts[uid] = d; save_json(POINTS_PATH, pts)
        await room_send_media(rid, f"❤️ مبروك زواج @{p_name} من @{partner} 💍", GAME_IMAGES["marriage"])
        return None

    if cmd in ("تخطي", "skip"):
        ok, out = await skip(rid); return out
    if cmd in ("ايقاف", "stop"):
        ok, out = await stop(rid); return out
    if cmd in ("مساعدة", "help", ".help"): return HELP_ROOM
    
    return None

# ----------------------------- الحلقات -----------------------------
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
            game = war_games.get(f"war_{rid}")
            if game and now >= game.get("expires_at", 0):
                war_games.pop(f"war_{rid}", None)
                try:
                    await room_send(rid, "⌛ انتهت لعبة الحرب تلقائياً بسبب انتهاء المهلة. اكتب «حرب» لبدء لعبة جديدة.")
                except Exception:
                    log.exception("failed to announce war timeout for room %s", rid)
        # تنظيف طلبات نشر@ القديمة
        for key, created in list(publish_pending.items()):
            if now - created > 120:
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
        await prepare_game_assets()
        global AUTH_ACCESS_TOKEN
        AUTH_ACCESS_TOKEN = getattr(getattr(res, "session", None), "access_token", None)
        await restore_rooms()
        # إذا كانت الغرف محفوظة من قبل، أعد الانضمام إليها حتى لو خرج البوت بسبب انقطاع الشبكة.
        if not rooms:
            await restore_saved_rooms()
        log.info("البوت جاهز كـ @%s", USERNAME)
        music_task = asyncio.create_task(music_worker_queue(), name="music-queue")
        try:
            await asyncio.gather(dm_loop(), room_loop(), heartbeat_loop(), session_loop(), network_loop())
        finally:
            music_task.cancel()
            try: await music_task
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
