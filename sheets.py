"""All Google Sheets read/write logic lives here."""
from datetime import datetime

import gspread

from config import GOOGLE_CREDENTIALS_FILE, GOOGLE_SHEETS_ID

HEADERS = ["Date & Time", "Place", "Buying List", "Price", "Paid By", "Shared By", "Amount/Person", "Picture", "Status", "Settled By"]

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


def current_month_sheet_name() -> str:
    return datetime.now().strftime("%b %Y")  # e.g. "Jun 2026"


def sanitize_sheet_name(name: str) -> str:
    bad = set('[]*/\\?:')
    cleaned = "".join(c for c in name if c not in bad).strip()
    return cleaned[:90] or "Untitled"


def get_or_create_worksheet(name: str):
    sh = _spreadsheet()
    name = sanitize_sheet_name(name)
    try:
        ws = sh.worksheet(name)
    except gspread.WorksheetNotFound:
        ws = sh.add_worksheet(title=name, rows=500, cols=len(HEADERS))
        ws.append_row(HEADERS)
        last_col = chr(ord("A") + len(HEADERS) - 1)
        ws.format(f"A1:{last_col}1", {"textFormat": {"bold": True}})
        ws.freeze(rows=1)
    return ws


def list_sheet_titles():
    return [ws.title for ws in _spreadsheet().worksheets()]


def append_expense(sheet_name, place, item, price, paid_by, shared_by, amount_per_person, picture="-", status="⏳ Unpaid"):
    ws = get_or_create_worksheet(sheet_name)
    ts = datetime.now().strftime("%d-%m-%Y %H:%M")
    from utils import format_price
    ws.append_row(
        [ts, place, item, format_price(price), paid_by, shared_by,
         format_price(amount_per_person) if amount_per_person is not None else "-", picture, status, ""],
        value_input_option="USER_ENTERED",
    )
    return ws


def get_recent(sheet_name, n=10):
    """Returns list of (row_number, row_values) for the last n data rows."""
    ws = get_or_create_worksheet(sheet_name)
    all_vals = ws.get_all_values()
    data_rows = all_vals[1:]  # skip header
    out = []
    for i, row in enumerate(data_rows):
        row_num = 2 + i  # header is row 1
        out.append((row_num, row))
    return out[-n:]


def delete_row(sheet_name, row_num: int):
    ws = get_or_create_worksheet(sheet_name)
    ws.delete_rows(row_num)


STATUS_COL = 9  # 1-indexed column number for "Status"

def toggle_paid_status(sheet_name: str, row_num: int) -> str:
    """Toggle the Status cell between ✅ Paid and ⏳ Unpaid. Returns the new status."""
    ws = get_or_create_worksheet(sheet_name)
    current = ws.cell(row_num, STATUS_COL).value or ""
    new_status = "✅ Paid" if current != "✅ Paid" else "⏳ Unpaid"
    ws.update_cell(row_num, STATUS_COL, new_status)
    return new_status


def set_paid_status_bulk(sheet_name: str, row_nums: list, status: str):
    """Set the Status column for multiple rows in one batch API call."""
    ws = get_or_create_worksheet(sheet_name)
    col_letter = chr(ord("A") + STATUS_COL - 1)  # STATUS_COL=9 → "I"
    updates = [
        {"range": f"{col_letter}{r}", "values": [[status]]}
        for r in row_nums
    ]
    ws.batch_update(updates)


SETTLED_COL = 10  # 1-indexed column number for "Settled By"


def get_settled_by(sheet_name: str, row_num: int) -> list:
    """Returns list of names who have settled this row (empty list if none)."""
    ws = get_or_create_worksheet(sheet_name)
    val = ws.cell(row_num, SETTLED_COL).value or ""
    return [n.strip() for n in val.split(",") if n.strip()]


def set_settled_by(sheet_name: str, row_num: int, names: list):
    """Write the settled-by list back to the sheet."""
    ws = get_or_create_worksheet(sheet_name)
    ws.update_cell(row_num, SETTLED_COL, ", ".join(names))


def sheet_url(sheet_name):
    ws = get_or_create_worksheet(sheet_name)
    return f"{_spreadsheet().url}#gid={ws.id}"


def spreadsheet_url():
    return _spreadsheet().url


def get_settlement_summary(sheet_name: str, start_date_str=None, end_date_str=None):
    ws = get_or_create_worksheet(sheet_name)
    data = ws.get_all_values()[1:]  # Skip the header row
    from utils import parse_price, format_price
    from datetime import datetime

    start_date = None
    end_date = None
    if start_date_str:
        start_date = datetime.strptime(start_date_str, "%d-%m-%Y").date()
    if end_date_str:
        end_date = datetime.strptime(end_date_str, "%d-%m-%Y").date()

    balances = {}
    for row in data:
        if len(row) < 7:
            continue

        # --- Skip fully paid entries ---
        status = row[8].strip() if len(row) > 8 else ""
        if status == "✅ Paid":
            continue

        # --- Date Filtering ---
        if start_date or end_date:
            try:
                row_date_str = row[0].split(" ")[0]
                row_date = datetime.strptime(row_date_str, "%d-%m-%Y").date()
                if start_date and row_date < start_date:
                    continue
                if end_date and row_date > end_date:
                    continue
            except Exception:
                continue

        try:
            price = parse_price(row[3])
            payer = row[4].strip()
            shared_by_str = row[5].strip()
            amt_str = row[6].strip()
            settled_str = row[9].strip() if len(row) > 9 else ""

            if not payer or not shared_by_str:
                continue

            sharers = [s.strip() for s in shared_by_str.split(",") if s.strip()]
            settled_names = set(s.strip() for s in settled_str.split(",") if s.strip())

            # Only count sharers who have NOT settled yet
            outstanding_sharers = [s for s in sharers if s not in settled_names]

            if not outstanding_sharers:
                # Everyone has individually settled — skip this row entirely
                continue

            if amt_str != "-":
                amt_per_person = parse_price(amt_str)
                outstanding_total = amt_per_person * len(outstanding_sharers)
            else:
                outstanding_total = price

            # Payer is owed back the outstanding amount only
            balances[payer] = balances.get(payer, 0.0) + outstanding_total

            # Deduct each outstanding sharer's share
            if amt_str != "-":
                for s in outstanding_sharers:
                    balances[s] = balances.get(s, 0.0) - amt_per_person

        except ValueError:
            continue

    debtors = []
    creditors = []
    for name, amount in balances.items():
        if amount < -10.0:
            debtors.append([name, abs(amount)])
        elif amount > 10.0:
            creditors.append([name, amount])

    debtors.sort(key=lambda x: x[1], reverse=True)
    creditors.sort(key=lambda x: x[1], reverse=True)

    transactions = []
    i, j = 0, 0
    while i < len(debtors) and j < len(creditors):
        debtor_name, debt_amt = debtors[i]
        cred_name, cred_amt = creditors[j]
        settle_amt = min(debt_amt, cred_amt)
        transactions.append(f"💸 *{debtor_name}* pays *{cred_name}*: {format_price(settle_amt)}")
        debtors[i][1] -= settle_amt
        creditors[j][1] -= settle_amt
        if debtors[i][1] < 10.0: i += 1
        if creditors[j][1] < 10.0: j += 1

    return transactions


def get_raw_summary(sheet_name: str, start_date_str=None, end_date_str=None):
    ws = get_or_create_worksheet(sheet_name)
    data = ws.get_all_values()[1:]
    from utils import parse_price, format_price
    from datetime import datetime

    start_date = None
    end_date = None
    if start_date_str:
        start_date = datetime.strptime(start_date_str, "%d-%m-%Y").date()
    if end_date_str:
        end_date = datetime.strptime(end_date_str, "%d-%m-%Y").date()

    direct_debts = {}
    for row in data:
        if len(row) < 7:
            continue

        # --- Skip fully paid entries ---
        status = row[8].strip() if len(row) > 8 else ""
        if status == "✅ Paid":
            continue

        # --- Date Filtering ---
        if start_date or end_date:
            try:
                row_date_str = row[0].split(" ")[0]
                row_date = datetime.strptime(row_date_str, "%d-%m-%Y").date()
                if start_date and row_date < start_date:
                    continue
                if end_date and row_date > end_date:
                    continue
            except Exception:
                continue

        try:
            payer = row[4].strip()
            shared_by_str = row[5].strip()
            amt_str = row[6].strip()
            settled_str = row[9].strip() if len(row) > 9 else ""

            if not payer or not shared_by_str or amt_str == "-":
                continue

            amt_per_person = parse_price(amt_str)
            sharers = [s.strip() for s in shared_by_str.split(",") if s.strip()]
            settled_names = set(s.strip() for s in settled_str.split(",") if s.strip())

            for person in sharers:
                # Skip if this person has already individually settled
                if person in settled_names:
                    continue
                if person.lower() != payer.lower():
                    debt_pair = (person, payer)
                    direct_debts[debt_pair] = direct_debts.get(debt_pair, 0.0) + amt_per_person

        except ValueError:
            continue

    transactions = []
    for (debtor, creditor), amount in direct_debts.items():
        if amount > 10.0:
            transactions.append(f"💸 *{debtor}* pays *{creditor}*: {format_price(amount)}")

    return transactions