"""Screenshots of the running app (desktop + phone width, English + Arabic) for docs/user-guide.md.

    python scripts/screenshots.py --out docs/img [--base http://127.0.0.1:8790] [--lang en,ar]

Signs in with ADMIN_USERNAME / ADMIN_PASSWORD from .env. Reports console errors and CSP violations; exits 1 if any.
"""

import argparse
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from p7.config import get_settings  # noqa: E402

PAGES = [
    ("overview", "/?month=2026-08"),
    ("site", "/sites/KHB-01?month=2026-08"),
    ("site-jed", "/sites/JED-01?month=2026-08"),
    ("compare", "/compare?month=2026-08"),
    ("reports", "/reports?month=2026-08"),
    ("uploads", "/uploads"),
    ("thresholds", "/thresholds"),
]
PHONE_PAGES = {"overview", "site", "uploads"}
VIEWPORTS = {"desktop": {"width": 1366, "height": 900}, "phone": {"width": 390, "height": 844}}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default=None)
    ap.add_argument("--out", default="docs/img")
    ap.add_argument("--lang", default="en,ar")
    ap.add_argument("--full", action="store_true", help="full-page screenshots")
    ap.add_argument("--upload", help="a file to upload for the upload-results screenshot (e.g. the sample zip)")
    ap.add_argument("--timeout", type=float, default=30, help="seconds to wait for each page (raise on a slow machine)")
    a = ap.parse_args()
    s = get_settings()
    base = a.base or f"http://{s.app_host}:{s.app_port}"
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    problems: list[str] = []
    with sync_playwright() as p:
        browser = p.chromium.launch()
        for vp_name, vp in VIEWPORTS.items():
            ctx = browser.new_context(viewport=vp, device_scale_factor=2 if vp_name == "phone" else 1,
                                      is_mobile=vp_name == "phone", has_touch=vp_name == "phone")
            page = ctx.new_page()
            page.set_default_timeout(a.timeout * 1000)
            page.set_default_navigation_timeout(a.timeout * 1000)
            page.on("console", lambda m: m.type == "error" and problems.append(f"console: {m.text}"))
            page.on("pageerror", lambda e: problems.append(f"pageerror: {e}"))
            page.goto(f"{base}/login")
            if vp_name == "desktop":
                page.screenshot(path=str(out / "login-en-desktop.png"))
            page.fill("input[name=username]", s.admin_username)
            page.fill("input[name=password]", s.admin_password)
            page.click("button[type=submit]")
            page.wait_for_url(f"{base}/")
            for lang in a.lang.split(","):
                page.goto(f"{base}/lang/{lang}?next=/")
                for name, path in PAGES:
                    if vp_name == "phone" and name not in PHONE_PAGES:
                        continue
                    page.goto(base + path, wait_until="networkidle")
                    page.wait_for_timeout(600)  # charts' first paint
                    width = page.evaluate("document.documentElement.scrollWidth")
                    if width > vp["width"] + 1:
                        problems.append(f"{name} {lang} {vp_name}: horizontal scroll ({width}px > {vp['width']}px)")
                    page.screenshot(path=str(out / f"{name}-{lang}-{vp_name}.png"), full_page=a.full)
                    if vp_name == "phone" and name == "overview":
                        page.evaluate("document.querySelector('table.sites').scrollIntoView()")
                        page.screenshot(path=str(out / f"sites-table-{lang}-phone.png"))
                if vp_name == "desktop" and lang == "en" and a.upload:
                    # re-upload the committed sample month: every file is reported as already loaded
                    page.goto(base + "/uploads")
                    page.set_input_files("input[type=file]", a.upload)
                    page.click("form.upload button[type=submit]")
                    page.wait_for_selector("#upload-results table", timeout=60000)
                    page.locator("#upload-results").scroll_into_view_if_needed()
                    page.screenshot(path=str(out / "upload-results-en-desktop.png"))
            ctx.close()
        browser.close()
    for pr in problems:
        print("PROBLEM", pr)
    print(f"screenshots -> {out}")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
