"""Tiny JSON-file-backed state."""
import json
import os
import uuid

STATE_FILE = os.path.join(os.path.dirname(__file__), "state.json")


def _load() -> dict:
    if not os.path.exists(STATE_FILE):
        return {}
    with open(STATE_FILE, "r", encoding="utf-8") as f:
        try:
            return json.load(f)
        except json.JSONDecodeError:
            return {}


def _save(data: dict) -> None:
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


# ── Active sheet ──────────────────────────────────────────────────────────────

def get_active_sheet(chat_id: int):
    return _load().get(str(chat_id))


def set_active_sheet(chat_id: int, name) -> None:
    data = _load()
    if name is None:
        data.pop(str(chat_id), None)
    else:
        data[str(chat_id)] = name
    _save(data)


# ── Members ───────────────────────────────────────────────────────────────────

def get_members(chat_id: int):
    return _load().get(f"members_{chat_id}", [])


def set_members(chat_id: int, members: list) -> None:
    data = _load()
    data[f"members_{chat_id}"] = members
    _save(data)


# ── Reminders (multiple per chat) ─────────────────────────────────────────────
# Stored as: "reminders_{chat_id}": [{"id": str, "hour": int, "minute": int, "message": str}, ...]

def get_reminders(chat_id: int) -> list:
    """Returns list of reminder dicts for this chat."""
    return _load().get(f"reminders_{chat_id}", [])


def add_reminder(chat_id: int, hour_utc: int, minute: int, message: str) -> str:
    """Adds a new reminder. Returns the generated reminder ID."""
    data = _load()
    key  = f"reminders_{chat_id}"
    reminders = data.get(key, [])
    rid = uuid.uuid4().hex[:8]  # short unique ID e.g. "a3f9c12b"
    reminders.append({"id": rid, "hour": hour_utc, "minute": minute, "message": message})
    data[key] = reminders
    _save(data)
    return rid


def delete_reminder(chat_id: int, reminder_id: str) -> bool:
    """Removes a reminder by ID. Returns True if found and removed."""
    data = _load()
    key  = f"reminders_{chat_id}"
    before = data.get(key, [])
    after  = [r for r in before if r["id"] != reminder_id]
    if len(after) == len(before):
        return False
    data[key] = after
    _save(data)
    return True


def delete_all_reminders(chat_id: int) -> None:
    data = _load()
    data.pop(f"reminders_{chat_id}", None)
    _save(data)


def get_all_reminders() -> dict:
    """Returns {chat_id (int): [list of reminder dicts]} for all chats."""
    data   = _load()
    result = {}
    for key, val in data.items():
        if key.startswith("reminders_") and isinstance(val, list):
            try:
                chat_id = int(key.replace("reminders_", ""))
                result[chat_id] = val
            except ValueError:
                pass
    return result