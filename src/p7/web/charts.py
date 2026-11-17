"""JSON for the ECharts charts (rendered by static/app.js). Values are rounded the same way as the tables."""

from collections import defaultdict
from datetime import date

import psycopg

from p7 import metrics
from p7.i18n import Translator


def _f(v, dp: int = 1):
    return None if v is None else round(float(v), dp)


def overview(conn: psycopg.Connection, t: Translator, month: date | None, sites: list[str] | None,
             table: list[dict]) -> dict:
    daily = metrics.daily(conn, month, sites)
    series: dict[str, list] = defaultdict(list)
    for r in daily:
        series[r["code"]].append([r["day"].isoformat(), _f(r["temp_avg"])])
    ordered = sorted(table, key=lambda r: r["code"])
    return {
        "daily_temp": {"type": "lines", "title": t.plain("chart_daily_avg_temp"), "unit": "°C",
                       "series": [{"name": k, "data": v} for k, v in sorted(series.items())]},
        "site_compare": {
            "type": "bars", "title": t.plain("chart_site_compare"),
            "categories": [r["code"] for r in ordered],
            "series": [
                {"name": t.plain("avg_temp") + " (°C)", "data": [_f(r["temp_avg"]) for r in ordered]},
                {"name": t.plain("time_out_of_range") + " (" + t.plain("unit_h") + ")",
                 "data": [_f(int(r["oor_minutes"] or 0) / 60) for r in ordered], "axis": 1},
            ],
        },
    }


def site(conn: psycopg.Connection, t: Translator, code: str, month: date | None, row: dict) -> dict:
    hourly = metrics.hourly(conn, code, month)
    temp, hum, temp_out, hum_out, stuck_ranges = [], [], [], [], []
    run_start = prev = None
    for r in hourly:
        x = r["reading_ts"].strftime("%Y-%m-%d %H:%M")
        if r["stuck"]:
            # unreliable: drawn as a grey band, not as a measured value
            temp.append([x, None])
            hum.append([x, None])
            run_start = run_start or x
            prev = x
            continue
        if run_start:
            stuck_ranges.append([run_start, prev])
            run_start = None
        temp.append([x, _f(r["temperature"])])
        hum.append([x, _f(r["humidity"])])
        if r["temp_out"]:
            temp_out.append([x, _f(r["temperature"])])
        if r["hum_out"]:
            hum_out.append([x, _f(r["humidity"])])
    if run_start:
        stuck_ranges.append([run_start, prev])
    profile = metrics.hour_profile(conn, month, [code])
    daily = metrics.daily(conn, month, [code])
    unreliable = t.plain("unreliable_readings")
    return {
        "temp": {"type": "timeseries", "title": t.plain("chart_temperature"), "unit": "°C",
                 "name": t.plain("temperature"), "data": temp, "out": temp_out, "out_name": t.plain("out_of_range"),
                 "min": _f(row["th_temp_min"]), "max": _f(row["th_temp_max"]),
                 "limit_names": [t.plain("limit_min"), t.plain("limit_max")],
                 "stuck": stuck_ranges, "stuck_name": unreliable},
        "hum": {"type": "timeseries", "title": t.plain("chart_humidity"), "unit": "%",
                "name": t.plain("humidity"), "data": hum, "out": hum_out, "out_name": t.plain("out_of_range"),
                "min": _f(row["th_hum_min"]), "max": _f(row["th_hum_max"]),
                "limit_names": [t.plain("limit_min"), t.plain("limit_max")],
                "stuck": stuck_ranges, "stuck_name": unreliable},
        "daily": {"type": "band", "title": t.plain("chart_daily_range"), "unit": "°C",
                  "x": [r["day"].isoformat() for r in daily],
                  "names": [t.plain("min"), t.plain("avg"), t.plain("max")],
                  "min": [_f(r["temp_min"]) for r in daily], "avg": [_f(r["temp_avg"]) for r in daily],
                  "max": [_f(r["temp_max"]) for r in daily]},
        "profile": _profile(t, profile, by_site=False),
    }


def _profile(t: Translator, rows: list[dict], by_site: bool) -> dict:
    hours = list(range(24))
    if by_site:
        per: dict[str, dict[int, float]] = defaultdict(dict)
        for r in rows:
            per[r["code"]][r["hour"]] = _f(r["temp_avg"])
        return {"type": "profile", "title": t.plain("chart_hour_profile"), "unit": "°C",
                "x": [f"{h:02d}:00" for h in hours],
                "series": [{"name": k, "data": [v.get(h) for h in hours]} for k, v in sorted(per.items())]}
    by_hour = {r["hour"]: r for r in rows}
    return {"type": "profile", "title": t.plain("chart_hour_profile"), "unit": "°C", "unit2": "%",
            "x": [f"{h:02d}:00" for h in hours],
            "series": [
                {"name": t.plain("temperature") + " (°C)", "data": [_f(by_hour[h]["temp_avg"]) if h in by_hour
                                                                     else None for h in hours]},
                {"name": t.plain("humidity") + " (%)", "axis": 1,
                 "data": [_f(by_hour[h]["hum_avg"]) if h in by_hour else None for h in hours]},
            ]}


def compare(conn: psycopg.Connection, t: Translator, month: date | None, sites: list[str] | None,
            table: list[dict]) -> dict:
    ordered = sorted(table, key=lambda r: r["code"])
    cats = [r["code"] for r in ordered]
    monthly = metrics.monthly(conn, sites)
    months = sorted({r["month"] for r in monthly})
    per: dict[str, dict] = defaultdict(dict)
    for r in monthly:
        per[r["code"]][r["month"]] = _f(r["temp_avg"])
    return {
        "temp_range": {"type": "bars", "title": t.plain("chart_temp_by_site"), "categories": cats, "unit": "°C",
                       "series": [{"name": t.plain("min"), "data": [_f(r["temp_min"]) for r in ordered]},
                                  {"name": t.plain("avg"), "data": [_f(r["temp_avg"]) for r in ordered]},
                                  {"name": t.plain("max"), "data": [_f(r["temp_max"]) for r in ordered]}]},
        "quality": {"type": "bars", "title": t.plain("chart_quality_by_site"), "categories": cats,
                    "series": [{"name": t.plain("completeness") + " (%)",
                                "data": [_f(r["completeness_pct"]) for r in ordered]},
                               {"name": t.plain("time_out_of_range") + " (" + t.plain("unit_h") + ")", "axis": 1,
                                "data": [_f(int(r["oor_minutes"] or 0) / 60) for r in ordered]}]},
        "profile": _profile(t, metrics.hour_profile(conn, month, sites), by_site=True),
        "trend": {"type": "lines_cat", "title": t.plain("chart_monthly_trend"), "unit": "°C",
                  "x": [t.month(m) for m in months],
                  "series": [{"name": k, "data": [v.get(m) for m in months]} for k, v in sorted(per.items())]},
    }
