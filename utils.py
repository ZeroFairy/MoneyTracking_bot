"""Small text/number helpers."""


def parse_price(raw: str) -> float:
    """Parse a loosely-formatted price string into a float.

    Accepts things like: "25000", "25.000", "25,000", "Rp25000", "25k"
    Assumes IDR-style formatting where '.' and ',' are thousand separators
    (decimals in currency text are rare/ignored here).
    """
    s = raw.strip().lower().replace("rp", "").replace(" ", "")
    if s.endswith("k"):
        # e.g. "25k" -> 25000, "1.5k" -> 1500
        body = s[:-1].replace(",", ".")
        try:
            return float(body) * 1000
        except ValueError:
            raise ValueError(f"Could not parse price from: {raw!r}")
    s = s.replace(".", "").replace(",", "")
    if not s.isdigit():
        raise ValueError(f"Could not parse price from: {raw!r}")
    return float(s)


def format_price(value) -> str:
    if value is None:
        return "-"
    return "Rp" + format(round(float(value)), ",").replace(",", ".")


WIB_OFFSET_HOURS = 7  # WIB = UTC+7


def now_wib():
    """Current time in WIB (UTC+7), regardless of the server's own timezone."""
    from datetime import datetime, timezone, timedelta
    return datetime.now(timezone.utc) + timedelta(hours=WIB_OFFSET_HOURS)


def parse_date_input(raw: str) -> str:
    """Parse a user-supplied date into a 'DD-MM-YYYY HH:MM' timestamp string.

    Accepts: "today", "now", "yesterday", or an explicit "DD-MM-YYYY" date.
    The current WIB time-of-day is always used (only the date itself is customizable).
    """
    from datetime import datetime, timedelta

    s = raw.strip().lower()
    now = now_wib()

    if s in ("today", "now"):
        date_part = now.date()
    elif s == "yesterday":
        date_part = (now - timedelta(days=1)).date()
    else:
        try:
            date_part = datetime.strptime(raw.strip(), "%d-%m-%Y").date()
        except ValueError:
            raise ValueError(f"Could not parse date from: {raw!r}")

    return f"{date_part.strftime('%d-%m-%Y')} {now.strftime('%H:%M')}"