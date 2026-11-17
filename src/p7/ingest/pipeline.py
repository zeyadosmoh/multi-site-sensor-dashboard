"""Ingest uploaded files: one transaction per file, idempotent (file hash + row upsert), every upload logged.

Flow per file:
  1. log row (status 'processing') committed on its own, so even a crash leaves a trace
  2. reject non-.xlsx / unreadable / unidentifiable files (status 'rejected_file', reason in `error`)
  3. same SHA-256 already loaded -> status 'duplicate_file', nothing else touched
  4. one transaction: upsert readings (inserted / updated / unchanged), store row rejects, finish the log row
  5. any exception -> that transaction rolls back; the log row becomes 'failed' with the error
"""

import hashlib
import io
import json
import logging
import uuid
import zipfile
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path, PurePosixPath

import psycopg

from p7.db import refresh_metrics
from p7.ingest.detect import detect
from p7.ingest.excel import FileRejected, parse_workbook
from p7.ingest.validate import reject_text, validate_rows

log = logging.getLogger(__name__)

MAX_ZIP_MEMBERS = 500
MAX_ZIP_TOTAL = 500 * 1024 * 1024
MAX_MEMBER = 50 * 1024 * 1024
MAX_RATIO = 100


@dataclass
class FileResult:
    upload_id: int
    filename: str
    status: str
    site_code: str | None = None
    site_id: int | None = None
    month: str | None = None
    detected_by: str | None = None
    rows_total: int = 0
    inserted: int = 0
    updated: int = 0
    unchanged: int = 0
    rejected: int = 0
    reject_reasons: dict[str, int] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    error: str | None = None
    archive_name: str | None = None

    @property
    def changed(self) -> bool:
        return self.status == "loaded" and (self.inserted + self.updated) > 0


@dataclass(frozen=True)
class Incoming:
    filename: str
    data: bytes
    archive_name: str | None = None


def expand(name: str, data: bytes) -> tuple[list[Incoming], list[tuple[Incoming, str]]]:
    """Turn one uploaded file into workbook candidates. Zips are opened in memory (never extracted to disk).
    Returns (files, rejected-with-reason)."""
    if not name.lower().endswith(".zip"):
        return [Incoming(PurePosixPath(name.replace("\\", "/")).name, data)], []
    try:
        z = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        return [], [(Incoming(name, data), "not a valid .zip archive")]
    members = [i for i in z.infolist() if not i.is_dir()]
    if len(members) > MAX_ZIP_MEMBERS:
        return [], [(Incoming(name, data), f"zip has {len(members)} files; the limit is {MAX_ZIP_MEMBERS}")]
    total = sum(i.file_size for i in members)
    if total > MAX_ZIP_TOTAL:
        return [], [(Incoming(name, data), "zip is too large when uncompressed")]
    ok, bad = [], []
    for info in members:
        base = PurePosixPath(info.filename.replace("\\", "/")).name
        if info.filename.startswith("__MACOSX/") or base.startswith((".", "~$")):
            continue
        if info.file_size > MAX_MEMBER or (info.compress_size and info.file_size / info.compress_size > MAX_RATIO):
            bad.append((Incoming(base, b"", name), "file inside the zip is too large or suspiciously compressed"))
            continue
        content = z.read(info)
        if base.lower().endswith(".zip"):
            bad.append((Incoming(base, content, name), "zip inside a zip is not supported"))
        else:
            ok.append(Incoming(base, content, name))
    return ok, bad


def _start_log(conn, batch_id, inc: Incoming, source: str, user_id: int | None) -> int:
    with conn.transaction():
        return conn.execute(
            """INSERT INTO uploads (batch_id, user_id, source, filename, archive_name, sha256, size_bytes, status)
               VALUES (%s, %s, %s, %s, %s, %s, %s, 'processing') RETURNING upload_id""",
            (batch_id, user_id, source, inc.filename[:255], inc.archive_name, hashlib.sha256(inc.data).hexdigest(),
             len(inc.data))).fetchone()["upload_id"]


def _finish(conn, r: FileResult) -> None:
    conn.execute(
        """UPDATE uploads SET status=%s, site_id=%s, month=%s, detected_by=%s, rows_total=%s, inserted=%s, updated=%s,
                  unchanged=%s, rejected=%s, warnings=%s, error=%s, finished_at=now() WHERE upload_id=%s""",
        (r.status, r.site_id, r.month and date.fromisoformat(r.month + "-01"), r.detected_by, r.rows_total,
         r.inserted, r.updated, r.unchanged, r.rejected, json.dumps(r.warnings), r.error, r.upload_id))


def _interval(conn) -> int:
    return conn.execute("SELECT reading_interval_minutes FROM app_config").fetchone()["reading_interval_minutes"]


def ingest_one(conn: psycopg.Connection, inc: Incoming, batch_id: uuid.UUID, source: str,
               user_id: int | None, pre_rejected: str | None = None) -> FileResult:
    upload_id = _start_log(conn, batch_id, inc, source, user_id)
    res = FileResult(upload_id, inc.filename, "processing", archive_name=inc.archive_name)
    sha = hashlib.sha256(inc.data).hexdigest()
    try:
        if pre_rejected:
            raise FileRejected(pre_rejected)
        if not inc.filename.lower().endswith(".xlsx"):
            raise FileRejected("only .xlsx workbooks are accepted (or a .zip of them)")
        # identical bytes already loaded (under any name): nothing to do; checked again under a lock below
        if prev := _loaded_copy(conn, sha):
            return _mark_duplicate(conn, res, prev)
        parsed = parse_workbook(inc.data)
        det = detect(inc.filename, parsed.cover)
        res.site_code, res.month, res.detected_by = det.site_code, f"{det.month:%Y-%m}", det.detected_by
        site = conn.execute("SELECT site_id FROM sites WHERE code = %s", (det.site_code,)).fetchone()
        if site is None:
            raise FileRejected(f"unknown site code {det.site_code}")
        res.site_id = site["site_id"]
        res.warnings = list(parsed.warnings)
        if det.detected_by == "cover_sheet":
            res.warnings.append("site and month taken from the Info cover sheet")
        if parsed.empty_rows:
            res.warnings.append(f"{parsed.empty_rows} empty row(s) ignored")
        v = validate_rows(parsed.rows, det.month, _interval(conn))
        res.warnings += v.warnings
        res.rows_total = len(parsed.rows)
        if res.rows_total and len(v.rejects) / res.rows_total > 0.5:
            counts = _count(v.rejects)
            if counts.get("outside_file_month", 0) / res.rows_total > 0.5:
                raise FileRejected(f"most rows are not in {det.month:%Y-%m}; check the site/month in the filename")

        with conn.transaction():
            conn.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (sha,))
            if prev := _loaded_copy(conn, sha):
                return _mark_duplicate(conn, res, prev)
            _upsert(conn, res, v.readings)
            if v.rejects:
                with conn.cursor() as cur:
                    cur.executemany(
                        """INSERT INTO row_rejects (upload_id, sheet, row_number, raw_date, raw_time, raw_temperature,
                                                    raw_humidity, reason, message)
                           VALUES (%(upload_id)s, %(sheet)s, %(row_number)s, %(raw_date)s, %(raw_time)s,
                                   %(raw_temperature)s, %(raw_humidity)s, %(reason)s, %(message)s)""",
                        [{**reject_text(r), "upload_id": upload_id, "sheet": parsed.sheet} for r in v.rejects])
            res.rejected = len(v.rejects)
            res.reject_reasons = _count(v.rejects)
            res.status = "loaded"
            _finish(conn, res)
        return res
    except FileRejected as e:
        res.status, res.error = "rejected_file", str(e)
    except Exception as e:  # noqa: BLE001 - logged and recorded, the batch continues with the next file
        log.exception("ingest failed for %s", inc.filename)
        res.status, res.error = "failed", f"{type(e).__name__}: {e}"[:500]
        res.inserted = res.updated = res.unchanged = res.rejected = 0
    with conn.transaction():
        _finish(conn, res)
    return res


def _loaded_copy(conn, sha: str) -> dict | None:
    return conn.execute("""SELECT u.upload_id, u.site_id, s.code, to_char(u.month, 'YYYY-MM') AS month
                           FROM uploads u JOIN sites s USING (site_id)
                           WHERE u.sha256 = %s AND u.status = 'loaded'""", (sha,)).fetchone()


def _mark_duplicate(conn, res: FileResult, prev: dict) -> FileResult:
    res.status = "duplicate_file"
    res.site_id, res.site_code, res.month = prev["site_id"], prev["code"], prev["month"]
    res.warnings = [f"identical file already loaded (upload #{prev['upload_id']}); nothing changed"]
    res.rows_total = res.inserted = res.updated = res.unchanged = res.rejected = 0
    with conn.transaction():
        _finish(conn, res)
    return res


def _count(rejects) -> dict[str, int]:
    out: dict[str, int] = {}
    for r in rejects:
        out[r.reason] = out.get(r.reason, 0) + 1
    return dict(sorted(out.items()))


def _upsert(conn, res: FileResult, readings) -> None:
    conn.execute("""CREATE TEMP TABLE IF NOT EXISTS stage_readings
                    (reading_ts timestamp, temperature numeric(5,1), humidity numeric(5,1)) ON COMMIT DELETE ROWS""")
    conn.execute("DELETE FROM stage_readings")
    with conn.cursor() as cur, cur.copy("COPY stage_readings (reading_ts, temperature, humidity) FROM STDIN") as cp:
        for r in readings:
            cp.write_row((r.ts, r.temperature, r.humidity))
    rows = conn.execute(
        """INSERT INTO readings AS r (site_id, reading_ts, temperature, humidity, upload_id)
           SELECT %(site)s, reading_ts, temperature, humidity, %(upload)s FROM stage_readings
           ON CONFLICT (site_id, reading_ts) DO UPDATE
              SET temperature = EXCLUDED.temperature, humidity = EXCLUDED.humidity,
                  upload_id = EXCLUDED.upload_id, updated_at = now()
            WHERE (r.temperature, r.humidity) IS DISTINCT FROM (EXCLUDED.temperature, EXCLUDED.humidity)
           RETURNING (xmax = 0) AS inserted""",
        {"site": res.site_id, "upload": res.upload_id}).fetchall()
    res.inserted = sum(1 for x in rows if x["inserted"])
    res.updated = len(rows) - res.inserted
    res.unchanged = len(readings) - len(rows)


def ingest_uploads(conn: psycopg.Connection, uploads: list[tuple[str, bytes]], source: str,
                   user_id: int | None) -> list[FileResult]:
    """Entry point for the web form and the CLI: a list of (filename, bytes); .zip files are expanded."""
    # autocommit outside explicit transaction blocks, so each `conn.transaction()` below is a real, separate
    # transaction (the log row commits on its own; each file commits or rolls back on its own)
    conn.commit()
    previous, conn.autocommit = conn.autocommit, True
    batch_id = uuid.uuid4()
    results: list[FileResult] = []
    try:
        for name, data in uploads:
            ok, bad = expand(name, data)
            for inc, reason in bad:
                results.append(ingest_one(conn, inc, batch_id, source, user_id, pre_rejected=reason))
            for inc in ok:
                results.append(ingest_one(conn, inc, batch_id, source, user_id))
        if any(r.changed for r in results):
            refresh_metrics(conn)
    finally:
        conn.autocommit = previous
    return results


def ingest_paths(conn: psycopg.Connection, paths: list[Path], source: str = "cli",
                 user_id: int | None = None) -> list[FileResult]:
    items: list[tuple[str, bytes]] = []
    for p in paths:
        if p.is_dir():
            items += [(f.name, f.read_bytes()) for f in sorted(p.iterdir()) if f.suffix.lower() in (".xlsx", ".zip")]
        else:
            items.append((p.name, p.read_bytes()))
    return ingest_uploads(conn, items, source, user_id)
