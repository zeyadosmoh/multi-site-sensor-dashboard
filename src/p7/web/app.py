"""FastAPI app factory: sessions, security headers, static files, error pages, routes."""

from importlib.resources import files
from urllib.parse import quote

from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException
from starlette.middleware.sessions import SessionMiddleware

from p7.config import Settings, get_settings
from p7.web import admin, views
from p7.web.deps import LoginRequired, page

CSP = ("default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data: https://tile.openstreetmap.org; "
       "font-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'")


def create_app(settings: Settings | None = None, dbname: str | None = None) -> FastAPI:
    s = settings or get_settings()
    if not s.session_secret or s.session_secret == "change-me" or len(s.session_secret) < 32:
        raise RuntimeError("SESSION_SECRET must be set to a long random value (run `python -m p7 setup-env`)")
    app = FastAPI(title="P7 sensor dashboard", docs_url=None, redoc_url=None, openapi_url=None)
    app.state.settings = s
    app.state.dbname = dbname
    app.add_middleware(SessionMiddleware, secret_key=s.session_secret, session_cookie="p7_session",
                       max_age=8 * 3600, same_site="lax", https_only=s.cookie_secure)

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        resp: Response = await call_next(request)
        resp.headers["Content-Security-Policy"] = CSP
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["X-Frame-Options"] = "DENY"
        # OpenStreetMap's tile policy needs a Referer; send only the origin
        resp.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        resp.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        if not request.url.path.startswith("/static/"):
            resp.headers["Cache-Control"] = "no-store"
        return resp

    @app.exception_handler(LoginRequired)
    async def to_login(request: Request, exc: LoginRequired):
        target = "/login?next=" + quote(request.url.path + (f"?{request.url.query}" if request.url.query else ""))
        if request.headers.get("hx-request"):
            return Response(status_code=401, headers={"HX-Redirect": target})
        if request.method != "GET":
            return Response("login required", status_code=401)
        return RedirectResponse(target, status_code=303)

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, exc: HTTPException):
        if request.headers.get("hx-request") or request.url.path.startswith(("/static/", "/export/")):
            return Response(str(exc.detail), status_code=exc.status_code, media_type="text/plain")
        return page(request, "error.html", {"status": exc.status_code, "detail": exc.detail},
                    status_code=exc.status_code)

    app.mount("/static", StaticFiles(directory=str(files("p7.web").joinpath("static"))), name="static")
    app.include_router(views.router)
    app.include_router(admin.router)
    return app
