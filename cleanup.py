#!/usr/bin/env python3
"""
cleanup.py — ملف تنظيف مخلفات بوت Giant Chat (alsfer_bot)
يُنفَّذ كل 10 ساعات (مثلاً عبر cron) لتنظيف:
1) المنشورات المنتهية (أكثر من 10 ساعات) من published_posts.json
2) صور الهدايا المولدة القديمة (أكثر من 30 دقيقة)
3) الملفات المؤقتة

طريقة التشغيل اليدوي:
    cd /home/.../bot_folder && python3 cleanup.py

طريقة cron (كل 10 ساعات):
    0 */10 * * * cd /home/Ahmd444/ready_bot && /usr/bin/python3 cleanup.py >> logs/cleanup.log 2>&1
"""

import json
import os
import sys
import tempfile
import time
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent

PUBLISHED_POSTS_PATH = BASE_DIR / "published_posts.json"
GIFT_RENDER_DIR = BASE_DIR / "generated_gifts"
PUBLISH_LOCAL_DIR = BASE_DIR / "published_media"

POST_TTL_SECONDS = 10 * 3600           # حذف المنشورات بعد 10 ساعات
GIFT_IMAGE_MAX_AGE_SECONDS = 30 * 60   # حذف صور الهدايا بعد 30 دقيقة
PUBLISH_MAX_AGE_SECONDS = 3600         # حذف ملفات المنشور المؤقتة بعد ساعة


def load_json(path, default=None):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return default if default is not None else {}


def save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def parse_post_time(created_at):
    try:
        s = str(created_at or "").replace("Z", "+00:00")
        from datetime import datetime
        return datetime.fromisoformat(s).timestamp()
    except Exception:
        return 0.0


def prune_expired_posts(posts):
    now = time.time()
    removed = 0
    for pid in list(posts.keys()):
        if (now - parse_post_time(posts[pid].get("created_at"))) > POST_TTL_SECONDS:
            posts.pop(pid, None)
            removed += 1
    return removed


def cleanup_leftovers():
    now = time.time()
    freed = 0
    targets = [
        (GIFT_RENDER_DIR, "gift_*.png", GIFT_IMAGE_MAX_AGE_SECONDS),
        (PUBLISH_LOCAL_DIR, "*.*", PUBLISH_MAX_AGE_SECONDS),
    ]
    for folder, pattern, max_age in targets:
        if folder.exists():
            for file_path in folder.glob(pattern):
                try:
                    if now - file_path.stat().st_mtime > max_age:
                        freed += file_path.stat().st_size
                        file_path.unlink()
                except OSError:
                    pass
    try:
        for file_path in Path(tempfile.gettempdir()).glob("alsfer_*.*"):
            try:
                if now - file_path.stat().st_mtime > PUBLISH_MAX_AGE_SECONDS:
                    freed += file_path.stat().st_size
                    file_path.unlink()
            except OSError:
                pass
    except Exception:
        pass
    return freed


def main():
    os.chdir(BASE_DIR)
    removed = 0
    if PUBLISHED_POSTS_PATH.exists():
        posts = load_json(PUBLISHED_POSTS_PATH, {})
        if isinstance(posts, dict):
            removed = prune_expired_posts(posts)
            save_json(PUBLISHED_POSTS_PATH, posts)
    freed = cleanup_leftovers()
    print(f"[cleanup] removed {removed} expired posts | freed {freed} bytes of leftovers")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"[cleanup] error: {e}", file=sys.stderr)
        sys.exit(1)
