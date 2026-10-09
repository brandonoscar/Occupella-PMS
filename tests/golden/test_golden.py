"""Golden files for reports. Every trust_report_* function needs at least one case under
tests/golden/cases/<function>/, and every case's output must match its .expected.txt exactly.
Regenerate deliberately with `pytest tests/golden --update-goldens` (see tools/golden.py)."""

from pathlib import Path

from tools import golden

CASES = Path(__file__).parent / "cases"


def test_every_report_function_has_a_golden_case(conn):
    assert golden.missing_cases(golden.report_functions(conn), CASES) == []


def test_every_golden_case_matches(conn, request):
    update = request.config.getoption("--update-goldens")
    diffs = [
        golden.compare(case, golden.run_case(conn, case), update) for case in golden.cases(CASES)
    ]
    assert [diff for diff in diffs if diff] == []
