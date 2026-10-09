"""tools/golden.py: normalizing, rendering, running a case and comparing it."""

from decimal import Decimal

from tools import golden


def test_normalize_gives_ids_stable_names_in_order():
    text = (
        "pgla_01M4GK49GDF21TEJPXV7X29X32 pglt_01M4GK49JEFFFTPQ7C8S8H793P "
        "pgla_01M4GK49GDF21TEJPXV7X29X32 3f2504e0-4f89-11d3-9a0c-0305e82c3301"
    )
    assert golden.normalize(text) == "<id1> <id2> <id1> <uuid1>"


def test_render_is_tab_separated_with_a_header():
    rendered = golden.render(["name", "amount", "note"], [("a", Decimal("1.50"), None)])
    assert rendered == "name\tamount\tnote\na\t1.50\tNULL\n"
    assert golden.cell(Decimal("1E+2")) == "100"


def test_statements_split_on_line_ending_semicolons():
    sql = "CREATE TEMP TABLE t (x int);\nINSERT INTO t VALUES (1);\n\nSELECT x FROM t;\n"
    assert golden.statements(sql) == [
        "CREATE TEMP TABLE t (x int)",
        "INSERT INTO t VALUES (1)",
        "SELECT x FROM t",
    ]


def test_run_case_rolls_back_its_fixture(conn, tmp_path):
    case = tmp_path / "trust_report_example/basic.sql"
    case.parent.mkdir()
    case.write_text(
        "INSERT INTO trust_pmcs (display_name) VALUES ('Golden PMC');\n"
        "SELECT display_name, pmc_id FROM trust_pmcs WHERE display_name = 'Golden PMC';\n"
    )
    assert golden.run_case(conn, case) == "display_name\tpmc_id\nGolden PMC\t<uuid1>\n"
    left = conn.execute("SELECT count(*) FROM trust_pmcs WHERE display_name = 'Golden PMC'")
    assert left.fetchone()[0] == 0


def test_compare_matches_diffs_and_updates(tmp_path):
    case = tmp_path / "trust_report_example/basic.sql"
    case.parent.mkdir()
    case.write_text("SELECT 1;\n")
    diff = golden.compare(case, "a\n", update=False)
    assert diff is not None and "+a" in diff

    assert golden.compare(case, "a\n", update=True) is None
    assert golden.expected_path(case).read_text() == "a\n"
    assert golden.compare(case, "a\n", update=False) is None
    assert "-a" in golden.compare(case, "b\n", update=False)


def test_report_discovery_and_missing_cases(conn, tmp_path):
    assert golden.report_functions(conn) == []
    (tmp_path / "trust_report_a").mkdir()
    (tmp_path / "trust_report_a/basic.sql").write_text("SELECT 1;\n")
    assert golden.cases(tmp_path) == [tmp_path / "trust_report_a/basic.sql"]
    assert golden.missing_cases(["trust_report_a", "trust_report_b"], tmp_path) == [
        "trust_report_b"
    ]
