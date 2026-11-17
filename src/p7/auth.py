"""Users and passwords (argon2id). Roles: admin (upload, thresholds, users) and management (view + download)."""

import psycopg
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

ROLES = ("admin", "management")
_ph = PasswordHasher()
# verify something even for unknown users so response time doesn't reveal which usernames exist
_DUMMY_HASH = _ph.hash("not-a-real-password")
MIN_PASSWORD = 10


class UserError(ValueError):
    pass


def hash_password(password: str) -> str:
    if len(password) < MIN_PASSWORD:
        raise UserError(f"password must be at least {MIN_PASSWORD} characters")
    return _ph.hash(password)


def create_user(conn: psycopg.Connection, username: str, password: str, role: str) -> int:
    username = username.strip().lower()
    if role not in ROLES:
        raise UserError(f"role must be one of {ROLES}")
    pw_hash = hash_password(password)
    try:
        with conn.transaction():
            return conn.execute(
                "INSERT INTO users (username, password_hash, role) VALUES (%s, %s, %s) RETURNING user_id",
                (username, pw_hash, role)).fetchone()["user_id"]
    except psycopg.errors.UniqueViolation as e:
        raise UserError(f"user '{username}' already exists") from e
    except psycopg.errors.CheckViolation as e:
        raise UserError("username: 3-40 characters, lowercase letters, digits, . _ -") from e


def get_user_by_name(conn: psycopg.Connection, username: str) -> dict | None:
    return conn.execute("SELECT * FROM users WHERE username = %s", (username.strip().lower(),)).fetchone()


def get_user(conn: psycopg.Connection, user_id: int) -> dict | None:
    return conn.execute("SELECT * FROM users WHERE user_id = %s", (user_id,)).fetchone()


def authenticate(conn: psycopg.Connection, username: str, password: str) -> dict | None:
    user = get_user_by_name(conn, username)
    try:
        _ph.verify(user["password_hash"] if user else _DUMMY_HASH, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return None
    if not user or not user["active"]:
        return None
    with conn.transaction():
        conn.execute("UPDATE users SET last_login_at = now() WHERE user_id = %s", (user["user_id"],))
        if _ph.check_needs_rehash(user["password_hash"]):
            conn.execute("UPDATE users SET password_hash = %s WHERE user_id = %s",
                         (_ph.hash(password), user["user_id"]))
    return user


def set_active(conn: psycopg.Connection, user_id: int, active: bool, acting_user_id: int) -> None:
    if user_id == acting_user_id and not active:
        raise UserError("you cannot deactivate your own account")
    with conn.transaction():
        if not active:
            target = get_user(conn, user_id)
            if target and target["role"] == "admin":
                n = conn.execute("SELECT count(*) AS n FROM users WHERE role = 'admin' AND active").fetchone()["n"]
                if n <= 1:
                    raise UserError("cannot deactivate the last active admin")
        conn.execute("UPDATE users SET active = %s WHERE user_id = %s", (active, user_id))


def set_password(conn: psycopg.Connection, user_id: int, password: str) -> None:
    pw_hash = hash_password(password)
    with conn.transaction():
        conn.execute("UPDATE users SET password_hash = %s WHERE user_id = %s", (pw_hash, user_id))


def list_users(conn: psycopg.Connection) -> list[dict]:
    return conn.execute("SELECT user_id, username, role, active, created_at, last_login_at FROM users "
                        "ORDER BY username").fetchall()
