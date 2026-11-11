"""Loads configuration from environment variables (.env file)."""
import os
from dotenv import load_dotenv

load_dotenv()

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
GOOGLE_SHEETS_ID = os.getenv("GOOGLE_SHEETS_ID", "").strip()
GOOGLE_CREDENTIALS_FILE = os.getenv("GOOGLE_CREDENTIALS_FILE", "credentials.json").strip()

_allowed = os.getenv("ALLOWED_CHAT_IDS", "").strip()
# If set, only these Telegram chat IDs can use the bot. If empty, anyone can use it.
try:
    ALLOWED_CHAT_IDS = (
        set(int(x) for x in _allowed.split(",") if x.strip()) if _allowed else None
    )
except ValueError:
    raise RuntimeError("ALLOWED_CHAT_IDS must be comma-separated integers, e.g. 12345,-100987.")
# Fail closed: a value like "," would otherwise parse to an empty set, which the
# bot treats as "no restriction" and silently opens the bot to everyone.
if _allowed and not ALLOWED_CHAT_IDS:
    raise RuntimeError("ALLOWED_CHAT_IDS is set but contains no valid chat IDs.")

if not TELEGRAM_BOT_TOKEN:
    raise RuntimeError("TELEGRAM_BOT_TOKEN is missing. Check your .env file.")
if not GOOGLE_SHEETS_ID:
    raise RuntimeError("GOOGLE_SHEETS_ID is missing. Check your .env file.")