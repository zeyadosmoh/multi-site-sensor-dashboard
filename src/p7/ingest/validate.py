"""Row validation. Every rejected row gets one reason code and a human message.

Reason codes (checked in this order; the first failing check is the reason):
  bad_date            date cell missing or not a real date
  bad_time            time cell missing, not a real time, or not on the reading interval
  missing_value       temperature or humidity blank
  non_numeric         temperature or humidity is text that is not a number
  temp_impossible     temperature outside -40..70 C (physically impossible for these sites)
  humidity_impossible humidity outside 0..100 %
  outside_file_month  the row's date is not in the file's month
  duplicate_exact     same hour again with identical values (the first copy is kept)
  duplicate_conflict  same hour with different values (every copy is rejected: we can't tell which is right)
"""

import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from p7.ingest.excel import RawRow

REASONS = ("bad_date", "bad_time", "missing_value", "non_numeric", "temp_impossible", "humidity_impossible",
           "outside_file_month", "duplicate_exact", "duplicate_conflict")
TEMP_RANGE = (Decimal(-40), Decimal(70))
HUM_RANGE = (Decimal(0), Decimal(100))
EXCEL_EPOCH = date(1899, 12, 30)
SERIAL_RANGE = (36526, 73051)  # 2000-01-01 .. 2099-12-31

ISO_RE = re.compile(r"^\s*(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})\s*$")
SLASH_RE = re.compile(r"^\s*(\d{1,2})[-/.](\d{1,2})[-/.](\d{4})\s*$")
TIME_RE = re.compile(r"^\s*(\d{1,2}):(\d{2})(?::(\d{2}))?\s*$")


@dataclass(frozen=True)
class Reading:
    ts: datetime
    temperature: Decimal
    humidity: Decimal
    row_number: int


@dataclass(frozen=True)
class Reject:
    row: RawRow
    reason: str
    message: str


@dataclass
class Validated:
    readings: list[Reading] = field(default_factory=list)
    rejects: list[Reject] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def _text(v: object) -> str | None:
    if v is None:
        return None
    if isinstance(v, datetime):
        return v.isoformat(sep=" ")
    return str(v)


def detect_slash_order(rows: list[RawRow]) -> tuple[str, str | None]:
    """For dates written as a/b/YYYY: decide day-first or month-first from the whole file."""
    first_gt_12 = second_gt_12 = False
    seen = False
    for r in rows:
        if isinstance(r.date, str) and (m := SLASH_RE.match(r.date)):
            seen = True
            first_gt_12 |= int(m.group(1)) > 12
            second_gt_12 |= int(m.group(2)) > 12
    if not seen:
        return "dmy", None
    if first_gt_12 and not second_gt_12:
        return "dmy", "dates are written as DD/MM/YYYY text; read day-first"
    if second_gt_12 and not first_gt_12:
        return "mdy", "dates are written as MM/DD/YYYY text; read month-first"
    if not first_gt_12 and not second_gt_12:
        return "dmy", "dates are written as a/b/YYYY and every day is <= 12; assumed day-first (DD/MM/YYYY)"
    return "dmy", "dates mix day-first and month-first; rows that don't fit day-first are rejected"


def parse_date(v: object, slash_order: str) -> date | None:
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    if isinstance(v, bool):
        return None
    if isinstance(v, int | float):
        if SERIAL_RANGE[0] <= v <= SERIAL_RANGE[1] and float(v).is_integer():
            return EXCEL_EPOCH + timedelta(days=int(v))
        return None
    if isinstance(v, str):
        try:
            if m := ISO_RE.match(v):
                return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
            if m := SLASH_RE.match(v):
                a, b, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
                return date(y, b, a) if slash_order == "dmy" else date(y, a, b)
        except ValueError:
            return None
    return None


def parse_time(v: object) -> time | None:
    if isinstance(v, datetime):
        return v.time()
    if isinstance(v, time):
        return v
    if isinstance(v, timedelta):
        secs = round(v.total_seconds())
        return time(secs // 3600, secs % 3600 // 60) if 0 <= secs < 86400 else None
    if isinstance(v, bool):
        return None
    if isinstance(v, float | int):  # Excel fraction of a day
        if 0 <= v < 1:
            minutes = round(v * 1440)
            return time(minutes // 60, minutes % 60) if minutes < 1440 else None
        return None
    if isinstance(v, str) and (m := TIME_RE.match(v)):
        h, mi, s = int(m.group(1)), int(m.group(2)), int(m.group(3) or 0)
        if h < 24 and mi < 60 and s < 60:
            return time(h, mi, s)
    return None


def parse_number(v: object) -> tuple[Decimal | None, str | None]:
    """Returns (value, problem) where problem is 'missing_value' or 'non_numeric'."""
    if v is None or (isinstance(v, str) and not v.strip()):
        return None, "missing_value"
    if isinstance(v, bool):
        return None, "non_numeric"
    if isinstance(v, int | float):
        d = Decimal(repr(v)) if isinstance(v, float) else Decimal(v)
    else:
        try:
            d = Decimal(str(v).strip().replace(",", "."))
        except InvalidOperation:
            return None, "non_numeric"
    if not d.is_finite():
        return None, "non_numeric"
    if abs(d) >= 1000:  # a number, but far outside any range check: leave it unrounded for the range check
        return d, None
    return d.quantize(Decimal("0.1"), rounding=ROUND_HALF_UP), None


def validate_rows(rows: list[RawRow], month: date, interval_minutes: int = 60) -> Validated:
    out = Validated()
    slash_order, note = detect_slash_order(rows)
    if note:
        out.warnings.append(note)
    if any(isinstance(r.date, int | float) and not isinstance(r.date, bool) for r in rows):
        out.warnings.append("dates are stored as plain Excel serial numbers; converted")
    next_month = date(month.year + (month.month == 12), month.month % 12 + 1, 1)

    candidates: list[Reading] = []
    for r in rows:
        d = parse_date(r.date, slash_order)
        if d is None:
            out.rejects.append(Reject(r, "bad_date", f"date {r.date!r} is not a valid date"))
            continue
        t = parse_time(r.time)
        if t is None:
            out.rejects.append(Reject(r, "bad_time", f"time {r.time!r} is not a valid time of day"))
            continue
        if (t.hour * 60 + t.minute) % interval_minutes or t.second:
            out.rejects.append(Reject(r, "bad_time",
                                      f"time {t:%H:%M:%S} is not on the {interval_minutes}-minute reading interval"))
            continue
        temp, tp = parse_number(r.temperature)
        hum, hp = parse_number(r.humidity)
        if tp or hp:
            reason = "missing_value" if "missing_value" in (tp, hp) else "non_numeric"
            what = [n for n, p in (("temperature", tp), ("humidity", hp)) if p == reason]
            raw = {"temperature": r.temperature, "humidity": r.humidity}
            msg = " and ".join(f"{w} {raw[w]!r}" for w in what)
            out.rejects.append(Reject(r, reason, f"{msg}: {'blank' if reason == 'missing_value' else 'not a number'}"))
            continue
        if not TEMP_RANGE[0] <= temp <= TEMP_RANGE[1]:
            out.rejects.append(Reject(r, "temp_impossible", f"temperature {temp} C is outside -40..70 C"))
            continue
        if not HUM_RANGE[0] <= hum <= HUM_RANGE[1]:
            out.rejects.append(Reject(r, "humidity_impossible", f"humidity {hum} % is outside 0..100 %"))
            continue
        if not month <= d < next_month:
            out.rejects.append(Reject(r, "outside_file_month",
                                      f"date {d:%Y-%m-%d} is outside this file's month {month:%Y-%m}"))
            continue
        candidates.append(Reading(datetime.combine(d, t), temp, hum, r.row_number))

    by_ts: dict[datetime, list[Reading]] = defaultdict(list)
    for c in candidates:
        by_ts[c.ts].append(c)
    raw_by_number = {r.row_number: r for r in rows}
    for ts, group in by_ts.items():
        if len(group) == 1:
            out.readings.append(group[0])
            continue
        values = {(g.temperature, g.humidity) for g in group}
        if len(values) == 1:
            out.readings.append(group[0])
            first = group[0].row_number
            for g in group[1:]:
                out.rejects.append(Reject(raw_by_number[g.row_number], "duplicate_exact",
                                          f"{ts:%Y-%m-%d %H:%M} repeats row {first} with the same values"))
        else:
            rows_txt = ", ".join(str(g.row_number) for g in group)
            for g in group:
                out.rejects.append(Reject(raw_by_number[g.row_number], "duplicate_conflict",
                                          f"{ts:%Y-%m-%d %H:%M} appears in rows {rows_txt} with different values"))
    out.readings.sort(key=lambda x: x.ts)
    out.rejects.sort(key=lambda x: x.row.row_number)
    return out


def reject_text(r: Reject) -> dict:
    return {"row_number": r.row.row_number, "raw_date": _text(r.row.date), "raw_time": _text(r.row.time),
            "raw_temperature": _text(r.row.temperature), "raw_humidity": _text(r.row.humidity),
            "reason": r.reason, "message": r.message}
