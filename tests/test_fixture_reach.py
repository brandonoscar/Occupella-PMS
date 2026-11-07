"""A sub-directory's conftest fixtures reach its tests whatever order the files are named in.

Seen 2026-11-07: `pytest tests/api/a.py tests/b.py tests/api/c.py` failed c.py's tests with
"fixture 'client' not found". pytest 9.1 ties a conftest's fixtures to the directory node that
first collected it; a file argument directly in tests/ makes pytest collect tests/ again, which
made a second tests/api node the conftest was never read for (pytest-dev/pytest#14635, fixed in
pytest 9.2.0, which keeps the first node). tests/api and tests/tools are the sub-directories
with a conftest of their own, so this runs each the same way: one of its files, a file in
tests/, another of its files.

`--setup-plan` resolves every test's fixtures without running a test or a fixture, so it needs
no database: a fixture that doesn't reach a test is still an error.
"""

import ast
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TESTS = ROOT / "tests"

# Run the inner pytest on the files it is given, as a developer would: no shard of a CI job,
# no coverage file, no moved clock.
OUTER_ONLY = (
    "PMS_TEST_SHARD",
    "PMS_TEST_SHARD_TIMES",
    "PMS_TEST_ROTATION",
    "PMS_SQL_COVERAGE_OUT",
    "PMS_TEST_STARTED_YESTERDAY",
)


def _is_fixture(decorator: ast.expr) -> bool:
    target = decorator.func if isinstance(decorator, ast.Call) else decorator
    return (isinstance(target, ast.Attribute) and target.attr == "fixture") or (
        isinstance(target, ast.Name) and target.id == "fixture"
    )


def fixtures_defined(path: Path) -> set[str]:
    """The names of the fixtures a file defines."""
    tree = ast.parse(path.read_text())
    return {
        node.name
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
        and any(_is_fixture(decorator) for decorator in node.decorator_list)
    }


def fixtures_requested(path: Path) -> set[str]:
    """The arguments a file's test functions take, at module level or in a class."""
    tree = ast.parse(path.read_text())
    return {
        argument.arg
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and node.name.startswith("test")
        for argument in node.args.args
    }


def users_of_conftest(directory: Path) -> list[Path]:
    """The test files directly in a directory that take a fixture its conftest defines and
    don't define that fixture themselves, in name order."""
    theirs = fixtures_defined(directory / "conftest.py")
    return [
        path
        for path in sorted(directory.glob("test_*.py"))
        if (fixtures_requested(path) & theirs) - fixtures_defined(path)
    ]


def directories_with_a_conftest() -> list[Path]:
    return sorted(path.parent for path in TESTS.glob("*/conftest.py"))


def a_file_in_tests() -> Path:
    """A test file directly in tests/, so that naming it collects tests/ again."""
    return sorted(TESTS.glob("test_*.py"))[0]


def interleaved() -> list[Path]:
    order: list[Path] = []
    for directory in directories_with_a_conftest():
        users = users_of_conftest(directory)
        assert len(users) >= 2, f"{directory}: fewer than two test files use its conftest"
        order += [users[0], a_file_in_tests(), users[-1]]
    return order


def setup_plan(paths: list[Path]) -> subprocess.CompletedProcess[str]:
    env = {key: value for key, value in os.environ.items() if key not in OUTER_ONLY}
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "--setup-plan",
            "-q",
            "-p",
            "no:cacheprovider",
            *(str(path.relative_to(ROOT)) for path in paths),
        ],
        capture_output=True,
        text=True,
        check=False,
        cwd=ROOT,
        env=env,
    )


def planned_fixtures(plan: str, path: Path) -> set[str]:
    """Every fixture --setup-plan says a test of the file uses: its lines read
    `tests/x.py::test_y (fixtures used: a, b)`."""
    prefix = path.relative_to(ROOT).as_posix() + "::"
    used: set[str] = set()
    for line in plan.splitlines():
        test, _, rest = line.strip().partition(" (fixtures used: ")
        if test.startswith(prefix) and rest.endswith(")"):
            used |= set(rest[:-1].split(", "))
    return used


def test_every_conftest_below_tests_is_covered():
    # A new sub-directory with a conftest is run below by itself; this says which there are
    # today, so a conftest that moves or goes is seen.
    assert [path.relative_to(TESTS).as_posix() for path in directories_with_a_conftest()] == [
        "api",
        "tools",
    ]


def test_a_file_in_tests_between_two_of_a_subdirectory_leaves_its_fixtures_in_reach():
    paths = interleaved()

    done = setup_plan(paths)

    assert "not found" not in done.stdout, done.stdout[-4000:]
    assert done.returncode == 0, done.stdout[-4000:] + done.stderr[-2000:]
    # The file named after the one in tests/ had its sub-directory's fixtures planned, so the
    # run above was not vacuous.
    for directory in directories_with_a_conftest():
        last = users_of_conftest(directory)[-1]
        wanted = (fixtures_requested(last) & fixtures_defined(directory / "conftest.py")) - (
            fixtures_defined(last)
        )
        used = planned_fixtures(done.stdout, last)
        assert used, f"no test of {last.name} was planned"
        assert wanted <= used, f"{sorted(wanted - used)} never planned for {last.name}"


def test_the_guard_sees_which_files_use_a_conftest(tmp_path):
    # Fixtures written both ways a file may: @pytest.fixture and, imported, @fixture.
    (tmp_path / "conftest.py").write_text(
        "from pytest import fixture\n\n" + "@" + "fixture\ndef shared():\n    return 1\n"
    )
    (tmp_path / "test_uses.py").write_text("def test_it(shared):\n    assert shared\n")
    (tmp_path / "test_own.py").write_text(
        "import pytest\n\n" + "@" + "pytest.fixture(scope='module')\ndef shared():\n"
        "    return 2\n\ndef test_it(shared):\n    assert shared\n"
    )
    (tmp_path / "test_none.py").write_text("def test_it():\n    pass\n")

    assert users_of_conftest(tmp_path) == [tmp_path / "test_uses.py"]
