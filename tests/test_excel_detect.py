"""Workbook parsing (header row, cover sheet, bad containers) and site/month detection."""

import io
import zipfile
from datetime import date, datetime

import pytest

from p7.ingest.detect import detect, from_filename
from p7.ingest.excel import FileRejected, parse_workbook
from p7.ingest.messages import msg
from tests.helpers import make_xlsx


def test_header_found_below_a_title_row():
    data = make_xlsx([[date(2026, 5, 1), "00:00", 22.0, 45.0]],
                     pre_rows=[["Sensor export - RUH-01", None, None, None], []])
    p = parse_workbook(data)
    assert p.header_row == 3
    assert p.rows[0].row_number == 4
    assert p.warnings == [msg("header_row", row=3, n=2)]
    assert str(p.warnings[0]) == "header found on row 3; 2 rows above it ignored"


def test_header_aliases_and_units_and_column_order():
    data = make_xlsx([[45.0, 22.0, "00:00", date(2026, 5, 1)]],
                     header=["Humidity (%)", "Temperature (°C)", "Time", "Date"])
    p = parse_workbook(data)
    r = p.rows[0]
    assert (r.date, r.time, r.temperature, r.humidity) == (datetime(2026, 5, 1), "00:00", 22.0, 45.0)


def test_empty_rows_are_skipped_not_rejected():
    data = make_xlsx([[date(2026, 5, 1), "00:00", 22.0, 45.0], [None, None, None, None], ["", " ", None, None]])
    p = parse_workbook(data)
    assert len(p.rows) == 1 and p.empty_rows == 2


def test_no_header_rejects_file():
    with pytest.raises(FileRejected, match="header"):
        parse_workbook(make_xlsx([[1, 2, 3, 4]], header=["a", "b", "c", "d"]))


def test_not_a_workbook():
    with pytest.raises(FileRejected, match="not a valid .xlsx"):
        parse_workbook(b"Date,Time,Temperature,Humidity\n")


def test_macro_workbook_rejected():
    buf = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(make_xlsx([])), "r") as src, zipfile.ZipFile(buf, "w") as dst:
        for i in src.infolist():
            dst.writestr(i, src.read(i))
        dst.writestr("xl/vbaProject.bin", b"\0")
    with pytest.raises(FileRejected, match="macro"):
        parse_workbook(buf.getvalue())


def test_cover_sheet_read():
    p = parse_workbook(make_xlsx([], cover={"Site code": "ABH-01", "Month": "2026-08"}))
    assert p.cover == {"site": "ABH-01", "month": "2026-08"}


@pytest.mark.parametrize("name, expected", [
    ("RUH-01_2026-05.xlsx", ("RUH-01", date(2026, 5, 1))),
    ("ruh01 2026_05.xlsx", ("RUH-01", date(2026, 5, 1))),
    ("export/JED-02-2026-07 (final).xlsx", ("JED-02", date(2026, 7, 1))),
    ("RUH-01_2026-13.xlsx", None),
    ("sensor_export_final (2).xlsx", None),
])
def test_filename_patterns(name, expected):
    assert from_filename(name) == expected


def test_detect_prefers_agreement_and_reports_source():
    assert detect("RUH-01_2026-05.xlsx", {}).detected_by == "filename"
    d = detect("export.xlsx", {"site": "abh-01", "month": datetime(2026, 8, 1)})
    assert (d.site_code, d.month, d.detected_by) == ("ABH-01", date(2026, 8, 1), "cover_sheet")
    d = detect("ABH-01_2026-08.xlsx", {"site": "ABH-01", "month": "2026-08"})
    assert d.detected_by == "filename+cover_sheet"


def test_detect_conflict_and_missing():
    with pytest.raises(FileRejected, match="filename says RUH-01 2026-05 but the cover sheet says"):
        detect("RUH-01_2026-05.xlsx", {"site": "RUH-02", "month": "2026-05"})
    with pytest.raises(FileRejected, match="cannot tell the site and month"):
        detect("export.xlsx", {"site": "RUH-01"})
