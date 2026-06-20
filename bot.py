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

HELP_TEXT = (
    "👋 *Expense Tracker Bot*\n\n"
    "/add — add one expense\n"
    "/split — split a group bill (itemized or evenly)\n"
    "/list — show recent entries\n"
    "/delete — delete an entry\n"
    "/sheet — get the spreadsheet link\n"
    "/newevent — create a new sheet for a trip/event\n"
    "/switch — switch between sheets\n"
    "/summary — summaries bill\n"
    "/cancel — cancel the current action\n\n"
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
    lines = [f"🧾 Last entries in *{name}*:"]
    for row_num, row in recent:
        item = row[1] if len(row) > 1 else "?"
        price = row[2] if len(row) > 2 else "?"
        lines.append(f"#{row_num}: {item} — {price}")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def summary_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    sheet_name = active_sheet_name(chat_id)
    
    await update.message.reply_text(f"📊 Calculating settlement for *{sheet_name}*...", parse_mode="Markdown")

    try:
        transactions = sheets.get_settlement_summary(sheet_name)
        if not transactions:
            await update.message.reply_text("🎉 Everybody is settled up! No one owes anything.")
        else:
            text = f"🧾 *Settlement for {sheet_name}*\n\n" + "\n".join(transactions)
            await update.message.reply_text(text, parse_mode="Markdown")
    except Exception as e:
        logger.error(f"Summary error: {e}")
        await update.message.reply_text("⚠️ Could not calculate summary. Make sure the sheet data is formatted correctly.")


async def cancel_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.chat_data.clear()
    await update.message.reply_text("Cancelled.")
    return ConversationHandler.END


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

# async def add_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
#     context.chat_data["draft"] = {}
#     await update.message.reply_text("📝 What did you buy?")
#     return ADD_ITEM

# async def add_place(update: Update, context: ContextTypes.DEFAULT_TYPE):
#     context.chat_data["draft"]["place"] = update.message.text.strip()
#     await update.message.reply_text("📍 Where did you spend this? (e.g. Indomaret, Steam, Resto X)")
#     return ADD_PLACE

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
    context.chat_data["draft"]["paid_by"] = name
    await query.edit_message_text(f"🙋 Paid by: {name}")
    await query.message.reply_text("👥 Who's sharing this? (comma-separated names, or just 'me')")
    return ADD_SHARED_BY


async def add_paid_by_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.chat_data["draft"]["paid_by"] = update.message.text.strip()
    await update.message.reply_text("👥 Who's sharing this? (comma-separated names, or just 'me')")
    return ADD_SHARED_BY


async def add_shared_by(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if text.lower() == "me":
        names = [update.effective_user.first_name]
    else:
        names = [n.strip() for n in text.split(",") if n.strip()]
    if not names:
        await update.message.reply_text("Please tell me at least one name.")
        return ADD_SHARED_BY

    draft = context.chat_data["draft"]
    draft["shared_by"] = names
    draft["amount_per_person"] = round(draft["price"] / len(names), 2)

    summary = (
        f"📋 *Confirm entry*\n"
        f"Place: {draft['place']}\n"
        f"Item: {draft['item']}\n"
        f"Price: {format_price(draft['price'])}\n"
        f"Paid by: {draft['paid_by']}\n"
        f"Shared by: {', '.join(names)}\n"
        f"Per person: {format_price(draft['amount_per_person'])}\n\n"
        f"Sheet: {active_sheet_name(update.effective_chat.id)}"
    )
    kb = [[InlineKeyboardButton("✅ Save", callback_data="add_save"), InlineKeyboardButton("❌ Cancel", callback_data="add_cancel")]]
    await update.message.reply_text(summary, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(kb))
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
    await update.message.reply_text("Who paid the bill overall?")
    return SPLIT_PAYER

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
    await update.message.reply_text("Who paid the bill?")
    return EVEN_PAYER


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
    app.add_handler(CommandHandler("switch", restricted(switch_cmd)))
    app.add_handler(CallbackQueryHandler(switch_callback, pattern=r"^switch:"))
    app.add_handler(CommandHandler("summary", restricted(summary_cmd)))

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
            ADD_SHARED_BY: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_shared_by)],
            ADD_CONFIRM: [CallbackQueryHandler(add_confirm, pattern=r"^add_(save|cancel)$")],
        },
        fallbacks=[CommandHandler("cancel", cancel_cmd)],
    )
    app.add_handler(add_conv)

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
            SPLIT_PAYER: [MessageHandler(filters.TEXT & ~filters.COMMAND, split_payer)],
            SPLIT_ITEMS: [
                CommandHandler("done", split_done),
                MessageHandler(filters.TEXT & ~filters.COMMAND, split_item_line),
            ],
            SPLIT_TAX: [MessageHandler(filters.TEXT & ~filters.COMMAND, split_tax)],
            SPLIT_CONFIRM: [CallbackQueryHandler(split_confirm, pattern=r"^split(save|cancel)$")],
            EVEN_PLACE: [MessageHandler(filters.TEXT & ~filters.COMMAND, even_place)],
            EVEN_PAYER: [MessageHandler(filters.TEXT & ~filters.COMMAND, even_payer)],
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
