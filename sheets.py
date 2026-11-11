"""All Google Sheets read/write logic lives here."""
from datetime import datetime

import gspread
from gspread.utils import rowcol_to_a1

from config import GOOGLE_CREDENTIALS_FILE, GOOGLE_SHEETS_ID
from utils import now_wib, format_price, parse_price, md_escape

HEADERS = ["Date & Time", "Place", "Buying List", "Price", "Paid By", "Shared By", "Amount/Person", "Picture", "Status", "Settled By", "Category"]

STATE_PREFIX = "BotState_"  # internal tabs (active sheet, members, reminders, budgets...)

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


def _set_cell(ws, row: int, col: int, value):
    """Write one plain-text (RAW) cell. update_cell() may parse the value
    (dates/numbers/formulas) depending on the gspread version."""
    ws.update(range_name=rowcol_to_a1(row, col), values=[[value]])


def _name_canon():
    """Returns f(name) mapping 'andi' / 'Andi ' / 'ANDI' to the first-seen
    spelling, so one person isn't split into several balances."""
    seen = {}

    def canon(name: str) -> str:
        name = name.strip()
        return seen.setdefault(name.lower(), name)
    return canon


def _split_names(text: str, canon) -> list:
    """Comma-separated names -> de-duplicated, canonicalised list (order kept)."""
    return list(dict.fromkeys(canon(n) for n in text.split(",") if n.strip()))


def current_month_sheet_name() -> str:
    return now_wib().strftime("%b %Y")


def sanitize_sheet_name(name: str) -> str:
    bad = set('[]*/\\?:')
    cleaned = "".join(c for c in name if c not in bad).strip()
    if cleaned.lower().startswith(STATE_PREFIX.lower()):
        cleaned = "My " + cleaned  # reserved for the bot's internal state tabs
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
    # Hide the bot's internal state tabs, or /switch could point expense logging at them.
    return [ws.title for ws in _spreadsheet().worksheets() if not ws.title.startswith(STATE_PREFIX)]


def append_expense(sheet_name, place, item, price, paid_by, shared_by, amount_per_person, picture="-", status="⏳ Unpaid", category="", date_str=None):
    ws = get_or_create_worksheet(sheet_name)
    ts = date_str if date_str else now_wib().strftime("%d-%m-%Y %H:%M")
    ws.append_row(
        [ts, place, item, format_price(price), paid_by, shared_by,
         format_price(amount_per_person) if amount_per_person is not None else "-",
         picture, status, "", category],
        value_input_option="RAW",  # keep dates/prices as the exact text written; blocks formula injection
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
    if row_num < 2:
        raise ValueError("Refusing to delete the header row.")
    ws = get_or_create_worksheet(sheet_name)
    ws.delete_rows(row_num)


def _trim(row):
    row = [str(c).strip() for c in row]
    while row and not row[-1]:
        row.pop()
    return row


def rows_equal(a, b) -> bool:
    """Compare two row snapshots, ignoring trailing empty cells / whitespace."""
    return _trim(a) == _trim(b)


def get_row(sheet_name, row_num: int) -> list:
    ws = get_or_create_worksheet(sheet_name)
    return ws.row_values(row_num)


def row_matches(sheet_name, row_num: int, snapshot) -> bool:
    """True if the sheet's current row still equals what the user was shown.
    Row numbers shift after deletes/sorts, so cached numbers must be verified
    before a destructive or mutating action."""
    return rows_equal(get_row(sheet_name, row_num), snapshot)


def recalc_per_person(sheet_name, row_num: int):
    """Recompute Amount/Person = Price / number of sharers (after a price or
    shared-by edit), so settlements don't use a stale per-person figure."""
    row = get_row(sheet_name, row_num)
    row += [""] * (7 - len(row))
    try:
        price = parse_price(row[3])
    except ValueError:
        return
    sharers = list(dict.fromkeys(n.strip() for n in row[5].split(",") if n.strip()))
    if not sharers:
        return
    ws = get_or_create_worksheet(sheet_name)
    _set_cell(ws, row_num, 7, format_price(price / len(sharers)))


STATUS_COL  = 9   # 1-indexed — "Status"
SETTLED_COL  = 10  # 1-indexed — "Settled By"
CATEGORY_COL = 11  # 1-indexed — "Category"


def toggle_paid_status(sheet_name: str, row_num: int) -> str:
    ws = get_or_create_worksheet(sheet_name)
    current = ws.cell(row_num, STATUS_COL).value or ""
    new_status = "✅ Paid" if current != "✅ Paid" else "⏳ Unpaid"
    _set_cell(ws, row_num, STATUS_COL, new_status)
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
    _set_cell(ws, row_num, SETTLED_COL, ", ".join(names))


# Column numbers for editable fields (1-indexed)
FIELD_COLS = {
    "date":       1,
    "place":      2,
    "item":       3,
    "price":      4,
    "paid_by":    5,
    "shared":     6,
    "per_person": 7,
    "category":   CATEGORY_COL,
}


def update_expense_field(sheet_name: str, row_num: int, field: str, value: str):
    """Update a single field in an expense row."""
    col = FIELD_COLS.get(field)
    if col is None:
        raise ValueError(f"Unknown field: {field}")
    ws = get_or_create_worksheet(sheet_name)
    _set_cell(ws, row_num, col, value)


def sort_by_date(sheet_name: str) -> int:
    """Re-sorts all data rows in the sheet chronologically (oldest first) by
    the Date & Time column (A).

    New entries are always appended to the bottom regardless of their date
    (e.g. backdated /add or /q @date entries), so this is what fixes the
    on-sheet order without touching entry content. Malformed/unparseable
    dates are pushed to the end, keeping their original relative order.

    Returns the number of data rows sorted (0 if the sheet is empty).
    """
    ws = get_or_create_worksheet(sheet_name)
    all_vals = ws.get_all_values()
    if len(all_vals) <= 1:
        return 0

    data_rows = [row for row in all_vals[1:] if any(row)]
    if not data_rows:
        return 0

    width = len(HEADERS)

    def _pad(row):
        row = list(row[:width])
        row += [""] * (width - len(row))
        return row

    data_rows = [_pad(row) for row in data_rows]

    def _sort_key(row):
        try:
            return (0, datetime.strptime(row[0].strip(), "%d-%m-%Y %H:%M"))
        except (ValueError, IndexError):
            return (1, datetime.max)  # unparseable dates sink to the bottom

    data_rows.sort(key=_sort_key)  # stable: ties/malformed keep original order

    last_col = chr(ord("A") + width - 1)
    end_row  = 1 + len(data_rows)
    original_last_row = len(all_vals)  # includes header, 1-indexed
    ws.update(range_name=f"A2:{last_col}{end_row}", values=data_rows)

    # If any fully-blank rows got filtered out above, the sorted block is now
    # shorter than the sheet's original data range — clear what's left beyond
    # it, or it would sit there as stale, duplicated content.
    if end_row < original_last_row:
        ws.batch_clear([f"A{end_row + 1}:{last_col}{original_last_row}"])

    return len(data_rows)


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

    start_date = None
    end_date = None
    if start_date_str:
        start_date = datetime.strptime(start_date_str, "%d-%m-%Y").date()
    if end_date_str:
        end_date = datetime.strptime(end_date_str, "%d-%m-%Y").date()

    balances = {}
    canon = _name_canon()
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
            payer = canon(row[4])
            shared_by_str = row[5].strip()
            amt_str = row[6].strip()
            settled_str = row[9].strip() if len(row) > 9 else ""
            if not payer or not shared_by_str:
                continue
            sharers = _split_names(shared_by_str, canon)
            if not sharers:
                continue
            settled_names = set(_split_names(settled_str, canon))
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
        transactions.append(f"💸 *{md_escape(debtors[i][0])}* pays *{md_escape(creditors[j][0])}*: {format_price(amt)}")
        debtors[i][1]   -= amt
        creditors[j][1] -= amt
        if debtors[i][1]   < 10.0: i += 1
        if creditors[j][1] < 10.0: j += 1
    return transactions


def get_raw_summary(sheet_name: str, start_date_str=None, end_date_str=None):
    ws = get_or_create_worksheet(sheet_name)
    data = ws.get_all_values()[1:]

    start_date = None
    end_date = None
    if start_date_str:
        start_date = datetime.strptime(start_date_str, "%d-%m-%Y").date()
    if end_date_str:
        end_date = datetime.strptime(end_date_str, "%d-%m-%Y").date()

    direct_debts = {}
    canon = _name_canon()
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
            payer = canon(row[4])
            shared_by_str = row[5].strip()
            amt_str = row[6].strip()
            settled_str = row[9].strip() if len(row) > 9 else ""
            if not payer or not shared_by_str or amt_str == "-":
                continue
            amt_per_person = parse_price(amt_str)
            sharers = _split_names(shared_by_str, canon)
            settled_names = set(_split_names(settled_str, canon))
            for person in sharers:
                if person in settled_names:
                    continue
                if person != payer:
                    k = (person, payer)
                    direct_debts[k] = direct_debts.get(k, 0.0) + amt_per_person
        except ValueError:
            continue

    return [
        f"💸 *{md_escape(d)}* pays *{md_escape(c)}*: {format_price(a)}"
        for (d, c), a in direct_debts.items() if a > 10.0
    ]


def get_person_totals(sheet_name: str, start_date_str=None, end_date_str=None):
    """Total spending per person regardless of paid/unpaid status.

    Returns a dict: {name: total_spent} and the grand total.
    """
    ws = get_or_create_worksheet(sheet_name)
    data = ws.get_all_values()[1:]

    start_date = end_date = None
    if start_date_str:
        start_date = datetime.strptime(start_date_str, "%d-%m-%Y").date()
    if end_date_str:
        end_date = datetime.strptime(end_date_str, "%d-%m-%Y").date()

    totals = {}
    canon = _name_canon()
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
            for person in _split_names(shared_by_str, canon):
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

    start_date = end_date = None
    if start_date_str:
        start_date = datetime.strptime(start_date_str, "%d-%m-%Y").date()
    if end_date_str:
        end_date = datetime.strptime(end_date_str, "%d-%m-%Y").date()

    cats = {}
    canon = _name_canon()  # "food & drink" and "Food & Drink" are the same category
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
            category = canon(row[10]) if len(row) > 10 and row[10].strip() else "Uncategorized"
            if category not in cats:
                cats[category] = {"total": 0.0, "count": 0}
            cats[category]["total"] += price
            cats[category]["count"] += 1
        except ValueError:
            continue

    return dict(sorted(cats.items(), key=lambda x: x[1]["total"], reverse=True))


def get_recap(sheet_name: str) -> dict:
    """Quick snapshot for the whole active sheet, no date filter — total
    entries, grand total spent (full bill amounts), the single biggest
    expense, the top category, and the biggest spender (by shared amount,
    same basis as /totals, regardless of paid status).

    Returns:
        {
            "count": int,
            "grand_total": float,
            "biggest_expense": (price, place, item) | None,
            "top_category": (name, total) | None,
            "top_spender": (name, total) | None,
        }
    """
    ws = get_or_create_worksheet(sheet_name)
    data = ws.get_all_values()[1:]

    count = 0
    grand_total = 0.0
    biggest = None  # (price, place, item)
    cat_totals = {}
    person_totals = {}
    canon = _name_canon()

    for row in data:
        if len(row) < 4 or not any(row):
            continue
        try:
            price = parse_price(row[3])
        except ValueError:
            continue

        count += 1
        grand_total += price
        place = row[1] if len(row) > 1 else "?"
        item  = row[2] if len(row) > 2 else "?"
        if biggest is None or price > biggest[0]:
            biggest = (price, place, item)

        category = canon(row[10]) if len(row) > 10 and row[10].strip() else "Uncategorized"
        cat_totals[category] = cat_totals.get(category, 0.0) + price

        shared_by_str = row[5].strip() if len(row) > 5 else ""
        amt_str = row[6].strip() if len(row) > 6 else ""
        if shared_by_str and amt_str and amt_str != "-":
            try:
                amt_per_person = parse_price(amt_str)
                for person in _split_names(shared_by_str, canon):
                    person_totals[person] = person_totals.get(person, 0.0) + amt_per_person
            except ValueError:
                pass

    top_category = max(cat_totals.items(), key=lambda x: x[1]) if cat_totals else None
    top_spender  = max(person_totals.items(), key=lambda x: x[1]) if person_totals else None

    return {
        "count": count,
        "grand_total": grand_total,
        "biggest_expense": biggest,
        "top_category": top_category,
        "top_spender": top_spender,
    }