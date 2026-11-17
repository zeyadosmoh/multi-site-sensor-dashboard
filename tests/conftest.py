"""Shared fixtures. DB tests run against a separate database, p7_test, on the same Postgres server."""

import os

import pytest

from p7.config import Settings
from p7.db import connect, ensure_database, migrate, reset
from p7.sites import seed_sites

TEST_DB = os.environ.get("P7_TEST_DB", "p7_test")
TABLES = "reports, threshold_audit, row_rejects, readings, uploads, users, sites"


@pytest.fixture(scope="session")
def settings() -> Settings:
    s = Settings(reports_after_upload=False)
    ensure_database(s, TEST_DB)
    return s


@pytest.fixture(scope="session")
def _db(settings):
    conn = connect(settings, TEST_DB)
    reset(conn)
    migrate(conn)
    yield conn
    conn.close()


@pytest.fixture
def conn(_db):
    """Empty tables (sites re-seeded with default thresholds, ids restart at 1) for every test."""
    _db.rollback()
    _db.autocommit = False
    with _db.transaction():
        _db.execute(f"TRUNCATE {TABLES} RESTART IDENTITY CASCADE")
        _db.execute("UPDATE app_config SET reading_interval_minutes = 60")
    seed_sites(_db)
    yield _db
    _db.rollback()
    _db.autocommit = False
