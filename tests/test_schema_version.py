"""The database's migrations against the code's (src/pms/schema.py, migration 20261105001100).

docs/edge-cases.md H-041: nothing compared schema_migrations to the code, so an older PMS (an
older image tag, or a newer backup restored under one) started and posted money on a schema it
was never tested on. The app's pooled connections, the nightly run and the webhook courier now
refuse a database migrated by a newer PMS, or not migrated yet, saying which and what to do."""

import base64

import psycopg
import pytest

from pms import nightly, schema, webhooks


def test_the_code_knows_every_migration_it_ships():
    shipped = schema.shipped()
    assert shipped is not None and "20261009000001" in shipped
    assert all(len(version) == 14 and version.isdigit() for version in shipped)


def test_the_database_the_tests_run_on_matches_the_code(app_conn):
    schema.check(app_conn)  # nothing to say


def test_a_database_a_newer_pms_migrated_is_refused(app_conn):
    shipped = schema.shipped()
    assert shipped is not None
    older = shipped - {max(shipped)}

    with pytest.raises(schema.Mismatch) as refused:
        schema.check(app_conn, older)

    assert str(refused.value) == (
        f"the database was migrated by a newer PMS (migration {max(shipped)}), which this one has"
        " never run on: run that version, or restore a backup made by this one"
    )


def test_a_database_not_migrated_yet_is_refused(app_conn):
    shipped = schema.shipped()
    assert shipped is not None

    with pytest.raises(schema.Mismatch) as refused:
        schema.check(app_conn, shipped | {"29991231000000", "29991231000001"})

    assert str(refused.value) == (
        "the database lacks this PMS's migration 29991231000000 and 1 more: run the migrations"
        " first (dbmate up)"
    )


def test_code_shipped_without_its_migrations_checks_nothing(app_conn, tmp_path, monkeypatch):
    assert schema.shipped(tmp_path / "nowhere") is None
    monkeypatch.setattr(schema, "MIGRATIONS", tmp_path / "nowhere")
    assert schema.shipped() is None
    schema.check(app_conn)  # can't tell, so nothing to say


def test_only_the_app_reads_the_versions(conn, ai_conn):
    assert conn.execute("SELECT count(*) FROM trust_schema_versions()").fetchone()[0] > 100
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        ai_conn.execute("SELECT trust_schema_versions()")


def test_the_nightly_run_and_the_courier_check_first(database_url, monkeypatch):
    def refuse(conn, known=None):
        raise schema.Mismatch("the database was migrated by a newer PMS")

    monkeypatch.setattr(schema, "check", refuse)
    monkeypatch.setenv("PMS_DATA_KEY", base64.b64encode(bytes(32)).decode())
    with pytest.raises(schema.Mismatch):
        nightly.Scheduler(database_url, "trust_app", "http://localhost:8000", None).run_once()
    with pytest.raises(schema.Mismatch):
        webhooks.Courier(database_url, "trust_app").once()
