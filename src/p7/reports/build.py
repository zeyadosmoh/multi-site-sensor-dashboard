"""Build PDF reports (HTML -> Playwright Chromium), store them under REPORTS_DIR and index them in `reports`.

A stored report is reused while it is fresh. It is stale when thresholds changed since (flag set by
p7.sites.update_thresholds) or when a file for its month (and site) was loaded after it was generated."""

import base64
import json
import logging
from datetime import date
from html import escape as html_escape
from importlib.resources import files
from pathlib import Path

import psycopg
from jinja2 import Environment, FileSystemLoader, StrictUndefined, select_autoescape

from p7 import fmt
from p7.config import Settings, get_settings
from p7.i18n import LANGS, Translator, direction, table
from p7.reports import charts
from p7.reports import facts as factsmod
from p7.sites import site_by_code

log = logging.getLogger(__name__)
PKG = files("p7.reports")
WEB_FONTS = files("p7.web").joinpath("static/vendor/fonts")


def report_langs() -> list[str]:
    return [lang for lang in LANGS if table(lang)]


def _env() -> Environment:
    env = Environment(loader=FileSystemLoader(str(PKG.joinpath("templates"))),
                      autoescape=select_autoescape(["html", "j2"]), undefined=StrictUndefined)
    env.globals.update(n=fmt.n, nr=fmt.nr)
    return env


def font_css(lang: str) -> str:
    if lang != "ar":
        return ""
    css = []
    for weight in (400, 700):
        data = base64.b64encode(WEB_FONTS.joinpath(f"noto-sans-arabic-arabic-{weight}-normal.woff2").read_bytes())
        css.append(f'@font-face {{ font-family: "Noto Sans Arabic"; font-weight: {weight}; '
                   f'src: url(data:font/woff2;base64,{data.decode()}) format("woff2"); }}')
    return "\n".join(css)


def render_html(f: dict, lang: str) -> str:
    t = Translator(lang)
    ctx = {"f": f, "t": t, "lang": lang, "dir": direction(lang), "font_css": font_css(lang),
           "loc": lambda obj, field: fmt.localized(obj, field, lang),
           "month_label": t.month(date.fromisoformat(f["month"] + "-01"))}
    if f["kind"] == "site_month":
        th = {k: float(v) for k, v in f["thresholds"].items() if k != "stuck_min_hours"}
        ctx["charts"] = {"temp": charts.daily_temperature(f["daily"], th["temp_min"], th["temp_max"]),
                         "hum": charts.daily_humidity(f["daily"], th["hum_min"], th["hum_max"]),
                         "profile": charts.hour_profile(f["profile"]) if f["profile"] else ""}
        return _env().get_template("site_month.html.j2").render(**ctx)
    ctx["charts"] = {"sites": charts.sites_bars(f["sites"])}
    return _env().get_template("summary_month.html.j2").render(**ctx)


def write_pdf(html: str, pdf_path: Path, lang: str, preview_png: Path | None = None) -> None:
    from playwright.sync_api import sync_playwright

    t = Translator(lang)
    footer = (f'<div style="font-size:7px;width:100%;padding:0 12mm;color:#6b7785;display:flex;'
              f'justify-content:space-between;direction:{direction(lang)}">'
              f'<span>{html_escape(t.plain("pdf_footer_synthetic"))}</span>'
              f'<span dir="ltr"><span class="pageNumber"></span> / <span class="totalPages"></span></span></div>')
    with sync_playwright() as p:
        browser = p.chromium.launch()
        try:
            page = browser.new_page(viewport={"width": 794, "height": 1123}, device_scale_factor=1.5)
            page.set_content(html, wait_until="load")
            page.emulate_media(media="print")
            page.evaluate("document.fonts.ready")
            page.pdf(path=str(pdf_path), format="A4", print_background=True, display_header_footer=True,
                     header_template="<div></div>", footer_template=footer,
                     margin={"top": "12mm", "bottom": "16mm", "left": "12mm", "right": "12mm"})
            if preview_png is not None:
                page.screenshot(path=str(preview_png), clip={"x": 0, "y": 0, "width": 794, "height": 1123})
        finally:
            browser.close()


def _out_path(s: Settings, kind: str, month: date, lang: str, code: str | None) -> Path:
    d = Path(s.reports_dir) / f"{month:%Y-%m}"
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{code or 'all-sites'}_{month:%Y-%m}_{lang}.pdf"


def _store(conn, kind: str, site_id: int | None, month: date, lang: str, path: Path, f: dict) -> None:
    with conn.transaction():
        conn.execute(
            """INSERT INTO reports (kind, site_id, month, lang, pdf_path, facts, generated_at, stale)
               VALUES (%s, %s, %s, %s, %s, %s, now(), false)
               ON CONFLICT (kind, coalesce(site_id, 0), month, lang) DO UPDATE
                 SET pdf_path = EXCLUDED.pdf_path, facts = EXCLUDED.facts, generated_at = now(), stale = false""",
            (kind, site_id, month, lang, str(path), json.dumps(f)))


def build_site_month(conn: psycopg.Connection, code: str, month: date | str, lang: str = "en",
                     settings: Settings | None = None) -> Path:
    s = settings or get_settings()
    month = date.fromisoformat(f"{month}-01") if isinstance(month, str) else month
    site = site_by_code(conn, code)
    if site is None:
        raise ValueError(f"unknown site {code}")
    f = factsmod.site_month(conn, code, month)
    path = _out_path(s, "site_month", month, lang, code)
    write_pdf(render_html(f, lang), path, lang)
    path.with_suffix(".facts.json").write_text(json.dumps(f, indent=1, ensure_ascii=False))
    _store(conn, "site_month", site["site_id"], month, lang, path, f)
    return path


def build_summary_month(conn: psycopg.Connection, month: date | str, lang: str = "en",
                        settings: Settings | None = None) -> Path:
    s = settings or get_settings()
    month = date.fromisoformat(f"{month}-01") if isinstance(month, str) else month
    f = factsmod.summary_month(conn, month)
    path = _out_path(s, "summary_month", month, lang, None)
    write_pdf(render_html(f, lang), path, lang)
    path.with_suffix(".facts.json").write_text(json.dumps(f, indent=1, ensure_ascii=False))
    _store(conn, "summary_month", None, month, lang, path, f)
    return path


_FRESH_SQL = """
SELECT r.pdf_path,
       NOT r.stale AND r.generated_at >= coalesce((
           SELECT max(u.finished_at) FROM uploads u
           WHERE u.status = 'loaded' AND u.month = r.month AND (r.site_id IS NULL OR u.site_id = r.site_id)),
           '-infinity') AS fresh
FROM reports r
WHERE r.kind = %(kind)s AND coalesce(r.site_id, 0) = %(site_id)s AND r.month = %(month)s AND r.lang = %(lang)s"""


def ensure_report(conn: psycopg.Connection, settings: Settings, month: date, lang: str,
                  code: str | None = None) -> Path:
    site_id = site_by_code(conn, code)["site_id"] if code else 0
    row = conn.execute(_FRESH_SQL, {"kind": "site_month" if code else "summary_month", "site_id": site_id,
                                    "month": month, "lang": lang}).fetchone()
    if row and row["fresh"] and Path(row["pdf_path"]).is_file():
        return Path(row["pdf_path"])
    if code:
        return build_site_month(conn, code, month, lang, settings)
    return build_summary_month(conn, month, lang, settings)


def report_index(conn: psycopg.Connection, month: date) -> list[dict]:
    """Report status per site (+ the summary) and language for the reports page."""
    rows = conn.execute(
        """SELECT k.code, k.name, k.name_ar, l.lang, r.generated_at,
                  CASE WHEN r.report_id IS NULL THEN 'missing'
                       WHEN r.stale OR r.generated_at < coalesce((
                            SELECT max(u.finished_at) FROM uploads u WHERE u.status = 'loaded' AND u.month = %(m)s
                              AND (k.site_id IS NULL OR u.site_id = k.site_id)), '-infinity') THEN 'stale'
                       ELSE 'fresh' END AS state
           FROM (SELECT NULL::int AS site_id, NULL::text AS code, NULL::text AS name, NULL::text AS name_ar
                 UNION ALL SELECT site_id, code, name, name_ar FROM sites) k
           CROSS JOIN unnest(%(langs)s::text[]) AS l(lang)
           LEFT JOIN reports r ON r.month = %(m)s AND r.lang = l.lang
                AND coalesce(r.site_id, 0) = coalesce(k.site_id, 0)
           ORDER BY k.code NULLS FIRST, l.lang""", {"m": month, "langs": report_langs()}).fetchall()
    out: dict[str | None, dict] = {}
    for r in rows:
        item = out.setdefault(r["code"], {"code": r["code"], "name": r["name"], "name_ar": r["name_ar"], "langs": {}})
        item["langs"][r["lang"]] = {"state": r["state"], "generated_at": r["generated_at"]}
    return list(out.values())


def build_after_upload(conn: psycopg.Connection, results, settings: Settings | None = None) -> list[Path]:
    """Rebuild the site-month reports touched by an upload batch and the monthly summaries, every language.
    Errors are logged and skipped (reports can be rebuilt later; the data is already committed)."""
    touched = sorted({(r.site_code, r.month) for r in results if r.changed})
    months = sorted({m for _, m in touched})
    built: list[Path] = []
    for lang in report_langs():
        for code, m in touched:
            try:
                built.append(build_site_month(conn, code, m, lang, settings))
            except Exception:  # noqa: BLE001
                log.exception("report failed: %s %s %s", code, m, lang)
        for m in months:
            try:
                built.append(build_summary_month(conn, m, lang, settings))
            except Exception:  # noqa: BLE001
                log.exception("summary report failed: %s %s", m, lang)
    return built


def build_month(conn: psycopg.Connection, month: date, settings: Settings | None = None) -> list[Path]:
    codes = [r["code"] for r in conn.execute("SELECT code FROM sites ORDER BY code").fetchall()]
    built = []
    for lang in report_langs():
        for code in codes:
            built.append(build_site_month(conn, code, month, lang, settings))
        built.append(build_summary_month(conn, month, lang, settings))
    return built
