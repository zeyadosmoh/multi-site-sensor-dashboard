"""Build small workbooks and readings for tests."""

import io
from datetime import datetime

from openpyxl import Workbook

HEADER = ["Date", "Time", "Temperature", "Humidity"]


def make_xlsx(rows: list[list], header: list | None = HEADER, cover: dict | None = None, sheet: str = "Readings",
              pre_rows: list[list] | None = None) -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.title = sheet
    if cover is not None:
        info = wb.create_sheet("Info", 0)
        for k, v in cover.items():
            info.append([k, v])
    for r in pre_rows or []:
        ws.append(r)
    if header:
        ws.append(header)
    for r in rows:
        ws.append(r)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def hour_rows(start: datetime, n: int, t: float = 22.0, h: float = 45.0, step_t: float = 0.1) -> list[list]:
    """n hourly rows with slowly changing temperature (never stuck)."""
    from datetime import timedelta

    out = []
    for i in range(n):
        ts = start + timedelta(hours=i)
        out.append([ts.date(), ts.strftime("%H:%M"), round(t + step_t * (i % 7), 1), round(h + 0.3 * (i % 5), 1)])
    return out


def load_readings(conn, site_code: str, readings: list[tuple], month: str = "2026-05") -> None:
    """Insert readings directly (bypassing Excel) plus a 'loaded' upload so the month counts as reported.
    readings: (datetime, temperature, humidity)."""
    with conn.transaction():
        site_id = conn.execute("SELECT site_id FROM sites WHERE code = %s", (site_code,)).fetchone()["site_id"]
        up = conn.execute(
            """INSERT INTO uploads (batch_id, source, filename, sha256, size_bytes, site_id, month, status)
               VALUES (gen_random_uuid(), 'cli', %s, md5(random()::text) || md5(random()::text), 0, %s, %s, 'loaded')
               RETURNING upload_id""", (f"{site_code}_{month}.xlsx", site_id, f"{month}-01")).fetchone()["upload_id"]
        for ts, t, h in readings:
            conn.execute("INSERT INTO readings (site_id, reading_ts, temperature, humidity, upload_id) "
                         "VALUES (%s, %s, %s, %s, %s)", (site_id, ts, t, h, up))
