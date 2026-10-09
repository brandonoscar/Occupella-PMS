"""tools/check_rollback.py: irreversible migrations refuse cleanly; reversible ones round-trip."""

import shutil
import uuid
from urllib.parse import urlsplit, urlunsplit

import pytest

from tools import check_rollback

IRREVERSIBLE = """-- migrate:up
CREATE TABLE ledger_like (id int);
-- migrate:down
DO $$ BEGIN RAISE EXCEPTION 'ledger_like cannot be rolled back'; END $$;
"""
REVERSIBLE = """-- migrate:up
CREATE TABLE notes (id int);
-- migrate:down
DROP TABLE notes;
"""
LEAKY = """-- migrate:up
CREATE TABLE notes (id int);
-- migrate:down
SELECT 1;
"""
LYING = """-- migrate:up
CREATE TABLE notes (id int);
-- migrate:down
-- RAISE EXCEPTION 'notes cannot be rolled back'   (commented out, so it rolls back after all)
DROP TABLE notes;
"""


def test_down_section_and_irreversible_marker():
    assert check_rollback.down_section("-- migrate:up\nx\n") == ""
    assert check_rollback.is_irreversible(IRREVERSIBLE)
    assert not check_rollback.is_irreversible(REVERSIBLE)
    assert not check_rollback.is_irreversible("RAISE EXCEPTION 'up only cannot be rolled back'")


@pytest.fixture
def scratch(database_url, tmp_path):
    """An empty database and migrations dir; dropped afterwards."""
    name = f"pms_rollback_{uuid.uuid4().hex[:10]}"
    url = urlunsplit(urlsplit(database_url)._replace(path="/" + name))
    migrations = tmp_path / "migrations"
    migrations.mkdir()
    dbmate = check_rollback.Dbmate(shutil.which("dbmate"), migrations, url)
    yield dbmate
    dbmate.run("drop")


def migrate(dbmate, **files):
    for name, text in files.items():
        (dbmate.migrations / f"{name}.sql").write_text(text)
    assert dbmate.run("--no-dump-schema", "up").returncode == 0


def test_irreversible_newest_migration_refuses_and_changes_nothing(scratch, capsys):
    migrate(scratch, **{"20270101000001_a": IRREVERSIBLE})
    assert check_rollback.check(scratch) == []
    assert "refuses to roll back" in capsys.readouterr().out


def test_reversible_migrations_roll_back_and_reapply(scratch, capsys):
    migrate(scratch, **{"20270101000001_a": IRREVERSIBLE, "20270101000002_b": REVERSIBLE})
    assert check_rollback.check(scratch) == []
    out = capsys.readouterr().out
    assert "20270101000002_b.sql rolled back" in out
    assert "20270101000001_a.sql refuses" in out


def test_down_that_leaves_the_table_fails_on_reapply(scratch):
    migrate(scratch, **{"20270101000001_a": LEAKY})
    problems = check_rollback.check(scratch)
    assert problems and "re-applying migrations failed" in problems[0]


def test_marker_that_does_not_raise_is_caught(scratch):
    migrate(scratch, **{"20270101000001_a": LYING})
    problems = check_rollback.check(scratch)
    assert problems[0] == "20270101000001_a.sql says it cannot be rolled back, but it was."


def test_failing_down_section_is_reported(scratch):
    broken = REVERSIBLE.replace("DROP TABLE notes;", "DROP TABLE no_such_table;")
    migrate(scratch, **{"20270101000001_a": broken})
    problems = check_rollback.check(scratch)
    assert "rollback failed" in problems[0]


def test_schema_dump_failure_is_raised(tmp_path):
    dbmate = check_rollback.Dbmate(
        shutil.which("dbmate"), tmp_path, "postgres://nobody@127.0.0.1:1/none?sslmode=disable"
    )
    with pytest.raises(RuntimeError, match="dbmate dump failed"):
        dbmate.schema()


def test_main_needs_a_database_and_dbmate(monkeypatch, capsys):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    assert check_rollback.main([]) == 1
    assert "needs DATABASE_URL" in capsys.readouterr().out


def test_main_on_the_real_migrations(scratch, monkeypatch, capsys):
    real = check_rollback.ROOT / "db/migrations"
    monkeypatch.setenv("DATABASE_URL", scratch.env["DATABASE_URL"])
    assert scratch.run("--migrations-dir", str(real), "--no-dump-schema", "up").returncode == 0
    assert check_rollback.main(["--migrations", str(real)]) == 0
    newest_irreversible = next(
        path.name
        for path in sorted(real.glob("*.sql"), reverse=True)
        if check_rollback.is_irreversible(path.read_text())
    )
    assert f"{newest_irreversible} refuses to roll back" in capsys.readouterr().out
