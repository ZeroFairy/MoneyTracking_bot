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


# ── Budgets (per chat, per category) ──────────────────────────────────────────
# Stored as: "budgets_{chat_id}": {category: amount}

def get_budgets(chat_id: int) -> dict:
    """Returns {category: amount} for this chat, or {} if none set."""
    return _load().get(f"budgets_{chat_id}", {})


def set_budget(chat_id: int, category: str, amount: float) -> None:
    data    = _load()
    key     = f"budgets_{chat_id}"
    budgets = data.get(key, {})
    budgets[category] = amount
    data[key] = budgets
    _save(data)


def delete_budget(chat_id: int, category: str) -> bool:
    """Removes a budget by exact category match. Returns True if it existed."""
    data    = _load()
    key     = f"budgets_{chat_id}"
    budgets = data.get(key, {})
    if category not in budgets:
        return False
    del budgets[category]
    data[key] = budgets
    _save(data)
    return True


# ── Overall budget (across ALL categories combined) ──────────────────────────
# Stored separately from per-category budgets as: "budget_total_{chat_id}": amount

def get_total_budget(chat_id: int):
    """Returns the overall budget amount for this chat, or None if not set."""
    return _load().get(f"budget_total_{chat_id}")


def set_total_budget(chat_id: int, amount: float) -> None:
    data = _load()
    data[f"budget_total_{chat_id}"] = amount
    _save(data)


def delete_total_budget(chat_id: int) -> bool:
    """Removes the overall budget. Returns True if it existed."""
    data = _load()
    key  = f"budget_total_{chat_id}"
    if key not in data:
        return False
    del data[key]
    _save(data)
    return True


# ── Known chats (so we can broadcast things like "new month started") ────────

def mark_known_chat(chat_id: int) -> None:
    """Records that this chat has used the bot at least once."""
    data = _load()
    known = set(data.get("known_chats", []))
    if chat_id not in known:
        known.add(chat_id)
        data["known_chats"] = list(known)
        _save(data)


def get_known_chats() -> list:
    return _load().get("known_chats", [])