"""Read-side metrics on top of the SQL views. The dashboard, the PDFs and the tests all call these functions,
so a number shown on screen and printed in a report comes from the same query.

`month` is a date (first of month) or None for the whole reporting period.
`sites` is a list of site codes or None for all sites.
"""

from datetime import date
from decimal import Decimal

import psycopg

# Site status rules (one place). Evaluated on the selected period. Out-of-range share is out-of-range readings
# / expected readings, so the same rule works for one month and for the whole period.
STATUS_RULES = {
    "red": {"completeness_below": 80.0, "oor_pct_above": 1.0, "stuck_runs_at_least": 1},
    "amber": {"completeness_below": 95.0, "oor_pct_above": 0.0},
}
STATUS_ORDER = {"red": 0, "amber": 1, "green": 2}


def status_of(row: dict) -> tuple[str, list[str]]:
    """Return (status, reasons) for a site-period row (completeness_pct, oor_readings, expected_readings,
    stuck_runs)."""
    r, a = STATUS_RULES["red"], STATUS_RULES["amber"]
    comp = float(row["completeness_pct"] or 0)
    expected = int(row["expected_readings"] or 0)
    oor = 100.0 * int(row["oor_readings"] or 0) / expected if expected else 0.0
    stuck = int(row["stuck_runs"] or 0)
    reasons: list[str] = []
    if comp < r["completeness_below"]:
        reasons.append("low_completeness")
    if oor > r["oor_pct_above"]:
        reasons.append("long_out_of_range")
    if stuck >= r["stuck_runs_at_least"]:
        reasons.append("stuck_sensor")
    if reasons:
        return "red", reasons
    if comp < a["completeness_below"]:
        reasons.append("incomplete")
    if oor > a["oor_pct_above"]:
        reasons.append("out_of_range")
    return ("amber", reasons) if reasons else ("green", [])


def months(conn: psycopg.Connection) -> list[date]:
    return [r["month"] for r in conn.execute("SELECT month FROM v_reporting_months ORDER BY month").fetchall()]


def _filters(month: date | None, sites: list[str] | None, alias: str = "v") -> tuple[str, dict]:
    clauses, params = [], {}
    if month is not None:
        clauses.append(f"{alias}.month = %(month)s")
        params["month"] = month
    if sites:
        clauses.append("s.code = ANY(%(sites)s)")
        params["sites"] = list(sites)
    return (" AND ".join(clauses) or "true"), params


def site_table(conn: psycopg.Connection, month: date | None = None, sites: list[str] | None = None) -> list[dict]:
    """One row per site for the period: averages weighted by readings, min/max, completeness, out of range,
    stuck, status. Ordered worst status first, then code."""
    where, params = _filters(month, sites)
    rows = conn.execute(f"""
        SELECT s.site_id, s.code, s.name, s.city, s.name_ar, s.city_ar, s.kind, s.lat, s.lon,
               s.temp_min AS th_temp_min, s.temp_max AS th_temp_max, s.hum_min AS th_hum_min,
               s.hum_max AS th_hum_max, s.stuck_min_hours,
               sum(v.readings) AS readings, sum(v.valid_readings) AS valid_readings,
               sum(v.expected_readings) AS expected_readings,
               round(100.0 * sum(v.readings) / nullif(sum(v.expected_readings), 0), 2) AS completeness_pct,
               sum(v.temp_avg * v.valid_readings) / nullif(sum(v.valid_readings), 0) AS temp_avg,
               min(v.temp_min) AS temp_min, max(v.temp_max) AS temp_max,
               sum(v.hum_avg * v.valid_readings) / nullif(sum(v.valid_readings), 0) AS hum_avg,
               min(v.hum_min) AS hum_min, max(v.hum_max) AS hum_max,
               sum(v.oor_readings) AS oor_readings, sum(v.oor_minutes) AS oor_minutes,
               sum(v.temp_out_readings) AS temp_out_readings, sum(v.hum_out_readings) AS hum_out_readings,
               sum(v.oor_events) AS oor_events, max(v.longest_event_minutes) AS longest_event_minutes,
               sum(v.unreliable_readings) AS unreliable_readings, sum(v.stuck_runs) AS stuck_runs
        FROM sites s JOIN v_site_month v USING (site_id)
        WHERE {where}
        GROUP BY s.site_id ORDER BY s.code""", params).fetchall()
    for r in rows:
        r["status"], r["status_reasons"] = status_of(r)
    rows.sort(key=lambda r: (STATUS_ORDER[r["status"]], r["code"]))
    return rows


def kpis(conn: psycopg.Connection, month: date | None = None, sites: list[str] | None = None) -> dict:
    """Headline numbers for the period, computed from the same per-site rows as the table."""
    rows = site_table(conn, month, sites)
    readings = sum(int(r["readings"] or 0) for r in rows)
    expected = sum(int(r["expected_readings"] or 0) for r in rows)
    with_data = [r for r in rows if r["valid_readings"]]

    def wavg(key: str) -> Decimal | None:
        """Average over valid (not unreliable) readings, weighted by each site's valid readings."""
        n = sum(int(r["valid_readings"]) for r in with_data)
        return sum(r[key] * int(r["valid_readings"]) for r in with_data) / n if n else None

    return {
        "sites": len(rows),
        "readings": readings,
        "valid_readings": sum(int(r["valid_readings"] or 0) for r in rows),
        "unreliable_readings": sum(int(r["unreliable_readings"] or 0) for r in rows),
        "expected_readings": expected,
        "completeness_pct": round(Decimal(100) * readings / expected, 2) if expected else None,
        "temp_avg": wavg("temp_avg"),
        "temp_min": min((r["temp_min"] for r in with_data), default=None),
        "temp_max": max((r["temp_max"] for r in with_data), default=None),
        "hum_avg": wavg("hum_avg"),
        "oor_readings": sum(int(r["oor_readings"] or 0) for r in rows),
        "oor_minutes": sum(int(r["oor_minutes"] or 0) for r in rows),
        "oor_events": sum(int(r["oor_events"] or 0) for r in rows),
        "stuck_runs": sum(int(r["stuck_runs"] or 0) for r in rows),
        "status_counts": {s: sum(1 for r in rows if r["status"] == s) for s in ("green", "amber", "red")},
    }


def daily(conn: psycopg.Connection, month: date | None = None, sites: list[str] | None = None) -> list[dict]:
    where, params = _filters(month, sites)
    return conn.execute(f"""
        SELECT s.code, v.day, v.readings, v.valid_readings, v.expected_readings, v.completeness_pct, v.temp_avg,
               v.temp_min, v.temp_max, v.hum_avg, v.hum_min, v.hum_max, v.oor_readings, v.oor_minutes, v.oor_events,
               v.unreliable_readings
        FROM v_site_day v JOIN sites s USING (site_id) WHERE {where} ORDER BY s.code, v.day""", params).fetchall()


def monthly(conn: psycopg.Connection, sites: list[str] | None = None) -> list[dict]:
    where, params = _filters(None, sites)
    rows = conn.execute(f"""
        SELECT s.code, s.name, v.* FROM v_site_month v JOIN sites s USING (site_id)
        WHERE {where} ORDER BY s.code, v.month""", params).fetchall()
    for r in rows:
        r["status"], r["status_reasons"] = status_of(r)
    return rows


def hourly(conn: psycopg.Connection, site: str, month: date | None = None) -> list[dict]:
    where, params = _filters(month, [site])
    return conn.execute(f"""
        SELECT v.reading_ts, v.temperature, v.humidity, v.temp_out, v.hum_out, v.stuck
        FROM v_readings_flagged v JOIN sites s USING (site_id) WHERE {where} ORDER BY v.reading_ts""",
                        params).fetchall()


def hour_profile(conn: psycopg.Connection, month: date | None = None, sites: list[str] | None = None) -> list[dict]:
    """Average temperature/humidity by hour of day (0-23) per site, valid readings only."""
    where, params = _filters(month, sites)
    return conn.execute(f"""
        SELECT s.code, v.hour, avg(v.temperature) AS temp_avg, avg(v.humidity) AS hum_avg, count(*) AS readings
        FROM v_readings_flagged v JOIN sites s USING (site_id) WHERE {where} AND NOT v.stuck
        GROUP BY s.code, v.hour ORDER BY s.code, v.hour""", params).fetchall()


def events(conn: psycopg.Connection, month: date | None = None, sites: list[str] | None = None,
           limit: int | None = None) -> list[dict]:
    where, params = _filters(month, sites)
    lim = f"LIMIT {int(limit)}" if limit else ""
    return conn.execute(f"""
        SELECT s.code, s.name, s.name_ar, v.start_ts, v.end_ts, v.readings, v.minutes, v.temp_out_readings,
               v.hum_out_readings,
               v.max_temperature, v.min_temperature, v.max_humidity, v.min_humidity
        FROM v_oor_events v JOIN sites s USING (site_id) WHERE {where}
        ORDER BY v.minutes DESC, v.start_ts {lim}""", params).fetchall()


def stuck_runs(conn: psycopg.Connection, month: date | None = None, sites: list[str] | None = None) -> list[dict]:
    where, params = _filters(month, sites)
    return conn.execute(f"""
        SELECT s.code, s.name, v.start_ts, v.end_ts, v.readings, v.temperature, v.humidity
        FROM v_stuck_runs v JOIN sites s USING (site_id) WHERE {where} ORDER BY v.start_ts""", params).fetchall()
