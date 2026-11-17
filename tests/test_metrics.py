"""Metrics math on small, hand-checked examples (expected numbers worked out in the comments)."""

from datetime import date, datetime, timedelta
from decimal import Decimal

import pytest

from p7 import metrics
from p7.sites import update_thresholds
from tests.helpers import load_readings

MAY = date(2026, 5, 1)
D = datetime(2026, 5, 1)

# RUH-01 is a warehouse: 15.0..30.0 C, 20.0..65.0 %, stuck after 6 hours.
#   hour  temp  hum    flags
#   00    20.0  40.0
#   01    22.0  40.0
#   02    31.0  40.0   temp out            } event 1 (3 readings, 180 min)
#   03    32.5  70.0   temp out + hum out  }
#   04    25.0  66.0   hum out             }
#   05    --    --     missing
#   06    30.0  65.0   in range (limits are inclusive)
#   07    30.1  50.0   temp out            } event 2 (1 reading, 60 min)
#   08-13 24.0  50.0   6 identical readings -> one stuck run (6 x 60 min >= 6 h)
#   14    24.0  50.1   humidity differs -> run ends
FIXTURE = [(D + timedelta(hours=h), t, rh) for h, t, rh in [
    (0, 20.0, 40.0), (1, 22.0, 40.0), (2, 31.0, 40.0), (3, 32.5, 70.0), (4, 25.0, 66.0),
    (6, 30.0, 65.0), (7, 30.1, 50.0)] + [(h, 24.0, 50.0) for h in range(8, 14)] + [(14, 24.0, 50.1)]]
# 14 readings.
# temperature sum = 20+22+31+32.5+25+30+30.1 + 7x24 = 190.6 + 168 = 358.6 -> avg 358.6/14 = 25.614285...
# humidity sum    = 40x3 + 70 + 66 + 65 + 50x7 + 0.1 = 120+70+66+65+350+0.1 = 671.1 -> wait: 50 x 6 + 50.1 + 50(07)
#   hours 07..13 = 7 readings at 50.0 (350.0), hour 14 = 50.1 -> 120+70+66+65+350+50.1 = 721.1 -> avg 51.507142...


@pytest.fixture
def day(conn):
    load_readings(conn, "RUH-01", FIXTURE)
    return conn


def one_day(conn):
    [row] = [r for r in metrics.daily(conn, MAY, ["RUH-01"]) if r["day"] == date(2026, 5, 1)]
    return row


def test_daily_numbers(day):
    r = one_day(day)
    assert r["readings"] == 14 and r["expected_readings"] == 24
    assert r["completeness_pct"] == Decimal("58.33")           # 14 / 24
    assert round(r["temp_avg"], 6) == Decimal("25.614286")     # 358.6 / 14
    assert (r["temp_min"], r["temp_max"]) == (Decimal("20.0"), Decimal("32.5"))
    assert round(r["hum_avg"], 6) == Decimal("51.507143")      # 721.1 / 14
    assert (r["hum_min"], r["hum_max"]) == (Decimal("40.0"), Decimal("70.0"))
    assert r["oor_readings"] == 4 and r["oor_minutes"] == 240  # hours 02, 03, 04, 07
    assert r["oor_events"] == 2
    assert r["stuck_readings"] == 6


def test_events_and_stuck_runs(day):
    ev = sorted(metrics.events(day, MAY, ["RUH-01"]), key=lambda e: e["start_ts"])
    assert [(e["start_ts"].hour, e["end_ts"].hour, e["readings"], e["minutes"], e["temp_out_readings"],
             e["hum_out_readings"]) for e in ev] == [(2, 4, 3, 180, 2, 2), (7, 7, 1, 60, 1, 0)]
    assert ev[0]["max_temperature"] == Decimal("32.5") and ev[0]["max_humidity"] == Decimal("70.0")
    [run] = metrics.stuck_runs(day, MAY, ["RUH-01"])
    assert (run["start_ts"].hour, run["end_ts"].hour, run["readings"]) == (8, 13, 6)
    assert (run["temperature"], run["humidity"]) == (Decimal("24.0"), Decimal("50.0"))


def test_month_row_and_status(day):
    [r] = metrics.site_table(day, MAY, ["RUH-01"])
    assert r["expected_readings"] == 744                      # 31 days x 24
    assert r["completeness_pct"] == Decimal("1.88")           # 14 / 744 = 1.8817
    assert r["oor_events"] == 2 and r["longest_event_minutes"] == 180
    assert r["stuck_runs"] == 1
    assert r["status"] == "red"
    assert set(r["status_reasons"]) == {"low_completeness", "stuck_sensor"}


def test_five_identical_readings_are_not_stuck(conn):
    rows = [(D + timedelta(hours=h), 22.0, 45.0) for h in range(5)] + [(D + timedelta(hours=5), 22.1, 45.0)]
    load_readings(conn, "RUH-01", rows)
    assert metrics.stuck_runs(conn, MAY, ["RUH-01"]) == []
    # same temperature but humidity moving is not stuck either
    rows2 = [(D + timedelta(hours=h), 22.0, 45.0 + h / 10) for h in range(10)]
    load_readings(conn, "RUH-02", rows2)
    assert metrics.stuck_runs(conn, MAY, ["RUH-02"]) == []


def test_a_gap_breaks_a_stuck_run_and_an_event(conn):
    # 4 identical, gap, 4 identical: not one run of 8
    rows = [(D + timedelta(hours=h), 22.0, 45.0) for h in (0, 1, 2, 3, 5, 6, 7, 8)]
    load_readings(conn, "RUH-01", rows)
    assert metrics.stuck_runs(conn, MAY, ["RUH-01"]) == []
    # out of range at 10, 11, (12 missing), 13 -> two events
    rows = [(D + timedelta(hours=h), 35.0, 45.0 + h / 10) for h in (10, 11, 13)]
    load_readings(conn, "RUH-02", rows)
    ev = metrics.events(conn, MAY, ["RUH-02"])
    assert sorted(e["readings"] for e in ev) == [1, 2]


def test_event_crossing_midnight_counts_on_its_start_day_and_month(conn):
    start = datetime(2026, 5, 31, 22)
    rows = [(start + timedelta(hours=h), 35.0, 45.0 + h / 10) for h in range(4)]  # 22, 23, 00, 01
    load_readings(conn, "RUH-01", rows)
    load_readings(conn, "RUH-01", [], month="2026-06")
    [e] = metrics.events(conn, None, ["RUH-01"])
    assert e["readings"] == 4 and e["minutes"] == 240
    may = {r["day"]: r for r in metrics.daily(conn, MAY, ["RUH-01"])}
    june = {r["day"]: r for r in metrics.daily(conn, date(2026, 6, 1), ["RUH-01"])}
    assert may[date(2026, 5, 31)]["oor_events"] == 1 and may[date(2026, 5, 31)]["oor_readings"] == 2
    assert june[date(2026, 6, 1)]["oor_events"] == 0 and june[date(2026, 6, 1)]["oor_readings"] == 2


def test_threshold_edit_changes_metrics_immediately_and_is_audited(day):
    changed = update_thresholds(day, 1, {"temp_min": 15, "temp_max": 33, "hum_min": 20, "hum_max": 65,
                                         "stuck_min_hours": 7}, user_id=None)
    assert changed
    r = one_day(day)
    # temp_max 33: only humidity at 03 (70) and 04 (66) remain out -> 2 readings, one event; 6 identical < 7 h
    assert (r["oor_readings"], r["oor_events"], r["stuck_readings"]) == (2, 1, 0)
    audit = day.execute("SELECT old_values, new_values FROM threshold_audit").fetchone()
    assert audit["old_values"]["temp_max"] == 30.0 and audit["new_values"]["temp_max"] == 33.0
    # writing the same values again is not a change
    assert not update_thresholds(day, 1, {"temp_min": 15, "temp_max": 33, "hum_min": 20, "hum_max": 65,
                                          "stuck_min_hours": 7}, user_id=None)


def test_reading_interval_setting(day):
    # with a 30-minute interval the same 14 readings are 14/48 of a day and each out-of-range reading is 30 min
    day.execute("UPDATE app_config SET reading_interval_minutes = 30")
    r = one_day(day)
    assert r["expected_readings"] == 48 and r["completeness_pct"] == Decimal("29.17") and r["oor_minutes"] == 120


def test_kpis_weight_by_readings_and_count_status(conn):
    # RUH-01: 2 readings avg 20; RUH-02: 6 readings avg 30 -> overall avg (40 + 180) / 8 = 27.5
    load_readings(conn, "RUH-01", [(D + timedelta(hours=h), 20.0, 40.0 + h) for h in range(2)])
    load_readings(conn, "RUH-02", [(D + timedelta(hours=h), 30.0, 40.0 + h) for h in range(6)])
    k = metrics.kpis(conn, MAY, ["RUH-01", "RUH-02"])
    assert k["temp_avg"] == Decimal("27.5")
    assert k["readings"] == 8 and k["expected_readings"] == 2 * 744
    assert k["completeness_pct"] == Decimal("0.54")  # 8 / 1488 = 0.5376
    assert k["status_counts"] == {"green": 0, "amber": 0, "red": 2}


@pytest.mark.parametrize("comp, oor, expected_readings, stuck, status", [
    (100, 0, 744, 0, "green"),
    (95, 0, 744, 0, "green"),
    (94.99, 0, 744, 0, "amber"),
    (100, 1, 744, 0, "amber"),
    (100, 7, 744, 0, "amber"),   # 7 / 744 = 0.94 % <= 1 %
    (100, 8, 744, 0, "red"),     # 8 / 744 = 1.08 % > 1 %
    (79.99, 0, 744, 0, "red"),
    (100, 0, 744, 1, "red"),
])
def test_status_rules(comp, oor, expected_readings, stuck, status):
    row = {"completeness_pct": comp, "oor_readings": oor, "expected_readings": expected_readings, "oor_minutes": 0,
           "stuck_runs": stuck}
    assert metrics.status_of(row)[0] == status


def test_site_with_no_file_for_a_reported_month_shows_zero_completeness(conn):
    load_readings(conn, "RUH-01", [(D, 22.0, 45.0)])
    rows = {r["code"]: r for r in metrics.site_table(conn, MAY)}
    assert len(rows) == 13
    assert rows["JED-01"]["readings"] == 0 and rows["JED-01"]["completeness_pct"] == Decimal("0.00")
    assert rows["JED-01"]["status"] == "red"
