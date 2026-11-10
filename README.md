# 💸 Expense Tracker Telegram Bot

A Telegram bot that records expenses straight into a Google Sheet — including group bill-splitting, calculating who owes whom, spending totals and category breakdowns, recurring reminders, multiple events/trips as separate tabs, and an automatic new sheet every month.

## What it does

| Command | What it does | 
| ----- | ----- | 
| `/add` | Log an expense step-by-step (place, item, price, paid by, category, date) | 
| `/q` | Quick-add a solo expense in one line, always marked paid, e.g. `/q Indomaret Aqua 5000` | 
| `/split` | Split a group bill — itemized (everyone ordered different things) or split evenly | 
| `/edit` | Edit a field on an existing entry (date, place, item, price, paid by, shared, amount/person, category) | 
| `/summary` | Calculate who owes whom — Smart (optimized) or Normal (straight), filterable by date | 
| `/totals` | Total spending per person, filterable by date | 
| `/overview` | Spending breakdown by category, filterable by date | 
| `/recap` | Quick snapshot: total entries, grand total, biggest expense, top category, and top spender | 
| `/budgets` | See all active budgets and current usage with progress bars | 
| `/setbudget` | Set a monthly spending cap per category or overall, with 80%/100% alerts | 
| `/sortsheet` (alias `/sort`) | Re-sort the active sheet chronologically by date & time | 
| `/list` | Browse all entries in the active sheet, paginated, numbered #1, #2, ... | 
| `/delete` | Delete an entry (tap from a paginated list) | 
| `/undo` | Remove the last entry quickly | 
| `/markpaid` | Mark an entry (or individual people on a split entry) as paid/unpaid | 
| `/getlink` (alias `/sheet`) | Get the spreadsheet link | 
| `/newevent` | Create a new sheet/tab for a trip or event, and switch to it | 
| `/switch` | Switch between sheets (current month / any event) | 
| `/setmembers` | Save group names for quick-select buttons | 
| `/setreminder HH:MM Message` | Add a daily reminder at that time (WIB, UTC+7) | 
| `/listreminders` | Show all your reminders | 
| `/stopreminder` | Remove one reminder, or all of them | 
| `/cancel` | Cancel whatever you're doing | 

Every sheet (tab) has these columns: **Date & Time, Place, Buying List, Price, Paid By, Shared By, Amount/Person, Picture, Status, Settled By, Category**.

By default, all entries go into a tab named after the current month (e.g. `Jun 2026`). The first time you use the bot in a new month, that tab is created automatically. Use `/newevent` to start a separate tab for something like a trip — entries go there until you `/switch` back.

## 1. Setup overview

You'll need to:

1. Get a server or lightweight container platform to run the bot on

2. Create a Telegram bot (via @BotFather) → get a **bot token**

3. Create a Google Cloud **service account** → get a **credentials.json** file

4. Create a Google Sheet and share it with that service account

5. Configure and run the bot

This takes about 20–30 minutes the first time.

## 2. Create the Telegram bot

1. Open Telegram, search for **@BotFather**, start a chat.

2. Send `/newbot`, give it a name and a username (must end in `bot`, e.g. `my_expense_tracker_bot`).

3. BotFather will reply with a token like `123456789:AAExampleToken...` — copy it, you'll need it soon.

4. Send your new bot a message (search its username and hit Start) so it can later message you back.

### Find your Telegram chat ID (optional, for privacy)

To restrict the bot to only you (recommended, since this is financial data):

1. Search for **@userinfobot** on Telegram, start it — it replies with your numeric chat ID.

2. For a group, add **@userinfobot** to the group temporarily, or check the bot logs after your first group message.

## 3. Create the Google service account

1. Go to https://console.cloud.google.com/ and create a new project (any name, e.g. "Expense Bot").

2. In the search bar, search **"Google Sheets API"** → click **Enable**.

3. In the left menu: **IAM & Admin → Service Accounts → Create Service Account**. Name it anything (e.g. `expense-bot`), click through, click **Done**.

4. Click on the service account you just created → **Keys** tab → **Add Key → Create new key → JSON**. This downloads a file. **Rename it to `credentials.json`** and upload it into your project folder.

5. Open `credentials.json` and copy the `"client_email"` field (looks like `expense-bot@your-project.iam.gserviceaccount.com`).

## 4. Create the Google Sheet

1. Go to https://sheets.google.com and create a new blank spreadsheet (e.g. "Expenses").

2. Click **Share** (top right) → paste your service account email → give it **Editor** access → Send.

3. Copy the Sheet's ID from the URL:
   `https://docs.google.com/spreadsheets/d/THIS_LONG_ID_HERE/edit`

## 5. Configure the bot

1. Create a file named `.env` in your project root.

2. Fill in your keys:

```
TELEGRAM_BOT_TOKEN=your_token_here
GOOGLE_SHEETS_ID=your_sheet_id_here
GOOGLE_CREDENTIALS_FILE=credentials.json
ALLOWED_CHAT_IDS=your_chat_id_optional

```

3. Make sure `credentials.json` is in the same folder as `bot.py`.

## 6. Install dependencies & run

```
# create and activate a virtual environment
python3 -m venv venv
source venv/bin/activate

# install dependencies (including job queue for reminders)
pip install -r requirements.txt

# run the bot
python bot.py

```

> **Important:** `requirements.txt` must include the `job-queue` extra, or reminders (`/setreminder`, `/stopreminder`) will fail:
>
> ```
> python-telegram-bot[job-queue]==21.4
> gspread==6.1.2
> python-dotenv==1.0.1
> 
> ```

## 7. Usage tips

* **Prices**: you can type `25000`, `25.000`, `25,000`, or `25k` — all parsed the same way.

* **Personal vs Group Expenses**: Use `/add` or `/q` for simple, personal logging — `/q` always saves as ✅ Paid. Use `/split` for shared bills.

* **Quick-Select Buttons**: Type `/setmembers Name1, Name2, Name3` in any chat or group to generate quick-select buttons.

* **Calculating Debts (`/summary`)**: choose Smart (optimized balances) or Normal (direct debts), filterable by date.

* **Splitting a restaurant bill**: use `/split` → "Itemized", send lines like `Nasi Goreng | 25000 | Andi, Budi`, and type `/done` when finished.

* **Splitting evenly**: use `/split` → "Split Evenly".

* **Tracking payments**: use `/markpaid` to mark entries or individual people as paid/unpaid.

* **Backdating**: `/q` accepts an `@date` tag at the end (e.g., `/q Indomaret Aqua 5000 @yesterday` or `@25-06-2026`).

* **Editing entries**: `/edit` lets you modify any field on an existing entry.

* **Daily reminders**: `/setreminder 20:00 Log your expenses!` sets a daily nudge in WIB. Manage them with `/listreminders` and `/stopreminder`.

* **Trips/events**: `/newevent` creates a new tab and switches you to it. Use `/switch` to jump between tabs.

## Notes

* All timestamps and reminder times run in **WIB (UTC+7)**.

* **Cloud-Backed State:** All active sheets, custom group members, and saved reminders are stored securely inside dedicated tabs in your Google Sheet (`BotState_ActiveSheets`, `BotState_Members`, `BotState_Reminders`, `BotState_KnownChats`). Your bot is completely stateless locally, meaning if your hosting container restarts or resets, **zero data or configurations will be lost**.

* All data lives in your own Google Sheet — you can open, edit, or chart it manually any time.