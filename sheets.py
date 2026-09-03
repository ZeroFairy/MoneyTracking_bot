"""All Google Sheets read/write logic lives here."""
from datetime import datetime

import gspread

from config import GOOGLE_CREDENTIALS_FILE, GOOGLE_SHEETS_ID

HEADERS = ["Date & Time", "Place", "Buying List", "Price", "Paid By", "Shared By", "Amount/Person", "Picture", "Status", "Settled By", "Category"]

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
    return datetime.now().strftime("%b %Y")


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
        ws = sh.add_worksheet(title=name, rows=1000, cols=len(HEADERS))
        ws.append_row(HEADERS)
        last_col = chr(ord("A") + len(HEADERS) - 1)
        ws.format(f"A1:{last_col}1", {"textFormat": {"bold": True}})
        ws.freeze(rows=1)
    return ws


def list_sheet_titles():
    return [ws.title for ws in _spreadsheet().worksheets()]


def append_expense(sheet_name, place, item, price, paid_by, shared_by, amount_per_person, picture="-", status="⏳ Unpaid", category="", date_str=None):
    ws = get_or_create_worksheet(sheet_name)
    ts = date_str if date_str else datetime.now().strftime("%d-%m-%Y %H:%M")
    from utils import format_price
    ws.append_row(
        [ts, place, item, format_price(price), paid_by, shared_by,
         format_price(amount_per_person) if amount_per_person is not None else "-",
         picture, status, "", category],
        value_input_option="USER_ENTERED",
    )
    return ws


def get_all_expenses(sheet_name):
    """Returns ALL (row_number, row_values) pairs, newest first, skipping empty rows."""
    ws = get_or_create_worksheet(sheet_name)
    all_vals = ws.get_all_values()
    data_rows = all_vals[1:]  # skip header
    out = [(2 + i, row) for i, row in enumerate(data_rows) if any(row)]
    return list(reversed(out))  # newest first


def get_recent(sheet_name, n=10):
    """Returns last n entries (oldest→newest) for legacy use."""
    all_rows = get_all_expenses(sheet_name)
    return list(reversed(all_rows[:n]))


def delete_row(sheet_name, row_num: int):
    ws = get_or_create_worksheet(sheet_name)
    ws.delete_rows(row_num)


STATUS_COL  = 9   # 1-indexed — "Status"
SETTLED_COL  = 10  # 1-indexed — "Settled By"
CATEGORY_COL = 11  # 1-indexed — "Category"


def toggle_paid_status(sheet_name: str, row_num: int) -> str:
    ws = get_or_create_worksheet(sheet_name)
    current = ws.cell(row_num, STATUS_COL).value or ""
    new_status = "✅ Paid" if current != "✅ Paid" else "⏳ Unpaid"
    ws.update_cell(row_num, STATUS_COL, new_status)
    return new_status


def set_paid_status_bulk(sheet_name: str, row_nums: list, status: str):
    ws = get_or_create_worksheet(sheet_name)
    col_letter = chr(ord("A") + STATUS_COL - 1)
    updates = [{"range": f"{col_letter}{r}", "values": [[status]]} for r in row_nums]
    ws.batch_update(updates)


def get_settled_by(sheet_name: str, row_num: int) -> list:
    ws = get_or_create_worksheet(sheet_name)
    val = ws.cell(row_num, SETTLED_COL).value or ""
    return [n.strip() for n in val.split(",") if n.strip()]


def set_settled_by(sheet_name: str, row_num: int, names: list):
    ws = get_or_create_worksheet(sheet_name)
    ws.update_cell(row_num, SETTLED_COL, ", ".join(names))


# Column numbers for editable fields (1-indexed)
FIELD_COLS = {
    "date":       1,
    "place":      2,
    "item":       3,
    "price":      4,
    "paid_by":    5,
    "shared":     6,
    "per_person": 7,
}


def update_expense_field(sheet_name: str, row_num: int, field: str, value: str):
    """Update a single field in an expense row."""
    col = FIELD_COLS.get(field)
    if col is None:
        raise ValueError(f"Unknown field: {field}")
    ws = get_or_create_worksheet(sheet_name)
    ws.update_cell(row_num, col, value)


def sheet_url(sheet_name):
    ws  = get_or_create_worksheet(sheet_name)
    sid = _spreadsheet().id
    return f"https://docs.google.com/spreadsheets/d/{sid}/edit#gid={ws.id}"


def spreadsheet_url():
    sid = _spreadsheet().id
    return f"https://docs.google.com/spreadsheets/d/{sid}/edit"


def get_settlement_summary(sheet_name: str, start_date_str=None, end_date_str=None):
    ws = get_or_create_worksheet(sheet_name)
    data = ws.get_all_values()[1:]
    from utils import parse_price, format_price

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
        # Skip fully paid entries
        status = row[8].strip() if len(row) > 8 else ""
        if status == "✅ Paid":
            continue
        # Date filter
        if start_date or end_date:
            try:
                row_date = datetime.strptime(row[0].split(" ")[0], "%d-%m-%Y").date()
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
            outstanding = [s for s in sharers if s not in settled_names]
            if not outstanding:
                continue
            amt_per_person = parse_price(amt_str) if amt_str != "-" else price / len(sharers)
            outstanding_total = amt_per_person * len(outstanding)
            balances[payer] = balances.get(payer, 0.0) + outstanding_total
            for s in outstanding:
                balances[s] = balances.get(s, 0.0) - amt_per_person
        except ValueError:
            continue

    debtors   = [[n, abs(a)] for n, a in balances.items() if a < -10.0]
    creditors = [[n, a]      for n, a in balances.items() if a >  10.0]
    debtors.sort(key=lambda x: x[1], reverse=True)
    creditors.sort(key=lambda x: x[1], reverse=True)

    transactions = []
    i = j = 0
    while i < len(debtors) and j < len(creditors):
        amt = min(debtors[i][1], creditors[j][1])
        transactions.append(f"💸 *{debtors[i][0]}* pays *{creditors[j][0]}*: {format_price(amt)}")
        debtors[i][1]   -= amt
        creditors[j][1] -= amt
        if debtors[i][1]   < 10.0: i += 1
        if creditors[j][1] < 10.0: j += 1
    return transactions


def get_raw_summary(sheet_name: str, start_date_str=None, end_date_str=None):
    ws = get_or_create_worksheet(sheet_name)
    data = ws.get_all_values()[1:]
    from utils import parse_price, format_price

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
        status = row[8].strip() if len(row) > 8 else ""
        if status == "✅ Paid":
            continue
        if start_date or end_date:
            try:
                row_date = datetime.strptime(row[0].split(" ")[0], "%d-%m-%Y").date()
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
                if person in settled_names:
                    continue
                if person.lower() != payer.lower():
                    k = (person, payer)
                    direct_debts[k] = direct_debts.get(k, 0.0) + amt_per_person
        except ValueError:
            continue

    from utils import format_price
    return [
        f"💸 *{d}* pays *{c}*: {format_price(a)}"
        for (d, c), a in direct_debts.items() if a > 10.0
    ]


def get_person_totals(sheet_name: str, start_date_str=None, end_date_str=None):
    """Total spending per person regardless of paid/unpaid status.

    Returns a dict: {name: total_spent} and the grand total.
    """
    ws = get_or_create_worksheet(sheet_name)
    data = ws.get_all_values()[1:]
    from utils import parse_price
    from datetime import datetime

    start_date = end_date = None
    if start_date_str:
        start_date = datetime.strptime(start_date_str, "%d-%m-%Y").date()
    if end_date_str:
        end_date = datetime.strptime(end_date_str, "%d-%m-%Y").date()

    totals = {}
    for row in data:
        if len(row) < 7 or not any(row):
            continue
        # Date filter
        if start_date or end_date:
            try:
                row_date = datetime.strptime(row[0].split(" ")[0], "%d-%m-%Y").date()
                if start_date and row_date < start_date:
                    continue
                if end_date and row_date > end_date:
                    continue
            except Exception:
                continue
        try:
            shared_by_str = row[5].strip()
            amt_str       = row[6].strip()
            if not shared_by_str or amt_str == "-":
                continue
            amt_per_person = parse_price(amt_str)
            for person in [s.strip() for s in shared_by_str.split(",") if s.strip()]:
                totals[person] = totals.get(person, 0.0) + amt_per_person
        except ValueError:
            continue

    return totals


def get_category_overview(sheet_name: str, start_date_str=None, end_date_str=None):
    """Total spending broken down by category.
    Returns {category: {"total": float, "count": int}} sorted by total desc.
    """
    ws = get_or_create_worksheet(sheet_name)
    data = ws.get_all_values()[1:]
    from utils import parse_price
    from datetime import datetime

    start_date = end_date = None
    if start_date_str:
        start_date = datetime.strptime(start_date_str, "%d-%m-%Y").date()
    if end_date_str:
        end_date = datetime.strptime(end_date_str, "%d-%m-%Y").date()

    cats = {}
    for row in data:
        if len(row) < 4 or not any(row):
            continue
        if start_date or end_date:
            try:
                row_date = datetime.strptime(row[0].split(" ")[0], "%d-%m-%Y").date()
                if start_date and row_date < start_date:
                    continue
                if end_date and row_date > end_date:
                    continue
            except Exception:
                continue
        try:
            price    = parse_price(row[3])
            category = row[10].strip() if len(row) > 10 and row[10].strip() else "Uncategorized"
            if category not in cats:
                cats[category] = {"total": 0.0, "count": 0}
            cats[category]["total"] += price
            cats[category]["count"] += 1
        except ValueError:
            continue

    return dict(sorted(cats.items(), key=lambda x: x[1]["total"], reverse=True))