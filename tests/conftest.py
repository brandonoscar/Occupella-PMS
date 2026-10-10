"""Test setup: one fresh database per pytest run, built by applying every migration with dbmate.

DATABASE_URL names a throwaway Postgres 16 server (any database on it). The run creates a new
database on that server, applies the migrations, runs the tests and drops the database. If
DATABASE_URL or dbmate is missing the run errors.

Nothing is skipped: a skip marker, a runtime skip, or an xfail without a link to an open issue
fails the run (see CLAUDE.md, "The standing rule").

Environment knobs, all optional:
  PMS_MIGRATIONS_DIR     apply migrations from here instead of db/migrations (mutation testing)
  PMS_SQL_COVERAGE_OUT   write PL/pgSQL coverage (plpgsql_check profiler) to this JSON file
  HYPOTHESIS_PROFILE     pr (default), nightly or mutation; example counts live in
                         ci/thresholds.toml

Every fixture is synthetic: no real names, addresses, bank numbers or tax IDs.
"""

import os
import shutil
import subprocess
import tomllib
import uuid
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import psycopg
import pytest
from hypothesis import HealthCheck, Phase, settings
from psycopg import sql

from tools import sql_coverage
from tools.run_rules import marker_problems, outcome_problems

ROOT = Path(__file__).resolve().parent.parent

_properties = tomllib.loads((ROOT / "ci/thresholds.toml").read_text())["properties"]
_common = {
    "deadline": None,
    "print_blob": True,
    "stateful_step_count": _properties["steps_per_example"],
    "suppress_health_check": [HealthCheck.too_slow],
}
settings.register_profile("pr", max_examples=_properties["pr_examples"], **_common)
settings.register_profile("nightly", max_examples=_properties["nightly_examples"], **_common)
# Mutation runs only need to know whether a test fails: no shrinking, and no example database,
# so a mutant's failures never replay in later normal runs.
settings.register_profile(
    "mutation",
    max_examples=25,
    phases=[Phase.explicit, Phase.generate],
    database=None,
    **_common,
)
settings.load_profile(os.environ.get("HYPOTHESIS_PROFILE", "pr"))


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
    migrations = Path(os.environ.get("PMS_MIGRATIONS_DIR", ROOT / "db/migrations"))
    coverage_out = os.environ.get("PMS_SQL_COVERAGE_OUT")

    name = f"pms_test_{uuid.uuid4().hex[:12]}"
    admin_url = _url_for_database(server_url, "postgres")
    test_url = _url_for_database(server_url, name)

    with psycopg.connect(admin_url, autocommit=True) as admin:
        admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    try:
        # The URL goes to dbmate through the environment, never the command line.
        migrate = subprocess.run(
            [dbmate, "--migrations-dir", str(migrations), "--no-dump-schema", "up"],
            env={**os.environ, "DATABASE_URL": test_url},
            capture_output=True,
            text=True,
        )
        if migrate.returncode != 0:
            raise RuntimeError(f"dbmate up failed:\n{migrate.stdout}\n{migrate.stderr}")
        if coverage_out:
            with psycopg.connect(test_url, autocommit=True) as connection:
                sql_coverage.start(connection, name)
        yield test_url
        if coverage_out:
            with psycopg.connect(test_url, autocommit=True) as connection:
                sql_coverage.write_raw(connection, Path(coverage_out))
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


@pytest.fixture
def ai_conn(database_url):
    """A connection acting as trust_ai_agent, the role behind the API key Occupella will hold."""
    with psycopg.connect(database_url, autocommit=True) as connection:
        connection.execute("SET ROLE trust_ai_agent")
        yield connection


def pytest_addoption(parser):
    parser.addoption(
        "--update-goldens",
        action="store_true",
        help="rewrite tests/golden expected files from the current output (review the diff)",
    )


def pytest_collection_modifyitems(config, items):
    problems = marker_problems(items)
    if problems:
        raise pytest.UsageError("\n".join(problems))
    # Fast example tests first, slow property tests last, so `pytest -x` fails fast.
    items.sort(key=lambda item: "/properties/" in item.nodeid)


def pytest_sessionfinish(session, exitstatus):
    reporter = session.config.pluginmanager.get_plugin("terminalreporter")
    problems = outcome_problems(reporter.stats) if reporter else []
    if problems and session.exitstatus == pytest.ExitCode.OK:
        for problem in problems:
            reporter.write_line(f"FAIL: {problem}")
        session.exitstatus = pytest.ExitCode.TESTS_FAILED
