"""Row validation: one case per reason code, plus date/time/number formats."""

from datetime import date, datetime, time, timedelta
from decimal import Decimal

import pytest

from p7.ingest.excel import RawRow
from p7.ingest.validate import REASONS, parse_date, parse_number, parse_time, validate_rows

MAY = date(2026, 5, 1)


def rr(n, d, t, temp=22.5, hum=45.0):
    return RawRow(n, d, t, temp, hum)


def reasons(rows, month=MAY):
    v = validate_rows(rows, month)
    return [(r.row.row_number, r.reason) for r in v.rejects], v


@pytest.mark.parametrize("row, reason", [
    (rr(2, "2026-13-01", "10:00"), "bad_date"),
    (rr(2, None, "10:00"), "bad_date"),
    (rr(2, "yesterday", "10:00"), "bad_date"),
    (rr(2, date(2026, 5, 3), "25:00"), "bad_time"),
    (rr(2, date(2026, 5, 3), None), "bad_time"),
    (rr(2, date(2026, 5, 3), "10:30"), "bad_time"),  # not on the 60-minute interval
    (rr(2, date(2026, 5, 3), "10:00", temp=None), "missing_value"),
    (rr(2, date(2026, 5, 3), "10:00", hum="  "), "missing_value"),
    (rr(2, date(2026, 5, 3), "10:00", temp="ERR"), "non_numeric"),
    (rr(2, date(2026, 5, 3), "10:00", hum=True), "non_numeric"),
    (rr(2, date(2026, 5, 3), "10:00", temp=85.0), "temp_impossible"),
    (rr(2, date(2026, 5, 3), "10:00", temp=-60), "temp_impossible"),
    (rr(2, date(2026, 5, 3), "10:00", temp="1e400"), "temp_impossible"),
    (rr(2, date(2026, 5, 3), "10:00", hum=120), "humidity_impossible"),
    (rr(2, date(2026, 5, 3), "10:00", hum=-5), "humidity_impossible"),
    (rr(2, date(2026, 6, 1), "00:00"), "outside_file_month"),
    (rr(2, date(2026, 4, 30), "23:00"), "outside_file_month"),
])
def test_each_reason(row, reason):
    got, v = reasons([row])
    assert got == [(2, reason)]
    assert v.readings == []
    assert v.rejects[0].message  # every reject explains itself


def test_every_reason_code_is_covered_by_a_test():
    covered = {"bad_date", "bad_time", "missing_value", "non_numeric", "temp_impossible", "humidity_impossible",
               "outside_file_month", "duplicate_exact", "duplicate_conflict"}
    assert covered == set(REASONS)


def test_limits_are_inclusive():
    _, v = reasons([rr(2, date(2026, 5, 3), "10:00", temp=70, hum=100),
                    rr(3, date(2026, 5, 3), "11:00", temp=-40, hum=0)])
    assert len(v.readings) == 2 and not v.rejects


def test_exact_duplicate_keeps_first_copy():
    rows = [rr(2, date(2026, 5, 3), "10:00"), rr(3, date(2026, 5, 3), "10:00"), rr(4, date(2026, 5, 3), "10:00")]
    got, v = reasons(rows)
    assert got == [(3, "duplicate_exact"), (4, "duplicate_exact")]
    assert [r.row_number for r in v.readings] == [2]


def test_conflicting_duplicates_reject_every_copy():
    rows = [rr(2, date(2026, 5, 3), "10:00", temp=22.0), rr(3, date(2026, 5, 3), "10:00", temp=23.3),
            rr(4, date(2026, 5, 3), "11:00")]
    got, v = reasons(rows)
    assert got == [(2, "duplicate_conflict"), (3, "duplicate_conflict")]
    assert [r.row_number for r in v.readings] == [4]
    assert "rows 2, 3" in v.rejects[0].message


def test_duplicate_detection_uses_parsed_timestamp_not_text():
    # same hour written two ways is still a duplicate
    rows = [rr(2, date(2026, 5, 3), "10:00"), rr(3, "2026-05-03", time(10, 0))]
    got, _ = reasons(rows)
    assert got == [(3, "duplicate_exact")]


def test_first_failing_check_wins():
    got, _ = reasons([rr(2, "bad", "99:99", temp="x", hum=500)])
    assert got == [(2, "bad_date")]


def test_day_first_text_dates_detected_from_the_file():
    rows = [rr(i + 2, f"{d:02d}/07/2026", "10:00") for i, d in enumerate((1, 2, 13, 31))]
    _, v = reasons(rows, date(2026, 7, 1))
    assert [r.ts.date() for r in v.readings] == [date(2026, 7, d) for d in (1, 2, 13, 31)]
    assert any("DD/MM/YYYY" in w for w in v.warnings)


def test_month_first_text_dates_detected_from_the_file():
    rows = [rr(i + 2, f"07/{d:02d}/2026", "10:00") for i, d in enumerate((1, 2, 13))]
    _, v = reasons(rows, date(2026, 7, 1))
    assert [r.ts.date() for r in v.readings] == [date(2026, 7, d) for d in (1, 2, 13)]
    assert any("MM/DD/YYYY" in w for w in v.warnings)


def test_ambiguous_slash_dates_assume_day_first_with_a_warning():
    _, v = reasons([rr(2, "03/05/2026", "10:00")])
    assert v.readings[0].ts == datetime(2026, 5, 3, 10)
    assert any("assumed day-first" in w for w in v.warnings)


def test_excel_serial_dates():
    serial = (date(2026, 10, 5) - date(1899, 12, 30)).days
    _, v = reasons([rr(2, serial, "10:00")], date(2026, 10, 1))
    assert v.readings[0].ts == datetime(2026, 10, 5, 10)
    assert any("serial" in w for w in v.warnings)


@pytest.mark.parametrize("raw, expected", [
    (datetime(2026, 5, 3, 0, 0), date(2026, 5, 3)),
    (date(2026, 5, 3), date(2026, 5, 3)),
    ("2026-05-03", date(2026, 5, 3)),
    ("2026/5/3", date(2026, 5, 3)),
    (46145, date(2026, 5, 3)),
    (46145.5, None),  # fractional serial is not a date
    (12, None),
    ("2026-02-30", None),
])
def test_parse_date(raw, expected):
    assert parse_date(raw, "dmy") == expected


@pytest.mark.parametrize("raw, expected", [
    ("7:00", time(7)), ("07:00:00", time(7)), (time(7), time(7)), (datetime(1899, 12, 30, 7), time(7)),
    (0.25, time(6)), (timedelta(hours=13), time(13)), ("24:00", None), ("7", None), (1.5, None),
])
def test_parse_time(raw, expected):
    assert parse_time(raw) == expected


@pytest.mark.parametrize("raw, expected, problem", [
    (22.45, Decimal("22.5"), None),  # half-up, from the decimal text not the binary float
    (22, Decimal("22.0"), None),
    ("22,4", Decimal("22.4"), None),  # decimal comma
    (" 22.4 ", Decimal("22.4"), None),
    ("", None, "missing_value"),
    ("NaN", None, "non_numeric"),
    ("inf", None, "non_numeric"),
    ("1e400", Decimal("1e400"), None),  # a number; the range check rejects it (temp_impossible)
])
def test_parse_number(raw, expected, problem):
    assert parse_number(raw) == (expected, problem)
