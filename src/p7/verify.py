"""Compare what ingestion stored against the generator's manifest.json (ground truth). Used by tests and
`python -m p7 verify-manifest`."""

import json
from datetime import datetime
from pathlib import Path

import psycopg


def compare_with_manifest(conn: psycopg.Connection, manifest_path: Path) -> dict:
    m = json.loads(manifest_path.read_text())
    rows = conn.execute(
        """SELECT DISTINCT ON (u.filename) u.filename, s.code AS site, to_char(u.month, 'YYYY-MM') AS month,
                  u.status, u.detected_by, u.inserted + u.updated + u.unchanged AS readings, u.rejected, u.warnings,
                  (SELECT coalesce(jsonb_object_agg(reason, n), '{}') FROM
                      (SELECT reason, count(*) n FROM row_rejects rr WHERE rr.upload_id = u.upload_id GROUP BY 1) x)
                      AS reasons
           FROM uploads u LEFT JOIN sites s USING (site_id)
           WHERE u.status = 'loaded' ORDER BY u.filename, u.upload_id""").fetchall()
    by_name = {r["filename"]: r for r in rows}
    files = []
    for f in m["files"]:
        got = by_name.get(f["filename"])
        problems = []
        if got is None:
            problems.append("not loaded")
        else:
            if (got["site"], got["month"]) != (f["site"], f["month"]):
                problems.append(f"detected {got['site']} {got['month']}")
            if got["readings"] != f["expected_readings"]:
                problems.append(f"readings {got['readings']} != expected {f['expected_readings']}")
            if dict(got["reasons"]) != f["expected_rejects"]:
                problems.append(f"rejects {dict(got['reasons'])} != expected {f['expected_rejects']}")
        files.append({"filename": f["filename"], "ok": not problems, "problems": problems,
                      "expected_rejects": f["expected_rejects"], "planted": f["problems"],
                      "detected_by": got and got["detected_by"], "warnings": got and got["warnings"]})

    stored = conn.execute("""SELECT s.code, count(*) n FROM readings JOIN sites s USING (site_id)
                             GROUP BY 1""").fetchall()
    stuck = conn.execute("""SELECT s.code AS site, r.start_ts, r.end_ts, r.readings FROM v_stuck_runs r
                            JOIN sites s USING (site_id) ORDER BY 1, 2""").fetchall()
    events = []
    for e in m["events"]:
        hit = conn.execute(
            """SELECT count(*) AS n, coalesce(sum(readings), 0) AS readings, min(start_ts) AS first, max(end_ts) AS last
               FROM v_oor_events v JOIN sites s USING (site_id)
               WHERE s.code = %s AND v.start_ts <= %s AND v.end_ts >= %s""",
            (e["site"], datetime.fromisoformat(e["end"]), datetime.fromisoformat(e["start"]))).fetchone()
        events.append({**e, "detected_events": hit["n"], "detected_readings": hit["readings"],
                       "detected_first": hit["first"] and hit["first"].isoformat(),
                       "detected_last": hit["last"] and hit["last"].isoformat()})
    outages = []
    for o in m["outages"]:
        n = conn.execute("""SELECT count(*) AS n FROM readings JOIN sites s USING (site_id)
                            WHERE s.code = %s AND reading_ts BETWEEN %s AND %s""",
                         (o["site"], datetime.fromisoformat(o["start"]), datetime.fromisoformat(o["end"]))).fetchone()
        outages.append({**o, "readings_in_window": n["n"]})
    expected_stuck = [(s["site"], s["start"], s["end"], s["readings"]) for s in m["stuck_runs"]]
    got_stuck = [(s["site"], s["start_ts"].isoformat(), s["end_ts"].isoformat(), s["readings"]) for s in stuck]
    return {
        "files_total": len(files),
        "files_ok": sum(f["ok"] for f in files),
        "files": files,
        "readings_stored": sum(r["n"] for r in stored),
        "readings_expected": m["totals"]["expected_readings"],
        "rejects_expected": m["totals"]["expected_rejects"],
        "stuck_expected": expected_stuck,
        "stuck_detected": got_stuck,
        "events": events,
        "outages": outages,
    }
