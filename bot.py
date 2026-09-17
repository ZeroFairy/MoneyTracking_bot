"""Telegram Expense Tracker Bot."""
import logging
import asyncio
from functools import wraps

from telegram import BotCommand, InlineKeyboardButton, InlineKeyboardMarkup, Update
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
from utils import format_price, parse_price, parse_date_input, now_wib

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

ADD_CATEGORY   = 26
SPLIT_CATEGORY = 27
EVEN_CATEGORY  = 28
OVERVIEW_SCOPE, OVERVIEW_DATE_INPUT = range(29, 31)
ADD_DATE = 31

LIST_PAGE_SIZE    = 5
MARKPAID_PAGE_SIZE = 8

PRESET_CATEGORIES = [
    ("🍔 Food & Drink",     "Food & Drink"),
    ("🚗 Transport",         "Transport"),
    ("🛍️ Shopping",          "Shopping"),
    ("🏠 Household",         "Household"),
    ("💊 Health",            "Health"),
    ("🎮 Entertainment",     "Entertainment"),
    ("📱 Bills & Utilities", "Bills & Utilities"),
    ("📦 Other",             "Other"),
]

# Short-tag aliases so /q's #tag lines up with the exact preset names used by
# /add's category buttons (matters for /overview and /budgets, which group
# and match categories by exact, case-insensitive name — "Food Drink" typed
# via /q would otherwise show up as a separate category from "Food & Drink"
# picked via /add's buttons).
_CATEGORY_ALIASES = {
    "food":             "Food & Drink",
    "fooddrink":        "Food & Drink",
    "drink":            "Food & Drink",
    "transport":        "Transport",
    "transportation":   "Transport",
    "shopping":         "Shopping",
    "household":        "Household",
    "health":           "Health",
    "entertainment":    "Entertainment",
    "bills":            "Bills & Utilities",
    "billsutilities":   "Bills & Utilities",
    "utilities":        "Bills & Utilities",
    "other":            "Other",
}


def _normalize_category(raw: str) -> str:
    """Snaps a typed category (e.g. /q's #tag, or a typed custom category) to
    the exact preset name if it's a recognizable match, so it stays consistent
    with categories picked via /add's buttons. Falls back to the text as
    typed (with underscores turned into spaces) if nothing matches.
    """
    if not raw:
        return raw
    key = raw.lower().replace("_", "").replace("&", "").replace(" ", "").replace("-", "")
    return _CATEGORY_ALIASES.get(key, raw.replace("_", " ").strip())


def _category_keyboard(callback_prefix: str = "cat"):
    """Inline keyboard with preset category buttons + custom option."""
    kb = []
    row = []
    for label, value in PRESET_CATEGORIES:
        row.append(InlineKeyboardButton(label, callback_data=f"{callback_prefix}:{value}"))
        if len(row) == 2:
            kb.append(row)
            row = []
    if row:
        kb.append(row)
    kb.append([InlineKeyboardButton("✍️ Type a custom category...", callback_data=f"{callback_prefix}:__custom__")])
    return InlineKeyboardMarkup(kb)

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
    "/overview — spending breakdown by category\n"
    "/list — browse all entries\n"
    "/delete — delete an entry\n"
    "/undo — quickly delete the most recent entry\n"
    "/markpaid — mark entries paid/unpaid or per-person\n"
    "/getlink — get the spreadsheet link\n"
    "/recap — quick snapshot: entries, grand total, biggest expense, top spender\n"
    "/setbudget Category Amount — set a monthly cap per category, with 80%/100% alerts\n"
    "/budgets — see all budgets and current usage\n"
    "/sortsheet — re-sort the active sheet by date & time\n"
    "/newevent — create a new sheet for a trip/event\n"
    "/switch — switch between sheets\n"
    "/setmembers — save group names for quick-select\n"
    "/setreminder HH:MM Message — add a daily reminder at that time\n"
    "/listreminders — show all your reminders\n"
    "/stopreminder — remove a reminder\n"
    "/cancel — cancel the current action\n\n"
    "💡 Prices: `25000`, `25.000`, or `25k` all work.\n"
    "💡 `/q` logs you as the payer and sole sharer. Use /add if others are sharing."
)


def active_sheet_name(chat_id: int) -> str:
    """The chat's active sheet. No longer silently follows the calendar —
    once set, it stays fixed until the user explicitly runs /switch, even
    after a new month starts. First-ever use for a chat is bootstrapped to
    the current month, saved explicitly right away.
    """
    name = state.get_active_sheet(chat_id)
    if name:
        return name
    name = sheets.current_month_sheet_name()
    state.set_active_sheet(chat_id, name)
    return name


def _disp(row_num: int) -> int:
    """Convert an actual sheet row number (row 1 = header, row 2 = first entry)
    into a friendly 1-based entry number shown to the user."""
    return row_num - 1


_known_chats_cache = set()  # avoids a state.json read on every command once a chat is seen


def restricted(func):
    @wraps(func)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE, *a, **kw):
        chat_id = update.effective_chat.id
        if config.ALLOWED_CHAT_IDS and chat_id not in config.ALLOWED_CHAT_IDS:
            await update.message.reply_text("🔒 Sorry, this bot is private.")
            return ConversationHandler.END
        if chat_id not in _known_chats_cache:
            state.mark_known_chat(chat_id)  # so the monthly new-sheet nudge knows who to message
            _known_chats_cache.add(chat_id)
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


async def recap_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Quick snapshot of the whole active sheet — total entries, grand total,
    biggest single expense, top category, and biggest spender. No date
    filter and no per-category breakdown — that's what /overview is for.
    """
    chat_id = update.effective_chat.id
    name    = active_sheet_name(chat_id)
    data    = sheets.get_recap(name)

    if data["count"] == 0:
        await update.message.reply_text(f"No entries yet in *{name}*.", parse_mode="Markdown")
        return

    lines = [f"📊 *Recap — {name}*", ""]
    lines.append(f"🧾 Entries: *{data['count']}*")
    lines.append(f"💰 Grand total: *{format_price(data['grand_total'])}*")

    if data["biggest_expense"]:
        price, place, item = data["biggest_expense"]
        lines.append(f"🔥 Biggest expense: *{format_price(price)}* — {place} ({item})")

    if data["top_category"]:
        cat, total = data["top_category"]
        lines.append(f"🏷️ Top category: *{cat}* ({format_price(total)})")

    if data["top_spender"]:
        person, total = data["top_spender"]
        lines.append(f"🙋 Biggest spender: *{person}* ({format_price(total)})")

    lines.append("\n_Want the full category breakdown? Use /overview._")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def sortsheet_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Re-sorts the active sheet's rows chronologically by Date & Time.

    Fixes ordering when backdated entries (via /add, /q @date, or /edit) end
    up out of sequence, since new rows are always appended to the bottom
    regardless of their date.
    """
    chat_id = update.effective_chat.id
    name    = active_sheet_name(chat_id)
    await update.message.reply_text(f"🔄 Sorting *{name}* by date & time…", parse_mode="Markdown")
    try:
        count = sheets.sort_by_date(name)
    except Exception:
        logger.exception("sortsheet failed")
        await update.message.reply_text("❌ Something went wrong while sorting. Please try again.")
        return
    if count == 0:
        await update.message.reply_text("Nothing to sort — the sheet is empty.")
        return
    await update.message.reply_text(
        f"✅ Sorted *{count}* {'entry' if count == 1 else 'entries'} in *{name}* "
        f"chronologically (oldest → newest).\n\n"
        f"⚠️ Row numbers may have shifted — re-check /list, /edit, /delete or /markpaid "
        f"before using an entry number you noted earlier.",
        parse_mode="Markdown",
    )


async def cancel_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.chat_data.clear()
    await update.message.reply_text("Cancelled.")
    return ConversationHandler.END


_QUICKADD_CATEGORY_LINE = "• Categories: " + ", ".join(
    f"#{value.lower().replace(' & ', '').replace(' ', '')}" for _, value in PRESET_CATEGORIES
) + " (or any custom word)\n"

QUICKADD_USAGE = (
    "⚡ *Quick Add usage:*\n"
    "`/q Place Item Price`\n"
    "`/q Place Item Price #category`\n"
    "`/q Place Item Price #category @date`\n"
    "`/q Place ; Item ; Price` _(for multi-word place names)_\n\n"
    "*Examples:*\n"
    "`/q Indomaret Aqua 5000`\n"
    "`/q Alfamart Indomie 3500 #food`\n"
    "`/q Grab Motor 15000 #transport`\n"
    "`/q Parkir Motor 2k`\n"
    "`/q Indomaret Aqua 5000 @yesterday`\n"
    "`/q Cafe Kopi 20000 #food @25-06-2026`\n"
    "`/q Cafe Kopi 20000 @14:30` _(today, at 14:30)_\n"
    "`/q Cafe Kopi 20000 @25-06-2026 @14:30` _(specific date AND time)_\n"
    "`/q Warung Nasi Padang ; Ayam Bakar Sambal ; 25000` _(multi-word place & item)_\n\n"
    "• First word = place, last word = price, everything in between = item\n"
    "• Place or item has more than one word? Use `Place ; Item ; Price` instead\n"
    "• Add `#tag` at the end to set a category (optional)\n"
    + _QUICKADD_CATEGORY_LINE +
    "  _(these line up with the same categories /add uses, so /overview and /budgets stay consistent)_\n"
    "• Add `@date` at the end for a backdated entry — `@today`, `@yesterday`, "
    "`@DD-MM-YYYY`, or just `@HH:MM` for today at that time (optional, defaults to right now)\n"
    "• Need both a specific date AND time? Add both as separate tags, date first: "
    "`@25-06-2026 @14:30`\n"
    "• You are set as payer and sole sharer\n"
    "• Use /add instead if others are sharing the cost"
)


async def quickadd_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    raw  = text.split(None, 1)

    if len(raw) < 2 or not raw[1].strip():
        await update.message.reply_text(QUICKADD_USAGE, parse_mode="Markdown")
        return

    tokens = raw[1].strip().split()

    # Peel off ALL trailing @tags — supports one combined tag (@yesterday,
    # @14:30, @25-06-2026) as well as a separate date + time given as two
    # tags (@25-06-2026 @14:30), which are joined back into "25-06-2026 14:30"
    # before parsing.
    date_str = None
    date_tags = []
    while tokens and tokens[-1].startswith("@"):
        date_tags.insert(0, tokens.pop()[1:].strip())
    if date_tags:
        date_raw = " ".join(date_tags)
        try:
            date_str = parse_date_input(date_raw)
        except ValueError:
            await update.message.reply_text(
                f"⚠️ Couldn't read `{date_raw}` as a date/time.\n"
                "Try `today`, `yesterday`, `25-06-2026`, `25-06-2026 14:30`, or just a time like `14:30`.",
                parse_mode="Markdown",
            )
            return
        if not tokens:
            await update.message.reply_text(
                "⚠️ Need at least *Place*, *Item*, *Price* before the @tag\n\n" + QUICKADD_USAGE,
                parse_mode="Markdown",
            )
            return

    # Then a trailing #category tag.
    category = ""
    if tokens[-1].startswith("#"):
        category = _normalize_category(tokens[-1][1:].strip())
        tokens = tokens[:-1]
        if not tokens:
            await update.message.reply_text(
                "⚠️ Need at least *Place*, *Item*, *Price* before the #tag\n\n" + QUICKADD_USAGE,
                parse_mode="Markdown",
            )
            return

    remainder = " ".join(tokens)

    # Semicolon format handles multi-word PLACE and/or multi-word ITEM unambiguously:
    # "/q Warung Nasi Padang ; Ayam Bakar Sambal ; 25000"
    if ";" in remainder:
        parts = [p.strip() for p in remainder.split(";")]
        if len(parts) != 3 or not all(parts):
            await update.message.reply_text(
                "⚠️ Semicolon format needs exactly 3 non-empty parts: `Place ; Item ; Price`\n\n"
                + QUICKADD_USAGE,
                parse_mode="Markdown",
            )
            return
        place, item, price_raw = parts
    else:
        args = remainder.split()
        if len(args) < 3:
            await update.message.reply_text(
                "⚠️ Need at least 3 parts: *Place*, *Item*, *Price*\n"
                "Multi-word place name? Use `Place ; Item ; Price` instead.\n\n" + QUICKADD_USAGE,
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
    sheets.append_expense(sheet_name, place, item, price, sender, sender, price, status="✅ Paid", category=category, date_str=date_str)
    cat_line  = f"  🏷️ {category}" if category else ""
    date_line = f"\n📅 {date_str}" if date_str else ""
    warning = _budget_warnings(chat_id, sheet_name, category)
    await update.message.reply_text(
        f"⚡ *Saved!*\n"
        f"📍 {place} — {item}\n"
        f"💰 {format_price(price)}  |  🙋 {sender}{cat_line}\n"
        f"📋 Sheet: {sheet_name}{date_line}"
        f"{warning}",
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
    "date":       ("📅 Date",           1, False),
    "place":      ("📍 Place",          2, False),
    "item":       ("📝 Item",           3, False),
    "price":      ("💰 Price",          4, True),
    "paid_by":    ("🙋 Paid By",        5, False),
    "shared":     ("👥 Shared By",      6, False),
    "per_person": ("💵 Amount/Person",  7, True),
    "category":   ("🏷️ Category",      11, False),
}


def _edit_entry_keyboard(all_rows, page):
    total_pages = max(1, -(-len(all_rows) // EDIT_PAGE_SIZE))
    page_rows   = all_rows[page * EDIT_PAGE_SIZE:(page + 1) * EDIT_PAGE_SIZE]
    kb = []
    for row_num, row in page_rows:
        item  = row[2] if len(row) > 2 else "?"
        price = row[3] if len(row) > 3 else "?"
        kb.append([InlineKeyboardButton(
            f"✏️ #{_disp(row_num)} {item} ({price})",
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
        "date":       row[0]  if len(row) > 0  else "?",
        "place":      row[1]  if len(row) > 1  else "?",
        "item":       row[2]  if len(row) > 2  else "?",
        "price":      row[3]  if len(row) > 3  else "?",
        "paid_by":    row[4]  if len(row) > 4  else "?",
        "shared":     row[5]  if len(row) > 5  else "?",
        "per_person": row[6]  if len(row) > 6  else "?",
        "category":   row[10] if len(row) > 10 else "",
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
        f"✏️ *#{_disp(row_num)} — {item}* ({price})\nTap a field to edit it:",
        parse_mode="Markdown", reply_markup=kb,
    )
    return EDIT_FIELD


EDIT_HINTS = {
    "date":       " (e.g. `25-06-2026`, `25-06-2026 14:30`, `today`, `yesterday`)",
    "place":      " (e.g. `Indomaret`)",
    "item":       " (e.g. `Aqua Botol`)",
    "price":      " (e.g. `25000`, `25.000`, or `25k`)",
    "paid_by":    " (a name, e.g. `Andi`)",
    "shared":     " (comma-separated names, e.g. `Andi, Budi`)",
    "per_person": " (e.g. `12500` or `12.5k`)",
    "category":   "",  # category uses button picker instead of a typed hint
}


async def edit_pick_field(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """User picked a field — ask for the new value (or, for category, show a picker)."""
    query   = update.callback_query
    await query.answer()
    parts   = query.data.split(":", 2)   # editfield:ROWNUM:FIELDKEY
    row_num = int(parts[1])
    field   = parts[2]
    label, _, needs_price = EDIT_FIELDS[field]
    context.chat_data["edit_field"] = field

    row     = context.chat_data.get("edit_row", [])
    field_values = {
        "date":       row[0]  if len(row) > 0  else "?",
        "place":      row[1]  if len(row) > 1  else "?",
        "item":       row[2]  if len(row) > 2  else "?",
        "price":      row[3]  if len(row) > 3  else "?",
        "paid_by":    row[4]  if len(row) > 4  else "?",
        "shared":     row[5]  if len(row) > 5  else "?",
        "per_person": row[6]  if len(row) > 6  else "?",
        "category":   row[10] if len(row) > 10 else "",
    }
    current = field_values.get(field, "?")

    if field == "category":
        # Show the same preset buttons /add uses, instead of asking to type
        # free text — avoids typos and category-name mismatches with /overview
        # and /budgets, which match category names exactly (case-insensitive).
        await query.edit_message_text(
            f"✏️ Editing *{label}* for entry #{_disp(row_num)}\n"
            f"Current value: `{current or '(none)'}`\n\n"
            f"Pick a category, or type your own:",
            parse_mode="Markdown",
            reply_markup=_category_keyboard("editcat"),
        )
        return EDIT_VALUE

    hint = EDIT_HINTS.get(field, "")
    await query.edit_message_text(
        f"✏️ Editing *{label}* for entry #{_disp(row_num)}\n"
        f"Current value: `{current}`\n\n"
        f"Type the new value{hint}:",
        parse_mode="Markdown",
    )
    return EDIT_VALUE


async def edit_category_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles a preset category button tap while editing the category field."""
    query = update.callback_query
    await query.answer()
    value = query.data.split(":", 1)[1]
    if value == "__custom__":
        await query.edit_message_text("✍️ Type your custom category:")
        return EDIT_VALUE

    async def _send(text, **kw):
        await query.edit_message_text(text, **kw)

    return await _finish_edit(context, _send, value)


async def _finish_edit(context: ContextTypes.DEFAULT_TYPE, send, save_value: str):
    """Shared save + cleanup for /edit, used by both the typed-value path and
    the category button path. `send(text, **kwargs)` delivers the result back
    to the user regardless of which path triggered it. Wrapped in try/except
    so an unexpected error can't strand the /edit conversation the way an
    unhandled exception here previously did (e.g. a field with no column
    mapping in sheets.py).
    """
    field   = context.chat_data.get("edit_field", "")
    row_num = context.chat_data.get("edit_row_num")
    name    = context.chat_data.get("edit_sheet", "")
    label   = EDIT_FIELDS.get(field, ("that field",))[0]

    for k in ("edit_sheet", "edit_rows", "edit_page", "edit_row_num", "edit_row", "edit_field"):
        context.chat_data.pop(k, None)

    try:
        sheets.update_expense_field(name, row_num, field, save_value)
    except Exception:
        logger.exception(f"Failed to save /edit for field={field!r}")
        await send("❌ Something went wrong saving that — please run /edit again.")
        return ConversationHandler.END

    await send(
        f"✅ *Updated!*\n{label} → `{save_value}`\n_(entry #{_disp(row_num)} in {name})_",
        parse_mode="Markdown",
    )
    return ConversationHandler.END


async def edit_save_value(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """User typed a new value — validate and save."""
    raw_value = update.message.text.strip()
    field     = context.chat_data.get("edit_field", "")
    label, _, needs_price = EDIT_FIELDS[field]

    if needs_price:
        try:
            parsed = parse_price(raw_value)
            save_value = format_price(parsed)
        except ValueError:
            await update.message.reply_text(
                "⚠️ Couldn't read that as a price. Try `25000`, `25.000`, or `25k`."
            )
            return EDIT_VALUE
    elif field == "date":
        try:
            save_value = parse_date_input(raw_value)
        except ValueError:
            await update.message.reply_text(
                "⚠️ Couldn't read that as a date. Try `25-06-2026`, `25-06-2026 14:30`, `today`, or `yesterday`."
            )
            return EDIT_VALUE
    else:
        save_value = raw_value

    async def _send(text, **kw):
        await update.message.reply_text(text, **kw)

    return await _finish_edit(context, _send, save_value)


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
        f"*#{_disp(row_num)} — {item}*\n"
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


def _delete_keyboard(all_rows, page):
    total_pages = max(1, -(-len(all_rows) // DELETE_PAGE_SIZE))
    page_rows   = all_rows[page * DELETE_PAGE_SIZE:(page + 1) * DELETE_PAGE_SIZE]
    kb = []
    for row_num, row in page_rows:
        item  = row[2] if len(row) > 2 else "?"
        price = row[3] if len(row) > 3 else "?"
        # Only the row number goes in callback_data — the sheet name is read
        # back from chat_data. Telegram caps callback_data at 64 bytes, and
        # custom sheet names (via /newevent) can run up to 90 chars, so
        # embedding the name directly here would crash this keyboard entirely
        # for any chat using a long custom sheet name.
        kb.append([InlineKeyboardButton(f"🗑️ #{_disp(row_num)} {item} ({price})", callback_data=f"del:{row_num}")])
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
    kb, total_pages = _delete_keyboard(all_rows, 0)
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
    kb, total_pages = _delete_keyboard(all_rows, page)
    await query.edit_message_text(
        f"🗑️ *Delete an entry* — *{name}*\nPage {page + 1}/{total_pages}",
        parse_mode="Markdown", reply_markup=kb,
    )


async def delete_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    row_num = int(query.data.split(":")[1])
    name    = context.chat_data.get("del_sheet") or active_sheet_name(update.effective_chat.id)
    sheets.delete_row(name, row_num)
    # Invalidate cached rows since row numbers have shifted
    context.chat_data.pop("del_rows",  None)
    context.chat_data.pop("del_sheet", None)
    await query.edit_message_text(f"🗑️ Deleted entry #{_disp(row_num)} from *{name}*.", parse_mode="Markdown")


async def delete_cancel_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.chat_data.pop("del_rows",  None)
    context.chat_data.pop("del_sheet", None)
    await query.edit_message_text("Cancelled.")


# ---------------------------------------------------------------------------
# /undo — quick-remove the single most recently added entry, no pagination
# ---------------------------------------------------------------------------

async def undo_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id  = update.effective_chat.id
    name     = active_sheet_name(chat_id)
    all_rows = sheets.get_all_expenses(name)  # newest (last appended) first
    if not all_rows:
        await update.message.reply_text(f"Nothing to undo — *{name}* is empty.", parse_mode="Markdown")
        return

    row_num, row = all_rows[0]
    date  = row[0] if len(row) > 0 else "?"
    place = row[1] if len(row) > 1 else "?"
    item  = row[2] if len(row) > 2 else "?"
    price = row[3] if len(row) > 3 else "?"

    # Only the row number goes in callback_data — the sheet name is read back
    # from chat_data. Telegram caps callback_data at 64 bytes, and custom
    # sheet names (via /newevent) can run up to 90 chars, so embedding the
    # name directly here would crash this button for a long custom sheet name.
    context.chat_data["undo_sheet"] = name
    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("🗑️ Yes, undo it", callback_data=f"undo:{row_num}"),
        InlineKeyboardButton("Cancel", callback_data="undocancel"),
    ]])
    await update.message.reply_text(
        f"↩️ *Undo the last entry?*\n\n"
        f"📅 {date}\n📍 {place} — {item}\n💰 {price}\n\n"
        f"This removes it from *{name}*.",
        parse_mode="Markdown", reply_markup=keyboard,
    )


async def undo_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query   = update.callback_query
    await query.answer()
    row_num = int(query.data.split(":")[1])
    name    = context.chat_data.get("undo_sheet") or active_sheet_name(update.effective_chat.id)

    # Re-check it's still the most recent row before deleting — something else
    # may have been added or removed since the confirmation prompt was sent.
    all_rows = sheets.get_all_expenses(name)
    if not all_rows or all_rows[0][0] != row_num:
        await query.edit_message_text(
            "⚠️ The sheet changed since this prompt — nothing was deleted.\n"
            "Use /list or /delete to remove a specific entry instead."
        )
        return

    sheets.delete_row(name, row_num)
    context.chat_data.pop("undo_sheet", None)
    await query.edit_message_text(f"↩️ Undone — removed the last entry from *{name}*.", parse_mode="Markdown")


async def undo_cancel_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.chat_data.pop("undo_sheet", None)
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
        return f"{s_icon} #{_disp(row_num)} {item} ({price}){badge} 👥"
    return f"{s_icon} #{_disp(row_num)} {item} ({price})"


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
    # Index into names, not the name itself — same 64-byte callback_data
    # concern as the /split and /add payer buttons.
    for i, name in enumerate(names):
        icon = "✅" if name in settled_set else "⏳"
        kb.append([InlineKeyboardButton(f"{icon} {name}", callback_data=f"mkperson:{row_num}:{i}")])
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
            f"{s_icon} *#{_disp(row_num)} — {item}* ({price})\n"
            f"Tap each person to toggle ✅/⏳, then Save:",
            parse_mode="Markdown", reply_markup=kb,
        )
    else:
        kb = _build_solo_keyboard(row_num, status)
        s_icon = "✅" if status == "✅ Paid" else "⏳"
        await query.edit_message_text(
            f"{s_icon} *#{_disp(row_num)} — {item}* ({price})\nCurrently: *{status}*",
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
        f"{emoji} Entry *#{_disp(row_num)}* marked as *{new_status}*.", parse_mode="Markdown"
    )


async def mk_person_toggle(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query   = update.callback_query
    parts   = query.data.split(":", 2)
    row_num = int(parts[1])
    names   = context.chat_data.get("mk_names", [])
    try:
        person = names[int(parts[2])]
    except (ValueError, IndexError):
        await query.answer("⚠️ That option expired — please run /markpaid again.")
        return
    settled = context.chat_data.get("mk_settled", set())
    if person in settled:
        settled.discard(person)
        await query.answer(f"⏳ {person} — not settled")
    else:
        settled.add(person)
        await query.answer(f"✅ {person} — settled")
    context.chat_data["mk_settled"] = settled
    kb = _build_people_keyboard(row_num, names, settled)
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
    await query.edit_message_text(f"💾 *Saved — #{_disp(row_num)}*\n{summary}", parse_mode="Markdown")


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
    context.chat_data["switch_titles"] = titles
    # Only an index goes in callback_data — the sheet name is read back from
    # chat_data. Telegram caps callback_data at 64 bytes, and custom sheet
    # names (via /newevent) can run up to 90 chars, so embedding the name
    # directly here would crash this keyboard for a long custom sheet name.
    this_month = sheets.current_month_sheet_name()
    kb = [[InlineKeyboardButton(f"📅 This month ({this_month})", callback_data="switch:thismonth")]]
    for i, t in enumerate(titles):
        kb.append([InlineKeyboardButton(t, callback_data=f"switch:{i}")])
    await update.message.reply_text("Pick a sheet to switch to:", reply_markup=InlineKeyboardMarkup(kb))


async def switch_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query   = update.callback_query
    await query.answer()
    chat_id = update.effective_chat.id
    token   = query.data.split(":", 1)[1]

    if token == "thismonth":
        # Explicit switch to whichever month it is right now — this is a
        # one-time snapshot, not an ongoing "auto-follow the calendar"
        # setting. It'll stay pinned here until you /switch again, even
        # once next month starts.
        name = sheets.current_month_sheet_name()
        state.set_active_sheet(chat_id, name)
        await query.edit_message_text(f"✅ Switched to: {name}")
        return

    titles = context.chat_data.get("switch_titles")
    if titles is None:
        titles = sheets.list_sheet_titles()  # fallback if chat_data was lost (e.g. bot restart)

    try:
        name = titles[int(token)]
    except (ValueError, IndexError):
        await query.edit_message_text("⚠️ That option expired — please run /switch again.")
        return

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
    # A fixed short token, not the name itself — Telegram first names can run
    # up to 64 chars, which combined with a prefix risks the 64-byte
    # callback_data cap. The actual name is re-read from the click itself.
    kb = [[InlineKeyboardButton(f"Me ({sender})", callback_data="paidby:me")]]
    await update.message.reply_text("🙋 Who paid?", reply_markup=InlineKeyboardMarkup(kb))
    return ADD_PAID_BY


async def add_paid_by_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    name  = query.from_user.first_name
    context.chat_data["draft"]["paid_by"] = name
    await query.edit_message_text(
        f"Paid by: {name}\n\n🏷️ Pick a category:",
        reply_markup=_category_keyboard("addcat"),
    )
    return ADD_CATEGORY


async def add_paid_by_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.chat_data["draft"]["paid_by"] = update.message.text.strip()
    await update.message.reply_text("🏷️ Pick a category:", reply_markup=_category_keyboard("addcat"))
    return ADD_CATEGORY


async def add_category_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    value = query.data.split(":", 1)[1]
    if value == "__custom__":
        await query.edit_message_text("✍️ Type your custom category:")
        return ADD_CATEGORY
    context.chat_data["draft"]["category"] = value
    return await _ask_add_date(update.effective_chat.id, context, query.message, edit=True)


async def add_category_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.chat_data["draft"]["category"] = update.message.text.strip()
    return await _ask_add_date(update.effective_chat.id, context, update.message, edit=False)


async def _ask_add_date(chat_id, context, message_obj, edit=False):
    kb = [
        [InlineKeyboardButton("📅 Today", callback_data="adddate:today"),
         InlineKeyboardButton("📅 Yesterday", callback_data="adddate:yesterday")],
        [InlineKeyboardButton("✍️ Type a date...", callback_data="adddate:__custom__")],
    ]
    text = "📅 When was this?"
    if edit:
        await message_obj.edit_text(text, reply_markup=InlineKeyboardMarkup(kb))
    else:
        await message_obj.reply_text(text, reply_markup=InlineKeyboardMarkup(kb))
    return ADD_DATE


async def add_date_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    value = query.data.split(":", 1)[1]
    if value == "__custom__":
        await query.edit_message_text(
            "✍️ Type the date, optionally with a time:\n"
            "`25-06-2026` or `25-06-2026 14:30`",
            parse_mode="Markdown",
        )
        return ADD_DATE
    context.chat_data["draft"]["date_str"] = parse_date_input(value)
    return await _show_add_confirm(update.effective_chat.id, context, query.message, edit=True)


async def add_date_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    raw = update.message.text.strip()
    try:
        context.chat_data["draft"]["date_str"] = parse_date_input(raw)
    except ValueError:
        await update.message.reply_text(
            "⚠️ Couldn't read that date. Try `25-06-2026`, `25-06-2026 14:30`, `today`, or `yesterday`.",
            parse_mode="Markdown",
        )
        return ADD_DATE
    return await _show_add_confirm(update.effective_chat.id, context, update.message, edit=False)


async def _show_add_confirm(chat_id, context, message_obj, edit=False):
    draft = context.chat_data["draft"]
    draft.setdefault("shared_by",        [draft["paid_by"]])
    draft.setdefault("amount_per_person", draft["price"])
    date_display = draft["date_str"].split(" ")[0] if draft.get("date_str") else "Today (now)"
    summary = (
        f"📋 *Confirm entry*\n"
        f"Place: {draft['place']}\n"
        f"Item: {draft['item']}\n"
        f"Price: {format_price(draft['price'])}\n"
        f"Paid by: {draft['paid_by']}\n"
        f"Category: {draft.get('category', '—')}\n"
        f"Date: {date_display}\n\n"
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
    category   = draft.get("category", "")
    sheets.append_expense(
        sheet_name, draft["place"], draft["item"], draft["price"],
        draft["paid_by"], ", ".join(draft["shared_by"]), draft["amount_per_person"],
        category=category, date_str=draft.get("date_str"),
    )
    warning = _budget_warnings(chat_id, sheet_name, category)
    await query.edit_message_text(f"✅ Saved to *{sheet_name}*!{warning}", parse_mode="Markdown")
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
    await update.message.reply_text("🏷️ Pick a category for this bill:", reply_markup=_category_keyboard("spcat"))
    return SPLIT_CATEGORY


async def split_category_btn(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    value = query.data.split(":", 1)[1]
    if value == "__custom__":
        await query.edit_message_text("✍️ Type your custom category:")
        return SPLIT_CATEGORY
    context.chat_data["split"]["category"] = value
    await query.edit_message_text(f"Category: {value}\n\nWho paid the bill overall?")
    return await _split_ask_payer(update, context)


async def split_category_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.chat_data["split"]["category"] = update.message.text.strip()
    await update.message.reply_text("Who paid the bill overall?")
    return await _split_ask_payer(update, context)


async def _split_ask_payer(update, context):
    members = state.get_members(update.effective_chat.id)
    if members:
        kb = []
        row = []
        # Index into the members list, not the name itself — member names
        # come from free-text /setmembers with no length cap, so embedding
        # one directly risks the 64-byte callback_data limit.
        for i, m in enumerate(members):
            row.append(InlineKeyboardButton(m, callback_data=f"payer:{i}"))
            if len(row) == 2:
                kb.append(row); row = []
        if row: kb.append(row)
        kb.append([InlineKeyboardButton("✍️ Type manually...", callback_data="payer:manual")])
        msg_text = "Who paid the bill overall?"
        if hasattr(update, 'callback_query') and update.callback_query:
            await update.callback_query.message.reply_text(msg_text, reply_markup=InlineKeyboardMarkup(kb))
        else:
            await update.message.reply_text(msg_text, reply_markup=InlineKeyboardMarkup(kb))
    return SPLIT_PAYER


async def split_payer_btn(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    token = query.data.split(":", 1)[1]
    if token == "manual":
        await query.edit_message_text("Type the name of who paid:")
        return SPLIT_PAYER
    members = state.get_members(update.effective_chat.id)
    try:
        payer = members[int(token)]
    except (ValueError, IndexError):
        await query.edit_message_text("⚠️ That option expired — please run /split again.")
        return ConversationHandler.END
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

    # Calculate each person's pre-tax subtotal
    subtotals = {}
    lines  = ["🧾 *Itemized Bill Summary*"]
    for it in items:
        share = it["price"] / len(it["names"])
        lines.append(
            f"• {it['item']} — {format_price(it['price'])} ÷ {len(it['names'])} "
            f"({', '.join(it['names'])}) = {format_price(share)}/person"
        )
        for n in it["names"]:
            subtotals[n] = subtotals.get(n, 0) + share

    subtotal = sum(it["price"] for it in items)
    lines.append(f"\nSubtotal: {format_price(subtotal)}")

    # Apply tax proportionally based on each person's share of the subtotal
    totals = dict(subtotals)
    if tax_pct:
        total_tax = subtotal * tax_pct / 100
        lines.append(f"Tax/Service ({tax_pct}%): {format_price(total_tax)}")
        lines.append("_(tax split proportionally by order size)_")
        for n in totals:
            person_tax = (subtotals[n] / subtotal) * total_tax if subtotal else 0
            totals[n] = subtotals[n] + person_tax

    lines.append(f"\n💰 *Paid by:* {split['payer']}")
    lines.append("*Final amounts owed (tax included):*")
    for n, amt in totals.items():
        note = " (payer)" if n.lower() == split["payer"].lower() else ""
        lines.append(f"  {n}: {format_price(amt)}{note}")

    split["totals"]   = totals
    split["subtotals"] = subtotals
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
    cat        = split.get("category", "")
    tax_pct    = split.get("tax_pct", 0)
    tax_mult   = 1 + tax_pct / 100  # e.g. 1.10 for 10% tax

    for it in split["items"]:
        # Bake proportional tax into each item's price
        taxed_price = round(it["price"] * tax_mult)
        share       = it["price"] / len(it["names"])
        taxed_share = round(share * tax_mult)
        label = it["item"] + (f" (incl. {tax_pct}% tax)" if tax_pct else "")
        sheets.append_expense(
            sheet_name, split["place"], label,
            taxed_price, payer, ", ".join(it["names"]),
            taxed_share, category=cat,
        )
    await query.edit_message_text(
        f"✅ Saved {len(split['items'])} item(s) to *{sheet_name}*!"
        + (f"\n_(prices include {tax_pct}% tax, split proportionally)_" if tax_pct else "")
        + _budget_warnings(chat_id, sheet_name, cat),
        parse_mode="Markdown",
    )
    return ConversationHandler.END


async def even_place(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.chat_data["even"]["place"] = update.message.text.strip()
    await update.message.reply_text("🏷️ Pick a category for this bill:", reply_markup=_category_keyboard("evcat"))
    return EVEN_CATEGORY


async def even_category_btn(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    value = query.data.split(":", 1)[1]
    if value == "__custom__":
        await query.edit_message_text("✍️ Type your custom category:")
        return EVEN_CATEGORY
    context.chat_data["even"]["category"] = value
    await query.edit_message_text(f"Category: {value}\n\nWho's splitting this? (comma-separated names)")
    return EVEN_NAMES


async def even_category_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.chat_data["even"]["category"] = update.message.text.strip()
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
        # Index into the members list, not the name itself — same
        # 64-byte callback_data concern as the /split payer buttons above.
        for i, m in enumerate(members):
            row.append(InlineKeyboardButton(m, callback_data=f"evenpayer:{i}"))
            if len(row) == 2:
                kb.append(row); row = []
        if row: kb.append(row)
        kb.append([InlineKeyboardButton("✍️ Type manually...", callback_data="evenpayer:manual")])
        await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(kb))
    else:
        await update.message.reply_text(text)
    return EVEN_PAYER


async def even_payer_btn(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    token = query.data.split(":", 1)[1]
    if token == "manual":
        await query.edit_message_text("Type the name of who paid:")
        return EVEN_PAYER
    members = state.get_members(update.effective_chat.id)
    try:
        payer = members[int(token)]
    except (ValueError, IndexError):
        await query.edit_message_text("⚠️ That option expired — please run /split again.")
        return ConversationHandler.END
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
    category   = even.get("category", "")
    sheets.append_expense(sheet_name, even["place"], even["label"], even["total"],
                          even["payer"], ", ".join(even["names"]), even["per_person"],
                          category=category)
    warning = _budget_warnings(chat_id, sheet_name, category)
    await query.edit_message_text(f"✅ Saved to *{sheet_name}*!{warning}", parse_mode="Markdown")
    return ConversationHandler.END


# ---------------------------------------------------------------------------
# /setreminder  /listreminders  /stopreminder  — multiple reminders per chat
# ---------------------------------------------------------------------------

import random
import datetime
import html

WIB_OFFSET = 7  # WIB = UTC+7

# Rotates so daily reminders don't feel like the exact same robotic ping every time.
_REMINDER_HEADERS = [
    ("⏰", "Reminder"),
    ("📝", "Don't forget"),
    ("💸", "Expense check-in"),
    ("🔔", "Friendly nudge"),
]


async def _send_reminder(context):
    """Job callback — sends the custom message saved with this reminder."""
    message = (context.job.data.get("message") or "").strip() or "Log your expenses!"
    chat_id = context.job.chat_id
    emoji, header = random.choice(_REMINDER_HEADERS)
    # Escape the user's own text so it can't break HTML parsing, then wrap it
    # with a bold header — much easier to read at a glance than plain text.
    text = f"{emoji} <b>{header}</b>\n\n{html.escape(message)}"
    await context.bot.send_message(chat_id=chat_id, text=text, parse_mode="HTML")


def _job_name(chat_id: int, reminder_id: str) -> str:
    return f"reminder_{chat_id}_{reminder_id}"


# ---------------------------------------------------------------------------
# New-month nudge — runs once a day; on the 1st of the month (WIB) it messages
# every chat that's ever used the bot. The active sheet no longer auto-follows
# the calendar (see active_sheet_name) — it stays fixed on whatever it was
# last explicitly set to, so a manual /switch is always required to move to
# the new month's sheet. This is purely a reminder to do that; it doesn't
# switch anything on your behalf.
# ---------------------------------------------------------------------------

async def _new_month_check(context):
    now = now_wib()
    if now.day != 1:
        return

    month_name = sheets.current_month_sheet_name()
    for chat_id in state.get_known_chats():
        current = state.get_active_sheet(chat_id)
        try:
            await context.bot.send_message(
                chat_id=chat_id,
                text=(
                    f"🗓️ *New month started — {month_name}*\n\n"
                    f"Your active sheet is still *{current}* — it won't switch on its own. "
                    f"Run /switch and pick *📅 This month ({month_name})* when you're "
                    f"ready to start logging into the new tab."
                ),
                parse_mode="Markdown",
            )
        except Exception:
            logger.warning(f"Could not send new-month nudge to chat {chat_id}", exc_info=True)


def _schedule_one(app, chat_id: int, reminder: dict):
    """Schedule a single reminder dict. Replaces existing job with same ID."""
    if app.job_queue is None:
        logger.warning("JobQueue not available — reminder saved but won't fire.")
        return
    jname = _job_name(chat_id, reminder["id"])
    for job in app.job_queue.get_jobs_by_name(jname):
        job.schedule_removal()
    app.job_queue.run_daily(
        _send_reminder,
        time=datetime.time(
            hour=reminder["hour"], minute=reminder["minute"],
            tzinfo=datetime.timezone.utc,
        ),
        chat_id=chat_id,
        name=jname,
        data={"message": reminder["message"]},
    )


async def setreminder_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Usage: /setreminder HH:MM Your custom message here"""
    text  = update.message.text.strip()
    parts = text.split(None, 2)   # ["/setreminder", "HH:MM", "message..."]

    if len(parts) < 3:
        await update.message.reply_text(
            "⏰ *Set a reminder*\n\n"
            "Usage: `/setreminder HH:MM Your message`\n\n"
            "*Examples:*\n"
            "`/setreminder 20:00 Log your expenses!`\n"
            "`/setreminder 12:00 Did you spend anything this morning?`\n"
            "`/setreminder 09:00 Weekly budget check`\n\n"
            "Time is in *WIB (UTC+7)*. You can add as many reminders as you like.",
            parse_mode="Markdown",
        )
        return

    time_str = parts[1]
    message  = parts[2].strip()

    try:
        hour_wib, minute = [int(x) for x in time_str.split(":")]
        if not (0 <= hour_wib <= 23 and 0 <= minute <= 59):
            raise ValueError
    except ValueError:
        await update.message.reply_text(
            "⚠️ Invalid time. Use HH:MM, e.g. `20:00`.", parse_mode="Markdown"
        )
        return

    hour_utc = (hour_wib - WIB_OFFSET) % 24
    chat_id  = update.effective_chat.id

    rid      = state.add_reminder(chat_id, hour_utc, minute, message)
    reminder = {"id": rid, "hour": hour_utc, "minute": minute, "message": message}
    _schedule_one(context.application, chat_id, reminder)

    existing = state.get_reminders(chat_id)
    await update.message.reply_text(
        f"✅ Reminder added at *{hour_wib:02d}:{minute:02d} WIB*\n"
        f"📝 Message: _{message}_\n\n"
        f"You now have *{len(existing)}* reminder(s). Use /listreminders to see them all.",
        parse_mode="Markdown",
    )

    if context.application.job_queue is None:
        await update.message.reply_text(
            "⚠️ Reminder saved but won't fire until the job scheduler is installed.\n\n"
            "SSH into the VM and run:\n"
            "`source ~/expense_bot/venv/bin/activate`\n"
            "`pip install 'python-telegram-bot[job-queue]==21.4'`\n"
            "Then restart the bot.",
            parse_mode="Markdown",
        )


async def listreminders_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id   = update.effective_chat.id
    reminders = state.get_reminders(chat_id)
    if not reminders:
        await update.message.reply_text(
            "You have no reminders set.\nUse /setreminder to add one."
        )
        return

    lines = [f"⏰ *Your reminders ({len(reminders)} total):*\n"]
    for i, r in enumerate(reminders, 1):
        hour_wib = (r["hour"] + WIB_OFFSET) % 24
        lines.append(f"*{i}. {hour_wib:02d}:{r['minute']:02d} WIB*  —  {r['message']}")
        lines.append(f"    ID: `{r['id']}`")
    lines.append("\nUse /stopreminder to remove one.")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def stopreminder_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id   = update.effective_chat.id
    reminders = state.get_reminders(chat_id)
    if not reminders:
        await update.message.reply_text("You have no active reminders.")
        return

    kb = []
    for r in reminders:
        hour_wib = (r["hour"] + WIB_OFFSET) % 24
        label    = f"🗑️ {hour_wib:02d}:{r['minute']:02d} WIB — {r['message'][:35]}"
        kb.append([InlineKeyboardButton(label, callback_data=f"delrem:{r['id']}")])
    kb.append([InlineKeyboardButton("🗑️ Remove ALL reminders", callback_data="delrem:__all__")])
    kb.append([InlineKeyboardButton("❌ Cancel", callback_data="delrem:__cancel__")])
    await update.message.reply_text(
        "Which reminder do you want to remove?",
        reply_markup=InlineKeyboardMarkup(kb),
    )


async def stopreminder_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query   = update.callback_query
    await query.answer()
    chat_id = update.effective_chat.id
    rid     = query.data.split(":", 1)[1]

    if rid == "__cancel__":
        await query.edit_message_text("Cancelled.")
        return

    if rid == "__all__":
        reminders = state.get_reminders(chat_id)
        if context.application.job_queue is not None:
            for r in reminders:
                jname = _job_name(chat_id, r["id"])
                for job in context.application.job_queue.get_jobs_by_name(jname):
                    job.schedule_removal()
        state.delete_all_reminders(chat_id)
        await query.edit_message_text("🗑️ All reminders removed.")
        return

    # Remove single reminder
    reminders = state.get_reminders(chat_id)
    target    = next((r for r in reminders if r["id"] == rid), None)
    if target:
        if context.application.job_queue is not None:
            jname = _job_name(chat_id, rid)
            for job in context.application.job_queue.get_jobs_by_name(jname):
                job.schedule_removal()
        state.delete_reminder(chat_id, rid)
        hour_wib = (target["hour"] + WIB_OFFSET) % 24
        await query.edit_message_text(
            f"🗑️ Removed: *{hour_wib:02d}:{target['minute']:02d} WIB* — {target['message']}",
            parse_mode="Markdown",
        )
    else:
        await query.edit_message_text("Reminder not found — it may have already been removed.")


# ---------------------------------------------------------------------------
# /overview  — spending breakdown by category
# ---------------------------------------------------------------------------

async def overview_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    kb = [
        [InlineKeyboardButton("📅 All Dates",  callback_data="ov_scope:all")],
        [InlineKeyboardButton("🗓️ Single Day", callback_data="ov_scope:day")],
        [InlineKeyboardButton("📆 Date Range", callback_data="ov_scope:range")],
    ]
    await update.message.reply_text(
        "🏷️ *Spending by category*\nWhich timeframe?",
        parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(kb),
    )
    return OVERVIEW_SCOPE


async def overview_scope(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    scope = query.data.split(":", 1)[1]
    context.chat_data["ov_scope"] = scope
    if scope == "all":
        return await execute_overview(update.effective_chat.id, context, query.message, None, None, edit=True)
    elif scope == "day":
        await query.edit_message_text("Type the date (e.g. `20-06-2026`):", parse_mode="Markdown")
    else:
        await query.edit_message_text("Type start and end dates (e.g. `15-06-2026 to 20-06-2026`):", parse_mode="Markdown")
    return OVERVIEW_DATE_INPUT


async def overview_date_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text  = update.message.text.strip().lower().replace("to", " ").split()
    scope = context.chat_data.get("ov_scope")
    start_date_str = end_date_str = None
    try:
        from datetime import datetime as dt
        if scope == "day":
            if len(text) != 1:
                await update.message.reply_text("Please provide exactly one date (e.g. `20-06-2026`).")
                return OVERVIEW_DATE_INPUT
            start_date_str = end_date_str = text[0]
            dt.strptime(start_date_str, "%d-%m-%Y")
        else:
            if len(text) != 2:
                await update.message.reply_text("Please provide two dates.")
                return OVERVIEW_DATE_INPUT
            start_date_str, end_date_str = text[0], text[1]
            dt.strptime(start_date_str, "%d-%m-%Y")
            dt.strptime(end_date_str,   "%d-%m-%Y")
    except ValueError:
        await update.message.reply_text("⚠️ Invalid date format. Use DD-MM-YYYY.")
        return OVERVIEW_DATE_INPUT
    return await execute_overview(update.effective_chat.id, context, update.message, start_date_str, end_date_str, edit=False)


async def execute_overview(chat_id, context, message_obj, start_date_str, end_date_str, edit=False):
    sheet_name = active_sheet_name(chat_id)
    date_msg   = "all dates"
    if start_date_str == end_date_str and start_date_str:
        date_msg = start_date_str
    elif start_date_str and end_date_str:
        date_msg = f"{start_date_str} to {end_date_str}"

    wait = f"⏳ Calculating overview for *{sheet_name}* ({date_msg})..."
    if edit:
        await message_obj.edit_text(wait, parse_mode="Markdown")
    else:
        await message_obj.reply_text(wait, parse_mode="Markdown")

    try:
        cats = sheets.get_category_overview(sheet_name, start_date_str, end_date_str)
        if not cats:
            text = f"No entries found in *{sheet_name}* for {date_msg}."
        else:
            grand_total = sum(v["total"] for v in cats.values())
            lines = [
                f"🏷️ *Spending by category*\n"
                f"_{sheet_name} — {date_msg}_\n"
                f"_(shows full bill amount per category, not per-person)_\n"
            ]
            for i, (cat, data) in enumerate(cats.items(), 1):
                pct = data["total"] / grand_total * 100 if grand_total else 0
                bar = "█" * int(pct / 10) + "░" * (10 - int(pct / 10))
                lines.append(
                    f"*{i}. {cat}*\n"
                    f"   {bar} {pct:.1f}%\n"
                    f"   {format_price(data['total'])}  ({data['count']} entr{'y' if data['count']==1 else 'ies'})"
                )
            lines.append(f"\n*Grand total: {format_price(grand_total)}*")
            text = "\n".join(lines)
        await context.bot.send_message(chat_id=chat_id, text=text, parse_mode="Markdown")
    except Exception as e:
        logger.error(f"Overview error: {e}")
        await context.bot.send_message(chat_id=chat_id, text="⚠️ Could not calculate overview.")

    context.chat_data.pop("ov_scope", None)
    return ConversationHandler.END


# ---------------------------------------------------------------------------
# /setbudget  /budgets — per-category spending caps with 80%/100% warnings
# ---------------------------------------------------------------------------

def _find_budget(chat_id: int, category: str):
    """Case-insensitive lookup. Returns the amount, or None if not set."""
    if not category:
        return None
    for cat, amt in state.get_budgets(chat_id).items():
        if cat.lower() == category.lower():
            return amt
    return None


def _budget_warning(chat_id: int, sheet_name: str, category: str) -> str:
    """Returns an extra text block to append to a save confirmation if this
    category has now hit 80% or 100% of its budget in the active sheet.
    Returns '' if no budget is set for this category, or it's still under 80%.
    """
    limit = _find_budget(chat_id, category)
    if limit is None or limit <= 0:
        return ""
    cats  = sheets.get_category_overview(sheet_name)
    spent = 0.0
    for cat, info in cats.items():
        if cat.lower() == category.lower():
            spent = info["total"]
            break
    pct = spent / limit * 100
    if pct >= 100:
        return (f"\n\n🔴 *Budget alert:* {category} is now {format_price(spent)} / "
                f"{format_price(limit)} ({pct:.0f}%) — over budget!")
    if pct >= 80:
        return (f"\n\n🟡 *Budget heads-up:* {category} is now {format_price(spent)} / "
                f"{format_price(limit)} ({pct:.0f}%) of budget.")
    return ""


def _overall_budget_warning(chat_id: int, sheet_name: str) -> str:
    """Same idea as _budget_warning, but against a single cap spanning ALL
    categories combined, tracked separately via /setbudget <amount> (no
    category given).
    """
    limit = state.get_total_budget(chat_id)
    if not limit or limit <= 0:
        return ""
    spent = sheets.get_recap(sheet_name)["grand_total"]
    pct   = spent / limit * 100
    if pct >= 100:
        return (f"\n\n🔴 *Overall budget alert:* {format_price(spent)} / "
                f"{format_price(limit)} ({pct:.0f}%) — over budget!")
    if pct >= 80:
        return (f"\n\n🟡 *Overall budget heads-up:* {format_price(spent)} / "
                f"{format_price(limit)} ({pct:.0f}%) of budget.")
    return ""


def _budget_warnings(chat_id: int, sheet_name: str, category: str) -> str:
    """Combined per-category + overall budget alert text, appended after saving."""
    return _budget_warning(chat_id, sheet_name, category) + _overall_budget_warning(chat_id, sheet_name)


SETBUDGET_USAGE = (
    "💰 *Set Budget usage:*\n"
    "`/setbudget Category Amount` — cap for one category\n"
    "`/setbudget Amount` — one overall cap across *all* categories\n\n"
    "*Examples:*\n"
    "`/setbudget Food 1000000`\n"
    "`/setbudget Transport 500k`\n"
    "`/setbudget 3000000` _(overall monthly cap)_\n\n"
    "You'll get a heads-up once spending (within the active sheet) passes "
    "*80%*, and an alert at *100%* — checked separately for each category "
    "budget and for the overall budget.\n\n"
    "Remove a budget by setting it to `0`: `/setbudget Food 0` or `/setbudget 0`\n"
    "See all budgets and current usage with /budgets."
)


async def setbudget_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    text    = update.message.text.strip()
    raw     = text.split(None, 1)

    if len(raw) < 2 or not raw[1].strip():
        await update.message.reply_text(SETBUDGET_USAGE, parse_mode="Markdown")
        return

    parts = raw[1].strip().split()

    # Single token, no category -> overall budget across ALL categories combined
    if len(parts) == 1:
        try:
            amount = parse_price(parts[0])
        except ValueError:
            await update.message.reply_text(
                f"⚠️ Couldn't read `{parts[0]}` as an amount.\n"
                "Try formats like `1000000`, `1.000.000`, or `1m`/`1000k`.",
                parse_mode="Markdown",
            )
            return
        if amount <= 0:
            removed = state.delete_total_budget(chat_id)
            await update.message.reply_text(
                "🗑️ Overall budget removed." if removed else "No overall budget was set.",
            )
            return
        state.set_total_budget(chat_id, amount)
        await update.message.reply_text(
            f"✅ Overall budget set: {format_price(amount)} across *all* categories\n"
            f"_(tracked against the active sheet's grand total — heads-up at 80%, alert at 100%)_",
            parse_mode="Markdown",
        )
        return

    price_raw = parts[-1]
    category  = " ".join(parts[:-1]).strip()

    try:
        amount = parse_price(price_raw)
    except ValueError:
        await update.message.reply_text(
            f"⚠️ Couldn't read `{price_raw}` as an amount.\n"
            "Try formats like `1000000`, `1.000.000`, or `1m`/`1000k`.",
            parse_mode="Markdown",
        )
        return

    if amount <= 0:
        removed = state.delete_budget(chat_id, category)
        if not removed:
            # try case-insensitive match too, since the user may retype the category differently
            for existing in list(state.get_budgets(chat_id).keys()):
                if existing.lower() == category.lower():
                    state.delete_budget(chat_id, existing)
                    removed = True
                    break
        if removed:
            await update.message.reply_text(f"🗑️ Budget removed for *{category}*.", parse_mode="Markdown")
        else:
            await update.message.reply_text(f"No budget was set for *{category}*.", parse_mode="Markdown")
        return

    state.set_budget(chat_id, category, amount)
    await update.message.reply_text(
        f"✅ Budget set: *{category}* → {format_price(amount)}\n"
        f"_(tracked against the active sheet — you'll get a heads-up at 80% and 100%)_",
        parse_mode="Markdown",
    )


async def budgets_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id      = update.effective_chat.id
    budgets      = state.get_budgets(chat_id)
    total_limit  = state.get_total_budget(chat_id)
    if not budgets and not total_limit:
        await update.message.reply_text(
            "No budgets set yet.\n\n" + SETBUDGET_USAGE, parse_mode="Markdown",
        )
        return

    name = active_sheet_name(chat_id)

    def _bar_line(label, spent, limit):
        pct    = (spent / limit * 100) if limit else 0
        filled = min(10, int(pct / 10))
        bar    = "█" * filled + "░" * (10 - filled)
        emoji  = "🔴" if pct >= 100 else ("🟡" if pct >= 80 else "🟢")
        return f"{emoji} *{label}*\n   {bar} {pct:.0f}%\n   {format_price(spent)} / {format_price(limit)}"

    lines = [f"💰 *Budgets — {name}*\n"]

    if total_limit:
        grand_total = sheets.get_recap(name)["grand_total"]
        lines.append(_bar_line("Overall (all categories)", grand_total, total_limit))
        if budgets:
            lines.append("")  # blank line before the per-category breakdown

    if budgets:
        cats = sheets.get_category_overview(name)
        spent_by_lower = {c.lower(): v["total"] for c, v in cats.items()}
        for cat, limit in budgets.items():
            spent = spent_by_lower.get(cat.lower(), 0.0)
            lines.append(_bar_line(cat, spent, limit))

    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


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

# ---------------------------------------------------------------------------
# Telegram's "☰ Menu" button — a short, curated list of the most-used
# commands (not every command — the full list stays in /help).
# ---------------------------------------------------------------------------

BOT_MENU_COMMANDS = [
    BotCommand("add",     "Log an expense step by step"),
    BotCommand("q",       "Quick-add a solo expense"),
    BotCommand("split",   "Split a group bill"),
    BotCommand("summary", "See who owes whom"),
    BotCommand("list",    "Browse recent entries"),
    BotCommand("undo",    "Remove the last entry"),
    BotCommand("switch",  "Switch active sheet"),
    BotCommand("help",    "Show all commands"),
]


async def _post_init(app):
    await app.bot.set_my_commands(BOT_MENU_COMMANDS)


def main():
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    app = ApplicationBuilder().token(config.TELEGRAM_BOT_TOKEN).post_init(_post_init).build()

    # Restore saved reminders from state.json on startup
    for chat_id, reminders in state.get_all_reminders().items():
        for reminder in reminders:
            try:
                _schedule_one(app, chat_id, reminder)
                hour_wib = (reminder["hour"] + WIB_OFFSET) % 24
                logger.info(f"Restored reminder for chat {chat_id} at {hour_wib:02d}:{reminder['minute']:02d} WIB")
            except Exception as e:
                logger.warning(f"Could not restore reminder {reminder.get('id')} for chat {chat_id}: {e}")

    # Daily check for "is it the 1st of the month" — 17:00 UTC = 00:00 WIB
    if app.job_queue is not None:
        app.job_queue.run_daily(
            _new_month_check,
            time=datetime.time(hour=17, minute=0, tzinfo=datetime.timezone.utc),
            name="new_month_check",
        )
    else:
        logger.warning("JobQueue not available — the new-month nudge won't fire.")

    app.add_handler(CommandHandler("start",      restricted(start_cmd)))
    app.add_handler(CommandHandler("help",       restricted(start_cmd)))
    app.add_handler(CommandHandler("getlink",    restricted(getlink_cmd)))
    app.add_handler(CommandHandler("recap",      restricted(recap_cmd)))
    app.add_handler(CommandHandler("setbudget",  restricted(setbudget_cmd)))
    app.add_handler(CommandHandler("budgets",    restricted(budgets_cmd)))
    app.add_handler(CommandHandler("sheet",      restricted(getlink_cmd)))  # keep old name as alias
    app.add_handler(CommandHandler("sortsheet",  restricted(sortsheet_cmd)))
    app.add_handler(CommandHandler("sort",       restricted(sortsheet_cmd)))  # short alias
    app.add_handler(CommandHandler("list",       restricted(list_cmd)))
    app.add_handler(CallbackQueryHandler(list_page_callback, pattern=r"^listpage:"))
    app.add_handler(CommandHandler("delete",     restricted(delete_cmd)))
    app.add_handler(CommandHandler("undo",       restricted(undo_cmd)))
    app.add_handler(CallbackQueryHandler(delete_page_callback,   pattern=r"^delpage:"))
    app.add_handler(CallbackQueryHandler(delete_callback,        pattern=r"^del:"))
    app.add_handler(CallbackQueryHandler(delete_cancel_callback, pattern=r"^delcancel$"))
    app.add_handler(CallbackQueryHandler(undo_callback,          pattern=r"^undo:"))
    app.add_handler(CallbackQueryHandler(undo_cancel_callback,   pattern=r"^undocancel$"))
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
    app.add_handler(CommandHandler("setreminder",   restricted(setreminder_cmd)))
    app.add_handler(CommandHandler("listreminders", restricted(listreminders_cmd)))
    app.add_handler(CommandHandler("stopreminder",  restricted(stopreminder_cmd)))
    app.add_handler(CallbackQueryHandler(stopreminder_callback, pattern=r"^delrem:"))
    app.add_handler(CommandHandler("setmembers",   restricted(setmembers_cmd)))

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
                CallbackQueryHandler(edit_category_button, pattern=r"^editcat:"),
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
            ADD_CATEGORY: [
                CallbackQueryHandler(add_category_button, pattern=r"^addcat:"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, add_category_text),
            ],
            ADD_DATE: [
                CallbackQueryHandler(add_date_button, pattern=r"^adddate:"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, add_date_text),
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

    overview_conv = ConversationHandler(
        entry_points=[CommandHandler("overview", restricted(overview_start))],
        states={
            OVERVIEW_SCOPE:      [CallbackQueryHandler(overview_scope, pattern=r"^ov_scope:")],
            OVERVIEW_DATE_INPUT: [MessageHandler(filters.TEXT & ~filters.COMMAND, overview_date_input)],
        },
        fallbacks=[CommandHandler("cancel", cancel_cmd)],
    )
    app.add_handler(overview_conv)

    newevent_conv = ConversationHandler(
        entry_points=[CommandHandler("newevent", restricted(newevent_start))],
        states={NEWEVENT_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, newevent_name)]},
        fallbacks=[CommandHandler("cancel", cancel_cmd)],
    )
    app.add_handler(newevent_conv)

    split_conv = ConversationHandler(
        entry_points=[CommandHandler("split", restricted(split_start))],
        states={
            SPLIT_MODE:     [CallbackQueryHandler(split_mode, pattern=r"^split_mode:")],
            SPLIT_PLACE:    [MessageHandler(filters.TEXT & ~filters.COMMAND, split_place)],
            SPLIT_CATEGORY: [
                CallbackQueryHandler(split_category_btn,  pattern=r"^spcat:"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, split_category_text),
            ],
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
            EVEN_CATEGORY: [
                CallbackQueryHandler(even_category_btn,  pattern=r"^evcat:"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, even_category_text),
            ],
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