"""Request dependencies: DB connection, current user, role checks, CSRF, template context."""

import hashlib
import hmac
import secrets
from collections.abc import Iterator
from importlib.resources import files

import psycopg
from fastapi import Depends, HTTPException, Request
from fastapi.templating import Jinja2Templates

from p7 import fmt
from p7.auth import get_user
from p7.db import connect
from p7.i18n import LANGS, Translator, direction, normalise

templates = Jinja2Templates(directory=str(files("p7.web").joinpath("templates")))
templates.env.globals.update(n=fmt.n, nh=fmt.nh, nts=fmt.nts, ni=fmt.ni, number=fmt.number, LANGS=LANGS)
templates.env.policies["json.dumps_kwargs"] = {"sort_keys": True, "default": str}


class LoginRequired(Exception):
    pass


def db(request: Request) -> Iterator[psycopg.Connection]:
    s = request.app.state.settings
    with connect(s, request.app.state.dbname) as conn:
        yield conn


def lang_of(request: Request) -> str:
    return normalise(request.session.get("lang"))


def csrf_token(request: Request) -> str:
    tok = request.session.get("csrf")
    if not tok:
        tok = request.session["csrf"] = secrets.token_urlsafe(32)
    return tok


def session_version(user: dict) -> str:
    """Changes when the password changes, which logs out every other session of that user. Derived with a hash
    so the (signed, not encrypted) session cookie never carries any part of the password hash."""
    return hashlib.sha256(("p7-session|" + user["password_hash"]).encode()).hexdigest()[:20]


def current_user(request: Request, conn: psycopg.Connection = Depends(db)) -> dict:
    uid = request.session.get("uid")
    user = get_user(conn, uid) if isinstance(uid, int) else None
    if not user or not user["active"] or request.session.get("pwv") != session_version(user):
        request.session.pop("uid", None)
        raise LoginRequired()
    return user


def admin_user(user: dict = Depends(current_user)) -> dict:
    if user["role"] != "admin":
        raise HTTPException(status_code=403, detail="admin only")
    return user


async def check_csrf(request: Request) -> None:
    """Every state-changing request carries the session's CSRF token (form field or X-CSRF-Token header)."""
    expected = request.session.get("csrf")
    sent = request.headers.get("x-csrf-token")
    if not sent:
        ctype = request.headers.get("content-type", "")
        if ctype.startswith(("application/x-www-form-urlencoded", "multipart/form-data")):
            form = await request.form()
            sent = form.get("csrf")
    if not expected or not isinstance(sent, str) or not hmac.compare_digest(expected, sent):
        raise HTTPException(status_code=403, detail="invalid or missing CSRF token; reload the page")


def page(request: Request, name: str, ctx: dict, status_code: int = 200, user: dict | None = None):
    lang = lang_of(request)
    t = Translator(lang)
    base = {"request": request, "t": t, "lang": lang, "dir": direction(lang), "user": user,
            "csrf": csrf_token(request), "path": request.url.path, "query": request.url.query}
    return templates.TemplateResponse(request, name, {**base, **ctx}, status_code=status_code)
