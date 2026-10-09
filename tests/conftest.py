"""Test setup: one fresh database per pytest run, built by applying every migration with dbmate.

DATABASE_URL names a throwaway Postgres 16 server (any database on it). The run creates a new
database on that server, applies db/migrations, runs the tests and drops the database. If
DATABASE_URL or dbmate is missing the run errors, and a skipped test fails the run.

Every fixture is synthetic: no real names, addresses, bank numbers or tax IDs.
"""

import os
import shutil
import subprocess
import uuid
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import psycopg
import pytest
from psycopg import sql

ROOT = Path(__file__).resolve().parent.parent


def _url_for_database(url: str, name: str) -> str:
    return urlunsplit(urlsplit(url)._replace(path="/" + name))


@pytest.fixture(scope="session")
def database_url():
    server_url = os.environ.get("DATABASE_URL")
    if not server_url:
        raise RuntimeError("DATABASE_URL is not set. See CLAUDE.md, 'Run the tests'.")
    dbmate = shutil.which("dbmate")
    if dbmate is None:
        raise RuntimeError("dbmate is not on PATH. See CLAUDE.md, 'Run the tests'.")

    name = f"pms_test_{uuid.uuid4().hex[:12]}"
    admin_url = _url_for_database(server_url, "postgres")
    test_url = _url_for_database(server_url, name)

    with psycopg.connect(admin_url, autocommit=True) as admin:
        admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    try:
        # The URL goes to dbmate through the environment, never the command line.
        migrate = subprocess.run(
            [dbmate, "--migrations-dir", str(ROOT / "db/migrations"), "--no-dump-schema", "up"],
            env={**os.environ, "DATABASE_URL": test_url},
            capture_output=True,
            text=True,
        )
        if migrate.returncode != 0:
            raise RuntimeError(f"dbmate up failed:\n{migrate.stdout}\n{migrate.stderr}")
        yield test_url
    finally:
        with psycopg.connect(admin_url, autocommit=True) as admin:
            admin.execute(
                sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(name))
            )


@pytest.fixture
def conn(database_url):
    """A connection as the role that ran the migrations, which owns every table."""
    with psycopg.connect(database_url, autocommit=True) as connection:
        yield connection


@pytest.fixture
def app_conn(database_url):
    """A connection acting as trust_app, the role the application will use."""
    with psycopg.connect(database_url, autocommit=True) as connection:
        connection.execute("SET ROLE trust_app")
        yield connection


def pytest_sessionfinish(session, exitstatus):
    reporter = session.config.pluginmanager.get_plugin("terminalreporter")
    skipped = reporter.stats.get("skipped", []) if reporter else []
    if skipped and session.exitstatus == pytest.ExitCode.OK:
        reporter.write_line(f"FAIL: {len(skipped)} test(s) skipped; every test must run.")
        session.exitstatus = pytest.ExitCode.TESTS_FAILED
