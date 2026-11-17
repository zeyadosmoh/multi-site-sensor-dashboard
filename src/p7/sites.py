"""Sites: seeding from the generator's site list, lookups, admin threshold edits (audited)."""

import json

import psycopg

from p7.generate.sites import SITES

THRESHOLD_FIELDS = ("temp_min", "temp_max", "hum_min", "hum_max", "stuck_min_hours")


def seed_sites(conn: psycopg.Connection) -> int:
    n = 0
    with conn.transaction():
        for s in SITES:
            th = s.thresholds
            cur = conn.execute(
                """INSERT INTO sites (code, name, city, kind, lat, lon, temp_min, temp_max, hum_min, hum_max)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) ON CONFLICT (code) DO NOTHING""",
                (s.code, s.name, s.city, s.kind, s.lat, s.lon,
                 th["temp_min"], th["temp_max"], th["hum_min"], th["hum_max"]))
            n += cur.rowcount
    return n


def all_sites(conn: psycopg.Connection) -> list[dict]:
    return conn.execute("SELECT * FROM sites ORDER BY code").fetchall()


def site_by_code(conn: psycopg.Connection, code: str) -> dict | None:
    return conn.execute("SELECT * FROM sites WHERE code = %s", (code,)).fetchone()


class ThresholdError(ValueError):
    pass


def validate_thresholds(values: dict) -> dict:
    try:
        v = {k: float(values[k]) for k in ("temp_min", "temp_max", "hum_min", "hum_max")}
        v["stuck_min_hours"] = int(values["stuck_min_hours"])
    except (KeyError, TypeError, ValueError) as e:
        raise ThresholdError("all five values are required and must be numbers") from e
    if not -40 <= v["temp_min"] < v["temp_max"] <= 70:
        raise ThresholdError("temperature: need -40 <= min < max <= 70")
    if not 0 <= v["hum_min"] < v["hum_max"] <= 100:
        raise ThresholdError("humidity: need 0 <= min < max <= 100")
    if not 2 <= v["stuck_min_hours"] <= 168:
        raise ThresholdError("stuck sensor: need 2 to 168 hours")
    return v


def update_thresholds(conn: psycopg.Connection, site_id: int, values: dict, user_id: int | None) -> bool:
    """Validate, write, audit and mark that site's reports (and the monthly summaries) stale. Returns changed?"""
    new = validate_thresholds(values)
    with conn.transaction():
        row = conn.execute(f"SELECT {', '.join(THRESHOLD_FIELDS)} FROM sites WHERE site_id = %s FOR UPDATE",
                           (site_id,)).fetchone()
        if row is None:
            raise ThresholdError("unknown site")
        old = {k: float(row[k]) if k != "stuck_min_hours" else int(row[k]) for k in THRESHOLD_FIELDS}
        if old == new:
            return False
        conn.execute("""UPDATE sites SET temp_min=%(temp_min)s, temp_max=%(temp_max)s, hum_min=%(hum_min)s,
                        hum_max=%(hum_max)s, stuck_min_hours=%(stuck_min_hours)s WHERE site_id=%(id)s""",
                     {**new, "id": site_id})
        conn.execute("INSERT INTO threshold_audit (site_id, user_id, old_values, new_values) VALUES (%s,%s,%s,%s)",
                     (site_id, user_id, json.dumps(old), json.dumps(new)))
        conn.execute("UPDATE reports SET stale = true WHERE site_id = %s OR kind = 'summary_month'", (site_id,))
    return True
