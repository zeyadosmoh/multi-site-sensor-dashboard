"""Admin-only pages: uploads, thresholds, users, report rebuilds. Every route depends on admin_user."""

import csv
import io

import psycopg
from fastapi import APIRouter, BackgroundTasks, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse, Response
from starlette.datastructures import UploadFile

from p7 import auth, metrics
from p7.config import Settings
from p7.db import connect
from p7.i18n import Translator
from p7.ingest.pipeline import FileResult, ingest_uploads
from p7.sites import ThresholdError, all_sites, update_thresholds
from p7.web.deps import admin_user, check_csrf, db, lang_of, page

router = APIRouter(dependencies=[Depends(admin_user)])


def _rebuild_reports(settings: Settings, dbname: str | None, results: list[FileResult]) -> None:
    from p7.reports.build import build_after_upload

    with connect(settings, dbname) as conn:
        build_after_upload(conn, results, settings)


@router.get("/uploads")
def uploads_page(request: Request, user: dict = Depends(admin_user), conn: psycopg.Connection = Depends(db)):
    log = conn.execute("""SELECT u.*, s.code AS site_code, us.username FROM uploads u
                          LEFT JOIN sites s USING (site_id) LEFT JOIN users us USING (user_id)
                          ORDER BY u.upload_id DESC LIMIT 200""").fetchall()
    return page(request, "uploads.html", {"log": log, "results": None,
                                          "max_mb": request.app.state.settings.max_upload_mb}, user=user)


@router.post("/uploads", dependencies=[Depends(check_csrf)])
async def upload(request: Request, background: BackgroundTasks, user: dict = Depends(admin_user)):
    s: Settings = request.app.state.settings
    form = await request.form(max_files=500, max_fields=50)
    files = [f for f in form.getlist("files") if isinstance(f, UploadFile) and f.filename]
    t = Translator(lang_of(request))
    if not files:
        return page(request, "_upload_results.html", {"results": [], "error": t("upload_none")}, user=user)
    limit = s.max_upload_mb * 1024 * 1024
    items, total = [], 0
    for f in files:
        data = await f.read(limit + 1)
        total += len(data)
        if len(data) > limit or total > 4 * limit:
            return page(request, "_upload_results.html",
                        {"results": [], "error": t("upload_too_large", mb=s.max_upload_mb)}, user=user,
                        status_code=413)
        items.append((f.filename, data))

    def run() -> list[FileResult]:
        with connect(s, request.app.state.dbname) as conn:
            return ingest_uploads(conn, items, source="web", user_id=user["user_id"])

    from starlette.concurrency import run_in_threadpool

    results = await run_in_threadpool(run)
    if s.reports_after_upload and any(r.changed for r in results):
        background.add_task(_rebuild_reports, s, request.app.state.dbname, results)
    return page(request, "_upload_results.html", {"results": results, "error": None,
                                                  "reports_queued": s.reports_after_upload and
                                                  any(r.changed for r in results)}, user=user)


@router.get("/uploads/{upload_id}/rejects.csv")
def rejects_csv(upload_id: int, conn: psycopg.Connection = Depends(db)):
    up = conn.execute("SELECT filename FROM uploads WHERE upload_id = %s", (upload_id,)).fetchone()
    if not up:
        raise HTTPException(404, "unknown upload")
    rows = conn.execute("""SELECT sheet, row_number, raw_date, raw_time, raw_temperature, raw_humidity, reason, message
                           FROM row_rejects WHERE upload_id = %s ORDER BY row_number""", (upload_id,)).fetchall()
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["sheet", "row", "date", "time", "temperature", "humidity", "reason", "message"])
    for r in rows:
        # neutralise spreadsheet formulas in echoed cell text (CSV injection)
        w.writerow([("'" + v) if isinstance(v, str) and v[:1] in "=+-@\t\r" else v for v in r.values()])
    return Response(buf.getvalue(), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="rejects_upload_{upload_id}.csv"'})


@router.get("/thresholds")
def thresholds_page(request: Request, user: dict = Depends(admin_user), conn: psycopg.Connection = Depends(db)):
    audit = conn.execute("""SELECT a.*, s.code, u.username FROM threshold_audit a JOIN sites s USING (site_id)
                            LEFT JOIN users u USING (user_id) ORDER BY a.audit_id DESC LIMIT 30""").fetchall()
    return page(request, "thresholds.html", {"sites": all_sites(conn), "audit": audit}, user=user)


@router.post("/thresholds/{code}", dependencies=[Depends(check_csrf)])
def save_thresholds(code: str, request: Request, temp_min: str = Form(...), temp_max: str = Form(...),
                    hum_min: str = Form(...), hum_max: str = Form(...), stuck_min_hours: str = Form(...),
                    user: dict = Depends(admin_user), conn: psycopg.Connection = Depends(db)):
    site = conn.execute("SELECT * FROM sites WHERE code = %s", (code,)).fetchone()
    if not site:
        raise HTTPException(404, "unknown site")
    t = Translator(lang_of(request))
    values = {"temp_min": temp_min, "temp_max": temp_max, "hum_min": hum_min, "hum_max": hum_max,
              "stuck_min_hours": stuck_min_hours}
    try:
        changed = update_thresholds(conn, site["site_id"], values, user["user_id"])
        msg, ok = (t("thresholds_saved") if changed else t("thresholds_unchanged")), True
    except ThresholdError as e:
        msg, ok = str(e), False
    site = conn.execute("SELECT * FROM sites WHERE code = %s", (code,)).fetchone()
    return page(request, "_threshold_row.html", {"s": site, "msg": msg, "ok": ok}, user=user,
                status_code=200 if ok else 422)


@router.get("/users")
def users_page(request: Request, user: dict = Depends(admin_user), conn: psycopg.Connection = Depends(db)):
    return page(request, "users.html", {"users": auth.list_users(conn), "msg": request.query_params.get("msg"),
                                        "err": request.query_params.get("err")}, user=user)


def _back(msg: str | None = None, err: str | None = None) -> RedirectResponse:
    from urllib.parse import urlencode

    q = urlencode({k: v for k, v in (("msg", msg), ("err", err)) if v})
    return RedirectResponse("/users" + (f"?{q}" if q else ""), status_code=303)


@router.post("/users", dependencies=[Depends(check_csrf)])
def create_user(username: str = Form(...), password: str = Form(...), role: str = Form(...),
                conn: psycopg.Connection = Depends(db)):
    try:
        auth.create_user(conn, username, password, role)
    except auth.UserError as e:
        return _back(err=str(e))
    return _back(msg=f"created {username.strip().lower()}")


@router.post("/users/{user_id}/active", dependencies=[Depends(check_csrf)])
def toggle_active(user_id: int, active: str = Form(...), user: dict = Depends(admin_user),
                  conn: psycopg.Connection = Depends(db)):
    try:
        auth.set_active(conn, user_id, active == "true", user["user_id"])
    except auth.UserError as e:
        return _back(err=str(e))
    return _back(msg="saved")


@router.post("/users/{user_id}/password", dependencies=[Depends(check_csrf)])
def reset_password(user_id: int, password: str = Form(...), conn: psycopg.Connection = Depends(db)):
    if not auth.get_user(conn, user_id):
        raise HTTPException(404, "unknown user")
    try:
        auth.set_password(conn, user_id, password)
    except auth.UserError as e:
        return _back(err=str(e))
    return _back(msg="password changed; that user's sessions are signed out")


@router.post("/reports/rebuild", dependencies=[Depends(check_csrf)])
def rebuild_reports(request: Request, background: BackgroundTasks, month: str = Form(...),
                    conn: psycopg.Connection = Depends(db)):
    from p7.web.views import parse_month

    m = parse_month(month, metrics.months(conn))
    if m is None:
        raise HTTPException(400, "pick a month")
    s = request.app.state.settings

    def run():
        from p7.reports.build import build_month

        with connect(s, request.app.state.dbname) as c:
            build_month(c, m, s)

    background.add_task(run)
    return RedirectResponse(f"/reports?month={m:%Y-%m}&queued=1", status_code=303)
