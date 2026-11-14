"""tools/schema_dump.py: a dump without what pg_dump writes when the schema didn't change.

From 2026-11-13 CI's postgres:16 service is 16.16, whose dumps carry each column's attver, an
internal number that changes whenever its table is made again. db.yml's down/up check failed on
every PR with 500 lines of nothing but those, and the nightly's backup checks with it.
"""

import os
import stat
import subprocess
import sys
from pathlib import Path

from tools import check_backup, check_rollback, schema_dump

ROOT = Path(__file__).resolve().parents[2]

DUMP = """--
-- PostgreSQL database dump
--

\\restrict 6F0yXwDSuKmMjSOp4CO3w1aA0hdHfGFCmmvRXyU7bvOWhHH

-- Dumped from database version 16.16 (Debian 16.16-1.pgdg13+1)
-- Dumped by pg_dump version 16.16 (Ubuntu 16.16-0ubuntu0.24.04.1)

CREATE TABLE public.trust_notes (
    id uuid NOT NULL,
    body text NOT NULL
);

ALTER TABLE ONLY public.trust_notes ALTER COLUMN id SET (pg_attver=39571152);
ALTER TABLE ONLY public.trust_notes ALTER COLUMN body SET (n_distinct=5, pg_attver=39571152);
ALTER TABLE ONLY public.trust_notes ALTER COLUMN body SET (pg_attver=39571152, n_distinct=-1);

\\unrestrict 6F0yXwDSuKmMjSOp4CO3w1aA0hdHfGFCmmvRXyU7bvOWhHH
"""

SAME_SCHEMA_ANOTHER_DAY = (
    DUMP.replace("39571152", "42285224")
    .replace("6F0yXwDSuKmMjSOp4CO3w1aA0hdHfGFCmmvRXyU7bvOWhHH", "Zq8Rv1aPq2W3e4R5t6Y7u8I9o0P")
    .replace("16.16 (Debian 16.16-1.pgdg13+1)", "16.15 (Ubuntu 16.15-0ubuntu0.24.04.1)")
)


def test_what_changes_when_the_schema_doesnt_is_left_out():
    assert schema_dump.normal(DUMP) == schema_dump.normal(SAME_SCHEMA_ANOTHER_DAY)
    kept = schema_dump.normal(DUMP)
    assert "pg_attver" not in kept
    assert "restrict" not in kept
    assert "Dumped from" not in kept and "Dumped by" not in kept


def test_everything_else_is_kept_as_dumped():
    kept = schema_dump.normal(DUMP)
    assert (
        "CREATE TABLE public.trust_notes (\n    id uuid NOT NULL,\n    body text NOT NULL\n);"
        in kept
    )
    # A column's other options stay; only its attver goes.
    assert "ALTER TABLE ONLY public.trust_notes ALTER COLUMN body SET (n_distinct=5);" in kept
    assert "ALTER TABLE ONLY public.trust_notes ALTER COLUMN body SET (n_distinct=-1);" in kept
    # A real difference still differs.
    changed = DUMP.replace("body text NOT NULL", "body text")
    assert schema_dump.normal(changed) != schema_dump.normal(DUMP)


def test_dbmates_own_fixed_restrict_key_is_kept():
    # dbmate writes "\\restrict dbmate" itself, the same every time; db/schema.sql has it.
    fixed = "\\restrict dbmate\n\nSET statement_timeout = 0;\n\n\\unrestrict dbmate\n"
    assert schema_dump.normal(fixed) == fixed


def test_the_command_rewrites_a_schema_file_in_place(tmp_path):
    path = tmp_path / "schema.sql"
    path.write_text(DUMP)
    assert schema_dump.main([str(path)]) == 0
    assert path.read_text() == schema_dump.normal(DUMP)
    assert schema_dump.main([]) == 2


def test_the_checks_and_db_schema_sql_read_dumps_through_it(tmp_path, monkeypatch):
    # The down/up check: a dbmate whose dump carries the stamps reads without them.
    fake = tmp_path / "dbmate"
    fake.write_text(
        "#!/bin/sh\n"
        'while [ "$1" != dump ]; do [ "$1" = --schema-file ] && out="$2"; shift; done\n'
        f"cat > \"$out\" <<'EOF'\n{DUMP}EOF\n"
    )
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    dbmate = check_rollback.Dbmate(str(fake), tmp_path, "postgres://unused")
    assert dbmate.schema() == schema_dump.normal(DUMP)
    # The backup check: pg_dump's own output, the same way.
    pg_dump = tmp_path / "pg_dump"
    pg_dump.write_text(f"#!/bin/sh\ncat <<'EOF'\n{DUMP}EOF\n")
    pg_dump.chmod(pg_dump.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("PATH", f"{tmp_path}{os.pathsep}{os.environ['PATH']}")
    assert check_backup.schema("postgres://u@localhost/x") == schema_dump.normal(DUMP).splitlines()
    # db/schema.sql: scripts/dump_schema.sh writes it through the same command.
    script = (ROOT / "scripts/dump_schema.sh").read_text()
    assert "python -m tools.schema_dump db/schema.sql" in script.splitlines()
    assert "pg_attver" not in (ROOT / "db/schema.sql").read_text()


def test_the_dump_script_runs_with_the_normalizer(tmp_path):
    # The script itself, against a copy of the repository's layout and a dbmate that writes the
    # dump with every stamp: what lands in db/schema.sql has none of them.
    for part in ("scripts", "tools"):
        (tmp_path / part).mkdir()
    (tmp_path / "db").mkdir()
    (tmp_path / "scripts/dump_schema.sh").write_text((ROOT / "scripts/dump_schema.sh").read_text())
    (tmp_path / "tools/__init__.py").write_text("")
    (tmp_path / "tools/schema_dump.py").write_text((ROOT / "tools/schema_dump.py").read_text())
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake = bin_dir / "dbmate"
    fake.write_text(f"#!/bin/sh\ncat > db/schema.sql <<'EOF'\n{DUMP}EOF\n")
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    python = bin_dir / "python"
    python.symlink_to(Path(sys.executable))
    subprocess.run(
        ["sh", str(tmp_path / "scripts/dump_schema.sh")],
        check=True,
        env={**os.environ, "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}"},
    )
    assert (tmp_path / "db/schema.sql").read_text() == schema_dump.normal(DUMP)
