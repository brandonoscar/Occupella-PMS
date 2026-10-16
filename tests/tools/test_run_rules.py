"""tools/run_rules.py, what tests/conftest.py applies: no skips, no unlinked xfails, shards."""

from types import SimpleNamespace

import pytest

from tools.run_rules import ISSUE_URL, marker_problems, outcome_problems, shard


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


def test_shards_split_the_tests_one_by_one_and_together_run_each_once():
    tests = list("abcdefg")
    assert shard(tests, "1/2") == (list("aceg"), list("bdf"))
    assert shard(tests, "2/2") == (list("bdf"), list("aceg"))
    thirds = [shard(tests, f"{k}/3")[0] for k in (1, 2, 3)]
    assert sorted(sum(thirds, [])) == tests
    assert shard(tests, "1/1") == (tests, [])


@pytest.mark.parametrize("spec", ["0/2", "3/2", "1/0", "2", "1/2 ", "a/b", ""])
def test_a_shard_is_k_of_n(spec):
    with pytest.raises(ValueError, match="PMS_TEST_SHARD is k/n"):
        shard(["a"], spec)
