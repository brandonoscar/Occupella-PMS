"""tools/sql_coverage.py: branch counting, merging runs, line mapping and the Cobertura report."""

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


def test_branch_hits_follow_plpgsql_check():
    statements = [
        stmt(0, "statement block", hits=5),
        stmt(1, "IF", 0, "body", hits=5),  # only THEN: then + the ELSE it doesn't write
        stmt(2, "RAISE", 1, "then body", hits=2),
        stmt(3, "IF", 0, "body", hits=3),  # IF / ELSIF / ELSE: 3
        stmt(4, "RETURN", 3, "then body", hits=1),
        stmt(5, "RETURN", 3, "elsif 1", hits=0),
        stmt(6, "RETURN", 3, "else body", hits=2),
        stmt(7, "CASE", 0, "body", hits=1),  # CASE with two WHENs: 2 + its ELSE, unwritten
        stmt(8, "RETURN", 7, "case when 1", hits=1),
        stmt(9, "RETURN", 7, "case when 2", hits=0),
        stmt(10, "FOREACH over array", 0, "body", hits=1),  # a loop: its body, from its first
        stmt(11, "assignment", 10, "loop body", hits=4),  # statement
        stmt(12, "RETURN", 10, "loop body", hits=0),
        stmt(13, "IF", 0, "body", hits=1),  # IF whose arms never appear still counts 2
        stmt(14, "statement block", 0, "body", hits=2),  # a block with an EXCEPTION clause:
        stmt(15, "SQL statement", 14, "body", hits=2),  # its body and each handler
        stmt(16, "RAISE", 14, "exception 1", hits=1),
        stmt(17, "RETURN", 14, "exception 2", hits=0),
        stmt(18, "CASE", 0, "body", hits=1),  # a written ELSE is the CASE's last branch
        stmt(19, "RETURN", 18, "case when 1", hits=0),
        stmt(20, "RETURN", 18, "case else", hits=1),
    ]
    assert sql_coverage.branch_hits(statements) == [
        *[2, 3],
        *[1, 0, 2],
        *[1, 0, 0],
        *[4],
        *[0, 1],
        *[2, 1, 0],
        *[0, 1],
    ]


def test_a_branch_is_taken_as_often_as_its_first_statement_runs():
    # An arm's later statements can run less often than its first (after a RETURN, say).
    statements = [
        stmt(1, "IF", hits=4),
        stmt(3, "RAISE", 1, "then body", hits=0),
        stmt(2, "IF", 1, "then body", hits=3),
        stmt(4, "RETURN", 2, "then body", hits=3),
    ]
    assert sql_coverage.branch_hits(statements) == [3, 1, 3, 0]


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
    # Without the profiler's ratio (a merged run), the statements alone decide.
    assert sql_coverage.summarize({**function, "branch_ratio": None}) == summary


@pytest.mark.parametrize("ratio", [0.3, 1.0])
def test_summarize_refuses_a_ratio_the_statements_do_not_give(ratio):
    function = {"name": "f", "args": "", "branch_ratio": ratio, "statements": [stmt(1, "IF")]}
    with pytest.raises(ValueError, match="update branch_hits"):
        sql_coverage.summarize(function)


def guarded(ran, raised, ratio):
    """IF ... THEN RAISE; END IF; RETURN: the IF ran `ran` times and raised `raised` times."""
    return {
        "name": "guard",
        "args": "p integer",
        "source": "body",
        "branch_ratio": ratio,
        "statements": [
            stmt(0, "statement block", lineno=1, hits=ran),
            stmt(1, "IF", 0, "body", lineno=2, hits=ran),
            stmt(2, "RAISE", 1, "then body", lineno=3, hits=raised),
            stmt(3, "RETURN", 0, "body", lineno=4, hits=ran - raised),
        ],
    }


def test_merge_adds_the_runs_up():
    # One run only raised, the other never did: neither took both branches, together they did.
    first, second = guarded(2, 2, 0.5), guarded(3, 0, 0.5)
    other = {**guarded(1, 0, 0.5), "name": "only_in_one_run"}
    merged = sql_coverage.merge([[first], [second, other]])

    assert [f["name"] for f in merged] == ["guard", "only_in_one_run"]
    assert [s["exec_stmts"] for s in merged[0]["statements"]] == [5, 5, 2, 3]
    assert merged[0]["branch_ratio"] is None
    summary = sql_coverage.summarize(merged[0])
    assert (summary.statements_hit, summary.branches_hit, summary.branches) == (4, 2, 2)
    # The runs themselves are left as they were.
    assert [s["exec_stmts"] for s in first["statements"]] == [2, 2, 2, 0]


def test_merge_checks_each_run_against_the_profiler():
    with pytest.raises(ValueError, match="update branch_hits"):
        sql_coverage.merge([[guarded(2, 2, 0.5)], [guarded(3, 0, 1.0)]])


def test_merge_refuses_a_function_that_differs_between_runs():
    moved = guarded(3, 0, 0.5)
    moved["statements"][3]["lineno"] = 5
    for other in ({**guarded(3, 0, 0.5), "source": "another body"}, moved):
        with pytest.raises(ValueError, match=r"guard\(p integer\) differs between the runs"):
            sql_coverage.merge([[guarded(2, 2, 0.5)], [other]])


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
