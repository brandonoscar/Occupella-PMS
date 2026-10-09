"""tools/check_pr_rules.py: the standing rule, checked on a PR's diff."""

from tools import check_pr_rules

FUNCTION = "CREATE OR REPLACE FUNCTION trust_x() RETURNS trigger AS $$ BEGIN RETURN NEW; END $$;"


def test_code_without_tests_fails():
    problems = check_pr_rules.check({"tools/a.py": "M"}, {})
    assert problems == ["Code changed but no test did: tools/a.py. Add the tests that cover it."]


def test_code_with_tests_passes():
    assert check_pr_rules.check({"tools/a.py": "M", "tests/tools/test_a.py": "M"}, {}) == []


def test_docs_and_config_need_no_tests():
    files = {"README.md": "M", "requirements-dev.txt": "M", ".github/workflows/ci.yml": "M"}
    assert check_pr_rules.check(files, {}) == []


def test_dockerfile_and_compose_count_as_code():
    problems = check_pr_rules.check({"Dockerfile": "M", "compose.yaml": "A"}, {})
    assert "Dockerfile" in problems[0] and "compose.yaml" in problems[0]


def test_ledger_migration_needs_a_property_test():
    path = "db/migrations/20270101000001_x.sql"
    files = {path: "A", "tests/test_x.py": "A"}
    problems = check_pr_rules.check(files, {path: [FUNCTION]})
    assert len(problems) == 1 and "tests/properties/" in problems[0]

    files["tests/properties/test_x.py"] = "M"
    assert check_pr_rules.check(files, {path: [FUNCTION]}) == []


def test_altering_a_ledger_table_is_ledger_logic():
    path = "db/migrations/20270101000001_x.sql"
    files = {path: "A", "tests/test_x.py": "A"}
    added = {path: ["ALTER TABLE pgledger_transfers ADD COLUMN note text;"]}
    assert "tests/properties/" in check_pr_rules.check(files, added)[0]


def test_plain_migration_needs_only_tests():
    path = "db/migrations/20270101000001_x.sql"
    files = {path: "A", "tests/test_x.py": "A"}
    assert check_pr_rules.check(files, {path: ["CREATE TABLE trust_notes (id uuid);"]}) == []


def test_new_report_needs_a_golden_file():
    path = "db/migrations/20270101000001_report.sql"
    added = {path: ["CREATE FUNCTION trust_report_rent_roll(p date) RETURNS TABLE (x int)"]}
    files = {path: "A", "tests/test_x.py": "A", "tests/properties/test_x.py": "A"}
    problems = check_pr_rules.check(files, added)
    assert len(problems) == 1 and "golden" in problems[0]

    files["tests/golden/cases/trust_report_rent_roll/basic.sql"] = "A"
    assert check_pr_rules.check(files, added) == []


def test_api_change_needs_api_tests():
    files = {"src/pms/api/routes.py": "A", "tests/test_x.py": "A"}
    problems = check_pr_rules.check(files, {})
    assert len(problems) == 1 and "tests/api/" in problems[0]

    files["tests/api/test_routes.py"] = "A"
    assert check_pr_rules.check(files, {}) == []


def test_deleted_files_are_not_checked_for_content():
    path = "db/migrations/20270101000001_x.sql"
    files = {path: "D", "tests/test_x.py": "M"}
    assert check_pr_rules.check(files, {path: [FUNCTION]}) == []


def test_main_reads_the_diff_between_two_commits(repo, capsys):
    repo.write("README.md", "hello\n")
    base = repo.commit("base")

    repo.write("db/migrations/20270101000001_x.sql", FUNCTION + "\n")
    repo.write("tests/test_x.py", "def test_x():\n    pass\n")
    head = repo.commit("ledger change without a property test")
    assert check_pr_rules.main(["--base", base, "--head", head, "--root", str(repo.path)]) == 1
    assert "tests/properties/" in capsys.readouterr().out

    repo.write("tests/properties/test_x.py", "def test_y():\n    pass\n")
    head = repo.commit("with a property test")
    assert check_pr_rules.main(["--base", base, "--head", head, "--root", str(repo.path)]) == 0
    assert "ok: 3 changed file(s)" in capsys.readouterr().out
