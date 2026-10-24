"""The test-run guards that tests/conftest.py applies to every pytest run.

Nothing is skipped: a skip marker or a runtime skip fails the run. Something that can't pass
yet is an xfail whose reason links the open issue tracking it (and xfail_strict is on, so it
fails again the moment it starts passing).

A run may be one shard of the suite (PMS_TEST_SHARD, `shard()`), and the shards together run
every test once. Given the property tests' measured times (PMS_TEST_SHARD_TIMES,
`balanced_shard()`), the property tests are dealt out by time and the rest round robin: the
coverage job splits the suite in two that way, and the nightly property jobs take tonight's part
of a rotation only (PMS_TEST_ROTATION). The nightly API fuzz shares out the API's operations by a
hash of each (PMS_FUZZ_SHARD, `in_shard()`).
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Callable, Iterable, Mapping, Sequence
from typing import Any, TypeVar

T = TypeVar("T")

ISSUE_URL = "https://github.com/brandonoscar/Occupella-PMS/issues/"


def marker_problems(items: Iterable[Any]) -> list[str]:
    """Skip markers are banned; an xfail marker must link its issue."""
    problems = []
    for item in items:
        for mark in item.iter_markers():
            if mark.name in ("skip", "skipif"):
                problems.append(f"{item.nodeid}: @pytest.mark.{mark.name} is not allowed")
            elif mark.name == "xfail":
                reason = str(mark.kwargs.get("reason", ""))
                if ISSUE_URL not in reason:
                    problems.append(f"{item.nodeid}: xfail reason must link {ISSUE_URL}<n>")
    return problems


def outcome_problems(stats: Mapping[str, list[Any]]) -> list[str]:
    """Runtime skips are banned, and so is a runtime xfail that doesn't link its issue."""
    problems = [f"{r.nodeid}: skipped; every test must run" for r in stats.get("skipped", [])]
    for report in stats.get("xfailed", []):
        if ISSUE_URL not in str(getattr(report, "wasxfail", "")):
            problems.append(f"{report.nodeid}: xfailed without a link to {ISSUE_URL}<n>")
    return problems


def shard_spec(spec: str, name: str = "PMS_TEST_SHARD") -> tuple[int, int]:
    """k and n of a shard `k/n`, named by the variable that gave it when it is wrong."""
    match = re.fullmatch(r"([1-9][0-9]*)/([1-9][0-9]*)", spec)
    if match is None or int(match.group(1)) > int(match.group(2)):
        raise ValueError(f"{name} is k/n with 1 <= k <= n, like 1/2; got {spec!r}")
    return int(match.group(1)), int(match.group(2))


def in_shard(key: str, spec: str, name: str) -> bool:
    """Whether `key` is in shard `k/n`, by a hash of the key alone: the same on every machine
    and run, whatever else there is, so the shards together take each key exactly once."""
    k, n = shard_spec(spec, name)
    return int(hashlib.sha256(key.encode()).hexdigest(), 16) % n == k - 1


def deal(seconds: Mapping[str, float], n: int) -> list[list[str]]:
    """Tests dealt into n shards by their measured seconds: longest first, each to the shard
    with the least time so far (the first of those), ties by name, so it comes out the same
    whatever order the tests were found in."""
    shards: list[list[str]] = [[] for _ in range(n)]
    totals = [0.0] * n
    for name in sorted(seconds, key=lambda name: (-seconds[name], name)):
        least = min(range(n), key=lambda j: (totals[j], j))
        shards[least].append(name)
        totals[least] += seconds[name]
    return shards


def part_of(seconds: Mapping[str, float], spec: str, rotation: str | None = None) -> list[str]:
    """The tests shard `k/n` takes of `seconds`' tests: with a rotation `r/R`, the tests are
    first dealt into R parts (`deal()`) and part r is dealt into the n shards; without one, all
    of them are."""
    k, n = shard_spec(spec)
    if rotation:
        r, parts = shard_spec(rotation, "PMS_TEST_ROTATION")
        tonight = set(deal(seconds, parts)[r - 1])
        seconds = {name: time for name, time in seconds.items() if name in tonight}
    return deal(seconds, n)[k - 1]


def balanced_shard(
    items: Sequence[T],
    spec: str,
    seconds: Mapping[str, float],
    name_of: Callable[[T], str],
    rotation: str | None = None,
    timed: Callable[[T], bool] | None = None,
) -> tuple[list[T], list[T]]:
    """The tests shard `k/n` runs, and the rest, each kept in collection order. The tests `timed`
    picks (all of them without it) are dealt by their seconds (`part_of()`, maybe tonight's part
    of a rotation first); a test with no time yet counts as long as the longest, so it can't
    crowd a shard unseen. The others are shared round robin, as `shard()` shares them."""
    k, n = shard_spec(spec)
    picked = [timed is None or timed(item) for item in items]
    longest = max(seconds.values(), default=1.0)
    names = {name_of(item) for item, by_time in zip(items, picked, strict=True) if by_time}
    mine = set(part_of({name: seconds.get(name, longest) for name in names}, spec, rotation))
    keep, others = [], 0
    for item, by_time in zip(items, picked, strict=True):
        if by_time:
            keep.append(name_of(item) in mine)
        else:
            keep.append(others % n == k - 1)
            others += 1
    return (
        [item for item, kept in zip(items, keep, strict=True) if kept],
        [item for item, kept in zip(items, keep, strict=True) if not kept],
    )


def shard(items: Sequence[T], spec: str) -> tuple[list[T], list[T]]:
    """The tests shard `k/n` runs, and the rest: every nth test in collection order, from the
    kth. Test by test rather than file by file, so a file of slow property tests is shared too."""
    k, n = shard_spec(spec)
    kept = [item for i, item in enumerate(items) if i % n == k - 1]
    rest = [item for i, item in enumerate(items) if i % n != k - 1]
    return kept, rest
