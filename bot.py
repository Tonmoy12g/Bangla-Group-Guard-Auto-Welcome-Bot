"""
Bangla Group Guard & Auto Welcome Telegram Bot (v1.0 - 10/10 Pro Edition)
All-in-One Group Security, Anti-Link, Anti-Spam, Captcha Verification & Custom Welcome Bot
Supports Telegram Bot API 9.4 Full Colored Buttons (style: primary / success / danger)

Render Environment Variables (শুধুমাত্র এই ২টি Render-এ দেবেন):
- BOT_TOKEN : Your Telegram Bot Token from @BotFather
- ADMIN_ID  : Your Telegram Numeric User ID (e.g. 123456789)

(Force Channel এবং Support Username সরাসরি টেলিগ্রামের Admin Panel থেকে সেট ও পরিবর্তন করা যাবে)
"""

import html
import json
import logging
import os
import re
import sqlite3
import threading
import time
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any, Dict, List, Optional, Tuple

import requests
from requests.adapters import HTTPAdapter

# ==================== LOGGING ====================
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger("BanglaGroupGuard")

# ==================== RENDER ENVIRONMENT CONFIGURATION ====================
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
RAW_ADMIN_IDS = os.getenv("ADMIN_ID", os.getenv("ADMIN_IDS", "")).strip()
ADMIN_IDS = set()
for _part in RAW_ADMIN_IDS.split(","):
    _part = _part.strip()
    if _part.isdigit():
        ADMIN_IDS.add(int(_part))

PORT = int(os.getenv("PORT", "10000"))

DEFAULT_BOT_NAME = "Bangla Group Guard"
BASE_API_URL = f"https://api.telegram.org/bot{BOT_TOKEN}"
DB_FILE = "group_guard.db"
BD_TZ = timezone(timedelta(hours=6))

db_lock = threading.Lock()

# Pooled HTTP Session for fast response
http_session = requests.Session()
_adapter = HTTPAdapter(pool_connections=40, pool_maxsize=40, max_retries=1)
http_session.mount("https://", _adapter)
http_session.mount("http://", _adapter)

# ==================== BOTTOM KEYBOARD LABELS (NO EMOJI COLOR DOTS) ====================
BTN_ADD_GROUP = "➕ গ্রুপে যুক্ত করুন"
BTN_MY_GROUPS = "🛡️ আমার গ্রুপসমূহ"
BTN_COMMANDS = "📖 কমান্ড ও গাইড"
BTN_FILTER_HELP = "⚙️ সিকিউরিটি ফিচার"
BTN_LIVE_STATS = "📊 বট স্ট্যাটাস"
BTN_SUPPORT = "📞 সাপোর্ট ও চ্যানেল"
BTN_ADMIN_PANEL = "👑 অ্যাডমিন প্যানেল"

DEFAULT_BAD_WORDS = [
    "scam",
    "casino",
    "betting",
    "1xbet",
    "melbet",
    "জুয়া",
    "ক্যাসিনো",
]


# ==================== SQLITE3 DATABASE ====================
def get_db() -> sqlite3.Connection:
    return sqlite3.connect(DB_FILE, timeout=25, check_same_thread=False)


def init_db() -> None:
    with db_lock:
        conn = get_db()
        c = conn.cursor()
        c.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                first_name TEXT,
                is_banned INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_active TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        c.execute(
            """
            CREATE TABLE IF NOT EXISTS groups (
                chat_id INTEGER PRIMARY KEY,
                title TEXT,
                welcome_on INTEGER DEFAULT 1,
                antilink_on INTEGER DEFAULT 1,
                antiforward_on INTEGER DEFAULT 0,
                antibot_on INTEGER DEFAULT 1,
                badword_on INTEGER DEFAULT 1,
                captcha_on INTEGER DEFAULT 0,
                warn_limit INTEGER DEFAULT 3,
                welcome_text TEXT DEFAULT '',
                rules_text TEXT DEFAULT '',
                bad_words TEXT DEFAULT '',
                added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        c.execute(
            """
            CREATE TABLE IF NOT EXISTS warnings (
                chat_id INTEGER,
                user_id INTEGER,
                warn_count INTEGER DEFAULT 0,
                PRIMARY KEY (chat_id, user_id)
            )
            """
        )
        c.execute(
            """
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT
            )
            """
        )
        c.execute(
            """
            CREATE TABLE IF NOT EXISTS stats (
                key TEXT PRIMARY KEY,
                count INTEGER DEFAULT 0
            )
            """
        )

        c.execute(
            "INSERT OR IGNORE INTO settings (key, value) VALUES ('bot_name', ?)",
            (DEFAULT_BOT_NAME,),
        )
        c.execute(
            "INSERT OR IGNORE INTO settings (key, value) VALUES ('bot_username', '')"
        )
        c.execute(
            "INSERT OR IGNORE INTO settings (key, value) VALUES ('force_sub', 'off')"
        )
        c.execute(
            "INSERT OR IGNORE INTO settings (key, value) VALUES ('channel_username', '')"
        )
        c.execute(
            "INSERT OR IGNORE INTO settings (key, value) VALUES ('support_username', '')"
        )
        c.execute(
            "INSERT OR IGNORE INTO settings (key, value) VALUES ('maintenance', 'off')"
        )

        c.execute(
            "INSERT OR IGNORE INTO stats (key, count) VALUES ('welcomes_sent', 0)"
        )
        c.execute(
            "INSERT OR IGNORE INTO stats (key, count) VALUES ('spam_blocked', 0)"
        )
        c.execute(
            "INSERT OR IGNORE INTO stats (key, count) VALUES ('actions_taken', 0)"
        )
        conn.commit()
        conn.close()


def get_setting(key: str, default: str = "") -> str:
    try:
        conn = get_db()
        c = conn.cursor()
        c.execute("SELECT value FROM settings WHERE key = ?", (key,))
        row = c.fetchone()
        conn.close()
        if row and row[0] is not None:
            return row[0]
        return default
    except Exception:
        return default


def set_setting(key: str, value: str) -> None:
    try:
        with db_lock:
            conn = get_db()
            c = conn.cursor()
            c.execute(
                "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
                (key, value),
            )
            conn.commit()
            conn.close()
    except Exception as e:
        logger.error(f"Error setting {key}: {e}")


def increment_stat(key: str, amount: int = 1) -> None:
    try:
        with db_lock:
            conn = get_db()
            c = conn.cursor()
            c.execute(
                "UPDATE stats SET count = count + ? WHERE key = ?", (amount, key)
            )
            conn.commit()
            conn.close()
    except Exception as e:
        logger.error(f"Error incrementing stat {key}: {e}")


def get_stats() -> Dict[str, int]:
    res = {
        "users": 0,
        "groups": 0,
        "welcomes": 0,
        "spam_blocked": 0,
        "actions": 0,
        "banned": 0,
    }
    try:
        conn = get_db()
        c = conn.cursor()
        c.execute("SELECT COUNT(*) FROM users")
        res["users"] = c.fetchone()[0]
        c.execute("SELECT COUNT(*) FROM groups")
        res["groups"] = c.fetchone()[0]
        c.execute("SELECT COUNT(*) FROM users WHERE is_banned = 1")
        res["banned"] = c.fetchone()[0]
        c.execute("SELECT count FROM stats WHERE key = 'welcomes_sent'")
        r1 = c.fetchone()
        res["welcomes"] = r1[0] if r1 else 0
        c.execute("SELECT count FROM stats WHERE key = 'spam_blocked'")
        r2 = c.fetchone()
        res["spam_blocked"] = r2[0] if r2 else 0
        c.execute("SELECT count FROM stats WHERE key = 'actions_taken'")
        r3 = c.fetchone()
        res["actions"] = r3[0] if r3 else 0
        conn.close()
    except Exception as e:
        logger.error(f"Error getting stats: {e}")
    return res


def register_or_update_user(user_id: int, username: str, first_name: str) -> None:
    try:
        with db_lock:
            conn = get_db()
            c = conn.cursor()
            c.execute("SELECT user_id FROM users WHERE user_id = ?", (user_id,))
            if c.fetchone():
                c.execute(
                    """
                    UPDATE users
                    SET username = ?, first_name = ?, last_active = CURRENT_TIMESTAMP
                    WHERE user_id = ?
                    """,
                    (username, first_name, user_id),
                )
            else:
                c.execute(
                    """
                    INSERT INTO users (user_id, username, first_name)
                    VALUES (?, ?, ?)
                    """,
                    (user_id, username, first_name),
                )
            conn.commit()
            conn.close()
    except Exception as e:
        logger.error(f"Error updating user {user_id}: {e}")


def is_user_banned(user_id: int) -> bool:
    try:
        conn = get_db()
        c = conn.cursor()
        c.execute("SELECT is_banned FROM users WHERE user_id = ?", (user_id,))
        row = c.fetchone()
        conn.close()
        return bool(row and row[0])
    except Exception:
        return False


def set_user_ban_status(user_id: int, ban: bool) -> None:
    try:
        with db_lock:
            conn = get_db()
            c = conn.cursor()
            c.execute(
                "INSERT OR IGNORE INTO users (user_id, username, first_name) VALUES (?, '', 'User')",
                (user_id,),
            )
            c.execute(
                "UPDATE users SET is_banned = ? WHERE user_id = ?",
                (1 if ban else 0, user_id),
            )
            conn.commit()
            conn.close()
    except Exception as e:
        logger.error(f"Error setting ban status: {e}")


def register_or_get_group(chat_id: int, title: str = "Group") -> Dict[str, Any]:
    try:
        with db_lock:
            conn = get_db()
            c = conn.cursor()
            c.execute(
                """
                SELECT chat_id, title, welcome_on, antilink_on, antiforward_on,
                       antibot_on, badword_on, captcha_on, warn_limit,
                       welcome_text, rules_text, bad_words
                FROM groups WHERE chat_id = ?
                """,
                (chat_id,),
            )
            row = c.fetchone()
            if not row:
                default_bw = ",".join(DEFAULT_BAD_WORDS)
                c.execute(
                    """
                    INSERT INTO groups (chat_id, title, bad_words)
                    VALUES (?, ?, ?)
                    """,
                    (chat_id, title, default_bw),
                )
                conn.commit()
                c.execute(
                    """
                    SELECT chat_id, title, welcome_on, antilink_on, antiforward_on,
                           antibot_on, badword_on, captcha_on, warn_limit,
                           welcome_text, rules_text, bad_words
                    FROM groups WHERE chat_id = ?
                    """,
                    (chat_id,),
                )
                row = c.fetchone()
            elif title and title != row[1]:
                c.execute(
                    "UPDATE groups SET title = ? WHERE chat_id = ?", (title, chat_id)
                )
                conn.commit()
            conn.close()

        return {
            "chat_id": row[0],
            "title": row[1] or title,
            "welcome_on": bool(row[2]),
            "antilink_on": bool(row[3]),
            "antiforward_on": bool(row[4]),
            "antibot_on": bool(row[5]),
            "badword_on": bool(row[6]),
            "captcha_on": bool(row[7]),
            "warn_limit": int(row[8] or 3),
            "welcome_text": row[9] or "",
            "rules_text": row[10] or "",
            "bad_words": [w.strip().lower() for w in (row[11] or "").split(",") if w.strip()],
        }
    except Exception as e:
        logger.error(f"Group DB error: {e}")
        return {
            "chat_id": chat_id,
            "title": title,
            "welcome_on": True,
            "antilink_on": True,
            "antiforward_on": False,
            "antibot_on": True,
            "badword_on": True,
            "captcha_on": False,
            "warn_limit": 3,
            "welcome_text": "",
            "rules_text": "",
            "bad_words": list(DEFAULT_BAD_WORDS),
        }


def toggle_group_field(chat_id: int, field: str) -> None:
    allowed = {
        "welcome_on",
        "antilink_on",
        "antiforward_on",
        "antibot_on",
        "badword_on",
        "captcha_on",
    }
    if field not in allowed:
        return
    try:
        with db_lock:
            conn = get_db()
            c = conn.cursor()
            c.execute(
                f"UPDATE groups SET {field} = CASE WHEN {field} = 1 THEN 0 ELSE 1 END WHERE chat_id = ?",
                (chat_id,),
            )
            conn.commit()
            conn.close()
    except Exception as e:
        logger.error(f"Toggle group field error: {e}")


def update_group_text(chat_id: int, field: str, value: str) -> None:
    if field not in ("welcome_text", "rules_text", "bad_words"):
        return
    try:
        with db_lock:
            conn = get_db()
            c = conn.cursor()
            c.execute(
                f"UPDATE groups SET {field} = ? WHERE chat_id = ?",
                (value, chat_id),
            )
            conn.commit()
            conn.close()
    except Exception as e:
        logger.error(f"Update group text error: {e}")


def add_user_warning(chat_id: int, user_id: int) -> int:
    try:
        with db_lock:
            conn = get_db()
            c = conn.cursor()
            c.execute(
                "SELECT warn_count FROM warnings WHERE chat_id = ? AND user_id = ?",
                (chat_id, user_id),
            )
            row = c.fetchone()
            new_count = (row[0] + 1) if row else 1
            c.execute(
                "INSERT OR REPLACE INTO warnings (chat_id, user_id, warn_count) VALUES (?, ?, ?)",
                (chat_id, user_id, new_count),
            )
            conn.commit()
            conn.close()
            return new_count
    except Exception:
        return 1


def reset_user_warnings(chat_id: int, user_id: int) -> None:
    try:
        with db_lock:
            conn = get_db()
            c = conn.cursor()
            c.execute(
                "DELETE FROM warnings WHERE chat_id = ? AND user_id = ?",
                (chat_id, user_id),
            )
            conn.commit()
            conn.close()
    except Exception:
        pass


# ==================== HELPERS & TELEGRAM API ====================
def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS


def safe_html(text: Any) -> str:
    if text is None:
        return ""
    return html.escape(str(text))


def current_bd_time_str() -> str:
    return datetime.now(BD_TZ).strftime("%I:%M %p • %d %b %Y")


def tg_request(
    method: str, data: Optional[Dict[str, Any]] = None, timeout: int = 25
) -> Dict[str, Any]:
    url = f"{BASE_API_URL}/{method}"
    try:
        resp = http_session.post(url, json=data or {}, timeout=timeout)
        return resp.json()
    except Exception as e:
        logger.error(f"Telegram API ({method}) error: {e}")
        return {"ok": False, "error": str(e)}


def sync_bot_info_from_telegram() -> None:
    try:
        res = tg_request("getMe", timeout=10)
        if res.get("ok"):
            result = res.get("result") or {}
            tg_name = (result.get("first_name") or "").strip()
            tg_uname = (result.get("username") or "").strip()
            if tg_name:
                set_setting("bot_name", tg_name)
            if tg_uname:
                set_setting("bot_username", tg_uname)
    except Exception:
        pass


def send_message(
    chat_id: int,
    text: str,
    reply_markup: Optional[Dict[str, Any]] = None,
    parse_mode: str = "HTML",
    reply_to_message_id: Optional[int] = None,
) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": parse_mode,
        "disable_web_page_preview": True,
    }
    if reply_markup:
        payload["reply_markup"] = reply_markup
    if reply_to_message_id:
        payload["reply_to_message_id"] = reply_to_message_id
    return tg_request("sendMessage", payload)


def edit_message_text(
    chat_id: int,
    message_id: int,
    text: str,
    reply_markup: Optional[Dict[str, Any]] = None,
    parse_mode: str = "HTML",
) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "chat_id": chat_id,
        "message_id": message_id,
        "text": text,
        "parse_mode": parse_mode,
        "disable_web_page_preview": True,
    }
    if reply_markup:
        payload["reply_markup"] = reply_markup
    return tg_request("editMessageText", payload)


def delete_message(chat_id: int, message_id: int) -> None:
    tg_request("deleteMessage", {"chat_id": chat_id, "message_id": message_id})


def answer_callback_query(
    callback_query_id: str, text: Optional[str] = None, show_alert: bool = False
) -> None:
    payload: Dict[str, Any] = {"callback_query_id": callback_query_id}
    if text:
        payload["text"] = text
        payload["show_alert"] = show_alert
    tg_request("answerCallbackQuery", payload)


def is_group_admin(chat_id: int, user_id: int) -> bool:
    if is_admin(user_id):
        return True
    try:
        res = tg_request("getChatMember", {"chat_id": chat_id, "user_id": user_id})
        if res.get("ok"):
            status = (res.get("result") or {}).get("status", "")
            return status in ("creator", "administrator")
    except Exception:
        pass
    return False


def restrict_member(chat_id: int, user_id: int, can_send: bool) -> bool:
    perms = {
        "can_send_messages": can_send,
        "can_send_audios": can_send,
        "can_send_documents": can_send,
        "can_send_photos": can_send,
        "can_send_videos": can_send,
        "can_send_video_notes": can_send,
        "can_send_voice_notes": can_send,
        "can_send_polls": can_send,
        "can_send_other_messages": can_send,
        "can_add_web_page_previews": can_send,
    }
    res = tg_request(
        "restrictChatMember",
        {"chat_id": chat_id, "user_id": user_id, "permissions": perms},
    )
    return bool(res.get("ok"))


def ban_member(chat_id: int, user_id: int) -> bool:
    res = tg_request("banChatMember", {"chat_id": chat_id, "user_id": user_id})
    return bool(res.get("ok"))


def unban_member(chat_id: int, user_id: int) -> bool:
    res = tg_request(
        "unbanChatMember",
        {"chat_id": chat_id, "user_id": user_id, "only_if_banned": True},
    )
    return bool(res.get("ok"))


def kick_member(chat_id: int, user_id: int) -> bool:
    if ban_member(chat_id, user_id):
        tg_request("unbanChatMember", {"chat_id": chat_id, "user_id": user_id})
        return True
    return False


# ==================== FULL COLORED KEYBOARDS (Bot API 9.4 `style` Property) ====================
def bottom_colorful_keyboard(user_id: int = 0) -> Dict[str, Any]:
    kb = [
        [
            {"text": BTN_ADD_GROUP, "style": "success"},
            {"text": BTN_MY_GROUPS, "style": "primary"},
        ],
        [
            {"text": BTN_COMMANDS, "style": "primary"},
            {"text": BTN_FILTER_HELP, "style": "success"},
        ],
        [
            {"text": BTN_LIVE_STATS, "style": "primary"},
            {"text": BTN_SUPPORT, "style": "success"},
        ],
    ]
    if is_admin(user_id):
        kb.append([{"text": BTN_ADMIN_PANEL, "style": "danger"}])

    return {
        "keyboard": kb,
        "resize_keyboard": True,
        "is_persistent": True,
        "one_time_keyboard": False,
        "input_field_placeholder": "নিচের কালারফুল বাটন থেকে অপশন বেছে নিন...",
    }


def add_to_group_inline_keyboard() -> Dict[str, Any]:
    bot_uname = get_setting("bot_username", "").strip()
    url = (
        f"https://t.me/{bot_uname}?startgroup=true"
        if bot_uname
        else "https://t.me/"
    )
    return {
        "inline_keyboard": [
            [
                {
                    "text": "➕ আপনার গ্রুপে বট যুক্ত করুন",
                    "url": url,
                    "style": "success",
                }
            ],
            [
                {
                    "text": "📖 সব কমান্ড দেখুন",
                    "callback_data": "show_commands",
                    "style": "primary",
                },
                {
                    "text": "⚙️ সিকিউরিটি গাইড",
                    "callback_data": "show_security",
                    "style": "success",
                },
            ],
        ]
    }


def group_settings_inline_keyboard(chat_id: int) -> Dict[str, Any]:
    g = register_or_get_group(chat_id)

    def _btn(label: str, flag: bool, field: str) -> Dict[str, Any]:
        st_txt = "ON ✅" if flag else "OFF ❌"
        style = "success" if flag else "danger"
        return {
            "text": f"{label}: {st_txt}",
            "callback_data": f"gtoggle|{chat_id}|{field}",
            "style": style,
        }

    return {
        "inline_keyboard": [
            [
                _btn("👋 Auto Welcome", g["welcome_on"], "welcome_on"),
                _btn("🔗 Anti-Link", g["antilink_on"], "antilink_on"),
            ],
            [
                _btn("📢 Anti-Forward", g["antiforward_on"], "antiforward_on"),
                _btn("🤖 Anti-Bot", g["antibot_on"], "antibot_on"),
            ],
            [
                _btn("🤬 গালি ফিল্টার", g["badword_on"], "badword_on"),
                _btn("🛡️ জয়েন ভেরিফাই", g["captcha_on"], "captcha_on"),
            ],
            [
                {
                    "text": "🔄 রিফ্রেশ প্যানেল",
                    "callback_data": f"grefresh|{chat_id}",
                    "style": "primary",
                },
                {
                    "text": "✖️ প্যানেল বন্ধ করুন",
                    "callback_data": f"gclose|{chat_id}",
                    "style": "danger",
                },
            ],
        ]
    }


def welcome_card_inline_keyboard(
    chat_id: int, new_user_id: int, need_captcha: bool
) -> Dict[str, Any]:
    rows = []
    if need_captcha:
        rows.append(
            [
                {
                    "text": "✅ আমি মানুষ (ক্লিক করে আনমিউট হন)",
                    "callback_data": f"verify|{chat_id}|{new_user_id}",
                    "style": "success",
                }
            ]
        )
    rows.append(
        [
            {
                "text": "📜 গ্রুপের নিয়মাবলী দেখুন",
                "callback_data": f"rules|{chat_id}",
                "style": "primary",
            }
        ]
    )
    return {"inline_keyboard": rows}


def force_sub_keyboard(channel_username: str) -> Dict[str, Any]:
    ch_clean = (
        channel_username.replace("@", "").replace("https://t.me/", "").strip()
    )
    return {
        "inline_keyboard": [
            [
                {
                    "text": "📢 অফিশিয়াল চ্যানেলে জয়েন করুন",
                    "url": f"https://t.me/{ch_clean}",
                    "style": "primary",
                }
            ],
            [
                {
                    "text": "✅ জয়েন করেছি (ভেরিফাই করুন)",
                    "callback_data": "check_sub",
                    "style": "success",
                }
            ],
        ]
    }


def admin_inline_keyboard() -> Dict[str, Any]:
    force_sub_status = get_setting("force_sub", "off").upper()
    maint_status = get_setting("maintenance", "off").upper()

    fs_btn = {
        "text": f"📢 Force Channel: {force_sub_status} (টগল করুন)",
        "callback_data": "admin_toggle_forcesub",
        "style": "success" if force_sub_status == "ON" else "danger",
    }
    maint_btn = {
        "text": f"🛠️ Maintenance Mode: {maint_status}",
        "callback_data": "admin_toggle_maint",
        "style": "danger" if maint_status == "ON" else "primary",
    }

    return {
        "inline_keyboard": [
            [
                {
                    "text": "➕ ফোর্স চ্যানেল সেট করুন (CHANNEL)",
                    "callback_data": "admin_set_channel",
                    "style": "success",
                },
                {
                    "text": "🗑️ চ্যানেল রিমুভ",
                    "callback_data": "admin_remove_channel",
                    "style": "danger",
                },
            ],
            [
                {
                    "text": "📢 চ্যানেল ইউজারনেম সেট করুন",
                    "callback_data": "admin_set_channel_only",
                    "style": "primary",
                }
            ],
            [fs_btn],
            [
                {
                    "text": "👨‍💻 সাপোর্ট ইউজারনেম সেট করুন (SUPPORT)",
                    "callback_data": "admin_set_support",
                    "style": "primary",
                }
            ],
            [
                {
                    "text": "📢 সব ইউজার ও গ্রুপে ব্রডকাস্ট পাঠান",
                    "callback_data": "admin_broadcast",
                    "style": "success",
                }
            ],
            [
                maint_btn,
                {
                    "text": "🚫 ইউজার ব্যান / আনব্যান",
                    "callback_data": "admin_ban_menu",
                    "style": "danger",
                },
            ],
            [
                {
                    "text": "🔄 লাইভ রিপোর্ট রিফ্রেশ",
                    "callback_data": "admin_refresh",
                    "style": "success",
                },
                {
                    "text": "✖️ প্যানেল বন্ধ করুন",
                    "callback_data": "admin_close",
                    "style": "danger",
                },
            ],
        ]
    }


# ==================== FORCE SUBSCRIBE CHECK ====================
def is_user_subscribed(user_id: int) -> bool:
    if is_admin(user_id):
        return True
    if get_setting("force_sub", "off").lower() != "on":
        return True

    ch = get_setting("channel_username", "").strip()
    if not ch:
        return True
    ch = ch.replace("https://t.me/", "").strip()
    if not ch.startswith("@") and not ch.startswith("-100"):
        ch = f"@{ch}"

    try:
        res = tg_request("getChatMember", {"chat_id": ch, "user_id": user_id})
        if res.get("ok"):
            status = (res.get("result") or {}).get("status", "")
            return status in ("creator", "administrator", "member", "restricted")
        return True
    except Exception:
        return True


# ==================== CARD BUILDERS ====================
def build_group_settings_card(chat_id: int) -> str:
    g = register_or_get_group(chat_id)
    return (
        f"╭━━━🛡️ <b>গ্রুপ সিকিউরিটি কন্ট্রোল প্যানেল</b> 🛡️━━━╮\n"
        f"┃ 🏷️ <b>গ্রুপ:</b> {safe_html(g['title'])}\n"
        f"┃ 🆔 <b>চ্যাট আইডি:</b> <code>{chat_id}</code>\n"
        f"┣━━━━━━━━━━━━━━━━━━━━━━━━━━━━┫\n"
        f"┃ 👋 <b>Auto Welcome:</b> {'চালু ✅' if g['welcome_on'] else 'বন্ধ ❌'}\n"
        f"┃ 🔗 <b>Anti-Link Guard:</b> {'চালু ✅' if g['antilink_on'] else 'বন্ধ ❌'}\n"
        f"┃ 📢 <b>Anti-Forward:</b> {'চালু ✅' if g['antiforward_on'] else 'বন্ধ ❌'}\n"
        f"┃ 🤖 <b>Anti-Bot Guard:</b> {'চালু ✅' if g['antibot_on'] else 'বন্ধ ❌'}\n"
        f"┃ 🤬 <b>গালি ফিল্টার:</b> {'চালু ✅' if g['badword_on'] else 'বন্ধ ❌'}\n"
        f"┃ 🛡️ <b>জয়েন ভেরিফিকেশন:</b> {'চালু ✅' if g['captcha_on'] else 'বন্ধ ❌'}\n"
        f"┃ ⚠️ <b>সর্বোচ্চ ওয়ার্নিং লিমিট:</b> <code>{g['warn_limit']}</code> বার\n"
        f"╰━━━━━━━━━━━━━━━━━━━━━━━━━━━━╯\n\n"
        f"👇 <b>নিচের কালারফুল বাটনে ক্লিক করে যেকোনো সিকিউরিটি অন/অফ করুন:</b>"
    )


def build_welcome_card(
    chat_title: str,
    member_name: str,
    member_id: int,
    custom_welcome: str,
    need_captcha: bool,
) -> str:
    custom_line = (
        custom_welcome.strip()
        if custom_welcome.strip()
        else "আমাদের গ্রুপের সকল নিয়ম মেনে চলুন এবং সবার সাথে সৌহার্দ্যপূর্ণ আচরণ করুন।"
    )
    captcha_note = (
        "\n⚠️ <b>নিরাপত্তা নোট:</b> চ্যাট করার জন্য নিচের সবুজ বাটনে ক্লিক করে নিজেকে ভেরিফাই করুন।"
        if need_captcha
        else ""
    )
    return (
        "━━━━━━━ 🛡️ <b>WELCOME TO GROUP</b> 🛡️ ━━━━━━━◆\n"
        f"✨ <b>স্বাগতম, <a href=\"tg://user?id={member_id}\">{safe_html(member_name)}</a>!</b> ✨\n\n"
        "╭─❖ <b>MEMBER INFORMATION</b> ─────────╮\n"
        f"│ 👤 <b>নাম      :</b> {safe_html(member_name)}\n"
        f"│ 🆔 <b>ইউজার আইডি :</b> <code>{member_id}</code>\n"
        f"│ 🏷️ <b>গ্রুপের নাম :</b> {safe_html(chat_title)}\n"
        f"│ 🕒 <b>জয়েন সময়  :</b> {safe_html(current_bd_time_str())}\n"
        "╰────────────────────────────────╯\n\n"
        "╔════════════════════════════════╗\n"
        f"  💬 {safe_html(custom_line)}\n"
        "╚════════════════════════════════╝"
        f"{captcha_note}"
    )


def build_commands_guide_text() -> str:
    return (
        "╭━━━📖 <b>গ্রুপ গার্ড কমান্ড ও গাইড</b> 📖━━━╮\n"
        "┃ বটকে আপনার গ্রুপে যুক্ত করে <b>Admin</b> বানান।\n"
        "╰━━━━━━━━━━━━━━━━━━━━━━━━━━╯\n\n"
        "👮‍♂️ <b>অ্যাডমিন কমান্ড (মেসেজে Reply দিয়ে লিখুন):</b>\n"
        "• <code>/ban</code> বা <code>ব্যান</code> — মেম্বারকে গ্রুপ থেকে ব্যান করবে\n"
        "• <code>/unban</code> বা <code>আনব্যান</code> — মেম্বারকে আনব্যান করবে\n"
        "• <code>/kick</code> বা <code>কিক</code> — মেম্বারকে গ্রুপ থেকে বের করে দেবে\n"
        "• <code>/mute</code> বা <code>মিউট</code> — মেম্বারকে চ্যাট করা থেকে মিউট করবে\n"
        "• <code>/unmute</code> বা <code>আনমিউট</code> — মেম্বারের মিউট খুলে দেবে\n"
        "• <code>/warn</code> বা <code>ওয়ার্ন</code> — মেম্বারকে ওয়ার্নিং দেবে (৩ বারে অটো ব্যান)\n"
        "• <code>/resetwarn</code> — মেম্বারের সব ওয়ার্নিং মুছে দেবে\n"
        "• <code>/pin</code> ও <code>/unpin</code> — মেসেজ পিন ও আনপিন করবে\n\n"
        "⚙️ <b>গ্রুপ সেটিংস ও কাস্টমাইজ কমান্ড:</b>\n"
        "• <code>/settings</code> বা <code>/panel</code> — কালারফুল বাটন সিকিউরিটি প্যানেল\n"
        "• <code>/setwelcome আপনার মেসেজ</code> — কাস্টম ওয়েলকাম সেট করুন\n"
        "• <code>/setrules আপনার নিয়মাবলী</code> — গ্রুপের রুলস সেট করুন\n"
        "• <code>/addword শব্দ</code> — নিষিদ্ধ শব্দের তালিকায় নতুন শব্দ যোগ করুন\n"
        "• <code>/rules</code> — গ্রুপের নিয়মাবলী দেখুন"
    )


def build_security_features_text() -> str:
    return (
        "╭━━━⚙️ <b>অটোমেটিক সিকিউরিটি ফিচারসমূহ</b> ⚙️━━━╮\n"
        "┃ এই বট ২৪ ঘণ্টা আপনার গ্রুপকে স্প্যামমুক্ত রাখবে।\n"
        "╰━━━━━━━━━━━━━━━━━━━━━━━━━━━━╯\n\n"
        "🛡️ <b>১. Anti-Link Guard:</b> সাধারণ মেম্বার কোনো লিংক (`t.me`, `http`, `https`) পাঠালে সাথে সাথে মেসেজ ডিলিট হবে এবং ওয়ার্নিং পাবে।\n\n"
        "📢 <b>২. Anti-Forward Guard:</b> অন্য চ্যানেল বা গ্রুপ থেকে ফরোয়ার্ড করা মেসেজ বা বিজ্ঞাপন অটোমেটিক ডিলিট হয়ে যাবে।\n\n"
        "🤖 <b>৩. Anti-Bot Protection:</b> অ্যাডমিন ছাড়া অন্য কেউ স্প্যাম বট গ্রুপে যুক্ত করলে সাথে সাথে সেই বটকে কিক করে দেবে।\n\n"
        "🤬 <b>৪. Bad-Word / গালি ফিল্টার:</b> অশ্লীল শব্দ বা আপনার ব্লক করা শব্দ লিখলেই মেসেজ ডিলিট হবে।\n\n"
        "✅ <b>৫. Human Verification (Captcha):</b> নতুন মেম্বার জয়েন করলে মিউট থাকবে এবং বাটনে ক্লিক করে ভেরিফাই করলেই চ্যাট করতে পারবে।"
    )


# ==================== PRIVATE CHAT HANDLERS ====================
user_states: Dict[int, str] = {}


def handle_private_start(user_id: int, username: str, first_name: str) -> None:
    register_or_update_user(user_id, username, first_name)
    if is_user_banned(user_id) and not is_admin(user_id):
        send_message(user_id, "🚫 আপনাকে এই বট ব্যবহার থেকে বিরত রাখা হয়েছে।")
        return

    if get_setting("maintenance", "off").lower() == "on" and not is_admin(user_id):
        send_message(
            user_id,
            "🛠️ বটে এখন আপগ্রেডের কাজ চলছে। কিছুক্ষণ পর আবার চেষ্টা করুন।",
        )
        return

    bot_display = safe_html(get_setting("bot_name", DEFAULT_BOT_NAME))

    if not is_user_subscribed(user_id):
        ch = get_setting("channel_username", "")
        msg = (
            f"╭━━━⚠️ <b>চ্যানেল ভেরিফিকেশন আবশ্যক</b> ⚠️━━━╮\n"
            f"┃ প্রিয় <b>{safe_html(first_name)}</b>,\n"
            f"┃ <b>{bot_display}</b> ব্যবহার করতে আমাদের\n"
            f"┃ অফিশিয়াল চ্যানেলে জয়েন থাকুন।\n"
            f"╰━━━━━━━━━━━━━━━━━━━━━━━━╯\n\n"
            f"👇 নিচের বাটনে চাপ দিয়ে জয়েন করে <b>'✅ জয়েন করেছি'</b> বাটনে ট্যাপ করুন:"
        )
        send_message(user_id, msg, reply_markup=force_sub_keyboard(ch))
        return

    stats = get_stats()
    welcome_text = (
        f"╭━━━🛡️ <b>{bot_display}</b> 🛡️━━━╮\n"
        f"┃ 👤 <b>ইউজার:</b> {safe_html(first_name)}\n"
        f"┃ 🆔 <b>আইডি:</b> <code>{user_id}</code>\n"
        f"┃ 📡 <b>স্ট্যাটাস:</b> <b>২৪/৭ অ্যাক্টিভ গার্ড</b>\n"
        f"╰━━━━━━━━━━━━━━━━━━━━━━━━╯\n\n"
        f"┏━━ 🏰 <b>সুরক্ষিত গ্রুপ সংখ্যা: {stats['groups']} টি</b> ━━┓\n"
        f"  🚫 ব্লক করা স্প্যাম ও লিংক: <code>{stats['spam_blocked']}</code> টি\n"
        f"┗━━━━━━━━━━━━━━━━━━━━━━━━┛\n\n"
        f"✨ <b>বটের প্রধান সুবিধাসমূহ:</b>\n"
        f"• <b>প্রিমিয়াম অটো ওয়েলকাম কার্ড ও ক্যাপচা ভেরিফিকেশন</b>\n"
        f"• <b>অটো লিংক ডিলিট, ফরোয়ার্ড ব্লক ও গালি ফিল্টার</b>\n"
        f"• <b>বাংলা ও ইংরেজি কমান্ডে ১ ক্লিকে ব্যান, মিউট, কিক ও ওয়ার্নিং</b>\n\n"
        f"👇 <b>নিচের কালারফুল বাটন থেকে বটটি আপনার গ্রুপে যুক্ত করুন:</b>"
    )
    send_message(
        user_id, welcome_text, reply_markup=bottom_colorful_keyboard(user_id)
    )
    send_message(
        user_id,
        "🚀 <b>ওয়ান-ক্লিকে আপনার গ্রুপে বট যুক্ত করতে নিচের সবুজ বাটনে ট্যাপ করুন:</b>",
        reply_markup=add_to_group_inline_keyboard(),
    )


def handle_my_groups_list(user_id: int) -> None:
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT chat_id, title, antilink_on, welcome_on FROM groups ORDER BY added_at DESC LIMIT 15")
    rows = c.fetchall()
    conn.close()

    if not rows:
        send_message(
            user_id,
            "⚠️ এখনো কোনো গ্রুপে বট যুক্ত করা হয়নি!\n"
            "নিচের বাটনে ক্লিক করে আপনার গ্রুপে বট যুক্ত করুন এবং Admin পারমিশন দিন।",
            reply_markup=add_to_group_inline_keyboard(),
        )
        return

    lines = ["╭━━━🛡️ <b>সুরক্ষিত গ্রুপ তালিকা</b> 🛡️━━━╮\n"]
    for idx, (cid, title, al_on, wel_on) in enumerate(rows, start=1):
        lines.append(
            f"<b>{idx}. 🏷️ {safe_html(title)}</b>\n"
            f"   🆔 <code>{cid}</code> | Anti-Link: {'✅' if al_on else '❌'} | Welcome: {'✅' if wel_on else '❌'}\n"
        )
    lines.append("╰━━━━━━━━━━━━━━━━━━━━━━━━━━╯")
    lines.append("💡 <i>গ্রুপের ভেতরে <code>/settings</code> লিখলে কালারফুল কন্ট্রোল বাটন চলে আসবে।</i>")
    send_message(user_id, "\n".join(lines), reply_markup=bottom_colorful_keyboard(user_id))


def handle_live_stats(user_id: int) -> None:
    stats = get_stats()
    bot_display = safe_html(get_setting("bot_name", DEFAULT_BOT_NAME))
    text = (
        f"╭━━━📊 <b>{bot_display} — লাইভ স্ট্যাটাস</b> 📊━━━╮\n"
        f"┃ 👥 <b>মোট ইউজার:</b> <code>{stats['users']}</code> জন\n"
        f"┃ 🏰 <b>মোট গ্রুপ:</b> <code>{stats['groups']}</code> টি\n"
        f"┃ 👋 <b>পাঠানো ওয়েলকাম কার্ড:</b> <code>{stats['welcomes']}</code> টি\n"
        f"┃ 🚫 <b>ডিলিট করা স্প্যাম/লিংক:</b> <code>{stats['spam_blocked']}</code> টি\n"
        f"┃ 🔨 <b>অ্যাকশন (ব্যান/মিউট/ওয়ার্ন):</b> <code>{stats['actions']}</code> বার\n"
        f"┃ ⚡ <b>সিকিউরিটি ইঞ্জিন:</b> ২৪/৭ হাই-স্পিড সক্রিয়\n"
        f"╰━━━━━━━━━━━━━━━━━━━━━━━━━━━━╯"
    )
    send_message(user_id, text, reply_markup=bottom_colorful_keyboard(user_id))


def handle_support(user_id: int) -> None:
    ch = (
        get_setting("channel_username", "")
        .strip()
        .replace("https://t.me/", "")
        .lstrip("@")
    )
    sup = (
        get_setting("support_username", "")
        .strip()
        .replace("https://t.me/", "")
        .lstrip("@")
    )
    bot_display = safe_html(get_setting("bot_name", DEFAULT_BOT_NAME))

    lines = [
        f"╭━━━📞 <b>{bot_display} — হেল্প সেন্টার</b> 📞━━━╮",
        "┃ আপনার গ্রুপকে স্প্যামার ও লিংক থেকে রক্ষা করতে",
        "┃ বটটিকে গ্রুপে যুক্ত করে Admin পারমিশন দিন",
        "┃ এবং গ্রুপে /settings লিখে সেটিংস সাজিয়ে নিন!",
        "╰━━━━━━━━━━━━━━━━━━━━━━━━━━╯",
    ]
    kb_rows = []
    if ch:
        lines.append(f"\n📢 <b>অফিশিয়াল চ্যানেল:</b> @{safe_html(ch)}")
        kb_rows.append(
            [
                {
                    "text": "📢 চ্যানেলে জয়েন করুন",
                    "url": f"https://t.me/{ch}",
                    "style": "primary",
                }
            ]
        )
    if sup:
        lines.append(f"👨‍💻 <b>অ্যাডমিন সাপোর্ট:</b> @{safe_html(sup)}")
        kb_rows.append(
            [
                {
                    "text": "💬 অ্যাডমিনকে মেসেজ দিন",
                    "url": f"https://t.me/{sup}",
                    "style": "success",
                }
            ]
        )

    markup = (
        {"inline_keyboard": kb_rows} if kb_rows else bottom_colorful_keyboard(user_id)
    )
    send_message(user_id, "\n".join(lines), reply_markup=markup)


# ==================== ADMIN PANEL HANDLERS ====================
def build_admin_panel_text() -> str:
    stats = get_stats()
    fs_status = get_setting("force_sub", "off").upper()
    maint_status = get_setting("maintenance", "off").upper()
    ch = get_setting("channel_username", "")
    sup = get_setting("support_username", "")

    ch_display = f"@{ch.lstrip('@')}" if ch else "সেট করা নেই"
    sup_display = f"@{sup.lstrip('@')}" if sup else "সেট করা নেই"

    return (
        f"╭━━━👑 <b>অ্যাডমিন কন্ট্রোল প্যানেল</b> 👑━━━╮\n"
        f"┃ 📢 <b>CHANNEL_USERNAME:</b> <code>{safe_html(ch_display)}</code>\n"
        f"┃ 🔒 <b>Force Channel Join:</b> <code>{fs_status}</code>\n"
        f"┃ 👨‍💻 <b>SUPPORT_USERNAME:</b> <code>{safe_html(sup_display)}</code>\n"
        f"┃ 🛠️ <b>Maintenance Mode:</b> <code>{maint_status}</code>\n"
        f"┣━━━━━━━━━━━━━━━━━━━━━━━━━━━━┫\n"
        f"┃ 👥 <b>মোট ইউজার:</b> <code>{stats['users']}</code> জন\n"
        f"┃ 🏰 <b>সুরক্ষিত গ্রুপ:</b> <code>{stats['groups']}</code> টি\n"
        f"┃ 👋 <b>মোট ওয়েলকাম:</b> <code>{stats['welcomes']}</code> টি\n"
        f"┃ 🚫 <b>ব্লক করা স্প্যাম:</b> <code>{stats['spam_blocked']}</code> টি\n"
        f"┃ 🔨 <b>মোট অ্যাকশন:</b> <code>{stats['actions']}</code> বার\n"
        f"┃ 🚫 <b>ব্যানড ইউজার:</b> <code>{stats['banned']}</code> জন\n"
        f"╰━━━━━━━━━━━━━━━━━━━━━━━━━━━━╯\n\n"
        f"👇 <b>নিচের কালারফুল বাটন থেকে সরাসরি ফোর্স চ্যানেল ও সাপোর্ট সেট করুন:</b>"
    )


def handle_admin_panel(user_id: int, edit_msg_id: Optional[int] = None) -> None:
    if not is_admin(user_id):
        send_message(user_id, "⛔ শুধুমাত্র অ্যাডমিন এই প্যানেলে প্রবেশ করতে পারবেন।")
        return

    text = build_admin_panel_text()
    kb = admin_inline_keyboard()
    if edit_msg_id:
        edit_message_text(user_id, edit_msg_id, text, reply_markup=kb)
    else:
        send_message(user_id, text, reply_markup=kb)


def handle_broadcast_send(admin_id: int, message_text: str) -> None:
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT user_id FROM users")
    u_rows = [r[0] for r in c.fetchall()]
    c.execute("SELECT chat_id FROM groups")
    g_rows = [r[0] for r in c.fetchall()]
    conn.close()

    targets = u_rows + g_rows
    total = len(targets)
    success = 0
    failed = 0

    status_msg = send_message(
        admin_id,
        f"⏳ <b>ব্রডকাস্ট পাঠানো হচ্ছে...</b> মোট টার্গেট: <code>{total}</code> টি",
    )
    msg_id = status_msg.get("result", {}).get("message_id")

    formatted = (
        f"╭━━━📢 <b>বিশেষ ঘোষণা</b> 📢━━━╮\n\n"
        f"{safe_html(message_text)}\n\n"
        f"╰━━━━━━━━━━━━━━━━━━━━━━━╯"
    )
    for tid in targets:
        try:
            res = send_message(tid, formatted)
            if res.get("ok"):
                success += 1
            else:
                failed += 1
            time.sleep(0.04)
        except Exception:
            failed += 1

    done_text = (
        f"✅ <b>ব্রডকাস্ট সম্পন্ন হয়েছে!</b>\n\n"
        f"🎯 মোট ইউজার ও গ্রুপ: <code>{total}</code>\n"
        f"✅ সফলভাবে গেছে: <code>{success}</code>\n"
        f"❌ ব্যর্থ/ব্লকড: <code>{failed}</code>"
    )
    if msg_id:
        edit_message_text(admin_id, msg_id, done_text)
    else:
        send_message(admin_id, done_text)


# ==================== GROUP GUARD & MODERATION ENGINE ====================
def contains_link(text: str, entities: List[Dict[str, Any]]) -> bool:
    for ent in entities or []:
        if ent.get("type") in ("url", "text_link"):
            return True
    if re.search(
        r"(?i)(https?://|t\.me/|telegram\.me/|telegram\.dog/|www\.|[a-z0-9-]+\.(com|org|net|xyz|me|io|live|top|vip)\b)",
        text or "",
    ):
        return True
    return False


def handle_group_message(msg: Dict[str, Any]) -> None:
    chat = msg.get("chat") or {}
    chat_id = chat.get("id")
    chat_title = chat.get("title") or "Group"
    from_user = msg.get("from") or {}
    user_id = from_user.get("id")
    first_name = from_user.get("first_name") or "Member"
    msg_id = msg.get("message_id")

    if not chat_id or not msg_id:
        return

    group_cfg = register_or_get_group(chat_id, chat_title)

    # 1. Handle New Chat Members (Auto Welcome & Anti-Bot & Captcha)
    new_members = msg.get("new_chat_members") or []
    if new_members:
        adder_is_admin = is_group_admin(chat_id, user_id) if user_id else False
        for nm in new_members:
            nm_id = nm.get("id")
            nm_name = nm.get("first_name") or "নতুন সদস্য"
            nm_is_bot = bool(nm.get("is_bot"))

            # If the bot itself was added, send setup greeting
            if nm_is_bot and nm.get("username", "").lower() == get_setting("bot_username", "").lower():
                send_message(
                    chat_id,
                    build_group_settings_card(chat_id),
                    reply_markup=group_settings_inline_keyboard(chat_id),
                )
                continue

            # Anti-Bot protection
            if nm_is_bot and group_cfg["antibot_on"] and not adder_is_admin:
                kick_member(chat_id, nm_id)
                increment_stat("spam_blocked", 1)
                send_message(
                    chat_id,
                    f"🤖 <b>Anti-Bot Guard:</b> অনুমতি ছাড়া যুক্ত করা বট <b>{safe_html(nm_name)}</b>-কে গ্রুপ থেকে রিমুভ করা হয়েছে!",
                )
                continue

            # Captcha mute if enabled
            need_captcha = bool(group_cfg["captcha_on"] and not nm_is_bot)
            if need_captcha:
                restrict_member(chat_id, nm_id, can_send=False)

            # Welcome card if enabled
            if group_cfg["welcome_on"] and not nm_is_bot:
                increment_stat("welcomes_sent", 1)
                card = build_welcome_card(
                    chat_title,
                    nm_name,
                    nm_id,
                    group_cfg["welcome_text"],
                    need_captcha,
                )
                send_message(
                    chat_id,
                    card,
                    reply_markup=welcome_card_inline_keyboard(
                        chat_id, nm_id, need_captcha
                    ),
                )
        return

    if not user_id:
        return

    text = (msg.get("text") or msg.get("caption") or "").strip()
    entities = (msg.get("entities") or []) + (msg.get("caption_entities") or [])
    user_admin = is_group_admin(chat_id, user_id)

    # 2. Public Group Commands (/rules)
    cmd_lower = text.split()[0].lower().split("@")[0] if text else ""
    if cmd_lower in ("/rules", "নিয়ম", "রুলস"):
        rules = (
            group_cfg["rules_text"].strip()
            or "১. গ্রুপে কোনো প্রকার স্প্যাম বা লিংক শেয়ার করা নিষেধ।\n২. সবার সাথে ভদ্র ও সম্মানজনক আচরণ করুন।\n৩. অপ্রাসঙ্গিক বিজ্ঞাপন বা ফরোয়ার্ড মেসেজ দেওয়া যাবে না।"
        )
        send_message(
            chat_id,
            f"╭━━━📜 <b>{safe_html(chat_title)} — নিয়মাবলী</b> 📜━━━╮\n\n"
            f"{safe_html(rules)}\n\n"
            f"╰━━━━━━━━━━━━━━━━━━━━━━━━━━━━╯",
            reply_to_message_id=msg_id,
        )
        return

    # 3. Admin Group Commands (/settings, /ban, /mute, /kick, /warn, /setwelcome, /setrules, /addword, /pin)
    if user_admin:
        if cmd_lower in ("/settings", "/panel", "সেটিংস"):
            send_message(
                chat_id,
                build_group_settings_card(chat_id),
                reply_markup=group_settings_inline_keyboard(chat_id),
                reply_to_message_id=msg_id,
            )
            return

        if cmd_lower == "/setwelcome":
            parts = text.split(maxsplit=1)
            if len(parts) < 2:
                send_message(
                    chat_id,
                    "✍️ ব্যবহারের নিয়ম: <code>/setwelcome আমাদের গ্রুপে আপনাকে স্বাগতম!</code>",
                    reply_to_message_id=msg_id,
                )
                return
            update_group_text(chat_id, "welcome_text", parts[1].strip())
            send_message(
                chat_id,
                "✅ গ্রুপের নতুন কাস্টম ওয়েলকাম মেসেজ সফলভাবে সেভ করা হয়েছে!",
                reply_to_message_id=msg_id,
            )
            return

        if cmd_lower == "/setrules":
            parts = text.split(maxsplit=1)
            if len(parts) < 2:
                send_message(
                    chat_id,
                    "✍️ ব্যবহারের নিয়ম: <code>/setrules ১. লিংক দেওয়া নিষেধ...</code>",
                    reply_to_message_id=msg_id,
                )
                return
            update_group_text(chat_id, "rules_text", parts[1].strip())
            send_message(
                chat_id,
                "✅ গ্রুপের নিয়মাবলী (Rules) সফলভাবে আপডেট করা হয়েছে!",
                reply_to_message_id=msg_id,
            )
            return

        if cmd_lower == "/addword":
            parts = text.split(maxsplit=1)
            if len(parts) < 2:
                send_message(
                    chat_id,
                    "✍️ ব্যবহারের নিয়ম: <code>/addword নিষিদ্ধ_শব্দ</code>",
                    reply_to_message_id=msg_id,
                )
                return
            new_w = parts[1].strip().lower()
            bw_list = group_cfg["bad_words"]
            if new_w not in bw_list:
                bw_list.append(new_w)
                update_group_text(chat_id, "bad_words", ",".join(bw_list))
            send_message(
                chat_id,
                f"✅ <code>{safe_html(new_w)}</code> শব্দটি ব্লক লিস্টে যুক্ত করা হয়েছে!",
                reply_to_message_id=msg_id,
            )
            return

        # Reply-based moderation commands
        reply_msg = msg.get("reply_to_message")
        if reply_msg:
            target_user = reply_msg.get("from") or {}
            t_id = target_user.get("id")
            t_name = target_user.get("first_name") or "User"

            if cmd_lower in ("/pin", "পিন"):
                tg_request(
                    "pinChatMessage",
                    {"chat_id": chat_id, "message_id": reply_msg.get("message_id")},
                )
                send_message(chat_id, "📌 মেসেজটি পিন করা হয়েছে!", reply_to_message_id=msg_id)
                return

            if cmd_lower in ("/unpin", "আনপিন"):
                tg_request(
                    "unpinChatMessage",
                    {"chat_id": chat_id, "message_id": reply_msg.get("message_id")},
                )
                send_message(chat_id, "📌 মেসেজটি আনপিন করা হয়েছে!", reply_to_message_id=msg_id)
                return

            if t_id and not is_group_admin(chat_id, t_id):
                if cmd_lower in ("/ban", "ব্যান"):
                    if ban_member(chat_id, t_id):
                        increment_stat("actions_taken", 1)
                        kb = {
                            "inline_keyboard": [
                                [
                                    {
                                        "text": "🔓 আনব্যান করুন",
                                        "callback_data": f"mod_unban|{chat_id}|{t_id}",
                                        "style": "success",
                                    }
                                ]
                            ]
                        }
                        send_message(
                            chat_id,
                            f"🚫 <b>অ্যাকশন:</b> <a href=\"tg://user?id={t_id}\">{safe_html(t_name)}</a>-কে গ্রুপ থেকে <b>Ban</b> করা হয়েছে!",
                            reply_markup=kb,
                        )
                    return

                if cmd_lower in ("/unban", "আনব্যান"):
                    if unban_member(chat_id, t_id):
                        send_message(
                            chat_id,
                            f"✅ <a href=\"tg://user?id={t_id}\">{safe_html(t_name)}</a>-কে <b>Unban</b> করা হয়েছে!",
                        )
                    return

                if cmd_lower in ("/kick", "কিক"):
                    if kick_member(chat_id, t_id):
                        increment_stat("actions_taken", 1)
                        send_message(
                            chat_id,
                            f"👢 <a href=\"tg://user?id={t_id}\">{safe_html(t_name)}</a>-কে গ্রুপ থেকে বের করে দেওয়া হয়েছে!",
                        )
                    return

                if cmd_lower in ("/mute", "মিউট"):
                    if restrict_member(chat_id, t_id, can_send=False):
                        increment_stat("actions_taken", 1)
                        kb = {
                            "inline_keyboard": [
                                [
                                    {
                                        "text": "🔊 আনমিউট করুন",
                                        "callback_data": f"mod_unmute|{chat_id}|{t_id}",
                                        "style": "success",
                                    }
                                ]
                            ]
                        }
                        send_message(
                            chat_id,
                            f"🔇 <a href=\"tg://user?id={t_id}\">{safe_html(t_name)}</a>-কে <b>Mute</b> করা হয়েছে!",
                            reply_markup=kb,
                        )
                    return

                if cmd_lower in ("/unmute", "আনমিউট"):
                    if restrict_member(chat_id, t_id, can_send=True):
                        send_message(
                            chat_id,
                            f"🔊 <a href=\"tg://user?id={t_id}\">{safe_html(t_name)}</a>-কে <b>Unmute</b> করা হয়েছে!",
                        )
                    return

                if cmd_lower in ("/warn", "ওয়ার্ন"):
                    w_count = add_user_warning(chat_id, t_id)
                    increment_stat("actions_taken", 1)
                    if w_count >= group_cfg["warn_limit"]:
                        ban_member(chat_id, t_id)
                        reset_user_warnings(chat_id, t_id)
                        send_message(
                            chat_id,
                            f"🚫 <a href=\"tg://user?id={t_id}\">{safe_html(t_name)}</a>-এর ওয়ার্নিং লিমিট (<code>{w_count}/{group_cfg['warn_limit']}</code>) পূর্ণ হওয়ায় অটোমেটিক <b>Ban</b> করা হয়েছে!",
                        )
                    else:
                        send_message(
                            chat_id,
                            f"⚠️ <a href=\"tg://user?id={t_id}\">{safe_html(t_name)}</a>-কে ওয়ার্নিং দেওয়া হয়েছে! (মোট ওয়ার্নিং: <code>{w_count}/{group_cfg['warn_limit']}</code>)",
                        )
                    return

                if cmd_lower == "/resetwarn":
                    reset_user_warnings(chat_id, t_id)
                    send_message(
                        chat_id,
                        f"✅ <a href=\"tg://user?id={t_id}\">{safe_html(t_name)}</a>-এর সব ওয়ার্নিং রিসেট করা হয়েছে!",
                    )
                    return

        return

    # 4. Automatic Security Filters for Non-Admin Group Members
    # 4a. Anti-Forward Filter
    if group_cfg["antiforward_on"] and (
        msg.get("forward_origin") or msg.get("forward_from") or msg.get("forward_from_chat")
    ):
        delete_message(chat_id, msg_id)
        increment_stat("spam_blocked", 1)
        send_message(
            chat_id,
            f"📢 <a href=\"tg://user?id={user_id}\">{safe_html(first_name)}</a>, এই গ্রুপে অন্য চ্যানেলের মেসেজ ফরোয়ার্ড করা নিষেধ!",
        )
        return

    # 4b. Anti-Link Filter
    if group_cfg["antilink_on"] and contains_link(text, entities):
        delete_message(chat_id, msg_id)
        increment_stat("spam_blocked", 1)
        w_count = add_user_warning(chat_id, user_id)
        if w_count >= group_cfg["warn_limit"]:
            restrict_member(chat_id, user_id, can_send=False)
            reset_user_warnings(chat_id, user_id)
            kb = {
                "inline_keyboard": [
                    [
                        {
                            "text": "🔊 আনমিউট করুন (Admin)",
                            "callback_data": f"mod_unmute|{chat_id}|{user_id}",
                            "style": "success",
                        }
                    ]
                ]
            }
            send_message(
                chat_id,
                f"🔇 <a href=\"tg://user?id={user_id}\">{safe_html(first_name)}</a> বারবার লিংক শেয়ার করার কারণে অটোমেটিক <b>Mute</b> হয়েছেন!",
                reply_markup=kb,
            )
        else:
            send_message(
                chat_id,
                f"🔗 <a href=\"tg://user?id={user_id}\">{safe_html(first_name)}</a>, গ্রুপে লিংক শেয়ার করা নিষেধ! (ওয়ার্নিং: <code>{w_count}/{group_cfg['warn_limit']}</code>)",
            )
        return

    # 4c. Bad-Word Filter
    if group_cfg["badword_on"] and text:
        low_txt = text.lower()
        for bw in group_cfg["bad_words"]:
            if bw and bw in low_txt:
                delete_message(chat_id, msg_id)
                increment_stat("spam_blocked", 1)
                send_message(
                    chat_id,
                    f"🤬 <a href=\"tg://user?id={user_id}\">{safe_html(first_name)}</a>, অনুগ্রহ করে গ্রুপে মার্জিত ভাষা ব্যবহার করুন!",
                )
                return


# ==================== MESSAGE & CALLBACK ROUTERS ====================
def process_message(msg: Dict[str, Any]) -> None:
    chat = msg.get("chat") or {}
    chat_type = chat.get("type", "private")

    if chat_type in ("group", "supergroup"):
        handle_group_message(msg)
        return

    user = msg.get("from") or {}
    user_id = user.get("id")
    if not user_id:
        return

    username = user.get("username", "")
    first_name = user.get("first_name", "User")
    text = (msg.get("text") or "").strip()

    register_or_update_user(user_id, username, first_name)
    if is_user_banned(user_id) and not is_admin(user_id):
        send_message(user_id, "🚫 আপনাকে এই বট ব্যবহার থেকে বিরত রাখা হয়েছে।")
        return

    if get_setting("maintenance", "off").lower() == "on" and not is_admin(user_id):
        send_message(
            user_id,
            "🛠️ বটে এখন আপগ্রেডের কাজ চলছে। কিছুক্ষণ পর আবার চেষ্টা করুন।",
        )
        return

    menu_commands = {
        "/start",
        "/admin",
        "/groups",
        "/commands",
        "/security",
        "/stats",
        "/help",
        BTN_ADD_GROUP,
        BTN_MY_GROUPS,
        BTN_COMMANDS,
        BTN_FILTER_HELP,
        BTN_LIVE_STATS,
        BTN_SUPPORT,
        BTN_ADMIN_PANEL,
    }
    if text in menu_commands:
        user_states.pop(user_id, None)

    # Admin Input States
    if is_admin(user_id) and user_id in user_states:
        state = user_states.pop(user_id, "")
        if text.lower() in ("বাতিল", "cancel"):
            send_message(user_id, "❌ অপারেশন বাতিল করা হয়েছে।")
            handle_admin_panel(user_id)
            return

        if state == "waiting_broadcast":
            handle_broadcast_send(user_id, text)
            return
        if state == "waiting_set_channel":
            clean_ch = text.replace("https://t.me/", "").replace("@", "").strip()
            set_setting("channel_username", clean_ch)
            set_setting("force_sub", "on")
            send_message(
                user_id,
                f"✅ ফোর্স চ্যানেল সেট হয়েছে: <code>@{safe_html(clean_ch)}</code> এবং <b>Force Channel Join: ON</b> করা হয়েছে!",
            )
            handle_admin_panel(user_id)
            return
        if state == "waiting_set_channel_only":
            clean_ch = text.replace("https://t.me/", "").replace("@", "").strip()
            set_setting("channel_username", clean_ch)
            send_message(
                user_id,
                f"✅ চ্যানেল ইউজারনেম সেট হয়েছে: <code>@{safe_html(clean_ch)}</code>",
            )
            handle_admin_panel(user_id)
            return
        if state == "waiting_set_support":
            clean_sup = text.replace("https://t.me/", "").replace("@", "").strip()
            set_setting("support_username", clean_sup)
            send_message(
                user_id,
                f"✅ সাপোর্ট ইউজারনেম সেট হয়েছে: <code>@{safe_html(clean_sup)}</code>",
            )
            handle_admin_panel(user_id)
            return
        if state == "waiting_ban_uid":
            if text.lstrip("-").isdigit():
                uid = int(text)
                now_ban = not is_user_banned(uid)
                set_user_ban_status(uid, now_ban)
                st_label = "🚫 ব্যান (Banned)" if now_ban else "✅ আনব্যান (Unbanned)"
                send_message(user_id, f"✅ ইউজার <code>{uid}</code> এখন {st_label}।")
            else:
                send_message(user_id, "⚠️ সঠিক Numeric User ID দিন।")
            handle_admin_panel(user_id)
            return

    if not is_user_subscribed(user_id):
        handle_private_start(user_id, username, first_name)
        return

    if text == "/start":
        handle_private_start(user_id, username, first_name)
    elif text in (BTN_ADMIN_PANEL, "/admin"):
        handle_admin_panel(user_id)
    elif text == BTN_ADD_GROUP:
        send_message(
            user_id,
            "➕ <b>আপনার গ্রুপে বট যুক্ত করতে নিচের সবুজ বাটনে ট্যাপ করুন এবং বটকে Admin পারমিশন দিন:</b>",
            reply_markup=add_to_group_inline_keyboard(),
        )
    elif text in (BTN_MY_GROUPS, "/groups"):
        handle_my_groups_list(user_id)
    elif text in (BTN_COMMANDS, "/commands"):
        send_message(
            user_id,
            build_commands_guide_text(),
            reply_markup=bottom_colorful_keyboard(user_id),
        )
    elif text in (BTN_FILTER_HELP, "/security"):
        send_message(
            user_id,
            build_security_features_text(),
            reply_markup=bottom_colorful_keyboard(user_id),
        )
    elif text in (BTN_LIVE_STATS, "/stats"):
        handle_live_stats(user_id)
    elif text in (BTN_SUPPORT, "/help"):
        handle_support(user_id)
    else:
        send_message(
            user_id,
            "👇 নিচের কালারফুল মেনু থেকে যেকোনো একটি বাটনে চাপ দিন:",
            reply_markup=bottom_colorful_keyboard(user_id),
        )


def process_callback_query(cb: Dict[str, Any]) -> None:
    cb_id = cb.get("id")
    from_user = cb.get("from") or {}
    user_id = from_user.get("id")
    data = cb.get("data", "")
    message = cb.get("message") or {}
    msg_id = message.get("message_id")
    chat_id = (message.get("chat") or {}).get("id")

    if not user_id or not cb_id:
        return

    if data == "check_sub":
        if is_user_subscribed(user_id):
            answer_callback_query(cb_id, "✅ ভেরিফিকেশন সফল হয়েছে!", show_alert=True)
            handle_private_start(
                user_id,
                from_user.get("username", ""),
                from_user.get("first_name", "User"),
            )
        else:
            answer_callback_query(
                cb_id,
                "❌ আপনি এখনো চ্যানেলে জয়েন করেননি! আগে জয়েন করে আবার চেষ্টা করুন।",
                show_alert=True,
            )
        return

    if data == "show_commands":
        answer_callback_query(cb_id)
        send_message(user_id, build_commands_guide_text())
        return

    if data == "show_security":
        answer_callback_query(cb_id)
        send_message(user_id, build_security_features_text())
        return

    # Group Captcha Verification
    if data.startswith("verify|"):
        parts = data.split("|")
        if len(parts) == 3:
            g_id = int(parts[1])
            target_uid = int(parts[2])
            if user_id != target_uid and not is_group_admin(g_id, user_id):
                answer_callback_query(
                    cb_id,
                    "⚠️ এই ভেরিফিকেশন বাটনটি শুধুমাত্র নতুন জয়েন করা সদস্যের জন্য!",
                    show_alert=True,
                )
                return
            restrict_member(g_id, target_uid, can_send=True)
            answer_callback_query(
                cb_id, "✅ ভেরিফিকেশন সম্পন্ন! এখন আপনি গ্রুপে চ্যাট করতে পারবেন।", show_alert=True
            )
            if chat_id and msg_id:
                edit_message_text(
                    chat_id,
                    msg_id,
                    f"✅ সদস্য ভেরিফাইড হয়েছেন এবং চ্যাট করার অনুমতি পেয়েছেন!",
                    reply_markup=welcome_card_inline_keyboard(g_id, target_uid, False),
                )
        return

    # Group Rules Popup/Message
    if data.startswith("rules|"):
        g_id = int(data.split("|")[1])
        g = register_or_get_group(g_id)
        rules = (
            g["rules_text"].strip()
            or "১. গ্রুপে কোনো লিংক বা স্প্যাম দেওয়া নিষেধ।\n২. সবার সাথে ভদ্র আচরণ করুন।\n৩. বিজ্ঞাপন ফরোয়ার্ড করা যাবে না।"
        )
        answer_callback_query(cb_id, rules[:190], show_alert=True)
        return

    # Group Settings Toggles
    if data.startswith(("gtoggle|", "grefresh|", "gclose|")):
        parts = data.split("|")
        g_id = int(parts[1])
        if not is_group_admin(g_id, user_id):
            answer_callback_query(
                cb_id, "⛔ শুধুমাত্র গ্রুপ অ্যাডমিন সেটিংস পরিবর্তন করতে পারবেন!", show_alert=True
            )
            return

        answer_callback_query(cb_id, "✅ আপডেট হয়েছে!")
        if data.startswith("gtoggle|") and len(parts) == 3:
            toggle_group_field(g_id, parts[2])
            if chat_id and msg_id:
                edit_message_text(
                    chat_id,
                    msg_id,
                    build_group_settings_card(g_id),
                    reply_markup=group_settings_inline_keyboard(g_id),
                )
        elif data.startswith("grefresh|"):
            if chat_id and msg_id:
                edit_message_text(
                    chat_id,
                    msg_id,
                    build_group_settings_card(g_id),
                    reply_markup=group_settings_inline_keyboard(g_id),
                )
        elif data.startswith("gclose|"):
            if chat_id and msg_id:
                edit_message_text(chat_id, msg_id, "🔒 সিকিউরিটি সেটিংস প্যানেল বন্ধ করা হয়েছে।")
        return

    # Moderation Inline Actions (Unban / Unmute)
    if data.startswith(("mod_unban|", "mod_unmute|")):
        parts = data.split("|")
        g_id = int(parts[1])
        t_uid = int(parts[2])
        if not is_group_admin(g_id, user_id):
            answer_callback_query(
                cb_id, "⛔ শুধুমাত্র গ্রুপ অ্যাডমিন এই বাটন ব্যবহার করতে পারবেন!", show_alert=True
            )
            return

        if data.startswith("mod_unban|"):
            unban_member(g_id, t_uid)
            answer_callback_query(cb_id, "✅ ইউজারকে আনব্যান করা হয়েছে!", show_alert=True)
            if chat_id and msg_id:
                edit_message_text(chat_id, msg_id, f"✅ ইউজার <code>{t_uid}</code>-কে আনব্যান করা হয়েছে।")
        else:
            restrict_member(g_id, t_uid, can_send=True)
            answer_callback_query(cb_id, "🔊 ইউজারকে আনমিউট করা হয়েছে!", show_alert=True)
            if chat_id and msg_id:
                edit_message_text(chat_id, msg_id, f"🔊 ইউজার <code>{t_uid}</code>-কে আনমিউট করা হয়েছে।")
        return

    answer_callback_query(cb_id)

    # Bot Owner Admin Panel Callbacks
    if data.startswith("admin_") and is_admin(user_id):
        if data == "admin_refresh":
            handle_admin_panel(user_id, edit_msg_id=msg_id)
        elif data == "admin_broadcast":
            user_states[user_id] = "waiting_broadcast"
            send_message(
                user_id,
                "📢 <b>সব ইউজার ও গ্রুপে যে মেসেজ পাঠাতে চান তা লিখে পাঠান</b> (অথবা <code>বাতিল</code> লিখুন):",
            )
        elif data == "admin_toggle_forcesub":
            ch = get_setting("channel_username", "").strip()
            curr = get_setting("force_sub", "off").lower()
            if curr == "off" and not ch:
                user_states[user_id] = "waiting_set_channel"
                send_message(
                    user_id,
                    "⚠️ এখনো কোনো চ্যানেল সেট করা নেই!\n"
                    "📢 <b>আপনার টেলিগ্রাম চ্যানেলের ইউজারনেম লিখে পাঠান</b> (যেমন: <code>my_channel</code>, অথবা <code>বাতিল</code> লিখুন):",
                )
                return
            set_setting("force_sub", "off" if curr == "on" else "on")
            handle_admin_panel(user_id, edit_msg_id=msg_id)
        elif data == "admin_remove_channel":
            set_setting("channel_username", "")
            set_setting("force_sub", "off")
            handle_admin_panel(user_id, edit_msg_id=msg_id)
        elif data == "admin_toggle_maint":
            curr = get_setting("maintenance", "off").lower()
            set_setting("maintenance", "off" if curr == "on" else "on")
            handle_admin_panel(user_id, edit_msg_id=msg_id)
        elif data == "admin_set_channel":
            user_states[user_id] = "waiting_set_channel"
            send_message(
                user_id,
                "📢 <b>আপনার ফোর্স চ্যানেলের ইউজারনেম লিখে পাঠান</b> (@ ছাড়া, যেমন: <code>my_channel</code>, অথবা <code>বাতিল</code> লিখুন):",
            )
        elif data == "admin_set_channel_only":
            user_states[user_id] = "waiting_set_channel_only"
            send_message(
                user_id,
                "📢 <b>আপনার চ্যানেল ইউজারনেম লিখে পাঠান</b> (@ ছাড়া, যেমন: <code>my_channel</code>, অথবা <code>বাতিল</code> লিখুন):",
            )
        elif data == "admin_set_support":
            user_states[user_id] = "waiting_set_support"
            send_message(
                user_id,
                "👨‍💻 <b>আপনার সাপোর্ট আইডির ইউজারনেম লিখে পাঠান</b> (@ ছাড়া, যেমন: <code>my_support</code>, অথবা <code>বাতিল</code> লিখুন):",
            )
        elif data == "admin_ban_menu":
            user_states[user_id] = "waiting_ban_uid"
            send_message(
                user_id,
                "🚫 <b>যেই ইউজারকে ব্যান বা আনব্যান করতে চান তার Numeric User ID লিখে পাঠান:</b>",
            )
        elif data == "admin_close":
            edit_message_text(user_id, msg_id, "🔒 অ্যাডমিন প্যানেল বন্ধ করা হয়েছে।")


# ==================== RENDER 24/7 HTTP HEALTH SERVER ====================
class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        stats = get_stats()
        b_name = get_setting("bot_name", DEFAULT_BOT_NAME)
        payload = {
            "status": "online",
            "bot": b_name,
            "users": stats["users"],
            "protected_groups": stats["groups"],
            "spam_blocked": stats["spam_blocked"],
        }
        self.send_response(200)
        self.send_header("Content-type", "application/json; charset=utf-8")
        self.end_headers()
        self.wfile.write(json.dumps(payload).encode("utf-8"))

    def log_message(self, format: str, *args: Any) -> None:
        pass


def run_http_server() -> None:
    try:
        server = HTTPServer(("0.0.0.0", PORT), HealthCheckHandler)
        logger.info(f"Render Health Server listening on port {PORT}")
        server.serve_forever()
    except Exception as e:
        logger.warning(f"HTTP Server error: {e}")


# ==================== MAIN BOT LOOP ====================
def run_bot() -> None:
    init_db()
    threading.Thread(target=run_http_server, daemon=True).start()

    if not BOT_TOKEN:
        logger.critical(
            "BOT_TOKEN is missing! Set BOT_TOKEN in Render Environment Variables."
        )
        while True:
            time.sleep(10)

    sync_bot_info_from_telegram()
    logger.info("🚀 Bangla Group Guard & Auto Welcome Bot is online and polling...")
    offset = 0

    while True:
        try:
            resp = tg_request(
                "getUpdates",
                {
                    "offset": offset,
                    "timeout": 25,
                    "allowed_updates": ["message", "callback_query"],
                },
                timeout=30,
            )
            if resp.get("ok"):
                for u in resp.get("result", []):
                    offset = u.get("update_id", offset) + 1
                    if "message" in u:
                        threading.Thread(
                            target=process_message,
                            args=(u["message"],),
                            daemon=True,
                        ).start()
                    elif "callback_query" in u:
                        threading.Thread(
                            target=process_callback_query,
                            args=(u["callback_query"],),
                            daemon=True,
                        ).start()
            else:
                time.sleep(2)
        except Exception as e:
            logger.error(f"Polling error: {e}")
            time.sleep(3)


if __name__ == "__main__":
    run_bot()