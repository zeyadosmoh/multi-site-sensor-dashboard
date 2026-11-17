"""Database helpers: connect, migrate, ensure test database."""

from importlib.resources import files

import psycopg
from psycopg.rows import dict_row

from p7.config import Settings, get_settings

SQL = files("p7.db").joinpath("sql")


def connect(settings: Settings | None = None, dbname: str | None = None, autocommit: bool = False,
            dict_rows: bool = True) -> psycopg.Connection:
    s = settings or get_settings()
    return psycopg.connect(s.dsn(dbname), autocommit=autocommit, row_factory=dict_row if dict_rows else None)


def ensure_database(settings: Settings, dbname: str) -> None:
    """Create `dbname` on the same server if missing (used for the p7_test database)."""
    with connect(settings, settings.postgres_db, autocommit=True) as conn:
        if not conn.execute("SELECT 1 FROM pg_database WHERE datname = %s", (dbname,)).fetchone():
            conn.execute(f'CREATE DATABASE "{dbname}"')


def migrate(conn: psycopg.Connection) -> list[str]:
    """Apply numbered migrations once (NNN_*.sql), then always re-create views (views.sql). Returns applied names."""
    applied: list[str] = []
    with conn.transaction():
        conn.execute("SELECT pg_advisory_xact_lock(7007)")
        conn.execute("""CREATE TABLE IF NOT EXISTS schema_migrations (
                            name text PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT now())""")
        done = {r["name"] if isinstance(r, dict) else r[0]
                for r in conn.execute("SELECT name FROM schema_migrations").fetchall()}
        for f in sorted(p for p in SQL.iterdir() if p.name[:3].isdigit() and p.name.endswith(".sql")):
            if f.name in done:
                continue
            conn.execute(f.read_text())
            conn.execute("INSERT INTO schema_migrations (name) VALUES (%s)", (f.name,))
            applied.append(f.name)
        conn.execute(SQL.joinpath("views.sql").read_text())
    return applied


def reset(conn: psycopg.Connection) -> None:
    """Drop everything this app owns (tests and `p7 reset-db`)."""
    with conn.transaction():
        conn.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
