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


def _parse_time_token(token: str):
    """Parses an 'HH:MM' token into (hour, minute) ints. Raises ValueError if invalid."""
    try:
        hh_str, mm_str = token.split(":")
        hh, mm = int(hh_str), int(mm_str)
        if not (0 <= hh <= 23 and 0 <= mm <= 59):
            raise ValueError
        return hh, mm
    except ValueError:
        raise ValueError(f"Could not parse time from: {token!r}")


def parse_date_input(raw: str) -> str:
    """Parse a user-supplied date and/or time into a 'DD-MM-YYYY HH:MM' string.

    Date part accepts: "today", "now", "yesterday", or an explicit "DD-MM-YYYY".
    An optional second token gives the time as "HH:MM" — if omitted, the current
    WIB time-of-day is used. Examples: "today", "yesterday 09:00", "25-06-2026",
    "25-06-2026 14:30".

    A single bare "HH:MM" token (no date part) means "today at that time" —
    e.g. "14:30" -> today's date at 14:30 WIB.
    """
    from datetime import datetime, timedelta

    parts = raw.strip().split()
    if not parts:
        raise ValueError(f"Could not parse date from: {raw!r}")

    now = now_wib()

    # Bare time only, e.g. "14:30" — no date word, so assume today.
    if len(parts) == 1 and ":" in parts[0] and parts[0].lower() not in ("today", "now", "yesterday"):
        hh, mm = _parse_time_token(parts[0])
        return f"{now.strftime('%d-%m-%Y')} {hh:02d}:{mm:02d}"

    date_token = parts[0].lower()
    time_token = parts[1] if len(parts) > 1 else None

    if date_token in ("today", "now"):
        date_part = now.date()
    elif date_token == "yesterday":
        date_part = (now - timedelta(days=1)).date()
    else:
        try:
            date_part = datetime.strptime(parts[0], "%d-%m-%Y").date()
        except ValueError:
            raise ValueError(f"Could not parse date from: {raw!r}")

    if time_token:
        hh, mm = _parse_time_token(time_token)
        time_str = f"{hh:02d}:{mm:02d}"
    else:
        time_str = now.strftime("%H:%M")

    return f"{date_part.strftime('%d-%m-%Y')} {time_str}"