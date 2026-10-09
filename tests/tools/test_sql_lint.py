"""tools/sql_lint.py: which functions get linted, and how findings are reported.

These use a fake connection, because only the coverage job's Postgres has plpgsql_check. The
coverage job then runs the lint for real on every migration (coverage.yml).
"""

import shutil

from tools import sql_lint

OURS = (11, "trust_pay(uuid)", 0, "", True)
TRIGGER = (12, "trust_guard()", 501, "pgledger_entries", True)
UPSTREAM = (13, "parse_ulid(text)", 0, "", False)


class FakeConnection:
    def __init__(self, targets, findings=None):
        self.targets = targets
        self.findings = findings or {}
        self.executed = []

    def execute(self, query, params=()):
        self.executed.append(query)
        if query == sql_lint.TARGETS:
            self.rows = self.targets
        elif query == sql_lint.FINDINGS:
            self.rows = self.findings.get(params, [])
        else:
            self.rows = []
        return self

    def fetchall(self):
        return self.rows


def test_targets_carry_the_trigger_table():
    found = sql_lint.targets(FakeConnection([OURS, TRIGGER]))
    assert [target.label for target in found] == [
        "trust_pay(uuid)",
        "trust_guard() on pgledger_entries",
    ]


def test_findings_name_the_function_line_and_hint():
    conn = FakeConnection(
        [],
        {
            (11, 0): [
                ("error", "42703", 8, 'column "balanse" does not exist', "Perhaps balance."),
                ("performance", "00000", None, "should be STABLE", None),
            ]
        },
    )
    target = sql_lint.Target(*OURS)
    assert sql_lint.findings(conn, target) == [
        'trust_pay(uuid) line 8: error 42703: column "balanse" does not exist (Perhaps balance.)',
        "trust_pay(uuid): performance 00000: should be STABLE",
    ]


def test_lint_checks_ours_and_lists_upstream(capsys):
    conn = FakeConnection(
        [OURS, TRIGGER, UPSTREAM],
        {(12, 501): [("warning", "00000", 4, 'unused variable "x"', None)]},
    )

    problems = sql_lint.lint(conn)

    assert conn.executed[0] == "CREATE EXTENSION IF NOT EXISTS plpgsql_check"
    assert problems == [
        'trust_guard() on pgledger_entries line 4: warning 00000: unused variable "x"'
    ]
    out = capsys.readouterr().out
    assert "linted trust_pay(uuid)\n" in out
    assert "not linted, upstream and verbatim: parse_ulid(text)" in out


def test_lint_refuses_to_pass_when_it_found_nothing_to_check():
    problems = sql_lint.lint(FakeConnection([UPSTREAM]))
    assert problems == [
        "found no trust_* PL/pgSQL functions to lint; the query or the schema is wrong."
    ]


def test_main_needs_a_database_and_dbmate(monkeypatch, capsys):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    assert sql_lint.main([]) == 1
    assert "needs DATABASE_URL and dbmate" in capsys.readouterr().out


def test_main_reports_migrations_that_do_not_apply(monkeypatch, database_url, tmp_path, capsys):
    assert shutil.which("dbmate")
    (tmp_path / "20270101000001_a.sql").write_text("-- migrate:up\nSELEC 1;\n")
    monkeypatch.setenv("DATABASE_URL", database_url)

    assert sql_lint.main(["--migrations", str(tmp_path)]) == 1
    assert "migrations did not apply" in capsys.readouterr().out
