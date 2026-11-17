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
