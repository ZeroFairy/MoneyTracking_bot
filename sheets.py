"""All Google Sheets read/write logic lives here."""
from datetime import datetime

import gspread

from config import GOOGLE_CREDENTIALS_FILE, GOOGLE_SHEETS_ID

HEADERS = ["Date & Time", "Place", "Buying List", "Price", "Paid By", "Shared By", "Amount/Person", "Picture"]

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


def append_expense(sheet_name, place, item, price, paid_by, shared_by, amount_per_person, picture="-"):
    ws = get_or_create_worksheet(sheet_name)
    ts = datetime.now().strftime("%d-%m-%Y %H:%M")
    from utils import format_price
    ws.append_row(
        [ts, place, item, format_price(price), paid_by, shared_by,
         format_price(amount_per_person) if amount_per_person is not None else "-", picture],
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


def sheet_url(sheet_name):
    ws = get_or_create_worksheet(sheet_name)
    return f"{_spreadsheet().url}#gid={ws.id}"


def spreadsheet_url():
    return _spreadsheet().url


def get_settlement_summary(sheet_name: str):
    ws = get_or_create_worksheet(sheet_name)
    data = ws.get_all_values()[1:]  # Skip the header row
    from utils import parse_price, format_price

    balances = {}
    for row in data:
        if len(row) < 7: 
            continue  # Skip broken/empty rows
            
        try:
            price = parse_price(row[3])
            payer = row[4].strip()
            shared_by_str = row[5].strip()
            amt_str = row[6].strip()

            if not payer or not shared_by_str: 
                continue

            # 1. Give the payer back their money
            balances[payer] = balances.get(payer, 0.0) + price

            # 2. Deduct the share from everyone involved
            sharers = [s.strip() for s in shared_by_str.split(",")]
            if amt_str != "-":
                amt_per_person = parse_price(amt_str)
                for s in sharers:
                    balances[s] = balances.get(s, 0.0) - amt_per_person
        except ValueError:
            # Skip any rows that have text where numbers should be
            continue

    # 3. Split people into who owes money (debtors) and who gets paid (creditors)
    debtors = []
    creditors = []
    for name, amount in balances.items():
        if amount < -10.0:  # Using 10 to ignore tiny decimal rounding errors
            debtors.append([name, abs(amount)])
        elif amount > 10.0:
            creditors.append([name, amount])

    # Sort them so big debts are settled first
    debtors.sort(key=lambda x: x[1], reverse=True)
    creditors.sort(key=lambda x: x[1], reverse=True)

    # 4. Figure out exactly who pays whom
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