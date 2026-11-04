"""src/pms/app_login.py: in a self-hosted install the app logs in as pms_app, which holds nothing of
its own and may only act as trust_app, never as the database's owner (compose.selfhost.yaml).
Roles are the server's, not one database's: each test makes a login of its own name and drops it."""

import re
import shutil
import uuid
from pathlib import Path

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg import sql
from psycopg.conninfo import make_conninfo

from pms import admin, app_login, nightly
from pms.api.app import Settings, create_app
from tools.check_rollback import Dbmate
from tools.scratch_db import scratch_database

ROOT = Path(__file__).resolve().parents[2]
COMPOSE = ROOT / "compose.selfhost.yaml"


@pytest.fixture
def books(database_url):
    """A migrated scratch database, its owner's URL, and a login role name to test with."""
    with scratch_database(database_url, "pms_app_login") as url:
        built = Dbmate(shutil.which("dbmate"), ROOT / "db/migrations", url).run(
            "--no-dump-schema", "up"
        )
        assert built.returncode == 0, built.stderr
        role = f"pms_app_{uuid.uuid4().hex[:8]}"
        try:
            yield url, role
        finally:
            with psycopg.connect(url, autocommit=True) as owner:
                owner.execute(sql.SQL("DROP ROLE IF EXISTS {}").format(sql.Identifier(role)))


def as_login(url, role):
    """The URL the app would use: the same database, as the login (the test server trusts it)."""
    return make_conninfo(url, user=role)


def memberships(conn, role):
    return conn.execute(
        "SELECT r.rolname, m.inherit_option, m.set_option FROM pg_auth_members AS m"
        " JOIN pg_roles AS r ON r.oid = m.roleid"
        " WHERE m.member = (SELECT oid FROM pg_roles WHERE rolname = %s) ORDER BY 1",
        (role,),
    ).fetchall()


def attributes(conn, role):
    return conn.execute(
        "SELECT rolcanlogin, rolsuper, rolinherit, rolcreaterole, rolcreatedb, rolreplication,"
        " rolbypassrls FROM pg_roles WHERE rolname = %s",
        (role,),
    ).fetchone()


def test_the_login_may_only_become_the_app_and_holds_nothing_of_its_own(books):
    url, role = books
    with psycopg.connect(url, autocommit=True) as owner:
        app_login.ensure(owner, "synthetic-password-1", role)

        assert attributes(owner, role) == (True, False, False, False, False, False, False)
        assert memberships(owner, role) == [("trust_app", False, True)]
        (kept,) = owner.execute(
            "SELECT rolpassword FROM pg_authid WHERE rolname = %s", (role,)
        ).fetchone()
        assert kept.startswith("SCRAM-SHA-256$") and "synthetic-password-1" not in kept
        # It may act as what trust_app may act as (with PR #194, trust_ai_agent), and no more.
        (ai_too,) = owner.execute(
            "SELECT pg_has_role('trust_app', 'trust_ai_agent', 'SET')"
        ).fetchone()

    with psycopg.connect(as_login(url, role), autocommit=True) as app:
        # As itself, before SET ROLE, it reads nothing and calls no write path.
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            app.execute("SELECT count(*) FROM trust_pmcs")
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            app.execute("SELECT trust_open_ledger_account(NULL, NULL, NULL, NULL, NULL, NULL)")
        app.execute("SET ROLE trust_app")
        assert app.execute("SELECT count(*) FROM trust_pmcs").fetchone() == (0,)
        # Undone, it is back to nothing: RESET ROLE leaves no way to the books.
        app.execute("RESET ROLE")
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            app.execute("SELECT count(*) FROM trust_pmcs")
        for other in ("postgres", "pg_read_all_data", "pg_write_all_data"):
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                app.execute(sql.SQL("SET ROLE {}").format(sql.Identifier(other)))
        try:
            app.execute("SET ROLE trust_ai_agent")
            became = True
        except psycopg.errors.InsufficientPrivilege:
            became = False
        assert became is ai_too


def test_run_again_it_takes_the_new_password_and_drops_any_other_membership(books):
    url, role = books
    with psycopg.connect(url, autocommit=True) as owner:
        app_login.ensure(owner, "synthetic-password-1", role)
        (first,) = owner.execute(
            "SELECT rolpassword FROM pg_authid WHERE rolname = %s", (role,)
        ).fetchone()
        # Something given by hand since: it goes on the next run.
        owner.execute(sql.SQL("GRANT pg_read_all_data TO {}").format(sql.Identifier(role)))

        app_login.ensure(owner, "synthetic-password-2", role)

        (second,) = owner.execute(
            "SELECT rolpassword FROM pg_authid WHERE rolname = %s", (role,)
        ).fetchone()
        assert second != first and second.startswith("SCRAM-SHA-256$")
        assert attributes(owner, role) == (True, False, False, False, False, False, False)
        assert memberships(owner, role) == [("trust_app", False, True)]
        with pytest.raises(ValueError, match="empty"):
            app_login.ensure(owner, "", role)


def test_the_app_the_admin_setup_and_the_nightly_run_work_as_the_login(books, monkeypatch, capsys):
    url, role = books
    with psycopg.connect(url, autocommit=True) as owner:
        app_login.ensure(owner, "synthetic-password-1", role)
    login = as_login(url, role)
    monkeypatch.setenv("DATABASE_URL", login)

    # docs/install.md, step 5, run in the app's container.
    assert (
        admin.main(["setup", "Synthetic Login Co", "First Admin", "first.admin@example.com"]) == 0
    )
    pmc = re.search(r"^company (\S+)$", capsys.readouterr().out, re.M).group(1)
    # The nightly run, as the app's scheduler or `python -m pms.nightly` runs it.
    assert nightly.main([]) == 0

    with TestClient(create_app(Settings(database_url=login, cookie_secure=False))) as client:
        assert client.get("/health").json() == {"status": "ok"}
        # A page that reads the books, through the app's pool as the login.
        public = client.get(f"/api/listings/{pmc}")
        assert public.status_code == 200 and public.json()["pmc"] == "Synthetic Login Co"


def test_main_needs_the_password_and_never_prints_it(books, monkeypatch, capsys):
    url, role = books
    monkeypatch.setattr(app_login, "ROLE", role)
    monkeypatch.setenv("DATABASE_URL", url)
    monkeypatch.delenv("PMS_APP_DB_PASSWORD", raising=False)

    assert app_login.main() == 2
    assert "set PMS_APP_DB_PASSWORD" in capsys.readouterr().err

    monkeypatch.setenv("PMS_APP_DB_PASSWORD", "synthetic-password-9")
    assert app_login.main() == 0
    said = capsys.readouterr()
    assert "synthetic-password-9" not in said.out + said.err
    assert f"the app connects as {role}" in said.err


def test_a_verifier_is_postgres_scram_form_and_salted_afresh_each_time():
    made = app_login.scram_verifier("synthetic", salt=b"\x00" * 16)

    assert re.fullmatch(
        r"SCRAM-SHA-256\$4096:[A-Za-z0-9+/=]{24}\$[A-Za-z0-9+/=]{44}:[A-Za-z0-9+/=]{44}", made
    )
    assert made == app_login.scram_verifier("synthetic", salt=b"\x00" * 16)
    assert app_login.scram_verifier("synthetic") != app_login.scram_verifier("synthetic")
    assert "synthetic" not in made


def test_the_installs_app_never_connects_as_the_databases_owner():
    text = COMPOSE.read_text()
    urls = re.findall(r"DATABASE_URL: (\S+)", text)
    app_block = text.split("\n  app:\n")[1]
    app_url = re.search(r"DATABASE_URL: (\S+)", app_block).group(1)

    assert app_url.startswith("postgres://pms_app:${PMS_APP_DB_PASSWORD:?")
    # Only migrate connects as the owner, and it makes the app's login after the migrations.
    assert [u for u in urls if u.startswith("postgres://postgres:")] == [urls[0]]
    assert 'command: ["sh", "-c", "dbmate --wait up && python -m pms.app_login"]' in text
    # The app's container doesn't keep the owner's password.
    assert 'PMS_DB_PASSWORD: ""' in app_block
