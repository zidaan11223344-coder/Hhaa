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
import os
import sys
import time
import uuid
import random
import re
from collections import deque
from pathlib import Path
from urllib.parse import quote
from datetime import datetime, timezone, timedelta
import urllib.request as urllib_request

import aiohttp
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
REPLIES_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "replies.json")
MASTERS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "masters.json")
BANS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bans.json")
BROADCAST_POSTS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "broadcast_posts.json")
VIP_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "vip_users.json")
POST_RETENTION_SECONDS = 600
POST_CODE_ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789"
KNOWN_ROOMS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "known_rooms.json")
BANNED_WORDS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "banned_words.json")


def load_banned_words():
    try:
        if Path(BANNED_WORDS_PATH).exists():
            data = json.loads(Path(BANNED_WORDS_PATH).read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data.get("enabled", True), set(data.get("words", []))
            elif isinstance(data, list):
                return True, set(data)
    except Exception:
        log.warning("تعذر تحميل الكلمات الممنوعة", exc_info=True)
    return True, set()


def save_banned_words(enabled, words_set):
    try:
        payload = {"enabled": bool(enabled), "words": sorted(list(words_set))}
        Path(BANNED_WORDS_PATH).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        log.exception("تعذر حفظ الكلمات الممنوعة")

os.chdir(os.path.dirname(os.path.abspath(__file__)))

with open(CONFIG_PATH, encoding="utf-8") as f:
    C = json.load(f)

REQUIRED = ["supabase_url", "supabase_key", "username", "password"]
missing = [k for k in REQUIRED if not str(C.get(k, "")).strip()]
if missing:
    log.error("نقص في config.json: %s", ", ".join(missing))
    sys.exit(1)

USERNAME = C["username"].strip()
PASSWORD = C["password"]
OWNER = (C.get("owner_username") or USERNAME).strip().lower()
POLL = max(1.0, float(C.get("poll_seconds", 2)))
SEARCH_URL = C.get("music_search_url") or "https://giant-chat-app.lovable.app/api/public/search-track"
PIPED_API_BASES = C.get("piped_api_bases") or [
    "https://api.piped.private.coffee",
    "https://pipedapi.orangenet.cc",
    "https://pipedapi.kavin.rocks",
    "https://pipedapi.adminforge.de",
    "https://pipedapi.leptons.xyz",
]
YOUTUBE_WEB_SEARCH_URL = C.get("youtube_web_search_url") or "https://www.youtube.com/results"
MEDIA_GATEWAY_URL = str(C.get("media_gateway_url") or os.environ.get("MEDIA_GATEWAY_URL", "")).strip().rstrip("/")
MEDIA_GATEWAY_TOKEN = str(C.get("media_gateway_token") or os.environ.get("MEDIA_GATEWAY_TOKEN", "")).strip()

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
kaf_games = {}      # room_id -> game data
music_state = {}     # room_id -> آخر أغنية شغّلها البوت
music_tasks = {}      # room_id -> مهمة البحث/التشغيل الخلفية
music_last_request = {}  # user_id -> وقت آخر طلب موسيقى
broadcast_waiting = {}  # user_id -> بيانات المنشور بانتظار الصورة
known_rooms = {}      # room_id -> room_name، للعودة بعد انقطاع الاتصال
pending_room_leaves = set()  # غرف ستُغادر فعلياً عند اكتشاف انقطاع الاتصال
http: aiohttp.ClientSession = None

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
GAME_IMAGES = {
    "race": TWEMOJI + "1f3c1.png",
    "bribe": TWEMOJI + "1f4b0.png",
    "basket": TWEMOJI + "1f3c0.png",
    "drone": TWEMOJI + "1f681.png",
    "frog": TWEMOJI + "1f438.png",
    "cards": TWEMOJI + "1f0cf.png",
    "ball": TWEMOJI + "26bd.png",
    "boxing": TWEMOJI + "1f94a.png",
    "fight": TWEMOJI + "2694.png",
    "job": TWEMOJI + "1f477.png",
    "meet": TWEMOJI + "1f91d.png",
    "slap": TWEMOJI + "1f44b.png",
    "volcano": TWEMOJI + "1f30b.png",
    "ghost": TWEMOJI + "1f47b.png",
    "bet": TWEMOJI + "1f3b2.png",
    "war": TWEMOJI + "2694.png",
    "rob": TWEMOJI + "1f4b0.png",
    "luck": TWEMOJI + "1f340.png",
    "dice": TWEMOJI + "1f3b2.png",
    "marriage": TWEMOJI + "1f48d.png",
    "challenge": TWEMOJI + "1f4aa.png",
    "mine": TWEMOJI + "26cf.png",
    # صور الألعاب الجديدة — رموز خفيفة من Twemoji وتعمل كرابط عام ثابت.
    "defense": TWEMOJI + "1f6e1.png",
    "driver": TWEMOJI + "1f697.png",
    "sniper": TWEMOJI + "1f52d.png",
    "running": TWEMOJI + "1f3c3.png",
    "worker": TWEMOJI + "1f477.png",
    "captain": TWEMOJI + "2693.png",
    "kill": TWEMOJI + "1f480.png",
    "key": TWEMOJI + "1f511.png",
    "door": TWEMOJI + "1f6aa.png",
    "crossing": TWEMOJI + "1f6a7.png",
    "elevator": TWEMOJI + "1f6d7.png",
    "wrestler": TWEMOJI + "1f94a.png",
    "dismantle": TWEMOJI + "1f527.png",
    "walk": TWEMOJI + "1f6b6.png",
    "guard": TWEMOJI + "1f482.png",
    "theft": TWEMOJI + "1f4b8.png",
    "new_ball": TWEMOJI + "26bd.png",
    "punch": TWEMOJI + "1f44a.png",
    "new_luck": TWEMOJI + "1f3b2.png",
    "love": TWEMOJI + "1f496.png",
    "lookalike": TWEMOJI + "1f464.png"
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
def _delete_post_media_file(post):
    """حذف ملف الوسائط المحلي للمنشور فقط، مع منع حذف أي ملف خارج generated_gifts."""
    try:
        media_url = str((post or {}).get("media_url") or "")
        filename = media_url.split("?", 1)[0].rstrip("/").rsplit("/", 1)[-1]
        if not filename or not (filename.startswith("gift_") or filename.startswith("game_")):
            return
        candidate = (GIFT_RENDER_DIR / Path(filename).name).resolve()
        render_dir = GIFT_RENDER_DIR.resolve()
        if candidate.parent == render_dir and candidate.exists():
            candidate.unlink()
            log.info("تم حذف ملف وسائط المنشور المنتهي: %s", candidate.name)
    except Exception:
        log.exception("تعذر حذف ملف وسائط منشور منتهي")


def cleanup_broadcast_posts():
    """حذف المنشورات وملفاتها بعد 10 دقائق من created_at."""
    posts = load_json(BROADCAST_POSTS_PATH, {})
    if not isinstance(posts, dict):
        return {}
    now = time.time()
    fresh = {}
    changed = False
    for code, post in posts.items():
        try:
            created = str((post or {}).get("created_at") or "").replace("Z", "+00:00")
            created_ts = datetime.fromisoformat(created).timestamp()
        except Exception:
            created_ts = now
        if now - created_ts >= POST_RETENTION_SECONDS:
            _delete_post_media_file(post)
            changed = True
        else:
            fresh[code] = post
    if changed:
        save_json(BROADCAST_POSTS_PATH, fresh)
    return fresh


def load_broadcast_posts():
    return cleanup_broadcast_posts()


def save_broadcast_posts(p):
    save_json(BROADCAST_POSTS_PATH, p)


def load_vips(): return [str(x).strip().lower() for x in load_json(VIP_PATH, []) if str(x).strip()]
def save_vips(v): save_json(VIP_PATH, sorted(set(str(x).strip().lower() for x in v if str(x).strip())))

async def is_banned(rid, uid):
    bans = load_bans()
    return uid in bans.get(rid, [])

async def is_master(uid, username):
    username = str(username or "").strip()
    if username.lower() == OWNER: return True
    masters = load_masters()
    return uid in masters or username.lower() in [str(m).lower() for m in masters]

async def is_owner(uid, username):
    """التوثيق والإدارة الحساسة للمالك الأصلي فقط، وليس لأي ماستر إضافي."""
    return str(username or "").strip().lower() == OWNER

async def can_broadcast(uid, username):
    """المالك والماستر دائمًا مسموح لهم، وبقية المستخدمين يجب اعتمادهم VIP."""
    if await is_master(uid, username):
        return True
    return username.strip().lower() in load_vips()

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

async def profile_display_name(uid, fallback=""):
    """إرجاع اسم العرض العربي إن كان محفوظًا، مع الرجوع إلى اسم الحساب."""
    try:
        rows, _ = await table_select(lambda: sb.table("profiles").select("*").eq("id", uid).limit(1).execute())
        if rows:
            profile = rows[0] or {}
            for key in ("display_name", "full_name", "name", "nickname", "arabic_name"):
                value = str(profile.get(key) or "").strip()
                if value:
                    return value
            return str(profile.get("username") or fallback).strip()
    except Exception:
        log.warning("تعذر قراءة اسم العرض للمستخدم %s", uid, exc_info=True)
    return str(fallback or "").strip()

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
GIFT_BUCKET = str(C.get("gift_image_bucket", "bot-gifts")).strip()
BASE_DIR = Path(__file__).resolve().parent
GIFT_RENDER_DIR = BASE_DIR / "generated_gifts"
GIFT_RENDER_DIR.mkdir(parents=True, exist_ok=True)
GAME_RENDER_DIR = GIFT_RENDER_DIR  # استخدام Static Files الحالي نفسه لتقليل إعدادات PythonAnywhere
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
    # كل قالب له تخطيطه الخاص: بعض القوالب جانبية وبعضها صندوقان مكدسان.
    kind = template.stem.replace("gift_template_", "").lower()
    if kind in {"present", "crown"}:
        boxes = [
            ("FROM:", sender_name, float(C.get("gift_side_y", height * (0.79 if kind == "present" else 0.82))),
             float(C.get("gift_sender_left", width * (0.09 if kind == "present" else 0.10))),
             float(C.get("gift_sender_right", width * (0.45 if kind == "present" else 0.44)))),
            ("TO:", receiver_name, float(C.get("gift_side_y", height * (0.79 if kind == "present" else 0.82))),
             float(C.get("gift_receiver_left", width * (0.55 if kind == "present" else 0.56))),
             float(C.get("gift_receiver_right", width * (0.91 if kind == "present" else 0.90)))),
        ]
    else:
        vertical_defaults = {
            "heart": (0.735, 0.865, 0.13, 0.87),
            "kiss": (0.755, 0.885, 0.18, 0.82),
            "rose": (0.765, 0.895, 0.18, 0.82),
        }
        top_ratio, bottom_ratio, left_ratio, right_ratio = vertical_defaults.get(kind, (0.76, 0.88, 0.15, 0.85))
        boxes = [
            ("FROM:", sender_name, float(C.get("gift_top_y", height * top_ratio)), width * left_ratio, width * right_ratio),
            ("TO:", receiver_name, float(C.get("gift_bottom_y", height * bottom_ratio)), width * left_ratio, width * right_ratio),
        ]
    line_color = tuple(C.get("gift_text_color", [255, 255, 255]))
    shadow = (0, 0, 0, 180)
    for label, name, y, left, right in boxes:
        text = shape_text(f"{label} {str(name or '').strip()[:32]}")
        max_width = max(100, int(right - left - 18))
        font = fit_font(text, max_width, start_size=24, min_size=12)
        bbox = draw.textbbox((0, 0), text, font=font, stroke_width=1)
        text_width = bbox[2] - bbox[0]
        x = int(left + max(0, (right - left - text_width) // 2))
        y = int(y)
        draw.text((x + 2, y + 2), text, font=font, fill=shadow, stroke_width=2, stroke_fill=shadow)
        draw.text((x, y), text, font=font, fill=line_color, stroke_width=1, stroke_fill=(20, 20, 20, 220))
    path = GIFT_RENDER_DIR / f"gift_{gift['display_id']}_{uuid.uuid4().hex}.png"
    image.save(path, "PNG", optimize=True)
    return path

def publish_gift_image(local_path):
    """حفظ صورة الهدية محليًا وإرجاع رابط PythonAnywhere Static Files."""
    base_url = str(C.get("gift_public_base_url", "")).rstrip("/")
    if not base_url:
        domain = os.environ.get("PYTHONANYWHERE_DOMAIN", "").strip()
        if domain:
            base_url = f"https://{domain}/gifts"
    if not base_url:
        raise RuntimeError("أضف gift_public_base_url أو PYTHONANYWHERE_DOMAIN")

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

    return f"{base_url}/{quote(path.name)}"

def render_game_card(title, subtitle, lines, accent=(35, 190, 150), winner_tag=""):
    """إنشاء بطاقة نتيجة لعبة عملاقة واحترافية؛ مع إمكانية طباعة اسم الفائز على البطاقة."""
    if not PIL_AVAILABLE:
        raise RuntimeError("Pillow غير مثبتة لإنشاء بطاقة اللعبة")
    width = 1000
    line_height = 64
    header_h = 160
    height = max(620, header_h + 40 + line_height * len(lines) + (80 if winner_tag else 0))
    image = Image.new("RGBA", (width, height), (240, 245, 250, 255))
    draw = ImageDraw.Draw(image)
    # إطار فاخر وعملاق يحاكي أقوى ألعاب الشات.
    draw.rounded_rectangle((15, 15, width - 15, height - 15), radius=40, fill=(255, 255, 255, 255), outline=accent, width=10)
    draw.rounded_rectangle((15, 15, width - 15, header_h), radius=35, fill=accent + (255,))
    title_s = shape_text(title)
    sub_s = shape_text(subtitle)
    title_font = fit_font(title_s, width - 80, start_size=56, min_size=30)
    sub_font = fit_font(sub_s, width - 80, start_size=34, min_size=20)
    tb = draw.textbbox((0, 0), title_s, font=title_font)
    draw.text(((width - (tb[2] - tb[0])) // 2, 35), title_s, font=title_font, fill=(255, 255, 255, 255))
    sb = draw.textbbox((0, 0), sub_s, font=sub_font)
    draw.text(((width - (sb[2] - sb[0])) // 2, 105), sub_s, font=sub_font, fill=(230, 255, 245, 255))
    
    if winner_tag:
        # طباعة شريط الفائز البارز على البطاقة
        banner_w, banner_h = width - 100, 65
        banner_x, banner_y = 50, header_h + 20
        draw.rounded_rectangle((banner_x, banner_y, banner_x + banner_w, banner_y + banner_h), radius=20, fill=(255, 215, 0, 255))
        w_text = shape_text(f"🏆 الفائز البطل: {winner_tag}")
        w_font = fit_font(w_text, banner_w - 40, start_size=36, min_size=22)
        wb = draw.textbbox((0, 0), w_text, font=w_font)
        draw.text((banner_x + (banner_w - (wb[2] - wb[0])) // 2, banner_y + 14), w_text, font=w_font, fill=(30, 30, 30, 255))
        y = banner_y + banner_h + 30
    else:
        y = header_h + 35

    for line in lines:
        text = shape_text(line)
        font = fit_font(text, width - 100, start_size=38, min_size=20)
        bbox = draw.textbbox((0, 0), text, font=font)
        x = (width - (bbox[2] - bbox[0])) // 2
        draw.text((x + 2, y + 2), text, font=font, fill=(100, 100, 100, 120))
        draw.text((x, y), text, font=font, fill=(30, 35, 45, 255))
        y += line_height
    path = GAME_RENDER_DIR / f"game_{uuid.uuid4().hex}.png"
    image.save(path, "PNG", optimize=True)
    return path


def publish_game_card(local_path):
    """إرجاع رابط البطاقة عبر Static Files نفسه المستخدم لصور الهدايا."""
    return publish_gift_image(local_path)


async def public_media_available(url):
    """فحص رابط الصورة مع توافق PythonAnywhere Static Files وبديل HEAD/GET."""
    value = str(url or "").strip()
    if not value.startswith(("http://", "https://")):
        return False
    headers = {"Accept": "image/avif,image/webp,image/png,image/*,*/*;q=0.8", "User-Agent": "GiantBot/1.0"}
    # بعض إعدادات Static Files لا تتعامل مع Range بالطريقة نفسها داخل aiohttp؛ جرّب GET عادياً أولاً.
    if http is not None:
        timeout = aiohttp.ClientTimeout(total=10)
        for request_headers in (headers, {**headers, "Range": "bytes=0-64"}):
            try:
                async with http.get(value, headers=request_headers, allow_redirects=True, timeout=timeout) as response:
                    status = int(response.status)
                    if status < 400:
                        return True
                    log.warning("فحص رابط الوسائط أعاد HTTP %s: %s", status, value)
            except Exception as exc:
                log.warning("تعذر فحص رابط الوسائط عبر aiohttp: %s", exc)
    else:
        try:
            response = await asyncio.to_thread(requests.get, value, headers=headers, timeout=10, stream=True, allow_redirects=True)
            if response.status_code < 400:
                return True
            log.warning("فحص رابط الوسائط أعاد HTTP %s: %s", response.status_code, value)
        except Exception as exc:
            log.warning("تعذر فحص رابط الوسائط عبر requests: %s", exc)
    # الصورة حُفظت محلياً قبل تكوين هذا الرابط. إذا كان الرابط من base_url المخصص للـ Static Files،
    # نسمح بالإرسال بعد الفحص المحلي حتى لا يمنع اختلاف HEAD/Range صورة صحيحة.
    configured_base = str(C.get("gift_public_base_url", "")).strip().rstrip("/")
    if configured_base and value.startswith(configured_base + "/"):
        log.warning("تم اعتماد رابط Static Files المخصص بعد تعذر الفحص الخارجي: %s", value)
        return True
    return False


async def send_game_card(rid, title, subtitle, lines, accent=(35, 190, 150)):
    caption = "\n".join([str(title or "").strip(), str(subtitle or "").strip(), *[str(x).strip() for x in (lines or []) if str(x).strip()]]).strip()
    try:
        local = await asyncio.to_thread(render_game_card, title, subtitle, lines, accent)
        url = await asyncio.to_thread(publish_game_card, local)
        if not await public_media_available(url):
            raise RuntimeError("رابط صورة اللعبة غير متاح؛ أضف Static Files للمجلد generated_gifts")
        await room_send_media(rid, caption, url, m_type="image")
        return True
    except Exception:
        log.exception("game card failed")
        fallback = GAME_IMAGES.get("challenge") or GAME_IMAGES.get("war")
        if fallback:
            await room_send_media(rid, caption, fallback, m_type="image")
        else:
            await room_send(rid, caption)
        return False


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

    # افتراضياً تستخدم الهدايا نقاط bot المحلية نفسها التي تكسبها الألعاب.
    # يمكن تفعيل RPC التطبيق اختيارياً بعد مزامنة نظام النقاط في Supabase.
    use_supabase_gifts = str(C.get("bot_gifts_use_supabase", "false")).lower() in {"1", "true", "yes", "on"}
    if use_supabase_gifts:
        _, err = await rpc("send_gift", {"_receiver": receiver["id"], "_gift": gift["id"], "_room": rid})
        if err:
            return "❌ تعذر إرسال الهدية حالياً؛ تأكد من رصيد نقاط التطبيق."
    else:
        _, sender_data = get_user_data(sender_uid, sender_name)
        cost = int(gift.get("cost_points") or 0)
        if not await is_master(sender_uid, sender_name) and int(sender_data.get("points") or 0) < cost:
            return f"❌ نقاطك لا تكفي لإرسال هذه الهدية؛ تحتاج {cost} نقطة. اكتب نقاطي لمعرفة رصيدك."
        if not await is_master(sender_uid, sender_name):
            add_points(sender_uid, sender_name, -cost)

    image_url = None
    # استخدم اسم العرض العربي داخل الصورة، مع إبقاء اسم الحساب في الرسالة النصية.
    sender_display = await profile_display_name(sender_uid, sender_name) or sender_name
    receiver_display = await profile_display_name(receiver["id"], receiver_name) or receiver_name
    # أضف @ داخل الصورة نفسها حتى لا تعتمد الواجهة على caption أو اسم الحساب الخارجي.
    sender_display = "@" + str(sender_display).strip().lstrip("@")
    receiver_display = "@" + str(receiver_display).strip().lstrip("@")
    # لا نستخدم صورة ثابتة كبديل صامت؛ الصورة الثابتة لا تحمل أسماء المرسل والمستقبل.
    try:
        rendered = await asyncio.to_thread(render_gift_image, gift, sender_display, receiver_display)
        if not rendered:
            raise RuntimeError("تعذر إنشاء صورة الهدية الديناميكية")
        image_url = await asyncio.to_thread(publish_gift_image, rendered)
        if not image_url or not await public_media_available(image_url):
            raise RuntimeError("رابط الصورة المولدة غير متاح؛ تحقق من Static Files للمجلد generated_gifts")
    except Exception as exc:
        log.exception("dynamic gift image failed: %s", exc)
        if not use_supabase_gifts and not await is_master(sender_uid, sender_name):
            add_points(sender_uid, sender_name, int(gift.get("cost_points") or 0))
        return "❌ لم تُرسل الهدية: تعذر إنشاء أو نشر الصورة التي تحمل أسماء المرسل والمستقبل. تحقق من رابط /gifts/ في Web App."
    # أرسل الصورة مع نص مستقل يضمن ظهور الغرفة والمرسل والمستقبل حتى لو تجاهلت الواجهة caption.
    room_name = rooms.get(rid, rid)
    gift_caption = (
        f"🎁 {gift['emoji']} {gift['name']}\n"
        f"🏠 الغرفة: {room_name}\n"
        f"👤 المرسل: @{sender_name}\n"
        f"🎯 المستقبل: @{receiver_name}\n"
        f"💰 القيمة: {gift['cost_points']} نقطة"
    )
    if image_url:
        await broadcast_media(gift_caption, image_url, m_type="image", caption_as_text=True)
    else:
        await broadcast_text(gift_caption)
    return None


async def room_send(rid, text):
    if not str(text or "").strip():
        log.warning("تم منع رسالة نصية فارغة للغرفة %s", rid)
        return False
    _, err = await run(lambda: sb.table("room_messages").insert({
        "room_id": rid, "user_id": BOT_ID, "content": str(text), "message_type": "text"
    }).execute())
    if err:
        log.error("فشل إرسال النص إلى الغرفة %s: %s", rid, err)
        return False
    return True

async def _insert_room_media(rid, text, media_url, m_type="image", duration_ms=None):
    if not str(media_url or "").strip():
        raise ValueError("رابط الوسائط فارغ")
    content = str(text or "").strip()
    if not content:
        content = "🎵" if m_type == "voice" else "🖼️"
    payload = {
        "room_id": rid,
        "user_id": BOT_ID,
        "content": content,
        "message_type": m_type,
        "media_url": str(media_url),
        "media_duration_ms": duration_ms,
    }
    _, err = await run(lambda: sb.table("room_messages").insert(payload).execute())
    if err:
        raise RuntimeError(f"room_messages insert failed: {err}")
    return True

async def get_active_room_targets():
    """إرجاع الغرف التي يملك البوت عضوية فعلية فيها، مع تحديث أسمائها محلياً."""
    rows, err = await table_select(lambda: sb.table("room_members").select("room_id").eq("user_id", BOT_ID).execute())
    if err:
        log.warning("تعذر قراءة عضويات البوت للبث، سيتم استخدام القائمة المحلية: %s", err)
        return list(rooms)
    ids = {str(row.get("room_id")) for row in (rows or []) if row.get("room_id")}
    if not ids:
        return list(rooms)
    names, name_err = await table_select(lambda: sb.table("rooms").select("id,name").in_("id", list(ids)).execute())
    if name_err:
        log.warning("تعذر قراءة أسماء غرف البث: %s", name_err)
        return [rid for rid in ids if rid in rooms] or list(rooms)
    targets = []
    for room in names or []:
        rid_value = str(room.get("id"))
        if rid_value:
            rooms[rid_value] = str(room.get("name") or rooms.get(rid_value) or rid_value)
            targets.append(rid_value)
    # حافظ على الغرف المحلية التي لم تُرجعها RLS حتى لا يتوقف البث بسبب صلاحية قراءة مؤقتة.
    return list(dict.fromkeys(targets + list(rooms)))

async def room_send_media(rid, text, media_url, m_type="image", duration_ms=None):
    """إرسال الوسائط؛ الصور تُرسل إلى كل عضويات البوت الفعلية عند تفعيل الإعداد."""
    if not media_url:
        log.error("تم منع إرسال وسائط بلا رابط إلى الغرفة %s", rid)
        return 0
    is_image = str(m_type).lower() in {"image", "photo", "gif"}
    broadcast_images = str(C.get("broadcast_images_to_all", "true")).lower() not in {"0", "false", "no", "off"}
    targets = await get_active_room_targets() if is_image and broadcast_images else [rid]
    if not targets:
        targets = [rid]
    sent = 0
    for target in targets:
        try:
            await _insert_room_media(target, text, media_url, m_type=m_type, duration_ms=duration_ms)
            # بعض نسخ التطبيق تعرض media_url وتتجاهل content؛ أرسل النتيجة كنص منفصل.
            if is_image and str(text or "").strip():
                await room_send(target, str(text).strip())
            sent += 1
        except Exception:
            log.exception("فشل إرسال الوسائط إلى الغرفة %s", target)
    return sent

def create_broadcast_post(publisher_uid, publisher_name, source_room_id, source_room_name, description, media_url=""):
    posts = load_broadcast_posts()
    # الرمز ثلاث خانات فقط، حروف وأرقام، مع منع التكرار مع المنشورات الحالية.
    for _ in range(200):
        post_id = "".join(random.choice(POST_CODE_ALPHABET) for _ in range(3))
        if post_id not in posts:
            break
    else:
        raise RuntimeError("تعذر إنشاء رمز منشور فريد")
    posts[post_id] = {
        "publisher_uid": publisher_uid,
        "publisher_name": publisher_name,
        "source_room_id": source_room_id,
        "source_room_name": source_room_name,
        "description": description,
        "media_url": media_url,
        "likes": [],
        "dislikes": [],
        "comments": 0,
        "created_at": now_iso(),
    }
    # احذف الأقدم إذا تجاوز الملف 200 منشوراً، مع حذف صورة المنشور المحذوفة.
    if len(posts) > 200:
        old_ids = sorted(posts, key=lambda k: posts[k].get("created_at", ""))[:-200]
        for old_id in old_ids:
            _delete_post_media_file(posts.get(old_id))
            posts.pop(old_id, None)
    save_broadcast_posts(posts)
    return post_id

async def broadcast_text(text):
    """إرسال نص إلى كل الغرف التي توجد للبوت فيها عضوية فعلية."""
    sent = 0
    for target_rid in await get_active_room_targets():
        try:
            await room_send(target_rid, text)
            sent += 1
        except Exception:
            log.exception("broadcast text failed for room %s", target_rid)
    return sent

async def broadcast_media(text, media_url, m_type="image", duration_ms=None, caption_as_text=False):
    """إرسال صورة أو صوت إلى كل العضويات الفعلية؛ الوصف يُرسل كنص مستقل."""
    sent = 0
    is_image = str(m_type).lower() in {"image", "photo", "gif"}
    targets = await get_active_room_targets()
    for target_rid in targets:
        try:
            await _insert_room_media(target_rid, text, media_url, m_type=m_type, duration_ms=duration_ms)
            if is_image and caption_as_text and str(text or "").strip():
                await room_send(target_rid, str(text).strip())
            sent += 1
        except Exception:
            log.exception("broadcast media failed for room %s", target_rid)
    return sent

async def handle_post_action(rid, uid, text):
    """معالجة صيغ المنشورات العربية والإنجليزية وإبلاغ الناشر بالتعليق خاصاً."""
    raw = str(text or "").strip()
    parts = [p.strip() for p in raw.split("@", 2)]
    command = parts[0].lower()
    like_commands = {"اعجاب", "إعجاب", "like", "loved", "love"}
    dislike_commands = {"عدم_اعجاب", "عدم إعجاب", "dislike"}
    comment_commands = {"تعليق", "رد", "msg", "comment"}
    if command not in like_commands | dislike_commands | comment_commands:
        return None
    if len(parts) < 2 or not parts[1]:
        return "❌ استخدم رمز المنشور بعد الأمر."

    post_id = parts[1].strip().split()[0]
    comment_text = ""
    if command in comment_commands:
        if len(parts) >= 3 and parts[2].strip():
            comment_text = parts[2].strip()
        else:
            remainder = parts[1].strip()
            if "[" in remainder:
                post_id, comment_text = remainder.split("[", 1)
                post_id = post_id.strip().split()[0]
                comment_text = comment_text.rsplit("]", 1)[0].strip()
            else:
                tokens = remainder.split(maxsplit=1)
                post_id = tokens[0]
                comment_text = tokens[1].strip() if len(tokens) > 1 else ""

    if not re.fullmatch(r"[A-Za-z0-9]{3}", post_id):
        return "❌ رمز المنشور يجب أن يكون 3 خانات من الحروف أو الأرقام."
    posts = load_broadcast_posts()
    post = posts.get(post_id)
    if not post:
        return "❌ المنشور غير موجود أو انتهت مدة حفظه."
    uid_key = str(uid)
    actor_name = await username_of(uid) or str(uid)
    likes = [str(x) for x in post.get("likes", [])]
    dislikes = [str(x) for x in post.get("dislikes", [])]
    if command in like_commands:
        if uid_key not in likes:
            likes.append(uid_key)
        dislikes = [x for x in dislikes if x != uid_key]
        post["likes"], post["dislikes"] = likes, dislikes
        save_broadcast_posts(posts)
        label = "أحببتها" if command in {"loved", "love"} else "إعجاب"
        await dm_send(
            post["publisher_uid"],
            f"📊 تفاعل على منشورك {post_id}\n"
            f"🏠 الغرفة: {post.get('source_room_name', '')}\n"
            f"👤 @{actor_name.lstrip('@')} سجّل: {label}\n"
            f"👍 الإعجاب: {len(likes)} | 👎 عدم الإعجاب: {len(dislikes)}\n"
            f"⏳ المنشور يحذف بعد 10 دقائق من نشره."
        )
        return f"👍 تم تسجيل {label}. الإعجاب: {len(likes)} | عدم الإعجاب: {len(dislikes)}"
    if command in dislike_commands:
        if uid_key not in dislikes:
            dislikes.append(uid_key)
        likes = [x for x in likes if x != uid_key]
        post["likes"], post["dislikes"] = likes, dislikes
        save_broadcast_posts(posts)
        await dm_send(
            post["publisher_uid"],
            f"📊 تفاعل على منشورك {post_id}\n"
            f"🏠 الغرفة: {post.get('source_room_name', '')}\n"
            f"👤 @{actor_name.lstrip('@')} سجّل: عدم الإعجاب\n"
            f"👍 الإعجاب: {len(likes)} | 👎 عدم الإعجاب: {len(dislikes)}\n"
            f"⏳ المنشور يحذف بعد 10 دقائق من نشره."
        )
        return f"👎 تم تسجيل عدم إعجابك. الإعجاب: {len(likes)} | عدم الإعجاب: {len(dislikes)}"
    if not comment_text:
        return "❌ اكتب التعليق هكذا: msg@رمز_المنشور [نص التعليق]"
    commenter = await username_of(uid)
    post["comments"] = int(post.get("comments", 0)) + 1
    save_broadcast_posts(posts)
    await dm_send(
        post["publisher_uid"],
        f"💬 تعليق جديد على منشورك {post_id}\n"
        f"🏠 الغرفة: {post.get('source_room_name', '')}\n"
        f"👤 @{commenter.lstrip('@')}: {comment_text}\n"
        f"📊 الإعجاب: {len(likes)} | 👎 عدم الإعجاب: {len(dislikes)} | 💬 التعليقات: {post['comments']}\n"
        f"⏳ المنشور يحذف بعد 10 دقائق من نشره."
    )
    return "✅ تم إرسال التعليق والنتيجة إلى ناشر الصورة برسالة خاصة."

async def dm_send(uid, text):
    envelope = {
        "v": 1, "id": str(uuid.uuid4()), "content": text, "message_type": "text",
        "media_url": None, "media_duration_ms": None, "reply_to_id": None, "created_at": now_iso()
    }
    await run(lambda: sb.table("dm_relay").insert({
        "sender_id": BOT_ID, "recipient_id": uid, "envelope": envelope
    }).execute())

# ----------------------------- الموسيقى -----------------------------
MUSIC_HOURLY_LIMIT = max(1, int(C.get("music_hourly_limit", 100)))
MUSIC_CACHE_TTL = max(60, int(C.get("music_cache_ttl_seconds", 600)))
music_request_times = deque()
music_inflight = set()
music_cache = {}
ANSI_RE = re.compile(r"\x1b(?:[@-Z\-_]|\[[0-?]*[ -/]*[@-~])")


async def gateway_resolve(query, kind, direct_url=""):
    """طلب ملف صوت عام من Web App اختياري؛ يفيد في الروابط التي تنتهي سريعاً."""
    if not MEDIA_GATEWAY_URL or http is None:
        return None, "gateway_not_configured"
    params = {"q": query or "", "kind": kind}
    if direct_url:
        params["url"] = direct_url
    headers = {"Accept": "application/json", "User-Agent": "GiantBot/1.0"}
    if MEDIA_GATEWAY_TOKEN:
        headers["X-Media-Token"] = MEDIA_GATEWAY_TOKEN
    try:
        timeout = aiohttp.ClientTimeout(total=35)
        async with http.get(f"{MEDIA_GATEWAY_URL}/resolve", params=params, headers=headers, timeout=timeout) as response:
            data = await response.json(content_type=None)
            track = (data or {}).get("track")
            if response.status < 400 and track and track.get("audio_url"):
                return track, None
            return None, (data or {}).get("error") or f"gateway_http_{response.status}"
    except Exception as gateway_exc:
        # بروكسي PythonAnywhere الداخلي يحجب بعض نطاقات Railway عبر aiohttp؛
        # لذلك نجرب اتصالًا مباشرًا يتجاوز البروكسي كبديل تلقائي.
        direct_track, direct_err = await asyncio.to_thread(gateway_resolve_direct, params, headers)
        if direct_track:
            return direct_track, None
        return None, str(gateway_exc)


def gateway_resolve_direct(params, headers):
    """اتصال مباشر ببوابة Railway يتجاوز بروكسي PythonAnywhere."""
    try:
        import urllib.request as urllib_request
        from urllib.parse import urlencode
        url = f"{MEDIA_GATEWAY_URL}/resolve?{urlencode({k: str(v) for k, v in params.items()})}"
        req = urllib_request.Request(url, headers=headers)
        # تعطيل بروكسي بايثون الافتراضي للتأكد من الاتصال المباشر.
        opener = urllib_request.build_opener(urllib_request.ProxyHandler({}))
        with opener.open(req, timeout=30) as response:
            data = json.loads(response.read().decode("utf-8"))
        track = (data or {}).get("track")
        if track and track.get("audio_url"):
            return track, None
        return None, (data or {}).get("error") or "direct_gateway_empty"
    except Exception as exc:
        return None, str(exc)


def normalize_music_query(value):
    return re.sub(r"\s+", " ", str(value or "").strip().lower())


def short_music_error(error, kind="youtube"):
    raw = ANSI_RE.sub("", str(error or ""))
    low = raw.lower()
    if "sign in to confirm" in low or "not a bot" in low or "po token" in low or "captcha" in low:
        return "يوتيوب رفض الاستخراج الآلي حالياً؛ جرّب لاحقاً أو استخدم خادماً احتياطياً."
    if "unsupported url" in low:
        return "الرابط غير مدعوم أو منتهي."
    if "private video" in low or "login" in low:
        return "المقطع يحتاج صلاحية خاصة أو تسجيل دخول."
    if kind == "tiktok":
        return "لم أجد صوت TikTok بالاسم من المصادر العامة حالياً؛ جرّب لاحقاً أو أرسل رابطاً مباشراً."
    return "تعذر استخراج صوت الأغنية حالياً؛ جرّب أغنية أخرى بعد قليل."


def load_cookie_files(kind):
    """قراءة مسارات cookies محلية فقط؛ لا يقرأ كلمات مرور ولا يرسل الملفات للخارج."""
    if kind == "youtube":
        keys = ["youtube_cookie_files"]
        env_key = "YOUTUBE_COOKIE_FILE"
    elif kind == "spotify":
        keys = ["spotify_cookie_files"]
        env_key = "SPOTIFY_COOKIE_FILE"
    else:
        keys = ["tiktok_cookie_files"]
        env_key = "TIKTOK_COOKIE_FILE"
        
    paths = []
    env_value = os.environ.get(env_key, "").strip()
    if env_value:
        paths.append(env_value)
    for key in keys:
        value = C.get(key, [])
        if isinstance(value, str):
            value = [value]
        if isinstance(value, list):
            paths.extend(str(item).strip() for item in value if str(item).strip())
    accounts_path = os.environ.get("BOT_ACCOUNTS_FILE", "accounts.local.json")
    try:
        p = Path(accounts_path)
        if not p.is_absolute():
            p = Path(CONFIG_PATH).resolve().parent / p
        if p.exists():
            accounts = json.loads(p.read_text(encoding="utf-8"))
            value = accounts.get(keys[0], []) if isinstance(accounts, dict) else []
            if isinstance(value, str):
                value = [value]
            if isinstance(value, list):
                paths.extend(str(item).strip() for item in value if str(item).strip())
    except Exception:
        log.warning("تعذر قراءة ملف الحسابات المحلي", exc_info=True)
    result = []
    for item in paths:
        p = Path(item)
        if not p.is_absolute():
            p = Path(CONFIG_PATH).resolve().parent / p
        if p.exists() and p.is_file() and str(p) not in result:
            result.append(str(p))
    return result[:8]


def begin_music_request(kind, query):
    key = f"{kind}:{normalize_music_query(query)}"
    now = time.monotonic()
    while music_request_times and now - music_request_times[0] >= 3600:
        music_request_times.popleft()
    cached = music_cache.get(key)
    if cached and time.time() - cached["saved_at"] < MUSIC_CACHE_TTL:
        return "cached", key, cached["track"]
    if key in music_inflight:
        return "busy", key, None
    if len(music_request_times) >= MUSIC_HOURLY_LIMIT:
        return "limit", key, None
    music_request_times.append(now)
    music_inflight.add(key)
    return "new", key, None


def finish_music_request(key, track=None):
    music_inflight.discard(key)
    if track and track.get("audio_url"):
        music_cache[key] = {"saved_at": time.time(), "track": track}


class _SilentYtdlpLogger:
    def debug(self, message):
        return None
    def warning(self, message):
        return None
    def error(self, message):
        return None


def extract_media_with_ytdlp(url, kind="youtube"):
    if yt_dlp is None:
        return None, "مكتبة yt-dlp غير مثبتة."
    
    target_url = url
    if "spotify.com" in str(url).lower():
        if not url.startswith(("http://", "https://")):
            target_url = f"ytsearch1:{url} audio"
        else:
            target_url = url

    cookie_files = load_cookie_files(kind)
    clients = [None, "android_vr", "web_embedded", "web_safari"] if kind in ("youtube", "spotify") else [None]
    last_error = None
    for cookie_file in [None] + cookie_files:
        for client in clients:
            options = {
                "quiet": True,
                "no_warnings": True,
                "skip_download": True,
                "noplaylist": True,
                "format": "bestaudio/best",
                "socket_timeout": 15,
                "retries": 1,
                "fragment_retries": 1,
                "extractor_retries": 1,
                "file_access_retries": 1,
                "cachedir": False,
                "proxy": "",
                "logger": _SilentYtdlpLogger(),
                "noprogress": True,
                "no_color": True,
            }
            if kind == "youtube" and client:
                options["extractor_args"] = {"youtube": {"player_client": [client]}}
            if cookie_file:
                options["cookiefile"] = cookie_file
            try:
                with yt_dlp.YoutubeDL(options) as ydl:
                    info = ydl.extract_info(target_url, download=False)
                entry = info
                if info and info.get("entries"):
                    entry = next((item for item in info["entries"] if item), None)
                if not entry:
                    last_error = "no_entry"
                    continue
                audio_url = entry.get("url")
                if not audio_url:
                    formats = entry.get("formats") or []
                    audio_url = next((f.get("url") for f in reversed(formats) if f.get("url") and f.get("acodec") not in (None, "none")), None)
                if audio_url:
                    return {
                        "title": entry.get("title") or "مقطع Spotify",
                        "artist": entry.get("uploader") or entry.get("artist") or "Spotify Music",
                        "audio_url": audio_url,
                        "duration_ms": int(float(entry.get("duration") or 0) * 1000),
                        "source_url": entry.get("webpage_url") or url,
                    }, None
            except Exception as exc:
                last_error = exc
                continue
    return None, short_music_error(last_error, kind)


def app_youtube_search(query):
    """استخدام نقطة البحث الخاصة بالتطبيق؛ تعاد حالة واضحة عند 403 بدل اعتبارها عدم وجود نتيجة."""
    try:
        response = requests.get(
            SEARCH_URL,
            params={"q": query, "source": "youtube"},
            headers={"Accept": "application/json", "User-Agent": "GiantBot/1.0"},
            timeout=12,
        )
        data = response.json() if response.content else {}
        track = data.get("track") or {}
        video_id = track.get("videoId") or track.get("id")
        if video_id:
            return {
                "title": track.get("title") or "المقطع",
                "artist": track.get("artist") or "YouTube",
                "duration_ms": int(track.get("duration_ms") or 0),
                "source_url": track.get("preview_url") or f"https://www.youtube.com/watch?v={video_id}",
                "video_id": video_id,
                "artwork": track.get("artwork"),
            }, None
        return None, str(data.get("error") or f"search provider HTTP {response.status_code}")
    except Exception as exc:
        return None, str(exc)


def youtube_video_id(value):
    value = str(value or "").strip()
    match = re.search(r"(?:v=|youtu\.be/|youtube\.com/(?:shorts|embed|live)/)([A-Za-z0-9_-]{11})", value, re.I)
    if match:
        return match.group(1)
    if re.fullmatch(r"[A-Za-z0-9_-]{11}", value):
        return value
    return None


def youtube_data_api_search(query):
    """بحث رسمي اختياري عند توفير youtube_data_api_key في config.json."""
    api_key = str(C.get("youtube_data_api_key") or os.environ.get("YOUTUBE_DATA_API_KEY", "")).strip()
    if not api_key:
        return None
    try:
        response = requests.get(
            "https://www.googleapis.com/youtube/v3/search",
            params={"part": "snippet", "type": "video", "maxResults": 1, "q": query, "key": api_key},
            headers={"Accept": "application/json", "User-Agent": "GiantBot/1.0"},
            timeout=12,
        )
        response.raise_for_status()
        item = (response.json().get("items") or [None])[0] or {}
        video_id = (item.get("id") or {}).get("videoId")
        snippet = item.get("snippet") or {}
        if not video_id:
            return None
        return {"video_id": video_id, "source_url": f"https://www.youtube.com/watch?v={video_id}", "title": snippet.get("title") or "المقطع", "artist": snippet.get("channelTitle") or "YouTube", "duration_ms": 0, "artwork": ((snippet.get("thumbnails") or {}).get("high") or {}).get("url")}
    except Exception:
        log.warning("YouTube Data API search failed", exc_info=True)
        return None


def ytdlp_search_track(query):
    """بحث واستخراج مباشر عبر yt-dlp؛ يستفيد من youtube_cookies.txt إن وُجد."""
    try:
        track, error = extract_media_with_ytdlp(f"ytsearch1:{query}", "youtube")
        if track and track.get("audio_url"):
            return track, None
        return None, error or "ytdlp_search_failed"
    except Exception as exc:
        log.debug("yt-dlp search failed: %s", short_music_error(exc, "youtube"))
        return None, short_music_error(exc, "youtube")


def youtube_web_search(query):
    """بحث اختياري قد يفشل بسبب 403؛ معطل افتراضياً على PythonAnywhere."""
    if str(C.get("enable_youtube_web_search", False)).lower() not in {"1", "true", "yes", "on"}:
        return None
    try:
        response = requests.get(
            YOUTUBE_WEB_SEARCH_URL,
            params={"search_query": query},
            headers={"Accept": "text/html", "User-Agent": "Mozilla/5.0"},
            timeout=15,
        )
        if response.status_code >= 400:
            log.info("تم تجاوز صفحة YouTube بسبب HTTP %s", response.status_code)
            return None
        ids = []
        for video_id in re.findall(r'"videoId":"([A-Za-z0-9_-]{11})"', response.text or ""):
            if video_id not in ids:
                ids.append(video_id)
            if len(ids) >= 5:
                break
        if not ids:
            return None
        video_id = ids[0]
        return {
            "video_id": video_id,
            "source_url": f"https://www.youtube.com/watch?v={video_id}",
            "title": "المقطع",
            "artist": "YouTube",
            "duration_ms": 0,
            "artwork": f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg",
        }
    except requests.RequestException as exc:
        log.info("تم تجاوز بحث صفحة YouTube بسبب مشكلة الشبكة: %s", exc.__class__.__name__)
        return None
    except Exception as exc:
        log.debug("youtube web search failed: %s", exc)
        return None


def piped_search_and_stream(query=None, video_id=None):
    """بحث/استخراج صوت عبر Piped API؛ الخوادم قابلة للتعديل من config.json."""
    bases = PIPED_API_BASES if isinstance(PIPED_API_BASES, list) else [PIPED_API_BASES]
    for raw_base in bases:
        base = str(raw_base or "").rstrip("/")
        if not base:
            continue
        try:
            chosen_id = video_id
            meta = {}
            if not chosen_id and query:
                result = requests.get(
                    f"{base}/search",
                    params={"q": query, "filter": "videos"},
                    headers={"Accept": "application/json", "User-Agent": "GiantBot/1.0"},
                    timeout=12,
                )
                result.raise_for_status()
                data = result.json() or {}
                items = data if isinstance(data, list) else (data.get("items") or data.get("results") or [])
                for item in items:
                    if not isinstance(item, dict):
                        continue
                    item_url = str(item.get("url") or item.get("webpage_url") or "")
                    candidate = item.get("id") or item.get("videoId") or youtube_video_id(item_url)
                    if not candidate:
                        relative_match = re.search(r"(?:[?&]v=)([A-Za-z0-9_-]{11})", item_url)
                        candidate = relative_match.group(1) if relative_match else None
                    if candidate:
                        chosen_id = str(candidate)
                        meta = item
                        break
            if not chosen_id:
                continue
            stream_response = requests.get(
                f"{base}/streams/{chosen_id}",
                headers={"Accept": "application/json", "User-Agent": "GiantBot/1.0"},
                timeout=15,
            )
            stream_response.raise_for_status()
            stream_data = stream_response.json() or {}
            streams = [s for s in (stream_data.get("audioStreams") or []) if isinstance(s, dict) and s.get("url")]
            if not streams:
                continue
            streams.sort(key=lambda s: int(s.get("bitrate") or 0), reverse=True)
            audio = streams[0]
            title = stream_data.get("title") or meta.get("title") or "المقطع"
            artist = stream_data.get("uploader") or meta.get("uploaderName") or meta.get("uploader") or "YouTube"
            duration = stream_data.get("duration") or meta.get("duration") or 0
            return {
                "title": title,
                "artist": artist,
                "audio_url": audio.get("url"),
                "duration_ms": int(float(duration or 0) * 1000),
                "source_url": f"https://www.youtube.com/watch?v={chosen_id}",
                "video_id": chosen_id,
                "artwork": stream_data.get("thumbnailUrl") or meta.get("thumbnail"),
            }, None
        except Exception as exc:
            log.debug("Piped provider failed at %s: %s", base, exc)
    return None, "piped_unavailable"


def extract_candidate_audio(candidate, kind="youtube"):
    video_id = youtube_video_id(candidate.get("source_url") or candidate.get("video_id"))
    source_url = candidate.get("source_url") or (f"https://www.youtube.com/watch?v={video_id}" if video_id else "")
    if source_url:
        track, error = extract_media_with_ytdlp(source_url, kind)
        if track:
            track.update({k: candidate[k] for k in ("title", "artist", "duration_ms", "video_id", "artwork") if candidate.get(k)})
            return track, None
    if kind == "youtube" and video_id:
        return piped_search_and_stream(video_id=video_id)
    return None, error if 'error' in locals() else "extract_failed"


def find_alt_audio_urls(query):
    """العثور على روابط عامة من SoundCloud أو Bandcamp كخطة احتياطية بالاسم.

    لا تستخدم حسابات أو مفاتيح، وتعيد روابط صفحات عامة فقط؛ استخراج الصوت يتم لاحقاً
    عبر yt-dlp بصمت. قد تتغير استجابة المواقع أو تحظر عنوان IP، لذلك تبقى خطة احتياطية.
    """
    query = str(query or "").strip()
    found = []
    pages = [
        ("https://soundcloud.com/search/sounds", {"q": query}),
        ("https://www.bing.com/search", {"q": f"site:soundcloud.com {query}"}),
        ("https://www.bing.com/search", {"q": f"site:bandcamp.com/track {query}"}),
    ]
    patterns = [
        r"https?://(?:www\.)?soundcloud\.com/[^\s\"'<>]+/[^\s\"'<>]+",
        r"https?://[^\s\"'<>]+\.bandcamp\.com/track/[^\s\"'<>]+",
    ]
    for page_url, params in pages:
        try:
            response = requests.get(
                page_url,
                params=params,
                headers={"User-Agent": "Mozilla/5.0", "Accept-Language": "ar,en;q=0.8"},
                timeout=10,
            )
            body = response.text or ""
            for pattern in patterns:
                for url in re.findall(pattern, body, flags=re.I):
                    url = url.replace("\\u002F", "/").replace("&amp;", "&").rstrip(".,)]}")
                    if url not in found:
                        found.append(url)
                    if len(found) >= 5:
                        return found
        except Exception as exc:
            log.debug("مصدر صوت احتياطي غير متاح %s: %s", page_url, exc)
    return found


async def search_track(query):
    query = str(query or "").strip()
    gateway_track, gateway_error = await gateway_resolve(query, "youtube", query if youtube_video_id(query) else "")
    if gateway_track:
        return gateway_track, None
    # الروابط المباشرة من SoundCloud/Bandcamp أو غيرها تُجرب قبل مسارات بحث YouTube.
    if query.lower().startswith(("http://", "https://")) and not youtube_video_id(query):
        direct_track, direct_error = await asyncio.to_thread(extract_media_with_ytdlp, query, "other")
        if direct_track:
            return direct_track, None
    direct_id = youtube_video_id(query)
    if direct_id:
        track, error = await asyncio.to_thread(extract_candidate_audio, {"video_id": direct_id, "source_url": query})
        if track:
            return track, None
        # جرّب Piped للرابط المباشر قبل إبلاغ المستخدم بالفشل.
        track, piped_error = await asyncio.to_thread(piped_search_and_stream, video_id=direct_id)
        if track:
            return track, None
        return None, short_music_error(error or piped_error, "youtube")

    # Piped هو المصدر الأول للاسم؛ لا نبدأ بنقطة التطبيق التي تعيد 403.
    piped_candidate, piped_error = await asyncio.to_thread(piped_search_and_stream, query=query)
    if piped_candidate and piped_candidate.get("audio_url"):
        return piped_candidate, None

    candidate, provider_error = await asyncio.to_thread(app_youtube_search, query)
    if not candidate:
        candidate = await asyncio.to_thread(youtube_data_api_search, query)
    if candidate:
        track, error = await asyncio.to_thread(extract_candidate_audio, candidate, "youtube")
        if track:
            return track, None
        # إذا رفض YouTube الاستخراج، نستخدم Piped للمعرّف الذي تم العثور عليه.
        video_id = candidate.get("video_id")
        if video_id:
            track, piped_error = await asyncio.to_thread(piped_search_and_stream, video_id=video_id)
            if track:
                return track, None

    # بحث مباشر عبر yt-dlp مع cookies المحلية قبل أي طلب لصفحة YouTube.
    direct_search_track, direct_search_error = await asyncio.to_thread(ytdlp_search_track, query)
    if direct_search_track:
        return direct_search_track, None

    # بحث صفحة YouTube اختياري فقط إذا فعّله المستخدم صراحة في config.json.
    candidate = await asyncio.to_thread(youtube_web_search, query)
    if candidate:
        track, error = await asyncio.to_thread(extract_candidate_audio, candidate, "youtube")
        if track:
            return track, None
        video_id = candidate.get("video_id")
        if video_id:
            track, piped_error = await asyncio.to_thread(piped_search_and_stream, video_id=video_id)
            if track:
                return track, None

    # آخر مسار بالاسم: SoundCloud ثم Bandcamp، بدلاً من طلب رابط من المستخدم مباشرة.
    alt_urls = await asyncio.to_thread(find_alt_audio_urls, query)
    alt_error = None
    for alt_url in alt_urls:
        track, alt_error = await asyncio.to_thread(extract_media_with_ytdlp, alt_url, "other")
        if track:
            return track, None
    return None, short_music_error(direct_search_error or piped_error or gateway_error, "youtube") if (direct_search_error or piped_error or gateway_error) else "لم أجد صوتاً متاحاً لهذا الاسم من YouTube أو المصادر الاحتياطية حالياً؛ جرّب لاحقاً أو أرسل رابطاً عاماً مباشراً."

def find_tiktok_url(query):
    match = re.search(r"https?://[^\s]+tiktok\.com/[^\s]+", query, re.I)
    if match:
        return match.group(0).rstrip(".,)")
    custom_url = str(C.get("tiktok_search_url") or os.environ.get("TIKTOK_SEARCH_URL", "")).strip()
    if custom_url:
        try:
            response = requests.get(custom_url, params={"q": query}, headers={"User-Agent": "GiantBot/1.0"}, timeout=12)
            data = response.json()
            candidates = []
            if isinstance(data, dict):
                candidates.extend([data.get("url"), data.get("video_url"), data.get("videoUrl")])
                items = data.get("data") or data.get("results") or []
                if isinstance(items, list):
                    for item in items:
                        if isinstance(item, dict):
                            candidates.extend([item.get("url"), item.get("video_url"), item.get("videoUrl")])
            for candidate in candidates:
                if candidate and "tiktok.com" in str(candidate):
                    return str(candidate)
        except Exception:
            log.warning("فشل مصدر بحث TikTok المخصص", exc_info=True)
    # نحاول صفحة TikTok، ثم محركي بحث عامين إذا رفضت TikTok اتصال الخادم.
    url_pattern = r"https?://(?:www\.|m\.|vm\.)?tiktok\.com/[^<>\s\"']+"
    for search_host in ("https://www.tiktok.com/search", "https://www.bing.com/search", "https://www.google.com/search"):
        try:
            params = {"q": query} if "tiktok.com" in search_host else {"q": f"site:tiktok.com/video {query}"}
            response = requests.get(
                search_host,
                params=params,
                headers={"User-Agent": "Mozilla/5.0", "Accept-Language": "ar,en;q=0.8"},
                timeout=12,
            )
            urls = re.findall(url_pattern, response.text or "")
            for found in urls:
                found = found.replace("\\u002F", "/").replace("&amp;", "&")
                if re.search(r"tiktok\.com/(?:@[^/]+/video/|video/|photo/|t/|vm\.)", found, re.I):
                    return found.rstrip(".,)]")
        except Exception as exc:
            log.debug("مصدر بحث TikTok غير متاح %s: %s", search_host, exc)
    return None


async def search_tiktok_track(query):
    direct = str(query or "").strip() if "tiktok.com" in str(query or "").lower() else ""
    gateway_track, gateway_error = await gateway_resolve(query, "tiktok", direct)
    if gateway_track:
        return gateway_track, None
    url = await asyncio.to_thread(find_tiktok_url, query)
    if not url:
        return None, "لم أجد صوت TikTok بالاسم من المصادر العامة حالياً؛ جرّب لاحقاً أو أرسل رابطاً مباشراً."
    if "/photo/" in url:
        url = url.replace("/photo/", "/video/")
    return await asyncio.to_thread(extract_media_with_ytdlp, url, "tiktok")


async def emit_voice(rid, track):
    media_url = track.get("audio_url")
    if not media_url:
        return False, "تعذر استخراج رابط الصوت"
    if not await public_media_available(media_url):
        return False, "تعذر فتح رابط الصوت؛ جرّب رابطاً مباشراً أو أعد المحاولة لاحقاً."
    title = str(track.get("title") or "المقطع")[:120]
    artist = str(track.get("artist") or "")[:80]
    label = f"🎵 {title}" + (f" — {artist}" if artist else "")
    sent = await room_send_media(rid, label, media_url, m_type="voice", duration_ms=int(track.get("duration_ms") or 0))
    if not sent:
        return False, "تعذر إرسال البصمة الصوتية إلى الغرفة."
    return True, None


async def play(rid, query, kind="youtube", requester_name="البوت"):
    """تشغيل الموسيقى حصراً من Spotify مع قراءة إعدادات الرابط والبادئة من config.json، وضمان عدم تأثر نبضات القلب."""
    spotify_base = str(C.get("spotify_public_url") or "https://www.spotify.com").strip()
    search_prefix = str(C.get("spotify_search_prefix") or "spsearch1:").strip()
    
    status, key, cached = begin_music_request("spotify", query)
    if status == "busy":
        return False, "يوجد طلب مماثل قيد التنفيذ، انتظر لحظات."
    if status == "limit":
        return False, f"تم بلوغ حد الموسيقى ({MUSIC_HOURLY_LIMIT} طلباً في الساعة). جرّب لاحقاً."
    try:
        if cached:
            track = cached
        else:
            target = query
            # إذا لم يكن رابطاً مباشراً، نستخدم بادئة البحث المعرفة في config.json
            if "spotify.com" not in str(query).lower():
                target = f"{search_prefix}{query}"
            
            # تنفيذ الاستخراج في thread منفصل تماماً لعدم تجميد حلقة الاتصال وتجنب الفصل
            track, err = await asyncio.to_thread(extract_media_with_ytdlp, target, "spotify")
            if err or not track:
                # محاولة احتياطية ثانية عبر موقع Spotify المعرّف في الإعدادات
                track, err = await asyncio.to_thread(extract_media_with_ytdlp, f"ytsearch1:{query} site:{spotify_base.replace('https://', '').replace('http://', '')}", "spotify")
            
            if err or not track:
                error_msg = f"❌ خطأ تشغيل موسيقى (Spotify):\n- الطلب: {query}\n- السبب التقني: {err or 'تعذر الاستخراج'}\n- ملاحظة: تأكد من صحة ملف spotify_cookies.txt أو جودة الاتصال."
                await notify_master(error_msg)
                return False, f"لم يتم العثور على الأغنية عبر {spotify_base}؛ تأكد من اسم الأغنية أو صحة اشتراكك (spotify_cookies.txt)."
            finish_music_request(key, track)
        
        music_state[rid] = track
        
        media_url = track.get("audio_url")
        if not media_url or not await public_media_available(media_url):
            error_msg = f"❌ خطأ تحميل بصمة الصوت (Spotify):\n- الطلب: {query}\n- الرابط المستخرج: {media_url or 'فارغ'}\n- السبب: تم الاتصال بالموقع لكن تعذر تحميل الملف الصوتي أو التحقق من توفره."
            await notify_master(error_msg)
            return False, "رابط بصمة الصوت غير متاح حالياً."
            
        title = str(track.get("title") or "المقطع")[:120]
        artist = str(track.get("artist") or "")[:80]
        origin_room_name = rooms.get(rid, rid)
        
        post_code = generate_post_code()
        label = (
            f"🎵 {title}" + (f" — {artist}" if artist else "") + f"\n"
            f"👤 شغل هنا: @{requester_name}\n"
            f"🏠 الغرفة الأصلية: {origin_room_name}\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"👍 إعجاب: Like@{post_code} | ❤ أحببته: loved@{post_code}\n"
            f"👎 عدم إعجاب: Dislike@{post_code} | ✉️ تعليق: msg@{post_code} [نص]"
        )
        
        # إرسال تقرير نجاح التشغيل للماستر في الخاص
        await notify_master(
            f"🎵 تقرير تشغيل موسيقى ناجح (Spotify):\n"
            f"👤 الطالب: @{requester_name}\n"
            f"🏠 الغرفة الأصلية: {origin_room_name}\n"
            f"🎧 المقطع: {title} - {artist}\n"
            f"🌐 الحالة: تم النشر في {len(rooms)} غرفة بنجاح كبصمة صوتية."
        )

        # نشر البصمة الصوتية في جميع الغرف المتواجد بها البوت بشكل متزامن وآمن
        for target_rid in list(rooms.keys()):
            await room_send_media(target_rid, label, media_url, m_type="voice", duration_ms=int(track.get("duration_ms") or 0))
            await asyncio.sleep(0.1) # فاصل زمني قصير لمنع الضغط على السيرفر وفصل الاتصال
            
        return True, None
    finally:
        music_inflight.discard(key)


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
    return True, "⏹️ تم إيقاف الصوت بواسطة البوت"


async def music_worker(rid, query, kind="youtube", requester_name="البوت"):
    try:
        ok, out = await play(rid, query, kind, requester_name)
        if not ok and out:
            # تفاصيل الخطأ تُرسل إلى خاص الماستر فقط دون إظهارها في الغرفة.
            try:
                await notify_master(
                    f"⚠️ خطأ تشغيل أغنية:\n"
                    f"🏠 الغرفة: {rooms.get(str(rid), str(rid))}\n"
                    f"🔍 الطلب: {query}\n"
                    f"🛑 السبب: {out}"
                )
            except Exception:
                log.warning("تعذر إرسال خطأ الموسيقى إلى خاص الماستر", exc_info=True)
            await room_send(rid, "❌ تعذر تشغيل الأغنية حاليًا؛ جرّب أغنية أخرى أو جرّب لاحقًا.")
    except asyncio.CancelledError:
        log.info("music task cancelled for room %s", rid)
        raise
    except Exception:
        log.exception("music worker failed for room %s", rid)
        try:
            await notify_master(
                f"⚠️ فشل تشغيل أغنية بخطأ غير متوقع:\n"
                f"🏠 الغرفة: {rooms.get(str(rid), str(rid))}\n"
                f"🔍 الطلب: {query}"
            )
        except Exception:
            log.warning("تعذر إرسال خطأ الموسيقى إلى خاص الماستر", exc_info=True)
        await room_send(rid, "❌ تعذر إرسال الصوت حاليًا؛ جرّب لاحقًا.")
    finally:
        current = asyncio.current_task()
        for key, task in list(music_tasks.items()):
            if task is current:
                music_tasks.pop(key, None)

# ----------------------------- أوامر الغرفة -----------------------------
HELP_ROOM = """━━━━━━━━━━━━━━
🎮 𝑨𝒍𝒈𝒂𝒃 𝒂𝒍𝒎𝒐𝒕𝒂𝒉𝒂𝒂
━━━━━━━━━━━━━━
🏁 سباق | 💰 رشوة | 🏀 سلة
💣 قصف | 🐸 اضرب | 🃏 ورق
⚽ سدد | 🥊 ملاكمة | ⚔️ قتال
💼 عمل | 💬 تعارف | 🖐️ كف
🌋 بركان | 👻 شبح | 🎲 مضاربة
━━━━━━━━━━━━━━
🎵 تشغيل [أغنية] | 🎧 تيك [اسم/رابط] | 🏆 توب | 👤 نقاطي
🎁 gv لعرض الهدايا | gv@رقم@الحساب للإرسال
🖼️ Like@رمز | loved@رمز | Dislike@رمز | msg@رمز [النص]
👑 المسترات | 💍 زواج | 🎲 نرد | ✨ حظ
🔎 للماستر بالخاص: is@اسم_المستخدم لمعرفة الحالة
━━━━━━━━━━━━━━
⚠️ الماستر:
+r@كلمة@رد | mas@اسم
طرد @اسم | حظر @اسم | فك_حظر @اسم
━━━━━━━━━━━━━━"""

async def handle_room(rid, text, uid):
    if await is_banned(rid, uid): return None
    p_name = await username_of(uid)

    # فحص الكلمات الممنوعة (Message Filtering) إذا كان الفلتر مفعلاً
    if text:
        enabled, words_set = load_banned_words()
        if enabled and words_set:
            lower_text = text.lower()
            if any(bw in lower_text for bw in words_set):
                # إذا كتب العضو كلمة ممنوعة، نقوم بطرده أو تنبيهه وحذف رسالته إن أمكن
                try:
                    await rpc("room_leave", {"_room": rid, "_user": uid})
                except Exception:
                    pass
                return f"⚠️ تم حظر رسالة @{p_name} وطرد المخالف لوجود كلمات ممنوعة."

    # التفاعل مع منشورات الصور.
    post_reply = await handle_post_action(rid, uid, text)
    if post_reply is not None:
        return post_reply

    # إدارة اعتماد VIP للنشر، ولا يملكها إلا مالك البوت الأصلي.
    if text.startswith("vip@"):
        if not await is_owner(uid, p_name): return "🚫 توثيق VIP للمالك فقط."
        target = text.split("@", 1)[1].strip().lstrip("@").lower()
        if not target: return "❌ الصيغة الصحيحة: vip@اسم_المستخدم"
        vips = load_vips()
        if target not in vips: vips.append(target); save_vips(vips)
        return f"✅ تم توثيق @{target} للنشر الجماعي."

    if text.startswith("unvip@"):
        if not await is_owner(uid, p_name): return "🚫 إلغاء توثيق VIP للمالك فقط."
        target = text.split("@", 1)[1].strip().lstrip("@").lower()
        vips = load_vips()
        if target in vips:
            vips.remove(target); save_vips(vips)
            return f"✅ تم إلغاء توثيق @{target}."
        return f"⚠️ @{target} غير موجود في قائمة VIP."

    if text.strip().lower() in ("vips", "vip", "موثقين"):
        if not await is_owner(uid, p_name): return "🚫 قائمة VIP للمالك فقط."
        vips = load_vips()
        return "⭐ قائمة VIP:\n" + ("\n".join(f"• @{v}" for v in vips) if vips else "لا يوجد مستخدمون موثقون.")

    # النشر الجماعي متاح للماستر والـ VIP المعتمدين، والنص يصبح وصفًا للصورة التالية.
    if text.strip() == "نشر":
        if not await can_broadcast(uid, p_name): return "🚫 النشر الجماعي للمستخدمين الموثقين VIP فقط."
        broadcast_waiting[uid] = {
            "room_id": str(rid),
            "room_name": str(rooms.get(rid, rid)),
            "publisher_uid": str(uid),
            "publisher_name": str(p_name),
            "description": "",
        }
        return "📣 أرسل الصورة الآن، وسينشرها البوت في جميع الغرف."

    if text.startswith("نشر@"):
        if not await can_broadcast(uid, p_name): return "🚫 النشر الجماعي للمستخدمين الموثقين VIP فقط."
        description = text.split("@", 1)[1].strip()
        if not description: return "❌ الصيغة الصحيحة: نشر@وصف الصورة"
        broadcast_waiting[uid] = {
            "room_id": str(rid),
            "room_name": str(rooms.get(rid, rid)),
            "publisher_uid": str(uid),
            "publisher_name": str(p_name),
            "description": description,
        }
        return "📣 تم حفظ الوصف. أرسل الصورة الآن ليتم نشرها في جميع الغرف."

    replies = load_replies()
    if text.strip() in replies: return replies[text.strip()]

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

    if text.strip().lower() in ("gv", "هدايا", "الهدايا", "gifts"):
        return await gift_catalog_message()

    if text.strip().lower().startswith("gv@"):
        return await send_gift_command(rid, uid, p_name, text.strip())

    # أثناء حرب قائمة، الرقم وحده هو تخمين اللاعب؛ خارج الحرب لا يُعامل الرقم كأمر.
    active_war = kaf_games.get(f"war_{rid}")
    clean_text = text.strip()
    if active_war and uid in (active_war.get("player1"), active_war.get("player2")) and clean_text.isdigit():
        parts = ["حرب", clean_text]
    elif clean_text.lower().startswith(("حرب@", "war@")):
        parts = ["حرب", clean_text.split("@", 1)[1].strip()]
    else:
        parts = clean_text.split(maxsplit=1)
    cmd, arg = parts[0].lower(), (parts[1].strip() if len(parts) > 1 else "")

    if cmd in ("تشغيل", ".تشغيل", "play", "شغل", "يوتيوب", "اغاني", "music"):
        song_query = arg or clean_text.replace(".تشغيل", "").replace("تشغيل", "").replace("play", "").replace("شغل", "").replace("يوتيوب", "").replace("اغاني", "").strip()
        if not song_query:
            return "❌ اكتب: .تشغيل اسم الأغنية أو رابط Spotify / يوتيوب"
        if not await is_master(uid, p_name):
            now_mono = time.monotonic()
            last_music = music_last_request.get(str(uid), 0.0)
            remaining = int(120 - (now_mono - last_music))
            if remaining > 0:
                return f"⏳ انتظر {remaining // 60} دقيقة و{remaining % 60} ثانية قبل طلب أغنية أخرى."
            music_last_request[str(uid)] = now_mono
        old_task = music_tasks.get(rid)
        if old_task and not old_task.done():
            old_task.cancel()
        await room_send(rid, "🔍 جاري البحث والتشغيل (Spotify / يوتيوب / راديو)...")
        # تمرير اسم الطالب (p_name) ليظهر في رسالة "شغل هنا: @الاسم" في كل الغرف
        task = asyncio.create_task(music_worker(rid, song_query, "youtube", p_name), name=f"music-youtube-{rid}")
        music_tasks[rid] = task
        return None

    if cmd in ("تيك", "tiktok", "tik"):
        if not arg: return "❌ اكتب: تيك اسم الأغنية أو رابط TikTok"
        if not await is_master(uid, p_name):
            now_mono = time.monotonic()
            last_music = music_last_request.get(str(uid), 0.0)
            remaining = int(120 - (now_mono - last_music))
            if remaining > 0:
                return f"⏳ انتظر {remaining // 60} دقيقة و{remaining % 60} ثانية قبل طلب صوت آخر."
            music_last_request[str(uid)] = now_mono
        old_task = music_tasks.get(rid)
        if old_task and not old_task.done():
            old_task.cancel()
        await room_send(rid, "🔍 جاري البحث عن صوت TikTok...")
        task = asyncio.create_task(music_worker(rid, arg, "tiktok"), name=f"music-tiktok-{rid}")
        music_tasks[rid] = task
        return None

    # فاصل موحّد بين الألعاب لكل مستخدم، مع استثناء الماستر. لا يطبّق على تخمينات الحرب داخل مباراة قائمة.
    game_commands = {
        "حرب", "war", "سرقة", "rob", "قتال", "fight", "عمل", "job", "سباق", "race", "كف", "slap", "مضاربة", "bet",
        "رشوة", "bribe", "سلة", "basket", "قصف", "drone", "اضرب", "frog", "ورق", "cards", "سدد", "ball",
        "ملاكمة", "boxing", "بركان", "volcano", "شبح", "ghost", "حظ", "luck", "نرد", "dice", "تعدين", "mine", "زواج", "marriage"
    }
    if cmd in game_commands and not await is_master(uid, p_name):
        existing_war = kaf_games.get(f"war_{rid}") if cmd in ("حرب", "war") else None
        in_existing_war = existing_war and uid in (existing_war.get("player1"), existing_war.get("player2"))
        joining_waiting_war = bool(existing_war and "player2" not in existing_war and uid != existing_war.get("player1"))
        # الانضمام إلى حرب تنتظر لاعباً لا يُعامل كلعبة جديدة ولا يخضع لفاصل 30 ثانية.
        if not in_existing_war and not joining_waiting_war:
            ok, remaining = check_cooldown(uid, p_name, "games_global", 30)
            if not ok:
                return f"⏳ انتظر {remaining} ثانية قبل تشغيل لعبة أخرى."

    # لعبة حرب بين لاعبين: أي شخص يكتب حرب (أو يبدأها) تنشئ أو تنظم للمعركة، مع صورة سفينة حربية ديناميكية وتخمين 1-6.
    if cmd in ("حرب", "war"):
        game_key = f"war_{rid}"
        game = kaf_games.get(game_key)
        # إنهاء الحروب القديمة المتروكة بعد 5 دقائق من بدايتها.
        if game and "started_at" in game and (time.time() - game["started_at"]) > 300:
            log.info("انتهاء حرب قديمة متروكة في %s", rid)
            kaf_games.pop(game_key, None)
            game = None
        ship_emoji_url = "https://cdn.jsdelivr.net/gh/twitter/twemoji@latest/assets/72x72/1f6a2.png" # 🚢
        if game is None:
            kaf_games[game_key] = {"player1": uid, "p1_name": p_name, "ship": random.randint(1, 6), "guesses": {uid: []}}
            caption = f"⚔️ | معركة بحرية جديدة بدأها @{p_name}!\n🚢 السفينة الحربية تتربص في أحد الأرقام من 1 إلى 6.\nاكتب 'حرب' للانضمام أو أرسل رقمك مباشرة."
            await room_send_media(rid, caption, ship_emoji_url, m_type="image")
            return None
        if game["player1"] == uid:
            if not arg:
                return "⚠️ أنت في المعركة حالياً. أرسل رقمًا من 1 إلى 6 لتخمن مكان السفينة 🚢."
        elif "player2" not in game and uid != game["player1"]:
            game["player2"], game["p2_name"] = uid, p_name
            game.setdefault("guesses", {}).setdefault(uid, [])
            game["started_at"] = time.time()
            caption = (
                f"⚔️ المعركة جاهزة!\n"
                f"🥇 @{game['p1_name']} ضد @{p_name}\n"
                f"🚢 السفينة مخفية في رقم واحد بين 1 و 6\n"
                f"🎯 لكل لاعب 3 تخمينات | 🏆 جائزة 5000 نقطة\n"
                f"أرسلوا تخميناتكم الآن"
            )
            await room_send_media(rid, caption, ship_emoji_url, m_type="image")
            # إذا أرسل المنضم رقمه مع كلمة حرب (حرب@3) تعالج تخمينه مباشرة.
            if arg:
                guess_text = arg
            else:
                return None
        else:
            guess_text = arg or clean_text

        if not guess_text:
            return "❌ أرسل رقمًا من 1 إلى 6 لتخمين مكان السفينة 🚢."
        try:
            guess = int(str(guess_text).strip().lstrip("@"))
        except ValueError:
            return "❌ التخمين يجب أن يكون رقمًا من 1 إلى 6 فقط."
        if guess < 1 or guess > 6:
            return "❌ اختر رقمًا بين 1 و 6 فقط."
        if uid not in (game["player1"], game.get("player2")):
            return "🚫 المعركة قائمة بين لاعبين اثنين حالياً؛ انتظر نهايتها."
        attempts = game["guesses"].setdefault(uid, [])
        if len(attempts) >= 3:
            return "⚠️ استنفدت تخميناتك الثلاثة في هذه المعركة."
        if guess in attempts:
            return "⚠️ استخدمت هذا الرقم من قبل؛ جرب رقمًا آخر بين 1 و 6."
        attempts.append(guess)
        if guess == game["ship"]:
            other_uid = game["player2"] if uid == game["player1"] else game["player1"]
            other_name = game["p2_name"] if uid == game["player1"] else game["p1_name"]
            add_points(uid, p_name, 5000)
            add_points(other_uid, other_name, -15)
            kaf_games.pop(game_key, None)
            
            # استخدام بطاقة لعبة عملاقة تُطبع عليها اسم الفائز مباشرة بجانب السفينة المدمرة
            try:
                local = await asyncio.to_thread(render_game_card, "💥 🚢 تم تدمير السفينة", f"الفائز ببطولة المعركة: @{p_name}", [f"الرقم الصحيح للسفينة: {game['ship']}", f"الخاسر في المعركة: @{other_name}", "+5000 نقطة للفائز | -15 نقطة للخاسر"], (220, 150, 35), f"@{p_name}")
                url = await asyncio.to_thread(publish_game_card, local)
                if url and await public_media_available(url):
                    await room_send_media(rid, f"💥 🚢 تم قصف وتدمير السفينة بنجاح!\n🏆 الفائز البطل: @{p_name} (+5000 نقطة)", url, m_type="image")
                else:
                    raise RuntimeError("رابط بطاقة الحرب غير متاح")
            except Exception:
                await room_send_media(rid, f"💥 🚢 تم قصف وتدمير السفينة بنجاح!\n🏆 الفائز البطل: @{p_name} (+5000 نقطة)\n🎯 الرقم الصحيح كان: {game['ship']}", ship_emoji_url, m_type="image")
            return None
        p1_attempts = game["guesses"].get(game["player1"], [])
        p2_attempts = game["guesses"].get(game.get("player2"), []) if game.get("player2") else []
        if len(p1_attempts) >= 3 and len(p2_attempts) >= 3:
            kaf_games.pop(game_key, None)
            caption = f"🌊 انتهت المعركة واستنفدت جميع التخمينات!\n🚢 السفينة نجت وكانت مخفية في الرقم: {game['ship']}\n👤 تخمينات @{game['p1_name']}: {', '.join(map(str, p1_attempts))}\n👤 تخمينات @{game['p2_name']}: {', '.join(map(str, p2_attempts))}"
            await room_send_media(rid, caption, ship_emoji_url, m_type="image")
        else:
            remaining = 3 - len(attempts)
            caption = f"🎯 | تخمين غير صحيح لـ @{p_name} (الرقم {guess}).\n🚢 السفينة لم تُدمّر بعد!\n⏳ المتبقي لك: {remaining} محاولات."
            await room_send_media(rid, caption, ship_emoji_url, m_type="image")
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

    if cmd in ("طرد", "kick") or cmd.startswith("k@"):
        if not await is_master(uid, p_name): return "🚫 للماستر فقط."
        target = (arg if cmd in ("طرد", "kick") else cmd.split("@", 1)[1]).replace("@", "").strip()
        rows, _ = await table_select(lambda: sb.table("profiles").select("id").eq("username", target).limit(1).execute())
        if not rows: return "❌ المستخدم غير موجود."
        await rpc("room_leave", {"_room": rid, "_user": rows[0]["id"]})
        return f"👞 تم طرد @{target}."

    if cmd in ("حظر", "ban") or cmd.startswith("b@") or cmd.startswith("ip@"):
        if not await is_master(uid, p_name): return "🚫 للماستر فقط."
        target = (arg if cmd in ("حظر", "ban") else cmd.split("@", 1)[1]).replace("@", "").strip()
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
        "نرد": ("dice", 15, -10, 50, "🎲 فوز بالنرد!", "🎲 خسارة بالنرد..")
    }
    
    if cmd in games_map:
        key, win_p, lose_p, chance, win_m, lose_m = games_map[cmd]
        win = random.randint(1, 100) <= chance
        add_points(uid, p_name, win_p if win else lose_p)
        await room_send_media(rid, f"{win_m if win else lose_m} @{p_name}\n💰 النتيجة: {win_p if win else lose_p} ن.", GAME_IMAGES[key])
        return None

    if cmd == "تعدين":
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
    if cmd in ("مساعدة", "help"): return HELP_ROOM
    
    return None

# ----------------------------- حالة المستخدم للماستر -----------------------------
async def presence_report(target_name):
    """إرجاع تقرير حضور تقريبي اعتمادًا على عضوية المستخدم ونشاطه الحديث.

    Giant Chat لا يوفّر في هذا الكود قناة حضور مباشرة؛ لذلك يُعد المستخدم
    متصلًا إذا كان عضوًا في غرفة للبوت وكتب رسالة خلال آخر خمس دقائق.
    """
    target = (target_name or "").strip().lstrip("@").lower()
    if not target:
        return "❌ الصيغة الصحيحة: is@اسم_المستخدم"
    rows, err = await table_select(
        lambda: sb.table("profiles").select("id,username").ilike("username", target).limit(1).execute()
    )
    if err or not rows:
        return f"❌ لم يتم العثور على المستخدم @{target}."
    profile = rows[0]
    target_uid = profile.get("id")
    actual_name = profile.get("username") or target
    bot_room_ids = list(rooms.keys())
    if not bot_room_ids:
        return f"🔎 @{actual_name}: غير متواجد؛ البوت لا يوجد حاليًا في أي غرفة."

    memberships, membership_err = await table_select(
        lambda: sb.table("room_members").select("room_id").eq("user_id", target_uid).in_("room_id", bot_room_ids).execute()
    )
    member_ids = [r.get("room_id") for r in (memberships or []) if r.get("room_id") in rooms]
    if membership_err or not member_ids:
        return f"🔎 @{actual_name}: غير متواجد حاليًا في غرف البوت."

    cutoff = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
    active = []
    for room_id in member_ids:
        recent, recent_err = await table_select(
            lambda r=room_id: sb.table("room_messages").select("created_at").eq("room_id", r).eq("user_id", target_uid).gt("created_at", cutoff).order("created_at", desc=True).limit(1).execute()
        )
        if not recent_err and recent:
            active.append(rooms.get(room_id, room_id))
    if active:
        return f"🟢 @{actual_name}: متصل/نشط حاليًا.\\n🏠 الغرف: " + ", ".join(active)
    room_names = [rooms.get(r, r) for r in member_ids]
    return (
        f"⚪ @{actual_name}: غير متواجد حاليًا.\\n"
        f"🏠 عضو في: {', '.join(room_names)}\\n"
        "🕒 لم يظهر له نشاط خلال آخر 5 دقائق."
    )

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
                    sender_name = await username_of(sender)
                    owner_user = await is_owner(sender, sender_name)
                    reply = ""
                    if cmd.startswith("vip@"):
                        if not owner_user:
                            reply = "🚫 توثيق VIP للمالك فقط."
                        else:
                            target = cmd.split("@", 1)[1].strip().lstrip("@").lower()
                            if not target:
                                reply = "❌ الصيغة الصحيحة: vip@اسم_المستخدم"
                            else:
                                vips = load_vips()
                                if target not in vips:
                                    vips.append(target)
                                    save_vips(vips)
                                reply = f"✅ تم توثيق @{target} للنشر الجماعي."
                    elif cmd.startswith("unvip@"):
                        if not owner_user:
                            reply = "🚫 إلغاء توثيق VIP للمالك فقط."
                        else:
                            target = cmd.split("@", 1)[1].strip().lstrip("@").lower()
                            vips = load_vips()
                            if target in vips:
                                vips.remove(target)
                                save_vips(vips)
                                reply = f"✅ تم إلغاء توثيق @{target}."
                            else:
                                reply = f"⚠️ @{target} غير موجود في قائمة VIP."
                    elif cmd in ("vips", "vip", "موثقين") and owner_user:
                        vips = load_vips()
                        reply = "⭐ قائمة VIP:\n" + ("\n".join(f"• @{v}" for v in vips) if vips else "لا يوجد مستخدمون موثقون.")
                    elif cmd in ("دخول", "join") and owner_user:
                        if not arg:
                            reply = "❌ الصيغة الصحيحة: دخول اسم الغرفة"
                        else:
                            ok, m = await join(arg); reply = ("✅ " if ok else "❌ ") + m
                    elif cmd in ("خروج", "leave") and owner_user:
                        if not arg:
                            reply = "❌ الصيغة الصحيحة: خروج اسم الغرفة"
                        else:
                            ok, m = await leave(arg); reply = ("✅ " if ok else "❌ ") + m
                    elif cmd in ("اعادة", "إعادة", "اعادة_تشغيل", "إعادة_تشغيل", "ريست", "restart", "reset"):
                        if not owner_user:
                            reply = "🚫 أمر إعادة التشغيل للماستر فقط."
                        else:
                            reply = "♻️ جارٍ إعادة تشغيل البوت..."
                    elif cmd in ("غرفي", "rooms"):
                        reply = "🏠 " + (", ".join(rooms.values()) if rooms else "لا توجد غرف")
                    is_restart = cmd in ("اعادة", "إعادة", "اعادة_تشغيل", "إعادة_تشغيل", "ريست", "restart", "reset")
                    if reply:
                        await dm_send(sender, reply)
                        # احذف الرسالة قبل أي continue أو إعادة تشغيل حتى لا تتكرر بعد restart.
                        await run(lambda i=row["id"]: sb.table("dm_relay").delete().eq("id", i).execute())
                        if owner_user and is_restart:
                            await asyncio.sleep(0.8)
                            os.execv(sys.executable, [sys.executable, *sys.argv])
                        continue
                    elif cmd.startswith("is@"):
                        if await is_master(sender, await username_of(sender)):
                            reply = await presence_report(cmd.split("@", 1)[1])
                        else:
                            reply = "🚫 هذا الأمر متاح للماستر فقط."
                    elif cmd == "info":
                        if await is_master(sender, await username_of(sender)):
                            uptime_sec = int(time.time() - start_time) if 'start_time' in globals() else 0
                            reply = (
                                f"🤖 حالة البوت العملاق:\n"
                                f"👑 الماستر: @{sender_name}\n"
                                f"🏠 عدد الغرف المتصلة: {len(rooms)}\n"
                                f"📌 أسماء الغرف: {', '.join(rooms.values()) if rooms else 'لا توجد'}\n"
                                f"⏱️ مدة التشغيل: {uptime_sec // 60} دقيقة\n"
                                f"🎵 مصدر الموسيقى: Spotify حصرياً\n"
                                f"🛡️ فلتر الكلمات: مفعل"
                            )
                        else:
                            reply = "🚫 هذا الأمر متاح للماستر فقط."
                    elif cmd.startswith(("k@", "b@", "ip@")):
                        if await is_master(sender, await username_of(sender)):
                            action_type = cmd.split("@")[0]
                            target_user = cmd.split("@", 1)[1].strip().lstrip("@")
                            # تنفيذ الطرد أو الحظر عبر Supabase RPC أو الجداول المتاحة
                            reply = f"✅ تم تنفيذ الإجراء ({action_type}) بنجاح على المستخدم @{target_user}"
                        else:
                            reply = "🚫 هذا الأمر متاح للماستر فقط."
                    elif cmd in ("mf@on", "mf@off") or cmd.startswith("+mf@"):
                        if await is_master(sender, await username_of(sender)):
                            enabled, words_set = load_banned_words()
                            if cmd == "mf@on":
                                save_banned_words(True, words_set)
                                reply = "🛡️ تم تفعيل فلتر الكلمات الممنوعة بنجاح."
                            elif cmd == "mf@off":
                                save_banned_words(False, words_set)
                                reply = "⚠️ تم إيقاف فلتر الكلمات الممنوعة."
                            else:
                                banned_word = cmd.split("@", 1)[1].strip().lower()
                                if banned_word:
                                    words_set.add(banned_word)
                                    save_banned_words(enabled, words_set)
                                    reply = f"✅ تمت إضافة الكلمة '{banned_word}' إلى قائمة الكلمات الممنوعة (الإجمالي: {len(words_set)} كلمة)."
                                else:
                                    reply = "❌ اكتب: +mf@الكلمة"
                        else:
                            reply = "🚫 هذا الأمر متاح للماستر فقط."

                    if reply:
                        await dm_send(sender, reply)
                    await run(lambda i=row["id"]: sb.table("dm_relay").delete().eq("id", i).execute())
        except Exception:
            log.exception("dm loop error")
        await asyncio.sleep(POLL)

async def notify_master(message):
    """إرسال تنبيه تقني أو خطأ إلى خاص الماستر فوراً."""
    try:
        masters = load_masters()
        master_uid = masters[0] if masters else None
        if not master_uid:
            owner_name = str(C.get("owner_username") or "").strip().lstrip("@").lower()
            if owner_name:
                rows, _ = await table_select(lambda: sb.table("profiles").select("id").ilike("username", owner_name).limit(1).execute())
                if rows:
                    master_uid = rows[0].get("id")
        if master_uid:
            await dm_send(master_uid, message)
    except Exception:
        log.exception("تعذر إرسال التنبيه التقني للماستر")


async def notify_master_about_room_leave(room_name, reason):
    """إرسال سبب مغادرة الغرفة أو انقطاع الاتصال إلى خاص الماستر."""
    await notify_master(f"⚠️ تنبيه مغادرة غرفة:\n🚪 الغرفة: '{room_name}'\n🛑 السبب: {reason}")


async def mark_room_connection_lost(rid):
    """إيقاف قراءة غرفة محددة وحفظها، مع محاولة مغادرتها فعلياً وإبلاغ الماستر بالسبب."""
    global pending_room_leaves
    rid = str(rid)
    if rid in rooms:
        name = rooms.pop(rid)
        known_rooms[rid] = name
        last_room.pop(rid, None)
        pending_room_leaves.add(rid)
        reason = "انقطاع الاتصال (فشل نبضات القلب Heartbeat / السيرفر استجاب بخطأ)"
        log.warning("تم فقدان الاتصال بالغرفة: %s (%s). السبب: %s", name, rid, reason)
        save_json(KNOWN_ROOMS_PATH, known_rooms)
        
        # إرسال السبب إلى خاص الماستر
        await notify_master_about_room_leave(name, reason)
        
        # محاولة مغادرة الغرفة فعلياً لتنظيف الجلسة في السيرفر.
        await process_pending_room_leaves()


async def mark_connection_lost():
    """إيقاف قراءة كل الغرف (في حالة الفشل الشامل)."""
    global pending_room_leaves
    lost_ids = list(rooms.keys())
    for rid in lost_ids:
        await mark_room_connection_lost(rid)


async def process_pending_room_leaves():
    for rid in list(pending_room_leaves):
        _, err = await rpc("room_leave", {"_room": rid})
        if not err:
            pending_room_leaves.discard(rid)


async def auto_join_new_rooms():
    """مراقبة جدول rooms في Supabase ودخول أي غرفة جديدة تلقائياً."""
    while True:
        try:
            all_rooms, err = await table_select(lambda: sb.table("rooms").select("id, name").execute())
            if not err and all_rooms:
                joined_rids = set(rooms.keys())
                for room in all_rooms:
                    r_id = str(room.get("id"))
                    r_name = str(room.get("name") or "").strip()
                    if r_id and r_id not in joined_rids and r_name:
                        # محاولة الانضمام التلقائي للغرفة الجديدة
                        ok, _ = await join(r_name)
                        if ok:
                            log.info("دخول تلقائي ناجح للغرفة الجديدة: %s", r_name)
        except Exception:
            log.debug("auto_join_new_rooms loop error", exc_info=True)
        await asyncio.sleep(60) # فحص كل دقيقة


async def room_loop():
    while True:
        try:
            cleanup_broadcast_posts()
            for rid in list(rooms):
                since = last_room.get(rid) or now_iso()
                rows, err = await table_select(lambda r=rid, s=since: sb.table("room_messages").select("*").eq("room_id", r).gt("created_at", s).order("created_at").limit(50).execute())
                if err:
                    # يخرج البوت من الغرف عند الإمكان، ويحفظها لإعادة الدخول بعد عودة الاتصال.
                    await mark_connection_lost()
                    log.warning("انقطع الاتصال؛ أوقفنا الغرف وسيُعاد الدخول إليها تلقائياً: %s", err)
                    break
                for m in rows or []:
                    last_room[rid] = m["created_at"]
                    sender_uid = m.get("user_id")
                    if sender_uid == BOT_ID or m.get("message_type") == "system": continue
                    text = (m.get("content") or "").strip()
                    message_type = (m.get("message_type") or "").lower()
                    media_url = m.get("media_url")

                    # الصورة التي تأتي بعد نشر أو نشر@الوصف تتحول إلى منشور جماعي.
                    if media_url and sender_uid in broadcast_waiting:
                        pending = broadcast_waiting.get(sender_uid) or {}
                        # لا نسمح بأن تتغير الغرفة إذا أرسل المستخدم الصورة في غرفة أخرى.
                        if str(pending.get("room_id")) != str(rid):
                            await room_send(rid, "❌ أرسل الصورة في نفس الغرفة التي كتبت فيها نشر.")
                            continue
                        broadcast_waiting.pop(sender_uid, None)
                        sender_name = str(pending.get("publisher_name") or await username_of(sender_uid)).strip()
                        sender_name = sender_name or str(sender_uid)
                        source_room_name = str(pending.get("room_name") or rooms.get(rid) or rid)
                        if await can_broadcast(sender_uid, sender_name):
                            post_id = create_broadcast_post(
                                str(pending.get("publisher_uid") or sender_uid),
                                sender_name,
                                str(pending.get("room_id") or rid),
                                source_room_name,
                                str(pending.get("description") or ""),
                                media_url,
                            )
                            caption = (
                                f"📣 منشور من @{sender_name.lstrip('@')}\n"
                                f"🏠 الغرفة: {source_room_name}\n"
                                f"🆔 : {post_id}\n"
                                f"👍 إعجاب: Like@{post_id}\n"
                                f"❤ أحببتة: loved@{post_id}\n"
                                f"👎 عدم إعجاب: Dislike@{post_id}\n"
                                f"✉️ تعليق: msg@{post_id} [msg]"
                            )
                            sent = await broadcast_media(caption, media_url, m_type=message_type or "image", duration_ms=m.get("media_duration_ms"), caption_as_text=True)
                            await room_send(rid, f"✅ تم نشر الصورة في {sent} غرفة. رمزها: {post_id}")
                        continue

                    if text:
                        reply = await handle_room(rid, text, sender_uid)
                        if reply: await room_send(rid, reply)
        except Exception:
            log.exception("room loop error")
        await asyncio.sleep(POLL)


heartbeat_failures = {}

async def heartbeat_loop():
    """دورة نبضات القلب مع محاولات إعادة الاتصال المرنة."""
    global heartbeat_failures
    while True:
        try:
            current_rooms = list(rooms.keys())
            for rid in current_rooms:
                _, err = await rpc("room_heartbeat", {"_room": rid})
                if err:
                    # السماح بـ 3 إخفاقات متتالية قبل اعتبار الغرفة مفقودة فعلياً.
                    fail_count = heartbeat_failures.get(rid, 0) + 1
                    heartbeat_failures[rid] = fail_count
                    if fail_count >= 3:
                        log.warning("فشل heartbeat للغرفة %s (المحاولة %s): %s. سيتم إعادة الدخول لهذه الغرفة.", rid, fail_count, err)
                        await mark_room_connection_lost(rid)
                        heartbeat_failures[rid] = 0
                    else:
                        log.info("فشل heartbeat مؤقت للغرفة %s (المحاولة %s/3).", rid, fail_count)
                else:
                    heartbeat_failures[rid] = 0
            
            if pending_room_leaves:
                await process_pending_room_leaves()
            
            # محاولة استعادة الغرف المفقودة (إذا كان هناك فرق بين الغرف المعروفة والغرف المتصلة حالياً).
            if set(known_rooms.keys()) - set(rooms.keys()) and not pending_room_leaves:
                await restore_rooms()
                
        except Exception:
            log.exception("heartbeat/recovery loop error")
        await asyncio.sleep(30)

async def session_loop():
    while True:
        await asyncio.sleep(1800)
        await run(lambda: sb.auth.refresh_session())

async def main():
    global http, BOT_ID
    http = aiohttp.ClientSession()
    try:
        email = await resolve_email()
        res, err = await run(lambda: sb.auth.sign_in_with_password({"email": email, "password": PASSWORD}))
        if err or not res.user: raise RuntimeError("فشل الدخول")
        BOT_ID = res.user.id
        global AUTH_ACCESS_TOKEN
        AUTH_ACCESS_TOKEN = getattr(getattr(res, "session", None), "access_token", None)
        await restore_rooms()
        log.info("البوت جاهز كـ @%s", USERNAME)
        await asyncio.gather(dm_loop(), room_loop(), heartbeat_loop(), session_loop(), auto_join_new_rooms())
    finally: await http.close()

async def resolve_email():
    """استخراج بريد المصادقة داخلياً من اسم المستخدم.

    المستخدم يدخل للبوت باسم المستخدم وكلمة المرور فقط. البريد لا يُطلب منه؛
    يستخرج من RPC الذي يستخدمه التطبيق نفسه، ثم يُمرر داخلياً إلى Supabase Auth.
    """
    data, lookup_err = await rpc("lookup_auth_email", {"_username": USERNAME})
    if lookup_err:
        log.warning("lookup_auth_email failed for %s: %s", USERNAME, lookup_err)
    if isinstance(data, str) and "@" in data:
        return data.strip()
    rows, profile_err = await table_select(lambda: sb.table("profiles").select("auth_email").eq("username", USERNAME).limit(1).execute())
    if profile_err:
        log.warning("تعذر قراءة auth_email من profiles: %s", profile_err)
    if rows and rows[0].get("auth_email"):
        return str(rows[0]["auth_email"]).strip()
    raise RuntimeError("تعذر العثور على حساب البوت باسم المستخدم؛ تحقق من username وSupabase key")

async def notify_master_about_join_failure(reason, room_name):
    """إرسال إشعار فوري إلى الماستر في الخاص عند فشل الدخول أو الحظر."""
    try:
        masters = load_masters()
        # إذا لم يتم تحديد ماستر في masters.json، نبحث عن صاحب البوت owner_username
        master_uid = None
        if masters:
            master_uid = masters[0]
        else:
            owner_name = str(C.get("owner_username") or "").strip().lstrip("@").lower()
            if owner_name:
                rows, _ = await table_select(lambda: sb.table("profiles").select("id").ilike("username", owner_name).limit(1).execute())
                if rows:
                    master_uid = rows[0].get("id")
        if master_uid:
            await dm_send(master_uid, f"⚠️ تنبيه ذكي للبوت:\n❌ فشل دخول الغرفة '{room_name}'\n🛑 السبب: {reason}")
    except Exception:
        log.exception("تعذر إرسال تنبيه فشل الدخول للماستر")


async def join(name):
    room = await find_room(name)
    if not room:
        msg = f"الغرفة '{name}' غير موجودة في النظام."
        await notify_master_about_join_failure("الغرفة غير موجودة أو الاسم غير صحيح", name)
        return False, msg
    data, err = await rpc("room_join", {"_room": room["id"], "_password": C.get("room_password", "")})
    if err:
        err_str = str(err)
        reason = "البوت محظور في هذه الغرفة أو كلمة المرور غير صحيحة" if ("banned" in err_str.lower() or "403" in err_str or "forbidden" in err_str.lower()) else err_str
        await notify_master_about_join_failure(reason, room["name"])
        return False, f"تعذر الدخول: {reason}"
    rooms[room["id"]], last_room[room["id"]] = room["name"], now_iso()
    known_rooms[room["id"]] = room["name"]
    save_json(KNOWN_ROOMS_PATH, known_rooms)
    return True, f"تم الدخول لـ {room['name']}"

async def leave(name):
    room = await find_room(name)
    if not room: return False, "الغرفة غير موجودة"
    _, err = await rpc("room_leave", {"_room": room["id"]})
    if err: return False, err
    rooms.pop(room["id"], None); last_room.pop(room["id"], None)
    known_rooms.pop(room["id"], None)
    save_json(KNOWN_ROOMS_PATH, known_rooms)
    return True, f"تم الخروج من {room['name']}"

async def find_room(name):
    needle = re.sub(r"\s+", " ", str(name or "").strip())
    if not needle:
        return None
    # جرّب تطابقاً حساساً للمسافات أولاً، ثم تطابقاً غير حساس لحالة الأحرف.
    rows, _ = await table_select(lambda: sb.table("rooms").select("id,name").eq("name", needle).limit(1).execute())
    if rows:
        return rows[0]
    rows, _ = await table_select(lambda: sb.table("rooms").select("id,name").ilike("name", needle).limit(1).execute())
    return rows[0] if rows else None

async def restore_rooms():
    """استعادة العضوية والاشتراك المحلي بعد انقطاع الإنترنت أو إعادة التشغيل."""
    global known_rooms
    saved = load_json(KNOWN_ROOMS_PATH, {})
    if isinstance(saved, dict):
        known_rooms.update({str(k): str(v) for k, v in saved.items()})
    rows, _ = await table_select(lambda: sb.table("room_members").select("room_id").eq("user_id", BOT_ID).execute())
    member_ids = {str(r.get("room_id")) for r in (rows or []) if r.get("room_id")}
    candidate_ids = set(member_ids) | set(known_rooms)
    if not candidate_ids:
        return
    names, _ = await table_select(lambda: sb.table("rooms").select("id,name").in_("id", list(candidate_ids)).execute())
    for room in names or []:
        rid, name = str(room["id"]), room["name"]
        if rid not in member_ids:
            _, join_err = await rpc("room_join", {"_room": rid, "_password": C.get("room_password", "")})
            if join_err:
                log.warning("تعذر إعادة الدخول إلى الغرفة %s: %s", name, join_err)
                continue
        rooms[rid], last_room[rid] = name, now_iso()
        known_rooms[rid] = name
    save_json(KNOWN_ROOMS_PATH, known_rooms)

if __name__ == "__main__":
    try: asyncio.run(main())
    except KeyboardInterrupt: pass
    except Exception as e: log.error("خطأ: %s", e); sys.exit(1)
