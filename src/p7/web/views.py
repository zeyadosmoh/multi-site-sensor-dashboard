"""Pages every signed-in user can see (admin and management): overview, site, compare, reports, exports."""

import csv
import io
import time
from datetime import date
from urllib.parse import urlsplit

import psycopg
from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request
from fastapi.responses import FileResponse, RedirectResponse, Response

from p7 import fmt, metrics
from p7.auth import authenticate
from p7.i18n import LANGS, Translator
from p7.sites import all_sites, site_by_code
from p7.web import charts
from p7.web.deps import check_csrf, current_user, db, lang_of, page, session_version

router = APIRouter()

# naive in-memory login throttle: 5 failures per username+IP in 5 minutes -> wait
_FAILS: dict[tuple[str, str], list[float]] = {}
MAX_FAILS, WINDOW = 5, 300


def safe_next(target: str | None) -> str:
    """Only local paths (no scheme, no host, no //) are allowed as redirect targets."""
    if not target:
        return "/"
    parts = urlsplit(target)
    if parts.scheme or parts.netloc or not target.startswith("/") or target.startswith("//") or "\\" in target:
        return "/"
    return target


def parse_month(value: str | None, available: list[date]) -> date | None:
    """'all' -> None (whole period); 'YYYY-MM' -> that month if reported; default -> latest reported month."""
    if value == "all":
        return None
    if value:
        try:
            y, m = map(int, value.split("-"))
            d = date(y, m, 1)
        except ValueError as e:
            raise HTTPException(400, "month must be YYYY-MM or all") from e
        if d in available:
            return d
        raise HTTPException(404, "no data for that month")
    return available[-1] if available else None


def parse_sites(values: list[str], known: list[str]) -> list[str] | None:
    chosen = [v for v in values if v in known]
    return chosen if chosen and len(chosen) < len(known) else None


def filters(conn, month: str | None, site: list[str]) -> dict:
    months = metrics.months(conn)
    codes = [s["code"] for s in all_sites(conn)]
    m = parse_month(month, months)
    return {"months": months, "month": m, "month_param": "all" if m is None else f"{m:%Y-%m}",
            "codes": codes, "sites": parse_sites(site, codes)}


@router.get("/healthz")
def healthz(conn: psycopg.Connection = Depends(db)):
    conn.execute("SELECT 1")
    return {"ok": True}


@router.get("/login")
def login_form(request: Request, next: str | None = None):
    return page(request, "login.html", {"next": safe_next(next), "error": None})


@router.post("/login", dependencies=[Depends(check_csrf)])
def login(request: Request, username: str = Form(""), password: str = Form(""), next: str = Form("/"),
          conn: psycopg.Connection = Depends(db)):
    key = (username.strip().lower(), request.client.host if request.client else "-")
    now = time.monotonic()
    recent = [x for x in _FAILS.get(key, []) if now - x < WINDOW]
    t = Translator(lang_of(request))
    if len(recent) >= MAX_FAILS:
        return page(request, "login.html", {"next": safe_next(next), "error": t("login_throttled")},
                    status_code=429)
    user = authenticate(conn, username, password) if username and password else None
    if not user:
        _FAILS[key] = recent + [now]
        return page(request, "login.html", {"next": safe_next(next), "error": t("login_failed")}, status_code=401)
    _FAILS.pop(key, None)
    lang = request.session.get("lang")
    request.session.clear()  # new session on login (no fixation)
    request.session.update({"uid": user["user_id"], "pwv": session_version(user)})
    if lang:
        request.session["lang"] = lang
    return RedirectResponse(safe_next(next), status_code=303)


@router.post("/logout", dependencies=[Depends(check_csrf)])
def logout(request: Request):
    lang = request.session.get("lang")
    request.session.clear()
    if lang:
        request.session["lang"] = lang
    return RedirectResponse("/login", status_code=303)


@router.get("/lang/{code}")
def set_lang(code: str, request: Request, next: str | None = None):
    if code not in LANGS:
        raise HTTPException(404, "unknown language")
    request.session["lang"] = code
    return RedirectResponse(safe_next(next), status_code=303)


@router.get("/")
def overview(request: Request, month: str | None = None, site: list[str] = Query(default=[]),
             user: dict = Depends(current_user), conn: psycopg.Connection = Depends(db)):
    f = filters(conn, month, site)
    t = Translator(lang_of(request))
    table = metrics.site_table(conn, f["month"], f["sites"])
    k = metrics.kpis(conn, f["month"], f["sites"])
    markers = [{"code": r["code"], "name": r["name"], "city": r["city"], "lat": float(r["lat"]),
                "lon": float(r["lon"]), "status": r["status"], "status_label": t.plain(f"status_{r['status']}"),
                "temp": fmt.number(r["temp_avg"]), "comp": fmt.number(r["completeness_pct"]),
                "url": f"/sites/{r['code']}?month={f['month_param']}"} for r in table]
    ctx = {**f, "table": table, "k": k, "events": metrics.events(conn, f["month"], f["sites"], limit=5),
           "charts": charts.overview(conn, t, f["month"], f["sites"], table), "markers": markers,
           "map_labels": {"temp": t.plain("avg_temp"), "comp": t.plain("completeness"),
                          "open": t.plain("open_site")}}
    return page(request, "overview.html", ctx, user=user)


@router.get("/sites/{code}")
def site_page(code: str, request: Request, month: str | None = None, user: dict = Depends(current_user),
              conn: psycopg.Connection = Depends(db)):
    s = site_by_code(conn, code)
    if not s:
        raise HTTPException(404, "unknown site")
    f = filters(conn, month, [])
    t = Translator(lang_of(request))
    rows = metrics.site_table(conn, f["month"], [code])
    if not rows:
        raise HTTPException(404, "no data")
    row = rows[0]
    ctx = {**f, "s": s, "row": row, "events": metrics.events(conn, f["month"], [code]),
           "stuck": metrics.stuck_runs(conn, f["month"], [code]),
           "charts": charts.site(conn, t, code, f["month"], row)}
    return page(request, "site.html", ctx, user=user)


@router.get("/compare")
def compare(request: Request, month: str | None = None, site: list[str] = Query(default=[]),
            user: dict = Depends(current_user), conn: psycopg.Connection = Depends(db)):
    f = filters(conn, month, site)
    t = Translator(lang_of(request))
    table = metrics.site_table(conn, f["month"], f["sites"])
    ctx = {**f, "table": table, "charts": charts.compare(conn, t, f["month"], f["sites"], table)}
    return page(request, "compare.html", ctx, user=user)


@router.get("/export/sites.csv")
def export_sites(request: Request, month: str | None = None, site: list[str] = Query(default=[]),
                 user: dict = Depends(current_user), conn: psycopg.Connection = Depends(db)):
    f = filters(conn, month, site)
    t = Translator(lang_of(request))
    table = sorted(metrics.site_table(conn, f["month"], f["sites"]), key=lambda r: r["code"])
    buf = io.StringIO()
    buf.write("﻿")  # BOM so Excel opens UTF-8 (Arabic headers) correctly
    w = csv.writer(buf)
    w.writerow([t.plain("synthetic_csv_note")])
    cols = [("code", "site_code", None), ("name", "site", None), ("status", "status", None),
            ("readings", "readings", 0), ("expected_readings", "expected_readings", 0),
            ("completeness_pct", "completeness_pct_col", 1), ("unreliable_readings", "unreliable_readings", 0),
            ("temp_avg", "avg_temp_c", 1), ("temp_min", "min_temp_c", 1), ("temp_max", "max_temp_c", 1),
            ("hum_avg", "avg_hum_pct", 1), ("hum_min", "min_hum_pct", 1), ("hum_max", "max_hum_pct", 1),
            ("oor_readings", "oor_readings", 0), ("oor_minutes", "oor_minutes", 0), ("oor_events", "oor_events", 0),
            ("stuck_runs", "stuck_runs", 0)]
    w.writerow([t.plain(label) for _, label, _ in cols])
    for r in table:
        w.writerow([fmt.number(r[k], dp).replace(",", "") if dp is not None else
                    (t.plain(f"status_{r[k]}") if k == "status" else r[k]) for k, _, dp in cols])
    name = f"sites_{f['month_param']}.csv"
    return Response(buf.getvalue(), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{name}"'})


@router.get("/reports")
def reports_page(request: Request, month: str | None = None, user: dict = Depends(current_user),
                 conn: psycopg.Connection = Depends(db)):
    from p7.reports.build import report_index

    f = filters(conn, month, [])
    if f["month"] is None:
        f["month"] = f["months"][-1] if f["months"] else None
        f["month_param"] = f"{f['month']:%Y-%m}" if f["month"] else "all"
    idx = report_index(conn, f["month"]) if f["month"] else []
    return page(request, "reports.html", {**f, "reports": idx}, user=user)


@router.get("/reports/download")
def report_download(request: Request, month: str, lang: str = "en", site: str | None = None,
                    user: dict = Depends(current_user), conn: psycopg.Connection = Depends(db)):
    """Serve the PDF; build it first if it is missing or stale (so on-demand works for every role)."""
    from p7.reports.build import ensure_report

    if lang not in LANGS:
        raise HTTPException(404, "unknown language")
    m = parse_month(month, metrics.months(conn))
    if m is None:
        raise HTTPException(400, "pick a month")
    if site and not site_by_code(conn, site):
        raise HTTPException(404, "unknown site")
    path = ensure_report(conn, request.app.state.settings, m, lang, site)
    name = f"{site or 'all-sites'}_{m:%Y-%m}_{lang}.pdf"
    return FileResponse(path, media_type="application/pdf", filename=name)
