"""tools/run_rules.py, what tests/conftest.py applies: no skips, no unlinked xfails, shards."""

from types import SimpleNamespace

import pytest

from tools.run_rules import (
    ISSUE_URL,
    balanced_shard,
    deal,
    in_shard,
    marker_problems,
    outcome_problems,
    part_of,
    shard,
)


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
    with pytest.raises(ValueError, match="PMS_FUZZ_SHARD is k/n"):
        in_shard("GET /health", spec, "PMS_FUZZ_SHARD")


def test_hashed_shards_take_each_key_once_and_the_same_way_every_time():
    keys = [f"{method} /api/{n}" for method in ("GET", "POST") for n in range(400)]
    shares = [
        [key for key in keys if in_shard(key, f"{k}/4", "PMS_FUZZ_SHARD")] for k in (1, 2, 3, 4)
    ]
    assert sorted(sum(shares, [])) == sorted(keys)
    # Each key's shard is its own: a key added or taken away moves no other.
    assert [key for key in keys[:-1] if in_shard(key, "2/4", "PMS_FUZZ_SHARD")] == [
        key for key in shares[1] if key != keys[-1]
    ]
    assert all(150 <= len(share) <= 250 for share in shares)  # about a quarter each
    assert all(in_shard(key, "1/1", "PMS_FUZZ_SHARD") for key in keys)


def test_dealing_by_time_gives_the_longest_first_to_the_least_loaded_shard():
    seconds = {"a": 10.0, "b": 7.0, "c": 6.0, "d": 4.0, "e": 3.0}
    # a (10) to the first; b (7) to the second; c to the second (13); d to the first (14); e to
    # the second (16).
    assert deal(seconds, 2) == [["a", "d"], ["b", "c", "e"]]
    # The order the tests were found in changes nothing; equal times go by name.
    assert deal(dict(reversed(list(seconds.items()))), 2) == deal(seconds, 2)
    assert deal({"y": 1.0, "x": 1.0}, 2) == [["x"], ["y"]]
    assert deal(seconds, 1) == [["a", "b", "c", "d", "e"]]


def test_a_rotation_deals_parts_first_and_every_test_runs_once_a_rotation():
    seconds = {f"t{n}": float(n % 7 + 1) for n in range(40)}
    runs = [
        name
        for r in (1, 2, 3, 4)
        for k in (1, 2, 3)
        for name in part_of(seconds, f"{k}/3", f"{r}/4")
    ]
    assert sorted(runs) == sorted(seconds)
    # Each night's part is a quarter of the time, give or take its longest test.
    nights = [
        sum(seconds[n] for k in (1, 2, 3) for n in part_of(seconds, f"{k}/3", f"{r}/4"))
        for r in (1, 2, 3, 4)
    ]
    assert max(nights) - min(nights) <= max(seconds.values())
    assert part_of(seconds, "1/1") == sorted(seconds, key=lambda n: (-seconds[n], n))
    with pytest.raises(ValueError, match="PMS_TEST_ROTATION is k/n"):
        part_of(seconds, "1/2", "5/4")


def test_a_test_with_no_time_counts_as_the_longest():
    items = ["a[1]", "a[2]", "b", "new"]
    seconds = {"a": 9.0, "b": 1.0}
    kept, rest = balanced_shard(items, "1/2", seconds, lambda item: item.split("[")[0])
    # "new", counted as long as "a" (9), takes the other shard to itself; "b" (1) joins "a", and
    # each case of "a" stays with it.
    assert kept == ["a[1]", "a[2]", "b"]
    assert rest == ["new"]


def test_only_the_timed_tests_are_dealt_by_time_and_the_rest_go_round_robin():
    # The coverage job: property tests (here "p_...") dealt by time, every other test shared one
    # by one in the order found; each shard keeps the order found, and together they run each
    # test once.
    items = ["x1", "p_slow", "x2", "p_new", "x3", "p_a", "x4", "p_b"]
    seconds = {"p_slow": 30.0, "p_a": 2.0, "p_b": 1.0}
    timed = lambda item: item.startswith("p_")  # noqa: E731
    one, rest_one = balanced_shard(items, "1/2", seconds, str, timed=timed)
    two, rest_two = balanced_shard(items, "2/2", seconds, str, timed=timed)
    # p_new counts as 30 like p_slow and goes first by name; p_a joins it on the tie, and p_b
    # goes to the lighter shard: 32 and 31 seconds. x1 and x3 go to the first, x2 and x4 not.
    assert one == ["x1", "p_new", "x3", "p_a"]
    assert two == ["p_slow", "x2", "x4", "p_b"]
    assert rest_one == two and rest_two == one
    # Without the predicate every test is dealt by time, as the nightly property jobs deal them.
    assert balanced_shard(["p_a", "p_b", "x1"], "1/2", seconds, str)[0] == ["x1"]
