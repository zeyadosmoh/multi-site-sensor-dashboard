"""Every number a PDF prints, as a JSON-able dict. Built from p7.metrics (the same queries as the dashboard) and
formatted with p7.fmt (the same rounding). Saved next to each PDF and in the reports table, so tests can check
PDF text == facts == database."""

from datetime import date, datetime

import psycopg

from p7 import fmt, metrics
from p7.generate.files import SYNTHETIC_NOTE
from p7.sites import site_by_code


def _kpis(r: dict) -> dict:
    return {
        "readings": fmt.number(r["readings"], 0),
        "expected_readings": fmt.number(r["expected_readings"], 0),
        "completeness_pct": fmt.number(r["completeness_pct"]),
        "unreliable_readings": fmt.number(r["unreliable_readings"], 0),
        "valid_readings": fmt.number(r["valid_readings"], 0),
        "temp_avg": fmt.number(r["temp_avg"]),
        "temp_min": fmt.number(r["temp_min"]),
        "temp_max": fmt.number(r["temp_max"]),
        "hum_avg": fmt.number(r["hum_avg"]),
        "hum_min": fmt.number(r["hum_min"]),
        "hum_max": fmt.number(r["hum_max"]),
        "oor_readings": fmt.number(r["oor_readings"], 0),
        "oor_hours": fmt.hours(r["oor_minutes"]),
        "oor_events": fmt.number(r["oor_events"], 0),
    }


def _event(e: dict) -> dict:
    return {"site": e["code"], "start": fmt.ts(e["start_ts"]), "end": fmt.ts(e["end_ts"]),
            "readings": fmt.number(e["readings"], 0), "hours": fmt.hours(e["minutes"]),
            "max_temperature": fmt.number(e["max_temperature"]), "max_humidity": fmt.number(e["max_humidity"]),
            "kind": "both" if e["temp_out_readings"] and e["hum_out_readings"]
            else ("temperature" if e["temp_out_readings"] else "humidity")}


def _stuck(r: dict) -> dict:
    return {"site": r["code"], "start": fmt.ts(r["start_ts"]), "end": fmt.ts(r["end_ts"]),
            "readings": fmt.number(r["readings"], 0), "temperature": fmt.number(r["temperature"]),
            "humidity": fmt.number(r["humidity"])}


def _uploads(conn, month: date, site_id: int | None) -> list[dict]:
    rows = conn.execute(
        """SELECT u.upload_id, u.filename, s.code, u.inserted, u.updated, u.unchanged, u.rejected, u.finished_at,
                  (SELECT coalesce(jsonb_object_agg(reason, n), '{}') FROM
                     (SELECT reason, count(*) n FROM row_rejects r WHERE r.upload_id = u.upload_id GROUP BY 1) x)
                  AS reasons
           FROM uploads u JOIN sites s USING (site_id)
           WHERE u.status = 'loaded' AND u.month = %s AND (%s::int IS NULL OR u.site_id = %s)
           ORDER BY s.code, u.upload_id""", (month, site_id, site_id)).fetchall()
    return [{"upload_id": r["upload_id"], "filename": r["filename"], "site": r["code"],
             "rows_loaded": fmt.number(r["inserted"] + r["updated"] + r["unchanged"], 0),
             "rejected": fmt.number(r["rejected"], 0), "reasons": dict(sorted(r["reasons"].items()))} for r in rows]


def site_month(conn: psycopg.Connection, code: str, month: date) -> dict:
    site = site_by_code(conn, code)
    [row] = metrics.site_table(conn, month, [code])
    return {
        "kind": "site_month",
        "notice": SYNTHETIC_NOTE,
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "month": f"{month:%Y-%m}",
        "site": {"code": site["code"], "name": site["name"], "city": site["city"], "kind": site["kind"],
                 "name_ar": site["name_ar"], "city_ar": site["city_ar"]},
        "thresholds": {"temp_min": fmt.number(site["temp_min"]), "temp_max": fmt.number(site["temp_max"]),
                       "hum_min": fmt.number(site["hum_min"]), "hum_max": fmt.number(site["hum_max"]),
                       "stuck_min_hours": str(site["stuck_min_hours"])},
        "status": row["status"],
        "status_reasons": row["status_reasons"],
        "kpis": {**_kpis(row), "longest_event_hours": fmt.hours(row["longest_event_minutes"]),
                 "stuck_runs": fmt.number(row["stuck_runs"], 0)},
        "events": [_event(e) for e in metrics.events(conn, month, [code], limit=10)],
        "events_total": fmt.number(row["oor_events"], 0),
        "stuck_runs": [_stuck(r) for r in metrics.stuck_runs(conn, month, [code])],
        "uploads": _uploads(conn, month, site["site_id"]),
        "daily": [{"day": d["day"].isoformat(), "temp_min": _f(d["temp_min"]), "temp_avg": _f(d["temp_avg"]),
                   "temp_max": _f(d["temp_max"]), "hum_avg": _f(d["hum_avg"]),
                   "completeness_pct": _f(d["completeness_pct"])} for d in metrics.daily(conn, month, [code])],
        "profile": [{"hour": p["hour"], "temp_avg": _f(p["temp_avg"]), "hum_avg": _f(p["hum_avg"])}
                    for p in metrics.hour_profile(conn, month, [code])],
    }


def summary_month(conn: psycopg.Connection, month: date) -> dict:
    table = sorted(metrics.site_table(conn, month), key=lambda r: (metrics.STATUS_ORDER[r["status"]], r["code"]))
    k = metrics.kpis(conn, month)
    return {
        "kind": "summary_month",
        "notice": SYNTHETIC_NOTE,
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "month": f"{month:%Y-%m}",
        "kpis": {
            "sites": str(k["sites"]),
            "readings": fmt.number(k["readings"], 0),
            "expected_readings": fmt.number(k["expected_readings"], 0),
            "completeness_pct": fmt.number(k["completeness_pct"]),
            "unreliable_readings": fmt.number(k["unreliable_readings"], 0),
            "temp_avg": fmt.number(k["temp_avg"]),
            "temp_min": fmt.number(k["temp_min"]),
            "temp_max": fmt.number(k["temp_max"]),
            "hum_avg": fmt.number(k["hum_avg"]),
            "oor_hours": fmt.hours(k["oor_minutes"]),
            "oor_events": fmt.number(k["oor_events"], 0),
            "stuck_runs": fmt.number(k["stuck_runs"], 0),
        },
        "status_counts": k["status_counts"],
        "sites": [{"code": r["code"], "name": r["name"], "city": r["city"], "name_ar": r["name_ar"],
                   "city_ar": r["city_ar"], "status": r["status"],
                   "status_reasons": r["status_reasons"], **_kpis(r)} for r in table],
        "events": [_event(e) for e in metrics.events(conn, month, None, limit=10)],
        "stuck_runs": [_stuck(r) for r in metrics.stuck_runs(conn, month)],
        "uploads": _uploads(conn, month, None),
    }


def _f(v):
    return None if v is None else round(float(v), 2)
