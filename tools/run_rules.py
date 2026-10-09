"""The test-run guards that tests/conftest.py applies to every pytest run.

Nothing is skipped: a skip marker or a runtime skip fails the run. Something that can't pass
yet is an xfail whose reason links the open issue tracking it (and xfail_strict is on, so it
fails again the moment it starts passing).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

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
