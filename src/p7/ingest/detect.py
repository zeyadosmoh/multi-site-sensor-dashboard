"""Detect site code and month from the filename and/or the cover sheet."""

import re
from dataclasses import dataclass
from datetime import date, datetime

from p7.ingest.excel import FileRejected

FILENAME_RE = re.compile(r"(?<![A-Za-z])([A-Za-z]{3})[-_ ]?(\d{2})[-_ ]+(\d{4})[-_ ]?(\d{2})(?!\d)")
SITE_RE = re.compile(r"^\s*([A-Za-z]{3})[-_ ]?(\d{2})\s*$")


@dataclass(frozen=True)
class Detected:
    site_code: str
    month: date  # first day
    detected_by: str


def _month(y: int, m: int) -> date | None:
    return date(y, m, 1) if 1 <= m <= 12 and 2000 <= y <= 2100 else None


def from_filename(name: str) -> tuple[str, date] | None:
    m = FILENAME_RE.search(name.rsplit("/", 1)[-1])
    if not m:
        return None
    month = _month(int(m.group(3)), int(m.group(4)))
    return (f"{m.group(1).upper()}-{m.group(2)}", month) if month else None


def from_cover(cover: dict) -> tuple[str | None, date | None]:
    site = month = None
    raw_site = cover.get("site")
    if isinstance(raw_site, str) and (m := SITE_RE.match(raw_site)):
        site = f"{m.group(1).upper()}-{m.group(2)}"
    raw_month = cover.get("month")
    if isinstance(raw_month, datetime | date):
        month = date(raw_month.year, raw_month.month, 1)
    elif isinstance(raw_month, str) and (m := re.match(r"^\s*(\d{4})[-/](\d{1,2})\s*$", raw_month)):
        month = _month(int(m.group(1)), int(m.group(2)))
    return site, month


def detect(filename: str, cover: dict) -> Detected:
    fn = from_filename(filename)
    cv_site, cv_month = from_cover(cover)
    cv = (cv_site, cv_month) if cv_site and cv_month else None
    if fn and cv:
        if fn != cv:
            raise FileRejected("site_month_conflict", file_site=fn[0], file_month=f"{fn[1]:%Y-%m}",
                               cover_site=cv[0], cover_month=f"{cv[1]:%Y-%m}")
        return Detected(fn[0], fn[1], "filename+cover_sheet")
    if fn:
        return Detected(fn[0], fn[1], "filename")
    if cv:
        return Detected(cv[0], cv[1], "cover_sheet")
    raise FileRejected("site_month_unknown")
