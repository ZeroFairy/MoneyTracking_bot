# 💸 Expense Tracker Telegram Bot

A Telegram bot that records expenses straight into a Google Sheet — including group
bill-splitting, calculating who owes whom, spending totals and category breakdowns,
recurring reminders, multiple events/trips as separate tabs, and an automatic new
sheet every month.

## What it does

| Command | What it does |
|---|---|
| `/add` | Log an expense step-by-step (place, item, price, paid by, category, date) |
| `/q` | Quick-add a solo expense in one line, always marked paid, e.g. `/q Indomaret Aqua 5000` |
| `/split` | Split a group bill — itemized (everyone ordered different things) or split evenly |
| `/edit` | Edit a field on an existing entry (date, place, item, price, paid by, shared, amount/person, category) |
| `/summary` | Calculate who owes whom — Smart (optimized) or Normal (straight), filterable by date |
| `/totals` | Total spending per person, filterable by date |
| `/overview` | Spending breakdown by category, filterable by date |
| `/list` | Browse all entries in the active sheet, paginated, numbered #1, #2, ... |
| `/delete` | Delete an entry (tap from a paginated list) |
| `/markpaid` | Mark an entry (or individual people on a split entry) as paid/unpaid |
| `/getlink` (alias `/sheet`) | Get the spreadsheet link |
| `/newevent` | Create a new sheet/tab for a trip or event, and switch to it |
| `/switch` | Switch between sheets (current month / any event) |
| `/setmembers` | Save group names for quick-select buttons |
| `/setreminder HH:MM Message` | Add a daily reminder at that time (WIB, UTC+7) |
| `/listreminders` | Show all your reminders |
| `/stopreminder` | Remove one reminder, or all of them |
| `/cancel` | Cancel whatever you're doing |

Every sheet (tab) has these columns: **Date & Time, Place, Buying List, Price, Paid By,
Shared By, Amount/Person, Picture, Status, Settled By, Category**.

By default, all entries go into a tab named after the current month (e.g. `Jun 2026`).
The first time you use the bot in a new month, that tab is created automatically.
Use `/newevent` to start a separate tab for something like a trip — entries go there
until you `/switch` back.

---

## 1. Setup overview

You'll need to:
1. Get a server to run the bot on (a small cloud VM works well — these instructions
   assume a Linux server, e.g. a Google Cloud Compute Engine instance)
2. Create a Telegram bot (via @BotFather) → get a **bot token**
3. Create a Google Cloud **service account** → get a **credentials.json** file
4. Create a Google Sheet and share it with that service account
5. Configure and run the bot, and keep it running 24/7

This takes about 20–30 minutes the first time.

---

## 2. Create the Telegram bot

1. Open Telegram, search for **@BotFather**, start a chat.
2. Send `/newbot`, give it a name and a username (must end in `bot`, e.g. `my_expense_tracker_bot`).
3. BotFather will reply with a **token** like `123456789:AAExampleToken...` — copy it, you'll need it soon.
4. Send your new bot a message (search its username and hit Start) so it can later message you back.

### Find your Telegram chat ID (optional, for privacy)
To restrict the bot to only you (recommended, since this is financial data):
1. Search for **@userinfobot** on Telegram, start it — it replies with your numeric chat ID.
2. For a group, add **@userinfobot** to the group temporarily, or check the bot logs after your
   first group message.

---

## 3. Create the Google service account (so the bot can edit a sheet without you logging in each time)

1. Go to https://console.cloud.google.com/ and create a new project (any name, e.g. "Expense Bot").
2. In the search bar, search **"Google Sheets API"** → click **Enable**.
3. In the left menu: **IAM & Admin → Service Accounts → Create Service Account**.
   - Name it anything (e.g. `expense-bot`), click through the defaults, click **Done**.
4. Click on the service account you just created → **Keys** tab → **Add Key → Create new key → JSON**.
   - This downloads a `.json` file. **Rename it to `credentials.json`** and upload it into your
     bot's project folder on the server (e.g. via `scp`, or the Cloud Console's SSH file upload).
5. Open `credentials.json` and find the `"client_email"` field — it looks like
   `expense-bot@your-project.iam.gserviceaccount.com`. Copy this email, you need it next.

---

## 4. Create the Google Sheet

1. Go to https://sheets.google.com and create a new blank spreadsheet (e.g. "Expenses").
2. Click **Share** (top right) → paste the service account email from step 3 → give it **Editor**
   access → Send (it's fine that it can't receive email, just confirm/share anyway).
3. Copy the Sheet's ID from the URL:

```
https://docs.google.com/spreadsheets/d/THIS_LONG_ID_HERE/edit
```

---

## 5. Set up the server

These steps assume a fresh Linux VM (e.g. Google Cloud Compute Engine) accessed via SSH.

```bash
# get the project onto the server (git clone, scp, or upload via Cloud Console SSH)
cd ~/expense_bot

# create and activate a virtual environment
python3 -m venv venv
source venv/bin/activate

# install dependencies
pip install -r requirements.txt
```

> **Important:** `requirements.txt` must include the `job-queue` extra, or reminders
> (`/setreminder`, `/stopreminder`) will fail with "Something went wrong":
> ```
> python-telegram-bot[job-queue]==21.4
> gspread==6.1.2
> python-dotenv==1.0.1
> ```
> Double check the extra is spelled exactly `job-queue` — a typo (e.g. `jon-queue`) will install
> silently without it, and `pip` will only print a warning, easy to miss.

Verify the install:
```bash
python -c "import apscheduler; print('apscheduler ok')"
```
This should print `apscheduler ok`. If it errors with `ModuleNotFoundError`, the extra above
wasn't installed — check `requirements.txt` and re-run `pip install -r requirements.txt`.

---

## 6. Configure the bot

1. In the project folder, copy `.env.example` to a new file named `.env`.
2. Open `.env` and fill in:

```
TELEGRAM_BOT_TOKEN=
GOOGLE_SHEETS_ID=<the sheet ID from step 4>
GOOGLE_CREDENTIALS_FILE=credentials.json
ALLOWED_CHAT_IDS=<your chat ID, optional>
```

3. Make sure `credentials.json` is in the same folder as `bot.py`.

---

## 7. Run the bot

With the venv active:
```bash
python bot.py
```

If you see `Bot starting (polling)...` with no errors, it's live — go message your bot on
Telegram and try `/start`. Stop it with `Ctrl+C` — but note that running it this way only lasts
as long as your SSH session stays open, which isn't enough for 24/7 use (see next section).

---

## 8. Keep it running 24/7 with systemd

On a Linux server, use `systemd` so the bot survives SSH disconnects, crashes, and reboots.

```bash
sudo nano /etc/systemd/system/expensebot.service
```

Paste (adjust the user and paths to match your setup):
```ini
[Unit]
Description=Expense Tracker Telegram Bot
After=network.target

[Service]
Type=simple
User=jordanjv453
WorkingDirectory=/home/jordanjv453/expense_bot
ExecStart=/home/jordanjv453/expense_bot/venv/bin/python bot.py
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
```

Then enable and start it:
```bash
sudo systemctl daemon-reload
sudo systemctl enable expensebot
sudo systemctl start expensebot
```

Useful commands going forward:
```bash
sudo systemctl status expensebot     # check it's running
sudo journalctl -u expensebot -f     # tail live logs
sudo systemctl restart expensebot    # after editing bot.py or requirements.txt
```

> If you ever ran the bot manually (`python bot.py` in a terminal, `nohup`, `screen`, `tmux`,
> etc.) before setting up systemd, stop that process first (`ps aux | grep bot.py` → `kill <PID>`)
> so you don't end up with two instances polling Telegram at once.

---

## 9. Usage tips

- **Prices**: you can type `25000`, `25.000`, `25,000`, or `25k` — all parsed the same way.
- **Personal vs Group Expenses**: Use `/add` or `/q` for simple, personal logging — `/q` always
  saves as ✅ Paid, since it's meant for things only you paid for. Use `/split` for any shared
  bills.
- **Quick-Select Buttons**: Type `/setmembers Name1, Name2, Name3` in any chat or group. The bot
  will remember those members and generate clickable buttons whenever it asks who paid for an
  expense!
- **Calculating Debts (`/summary`)**: choose Smart (nets everyone's balances into the fewest
  payments) or Normal (direct debts only), then filter by date:
  - `/summary` (all dates in the active sheet)
  - date range or single day, entered when prompted (`DD-MM-YYYY`)
- **Splitting a restaurant bill where everyone ordered different things**: use `/split` →
  "Itemized", then send lines like `Nasi Goreng | 25000 | Andi, Budi` (pipe-separated). Type
  `/done` when finished.
- **Splitting one shared total evenly**: use `/split` → "Split Evenly".
- **Tracking payments**: use `/markpaid` to mark a whole entry paid/unpaid, or — for split
  entries — toggle which individual people have settled up.
- **Backdating an expense**: `/add` asks "When was this?" with Today / Yesterday / type-a-date
  buttons. `/q` accepts an `@date` tag at the very end, e.g. `/q Indomaret Aqua 5000 @yesterday`
  or `/q Cafe Kopi 20000 #food @25-06-2026`.
- **Fixing the date on an existing entry**: `/edit` → pick the entry → **📅 Date**. Accepts a date
  alone (`25-06-2026`, keeps the current time) or a date with a specific time
  (`25-06-2026 14:30`), plus `today` / `yesterday` (optionally with a time too, e.g.
  `yesterday 20:15`).
- **Entry numbers**: `/list`, `/edit`, `/delete`, and `/markpaid` all number entries starting from
  `#1` for your first-ever entry in that sheet, in the order they were added.
- **Daily reminders**: `/setreminder 20:00 Log your expenses!` sets a daily nudge at 20:00 WIB.
  Add as many as you like; manage them with `/listreminders` and `/stopreminder`.
- **Trips/events**: `/newevent` makes a new tab and switches you to it. Use `/switch` any time to
  jump between the current month's tab and any event tab. New `/add`/`/split`/`/q` entries always
  go to whichever tab is currently active.
- **Multiple people, one bot**: anyone in `ALLOWED_CHAT_IDS` (or anyone, if you left it empty) can
  use the same bot — each Telegram chat (you 1-on-1, or a group chat) has its own "active sheet"
  and member list memory.

---

## Notes
- All timestamps (dates on new entries, reminder times) are calculated in **WIB (UTC+7)**
  regardless of what timezone the server itself is set to — most cloud VMs default to UTC, so
  this conversion happens in code rather than relying on the server's clock/timezone settings.
- All data lives in your own Google Sheet — you can open, edit, or chart it manually any time.
- `state.json` (created automatically next to `bot.py`) remembers which tab each chat is
  currently using, custom group members, and saved reminders — safe to delete if you want to
  reset everyone back to defaults (this will also cancel all active reminders).