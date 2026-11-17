"""Ingestion against the database: idempotency, corrections, one transaction per file, upload log, zips."""

import io
import zipfile
from datetime import date, datetime

import pytest

from p7.ingest import pipeline
from p7.ingest.pipeline import ingest_uploads
from tests.helpers import hour_rows, make_xlsx

MAY1 = datetime(2026, 5, 1)


def counts(conn):
    return conn.execute("SELECT count(*) AS n, max(updated_at) AS last FROM readings").fetchone()


def ingest(conn, *files):
    return ingest_uploads(conn, list(files), source="cli", user_id=None)


def test_load_then_reupload_same_file_changes_nothing(conn):
    data = make_xlsx(hour_rows(MAY1, 48))
    [r1] = ingest(conn, ("RUH-01_2026-05.xlsx", data))
    assert (r1.status, r1.inserted, r1.updated, r1.unchanged, r1.rejected) == ("loaded", 48, 0, 0, 0)
    before = counts(conn)
    snapshot = conn.execute("SELECT * FROM readings ORDER BY reading_ts").fetchall()

    [r2] = ingest(conn, ("RUH-01_2026-05.xlsx", data))
    [r3] = ingest(conn, ("renamed copy.xlsx", data))  # same bytes under another name: still the same file
    assert r2.status == r3.status == "duplicate_file"
    assert (r2.inserted, r2.updated) == (0, 0)
    assert counts(conn) == before
    assert conn.execute("SELECT * FROM readings ORDER BY reading_ts").fetchall() == snapshot
    log = conn.execute("SELECT status, count(*) AS n FROM uploads GROUP BY 1 ORDER BY 1").fetchall()
    assert [(x["status"], x["n"]) for x in log] == [("duplicate_file", 2), ("loaded", 1)]


def test_corrected_file_upserts_rows(conn):
    rows = hour_rows(MAY1, 10)
    ingest(conn, ("RUH-01_2026-05.xlsx", make_xlsx(rows)))
    fixed = [list(r) for r in rows]
    fixed[3][2] = 30.5          # value corrected
    fixed.append([date(2026, 5, 1), "10:00", 22.0, 45.0])  # a missing hour supplied
    [r] = ingest(conn, ("RUH-01_2026-05.xlsx", make_xlsx(fixed)))
    assert (r.status, r.inserted, r.updated, r.unchanged) == ("loaded", 1, 1, 9)
    row = conn.execute("SELECT temperature, upload_id FROM readings WHERE reading_ts = %s",
                       (datetime(2026, 5, 1, 3),)).fetchone()
    assert float(row["temperature"]) == 30.5 and row["upload_id"] == r.upload_id
    # a correction file that equals what's stored (different bytes): loaded, but nothing changes
    [r2] = ingest(conn, ("RUH-01_2026-05.xlsx", make_xlsx(fixed, sheet="Sheet1")))
    assert (r2.status, r2.inserted, r2.updated, r2.unchanged) == ("loaded", 0, 0, 11)


def test_rejects_stored_with_row_numbers_and_reasons(conn):
    rows = hour_rows(MAY1, 3) + [[date(2026, 5, 1), "05:00", 85, 40], [date(2026, 5, 1), "06:00", "ERR", 40]]
    [r] = ingest(conn, ("RUH-01_2026-05.xlsx", make_xlsx(rows)))
    assert (r.inserted, r.rejected, r.reject_reasons) == (3, 2, {"non_numeric": 1, "temp_impossible": 1})
    rej = conn.execute("SELECT sheet, row_number, raw_temperature, reason FROM row_rejects ORDER BY row_number")
    assert [tuple(x.values()) for x in rej.fetchall()] == [("Readings", 5, "85", "temp_impossible"),
                                                           ("Readings", 6, "ERR", "non_numeric")]


def test_failure_mid_file_rolls_back_everything_for_that_file_only(conn, monkeypatch):
    good = make_xlsx(hour_rows(MAY1, 24))
    bad = make_xlsx(hour_rows(MAY1, 24) + [[date(2026, 5, 2), "00:00", 85, 40]])
    real_upsert = pipeline._upsert

    def exploding_upsert(c, res, readings):
        real_upsert(c, res, readings)  # rows are written...
        if res.site_code == "RUH-02":
            raise RuntimeError("disk full (simulated)")  # ...then the transaction fails

    monkeypatch.setattr(pipeline, "_upsert", exploding_upsert)
    results = ingest(conn, ("RUH-02_2026-05.xlsx", bad), ("RUH-01_2026-05.xlsx", good))
    assert [r.status for r in results] == ["failed", "loaded"]
    assert "disk full" in results[0].error
    rows = conn.execute("""SELECT s.code, count(r.reading_ts) AS n FROM sites s LEFT JOIN readings r USING (site_id)
                           WHERE s.code IN ('RUH-01', 'RUH-02') GROUP BY 1""").fetchall()
    assert {x["code"]: x["n"] for x in rows} == {"RUH-01": 24, "RUH-02": 0}
    # no partial rejects either, and the failure is in the log
    assert conn.execute("SELECT count(*) AS n FROM row_rejects").fetchone()["n"] == 0
    log = conn.execute("SELECT status, error, finished_at FROM uploads WHERE upload_id = %s",
                       (results[0].upload_id,)).fetchone()
    assert log["status"] == "failed" and "disk full" in log["error"] and log["finished_at"]
    # after the failure the same file can be loaded (its hash was never marked loaded)
    monkeypatch.setattr(pipeline, "_upsert", real_upsert)
    [again] = ingest(conn, ("RUH-02_2026-05.xlsx", bad))
    assert (again.status, again.inserted, again.rejected) == ("loaded", 24, 1)


@pytest.mark.parametrize("name, data, error", [
    ("RUH-01_2026-05.csv", b"Date,Time\n", "only .xlsx"),
    ("RUH-01_2026-05.xlsx", b"garbage", "not a valid .xlsx"),
    ("ZZZ-01_2026-05.xlsx", make_xlsx(hour_rows(MAY1, 2)), "unknown site code ZZZ-01"),
    ("export.xlsx", make_xlsx(hour_rows(MAY1, 2)), "cannot tell the site and month"),
    ("RUH-01_2026-05.xlsx", make_xlsx(hour_rows(MAY1, 2), cover={"Site code": "JED-01", "Month": "2026-05"}),
     "filename says"),
    ("RUH-01_2026-06.xlsx", make_xlsx(hour_rows(MAY1, 10)), "most rows are not in 2026-06"),
])
def test_file_level_rejections_are_logged_with_reason(conn, name, data, error):
    [r] = ingest(conn, (name, data))
    assert r.status == "rejected_file" and error in r.error
    row = conn.execute("SELECT status, error FROM uploads").fetchone()
    assert row["status"] == "rejected_file" and error in row["error"]
    assert conn.execute("SELECT count(*) AS n FROM readings").fetchone()["n"] == 0


def test_cover_sheet_only_file_loads(conn):
    [r] = ingest(conn, ("sensor_export_final (2).xlsx",
                        make_xlsx(hour_rows(MAY1, 5), cover={"Site code": "ABH-01", "Month": "2026-05"})))
    assert (r.status, r.site_code, r.detected_by, r.inserted) == ("loaded", "ABH-01", "cover_sheet", 5)


def _zip(members: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for n, d in members.items():
            z.writestr(n, d)
    return buf.getvalue()


def test_zip_upload_expands_and_reports_each_member(conn):
    inner = _zip({"x.xlsx": b""})
    data = _zip({
        "batch/RUH-01_2026-05.xlsx": make_xlsx(hour_rows(MAY1, 5)),
        "batch/RUH-02_2026-05.xlsx": make_xlsx(hour_rows(MAY1, 6)),
        "batch/notes.txt": b"hello",
        "batch/nested.zip": inner,
        "__MACOSX/batch/._RUH-01_2026-05.xlsx": b"junk",
    })
    results = ingest(conn, ("may.zip", data))
    got = sorted((r.filename, r.status, r.inserted) for r in results)
    assert got == [("RUH-01_2026-05.xlsx", "loaded", 5), ("RUH-02_2026-05.xlsx", "loaded", 6),
                   ("nested.zip", "rejected_file", 0), ("notes.txt", "rejected_file", 0)]
    assert all(r.archive_name == "may.zip" for r in results)
    # the same zip again: both workbooks are duplicates, nothing changes
    again = ingest(conn, ("may.zip", data))
    assert sorted(r.status for r in again) == ["duplicate_file", "duplicate_file", "rejected_file", "rejected_file"]
    assert conn.execute("SELECT count(*) AS n FROM readings").fetchone()["n"] == 11


def test_zip_bomb_guard(conn, monkeypatch):
    monkeypatch.setattr(pipeline, "MAX_RATIO", 5)
    data = _zip({"RUH-01_2026-05.xlsx": b"\0" * 1_000_000})
    [r] = ingest(conn, ("bomb.zip", data))
    assert r.status == "rejected_file" and "suspiciously compressed" in r.error


def test_not_a_zip(conn):
    [r] = ingest(conn, ("broken.zip", b"PK\x03\x04garbage"))
    assert r.status == "rejected_file" and "not a valid .zip" in r.error
