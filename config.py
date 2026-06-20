"""Loads configuration from environment variables (.env file)."""
import os
from dotenv import load_dotenv

load_dotenv()

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
GOOGLE_SHEETS_ID = os.getenv("GOOGLE_SHEETS_ID", "").strip()
GOOGLE_CREDENTIALS_FILE = os.getenv("GOOGLE_CREDENTIALS_FILE", "credentials.json").strip()

_allowed = os.getenv("ALLOWED_CHAT_IDS", "").strip()
# If set, only these Telegram chat IDs can use the bot. If empty, anyone can use it.
ALLOWED_CHAT_IDS = (
    set(int(x) for x in _allowed.split(",") if x.strip()) if _allowed else None
)

if not TELEGRAM_BOT_TOKEN:
    raise RuntimeError("TELEGRAM_BOT_TOKEN is missing. Check your .env file.")
if not GOOGLE_SHEETS_ID:
    raise RuntimeError("GOOGLE_SHEETS_ID is missing. Check your .env file.")
