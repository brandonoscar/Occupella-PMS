"""tools/check_upgrade.py: migrations must upgrade a database that already holds money."""

import shutil
from pathlib import Path

import pytest

from tools import check_upgrade
from tools.check_rollback import Dbmate
from tools.scratch_db import scratch_database

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def base(repo):
    """A repo whose base commit has this repo's migrations and self-host seed."""
    for path in [*ROOT.glob("db/migrations/*.sql"), ROOT / "selfhost/__init__.py"]:
        repo.write(str(path.relative_to(ROOT)), path.read_text())
    repo.write("selfhost/smoke.py", (ROOT / "selfhost/smoke.py").read_text())
    return repo.commit("base")


def add_migration(repo, sql):
    repo.write("db/migrations/20991231000001_new.sql", f"-- migrate:up\n{sql}\n-- migrate:down\n")
    repo.commit("new migration")


def upgrade(repo, base, database_url):
    return check_upgrade.check(repo.path, base, database_url, shutil.which("dbmate"))


def test_migration_that_keeps_the_money_upgrades_cleanly(repo, base, database_url, capsys):
    add_migration(repo, "ALTER TABLE trust_owners ADD COLUMN note text;")

    code = check_upgrade.main(["--base", base, "--root", str(repo.path)])

    out = capsys.readouterr().out
    assert code == 0
    assert "Applied: 20991231000001_new.sql" in out
    assert out.count("healthy: every ledger invariant holds") == 2  # base seed, then upgraded
    assert "pgledger_entries 200 rows" in out


def test_migration_that_only_fails_once_rows_exist_is_caught(repo, base, database_url):
    add_migration(repo, "ALTER TABLE trust_owners ADD COLUMN tax_class text NOT NULL;")
    # It applies to an empty database, so the other migration checks would pass it.
    with scratch_database(database_url, "pms_empty") as url:
        empty = Dbmate(shutil.which("dbmate"), repo.path / "db/migrations", url)
        assert empty.run("--no-dump-schema", "up").returncode == 0

    problems = upgrade(repo, base, database_url)

    assert len(problems) == 1
    assert "fail on a database that already holds money" in problems[0]
    assert "tax_class" in problems[0]


def test_migration_that_changes_posted_money_is_caught(repo, base, database_url):
    add_migration(repo, "UPDATE pgledger_accounts SET version = version + 1;")

    assert upgrade(repo, base, database_url) == [
        "pgledger_accounts: the upgrade changed money that was already posted."
    ]


def test_schema_that_breaks_new_postings_is_caught(repo, base, database_url):
    # NOT VALID skips the existing rows, so the upgrade itself succeeds; new PMCs then fail.
    add_migration(
        repo,
        "ALTER TABLE trust_pmcs ADD CONSTRAINT short_name"
        " CHECK (length(display_name) < 10) NOT VALID;",
    )

    problems = upgrade(repo, base, database_url)

    assert len(problems) == 1
    assert "self-host smoke failed on the upgraded database" in problems[0]
    assert "short_name" in problems[0]


def test_edited_or_deleted_committed_migrations_are_refused(repo, base):
    migrations = sorted((repo.path / "db/migrations").glob("*.sql"))
    migrations[-1].write_text(migrations[-1].read_text() + "\n-- one more line\n")
    migrations[0].unlink()
    repo.commit("rewrite history")

    problems = check_upgrade.check(repo.path, base, "postgres://unused", "dbmate")

    assert problems == [
        f"{migrations[0].name} is on the base branch but was deleted.",
        f"{migrations[-1].name} is on the base branch but was edited. A committed migration is "
        "never edited: add a new one.",
    ]


def test_base_without_a_seed_is_refused(repo):
    repo.write("db/migrations/20270101000001_a.sql", "-- migrate:up\nSELECT 1;\n")
    base = repo.commit("no seed")

    assert check_upgrade.check(repo.path, base, "postgres://unused", "dbmate") == [
        "the base branch has no selfhost/smoke.py to seed its schema with."
    ]


def test_base_that_does_not_build_or_seed_is_reported(repo, base, database_url):
    repo.write("selfhost/smoke.py", "raise SystemExit('no ledger here')\n")
    seed_fails = repo.commit("seed fails")
    repo.write("selfhost/smoke.py", "print('seeded nothing')\n")
    seeds_nothing = repo.commit("seed posts nothing")
    repo.write("db/migrations/20270101000001_a.sql", "-- migrate:up\nSELEC 1;\n")
    broken = repo.commit("broken migration")

    assert "seed failed on its own schema" in upgrade(repo, seed_fails, database_url)[0]
    assert upgrade(repo, seeds_nothing, database_url) == [
        "the base branch's seed posted nothing, so the upgrade proves nothing."
    ]
    assert "base branch's migrations did not apply" in upgrade(repo, broken, database_url)[0]


def test_main_needs_a_database_and_dbmate(monkeypatch, capsys):
    monkeypatch.delenv("DATABASE_URL", raising=False)

    assert check_upgrade.main(["--base", "HEAD"]) == 1
    assert "needs DATABASE_URL and dbmate" in capsys.readouterr().out
