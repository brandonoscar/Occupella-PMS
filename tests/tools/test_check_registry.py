"""tools/check_registry.py: every invariant is tested or tracked by an open issue."""

import io
import json

import pytest

from tools import check_registry

ISSUE = "https://github.com/brandonoscar/Occupella-PMS/issues/42"
TEST_FILE = """
import x

def test_plain():
    pass

class TestGroup:
    def test_method(self):
        pass

TestMachine = Machine.TestCase
"""


@pytest.fixture
def root(tmp_path):
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests/test_a.py").write_text(TEST_FILE)
    return tmp_path


@pytest.mark.parametrize(
    "node_id, exists",
    [
        ("tests/test_a.py::test_plain", True),
        ("tests/test_a.py::TestGroup::test_method", True),
        ("tests/test_a.py::TestMachine", True),
        ("tests/test_a.py::test_missing", False),
        ("tests/test_a.py::TestGroup::test_missing", False),
        ("tests/test_a.py::test_plain::nested", False),
        ("tests/test_a.py", False),
        ("tests/missing.py::test_plain", False),
    ],
)
def test_node_exists_parses_the_file(root, node_id, exists):
    assert check_registry.node_exists(node_id, root) is exists


def test_text_mention_is_not_a_test(root):
    (root / "tests/test_b.py").write_text('NOTE = "def test_ghost(): pass"\n')
    assert check_registry.node_exists("tests/test_b.py::test_ghost", root) is False


def registry(**entry):
    return {"invariant": [{"id": "x", "statement": "something holds", **entry}]}


def test_entry_with_tests_or_pending_passes(root):
    assert check_registry.check(registry(tests=["tests/test_a.py::test_plain"]), root) == []
    assert check_registry.check(registry(pending=ISSUE), root) == []
    both = registry(tests=["tests/test_a.py::test_plain"], pending=ISSUE)
    assert check_registry.check(both, root) == []


def test_entry_problems_are_reported(root):
    assert check_registry.check(registry(), root) == [
        "invariant:x: needs `tests`, a `pending` issue link, or both."
    ]
    problems = check_registry.check(
        registry(statement="", pending="https://example.com/issues/1"), root
    )
    assert "invariant:x: missing `statement`." in problems
    assert any("must be an issue URL in this repo" in p for p in problems)
    problems = check_registry.check(registry(tests=["tests/test_a.py::test_missing"]), root)
    assert problems == ["invariant:x: test tests/test_a.py::test_missing does not exist."]


def test_closed_or_wrong_issue_fails():
    states = {"42": "open"}
    assert check_registry.check_issues(registry(pending=ISSUE), None, lambda n, t: states[n]) == []
    states["42"] = "closed"
    assert check_registry.check_issues(registry(pending=ISSUE), None, lambda n, t: states[n]) == [
        f"invariant:x: pending issue {ISSUE} is closed."
    ]


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


@pytest.mark.parametrize(
    "payload, state",
    [({"state": "open"}, "open"), ({"state": "open", "pull_request": {}}, "pull request")],
)
def test_issue_state_reads_the_api(monkeypatch, payload, state):
    seen = {}

    def urlopen(request, timeout):
        seen["auth"] = request.get_header("Authorization")
        return FakeResponse(json.dumps(payload).encode())

    monkeypatch.setattr(check_registry.urllib.request, "urlopen", urlopen)
    assert check_registry.issue_state("42", "token-value") == state
    assert seen["auth"] == "Bearer token-value"


def test_compare_refuses_removing_entries_or_tests():
    base = {"invariant": [{"id": "a", "tests": ["t1", "t2"]}, {"id": "b", "pending": ISSUE}]}
    head = {"invariant": [{"id": "a", "tests": ["t1"]}]}
    assert check_registry.compare(base, head) == [
        "invariant:a lost tests ['t2'].",
        "invariant:b was removed from ci/registry.toml.",
    ]
    assert check_registry.compare(base, base) == []


def test_main_checks_the_real_registry(capsys):
    assert check_registry.main([]) == 0
    assert "ok:" in capsys.readouterr().out


def test_main_against_a_base_commit(repo, capsys):
    repo.write("tests/test_a.py", TEST_FILE)
    repo.write(
        "ci/registry.toml",
        '[[invariant]]\nid = "x"\nstatement = "s"\ntests = ["tests/test_a.py::test_plain"]\n',
    )
    base = repo.commit("base")
    repo.write("ci/registry.toml", "")
    assert check_registry.main(["--root", str(repo.path), "--base", base]) == 1
    assert "invariant:x was removed" in capsys.readouterr().out


def test_main_with_issue_check(monkeypatch, root, capsys):
    (root / "ci").mkdir()
    (root / "ci/registry.toml").write_text(
        f'[[report]]\nid = "r"\nstatement = "s"\npending = "{ISSUE}"\n'
    )
    monkeypatch.setattr(check_registry, "issue_state", lambda number, token: "closed")
    assert check_registry.main(["--root", str(root), "--check-issues"]) == 1
    assert "is closed" in capsys.readouterr().out
