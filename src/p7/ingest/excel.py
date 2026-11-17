"""Read an .xlsx: find the data sheet and its header row, read the cover sheet, return raw cell values."""

import io
import re
import zipfile
from dataclasses import dataclass, field

from openpyxl import load_workbook

HEADER_ALIASES = {
    "date": "date", "day": "date",
    "time": "time", "hour": "time",
    "temperature": "temperature", "temp": "temperature", "t": "temperature",
    "humidity": "humidity", "rh": "humidity", "relative humidity": "humidity", "hum": "humidity",
}
REQUIRED = ("date", "time", "temperature", "humidity")
HEADER_SCAN_ROWS = 10
MAX_XLSX_UNCOMPRESSED = 200 * 1024 * 1024


class FileRejected(Exception):
    """The whole file is unusable; the message is shown to the user."""


@dataclass
class RawRow:
    row_number: int  # 1-based sheet row
    date: object
    time: object
    temperature: object
    humidity: object


@dataclass
class ParsedWorkbook:
    sheet: str
    header_row: int
    rows: list[RawRow]
    cover: dict[str, object] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    empty_rows: int = 0


def _norm(cell: object) -> str:
    if not isinstance(cell, str):
        return ""
    s = re.sub(r"\(.*?\)|\[.*?\]|°c|%", "", cell.strip().lower())
    return re.sub(r"\s+", " ", s).strip(" :")


def _header_map(row: tuple) -> dict[str, int] | None:
    found: dict[str, int] = {}
    for i, cell in enumerate(row):
        key = HEADER_ALIASES.get(_norm(cell))
        if key and key not in found:
            found[key] = i
    return found if all(k in found for k in REQUIRED) else None


def _check_container(data: bytes) -> None:
    try:
        z = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as e:
        raise FileRejected("not a valid .xlsx file (it is not an Office Open XML workbook)") from e
    names = set(z.namelist())
    if "xl/workbook.xml" not in names:
        raise FileRejected("not a valid .xlsx file (no workbook inside)")
    if "xl/vbaProject.bin" in names:
        raise FileRejected("macro-enabled workbook; save it as a plain .xlsx")
    if sum(i.file_size for i in z.infolist()) > MAX_XLSX_UNCOMPRESSED:
        raise FileRejected("workbook is too large when uncompressed")


def read_cover(ws) -> dict[str, object]:
    out: dict[str, object] = {}
    for row in ws.iter_rows(min_row=1, max_row=20, max_col=2, values_only=True):
        if len(row) < 2:
            continue
        label = _norm(row[0])
        if label in ("site code", "site", "site id"):
            out["site"] = row[1]
        elif label in ("month", "period", "reporting month"):
            out["month"] = row[1]
    return out


def parse_workbook(data: bytes) -> ParsedWorkbook:
    _check_container(data)
    try:
        wb = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    except Exception as e:  # openpyxl raises many types on corrupt files
        raise FileRejected(f"could not open the workbook ({type(e).__name__})") from e
    try:
        cover: dict[str, object] = {}
        data_sheet = None
        for ws in wb.worksheets:
            head = list(ws.iter_rows(min_row=1, max_row=HEADER_SCAN_ROWS, values_only=True))
            hit = next(((i, m) for i, r in enumerate(head) if (m := _header_map(r))), None)
            if hit and data_sheet is None:
                data_sheet = (ws, hit[0] + 1, hit[1])
            elif not hit and not cover:
                cover = read_cover(ws)
        if data_sheet is None:
            raise FileRejected("no sheet has a Date / Time / Temperature / Humidity header in its first 10 rows")
        ws, header_row, cols = data_sheet
        parsed = ParsedWorkbook(sheet=ws.title, header_row=header_row, rows=[], cover=cover)
        if header_row > 1:
            parsed.warnings.append(f"header found on row {header_row}; {header_row - 1} row(s) above it ignored")
        for n, row in enumerate(ws.iter_rows(min_row=header_row + 1, values_only=True), start=header_row + 1):
            vals = [row[cols[k]] if cols[k] < len(row) else None for k in REQUIRED]
            if all(v is None or (isinstance(v, str) and not v.strip()) for v in vals):
                parsed.empty_rows += 1
                continue
            parsed.rows.append(RawRow(n, *vals))
        return parsed
    finally:
        wb.close()
