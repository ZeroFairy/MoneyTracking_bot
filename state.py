"""State management using Google Sheets internal tabs instead of state.json."""
import json
import gspread
from config import GOOGLE_CREDENTIALS_FILE, GOOGLE_SHEETS_ID

STATE_PREFIX = "BotState_"

_gc = None
_sh = None

def _client():
    global _gc
    if _gc is None:
        _gc = gspread.service_account(filename=GOOGLE_CREDENTIALS_FILE)
    return _gc

def _spreadsheet():
    global _sh
    if _sh is None:
        _sh = _client().open_by_key(GOOGLE_SHEETS_ID)
    return _sh

def _get_or_create_state_sheet(name: str):
    sh = _spreadsheet()
    title = STATE_PREFIX + name
    try:
        ws = sh.worksheet(title)
    except gspread.WorksheetNotFound:
        ws = sh.add_worksheet(title=title, rows=100, cols=2)
        ws.append_row(["Key", "Value"])
    return ws

# ---------------------------------------------------------------------------
# Active Sheet per Chat
# ---------------------------------------------------------------------------
def get_active_sheet(chat_id: int) -> str:
    ws = _get_or_create_state_sheet("ActiveSheets")
    records = ws.get_all_records()
    for r in records:
        if str(r.get("Key")) == str(chat_id):
            return str(r.get("Value", ""))
    return ""

def set_active_sheet(chat_id: int, sheet_name: str):
    ws = _get_or_create_state_sheet("ActiveSheets")
    cell = ws.find(str(chat_id))
    if cell:
        ws.update_cell(cell.row, 2, sheet_name)
    else:
        ws.append_row([str(chat_id), sheet_name])

# ---------------------------------------------------------------------------
# Known Chats (for monthly new-sheet nudge)
# ---------------------------------------------------------------------------
def get_known_chats() -> set:
    ws = _get_or_create_state_sheet("KnownChats")
    records = ws.get_all_records()
    return {int(r["Key"]) for r in records if str(r.get("Key", "")).lstrip("-").isdigit()}

def mark_known_chat(chat_id: int):
    chats = get_known_chats()
    if chat_id not in chats:
        ws = _get_or_create_state_sheet("KnownChats")
        ws.append_row([str(chat_id), "True"])

# ---------------------------------------------------------------------------
# Members per Chat
# ---------------------------------------------------------------------------
def get_members(chat_id: int) -> list:
    ws = _get_or_create_state_sheet("Members")
    records = ws.get_all_records()
    for r in records:
        if str(r.get("Key")) == str(chat_id):
            val = r.get("Value", "[]")
            try:
                return json.loads(val)
            except Exception:
                return []
    return []

def set_members(chat_id: int, members: list):
    ws = _get_or_create_state_sheet("Members")
    val_str = json.dumps(members)
    cell = ws.find(str(chat_id))
    if cell:
        ws.update_cell(cell.row, 2, val_str)
    else:
        ws.append_row([str(chat_id), val_str])

# ---------------------------------------------------------------------------
# Reminders per Chat
# ---------------------------------------------------------------------------
def get_reminders(chat_id: int) -> list:
    ws = _get_or_create_state_sheet("Reminders")
    records = ws.get_all_records()
    for r in records:
        if str(r.get("Key")) == str(chat_id):
            val = r.get("Value", "[]")
            try:
                return json.loads(val)
            except Exception:
                return []
    return []

def get_all_reminders() -> dict:
    """Returns {chat_id: [reminder_dicts, ...]} for all chats."""
    ws = _get_or_create_state_sheet("Reminders")
    records = ws.get_all_records()
    out = {}
    for r in records:
        key_str = str(r.get("Key", ""))
        val_str = r.get("Value", "[]")
        if key_str.lstrip("-").isdigit():
            chat_id = int(key_str)
            try:
                out[chat_id] = json.loads(val_str)
            except Exception:
                out[chat_id] = []
    return out

def add_reminder(chat_id: int, hour: int, minute: int, message: str) -> str:
    import uuid
    reminders = get_reminders(chat_id)
    rid = uuid.uuid4().hex[:8]
    reminders.append({"id": rid, "hour": hour, "minute": minute, "message": message})
    _save_reminders(chat_id, reminders)
    return rid

def delete_reminder(chat_id: int, rid: str):
    reminders = get_reminders(chat_id)
    reminders = [r for r in reminders if r["id"] != rid]
    _save_reminders(chat_id, reminders)

def delete_all_reminders(chat_id: int):
    _save_reminders(chat_id, [])

def _save_reminders(chat_id: int, reminders: list):
    ws = _get_or_create_state_sheet("Reminders")
    val_str = json.dumps(reminders)
    cell = ws.find(str(chat_id))
    if cell:
        ws.update_cell(cell.row, 2, val_str)
    else:
        ws.append_row([str(chat_id), val_str])

# ---------------------------------------------------------------------------
# Budgets & Total Budget per Chat
# ---------------------------------------------------------------------------
def get_budgets(chat_id: int) -> dict:
    ws = _get_or_create_state_sheet("Budgets")
    records = ws.get_all_records()
    for r in records:
        if str(r.get("Key")) == str(chat_id):
            try:
                return json.loads(r.get("Value", "{}"))
            except Exception:
                return {}
    return {}

def set_budget(chat_id: int, category: str, amount: float):
    budgets = get_budgets(chat_id)
    budgets[category] = amount
    _save_budgets(chat_id, budgets)

def delete_budget(chat_id: int, category: str) -> bool:
    budgets = get_budgets(chat_id)
    if category in budgets:
        del budgets[category]
        _save_budgets(chat_id, budgets)
        return True
    return False

def _save_budgets(chat_id: int, budgets: dict):
    ws = _get_or_create_state_sheet("Budgets")
    val_str = json.dumps(budgets)
    cell = ws.find(str(chat_id))
    if cell:
        ws.update_cell(cell.row, 2, val_str)
    else:
        ws.append_row([str(chat_id), val_str])

def get_total_budget(chat_id: int) -> float:
    ws = _get_or_create_state_sheet("TotalBudgets")
    records = ws.get_all_records()
    for r in records:
        if str(r.get("Key")) == str(chat_id):
            try:
                return float(r.get("Value", 0))
            except Exception:
                return 0.0
    return 0.0

def set_total_budget(chat_id: int, amount: float):
    ws = _get_or_create_state_sheet("TotalBudgets")
    cell = ws.find(str(chat_id))
    if cell:
        ws.update_cell(cell.row, 2, str(amount))
    else:
        ws.append_row([str(chat_id), str(amount)])

def delete_total_budget(chat_id: int) -> bool:
    ws = _get_or_create_state_sheet("TotalBudgets")
    cell = ws.find(str(chat_id))
    if cell:
        ws.update_cell(cell.row, 2, "0")
        return True
    return False