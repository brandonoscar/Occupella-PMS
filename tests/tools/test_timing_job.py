"""The nightly timing job of ledger-invariants.yml runs every @pytest.mark.timing test 50 times
(CLAUDE.md, the standing rule's item 8), in parts that each fit their job's limit: 50 in one job
reached its 40 minutes (issue #230), so the runs are shared out over a matrix."""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = (ROOT / ".github/workflows/ledger-invariants.yml").read_text()


def timing_job() -> str:
    """The timing job of ledger-invariants.yml, up to the next job."""
    match = re.search(r"^  timing:\n(.*?)(?=^  \S)", WORKFLOW, re.M | re.S)
    assert match, "ledger-invariants.yml has no timing job"
    return match.group(1)


def test_the_parts_together_run_every_timing_test_50_times():
    job = timing_job()
    (parts,) = re.findall(r"^\s+part: \[([\d, ]+)\]$", job, re.M)
    (runs,) = re.findall(r"for run in \$\(seq 1 (\d+)\)", job)

    assert [int(part) for part in parts.split(",")] == [1, 2]
    assert len(parts.split(",")) * int(runs) == 50
    assert "pytest -q -p no:cacheprovider -m timing tests" in job
    # A failure in one part never cancels the other's runs, and every failed run fails its job.
    assert "fail-fast: false" in job
    assert 'test "$failed" -eq 0' in job


def test_each_part_keeps_the_40_minutes_and_runs_only_at_night():
    job = timing_job()

    assert re.findall(r"^    timeout-minutes: (\d+)$", job, re.M) == ["40"]
    assert "if: github.event_name == 'schedule' || github.event_name == 'workflow_dispatch'" in job
