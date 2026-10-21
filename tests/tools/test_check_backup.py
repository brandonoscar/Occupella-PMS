"""scripts/backup.sh and tools/check_backup.py: a backup of the books restores whole, into a new
empty database only, on a server that has never seen the PMS's roles."""

import shutil
import subprocess
import uuid
from datetime import date
from pathlib import Path

import psycopg
import pytest
from psycopg import sql

from selfhost import smoke
from tools import check_backup
from tools.check_rollback import Dbmate
from tools.scratch_db import scratch_database

ROOT = Path(__file__).resolve().parents[2]


def script(command, archive, url, stdin=None):
    return subprocess.run(
        [str(ROOT / "scripts/backup.sh"), command, str(archive)],
        env=check_backup.libpq_env(url),
        input=stdin,
        capture_output=True,
    )


def migrated(url):
    built = Dbmate(shutil.which("dbmate"), ROOT / "db/migrations", url).run(
        "--no-dump-schema", "up"
    )
    assert built.returncode == 0, built.stderr


def seed(url):
    with psycopg.connect(url, autocommit=True) as conn:
        seeded = smoke.seed(conn, doors=4, owners=2)
        smoke.post_month_of_rent(conn, seeded, date(2026, 2, 1))


def unbalance(url):
    """One account's balance one cent off its entries, past the triggers that would refuse it."""
    with psycopg.connect(url, autocommit=True) as conn:
        conn.execute("SET session_replication_role = replica")
        conn.execute(
            "UPDATE pgledger_accounts SET balance = balance + 0.01"
            " WHERE id = (SELECT ledger_account_id FROM trust_ledger_accounts"
            "             WHERE kind = 'owner_property' LIMIT 1)"
        )


@pytest.fixture(scope="module")
def archive(database_url, tmp_path_factory):
    """A dump of books holding money: four doors, their deposits and a month of rent."""
    path = tmp_path_factory.mktemp("backup") / "pms.dump"
    with scratch_database(database_url, "pms_backup_test") as url:
        migrated(url)
        seed(url)
        dumped = script("dump", path, url)
    assert dumped.returncode == 0, dumped.stderr
    return path


def test_the_whole_check_passes_on_this_branch(database_url, monkeypatch, capsys):
    # The same run as db.yml's step: migrations, the self-host seed and the demo, dumped,
    # restored and compared.
    monkeypatch.setenv("DATABASE_URL", database_url)

    assert check_backup.main([]) == 0

    out = capsys.readouterr().out
    assert "the ledger ties out" in out
    assert "ok: the backup restores into an empty database" in out


def test_a_dump_is_private_and_reads_back(archive):
    assert archive.stat().st_mode & 0o777 == 0o600
    assert not archive.with_suffix(".dump.partial").exists()
    assert archive.read_bytes().startswith(b"PGDMP")


def test_a_dump_piped_out_restores_from_a_pipe(archive, database_url):
    with scratch_database(database_url, "pms_restore_test") as url:
        restored = script("restore", "-", url, stdin=archive.read_bytes())

        assert restored.returncode == 0, restored.stderr
        assert b"8 transfers" in restored.stderr
        assert b"the ledger ties out" in restored.stderr
        with psycopg.connect(url) as conn:
            assert conn.execute("SELECT count(*) FROM trust_tenants").fetchone() == (4,)


def test_a_dump_to_standard_output_is_the_same_archive(database_url, tmp_path):
    with scratch_database(database_url, "pms_backup_test") as url:
        migrated(url)
        piped = script("dump", "-", url)
        (tmp_path / "piped.dump").write_bytes(piped.stdout)

        assert piped.returncode == 0, piped.stderr
        listed = subprocess.run(
            ["pg_restore", "--list", str(tmp_path / "piped.dump")], capture_output=True, text=True
        )
        assert listed.returncode == 0, listed.stderr
        assert "TABLE DATA public pgledger_transfers" in listed.stdout


def test_a_restore_never_writes_over_a_database_that_holds_anything(archive, database_url):
    with scratch_database(database_url, "pms_restore_test") as url:
        with psycopg.connect(url, autocommit=True) as conn:
            conn.execute("CREATE TABLE running_books (id int)")

        refused = script("restore", archive, url)

        assert refused.returncode == 1
        assert b"is not empty" in refused.stderr
        with psycopg.connect(url) as conn:
            assert conn.execute("SELECT to_regclass('trust_pmcs')").fetchone() == (None,)


def test_a_restore_makes_the_roles_the_books_grant_to_when_the_server_lacks_them(
    database_url, tmp_path
):
    # trust_app and trust_ai_agent live in the server, not the database: on a new server they
    # don't exist. A role made for this test stands in for them, dropped before the restore.
    role = f"pms_backup_role_{uuid.uuid4().hex[:8]}"
    path = tmp_path / "granted.dump"
    with psycopg.connect(database_url, autocommit=True) as conn:
        conn.execute(sql.SQL("CREATE ROLE {} NOLOGIN").format(sql.Identifier(role)))
    try:
        with scratch_database(database_url, "pms_backup_test") as url:
            migrated(url)
            with psycopg.connect(url, autocommit=True) as conn:
                conn.execute(
                    sql.SQL("GRANT SELECT ON trust_pmcs TO {}").format(sql.Identifier(role))
                )
            assert script("dump", path, url).returncode == 0
        with psycopg.connect(database_url, autocommit=True) as conn:
            conn.execute(sql.SQL("DROP ROLE {}").format(sql.Identifier(role)))

        with scratch_database(database_url, "pms_restore_test") as url:
            restored = script("restore", path, url)

            assert restored.returncode == 0, restored.stderr
            with psycopg.connect(url) as conn:
                assert conn.execute(
                    "SELECT rolcanlogin, has_table_privilege(rolname, 'trust_pmcs', 'SELECT')"
                    " FROM pg_roles WHERE rolname = %s",
                    (role,),
                ).fetchone() == (False, True)
    finally:
        with psycopg.connect(database_url, autocommit=True) as conn:
            conn.execute(sql.SQL("DROP ROLE IF EXISTS {}").format(sql.Identifier(role)))


def test_a_restore_whose_ledger_does_not_tie_out_fails(database_url, tmp_path):
    path = tmp_path / "unbalanced.dump"
    with scratch_database(database_url, "pms_backup_test") as url:
        migrated(url)
        seed(url)
        unbalance(url)
        assert script("dump", path, url).returncode == 0

    with scratch_database(database_url, "pms_restore_test") as url:
        restored = script("restore", path, url)

    assert restored.returncode == 1
    assert b"doesn't tie out" in restored.stderr
    assert b"accounts whose balance isn't the sum of their entries" in restored.stderr


def test_the_script_takes_two_arguments():
    wrong = subprocess.run(
        [str(ROOT / "scripts/backup.sh"), "copy", "x"], capture_output=True, text=True
    )

    assert wrong.returncode == 2
    assert "usage:" in wrong.stderr


def quick(monkeypatch):
    """The check on the self-host seed alone, for the cases that test how it fails."""
    monkeypatch.setattr(check_backup, "fill", seed)


def test_the_check_catches_a_restore_that_loses_rows(database_url, monkeypatch):
    quick(monkeypatch)
    real = check_backup.backup

    def losing(root, command, archive, url):
        ran = real(root, command, archive, url)
        if command == "restore":
            with psycopg.connect(url, autocommit=True) as conn:
                conn.execute("SET session_replication_role = replica")
                conn.execute("DELETE FROM trust_tenants WHERE display_name = 'Tenant 01'")
        return ran

    monkeypatch.setattr(check_backup, "backup", losing)

    assert check_backup.check(ROOT, database_url, shutil.which("dbmate")) == [
        "public.trust_tenants: 4 rows in the original, 3 restored."
    ]


def test_the_check_refuses_books_that_do_not_tie_out_to_begin_with(database_url, monkeypatch):
    monkeypatch.setattr(check_backup, "fill", lambda url: (seed(url), unbalance(url)))

    assert check_backup.check(ROOT, database_url, shutil.which("dbmate")) == [
        "the original's ledger doesn't tie out, so the restore proves nothing."
    ]


def test_the_check_reports_the_script_failing(database_url, monkeypatch, tmp_path):
    quick(monkeypatch)
    failing = tmp_path / "scripts/backup.sh"
    failing.parent.mkdir()
    failing.write_text("#!/bin/sh\necho 'FAIL: no space left' >&2\nexit 1\n")
    failing.chmod(0o755)
    monkeypatch.setattr(check_backup, "SCRIPT", str(failing))

    problems = check_backup.check(ROOT, database_url, shutil.which("dbmate"))

    assert problems == [f"{failing} dump failed: FAIL: no space left"]


def test_the_check_reports_migrations_that_do_not_apply(database_url, tmp_path):
    (tmp_path / "db/migrations").mkdir(parents=True)
    (tmp_path / "db/migrations/20990101000000_broken.sql").write_text(
        "-- migrate:up\nSELECT no_such_function();\n-- migrate:down\n"
    )

    problems = check_backup.check(tmp_path, database_url, shutil.which("dbmate"))

    assert len(problems) == 1
    assert problems[0].startswith("the migrations did not apply")


def test_main_needs_a_database_and_the_tools(monkeypatch, capsys):
    monkeypatch.delenv("DATABASE_URL", raising=False)

    assert check_backup.main([]) == 1
    assert "needs DATABASE_URL" in capsys.readouterr().out


def test_main_prints_each_problem(database_url, monkeypatch, capsys):
    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.setattr(check_backup, "check", lambda root, url, dbmate: ["a row went missing."])

    assert check_backup.main([]) == 1
    assert "FAIL: a row went missing." in capsys.readouterr().out


def test_the_connection_goes_through_libpq_variables():
    env = check_backup.libpq_env(
        "postgres://pms_owner:synthetic@db.example.invalid:6543/books?sslmode=require"
    )

    assert {key: env[key] for key in env if key.startswith("PG")} == {
        "PGHOST": "db.example.invalid",
        "PGPORT": "6543",
        "PGUSER": "pms_owner",
        "PGPASSWORD": "synthetic",
        "PGDATABASE": "books",
        "PGSSLMODE": "require",
    }
    with pytest.raises(ValueError, match="options"):
        check_backup.libpq_env("postgres://h/db?options=-c%20search_path%3Dx")


def test_a_check_constraint_whose_ands_came_back_flattened_is_the_same():
    before = (
        "    CONSTRAINT c_check CHECK ((((length(d) >= 5) AND (length(d) <= 9)) AND (d <> '')))"
    )
    after = "    CONSTRAINT c_check CHECK (((length(d) >= 5) AND (length(d) <= 9) AND (d <> '')))"

    assert check_backup.schema_differences(["a", before], ["a", after]) == []


def test_any_other_schema_difference_counts():
    assert check_backup.schema_differences(
        ["GRANT SELECT ON TABLE public.t TO trust_app;"],
        ["GRANT ALL ON TABLE public.t TO trust_app;"],
    ) == [
        "line 1: 'GRANT SELECT ON TABLE public.t TO trust_app;'"
        " came back 'GRANT ALL ON TABLE public.t TO trust_app;'"
    ]
    # Parentheses dropped outside a CHECK constraint are a difference.
    assert len(check_backup.schema_differences(["SELECT (a)"], ["SELECT a"])) == 1
    assert check_backup.schema_differences(["a", "b"], ["a"]) == [
        "2 lines in the original's schema, 1 restored"
    ]


def test_compare_names_each_table_missing_extra_or_changed():
    original = {"public.a": (1, "x"), "public.b": (2, "y"), "public.c": (3, "z")}
    restored = {"public.b": (2, "changed"), "public.c": (3, "z"), "public.d": (0, "")}

    assert check_backup.compare(original, restored) == [
        "public.a is missing from the restored copy.",
        "public.b: the rows restored differ from the original's.",
        "public.d is in the restored copy but not the original.",
    ]
