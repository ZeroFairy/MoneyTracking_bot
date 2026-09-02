"""Telegram Expense Tracker Bot."""
import logging
import asyncio
from functools import wraps

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

import config
import sheets
import state
from utils import format_price, parse_price

logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

# ---- Conversation states ----
ADD_PLACE, ADD_ITEM, ADD_PRICE, ADD_PAID_BY, ADD_CONFIRM = range(5)
NEWEVENT_NAME = 5
(
    SPLIT_MODE, SPLIT_PLACE, SPLIT_PAYER, SPLIT_ITEMS, SPLIT_TAX, SPLIT_CONFIRM,
    EVEN_PLACE, EVEN_PAYER, EVEN_NAMES, EVEN_TOTAL, EVEN_LABEL, EVEN_CONFIRM,
) = range(6, 18)
SUMMARY_MODE, SUMMARY_SCOPE, SUMMARY_DATE_INPUT = range(18, 21)

LIST_PAGE_SIZE   = 5
MARKPAID_PAGE_SIZE = 8

HELP_TEXT = (
    "👋 *Expense Tracker Bot*\n\n"
    "⚡ *Quick add:*\n"
    "`/q Place Item Price`\n"
    "e.g. `/q Indomaret Aqua 5000`\n\n"
    "📋 *All commands:*\n"
    "/add — log an expense (step-by-step)\n"
    "/q — quick add a solo expense in one line\n"
    "/split — split a group bill\n"
    "/edit — edit an existing entry\n"
    "/summary — calculate who owes whom\n"
    "/totals — total spending per person\n"
    "/list — browse all entries\n"
    "/delete — delete an entry\n"
    "/markpaid — mark entries paid/unpaid or per-person\n"
    "/getlink — get the spreadsheet link\n"
    "/newevent — create a new sheet for a trip/event\n"
    "/switch — switch between sheets\n"
    "/setmembers — save group names for quick-select\n"
    "/cancel — cancel the current action\n\n"
    "💡 Prices: `25000`, `25.000`, or `25k` all work.\n"
    "💡 `/q` logs you as the payer and sole sharer. Use /add if others are sharing."
)


def active_sheet_name(chat_id: int) -> str:
    name = state.get_active_sheet(chat_id)
    return name if name else sheets.current_month_sheet_name()


def restricted(func):
    @wraps(func)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE, *a, **kw):
        chat_id = update.effective_chat.id
        if config.ALLOWED_CHAT_IDS and chat_id not in config.ALLOWED_CHAT_IDS:
            await update.message.reply_text("🔒 Sorry, this bot is private.")
            return ConversationHandler.END
        return await func(update, context, *a, **kw)
    return wrapper


# ---------------------------------------------------------------------------
# Basic commands
# ---------------------------------------------------------------------------

async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(HELP_TEXT, parse_mode="Markdown")


async def getlink_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    name    = active_sheet_name(chat_id)
    tab_url   = sheets.sheet_url(name)
    sheet_url = sheets.spreadsheet_url()
    await update.message.reply_text(
        f"📊 Active sheet: {name}\n"
        f"{tab_url}\n\n"
        f"Full spreadsheet:\n"
        f"{sheet_url}"
    )


async def cancel_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.chat_data.clear()
    await update.message.reply_text("Cancelled.")
    return ConversationHandler.END


QUICKADD_USAGE = (
    "⚡ *Quick Add usage:*\n"
    "`/q Place Item Price`\n\n"
    "*Examples:*\n"
    "`/q Indomaret Aqua 5000`\n"
    "`/q Alfamart Indomie 3500`\n"
    "`/q Resto Nasi Goreng 25000`\n"
    "`/q Parkir Motor 2k`\n\n"
    "• First word = place\n"
    "• Last word = price\n"
    "• Everything in between = item name\n"
    "• You are set as payer and sole sharer\n"
    "• Use /add instead if others are sharing the cost"
)


async def quickadd_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Strip the command prefix and get the rest
    text = update.message.text.strip()
    raw  = text.split(None, 1)   # split off /q or /quickadd

    if len(raw) < 2 or not raw[1].strip():
        await update.message.reply_text(QUICKADD_USAGE, parse_mode="Markdown")
        return

    args = raw[1].strip().split()
    if len(args) < 3:
        await update.message.reply_text(
            "⚠️ Need at least 3 parts: *Place*, *Item*, *Price*\n\n" + QUICKADD_USAGE,
            parse_mode="Markdown",
        )
        return

    place     = args[0]
    price_raw = args[-1]
    item      = " ".join(args[1:-1])

    try:
        price = parse_price(price_raw)
    except ValueError:
        await update.message.reply_text(
            f"⚠️ Couldn't read `{price_raw}` as a price.\n"
            "Try formats like `5000`, `5.000`, or `5k`.",
            parse_mode="Markdown",
        )
        return

    sender     = update.effective_user.first_name
    chat_id    = update.effective_chat.id
    sheet_name = active_sheet_name(chat_id)
    sheets.append_expense(sheet_name, place, item, price, sender, sender, price)
    await update.message.reply_text(
        f"⚡ *Saved!*\n"
        f"📍 {place} — {item}\n"
        f"💰 {format_price(price)}  |  🙋 {sender}\n"
        f"📋 Sheet: {sheet_name}",
        parse_mode="Markdown",
    )




async def setmembers_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.replace("/setmembers", "").strip()
    if not text:
        await update.message.reply_text(
            "Example: `/setmembers Andi, Budi, Charlie`", parse_mode="Markdown"
        )
        return
    names = [n.strip() for n in text.split(",") if n.strip()]
    state.set_members(update.effective_chat.id, names)
    await update.message.reply_text(f"✅ Saved {len(names)} members: {', '.join(names)}")


# ---------------------------------------------------------------------------
# /edit  — edit a field in an existing entry
# ---------------------------------------------------------------------------

EDIT_PICK, EDIT_FIELD, EDIT_VALUE = range(23, 26)
EDIT_PAGE_SIZE = 8

# Maps field key → (display label, column number, needs price parsing)
EDIT_FIELDS = {
    "place":      ("📍 Place",          2, False),
    "item":       ("📝 Item",           3, False),
    "price":      ("💰 Price",          4, True),
    "paid_by":    ("🙋 Paid By",        5, False),
    "shared":     ("👥 Shared By",      6, False),
    "per_person": ("💵 Amount/Person",  7, True),
}


def _edit_entry_keyboard(all_rows, page):
    total_pages = max(1, -(-len(all_rows) // EDIT_PAGE_SIZE))
    page_rows   = all_rows[page * EDIT_PAGE_SIZE:(page + 1) * EDIT_PAGE_SIZE]
    kb = []
    for row_num, row in page_rows:
        item  = row[2] if len(row) > 2 else "?"
        price = row[3] if len(row) > 3 else "?"
        kb.append([InlineKeyboardButton(
            f"✏️ #{row_num} {item} ({price})",
            callback_data=f"editpick:{row_num}"
        )])
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("◀ Newer", callback_data=f"editpage:{page - 1}"))
    if page < total_pages - 1:
        nav.append(InlineKeyboardButton("Older ▶", callback_data=f"editpage:{page + 1}"))
    if nav:
        kb.append(nav)
    kb.append([InlineKeyboardButton("❌ Cancel", callback_data="editcancel")])
    return InlineKeyboardMarkup(kb), total_pages


def _edit_field_keyboard(row_num, row):
    """Show the current values and let user pick which field to edit."""
    kb = []
    field_values = {
        "place":      row[1] if len(row) > 1 else "?",
        "item":       row[2] if len(row) > 2 else "?",
        "price":      row[3] if len(row) > 3 else "?",
        "paid_by":    row[4] if len(row) > 4 else "?",
        "shared":     row[5] if len(row) > 5 else "?",
        "per_person": row[6] if len(row) > 6 else "?",
    }
    for key, (label, _, _) in EDIT_FIELDS.items():
        current = field_values[key]
        kb.append([InlineKeyboardButton(
            f"{label}: {current}",
            callback_data=f"editfield:{row_num}:{key}"
        )])
    kb.append([InlineKeyboardButton("🔙 Back",    callback_data="editback"),
               InlineKeyboardButton("❌ Cancel",  callback_data="editcancel")])
    return InlineKeyboardMarkup(kb)


async def edit_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id  = update.effective_chat.id
    name     = active_sheet_name(chat_id)
    all_rows = sheets.get_all_expenses(name)
    if not all_rows:
        await update.message.reply_text(f"No entries yet in *{name}*.", parse_mode="Markdown")
        return ConversationHandler.END
    context.chat_data["edit_sheet"] = name
    context.chat_data["edit_rows"]  = all_rows
    context.chat_data["edit_page"]  = 0
    kb, total_pages = _edit_entry_keyboard(all_rows, 0)
    await update.message.reply_text(
        f"✏️ *Edit an entry* — *{name}*\nPage 1/{total_pages}",
        parse_mode="Markdown", reply_markup=kb,
    )
    return EDIT_PICK


async def edit_page(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query    = update.callback_query
    await query.answer()
    page     = int(query.data.split(":")[1])
    all_rows = context.chat_data.get("edit_rows", [])
    name     = context.chat_data.get("edit_sheet", "")
    if not all_rows:
        all_rows = sheets.get_all_expenses(name)
        context.chat_data["edit_rows"] = all_rows
    context.chat_data["edit_page"] = page
    kb, total_pages = _edit_entry_keyboard(all_rows, page)
    await query.edit_message_text(
        f"✏️ *Edit an entry* — *{name}*\nPage {page + 1}/{total_pages}",
        parse_mode="Markdown", reply_markup=kb,
    )
    return EDIT_PICK


async def edit_pick_entry(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """User tapped an entry — show field picker."""
    query   = update.callback_query
    await query.answer()
    row_num = int(query.data.split(":")[1])
    rows    = context.chat_data.get("edit_rows", [])
    chosen  = next((r for rn, r in rows if rn == row_num), None)
    if chosen is None:
        await query.edit_message_text("Entry not found. Try /edit again.")
        return ConversationHandler.END
    context.chat_data["edit_row_num"] = row_num
    context.chat_data["edit_row"]     = chosen
    item  = chosen[2] if len(chosen) > 2 else "?"
    price = chosen[3] if len(chosen) > 3 else "?"
    kb    = _edit_field_keyboard(row_num, chosen)
    await query.edit_message_text(
        f"✏️ *#{row_num} — {item}* ({price})\nTap a field to edit it:",
        parse_mode="Markdown", reply_markup=kb,
    )
    return EDIT_FIELD


async def edit_pick_field(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """User picked a field — ask for the new value."""
    query   = update.callback_query
    await query.answer()
    parts   = query.data.split(":", 2)   # editfield:ROWNUM:FIELDKEY
    row_num = int(parts[1])
    field   = parts[2]
    label, _, needs_price = EDIT_FIELDS[field]
    context.chat_data["edit_field"] = field
    hint = " (e.g. 25000 or 25k)" if needs_price else ""
    row     = context.chat_data.get("edit_row", [])
    field_values = {
        "place": row[1], "item": row[2], "price": row[3],
        "paid_by": row[4], "shared": row[5], "per_person": row[6],
    }
    current = field_values.get(field, "?") if len(row) > 6 else "?"
    await query.edit_message_text(
        f"✏️ Editing *{label}* for entry #{row_num}\n"
        f"Current value: `{current}`\n\n"
        f"Type the new value{hint}:",
        parse_mode="Markdown",
    )
    return EDIT_VALUE


async def edit_save_value(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """User typed a new value — validate and save."""
    raw_value = update.message.text.strip()
    field     = context.chat_data.get("edit_field", "")
    row_num   = context.chat_data.get("edit_row_num")
    name      = context.chat_data.get("edit_sheet", "")
    label, _, needs_price = EDIT_FIELDS[field]

    if needs_price:
        try:
            parsed = parse_price(raw_value)
            save_value = format_price(parsed)
        except ValueError:
            await update.message.reply_text(
                f"⚠️ Couldn't read that as a price. Try `25000`, `25.000`, or `25k`."
            )
            return EDIT_VALUE
    else:
        save_value = raw_value

    sheets.update_expense_field(name, row_num, field, save_value)

    # Clean up
    for k in ("edit_sheet", "edit_rows", "edit_page", "edit_row_num", "edit_row", "edit_field"):
        context.chat_data.pop(k, None)

    await update.message.reply_text(
        f"✅ *Updated!*\n{label} → `{save_value}`\n_(row #{row_num} in {name})_",
        parse_mode="Markdown",
    )
    return ConversationHandler.END


async def edit_back(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Go back to entry list from field picker."""
    query    = update.callback_query
    await query.answer()
    all_rows = context.chat_data.get("edit_rows", [])
    name     = context.chat_data.get("edit_sheet", "")
    page     = context.chat_data.get("edit_page", 0)
    context.chat_data.pop("edit_row_num", None)
    context.chat_data.pop("edit_row",     None)
    context.chat_data.pop("edit_field",   None)
    if not all_rows:
        all_rows = sheets.get_all_expenses(name)
        context.chat_data["edit_rows"] = all_rows
    kb, total_pages = _edit_entry_keyboard(all_rows, page)
    await query.edit_message_text(
        f"✏️ *Edit an entry* — *{name}*\nPage {page + 1}/{total_pages}",
        parse_mode="Markdown", reply_markup=kb,
    )
    return EDIT_PICK


async def edit_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    for k in ("edit_sheet", "edit_rows", "edit_page", "edit_row_num", "edit_row", "edit_field"):
        context.chat_data.pop(k, None)
    await query.edit_message_text("Cancelled.")
    return ConversationHandler.END


# ---------------------------------------------------------------------------
# /list  — paginated
# ---------------------------------------------------------------------------

def _list_row_text(row_num, row):
    date    = row[0] if len(row) > 0 else "?"
    place   = row[1] if len(row) > 1 else "?"
    item    = row[2] if len(row) > 2 else "?"
    price   = row[3] if len(row) > 3 else "?"
    paid_by = row[4] if len(row) > 4 else "?"
    shared  = row[5] if len(row) > 5 else "?"
    per_p   = row[6] if len(row) > 6 else "?"
    status  = row[8] if len(row) > 8 and row[8] else "⏳ Unpaid"
    settled = row[9] if len(row) > 9 and row[9] else ""
    return (
        f"*#{row_num} — {item}*\n"
        f"📍 {place}  |  💰 {price}  |  {status}\n"
        f"🙋 Paid by: {paid_by}\n"
        f"👥 Shared: {shared}  ({per_p}/person)\n"
        + (f"💸 Settled: {settled}\n" if settled else "")
        + f"🕐 {date}"
    )


def _list_keyboard(page, total_pages):
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("◀ Newer", callback_data=f"listpage:{page - 1}"))
    if page < total_pages - 1:
        nav.append(InlineKeyboardButton("Older ▶", callback_data=f"listpage:{page + 1}"))
    return InlineKeyboardMarkup([nav]) if nav else None


async def list_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    name    = active_sheet_name(chat_id)
    all_rows = sheets.get_all_expenses(name)
    if not all_rows:
        await update.message.reply_text(f"No entries yet in *{name}*.", parse_mode="Markdown")
        return
    context.chat_data["list_rows"]  = all_rows
    context.chat_data["list_sheet"] = name
    total_pages = max(1, -(-len(all_rows) // LIST_PAGE_SIZE))
    page_rows = all_rows[0:LIST_PAGE_SIZE]
    lines = [f"🧾 *{name}* — page 1/{total_pages}\n"]
    for row_num, row in page_rows:
        lines.append(_list_row_text(row_num, row))
        lines.append("")
    kb = _list_keyboard(0, total_pages)
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown", reply_markup=kb)


async def list_page_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query    = update.callback_query
    await query.answer()
    page     = int(query.data.split(":")[1])
    all_rows = context.chat_data.get("list_rows")
    name     = context.chat_data.get("list_sheet")
    if not all_rows or not name:
        name     = active_sheet_name(update.effective_chat.id)
        all_rows = sheets.get_all_expenses(name)
        context.chat_data["list_rows"]  = all_rows
        context.chat_data["list_sheet"] = name
    total_pages = max(1, -(-len(all_rows) // LIST_PAGE_SIZE))
    page = max(0, min(page, total_pages - 1))
    page_rows = all_rows[page * LIST_PAGE_SIZE:(page + 1) * LIST_PAGE_SIZE]
    lines = [f"🧾 *{name}* — page {page + 1}/{total_pages}\n"]
    for row_num, row in page_rows:
        lines.append(_list_row_text(row_num, row))
        lines.append("")
    kb = _list_keyboard(page, total_pages)
    await query.edit_message_text("\n".join(lines), parse_mode="Markdown", reply_markup=kb)


# ---------------------------------------------------------------------------
# /delete  — paginated
# ---------------------------------------------------------------------------

DELETE_PAGE_SIZE = 8


def _delete_keyboard(all_rows, page, name):
    total_pages = max(1, -(-len(all_rows) // DELETE_PAGE_SIZE))
    page_rows   = all_rows[page * DELETE_PAGE_SIZE:(page + 1) * DELETE_PAGE_SIZE]
    kb = []
    for row_num, row in page_rows:
        item  = row[2] if len(row) > 2 else "?"
        price = row[3] if len(row) > 3 else "?"
        kb.append([InlineKeyboardButton(f"🗑️ #{row_num} {item} ({price})", callback_data=f"del:{name}:{row_num}")])
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("◀ Newer", callback_data=f"delpage:{page - 1}"))
    if page < total_pages - 1:
        nav.append(InlineKeyboardButton("Older ▶", callback_data=f"delpage:{page + 1}"))
    if nav:
        kb.append(nav)
    kb.append([InlineKeyboardButton("❌ Cancel", callback_data="delcancel")])
    return InlineKeyboardMarkup(kb), total_pages


async def delete_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id  = update.effective_chat.id
    name     = active_sheet_name(chat_id)
    all_rows = sheets.get_all_expenses(name)
    if not all_rows:
        await update.message.reply_text(f"No entries to delete in *{name}*.", parse_mode="Markdown")
        return
    context.chat_data["del_rows"]  = all_rows
    context.chat_data["del_sheet"] = name
    kb, total_pages = _delete_keyboard(all_rows, 0, name)
    await update.message.reply_text(
        f"🗑️ *Delete an entry* — *{name}*\nPage 1/{total_pages}",
        parse_mode="Markdown", reply_markup=kb,
    )


async def delete_page_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query    = update.callback_query
    await query.answer()
    page     = int(query.data.split(":")[1])
    all_rows = context.chat_data.get("del_rows", [])
    name     = context.chat_data.get("del_sheet", "")
    if not all_rows:
        name     = active_sheet_name(update.effective_chat.id)
        all_rows = sheets.get_all_expenses(name)
        context.chat_data["del_rows"]  = all_rows
        context.chat_data["del_sheet"] = name
    kb, total_pages = _delete_keyboard(all_rows, page, name)
    await query.edit_message_text(
        f"🗑️ *Delete an entry* — *{name}*\nPage {page + 1}/{total_pages}",
        parse_mode="Markdown", reply_markup=kb,
    )


async def delete_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    _, name, row_num = query.data.split(":")
    sheets.delete_row(name, int(row_num))
    # Invalidate cached rows since row numbers have shifted
    context.chat_data.pop("del_rows",  None)
    context.chat_data.pop("del_sheet", None)
    await query.edit_message_text(f"🗑️ Deleted row #{row_num} from *{name}*.", parse_mode="Markdown")


async def delete_cancel_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.chat_data.pop("del_rows",  None)
    context.chat_data.pop("del_sheet", None)
    await query.edit_message_text("Cancelled.")


# ---------------------------------------------------------------------------
# /markpaid  — unified flow, with pagination on entry list
#
# Step 1: paginated list of all entries (tap one)
# Step 2a: solo entry  → toggle whole entry paid/unpaid
# Step 2b: split entry → toggle each person individually
# ---------------------------------------------------------------------------

def _is_split(row) -> bool:
    shared = row[5] if len(row) > 5 else ""
    return len([n for n in shared.split(",") if n.strip()]) > 1


def _entry_label(row_num, row):
    item    = row[2] if len(row) > 2 else "?"
    price   = row[3] if len(row) > 3 else "?"
    status  = row[8] if len(row) > 8 and row[8] else "⏳ Unpaid"
    settled = row[9] if len(row) > 9 and row[9] else ""
    s_icon  = "✅" if status == "✅ Paid" else "⏳"
    if _is_split(row):
        names   = [n.strip() for n in row[5].split(",") if n.strip()]
        s_names = [n.strip() for n in settled.split(",") if n.strip()]
        badge   = f" ({len(s_names)}/{len(names)} settled)"
        return f"{s_icon} #{row_num} {item} ({price}){badge} 👥"
    return f"{s_icon} #{row_num} {item} ({price})"


def _markpaid_keyboard(all_rows, page):
    total_pages = max(1, -(-len(all_rows) // MARKPAID_PAGE_SIZE))
    page_rows = all_rows[page * MARKPAID_PAGE_SIZE:(page + 1) * MARKPAID_PAGE_SIZE]
    kb = []
    for row_num, row in page_rows:
        kb.append([InlineKeyboardButton(_entry_label(row_num, row), callback_data=f"mk1:{row_num}")])
    # nav row
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("◀ Newer", callback_data=f"mkpage:{page - 1}"))
    if page < total_pages - 1:
        nav.append(InlineKeyboardButton("Older ▶", callback_data=f"mkpage:{page + 1}"))
    if nav:
        kb.append(nav)
    kb.append([InlineKeyboardButton("❌ Cancel", callback_data="mkcancel")])
    return InlineKeyboardMarkup(kb), total_pages


def _build_solo_keyboard(row_num, status):
    other       = "⏳ Unpaid" if status == "✅ Paid" else "✅ Paid"
    other_emoji = "⏳" if status == "✅ Paid" else "✅"
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(f"Mark as {other_emoji} {other}", callback_data=f"mksolo:{row_num}")],
        [InlineKeyboardButton("🔙 Back", callback_data="mkback"),
         InlineKeyboardButton("❌ Cancel", callback_data="mkcancel")],
    ])


def _build_people_keyboard(row_num, names, settled_set):
    kb = []
    for name in names:
        icon = "✅" if name in settled_set else "⏳"
        kb.append([InlineKeyboardButton(f"{icon} {name}", callback_data=f"mkperson:{row_num}:{name}")])
    kb.append([
        InlineKeyboardButton("💾 Save", callback_data=f"mksave:{row_num}"),
        InlineKeyboardButton("🔙 Back", callback_data="mkback"),
    ])
    kb.append([InlineKeyboardButton("❌ Cancel", callback_data="mkcancel")])
    return InlineKeyboardMarkup(kb)


def _mk_cleanup(context):
    for k in ("mk_sheet", "mk_rows", "mk_page", "mk_names", "mk_settled"):
        context.chat_data.pop(k, None)


async def markpaid_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id  = update.effective_chat.id
    name     = active_sheet_name(chat_id)
    all_rows = sheets.get_all_expenses(name)
    if not all_rows:
        await update.message.reply_text(f"No entries yet in *{name}*.", parse_mode="Markdown")
        return
    context.chat_data["mk_sheet"] = name
    context.chat_data["mk_rows"]  = all_rows
    context.chat_data["mk_page"]  = 0
    kb, total_pages = _markpaid_keyboard(all_rows, 0)
    await update.message.reply_text(
        f"💳 *Mark payment status* — *{name}*\n"
        f"Page 1/{total_pages}  |  👥 = split bill",
        parse_mode="Markdown",
        reply_markup=kb,
    )


async def mk_page(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Navigate pages in the entry list."""
    query = update.callback_query
    await query.answer()
    page     = int(query.data.split(":")[1])
    all_rows = context.chat_data.get("mk_rows", [])
    name     = context.chat_data.get("mk_sheet", "")
    if not all_rows:
        all_rows = sheets.get_all_expenses(name)
        context.chat_data["mk_rows"] = all_rows
    context.chat_data["mk_page"] = page
    kb, total_pages = _markpaid_keyboard(all_rows, page)
    await query.edit_message_text(
        f"💳 *Mark payment status* — *{name}*\n"
        f"Page {page + 1}/{total_pages}  |  👥 = split bill",
        parse_mode="Markdown",
        reply_markup=kb,
    )


async def mk_pick_entry(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query   = update.callback_query
    await query.answer()
    row_num = int(query.data.split(":")[1])
    rows    = context.chat_data.get("mk_rows", [])
    chosen  = next((r for rn, r in rows if rn == row_num), None)
    if chosen is None:
        await query.edit_message_text("Entry not found. Try /markpaid again.")
        return
    item   = chosen[2] if len(chosen) > 2 else "?"
    price  = chosen[3] if len(chosen) > 3 else "?"
    status = chosen[8] if len(chosen) > 8 and chosen[8] else "⏳ Unpaid"

    if _is_split(chosen):
        shared  = chosen[5] if len(chosen) > 5 else ""
        settled = chosen[9] if len(chosen) > 9 and chosen[9] else ""
        names   = [n.strip() for n in shared.split(",") if n.strip()]
        s_names = set(n.strip() for n in settled.split(",") if n.strip())
        context.chat_data["mk_names"]   = names
        context.chat_data["mk_settled"] = s_names
        kb = _build_people_keyboard(row_num, names, s_names)
        s_icon = "✅" if status == "✅ Paid" else "⏳"
        await query.edit_message_text(
            f"{s_icon} *#{row_num} — {item}* ({price})\n"
            f"Tap each person to toggle ✅/⏳, then Save:",
            parse_mode="Markdown", reply_markup=kb,
        )
    else:
        kb = _build_solo_keyboard(row_num, status)
        s_icon = "✅" if status == "✅ Paid" else "⏳"
        await query.edit_message_text(
            f"{s_icon} *#{row_num} — {item}* ({price})\nCurrently: *{status}*",
            parse_mode="Markdown", reply_markup=kb,
        )


async def mk_solo_toggle(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query   = update.callback_query
    await query.answer()
    row_num = int(query.data.split(":")[1])
    name    = context.chat_data.get("mk_sheet", "")
    new_status = sheets.toggle_paid_status(name, row_num)
    emoji = "✅" if new_status == "✅ Paid" else "⏳"
    _mk_cleanup(context)
    await query.edit_message_text(
        f"{emoji} Row *#{row_num}* marked as *{new_status}*.", parse_mode="Markdown"
    )


async def mk_person_toggle(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query  = update.callback_query
    parts  = query.data.split(":", 2)
    row_num = int(parts[1])
    person  = parts[2]
    settled = context.chat_data.get("mk_settled", set())
    if person in settled:
        settled.discard(person)
        await query.answer(f"⏳ {person} — not settled")
    else:
        settled.add(person)
        await query.answer(f"✅ {person} — settled")
    context.chat_data["mk_settled"] = settled
    names = context.chat_data.get("mk_names", [])
    kb    = _build_people_keyboard(row_num, names, settled)
    await query.edit_message_reply_markup(reply_markup=kb)


async def mk_save(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query   = update.callback_query
    await query.answer()
    row_num = int(query.data.split(":")[1])
    name    = context.chat_data.get("mk_sheet", "")
    settled = context.chat_data.get("mk_settled", set())
    names   = context.chat_data.get("mk_names", [])
    ordered   = [n for n in names if n in settled]
    remaining = [n for n in names if n not in settled]
    sheets.set_settled_by(name, row_num, ordered)

    # Auto-sync the Status column based on individual settlements
    if len(ordered) == len(names) and names:
        # Everyone settled → mark whole entry as ✅ Paid
        sheets.set_paid_status_bulk(name, [row_num], "✅ Paid")
    else:
        # Someone still unpaid → make sure Status shows ⏳ Unpaid
        sheets.set_paid_status_bulk(name, [row_num], "⏳ Unpaid")

    if not ordered:
        summary = "No one marked as settled yet."
    elif not remaining:
        summary = "Everyone has settled! 🎉"
    else:
        summary = f"✅ Settled: {', '.join(ordered)}\n⏳ Still owes: {', '.join(remaining)}"
    _mk_cleanup(context)
    await query.edit_message_text(f"💾 *Saved — #{row_num}*\n{summary}", parse_mode="Markdown")


async def mk_back(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    all_rows = context.chat_data.get("mk_rows", [])
    name     = context.chat_data.get("mk_sheet", "")
    page     = context.chat_data.get("mk_page", 0)
    context.chat_data.pop("mk_names",   None)
    context.chat_data.pop("mk_settled", None)
    if not all_rows:
        all_rows = sheets.get_all_expenses(name)
        context.chat_data["mk_rows"] = all_rows
    kb, total_pages = _markpaid_keyboard(all_rows, page)
    await query.edit_message_text(
        f"💳 *Mark payment status* — *{name}*\n"
        f"Page {page + 1}/{total_pages}  |  👥 = split bill",
        parse_mode="Markdown", reply_markup=kb,
    )


async def mk_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    _mk_cleanup(context)
    await query.edit_message_text("Cancelled.")


# ---------------------------------------------------------------------------
# /newevent
# ---------------------------------------------------------------------------

async def newevent_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🎉 What's the name of this event/sheet?")
    return NEWEVENT_NAME


async def newevent_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    name = sheets.sanitize_sheet_name(update.message.text.strip())
    sheets.get_or_create_worksheet(name)
    state.set_active_sheet(update.effective_chat.id, name)
    await update.message.reply_text(
        f"✅ Created sheet *{name}* and switched to it.", parse_mode="Markdown"
    )
    return ConversationHandler.END


# ---------------------------------------------------------------------------
# /switch
# ---------------------------------------------------------------------------

async def switch_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    titles = sheets.list_sheet_titles()
    kb = [[InlineKeyboardButton("🏠 Current Month (auto)", callback_data="switch:__default__")]]
    for t in titles:
        kb.append([InlineKeyboardButton(t, callback_data=f"switch:{t}")])
    await update.message.reply_text("Pick a sheet to switch to:", reply_markup=InlineKeyboardMarkup(kb))


async def switch_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    name  = query.data.split(":", 1)[1]
    chat_id = update.effective_chat.id
    if name == "__default__":
        state.set_active_sheet(chat_id, None)
        await query.edit_message_text(f"✅ Switched to: {sheets.current_month_sheet_name()}")
    else:
        state.set_active_sheet(chat_id, name)
        await query.edit_message_text(f"✅ Switched to: {name}")


# ---------------------------------------------------------------------------
# /summary
# ---------------------------------------------------------------------------

async def summary_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    kb = [
        [InlineKeyboardButton("📊 Smart (Optimized)", callback_data="sum_mode:smart")],
        [InlineKeyboardButton("🧾 Normal (Straight)",  callback_data="sum_mode:raw")],
    ]
    await update.message.reply_text("How do you want to calculate the summary?", reply_markup=InlineKeyboardMarkup(kb))
    return SUMMARY_MODE


async def summary_mode(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    mode  = query.data.split(":", 1)[1]
    context.chat_data["summary_mode"] = mode
    kb = [
        [InlineKeyboardButton("📅 All Dates",   callback_data="sum_scope:all")],
        [InlineKeyboardButton("🗓️ Single Day",  callback_data="sum_scope:day")],
        [InlineKeyboardButton("📆 Date Range",  callback_data="sum_scope:range")],
    ]
    title = "Smart" if mode == "smart" else "Normal"
    await query.edit_message_text(
        f"Selected: *{title}*\n\nWhich timeframe?", parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(kb)
    )
    return SUMMARY_SCOPE


async def summary_scope(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    scope = query.data.split(":", 1)[1]
    context.chat_data["summary_scope"] = scope
    if scope == "all":
        return await execute_summary(update.effective_chat.id, context, query.message, None, None, edit=True)
    elif scope == "day":
        await query.edit_message_text("Type the date (e.g. `20-06-2026`):", parse_mode="Markdown")
    else:
        await query.edit_message_text("Type start and end dates (e.g. `15-06-2026 to 20-06-2026`):", parse_mode="Markdown")
    return SUMMARY_DATE_INPUT


async def summary_date_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text  = update.message.text.strip().lower().replace("to", " ").split()
    scope = context.chat_data.get("summary_scope")
    start_date_str = end_date_str = None
    try:
        from datetime import datetime as dt
        if scope == "day":
            if len(text) != 1:
                await update.message.reply_text("Please provide exactly one date (e.g. `20-06-2026`).")
                return SUMMARY_DATE_INPUT
            start_date_str = end_date_str = text[0]
            dt.strptime(start_date_str, "%d-%m-%Y")
        else:
            if len(text) != 2:
                await update.message.reply_text("Please provide two dates (e.g. `15-06-2026 20-06-2026`).")
                return SUMMARY_DATE_INPUT
            start_date_str, end_date_str = text[0], text[1]
            dt.strptime(start_date_str, "%d-%m-%Y")
            dt.strptime(end_date_str,   "%d-%m-%Y")
    except ValueError:
        await update.message.reply_text("⚠️ Invalid date format. Use DD-MM-YYYY.")
        return SUMMARY_DATE_INPUT
    return await execute_summary(update.effective_chat.id, context, update.message, start_date_str, end_date_str, edit=False)


async def execute_summary(chat_id, context, message_obj, start_date_str, end_date_str, edit=False):
    mode       = context.chat_data.get("summary_mode", "smart")
    sheet_name = active_sheet_name(chat_id)
    date_msg   = "all dates"
    if start_date_str == end_date_str and start_date_str:
        date_msg = start_date_str
    elif start_date_str and end_date_str:
        date_msg = f"{start_date_str} to {end_date_str}"
    calc_text = f"📊 Calculating for *{sheet_name}* ({date_msg})..."
    if edit:
        await message_obj.edit_text(calc_text, parse_mode="Markdown")
    else:
        await message_obj.reply_text(calc_text, parse_mode="Markdown")
    try:
        if mode == "smart":
            txns = sheets.get_settlement_summary(sheet_name, start_date_str, end_date_str)
        else:
            txns = sheets.get_raw_summary(sheet_name, start_date_str, end_date_str)
        if not txns:
            text = "🎉 Everyone is settled up! No one owes anything."
        else:
            title = "🧾 *Smart Settlement*" if mode == "smart" else "🧾 *Straight Settlement*"
            text  = f"{title}\nPeriod: {date_msg}\n\n" + "\n".join(txns)
        await context.bot.send_message(chat_id=chat_id, text=text, parse_mode="Markdown")
    except Exception as e:
        logger.error(f"Summary error: {e}")
        await context.bot.send_message(chat_id=chat_id, text="⚠️ Could not calculate summary.")
    context.chat_data.pop("summary_mode",  None)
    context.chat_data.pop("summary_scope", None)
    return ConversationHandler.END


# ---------------------------------------------------------------------------
# /totals  — total spending per person (ignores paid/unpaid status)
# ---------------------------------------------------------------------------

TOTALS_SCOPE, TOTALS_DATE_INPUT = range(21, 23)


async def totals_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    kb = [
        [InlineKeyboardButton("📅 All Dates",  callback_data="tot_scope:all")],
        [InlineKeyboardButton("🗓️ Single Day", callback_data="tot_scope:day")],
        [InlineKeyboardButton("📆 Date Range", callback_data="tot_scope:range")],
    ]
    await update.message.reply_text(
        "💰 *Total spending per person*\nWhich timeframe?",
        parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(kb),
    )
    return TOTALS_SCOPE


async def totals_scope(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    scope = query.data.split(":", 1)[1]
    context.chat_data["totals_scope"] = scope
    if scope == "all":
        return await execute_totals(update.effective_chat.id, context, query.message, None, None, edit=True)
    elif scope == "day":
        await query.edit_message_text("Type the date (e.g. `20-06-2026`):", parse_mode="Markdown")
    else:
        await query.edit_message_text(
            "Type start and end dates (e.g. `15-06-2026 to 20-06-2026`):", parse_mode="Markdown"
        )
    return TOTALS_DATE_INPUT


async def totals_date_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text  = update.message.text.strip().lower().replace("to", " ").split()
    scope = context.chat_data.get("totals_scope")
    start_date_str = end_date_str = None
    try:
        from datetime import datetime as dt
        if scope == "day":
            if len(text) != 1:
                await update.message.reply_text("Please provide exactly one date (e.g. `20-06-2026`).")
                return TOTALS_DATE_INPUT
            start_date_str = end_date_str = text[0]
            dt.strptime(start_date_str, "%d-%m-%Y")
        else:
            if len(text) != 2:
                await update.message.reply_text("Please provide two dates (e.g. `15-06-2026 20-06-2026`).")
                return TOTALS_DATE_INPUT
            start_date_str, end_date_str = text[0], text[1]
            dt.strptime(start_date_str, "%d-%m-%Y")
            dt.strptime(end_date_str,   "%d-%m-%Y")
    except ValueError:
        await update.message.reply_text("⚠️ Invalid date format. Use DD-MM-YYYY.")
        return TOTALS_DATE_INPUT
    return await execute_totals(update.effective_chat.id, context, update.message, start_date_str, end_date_str, edit=False)


async def execute_totals(chat_id, context, message_obj, start_date_str, end_date_str, edit=False):
    sheet_name = active_sheet_name(chat_id)
    date_msg   = "all dates"
    if start_date_str == end_date_str and start_date_str:
        date_msg = start_date_str
    elif start_date_str and end_date_str:
        date_msg = f"{start_date_str} to {end_date_str}"

    wait_text = f"⏳ Calculating totals for *{sheet_name}* ({date_msg})..."
    if edit:
        await message_obj.edit_text(wait_text, parse_mode="Markdown")
    else:
        await message_obj.reply_text(wait_text, parse_mode="Markdown")

    try:
        totals = sheets.get_person_totals(sheet_name, start_date_str, end_date_str)
        if not totals:
            text = f"No entries found in *{sheet_name}* for {date_msg}."
        else:
            grand_total = sum(totals.values())
            # Sort by amount descending
            sorted_totals = sorted(totals.items(), key=lambda x: x[1], reverse=True)
            lines = [f"💰 *Total spending per person*\n_{sheet_name} — {date_msg}_\n"]
            for i, (person, amt) in enumerate(sorted_totals):
                pct = (amt / grand_total * 100) if grand_total else 0
                lines.append(f"{i + 1}. *{person}*: {format_price(amt)} ({pct:.1f}%)")
            lines.append(f"\n*Grand total: {format_price(grand_total)}*")
            text = "\n".join(lines)
        await context.bot.send_message(chat_id=chat_id, text=text, parse_mode="Markdown")
    except Exception as e:
        logger.error(f"Totals error: {e}")
        await context.bot.send_message(chat_id=chat_id, text="⚠️ Could not calculate totals.")

    context.chat_data.pop("totals_scope", None)
    return ConversationHandler.END


# ---------------------------------------------------------------------------
# /add
# ---------------------------------------------------------------------------

async def add_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.chat_data["draft"] = {}
    await update.message.reply_text("📍 Where did you spend this? (e.g. Indomaret, Resto X)")
    return ADD_PLACE


async def add_place(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.chat_data["draft"]["place"] = update.message.text.strip()
    await update.message.reply_text("📝 What did you buy?")
    return ADD_ITEM


async def add_item(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.chat_data["draft"]["item"] = update.message.text.strip()
    await update.message.reply_text("💰 How much? (e.g. 25000 or 25k)")
    return ADD_PRICE


async def add_price(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        price = parse_price(update.message.text)
    except ValueError:
        await update.message.reply_text("Couldn't read that price, try again e.g. 25000 or 25k")
        return ADD_PRICE
    context.chat_data["draft"]["price"] = price
    sender = update.effective_user.first_name
    kb = [[InlineKeyboardButton(f"Me ({sender})", callback_data=f"paidby:{sender}")]]
    await update.message.reply_text("🙋 Who paid?", reply_markup=InlineKeyboardMarkup(kb))
    return ADD_PAID_BY


async def add_paid_by_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    name  = query.data.split(":", 1)[1]
    return await _finalize_add(update.effective_chat.id, name, context, query.message, edit=True)


async def add_paid_by_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    return await _finalize_add(update.effective_chat.id, update.message.text.strip(), context, update.message, edit=False)


async def _finalize_add(chat_id, payer_name, context, message_obj, edit=False):
    draft = context.chat_data["draft"]
    draft["paid_by"]          = payer_name
    draft["shared_by"]        = [payer_name]
    draft["amount_per_person"] = draft["price"]
    summary = (
        f"📋 *Confirm entry*\n"
        f"Place: {draft['place']}\nItem: {draft['item']}\n"
        f"Price: {format_price(draft['price'])}\nPaid by: {draft['paid_by']}\n\n"
        f"Sheet: {active_sheet_name(chat_id)}"
    )
    kb = [[InlineKeyboardButton("✅ Save", callback_data="add_save"),
           InlineKeyboardButton("❌ Cancel", callback_data="add_cancel")]]
    if edit:
        await message_obj.edit_text(summary, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(kb))
    else:
        await message_obj.reply_text(summary, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(kb))
    return ADD_CONFIRM


async def add_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.data == "add_cancel":
        context.chat_data.pop("draft", None)
        await query.edit_message_text("Cancelled.")
        return ConversationHandler.END
    draft      = context.chat_data.pop("draft")
    chat_id    = update.effective_chat.id
    sheet_name = active_sheet_name(chat_id)
    sheets.append_expense(
        sheet_name, draft["place"], draft["item"], draft["price"],
        draft["paid_by"], ", ".join(draft["shared_by"]), draft["amount_per_person"]
    )
    await query.edit_message_text(f"✅ Saved to *{sheet_name}*!", parse_mode="Markdown")
    return ConversationHandler.END


# ---------------------------------------------------------------------------
# /split
# ---------------------------------------------------------------------------

async def split_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    kb = [
        [InlineKeyboardButton("🧾 Itemized (different orders)", callback_data="split_mode:item")],
        [InlineKeyboardButton("➗ Split Evenly (one total)",    callback_data="split_mode:even")],
    ]
    await update.message.reply_text("How do you want to split this bill?", reply_markup=InlineKeyboardMarkup(kb))
    return SPLIT_MODE


async def split_mode(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    mode  = query.data.split(":", 1)[1]
    if mode == "even":
        context.chat_data["even"] = {}
        await query.edit_message_text("➗ Even split.\n\n📍 Where is this expense from?")
        return EVEN_PLACE
    context.chat_data["split"] = {"items": []}
    await query.edit_message_text("🧾 Itemized split.\n\n📍 Where is this expense from?")
    return SPLIT_PLACE


async def split_place(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.chat_data["split"]["place"] = update.message.text.strip()
    members = state.get_members(update.effective_chat.id)
    text = "Who paid the bill overall?"
    if members:
        kb = []
        row = []
        for m in members:
            row.append(InlineKeyboardButton(m, callback_data=f"payer:{m}"))
            if len(row) == 2:
                kb.append(row); row = []
        if row: kb.append(row)
        kb.append([InlineKeyboardButton("✍️ Type manually...", callback_data="payer:__manual__")])
        await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(kb))
    else:
        await update.message.reply_text(text)
    return SPLIT_PAYER


async def split_payer_btn(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    payer = query.data.split(":", 1)[1]
    if payer == "__manual__":
        await query.edit_message_text("Type the name of who paid:")
        return SPLIT_PAYER
    context.chat_data["split"]["payer"] = payer
    await query.edit_message_text(
        f"Paid by: {payer}\n\nSend each item as:\n`Item | Price | Names`\nExample: `Nasi Goreng | 25000 | Andi, Budi`\n\nType /done when finished.",
        parse_mode="Markdown",
    )
    return SPLIT_ITEMS


async def split_payer(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.chat_data["split"]["payer"] = update.message.text.strip()
    await update.message.reply_text(
        "Send each item as:\n`Item | Price | Names`\nExample: `Nasi Goreng | 25000 | Andi, Budi`\n\nType /done when finished.",
        parse_mode="Markdown",
    )
    return SPLIT_ITEMS


async def split_item_line(update: Update, context: ContextTypes.DEFAULT_TYPE):
    parts = [p.strip() for p in update.message.text.strip().split("|")]
    if len(parts) != 3:
        await update.message.reply_text("Format: `Item | Price | Names`", parse_mode="Markdown")
        return SPLIT_ITEMS
    name, price_raw, names_raw = parts
    try:
        price = parse_price(price_raw)
    except ValueError:
        await update.message.reply_text("Couldn't read that price, try again.")
        return SPLIT_ITEMS
    names = [n.strip() for n in names_raw.split(",") if n.strip()]
    if not names:
        await update.message.reply_text("Please list at least one name.")
        return SPLIT_ITEMS
    context.chat_data["split"]["items"].append({"item": name, "price": price, "names": names})
    await update.message.reply_text(f"Added: {name} ({format_price(price)}) → {', '.join(names)}\nSend next, or /done.")
    return SPLIT_ITEMS


async def split_done(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.chat_data["split"].get("items"):
        await update.message.reply_text("No items yet. Send at least one, or /cancel.")
        return SPLIT_ITEMS
    await update.message.reply_text("Any tax/service charge %? (e.g. 10, or 0 if none)")
    return SPLIT_TAX


async def split_tax(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        tax_pct = float(update.message.text.strip().replace("%", ""))
    except ValueError:
        await update.message.reply_text("Please send a number e.g. 10 or 0.")
        return SPLIT_TAX
    split = context.chat_data["split"]
    split["tax_pct"] = tax_pct
    items = split["items"]
    totals = {}
    lines  = ["🧾 *Itemized Bill Summary*"]
    for it in items:
        share = it["price"] / len(it["names"])
        lines.append(
            f"• {it['item']} — {format_price(it['price'])} ÷ {len(it['names'])} "
            f"({', '.join(it['names'])}) = {format_price(share)}/person"
        )
        for n in it["names"]:
            totals[n] = totals.get(n, 0) + share
    subtotal = sum(it["price"] for it in items)
    lines.append(f"\nSubtotal: {format_price(subtotal)}")
    if tax_pct:
        lines.append(f"Tax/Service: {tax_pct}%")
        for n in totals:
            totals[n] *= 1 + tax_pct / 100
    lines.append(f"\n💰 *Paid by:* {split['payer']}")
    lines.append("*Final amounts owed:*")
    for n, amt in totals.items():
        note = " (payer)" if n.lower() == split["payer"].lower() else ""
        lines.append(f"  {n}: {format_price(amt)}{note}")
    split["totals"] = totals
    kb = [[InlineKeyboardButton("✅ Save to Sheet", callback_data="splitsave"),
           InlineKeyboardButton("❌ Cancel",        callback_data="splitcancel")]]
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(kb))
    return SPLIT_CONFIRM


async def split_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.data == "splitcancel":
        context.chat_data.pop("split", None)
        await query.edit_message_text("Cancelled.")
        return ConversationHandler.END
    split      = context.chat_data.pop("split")
    chat_id    = update.effective_chat.id
    sheet_name = active_sheet_name(chat_id)
    payer      = split["payer"]
    for it in split["items"]:
        share = it["price"] / len(it["names"])
        sheets.append_expense(sheet_name, split["place"], it["item"], it["price"], payer, ", ".join(it["names"]), round(share, 2))
    if split.get("tax_pct"):
        tax_amount = sum(i["price"] for i in split["items"]) * split["tax_pct"] / 100
        all_names  = list(split["totals"].keys())
        sheets.append_expense(sheet_name, split["place"], f"Tax/Service ({split['tax_pct']}%)",
                              round(tax_amount, 2), payer, ", ".join(all_names),
                              round(tax_amount / len(all_names), 2))
    await query.edit_message_text(f"✅ Saved {len(split['items'])} item(s) to *{sheet_name}*!", parse_mode="Markdown")
    return ConversationHandler.END


async def even_place(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.chat_data["even"]["place"] = update.message.text.strip()
    await update.message.reply_text("Who's splitting this? (comma-separated names)")
    return EVEN_NAMES


async def even_names(update: Update, context: ContextTypes.DEFAULT_TYPE):
    names = [n.strip() for n in update.message.text.split(",") if n.strip()]
    if not names:
        await update.message.reply_text("Please give at least one name.")
        return EVEN_NAMES
    context.chat_data["even"]["names"] = names
    members = state.get_members(update.effective_chat.id)
    text = "Who paid the bill?"
    if members:
        kb = []
        row = []
        for m in members:
            row.append(InlineKeyboardButton(m, callback_data=f"evenpayer:{m}"))
            if len(row) == 2:
                kb.append(row); row = []
        if row: kb.append(row)
        kb.append([InlineKeyboardButton("✍️ Type manually...", callback_data="evenpayer:__manual__")])
        await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(kb))
    else:
        await update.message.reply_text(text)
    return EVEN_PAYER


async def even_payer_btn(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    payer = query.data.split(":", 1)[1]
    if payer == "__manual__":
        await query.edit_message_text("Type the name of who paid:")
        return EVEN_PAYER
    context.chat_data["even"]["payer"] = payer
    await query.edit_message_text(f"Paid by: {payer}\n\nWhat's the total amount?")
    return EVEN_TOTAL


async def even_payer(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.chat_data["even"]["payer"] = update.message.text.strip()
    await update.message.reply_text("What's the total amount?")
    return EVEN_TOTAL


async def even_total(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        total = parse_price(update.message.text)
    except ValueError:
        await update.message.reply_text("Couldn't read that amount, try again.")
        return EVEN_TOTAL
    context.chat_data["even"]["total"] = total
    await update.message.reply_text("Give this bill a short name (e.g. 'Dinner at Resto X')")
    return EVEN_LABEL


async def even_label(update: Update, context: ContextTypes.DEFAULT_TYPE):
    even = context.chat_data["even"]
    even["label"]      = update.message.text.strip()
    per_person         = round(even["total"] / len(even["names"]), 2)
    even["per_person"] = per_person
    summary = (
        f"➗ *Even Split Summary*\n{even['label']}\n"
        f"Total: {format_price(even['total'])}\nPaid by: {even['payer']}\n"
        f"Split between: {', '.join(even['names'])}\nEach owes: {format_price(per_person)}"
    )
    kb = [[InlineKeyboardButton("✅ Save to Sheet", callback_data="evensave"),
           InlineKeyboardButton("❌ Cancel",        callback_data="evencancel")]]
    await update.message.reply_text(summary, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(kb))
    return EVEN_CONFIRM


async def even_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.data == "evencancel":
        context.chat_data.pop("even", None)
        await query.edit_message_text("Cancelled.")
        return ConversationHandler.END
    even       = context.chat_data.pop("even")
    chat_id    = update.effective_chat.id
    sheet_name = active_sheet_name(chat_id)
    sheets.append_expense(sheet_name, even["place"], even["label"], even["total"],
                          even["payer"], ", ".join(even["names"]), even["per_person"])
    await query.edit_message_text(f"✅ Saved to *{sheet_name}*!", parse_mode="Markdown")
    return ConversationHandler.END


# ---------------------------------------------------------------------------
# Error handler
# ---------------------------------------------------------------------------

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    logger.error("Exception while handling an update:", exc_info=context.error)
    if isinstance(update, Update) and update.effective_message:
        await update.effective_message.reply_text("⚠️ Something went wrong. Try again or check the log.")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    app = ApplicationBuilder().token(config.TELEGRAM_BOT_TOKEN).build()

    app.add_handler(CommandHandler("start",      restricted(start_cmd)))
    app.add_handler(CommandHandler("help",       restricted(start_cmd)))
    app.add_handler(CommandHandler("getlink",    restricted(getlink_cmd)))
    app.add_handler(CommandHandler("sheet",      restricted(getlink_cmd)))  # keep old name as alias
    app.add_handler(CommandHandler("list",       restricted(list_cmd)))
    app.add_handler(CallbackQueryHandler(list_page_callback, pattern=r"^listpage:"))
    app.add_handler(CommandHandler("delete",     restricted(delete_cmd)))
    app.add_handler(CallbackQueryHandler(delete_page_callback,   pattern=r"^delpage:"))
    app.add_handler(CallbackQueryHandler(delete_callback,        pattern=r"^del:"))
    app.add_handler(CallbackQueryHandler(delete_cancel_callback, pattern=r"^delcancel$"))
    app.add_handler(CommandHandler("q",          restricted(quickadd_cmd)))
    app.add_handler(CommandHandler("quickadd",   restricted(quickadd_cmd)))
    app.add_handler(CommandHandler("markpaid",   restricted(markpaid_cmd)))
    app.add_handler(CallbackQueryHandler(mk_page,         pattern=r"^mkpage:"))
    app.add_handler(CallbackQueryHandler(mk_pick_entry,   pattern=r"^mk1:"))
    app.add_handler(CallbackQueryHandler(mk_solo_toggle,  pattern=r"^mksolo:"))
    app.add_handler(CallbackQueryHandler(mk_person_toggle,pattern=r"^mkperson:"))
    app.add_handler(CallbackQueryHandler(mk_save,         pattern=r"^mksave:"))
    app.add_handler(CallbackQueryHandler(mk_back,         pattern=r"^mkback$"))
    app.add_handler(CallbackQueryHandler(mk_cancel,       pattern=r"^mkcancel$"))
    app.add_handler(CommandHandler("switch",     restricted(switch_cmd)))
    app.add_handler(CallbackQueryHandler(switch_callback, pattern=r"^switch:"))
    app.add_handler(CommandHandler("setmembers", restricted(setmembers_cmd)))

    edit_conv = ConversationHandler(
        entry_points=[CommandHandler("edit", restricted(edit_start))],
        states={
            EDIT_PICK:  [
                CallbackQueryHandler(edit_page,       pattern=r"^editpage:"),
                CallbackQueryHandler(edit_pick_entry, pattern=r"^editpick:"),
                CallbackQueryHandler(edit_cancel,     pattern=r"^editcancel$"),
            ],
            EDIT_FIELD: [
                CallbackQueryHandler(edit_pick_field, pattern=r"^editfield:"),
                CallbackQueryHandler(edit_back,       pattern=r"^editback$"),
                CallbackQueryHandler(edit_cancel,     pattern=r"^editcancel$"),
            ],
            EDIT_VALUE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, edit_save_value),
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel_cmd)],
    )
    app.add_handler(edit_conv)

    add_conv = ConversationHandler(
        entry_points=[CommandHandler("add", restricted(add_start))],
        states={
            ADD_PLACE:    [MessageHandler(filters.TEXT & ~filters.COMMAND, add_place)],
            ADD_ITEM:     [MessageHandler(filters.TEXT & ~filters.COMMAND, add_item)],
            ADD_PRICE:    [MessageHandler(filters.TEXT & ~filters.COMMAND, add_price)],
            ADD_PAID_BY:  [
                CallbackQueryHandler(add_paid_by_button, pattern=r"^paidby:"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, add_paid_by_text),
            ],
            ADD_CONFIRM:  [CallbackQueryHandler(add_confirm, pattern=r"^add_(save|cancel)$")],
        },
        fallbacks=[CommandHandler("cancel", cancel_cmd)],
    )
    app.add_handler(add_conv)

    summary_conv = ConversationHandler(
        entry_points=[CommandHandler("summary", restricted(summary_start))],
        states={
            SUMMARY_MODE:       [CallbackQueryHandler(summary_mode,  pattern=r"^sum_mode:")],
            SUMMARY_SCOPE:      [CallbackQueryHandler(summary_scope, pattern=r"^sum_scope:")],
            SUMMARY_DATE_INPUT: [MessageHandler(filters.TEXT & ~filters.COMMAND, summary_date_input)],
        },
        fallbacks=[CommandHandler("cancel", cancel_cmd)],
    )
    app.add_handler(summary_conv)

    totals_conv = ConversationHandler(
        entry_points=[CommandHandler("totals", restricted(totals_start))],
        states={
            TOTALS_SCOPE:      [CallbackQueryHandler(totals_scope, pattern=r"^tot_scope:")],
            TOTALS_DATE_INPUT: [MessageHandler(filters.TEXT & ~filters.COMMAND, totals_date_input)],
        },
        fallbacks=[CommandHandler("cancel", cancel_cmd)],
    )
    app.add_handler(totals_conv)

    newevent_conv = ConversationHandler(
        entry_points=[CommandHandler("newevent", restricted(newevent_start))],
        states={NEWEVENT_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, newevent_name)]},
        fallbacks=[CommandHandler("cancel", cancel_cmd)],
    )
    app.add_handler(newevent_conv)

    split_conv = ConversationHandler(
        entry_points=[CommandHandler("split", restricted(split_start))],
        states={
            SPLIT_MODE:    [CallbackQueryHandler(split_mode, pattern=r"^split_mode:")],
            SPLIT_PLACE:   [MessageHandler(filters.TEXT & ~filters.COMMAND, split_place)],
            SPLIT_PAYER:   [
                CallbackQueryHandler(split_payer_btn, pattern=r"^payer:"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, split_payer),
            ],
            SPLIT_ITEMS:   [
                CommandHandler("done", split_done),
                MessageHandler(filters.TEXT & ~filters.COMMAND, split_item_line),
            ],
            SPLIT_TAX:     [MessageHandler(filters.TEXT & ~filters.COMMAND, split_tax)],
            SPLIT_CONFIRM: [CallbackQueryHandler(split_confirm, pattern=r"^split(save|cancel)$")],
            EVEN_PLACE:    [MessageHandler(filters.TEXT & ~filters.COMMAND, even_place)],
            EVEN_PAYER:    [
                CallbackQueryHandler(even_payer_btn, pattern=r"^evenpayer:"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, even_payer),
            ],
            EVEN_NAMES:    [MessageHandler(filters.TEXT & ~filters.COMMAND, even_names)],
            EVEN_TOTAL:    [MessageHandler(filters.TEXT & ~filters.COMMAND, even_total)],
            EVEN_LABEL:    [MessageHandler(filters.TEXT & ~filters.COMMAND, even_label)],
            EVEN_CONFIRM:  [CallbackQueryHandler(even_confirm, pattern=r"^even(save|cancel)$")],
        },
        fallbacks=[CommandHandler("cancel", cancel_cmd)],
    )
    app.add_handler(split_conv)

    app.add_error_handler(error_handler)
    logger.info("Bot starting (polling)...")
    app.run_polling()


if __name__ == "__main__":
    main()