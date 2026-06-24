"""Telegram Expense Tracker Bot.

Commands:
  /add        - add a single expense
  /split      - split a group bill (itemized or evenly)
  /list       - show recent entries in the active sheet
  /delete     - delete an entry
  /sheet      - get the spreadsheet link
  /newevent   - create a new sheet/tab for a trip or event
  /switch     - switch which sheet/tab is active
  /summary    - summaries bill
  /cancel     - cancel whatever you're doing
"""
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

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

# ---- Conversation states ----
ADD_PLACE, ADD_ITEM, ADD_PRICE, ADD_PAID_BY, ADD_SHARED_BY, ADD_CONFIRM = range(6)
NEWEVENT_NAME = 6
(
    SPLIT_MODE,
    SPLIT_PLACE,
    SPLIT_PAYER,
    SPLIT_ITEMS,
    SPLIT_TAX,
    SPLIT_CONFIRM,
    EVEN_PLACE,
    EVEN_PAYER,
    EVEN_NAMES,
    EVEN_TOTAL,
    EVEN_LABEL,
    EVEN_CONFIRM,
) = range(7, 19)
SUMMARY_MODE, SUMMARY_SCOPE, SUMMARY_DATE_INPUT = range(19, 22)

HELP_TEXT = (
    "👋 *Expense Tracker Bot*\n\n"
    "/add — log a personal expense\n"
    "/split — split a group bill (itemized or evenly)\n"
    "/summary — calculate who owes whom\n"
    "/list — show recent entries (with full details)\n"
    "/delete — delete an entry\n"
    "/markpaid — mark entries paid/unpaid; for split bills, mark per person\n"
    "/sheet — get the spreadsheet link\n"
    "/newevent — create a new sheet for a trip/event\n"
    "/switch — switch between sheets\n"
    "/setmembers — save group names for quick-select buttons\n"
    "/cancel — cancel the current action\n\n"
    "📅 *Summary Commands:*\n"
    "• `/summary` (calculates all dates)\n"
    "• `/summary 20-06-2026` (single day)\n"
    "• `/summary 15-06-2026 20-06-2026` (date range)\n\n"
    "By default, entries go into a sheet named after the current month, "
    "and a new one is created automatically the first time you use the bot "
    "in a new month.\n\n"
    "💡 Prices: you can type `25000`, `25.000`, or `25k`."
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


async def sheet_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    name = active_sheet_name(chat_id)
    url = sheets.sheet_url(name)
    await update.message.reply_text(
        f"📊 Active sheet: *{name}*\n{url}\n\nFull spreadsheet:\n{sheets.spreadsheet_url()}",
        parse_mode="Markdown",
    )


async def list_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    name = active_sheet_name(chat_id)
    recent = sheets.get_recent(name, 10)
    if not recent:
        await update.message.reply_text(f"No entries yet in *{name}*.", parse_mode="Markdown")
        return

    lines = [f"🧾 Last entries in *{name}*:\n"]
    for row_num, row in recent:
        date     = row[0] if len(row) > 0 else "?"
        place    = row[1] if len(row) > 1 else "?"
        item     = row[2] if len(row) > 2 else "?"
        price    = row[3] if len(row) > 3 else "?"
        paid_by  = row[4] if len(row) > 4 else "?"
        shared   = row[5] if len(row) > 5 else "?"
        per_p    = row[6] if len(row) > 6 else "?"
        status   = row[8] if len(row) > 8 and row[8] else "⏳ Unpaid"
        settled  = row[9] if len(row) > 9 and row[9] else ""

        settled_line = f"💸 Settled: {settled}" if settled else ""

        lines.append(
            f"*#{row_num} — {item}*\n"
            f"📍 {place}  |  💰 {price}  |  {status}\n"
            f"🙋 Paid by: {paid_by}\n"
            f"👥 Shared: {shared}  ({per_p}/person)\n"
            + (f"💸 Settled: {settled}\n" if settled else "")
            + f"🕐 {date}"
        )
        lines.append("")

    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


# async def summary_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
#     chat_id = update.effective_chat.id
#     sheet_name = active_sheet_name(chat_id)
    
#     args = context.args
#     start_date_str = None
#     end_date_str = None
    
#     # Parse dates from the command arguments
#     if len(args) >= 1:
#         start_date_str = args[0]
#         end_date_str = args[0] # Assume single day first
#     if len(args) >= 2:
#         if args[1].lower() == "to" and len(args) >= 3: # Handle "/summary 15-06-2026 to 20-06-2026"
#             end_date_str = args[2]
#         else: # Handle "/summary 15-06-2026 20-06-2026"
#             end_date_str = args[1]
            
#     # Validate the date format
#     from datetime import datetime
#     try:
#         if start_date_str:
#             datetime.strptime(start_date_str, "%d-%m-%Y")
#         if end_date_str:
#             datetime.strptime(end_date_str, "%d-%m-%Y")
#     except ValueError:
#         await update.message.reply_text("⚠️ Invalid date format. Please use DD-MM-YYYY\nExamples:\n`/summary 20-06-2026`\n`/summary 15-06-2026 20-06-2026`", parse_mode="Markdown")
#         return

#     # Create a nice message about what dates are being checked
#     date_msg = "all dates"
#     if start_date_str == end_date_str and start_date_str:
#         date_msg = f"{start_date_str}"
#     elif start_date_str and end_date_str:
#         date_msg = f"{start_date_str} to {end_date_str}"

#     await update.message.reply_text(f"📊 Calculating settlement for *{sheet_name}* ({date_msg})...", parse_mode="Markdown")

#     try:
#         transactions = sheets.get_settlement_summary(sheet_name, start_date_str, end_date_str)
#         if not transactions:
#             await update.message.reply_text("🎉 Everybody is settled up for this period! No one owes anything.")
#         else:
#             text = f"🧾 *Settlement ({date_msg})*\n\n" + "\n".join(transactions)
#             await update.message.reply_text(text, parse_mode="Markdown")
#     except Exception as e:
#         logger.error(f"Summary error: {e}")
#         await update.message.reply_text("⚠️ Could not calculate summary. Make sure the sheet data is formatted correctly.")

async def summary_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    kb = [
        [InlineKeyboardButton("📊 Smart (Optimized)", callback_data="sum_mode:smart")],
        [InlineKeyboardButton("🧾 Normal (Straight)", callback_data="sum_mode:raw")]
    ]
    await update.message.reply_text("How do you want to calculate the summary?", reply_markup=InlineKeyboardMarkup(kb))
    return SUMMARY_MODE

async def summary_mode(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    mode = query.data.split(":", 1)[1]
    context.chat_data["summary_mode"] = mode

    kb = [
        [InlineKeyboardButton("📅 All Dates", callback_data="sum_scope:all")],
        [InlineKeyboardButton("🗓️ Single Day", callback_data="sum_scope:day")],
        [InlineKeyboardButton("📆 Date Range", callback_data="sum_scope:range")]
    ]
    
    title = "Smart" if mode == "smart" else "Normal"
    await query.edit_message_text(f"Selected: *{title}*\n\nWhich timeframe do you want to check?", parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(kb))
    return SUMMARY_SCOPE

async def summary_scope(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    scope = query.data.split(":", 1)[1]
    context.chat_data["summary_scope"] = scope

    if scope == "all":
        return await execute_summary(update.effective_chat.id, context, query.message, None, None, edit=True)
    elif scope == "day":
        await query.edit_message_text("Type the date you want to check (e.g. `20-06-2026`):", parse_mode="Markdown")
        return SUMMARY_DATE_INPUT
    elif scope == "range":
        await query.edit_message_text("Type the start and end dates (e.g. `15-06-2026 to 20-06-2026`):", parse_mode="Markdown")
        return SUMMARY_DATE_INPUT

async def summary_date_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip().lower().replace("to", " ").split()
    scope = context.chat_data.get("summary_scope")
    start_date_str = None
    end_date_str = None

    try:
        from datetime import datetime
        if scope == "day":
            if len(text) != 1:
                await update.message.reply_text("Please provide exactly one date (e.g. `20-06-2026`).")
                return SUMMARY_DATE_INPUT
            start_date_str = text[0]
            end_date_str = text[0]
            datetime.strptime(start_date_str, "%d-%m-%Y")
        elif scope == "range":
            if len(text) != 2:
                await update.message.reply_text("Please provide exactly two dates (e.g. `15-06-2026 20-06-2026`).")
                return SUMMARY_DATE_INPUT
            start_date_str = text[0]
            end_date_str = text[1]
            datetime.strptime(start_date_str, "%d-%m-%Y")
            datetime.strptime(end_date_str, "%d-%m-%Y")
    except ValueError:
        await update.message.reply_text("⚠️ Invalid date format. Please use DD-MM-YYYY.")
        return SUMMARY_DATE_INPUT

    return await execute_summary(update.effective_chat.id, context, update.message, start_date_str, end_date_str, edit=False)

async def execute_summary(chat_id, context, message_obj, start_date_str, end_date_str, edit=False):
    mode = context.chat_data.get("summary_mode", "smart")
    sheet_name = active_sheet_name(chat_id)
    date_msg = "all dates"
    
    if start_date_str == end_date_str and start_date_str:
        date_msg = f"{start_date_str}"
    elif start_date_str and end_date_str:
        date_msg = f"{start_date_str} to {end_date_str}"

    calc_text = f"📊 Calculating {'Smart' if mode == 'smart' else 'Straight'} settlement for *{sheet_name}* ({date_msg})..."
    
    if edit:
        await message_obj.edit_text(calc_text, parse_mode="Markdown")
    else:
        await message_obj.reply_text(calc_text, parse_mode="Markdown")

    try:
        if mode == "smart":
            transactions = sheets.get_settlement_summary(sheet_name, start_date_str, end_date_str)
        else:
            transactions = sheets.get_raw_summary(sheet_name, start_date_str, end_date_str)

        if not transactions:
            text = "🎉 Everybody is settled up for this period! No one owes anything."
        else:
            title = "🧾 *Smart Settlement*" if mode == "smart" else "🧾 *Straight Settlement (Bill-by-Bill)*"
            text = f"{title}\nPeriod: {date_msg}\n\n" + "\n".join(transactions)
            
        # Send as a new message so it triggers a notification
        await context.bot.send_message(chat_id=chat_id, text=text, parse_mode="Markdown")
    except Exception as e:
        logger.error(f"Summary error: {e}")
        await context.bot.send_message(chat_id=chat_id, text="⚠️ Could not calculate summary. Make sure the sheet data is formatted correctly.")

    context.chat_data.pop("summary_mode", None)
    context.chat_data.pop("summary_scope", None)
    return ConversationHandler.END

async def cancel_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.chat_data.clear()
    await update.message.reply_text("Cancelled.")
    return ConversationHandler.END


async def setmembers_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.replace("/setmembers", "").strip()
    if not text:
        await update.message.reply_text(
            "Please provide names separated by commas. \nExample: `/setmembers Andi, Budi, Charlie`", 
            parse_mode="Markdown"
        )
        return
    
    names = [n.strip() for n in text.split(",") if n.strip()]
    chat_id = update.effective_chat.id
    state.set_members(chat_id, names)
    
    await update.message.reply_text(f"✅ Saved {len(names)} members for this chat: {', '.join(names)}")

# ---------------------------------------------------------------------------
# /delete
# ---------------------------------------------------------------------------

async def delete_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    name = active_sheet_name(chat_id)
    recent = sheets.get_recent(name, 10)
    if not recent:
        await update.message.reply_text(f"No entries to delete in *{name}*.", parse_mode="Markdown")
        return
    kb = []
    for row_num, row in recent:
        item = row[1] if len(row) > 1 else "?"
        price = row[2] if len(row) > 2 else "?"
        kb.append([InlineKeyboardButton(f"#{row_num} {item} ({price})", callback_data=f"del:{name}:{row_num}")])
    await update.message.reply_text(
        f"Tap an entry to delete from *{name}*:", parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(kb)
    )


async def delete_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    _, name, row_num = query.data.split(":")
    sheets.delete_row(name, int(row_num))
    await query.edit_message_text(f"🗑️ Deleted row #{row_num} from {name}.")


# ---------------------------------------------------------------------------
# /markpaid  — unified flow
#
# Step 1: list of entries (tap one to drill in)
# Step 2a: solo entry  → toggle whole entry paid/unpaid directly
# Step 2b: split entry → show each person as individual toggles
# ---------------------------------------------------------------------------

def _is_split(row) -> bool:
    """True if this entry has more than one person in Shared By."""
    shared = row[5] if len(row) > 5 else ""
    return len([n for n in shared.split(",") if n.strip()]) > 1


def _build_entry_list_keyboard(rows):
    """Step 1: list every entry as a tappable button."""
    kb = []
    for row_num, row in rows:
        item    = row[2] if len(row) > 2 else "?"
        price   = row[3] if len(row) > 3 else "?"
        status  = row[8] if len(row) > 8 and row[8] else "⏳ Unpaid"
        settled = row[9] if len(row) > 9 and row[9] else ""
        s_icon  = "✅" if status == "✅ Paid" else "⏳"

        if _is_split(row):
            shared = row[5] if len(row) > 5 else ""
            names  = [n.strip() for n in shared.split(",") if n.strip()]
            s_names = [n.strip() for n in settled.split(",") if n.strip()]
            badge  = f" ({len(s_names)}/{len(names)} settled)"
            label  = f"{s_icon} #{row_num} {item} ({price}){badge} 👥"
        else:
            label  = f"{s_icon} #{row_num} {item} ({price})"

        kb.append([InlineKeyboardButton(label, callback_data=f"mk1:{row_num}")])
    kb.append([InlineKeyboardButton("❌ Cancel", callback_data="mkcancel")])
    return InlineKeyboardMarkup(kb)


def _build_solo_keyboard(row_num, status):
    """Step 2a: single-person entry — just toggle paid/unpaid."""
    other = "⏳ Unpaid" if status == "✅ Paid" else "✅ Paid"
    other_emoji = "⏳" if status == "✅ Paid" else "✅"
    kb = [
        [InlineKeyboardButton(f"Mark as {other_emoji} {other}", callback_data=f"mksolo:{row_num}")],
        [InlineKeyboardButton("🔙 Back", callback_data="mkback"),
         InlineKeyboardButton("❌ Cancel", callback_data="mkcancel")],
    ]
    return InlineKeyboardMarkup(kb)


def _build_people_keyboard(row_num, names, settled_set):
    """Step 2b: split entry — toggle each person individually."""
    kb = []
    for name in names:
        icon  = "✅" if name in settled_set else "⏳"
        kb.append([InlineKeyboardButton(f"{icon} {name}", callback_data=f"mkperson:{row_num}:{name}")])
    kb.append([
        InlineKeyboardButton("💾 Save", callback_data=f"mksave:{row_num}"),
        InlineKeyboardButton("🔙 Back", callback_data="mkback"),
    ])
    kb.append([InlineKeyboardButton("❌ Cancel", callback_data="mkcancel")])
    return InlineKeyboardMarkup(kb)


def _mk_cleanup(context):
    for k in ("mk_sheet", "mk_rows", "mk_names", "mk_settled"):
        context.chat_data.pop(k, None)


async def markpaid_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    name    = active_sheet_name(chat_id)
    recent  = sheets.get_recent(name, 10)
    if not recent:
        await update.message.reply_text(f"No entries yet in *{name}*.", parse_mode="Markdown")
        return
    context.chat_data["mk_sheet"] = name
    context.chat_data["mk_rows"]  = recent
    kb = _build_entry_list_keyboard(recent)
    await update.message.reply_text(
        f"💳 *Mark payment status* — *{name}*\n"
        f"_(👥 = split bill, tap any entry)_",
        parse_mode="Markdown",
        reply_markup=kb,
    )


async def mk_pick_entry(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """User tapped an entry — route to solo or split flow."""
    query   = update.callback_query
    await query.answer()
    row_num = int(query.data.split(":")[1])
    rows    = context.chat_data.get("mk_rows", [])

    chosen = next((r for rn, r in rows if rn == row_num), None)
    if chosen is None:
        await query.edit_message_text("Entry not found. Try /markpaid again.")
        return

    item   = chosen[2] if len(chosen) > 2 else "?"
    price  = chosen[3] if len(chosen) > 3 else "?"
    status = chosen[8] if len(chosen) > 8 and chosen[8] else "⏳ Unpaid"

    if _is_split(chosen):
        # Split bill → show per-person toggles
        shared  = chosen[5] if len(chosen) > 5 else ""
        settled = chosen[9] if len(chosen) > 9 and chosen[9] else ""
        names   = [n.strip() for n in shared.split(",") if n.strip()]
        s_names = set(n.strip() for n in settled.split(",") if n.strip())
        context.chat_data["mk_names"]   = names
        context.chat_data["mk_settled"] = s_names
        kb = _build_people_keyboard(row_num, names, s_names)
        await query.edit_message_text(
            f"👥 *#{row_num} — {item}* ({price})\n"
            f"Tap each person to toggle ✅/⏳, then Save:",
            parse_mode="Markdown",
            reply_markup=kb,
        )
    else:
        # Solo entry → show simple toggle
        kb = _build_solo_keyboard(row_num, status)
        s_icon = "✅" if status == "✅ Paid" else "⏳"
        await query.edit_message_text(
            f"{s_icon} *#{row_num} — {item}* ({price})\nCurrently: *{status}*",
            parse_mode="Markdown",
            reply_markup=kb,
        )


async def mk_solo_toggle(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Toggle the overall paid status for a solo entry."""
    query   = update.callback_query
    await query.answer()
    row_num = int(query.data.split(":")[1])
    name    = context.chat_data.get("mk_sheet", "")
    new_status = sheets.toggle_paid_status(name, row_num)
    emoji = "✅" if new_status == "✅ Paid" else "⏳"
    _mk_cleanup(context)
    await query.edit_message_text(
        f"{emoji} Row *#{row_num}* marked as *{new_status}*.",
        parse_mode="Markdown",
    )


async def mk_person_toggle(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Toggle one person's settled status in a split entry."""
    query  = update.callback_query
    parts  = query.data.split(":", 2)   # mkperson:ROWNUM:NAME
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
    """Save per-person settled list to sheet."""
    query   = update.callback_query
    await query.answer()
    row_num = int(query.data.split(":")[1])
    name    = context.chat_data.get("mk_sheet", "")
    settled = context.chat_data.get("mk_settled", set())
    names   = context.chat_data.get("mk_names", [])

    ordered   = [n for n in names if n in settled]
    remaining = [n for n in names if n not in settled]
    sheets.set_settled_by(name, row_num, ordered)

    if not ordered:
        summary = "No one marked as settled yet."
    elif not remaining:
        summary = "Everyone has settled! 🎉"
    else:
        summary = f"✅ Settled: {', '.join(ordered)}\n⏳ Still owes: {', '.join(remaining)}"

    _mk_cleanup(context)
    await query.edit_message_text(
        f"💾 *Saved — #{row_num}*\n{summary}",
        parse_mode="Markdown",
    )


async def mk_back(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Go back to the entry list."""
    query = update.callback_query
    await query.answer()
    rows = context.chat_data.get("mk_rows", [])
    name = context.chat_data.get("mk_sheet", "")
    context.chat_data.pop("mk_names",   None)
    context.chat_data.pop("mk_settled", None)
    kb = _build_entry_list_keyboard(rows)
    await query.edit_message_text(
        f"💳 *Mark payment status* — *{name}*\n_(👥 = split bill, tap any entry)_",
        parse_mode="Markdown",
        reply_markup=kb,
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
    await update.message.reply_text("🎉 What's the name of this event/sheet? (e.g. 'Bali Trip', 'Office Lunch June')")
    return NEWEVENT_NAME


async def newevent_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    name = sheets.sanitize_sheet_name(update.message.text.strip())
    sheets.get_or_create_worksheet(name)
    state.set_active_sheet(update.effective_chat.id, name)
    await update.message.reply_text(
        f"✅ Created sheet *{name}* and switched to it.\n"
        f"All new entries (/add, /split) will go here until you /switch back.",
        parse_mode="Markdown",
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
    name = query.data.split(":", 1)[1]
    chat_id = update.effective_chat.id
    if name == "__default__":
        state.set_active_sheet(chat_id, None)
        await query.edit_message_text(f"✅ Switched to current month sheet: {sheets.current_month_sheet_name()}")
    else:
        state.set_active_sheet(chat_id, name)
        await query.edit_message_text(f"✅ Switched to: {name}")


# ---------------------------------------------------------------------------
# /add  (single expense)
# ---------------------------------------------------------------------------

async def add_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.chat_data["draft"] = {}
    await update.message.reply_text("📍 Where did you spend this? (e.g. Indomaret, Steam, Resto X)")
    return ADD_PLACE

async def add_place(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.chat_data["draft"]["place"] = update.message.text.strip()
    await update.message.reply_text("📝 What did you buy?")
    return ADD_ITEM

async def add_item(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.chat_data["draft"]["item"] = update.message.text.strip()
    await update.message.reply_text("💰 How much was it? (e.g. 25000 or 25k)")
    return ADD_PRICE

async def add_price(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        price = parse_price(update.message.text)
    except ValueError:
        await update.message.reply_text("Hmm, I couldn't read that price. Try again, e.g. 25000 or 25k")
        return ADD_PRICE
    context.chat_data["draft"]["price"] = price
    sender = update.effective_user.first_name
    kb = [[InlineKeyboardButton(f"Me ({sender})", callback_data=f"paidby:{sender}")]]
    await update.message.reply_text("🙋 Who paid? (tap below, or type a name)", reply_markup=InlineKeyboardMarkup(kb))
    return ADD_PAID_BY


async def add_paid_by_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    name = query.data.split(":", 1)[1]
    return await _finalize_add(update.effective_chat.id, name, context, query.message, edit=True)

async def add_paid_by_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    name = update.message.text.strip()
    return await _finalize_add(update.effective_chat.id, name, context, update.message, edit=False)

async def _finalize_add(chat_id, payer_name, context, message_obj, edit=False):
    draft = context.chat_data["draft"]
    draft["paid_by"] = payer_name
    
    # Auto-fill shared_by with just the payer (since /split handles groups)
    draft["shared_by"] = [payer_name]
    draft["amount_per_person"] = draft["price"]

    summary = (
        f"📋 *Confirm entry*\n"
        f"Place: {draft['place']}\n"
        f"Item: {draft['item']}\n"
        f"Price: {format_price(draft['price'])}\n"
        f"Paid by: {draft['paid_by']}\n\n"
        f"Sheet: {active_sheet_name(chat_id)}"
    )
    kb = [[InlineKeyboardButton("✅ Save", callback_data="add_save"), InlineKeyboardButton("❌ Cancel", callback_data="add_cancel")]]
    
    if edit:
        # Changed edit_message_text to edit_text here!
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
    draft = context.chat_data.pop("draft")
    chat_id = update.effective_chat.id
    sheet_name = active_sheet_name(chat_id)
    sheets.append_expense(
        sheet_name, draft["place"], draft["item"], draft["price"], draft["paid_by"], ", ".join(draft["shared_by"]), draft["amount_per_person"]
    )
    await query.edit_message_text(f"✅ Saved to *{sheet_name}*!", parse_mode="Markdown")
    return ConversationHandler.END


# ---------------------------------------------------------------------------
# /split  (group bill: itemized or even)
# ---------------------------------------------------------------------------

async def split_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    kb = [
        [InlineKeyboardButton("🧾 Itemized (different orders)", callback_data="split_mode:item")],
        [InlineKeyboardButton("➗ Split Evenly (one total)", callback_data="split_mode:even")],
    ]
    await update.message.reply_text("How do you want to split this bill?", reply_markup=InlineKeyboardMarkup(kb))
    return SPLIT_MODE


async def split_mode(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    mode = query.data.split(":", 1)[1]
    if mode == "even":
        context.chat_data["even"] = {}
        await query.edit_message_text("➗ Even split selected.\n\n📍 Where is this expense from? (e.g. Resto X)")
        return EVEN_PLACE
    context.chat_data["split"] = {"items": []}
    await query.edit_message_text("🧾 Itemized split selected.\n\n📍 Where is this expense from? (e.g. Resto X)")
    return SPLIT_PLACE

async def split_place(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.chat_data["split"]["place"] = update.message.text.strip()
    chat_id = update.effective_chat.id
    members = state.get_members(chat_id)
    
    text = "Who paid the bill overall?"
    if members:
        # Build a 2-column grid of buttons
        kb = []
        row = []
        for m in members:
            row.append(InlineKeyboardButton(m, callback_data=f"payer:{m}"))
            if len(row) == 2:
                kb.append(row)
                row = []
        if row:
            kb.append(row)
        kb.append([InlineKeyboardButton("✍️ Type it manually...", callback_data="payer:__manual__")])
        
        await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(kb))
    else:
        await update.message.reply_text(text + "\n*(Tip: Use /setmembers Andi, Budi to get buttons here!)*", parse_mode="Markdown")
        
    return SPLIT_PAYER

async def split_payer_btn(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    payer = query.data.split(":", 1)[1]
    
    if payer == "__manual__":
        await query.edit_message_text("Please type the name of the person who paid:")
        return SPLIT_PAYER
        
    context.chat_data["split"]["payer"] = payer
    await query.edit_message_text(
        f"Paid by: {payer}\n\nNow send each item like this:\n`Item | Price | Names`\n"
        "Example: `Nasi Goreng | 25000 | Andi, Budi`\n\n"
        "Send one item per message. Type /done when finished.",
        parse_mode="Markdown",
    )
    return SPLIT_ITEMS

async def even_place(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.chat_data["even"]["place"] = update.message.text.strip()
    await update.message.reply_text("Who's splitting this? (comma-separated names)")
    return EVEN_NAMES

# --- itemized path ---

async def split_payer(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.chat_data["split"]["payer"] = update.message.text.strip()
    await update.message.reply_text(
        "Now send each item like this:\n`Item | Price | Names`\n"
        "Example: `Nasi Goreng | 25000 | Andi, Budi`\n\n"
        "Send one item per message. Type /done when finished.",
        parse_mode="Markdown",
    )
    return SPLIT_ITEMS


async def split_item_line(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    parts = [p.strip() for p in text.split("|")]
    if len(parts) != 3:
        await update.message.reply_text("Format should be: `Item | Price | Names`", parse_mode="Markdown")
        return SPLIT_ITEMS
    name, price_raw, names_raw = parts
    try:
        price = parse_price(price_raw)
    except ValueError:
        await update.message.reply_text("I couldn't read that price, try again.")
        return SPLIT_ITEMS
    names = [n.strip() for n in names_raw.split(",") if n.strip()]
    if not names:
        await update.message.reply_text("Please list at least one name for this item.")
        return SPLIT_ITEMS
    context.chat_data["split"]["items"].append({"item": name, "price": price, "names": names})
    await update.message.reply_text(f"Added: {name} ({format_price(price)}) → {', '.join(names)}\nSend next item, or /done.")
    return SPLIT_ITEMS


async def split_done(update: Update, context: ContextTypes.DEFAULT_TYPE):
    items = context.chat_data["split"].get("items", [])
    if not items:
        await update.message.reply_text("You haven't added any items yet. Send at least one, or /cancel.")
        return SPLIT_ITEMS
    await update.message.reply_text("Any tax/service charge %? (number, e.g. 10, or 0 if none)")
    return SPLIT_TAX


async def split_tax(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        tax_pct = float(update.message.text.strip().replace("%", ""))
    except ValueError:
        await update.message.reply_text("Please send a number, e.g. 10 or 0.")
        return SPLIT_TAX

    split = context.chat_data["split"]
    split["tax_pct"] = tax_pct
    payer = split["payer"]
    items = split["items"]

    totals = {}
    lines = ["🧾 *Itemized Bill Summary*"]
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

    lines.append(f"\n💰 *Paid by:* {payer}")
    lines.append("*Final amounts owed:*")
    for n, amt in totals.items():
        note = " (payer)" if n.lower() == payer.lower() else ""
        lines.append(f"  {n}: {format_price(amt)}{note}")

    split["totals"] = totals
    text = "\n".join(lines)
    kb = [[InlineKeyboardButton("✅ Save to Sheet", callback_data="splitsave"), InlineKeyboardButton("❌ Cancel", callback_data="splitcancel")]]
    await update.message.reply_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(kb))
    return SPLIT_CONFIRM


async def split_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.data == "splitcancel":
        context.chat_data.pop("split", None)
        await query.edit_message_text("Cancelled.")
        return ConversationHandler.END

    split = context.chat_data.pop("split")
    chat_id = update.effective_chat.id
    sheet_name = active_sheet_name(chat_id)
    payer = split["payer"]

    for it in split["items"]:
        share = it["price"] / len(it["names"])
        sheets.append_expense(sheet_name, split["place"], it["item"], it["price"], payer, ", ".join(it["names"]), round(share, 2))

    if split.get("tax_pct"):
        tax_amount = sum(i["price"] for i in split["items"]) * split["tax_pct"] / 100
        # Give the bot the exact names so it can do the math later
        all_names = list(split["totals"].keys())
        tax_per_person = tax_amount / len(all_names)
        sheets.append_expense(sheet_name, split["place"], f"Tax/Service ({split['tax_pct']}%)", round(tax_amount, 2), payer, ", ".join(all_names), round(tax_per_person, 2))

    await query.edit_message_text(f"✅ Saved {len(split['items'])} item(s) to *{sheet_name}*!", parse_mode="Markdown")
    return ConversationHandler.END


# --- even split path ---

async def even_payer(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.chat_data["even"]["payer"] = update.message.text.strip()
    await update.message.reply_text("What's the total amount?")
    return EVEN_TOTAL


async def even_names(update: Update, context: ContextTypes.DEFAULT_TYPE):
    names = [n.strip() for n in update.message.text.split(",") if n.strip()]
    if not names:
        await update.message.reply_text("Please give at least one name.")
        return EVEN_NAMES
    context.chat_data["even"]["names"] = names
    
    chat_id = update.effective_chat.id
    members = state.get_members(chat_id)
    
    text = "Who paid the bill?"
    if members:
        kb = []
        row = []
        for m in members:
            row.append(InlineKeyboardButton(m, callback_data=f"evenpayer:{m}"))
            if len(row) == 2:
                kb.append(row)
                row = []
        if row:
            kb.append(row)
        kb.append([InlineKeyboardButton("✍️ Type it manually...", callback_data="evenpayer:__manual__")])
        
        await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(kb))
    else:
        await update.message.reply_text(text)
        
    return EVEN_PAYER

async def even_payer_btn(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    payer = query.data.split(":", 1)[1]
    
    if payer == "__manual__":
        await query.edit_message_text("Please type the name of the person who paid:")
        return EVEN_PAYER
        
    context.chat_data["even"]["payer"] = payer
    await query.edit_message_text(f"Paid by: {payer}\n\nWhat's the total amount?")
    return EVEN_TOTAL


async def even_total(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        total = parse_price(update.message.text)
    except ValueError:
        await update.message.reply_text("Couldn't read that amount, try again e.g. 150000")
        return EVEN_TOTAL
    context.chat_data["even"]["total"] = total
    await update.message.reply_text("Give this bill a short name (e.g. 'Dinner at Resto X')")
    return EVEN_LABEL


async def even_label(update: Update, context: ContextTypes.DEFAULT_TYPE):
    even = context.chat_data["even"]
    even["label"] = update.message.text.strip()
    per_person = round(even["total"] / len(even["names"]), 2)
    even["per_person"] = per_person
    summary = (
        f"➗ *Even Split Summary*\n"
        f"{even['label']}\n"
        f"Total: {format_price(even['total'])}\n"
        f"Paid by: {even['payer']}\n"
        f"Split between: {', '.join(even['names'])}\n"
        f"Each owes: {format_price(per_person)}"
    )
    kb = [[InlineKeyboardButton("✅ Save to Sheet", callback_data="evensave"), InlineKeyboardButton("❌ Cancel", callback_data="evencancel")]]
    await update.message.reply_text(summary, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(kb))
    return EVEN_CONFIRM


async def even_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.data == "evencancel":
        context.chat_data.pop("even", None)
        await query.edit_message_text("Cancelled.")
        return ConversationHandler.END
    even = context.chat_data.pop("even")
    chat_id = update.effective_chat.id
    sheet_name = active_sheet_name(chat_id)
    sheets.append_expense(sheet_name, even["place"], even["label"], even["total"], even["payer"], ", ".join(even["names"]), even["per_person"])
    await query.edit_message_text(f"✅ Saved to *{sheet_name}*!", parse_mode="Markdown")
    return ConversationHandler.END


# ---------------------------------------------------------------------------
# Error handler
# ---------------------------------------------------------------------------

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    logger.error("Exception while handling an update:", exc_info=context.error)
    if isinstance(update, Update) and update.effective_message:
        await update.effective_message.reply_text(
            "⚠️ Something went wrong. Please try again, or check the terminal log."
        )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    app = ApplicationBuilder().token(config.TELEGRAM_BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", restricted(start_cmd)))
    app.add_handler(CommandHandler("help", restricted(start_cmd)))
    app.add_handler(CommandHandler("sheet", restricted(sheet_cmd)))
    app.add_handler(CommandHandler("list", restricted(list_cmd)))
    app.add_handler(CommandHandler("delete", restricted(delete_cmd)))
    app.add_handler(CallbackQueryHandler(delete_callback, pattern=r"^del:"))
    app.add_handler(CommandHandler("markpaid",   restricted(markpaid_cmd)))
    app.add_handler(CallbackQueryHandler(mk_pick_entry,   pattern=r"^mk1:"))
    app.add_handler(CallbackQueryHandler(mk_solo_toggle,  pattern=r"^mksolo:"))
    app.add_handler(CallbackQueryHandler(mk_person_toggle,pattern=r"^mkperson:"))
    app.add_handler(CallbackQueryHandler(mk_save,         pattern=r"^mksave:"))
    app.add_handler(CallbackQueryHandler(mk_back,         pattern=r"^mkback$"))
    app.add_handler(CallbackQueryHandler(mk_cancel,       pattern=r"^mkcancel$"))
    app.add_handler(CommandHandler("switch", restricted(switch_cmd)))
    app.add_handler(CallbackQueryHandler(switch_callback, pattern=r"^switch:"))
    # app.add_handler(CommandHandler("summary", restricted(summary_cmd)))
    app.add_handler(CommandHandler("setmembers", restricted(setmembers_cmd)))

    add_conv = ConversationHandler(
        entry_points=[CommandHandler("add", restricted(add_start))],
        states={
            ADD_PLACE: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_place)],
            ADD_ITEM: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_item)],
            ADD_PRICE: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_price)],
            ADD_PAID_BY: [
                CallbackQueryHandler(add_paid_by_button, pattern=r"^paidby:"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, add_paid_by_text),
            ],
            ADD_CONFIRM: [CallbackQueryHandler(add_confirm, pattern=r"^add_(save|cancel)$")],
        },
        fallbacks=[CommandHandler("cancel", cancel_cmd)],
    )
    app.add_handler(add_conv)

    summary_conv = ConversationHandler(
        entry_points=[CommandHandler("summary", restricted(summary_start))],
        states={
            SUMMARY_MODE: [CallbackQueryHandler(summary_mode, pattern=r"^sum_mode:")],
            SUMMARY_SCOPE: [CallbackQueryHandler(summary_scope, pattern=r"^sum_scope:")],
            SUMMARY_DATE_INPUT: [MessageHandler(filters.TEXT & ~filters.COMMAND, summary_date_input)]
        },
        fallbacks=[CommandHandler("cancel", cancel_cmd)],
    )
    app.add_handler(summary_conv)

    newevent_conv = ConversationHandler(
        entry_points=[CommandHandler("newevent", restricted(newevent_start))],
        states={NEWEVENT_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, newevent_name)]},
        fallbacks=[CommandHandler("cancel", cancel_cmd)],
    )
    app.add_handler(newevent_conv)

    split_conv = ConversationHandler(
        entry_points=[CommandHandler("split", restricted(split_start))],
        states={
            SPLIT_MODE: [CallbackQueryHandler(split_mode, pattern=r"^split_mode:")],
            SPLIT_PLACE: [MessageHandler(filters.TEXT & ~filters.COMMAND, split_place)],
            SPLIT_PAYER: [
                CallbackQueryHandler(split_payer_btn, pattern=r"^payer:"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, split_payer)
            ],
            SPLIT_ITEMS: [
                CommandHandler("done", split_done),
                MessageHandler(filters.TEXT & ~filters.COMMAND, split_item_line),
            ],
            SPLIT_TAX: [MessageHandler(filters.TEXT & ~filters.COMMAND, split_tax)],
            SPLIT_CONFIRM: [CallbackQueryHandler(split_confirm, pattern=r"^split(save|cancel)$")],
            EVEN_PLACE: [MessageHandler(filters.TEXT & ~filters.COMMAND, even_place)],
            EVEN_PAYER: [
                CallbackQueryHandler(even_payer_btn, pattern=r"^evenpayer:"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, even_payer)
            ],
            EVEN_NAMES: [MessageHandler(filters.TEXT & ~filters.COMMAND, even_names)],
            EVEN_TOTAL: [MessageHandler(filters.TEXT & ~filters.COMMAND, even_total)],
            EVEN_LABEL: [MessageHandler(filters.TEXT & ~filters.COMMAND, even_label)],
            EVEN_CONFIRM: [CallbackQueryHandler(even_confirm, pattern=r"^even(save|cancel)$")],
        },
        fallbacks=[CommandHandler("cancel", cancel_cmd)],
    )
    app.add_handler(split_conv)

    app.add_error_handler(error_handler)

    logger.info("Bot starting (polling)...")
    app.run_polling()


if __name__ == "__main__":
    main()