"""tools/sql_coverage.py: branch counting, line mapping and the Cobertura report."""

from xml.etree import ElementTree

import pytest

from tools import sql_coverage


def stmt(stmtid, name, parent=None, note=None, lineno=1, hits=1):
    return {
        "stmtid": stmtid,
        "parent_stmtid": parent,
        "parent_note": note,
        "lineno": lineno,
        "exec_stmts": hits,
        "stmtname": name,
    }


def test_branch_count_follows_plpgsql_check():
    statements = [
        stmt(0, "statement block"),
        stmt(1, "IF", 0, "body"),  # IF with only THEN: then + implicit else
        stmt(2, "RAISE", 1, "then body"),
        stmt(3, "IF", 0, "body"),  # IF / ELSIF / ELSE: 3
        stmt(4, "RETURN", 3, "then body"),
        stmt(5, "RETURN", 3, "elsif body"),
        stmt(6, "RETURN", 3, "else body"),
        stmt(7, "CASE", 0, "body"),  # CASE with two WHENs: 2 + else
        stmt(8, "RETURN", 7, "case when 1"),
        stmt(9, "RETURN", 7, "case when 2"),
        stmt(10, "FOREACH over array", 0, "body"),  # a loop is one branch
        stmt(11, "IF", 0, "body"),  # IF whose arms never appear still counts 2
    ]
    assert sql_coverage.branch_count(statements) == 2 + 3 + 3 + 1 + 2


def test_summarize_counts_statements_and_branches():
    function = {
        "name": "f",
        "args": "",
        "branch_ratio": 0.5,
        "statements": [
            stmt(0, "statement block"),
            stmt(1, "IF", 0),
            stmt(2, "RAISE", 1, "then body", hits=0),
        ],
    }
    summary = sql_coverage.summarize(function)
    assert (summary.statements, summary.statements_hit) == (3, 2)
    assert (summary.branches, summary.branches_hit) == (2, 1)


def test_summarize_refuses_a_ratio_that_does_not_fit():
    function = {"name": "f", "args": "", "branch_ratio": 0.3, "statements": [stmt(1, "IF")]}
    with pytest.raises(ValueError, match="update branch_count"):
        sql_coverage.summarize(function)


def test_percent_of_nothing_is_full():
    assert sql_coverage.percent(0, 0) == 100.0
    assert sql_coverage.percent(1, 4) == 25.0


def test_locate_prefers_the_newest_migration():
    body = "\nBEGIN\n  RETURN 1;\nEND;\n"
    files = {
        "db/migrations/001_a.sql": f"x\nCREATE FUNCTION f() AS $${body}$$;\n",
        "db/migrations/002_b.sql": f"\n\nCREATE OR REPLACE FUNCTION f() AS $${body}$$;\n",
    }
    assert sql_coverage.locate(body, files) == ("db/migrations/002_b.sql", 3)
    assert sql_coverage.locate("missing", files) is None


def test_line_hits_and_cobertura_point_into_the_migration():
    body = "\nBEGIN\n  RAISE 'x';\n  RETURN 1;\nEND;\n"
    files = {"db/migrations/001_a.sql": f"-- header\nCREATE FUNCTION f() AS $${body}$$;\n"}
    function = {
        "source": body,
        "statements": [
            stmt(0, "statement block", lineno=2, hits=3),
            stmt(1, "RAISE", 0, "body", lineno=3, hits=0),
            stmt(2, "RETURN", 0, "body", lineno=4, hits=3),
        ],
    }
    assert sql_coverage.line_hits(function, files) == (
        "db/migrations/001_a.sql",
        {3: 3, 4: 0, 5: 3},
    )
    unplaced = {**function, "source": "not in any file"}
    assert sql_coverage.line_hits(unplaced, files) is None

    root = ElementTree.fromstring(sql_coverage.cobertura([function, unplaced], files))
    lines = {line.get("number"): line.get("hits") for line in root.iter("line")}
    assert lines == {"3": "3", "4": "0", "5": "3"}
    assert root.find(".//class").get("filename") == "db/migrations/001_a.sql"
    assert root.get("line-rate") == "0.6667"


def test_read_migrations_keys_paths_from_the_repo_root(tmp_path):
    migrations = tmp_path / "db/migrations"
    migrations.mkdir(parents=True)
    (migrations / "001_a.sql").write_text("SELECT 1;\n")
    assert sql_coverage.read_migrations(migrations, tmp_path) == {
        "db/migrations/001_a.sql": "SELECT 1;\n"
    }


class FakeConnection:
    """Answers SHOW shared_preload_libraries and the extension-version query."""

    def __init__(self, preload, version="2.7"):
        self.answers = {"SHOW": (preload,), "extversion": (version,) if version else None}
        self.statements = []
        self.last = None

    def execute(self, statement, *args):
        self.statements.append(statement)
        self.last = str(statement)
        return self

    def fetchone(self):
        for key, answer in self.answers.items():
            if key in self.last:
                return answer
        return None


def test_start_needs_the_profiler_preloaded():
    with pytest.raises(RuntimeError, match="shared_preload_libraries"):
        sql_coverage.start(FakeConnection(""), "db")


@pytest.mark.parametrize("version", ["2.10", None])
def test_start_refuses_another_extension_version(version):
    with pytest.raises(RuntimeError, match="needs plpgsql_check 2.7"):
        sql_coverage.start(FakeConnection("plpgsql_check", version), "db")


def test_start_turns_the_profiler_on_for_the_database():
    connection = FakeConnection("plpgsql_check")
    sql_coverage.start(connection, "pms_test_x")
    assert len(connection.statements) == 5
