"""Web app: role permissions on every route, CSRF, login/session rules, uploads and threshold edits via HTTP."""

import io
import re
import zipfile
from datetime import datetime, timedelta

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from p7.auth import create_user, set_active, set_password
from p7.i18n import table
from p7.web import admin, views
from p7.web.app import create_app
from p7.web.deps import admin_user
from tests.conftest import TEST_DB
from tests.helpers import hour_rows, load_readings, make_xlsx

ADMIN_PW = "admin-password-123"
MGMT_PW = "manager-password-123"
PUBLIC = {"/login", "/healthz", "/lang/{code}"}
SAMPLE_PATH = {"code": "KHB-01", "upload_id": "1", "user_id": "1"}


@pytest.fixture
def app(conn, settings, tmp_path):
    s = settings.model_copy(update={"session_secret": "s" * 48, "reports_dir": tmp_path / "reports",
                                    "reports_after_upload": False})
    create_user(conn, "admin", ADMIN_PW, "admin")
    create_user(conn, "boss", MGMT_PW, "management")
    d = datetime(2026, 8, 5)
    load_readings(conn, "KHB-01", [(d + timedelta(hours=h), 23.4, 41.0) for h in range(8)] +
                  [(d + timedelta(hours=h), 24.0 + h / 10, 45.0) for h in range(8, 20)], month="2026-08")
    load_readings(conn, "RUH-01", [(d + timedelta(hours=h), 25.0 + h / 10, 30.0) for h in range(24)], month="2026-08")
    views._FAILS.clear()
    return create_app(s, TEST_DB)


def client(app) -> TestClient:
    return TestClient(app, follow_redirects=False)


def csrf_of(c: TestClient) -> str:
    return re.search(r'name="csrf" value="([^"]+)"', c.get("/login").text).group(1)


def login(app, username, password) -> TestClient:
    c = client(app)
    r = c.post("/login", data={"username": username, "password": password, "csrf": csrf_of(c), "next": "/"})
    assert r.status_code == 303, r.text
    return c


def token(c: TestClient) -> str:
    """CSRF token of a signed-in session (any page carries it)."""
    return re.search(r'name="csrf" value="([^"]+)"', c.get("/reports?month=2026-08").text).group(1)


def routes(app):
    """(method, template, concrete path, is_admin) for every API route of the two routers the app mounts."""
    out = []
    for router, is_admin in ((views.router, False), (admin.router, True)):
        for r in router.routes:
            assert isinstance(r, APIRoute)
            path = r.path
            for k, v in SAMPLE_PATH.items():
                path = path.replace("{" + k + "}", v)
            for m in r.methods:
                out.append((m, r.path, path, is_admin))
    return out


def test_every_route_is_classified(app):
    """Every route is public, viewer (login) or admin; the matrix below covers all of them."""
    # the app mounts exactly these two routers (plus /static), and the admin router enforces admin_user
    mounted = [getattr(r, "original_router", None) for r in app.routes if getattr(r, "path", None) != "/static"]
    assert mounted == [views.router, admin.router]
    assert any(d.dependency is admin_user for d in admin.router.dependencies)
    rs = routes(app)
    assert len(rs) >= 20
    admin_paths = {p for _, p, _, a in rs if a}
    assert admin_paths == {"/uploads", "/uploads/{upload_id}/rejects.csv", "/thresholds", "/thresholds/{code}",
                           "/users", "/users/{user_id}/active", "/users/{user_id}/password", "/reports/rebuild"}


def test_role_matrix_get(app):
    anon, boss, admin = client(app), login(app, "boss", MGMT_PW), login(app, "admin", ADMIN_PW)
    for method, template, path, is_admin in routes(app):
        if method != "GET" or template in PUBLIC:
            continue
        if template == "/reports/download":
            path = "/reports/download?month=2026-08&site=KHB-01&lang=en"
        r = anon.get(path)
        assert r.status_code == 303 and r.headers["location"].startswith("/login?next="), (path, r.status_code)
        r = boss.get(path)
        expected = 403 if is_admin else 200
        if template == "/reports/download":
            continue  # PDF rendering is covered in test_reports.py
        assert r.status_code == expected, (path, "management", r.status_code)
        assert admin.get(path).status_code == 200, (path, "admin")


def test_role_matrix_post(app):
    anon, boss, admin = client(app), login(app, "boss", MGMT_PW), login(app, "admin", ADMIN_PW)
    boss_tok = token(boss)
    for method, template, path, is_admin in routes(app):
        if method != "POST" or template in ("/login", "/logout"):
            continue
        # anonymous: refused whatever it sends
        assert anon.post(path, data={"csrf": "x"}).status_code in (401, 403), path
        # management with a valid CSRF token: still refused on every admin route
        if is_admin:
            r = boss.post(path, data={"csrf": boss_tok, "month": "2026-08", "active": "false", "password": "x" * 12,
                                      "username": "eve", "role": "admin"})
            assert r.status_code == 403, (path, r.status_code)
    # nothing changed
    assert admin.get("/users").text.count("eve") == 0


def test_csrf_required_for_state_changes(app, conn):
    admin = login(app, "admin", ADMIN_PW)
    form = {"temp_min": "10", "temp_max": "40", "hum_min": "10", "hum_max": "90", "stuck_min_hours": "6"}
    assert admin.post("/thresholds/KHB-01", data=form).status_code == 403
    assert admin.post("/thresholds/KHB-01", data={**form, "csrf": "wrong"}).status_code == 403
    assert conn.execute("SELECT temp_max FROM sites WHERE code='KHB-01'").fetchone()["temp_max"] == 27
    r = admin.post("/thresholds/KHB-01", data={**form, "csrf": token(admin)})
    assert r.status_code == 200 and "Saved" in r.text
    assert conn.execute("SELECT temp_max FROM sites WHERE code='KHB-01'").fetchone()["temp_max"] == 40
    # header form (HTMX) works too
    r = admin.post("/thresholds/KHB-01", data={**form, "temp_max": "41"}, headers={"X-CSRF-Token": token(admin)})
    assert r.status_code == 200
    bad = admin.post("/thresholds/KHB-01", data={**form, "temp_max": "5", "csrf": token(admin)})
    assert bad.status_code == 422 and "min &lt; max" in bad.text


def test_login_rules(app, conn):
    c = client(app)
    tok = csrf_of(c)
    assert c.post("/login", data={"username": "boss", "password": "nope", "csrf": tok}).status_code == 401
    assert c.post("/login", data={"username": "boss", "password": MGMT_PW}).status_code == 403  # no CSRF
    # open redirects are neutralised
    for evil in ("https://evil.example/", "//evil.example", "/\\evil.example", "javascript:alert(1)"):
        r = c.post("/login", data={"username": "boss", "password": MGMT_PW, "csrf": csrf_of(c), "next": evil})
        assert r.headers["location"] == "/", evil
    # throttle: 5 failures -> 429 even with the right password
    c2 = client(app)
    for _ in range(5):
        c2.post("/login", data={"username": "admin", "password": "wrong", "csrf": csrf_of(c2)})
    r = c2.post("/login", data={"username": "admin", "password": ADMIN_PW, "csrf": csrf_of(c2)})
    assert r.status_code == 429


def test_password_change_and_deactivation_end_sessions(app, conn):
    boss = login(app, "boss", MGMT_PW)
    assert boss.get("/").status_code == 200
    uid = conn.execute("SELECT user_id FROM users WHERE username='boss'").fetchone()["user_id"]
    set_password(conn, uid, "another-password-1")
    assert boss.get("/").status_code == 303
    boss = login(app, "boss", "another-password-1")
    admin_id = conn.execute("SELECT user_id FROM users WHERE username='admin'").fetchone()["user_id"]
    set_active(conn, uid, False, admin_id)
    assert boss.get("/").status_code == 303


def test_session_cookie_flags_and_security_headers(app):
    c = client(app)
    r = c.get("/login")
    cookie = r.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=lax" in cookie
    for h in ("content-security-policy", "x-frame-options", "x-content-type-options", "referrer-policy"):
        assert h in r.headers
    assert "script-src 'self'" in r.headers["content-security-policy"]
    # no inline scripts anywhere (CSP would block them); JSON data blocks only
    admin = login(app, "admin", ADMIN_PW)
    for path in ("/", "/sites/KHB-01?month=2026-08", "/compare", "/uploads"):
        html = admin.get(path).text
        for tag in re.findall(r"<script[^>]*>", html):
            assert "src=" in tag or 'type="application/json"' in tag, (path, tag)
        assert " style=" not in html and " onclick=" not in html, path


def test_pages_show_unreliable_readings(app):
    boss = login(app, "boss", MGMT_PW)
    html = boss.get("/sites/KHB-01?month=2026-08").text
    assert "8 unreliable readings" in html.replace('<span class="num" dir="ltr">', "").replace("</span>", "")
    assert "Unreliable readings excluded" in html
    assert "SYNTHETIC" in boss.get("/export/sites.csv?month=2026-08").text.upper()


def test_csv_export(app):
    boss = login(app, "boss", MGMT_PW)
    r = boss.get("/export/sites.csv?month=2026-08&site=KHB-01&site=RUH-01")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv")
    lines = r.text.lstrip("﻿").splitlines()
    assert lines[0].startswith("SYNTHETIC DATA")
    assert lines[1].startswith("Site code,Site,Status,Readings")
    khb = next(x for x in lines if x.startswith("KHB-01"))
    # 20 readings received, 8 unreliable (stuck 23.4/41.0), averages over the 12 valid ones
    assert ",20,744,2.7,8," in khb


def test_upload_via_web_and_rejects_csv_is_safe(app, conn):
    admin = login(app, "admin", ADMIN_PW)
    rows = hour_rows(datetime(2026, 8, 1), 5) + [["+HYPERLINK(\"http://x\")", "01:00", 22, 40]]
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("RUH-02_2026-08.xlsx", make_xlsx(rows))
    files = [("files", ("batch.zip", buf.getvalue(), "application/zip")),
             ("files", ("JED-01_2026-08.xlsx", make_xlsx(hour_rows(datetime(2026, 8, 1), 3)), "application/x"))]
    r = admin.post("/uploads", files=files, headers={"X-CSRF-Token": token(admin), "HX-Request": "true"})
    assert r.status_code == 200
    assert r.text.count("loaded") >= 2
    up = conn.execute("SELECT upload_id, rejected FROM uploads WHERE filename='RUH-02_2026-08.xlsx'").fetchone()
    assert up["rejected"] == 1
    csv = admin.get(f"/uploads/{up['upload_id']}/rejects.csv").text
    assert "'+HYPERLINK" in csv  # spreadsheet formula prefix neutralised
    # same files again: nothing changes
    r = admin.post("/uploads", files=files, headers={"X-CSRF-Token": token(admin), "HX-Request": "true"})
    assert r.text.count('class="state duplicate_file"') == 2


@pytest.mark.skipif(not table("ar"), reason="ar.json not written yet (phase 8)")
def test_language_switch_and_rtl(app):
    boss = login(app, "boss", MGMT_PW)
    r = boss.get("/lang/ar?next=/sites/KHB-01?month=2026-08")
    assert r.status_code == 303 and r.headers["location"] == "/sites/KHB-01?month=2026-08"
    html = boss.get("/sites/KHB-01?month=2026-08").text
    assert '<html lang="ar" dir="rtl">' in html
    assert boss.get("/lang/xx").status_code == 404
    assert boss.get("/lang/en?next=https://evil.example").headers["location"] == "/"


def test_unknown_things_404(app):
    boss = login(app, "boss", MGMT_PW)
    assert boss.get("/sites/ZZZ-99").status_code == 404
    assert boss.get("/?month=2030-01").status_code == 404
    assert boss.get("/?month=garbage").status_code == 400
    assert boss.get("/no-such-page").status_code == 404
