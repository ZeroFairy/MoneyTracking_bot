"""Small text/number helpers."""
import re


_PRICE_SUFFIXES = (("jt", 1_000_000), ("m", 1_000_000), ("rb", 1_000), ("k", 1_000))


def parse_price(raw: str) -> float:
    """Parse a loosely-formatted price string into a float.

    Accepts things like: "25000", "25.000", "25,000", "Rp25000", "25k",
    "1.5k", "2m" / "2jt" (millions), "500rb" (thousands).
    Assumes IDR-style formatting where '.' and ',' are thousand separators
    (decimals are only understood together with a k/m/jt/rb suffix).
    Raises ValueError for anything else (negative, NaN, junk, empty).
    """
    s = str(raw).strip().lower().replace("rp", "").replace(" ", "")
    mult = 1
    for suffix, factor in _PRICE_SUFFIXES:
        if s.endswith(suffix):
            s, mult = s[: -len(suffix)], factor
            break
    if mult != 1:
        body = s.replace(",", ".")
        if not re.fullmatch(r"\d+(?:\.\d+)?", body, re.ASCII):
            raise ValueError(f"Could not parse price from: {raw!r}")
        return float(body) * mult
    s = s.replace(".", "").replace(",", "")
    if not re.fullmatch(r"\d+", s, re.ASCII):
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
    if not parts or len(parts) > 2:
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


def md_escape(text) -> str:
    """Escape user-supplied text for Telegram's legacy "Markdown" parse mode.

    Without this, a name like "Mie_Ayam" or "Kopi*Susu" makes Telegram reject
    the whole message ("can't parse entities").
    """
    s = str(text)
    for ch in ("_", "*", "`", "["):
        s = s.replace(ch, "\\" + ch)
    return s


def code_safe(text) -> str:
    """For text placed inside a `code span` (escaping isn't allowed there)."""
    return str(text).replace("`", "'")