"""tools/sql_mutation.py: which mutants exist, how they run, and how they're scored."""

import json
import sys

import pytest

from tools import sql_mutation

LEDGER = """-- migrate:up
-- a comment with < and RAISE EXCEPTION that must not be mutated
CREATE FUNCTION money(x numeric) RETURNS numeric AS $$
BEGIN
    IF x < 0 THEN
        RAISE EXCEPTION 'x < 0 is refused';
    END IF;
    RETURN x + 1;
END;
$$ LANGUAGE plpgsql;
CREATE FUNCTION other() RETURNS int AS $$ BEGIN RETURN 1 + 1; END; $$ LANGUAGE plpgsql;
GRANT EXECUTE ON FUNCTION money(numeric) TO app;
"""
LATER = """-- migrate:up
CREATE CONSTRAINT TRIGGER ties_out
AFTER INSERT ON t
DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW EXECUTE FUNCTION money();
REVOKE ALL ON FUNCTION money(numeric) FROM PUBLIC;
"""
GUARDS = """-- migrate:up
CREATE TRIGGER guard
BEFORE UPDATE ON t
FOR EACH ROW WHEN (NEW.balance < 0)
EXECUTE FUNCTION money();
GRANT SELECT ON t TO app;
REVOKE ALL ON t FROM PUBLIC;
"""


@pytest.fixture
def files():
    return {
        "db/migrations/001_ledger_core.sql": LEDGER,
        "db/migrations/002_ledger_append_only.sql": GUARDS,
        "db/migrations/005_trust_account_tie_out.sql": LATER,
    }


def test_only_money_bodies_and_guard_statements_are_mutated(files):
    mutants = sql_mutation.generate(files, {"money"})
    described = {(m.file.split("/")[-1], m.description) for m in mutants}
    assert ("001_ledger_core.sql", "< to <=") in described
    assert ("001_ledger_core.sql", "RAISE EXCEPTION to NOTICE") in described
    assert ("001_ledger_core.sql", "+ to -") in described
    assert ("002_ledger_append_only.sql", "delete `CREATE TRIGGER guard`") in described
    assert ("002_ledger_append_only.sql", "delete `GRANT SELECT ON t TO app;`") in described
    assert ("002_ledger_append_only.sql", "< to <=") in described  # the trigger's WHEN clause
    # The other function, the comment and the string literal are left alone.
    ledger = [m for m in mutants if m.file.endswith("001_ledger_core.sql")]
    assert not any(m.line in (2, 11) for m in ledger)
    raise_line = [m for m in ledger if m.line == 6]
    assert [m.description for m in raise_line] == ["RAISE EXCEPTION to NOTICE"]
    assert [m.id for m in mutants[:2]] == ["m001", "m002"]


def test_guards_in_every_migration_we_wrote_are_mutated_but_not_upstream(files):
    described = {
        (m.file.split("/")[-1], m.description) for m in sql_mutation.generate(files, {"money"})
    }
    later = "005_trust_account_tie_out.sql"
    assert (later, "delete `CREATE CONSTRAINT TRIGGER ties_out`") in described
    assert (later, "delete `REVOKE ALL ON FUNCTION money(numeric) FROM PUBLIC;`") in described
    # pgledger's own file is copied verbatim from upstream: none of its statements are deleted.
    assert not any(
        file == "001_ledger_core.sql" and description.startswith("delete")
        for file, description in described
    )


def test_down_sections_are_not_mutated():
    reversible = """-- migrate:up
REVOKE EXECUTE ON FUNCTION money(numeric) FROM app;
CREATE FUNCTION money(x numeric) RETURNS numeric AS $$ BEGIN RETURN x + 1; END; $$;
-- migrate:down
GRANT EXECUTE ON FUNCTION money(numeric) TO app;
CREATE FUNCTION money(x numeric) RETURNS numeric AS $$ BEGIN RETURN x - 1; END; $$;
"""
    mutants = sql_mutation.generate({"db/migrations/007_keys.sql": reversible}, {"money"})

    assert [(m.line, m.description) for m in mutants] == [
        (2, "delete `REVOKE EXECUTE ON FUNCTION money(numeric) FROM app;`"),
        (3, "+ to -"),
        (3, "1 to 0"),
    ]


def test_apply_changes_one_place(files):
    mutant = next(m for m in sql_mutation.generate(files, {"money"}) if m.description == "< to <=")
    changed = sql_mutation.apply(files, mutant)[mutant.file]
    assert "IF x <= 0 THEN" in changed
    assert changed.count("<=") == 1
    assert sql_mutation.key(mutant) == "001_ledger_core.sql: < to <="


def test_shard_splits_evenly_and_checks_its_spec(files):
    mutants = sql_mutation.generate(files, {"money"})
    shards = [sql_mutation.shard(mutants, f"{i}/3") for i in (1, 2, 3)]
    assert sorted(m.id for s in shards for m in s) == sorted(m.id for m in mutants)
    with pytest.raises(ValueError, match="bad shard"):
        sql_mutation.shard(mutants, "4/3")


def test_files_round_trip_through_a_directory(tmp_path, files):
    target = sql_mutation.write_files(files, tmp_path)
    assert sql_mutation.read_files(target) == files
    assert sql_mutation.url_for("postgres://u@h:5432/a?sslmode=disable", "b") == (
        "postgres://u@h:5432/b?sslmode=disable"
    )


@pytest.mark.parametrize(
    "apply_code, test_code, outcome",
    [(1, 0, "invalid"), (0, 1, "killed"), (0, 0, "survived")],
)
def test_execute_classifies_each_mutant(files, apply_code, test_code, outcome):
    mutant = sql_mutation.generate(files, {"money"})[0]
    calls = []

    def runner(command, env, timeout):
        calls.append(command)
        if command[-1] == "up":
            assert "--no-dump-schema" in command  # never rewrite db/schema.sql
            return apply_code
        if command[-1] == "drop":
            return 0
        assert env["PMS_MIGRATIONS_DIR"].endswith("db/migrations")
        assert env["HYPOTHESIS_PROFILE"] == "mutation"
        return test_code

    result = sql_mutation.execute(
        mutant, files, "postgres://u@h/db", "dbmate", ["pytest"], runner=runner
    )
    assert result == outcome
    assert len(calls) == (2 if outcome == "invalid" else 3)


def test_run_command_reports_exit_codes_and_timeouts():
    assert sql_mutation.run_command([sys.executable, "-c", "raise SystemExit(3)"], {}, 30) == 3
    slow = [sys.executable, "-c", "import time; time.sleep(5)"]
    assert sql_mutation.run_command(slow, {}, 1) == -1


def results(*outcomes):
    return [
        {
            "id": f"m{i}",
            "file": "db/migrations/001.sql",
            "line": i,
            "description": "d",
            "outcome": outcome,
        }
        for i, outcome in enumerate(outcomes, start=1)
    ]


def test_score_leaves_out_invalid_and_equivalent():
    assert sql_mutation.score(results("killed", "survived", "invalid", "equivalent")) == (
        50.0,
        1,
        1,
        1,
    )
    assert sql_mutation.score([])[0] == 100.0


def test_report_lists_survivors_and_compares_with_the_baseline():
    text, ok = sql_mutation.report(results("killed", "survived", "equivalent"), 60.0)
    assert not ok
    assert "| m2 | db/migrations/001.sql:2 | d |" in text
    assert "1 equivalent mutant(s) skipped" in text
    assert "below the baseline 60.0%" in text

    text, ok = sql_mutation.report(results("killed", "killed"), 90.0)
    assert ok and "Raise [mutation] baseline_score" in text

    text, ok = sql_mutation.report(results("killed"), 100.0)
    assert ok and "Raise" not in text


def test_main_lists_scores_and_needs_a_database(tmp_path, monkeypatch, capsys):
    assert sql_mutation.main(["--list"]) == 0
    assert "mutants" in capsys.readouterr().out.splitlines()[-1]

    shard = tmp_path / "shard.json"
    shard.write_text(json.dumps(results("killed", "killed")))
    summary = tmp_path / "summary.md"
    thresholds = tmp_path / "t.toml"
    thresholds.write_text("[coverage.money]\nfunctions = []\n[mutation]\nbaseline_score = 100.0\n")
    assert (
        sql_mutation.main(
            ["--thresholds", str(thresholds), "--score", str(shard), "--summary", str(summary)]
        )
        == 0
    )
    assert "Score: **100.0%**" in summary.read_text()

    monkeypatch.delenv("DATABASE_URL", raising=False)
    assert sql_mutation.main(["--thresholds", str(thresholds), "--out", str(shard)]) == 1


def test_main_runs_chosen_mutants(tmp_path, monkeypatch, capsys):
    migrations = tmp_path / "db/migrations"
    migrations.mkdir(parents=True)
    (migrations / "001_ledger_core.sql").write_text(LEDGER)
    thresholds = tmp_path / "t.toml"
    thresholds.write_text(
        '[coverage.money]\nfunctions = ["money"]\n'
        '[mutation]\nbaseline_score = 0.0\nequivalent = ["001_ledger_core.sql: + to -"]\n'
    )
    monkeypatch.setenv("DATABASE_URL", "postgres://u@h/db")
    monkeypatch.setattr(sql_mutation.shutil, "which", lambda name: "/bin/dbmate")
    monkeypatch.setattr(sql_mutation, "execute", lambda *args: "killed")
    out = tmp_path / "out/check.json"
    mutants = sql_mutation.generate(sql_mutation.read_files(migrations), {"money"})
    plus = next(m.id for m in mutants if m.description == "+ to -")
    args = ["--migrations", str(migrations), "--thresholds", str(thresholds), "--out", str(out)]

    assert sql_mutation.main([*args, "--only", f"m001,{plus}"]) == 0
    outcomes = {r["id"]: r["outcome"] for r in json.loads(out.read_text())}
    assert outcomes == {"m001": "killed", plus: "equivalent"}

    assert sql_mutation.main(args) == 0
    assert len(json.loads(out.read_text())) == len(mutants)
