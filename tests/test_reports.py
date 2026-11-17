"""PDF reports: every number printed in the PDF equals facts.json, and facts equal an independent plain-Python
recomputation from the raw `readings` table (no SQL views involved). Uses the committed sample month
(data/sample/sensors_2026-08.zip: 13 files, with KHB-01's stuck sensor and JED-01's AC failure)."""

import re
import unicodedata
from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from pypdf import PdfReader

from p7 import fmt
from p7.i18n import table
from p7.ingest.pipeline import ingest_paths
from p7.reports.build import build_site_month, build_summary_month, ensure_report, report_index
from p7.sites import update_thresholds
from tests.conftest import TABLES

pytestmark = pytest.mark.pdf
SAMPLE = Path(__file__).parent.parent / "data" / "sample" / "sensors_2026-08.zip"
AUG = date(2026, 8, 1)
LANGS = [lang for lang in ("en", "ar") if table(lang)]


@pytest.fixture(scope="module")
def loaded(_db, settings, tmp_path_factory):
    with _db.transaction():
        _db.execute(f"TRUNCATE {TABLES} RESTART IDENTITY CASCADE")
    from p7.sites import seed_sites

    seed_sites(_db)
    results = ingest_paths(_db, [SAMPLE])
    assert [r.status for r in results] == ["loaded"] * 13
    s = settings.model_copy(update={"reports_dir": tmp_path_factory.mktemp("reports")})
    return _db, s


def pdf_text(path: Path) -> str:
    text = "\n".join(p.extract_text() for p in PdfReader(path).pages)
    return re.sub(r"[ \t]+", " ", text)


# ---------- independent recomputation (plain Python over raw readings) ----------

def recompute(conn, code: str, month: date) -> dict:
    site = conn.execute("SELECT * FROM sites WHERE code = %s", (code,)).fetchone()
    rows = conn.execute("""SELECT reading_ts, temperature, humidity FROM readings
                           WHERE site_id = %s AND reading_ts >= %s AND reading_ts < %s ORDER BY reading_ts""",
                        (site["site_id"], month, date(2026, 9, 1))).fetchall()
    hour = timedelta(hours=1)
    # stuck runs: consecutive hours with identical temperature and humidity, >= stuck_min_hours long
    stuck = set()
    run = [rows[0]] if rows else []
    for prev, cur in zip(rows, rows[1:], strict=False):
        if cur["reading_ts"] - prev["reading_ts"] == hour and (cur["temperature"], cur["humidity"]) == \
                (prev["temperature"], prev["humidity"]):
            run.append(cur)
        else:
            if len(run) >= site["stuck_min_hours"]:
                stuck |= {r["reading_ts"] for r in run}
            run = [cur]
    if len(run) >= site["stuck_min_hours"]:
        stuck |= {r["reading_ts"] for r in run}
    valid = [r for r in rows if r["reading_ts"] not in stuck]

    def out(r):
        return not (site["temp_min"] <= r["temperature"] <= site["temp_max"]) or \
            not (site["hum_min"] <= r["humidity"] <= site["hum_max"])

    oor_ts = {r["reading_ts"] for r in valid if out(r)}
    events, cur_len = [], 0
    for r in rows:  # walk all stored readings: a gap or an unreliable reading ends an event
        ts = r["reading_ts"]
        if ts in oor_ts and cur_len and (ts - hour) in oor_ts:
            cur_len += 1
        elif ts in oor_ts:
            if cur_len:
                events.append(cur_len)
            cur_len = 1
        else:
            if cur_len:
                events.append(cur_len)
            cur_len = 0
    if cur_len:
        events.append(cur_len)
    temps = [r["temperature"] for r in valid]
    hums = [r["humidity"] for r in valid]
    expected = 31 * 24
    return {
        "readings": fmt.number(len(rows), 0),
        "expected_readings": fmt.number(expected, 0),
        "completeness_pct": fmt.number(Decimal(100) * len(rows) / expected),
        "unreliable_readings": fmt.number(len(stuck), 0),
        "valid_readings": fmt.number(len(valid), 0),
        "temp_avg": fmt.number(sum(temps) / len(temps)),
        "temp_min": fmt.number(min(temps)), "temp_max": fmt.number(max(temps)),
        "hum_avg": fmt.number(sum(hums) / len(hums)),
        "hum_min": fmt.number(min(hums)), "hum_max": fmt.number(max(hums)),
        "oor_readings": fmt.number(len(oor_ts), 0),
        "oor_hours": fmt.hours(len(oor_ts) * 60),
        "oor_events": fmt.number(len(events), 0),
        "longest_event_hours": fmt.hours(max(events, default=0) * 60),
        "stuck_runs": fmt.number(1 if stuck else 0, 0),
    }


# ---------- tests ----------

@pytest.mark.parametrize("code", ["KHB-01", "JED-01", "TAB-01"])
def test_site_facts_match_independent_recomputation(loaded, code):
    conn, s = loaded
    path = build_site_month(conn, code, AUG, "en", s)
    import json

    facts = json.loads(path.with_suffix(".facts.json").read_text())
    assert facts["kpis"] == recompute(conn, code, AUG)
    assert "SYNTHETIC" in facts["notice"]


def test_khb_stuck_sensor_is_excluded_in_the_pdf(loaded):
    conn, s = loaded
    path = build_site_month(conn, "KHB-01", AUG, "en", s)
    text = pdf_text(path)
    ind = recompute(conn, "KHB-01", AUG)
    assert ind["unreliable_readings"] == "30" and ind["valid_readings"] == "713"
    # key figures, as printed (label then value on one line)
    for label, value in [("Completeness", ind["completeness_pct"] + "%"),
                         ("Readings received", f"{ind['readings']} / {ind['expected_readings']}"),
                         ("Unreliable readings", ind["unreliable_readings"]),
                         ("Valid readings", ind["valid_readings"]),
                         ("Avg temperature", ind["temp_avg"] + " °C"),
                         ("Min temperature", ind["temp_min"] + " °C"),
                         ("Max temperature", ind["temp_max"] + " °C"),
                         ("Avg humidity", ind["hum_avg"] + " %"),
                         ("Out of range", ind["oor_hours"] + " h"),
                         ("Stuck-sensor periods", "1")]:
        assert re.search(rf"^{re.escape(label)} {re.escape(value)}$", text, re.M), (label, value)
    assert "2026-08-05 02:00 2026-08-06 07:00 30 23.4 °C / 41.0 %" in text
    assert "SYNTHETIC DATA" in text and "Synthetic demo data" in text


def test_jed_ac_failure_event_in_the_pdf(loaded):
    conn, s = loaded
    text = pdf_text(build_site_month(conn, "JED-01", AUG, "en", s))
    ind = recompute(conn, "JED-01", AUG)
    assert ind["oor_events"] == "1" and ind["longest_event_hours"] == "56.0"
    assert re.search(r"^Longest event 56\.0 h$", text, re.M)
    assert "2026-08-12 12:00 2026-08-14 19:00 56.0 temperature + humidity" in text
    assert re.search(r"^Status: Action", text, re.M)


def test_summary_pdf_matches_database(loaded):
    conn, s = loaded
    import json

    path = build_summary_month(conn, AUG, "en", s)
    facts = json.loads(path.with_suffix(".facts.json").read_text())
    text = pdf_text(path)
    # every site row: code, city, completeness, unreliable, avg, min - max, avg humidity, hours, events
    for site in facts["sites"]:
        ind = recompute(conn, site["code"], AUG)
        for k in ("completeness_pct", "unreliable_readings", "temp_avg", "temp_min", "temp_max", "hum_avg",
                  "oor_hours", "oor_events"):
            assert site[k] == ind[k], (site["code"], k)
        row = (f"{site['code']} {site['city']} {ind['completeness_pct']}% {ind['unreliable_readings']} "
               f"{ind['temp_avg']} {ind['temp_min']} – {ind['temp_max']} {ind['hum_avg']} {ind['oor_hours']} "
               f"{ind['oor_events']}")
        assert row in text, row
    # totals: all 13 sites
    total = conn.execute("SELECT count(*) AS n FROM readings").fetchone()["n"]
    assert facts["kpis"]["readings"] == fmt.number(total, 0)
    assert facts["kpis"]["unreliable_readings"] == "30"
    assert facts["status_counts"] == {"green": 9, "amber": 2, "red": 2}
    assert re.search(rf"^Readings received {re.escape(fmt.number(total, 0))} / 9,672$", text, re.M)
    assert re.search(r"^Unreliable readings 30$", text, re.M)


def test_report_freshness_and_threshold_staleness(loaded):
    conn, s = loaded
    p1 = ensure_report(conn, s, AUG, "en", "RUH-01")
    mtime = p1.stat().st_mtime_ns
    assert ensure_report(conn, s, AUG, "en", "RUH-01").stat().st_mtime_ns == mtime  # fresh: reused
    site_id = conn.execute("SELECT site_id FROM sites WHERE code='RUH-01'").fetchone()["site_id"]
    update_thresholds(conn, site_id, {"temp_min": 15, "temp_max": 29, "hum_min": 20, "hum_max": 65,
                                      "stuck_min_hours": 6}, None)
    idx = {r["code"]: r for r in report_index(conn, AUG)}
    assert idx["RUH-01"]["langs"]["en"]["state"] == "stale"
    p2 = ensure_report(conn, s, AUG, "en", "RUH-01")
    assert p2.stat().st_mtime_ns != mtime
    import json

    facts = json.loads(p2.with_suffix(".facts.json").read_text())
    assert facts["thresholds"]["temp_max"] == "29.0"
    assert facts["kpis"] == recompute(conn, "RUH-01", AUG)
    assert "Temperature 15.0 – 29.0 °C" in pdf_text(p2)


def flat(text: str) -> str:
    """Arabic text from pypdf comes out in presentation forms with spacing quirks: fold the forms (NFKC) and drop
    whitespace so the comparison is about characters, not layout."""
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", text))


@pytest.mark.skipif("ar" not in LANGS, reason="ar.json not written yet")
def test_arabic_pdfs_print_the_same_numbers(loaded):
    conn, s = loaded
    import json

    for code in ("KHB-01", "JED-01"):
        path = build_site_month(conn, code, AUG, "ar", s)
        facts = json.loads(path.with_suffix(".facts.json").read_text())
        assert facts["kpis"] == recompute(conn, code, AUG)
        text = flat(pdf_text(path))
        k = facts["kpis"]
        # every number sits in its own LTR run, so it is extracted intact, in order (743/744, never 744/743)
        for v in (k["completeness_pct"] + "%", f"{k['readings']}/{k['expected_readings']}", k["valid_readings"],
                  k["unreliable_readings"], k["temp_avg"] + "°C", k["temp_min"] + "°C", k["temp_max"] + "°C",
                  k["hum_avg"] + "%", k["oor_hours"]):
            assert v in text, (code, v)
        assert f"{k['expected_readings']}/{k['readings']}" not in text
        assert flat("اكتمال البيانات") in text and flat("القراءات غير الموثوقة") in text
        assert flat(facts["site"]["name_ar"]) in text           # Arabic site name, not the English one
        assert flat("بيانات تجريبية") in text                    # synthetic label
    jed = flat(pdf_text(build_site_month(conn, "JED-01", AUG, "ar", s)))
    assert "2026-08-1212:00" in jed and "2026-08-1419:00" in jed and "56.0" in jed

    summary = build_summary_month(conn, AUG, "ar", s)
    facts = json.loads(summary.with_suffix(".facts.json").read_text())
    text = flat(pdf_text(summary))
    for site in facts["sites"]:
        for v in (site["completeness_pct"] + "%", site["temp_avg"], f"{site['temp_min']}–{site['temp_max']}",
                  site["oor_hours"]):
            assert v in text, (site["code"], v)
        assert flat(site["city_ar"]) in text, site["code"]
    assert f"{facts['kpis']['readings']}/9,672" in text


def test_report_index_lists_every_site_and_summary(loaded):
    conn, _ = loaded
    idx = report_index(conn, AUG)
    assert len(idx) == 14 and idx[0]["code"] is None
    assert set(idx[1]["langs"]) == set(LANGS)


def test_daily_series_has_every_day(loaded):
    conn, _ = loaded
    from p7.reports.facts import site_month

    f = site_month(conn, "KHB-01", AUG)
    assert len(f["daily"]) == 31
    days = defaultdict(int)
    for d in f["daily"]:
        days[d["day"][:7]] += 1
    assert days == {"2026-08": 31}
