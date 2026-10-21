"""The test-run guards that tests/conftest.py applies to every pytest run.

Nothing is skipped: a skip marker or a runtime skip fails the run. Something that can't pass
yet is an xfail whose reason links the open issue tracking it (and xfail_strict is on, so it
fails again the moment it starts passing).

A run may be one shard of the suite (PMS_TEST_SHARD, `shard()`): the coverage job splits the
suite in two that way, and the shards together run every test once.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
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


def shard(items: Sequence[T], spec: str) -> tuple[list[T], list[T]]:
    """The tests shard `k/n` runs, and the rest: every nth test in collection order, from the
    kth. Test by test rather than file by file, so a file of slow property tests is shared too."""
    match = re.fullmatch(r"([1-9][0-9]*)/([1-9][0-9]*)", spec)
    if match is None or int(match.group(1)) > int(match.group(2)):
        raise ValueError(f"PMS_TEST_SHARD is k/n with 1 <= k <= n, like 1/2; got {spec!r}")
    k, n = int(match.group(1)), int(match.group(2))
    kept = [item for i, item in enumerate(items) if i % n == k - 1]
    rest = [item for i, item in enumerate(items) if i % n != k - 1]
    return kept, rest
