"""tools/run_rules.py, the guards tests/conftest.py applies: no skips, no unlinked xfails."""

from types import SimpleNamespace

import pytest

from tools.run_rules import ISSUE_URL, marker_problems, outcome_problems


def item(nodeid, *marks):
    return SimpleNamespace(nodeid=nodeid, iter_markers=lambda: list(marks))


def test_skip_markers_are_refused():
    problems = marker_problems(
        [item("t::a", pytest.mark.skip.mark), item("t::b", pytest.mark.skipif(True).mark)]
    )
    assert problems == [
        "t::a: @pytest.mark.skip is not allowed",
        "t::b: @pytest.mark.skipif is not allowed",
    ]


def test_xfail_needs_an_issue_link():
    unlinked = pytest.mark.xfail(reason="later").mark
    linked = pytest.mark.xfail(reason=f"waits on {ISSUE_URL}12").mark
    assert marker_problems([item("t::a", unlinked), item("t::b", linked)]) == [
        f"t::a: xfail reason must link {ISSUE_URL}<n>"
    ]


def test_runtime_skips_and_unlinked_xfails_fail_the_run():
    stats = {
        "skipped": [SimpleNamespace(nodeid="t::a")],
        "xfailed": [
            SimpleNamespace(nodeid="t::b", wasxfail="later"),
            SimpleNamespace(nodeid="t::c", wasxfail=f"see {ISSUE_URL}3"),
        ],
    }
    assert outcome_problems(stats) == [
        "t::a: skipped; every test must run",
        f"t::b: xfailed without a link to {ISSUE_URL}<n>",
    ]
    assert outcome_problems({}) == []
