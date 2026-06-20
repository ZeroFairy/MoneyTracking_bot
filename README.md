# 💸 Expense Tracker Telegram Bot

A Telegram bot that records expenses straight into a Google Sheet — including group
bill-splitting, calculating who owes whom, multiple events/trips as separate tabs, and an automatic new sheet every month.

## What it does

| Command | What it does |
|---|---|
| `/add` | Log a personal expense (place, item, price, paid by) |
| `/split` | Split a group bill — itemized (everyone ordered different things) or split evenly |
| `/summary` | Calculate who owes whom (supports filtering by date) |
| `/list` | Show the last 10 entries in the active sheet |
| `/delete` | Delete an entry (tap from a list) |
| `/sheet` | Get the spreadsheet link |
| `/newevent` | Create a new sheet/tab for a trip or event, and switch to it |
| `/switch` | Switch between sheets (current month / any event) |
| `/cancel` | Cancel whatever you're doing |

Every sheet (tab) has these columns: **Date & Time, Place, Buying List, Price, Paid By, Shared By, Amount/Person**.

By default, all entries go into a tab named after the current month (e.g. `Jun 2026`).
The first time you use the bot in a new month, that tab is created automatically.
Use `/newevent` to start a separate tab for something like a trip — entries go there
until you `/switch` back.

---

## 1. Setup overview

You'll need to:
1. Install Python on your PC
2. Create a Telegram bot (via @BotFather) → get a **bot token**
3. Create a Google Cloud **service account** → get a **credentials.json** file
4. Create a Google Sheet and share it with that service account
5. Configure and run the bot

This takes about 15–20 minutes the first time, then it's a one-click run after that.

---

## 2. Install Python

1. Download Python 3.11+ from https://www.python.org/downloads/
2. Run the installer. **Check the box "Add python.exe to PATH"** before clicking Install.
3. Confirm it worked: open **PowerShell** (search "PowerShell" in the Start menu) and run: 

```python --version```

---

## 3. Create the Telegram bot

1. Open Telegram, search for **@BotFather**, start a chat.
2. Send `/newbot`, give it a name and a username (must end in `bot`, e.g. `my_expense_tracker_bot`).
3. BotFather will reply with a **token** like `123456789:AAExampleToken...` — copy it, you'll need it soon.
4. Send your new bot a message (search its username and hit Start) so it can later message you back.

### Find your Telegram chat ID (optional, for privacy)
To restrict the bot to only you (recommended, since this is financial data):
1. Search for **@userinfobot** on Telegram, start it — it replies with your numeric chat ID.
2. For a group, add **@userinfobot** to the group temporarily, or check the bot logs after your first group message (it'll be in the bot's terminal output if you add logging — or simplest: just message your bot and check the `chat_id` printed if an error occurs, or skip this and leave `ALLOWED_CHAT_IDS` empty while testing).

---

## 4. Create the Google service account (so the bot can edit a sheet without you logging in each time)

1. Go to https://console.cloud.google.com/ and create a new project (any name, e.g. "Expense Bot").
2. In the search bar, search **"Google Sheets API"** → click **Enable**.
3. In the left menu: **IAM & Admin → Service Accounts → Create Service Account**.
- Name it anything (e.g. `expense-bot`), click through the defaults, click **Done**.
4. Click on the service account you just created → **Keys** tab → **Add Key → Create new key → JSON**.
- This downloads a `.json` file. **Rename it to `credentials.json`** and move it into your bot's project folder.
5. Open `credentials.json` in Notepad and find the `"client_email"` field — it looks like
`expense-bot@your-project.iam.gserviceaccount.com`. Copy this email, you need it next.

---

## 5. Create the Google Sheet

1. Go to https://sheets.google.com and create a new blank spreadsheet (e.g. "Expenses").
2. Click **Share** (top right) → paste the service account email from step 4 → give it **Editor** access → Send (it's fine that it can't receive email, just confirm/share anyway).
3. Copy the Sheet's ID from the URL:

```https://docs.google.com/spreadsheets/d/THIS_LONG_ID_HERE/edit```

---

## 6. Configure the bot

1. In the project folder, copy `.env.example` to a new file named `.env`.
2. Open `.env` in Notepad and fill in:

```
TELEGRAM_BOT_TOKEN=
GOOGLE_SHEETS_ID=<the sheet ID from step 5>
GOOGLE_CREDENTIALS_FILE=credentials.json
ALLOWED_CHAT_IDS=<your chat ID, optional>
```

3. Make sure `credentials.json` is in the same folder as `bot.py`.

---

## 7. Install dependencies and run

Open PowerShell in the project folder (Shift + right-click inside the folder → "Open PowerShell window here"), then:

```
pip install -r requirements.txt
python bot.py
```

If you see `Bot starting (polling)...` with no errors, it's live — go message your bot on Telegram and try `/start`.

For everyday use, just double-click **`run_bot.bat`**.

---

## 8. Keep it running 24/7 on Windows

The bot only works while `python bot.py` (or `run_bot.bat`) is running. Options:

- **Simplest:** leave the PowerShell window / `run_bot.bat` window open and minimized.
- **Auto-start on login:** press `Win + R`, type `shell:startup`, hit Enter — this opens your
  Startup folder. Create a shortcut to `run_bot.bat` there, and it'll launch every time you log in.
- **Run hidden (no visible window):** rename `run_bot.bat`'s `python` call to `pythonw` (the windowless
  Python interpreter) — edit `run_bot.bat` and replace `python bot.py` with `pythonw bot.py`.

---

## 9. Usage tips

- **Prices**: you can type `25000`, `25.000`, `25,000`, or `25k` — all parsed the same way.
- **Personal vs Group Expenses**: Use `/add` for simple, personal logging. Use `/split` for any shared bills.
- **Calculating Debts (`/summary`)**: The bot will pair up debtors and creditors automatically. You can filter the summary by dates (using `DD-MM-YYYY` format):
  - `/summary` (calculates all dates in the active sheet)
  - `/summary 20-06-2026` (calculates for a single day)
  - `/summary 15-06-2026 20-06-2026` (calculates a date range)
- **Splitting a restaurant bill where everyone ordered different things**: use `/split` → "Itemized",
  then send lines like `Nasi Goreng | 25000 | Andi, Budi` (pipe-separated). Type `/done` when finished.
- **Splitting one shared total evenly**: use `/split` → "Split Evenly".
- **Trips/events**: `/newevent` makes a new tab and switches you to it. Use `/switch` any time to
  jump between the current month's tab and any event tab. New `/add`/`/split` entries always go to
  whichever tab is currently active.
- **Multiple people, one bot**: anyone in `ALLOWED_CHAT_IDS` (or anyone, if you left it empty) can use
  the same bot — each Telegram chat (you 1-on-1, or a group chat) has its own "active sheet" memory.

---

## Notes
- All data lives in your own Google Sheet — you can open, edit, or chart it manually any time.
- `state.json` (created automatically next to `bot.py`) just remembers which tab each chat is
  currently using — safe to delete if you want to reset everyone back to "current month".