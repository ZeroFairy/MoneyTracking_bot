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
