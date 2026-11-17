"""Number and date formatting shared by the dashboard, CSV export and PDF reports, so every surface prints the
same rounding. `n()` returns an isolated LTR span so numbers stay intact inside Arabic (RTL) text."""

from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal

from markupsafe import Markup, escape

DASH = "–"


def number(v, dp: int = 1) -> str:
    """Plain text: thousands separators, half-up rounding (Decimal, not binary float). None -> en dash."""
    if v is None:
        return DASH
    d = Decimal(str(v)).quantize(Decimal(1).scaleb(-dp), rounding=ROUND_HALF_UP)
    return f"{d:,.{dp}f}"


def hours(minutes, dp: int = 1) -> str:
    return DASH if minutes is None else number(Decimal(int(minutes)) / 60, dp)


def ts(v: datetime | date | None) -> str:
    if v is None:
        return DASH
    return v.strftime("%Y-%m-%d %H:%M") if isinstance(v, datetime) else v.strftime("%Y-%m-%d")


def n(v, dp: int = 1, unit: str = "") -> Markup:
    """HTML: the number (and a symbol unit like % or °C) in its own left-to-right span."""
    text = number(v, dp) if not isinstance(v, str) else v
    if unit and text != DASH:
        text = f"{text}{unit}"
    return Markup('<span class="num" dir="ltr">{}</span>').format(escape(text))


def nh(minutes, dp: int = 1) -> Markup:
    return n(hours(minutes, dp))


def nts(v) -> Markup:
    return n(ts(v))


def ni(v) -> Markup:
    return n(v, 0)


def nr(a, b, dp: int = 1, unit: str = "") -> Markup:
    """A range or ratio ("20.7 – 38.9 °C", "743 / 744") as ONE left-to-right span, so right-to-left text can
    never swap its two ends."""
    sep = " / " if unit == "/" else " – "
    unit = "" if unit == "/" else unit
    left = a if isinstance(a, str) else number(a, dp)
    right = b if isinstance(b, str) else number(b, dp)
    return Markup('<span class="num" dir="ltr">{}</span>').format(escape(f"{left}{sep}{right}{unit}"))


def localized(obj: dict, field: str, lang: str):
    """obj[field + '_ar'] in Arabic when present, else obj[field] (site and city names)."""
    if lang == "ar" and obj.get(f"{field}_ar"):
        return obj[f"{field}_ar"]
    return obj.get(field)


def keepwords(text) -> Markup:
    """Table headers: wrap only at spaces. Each hyphenated word ("Out-of-range") goes in a no-wrap span so the
    browser cannot break it after a hyphen; the CSS keeps other words whole (no overflow-wrap: anywhere)."""
    words = (escape(text) if not isinstance(text, Markup) else text).split(" ")
    return Markup(" ").join(Markup('<span class="nw">') + w + Markup("</span>") if "-" in w else w for w in words)
