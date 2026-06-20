"""Tiny JSON-file-backed state: remembers which sheet/tab each chat is using."""
import json
import os

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


def get_active_sheet(chat_id: int):
    """Returns the sheet name this chat is pinned to, or None for 'current month' (default)."""
    return _load().get(str(chat_id))


def set_active_sheet(chat_id: int, name) -> None:
    data = _load()
    if name is None:
        data.pop(str(chat_id), None)
    else:
        data[str(chat_id)] = name
    _save(data)
