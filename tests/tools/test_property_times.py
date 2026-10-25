"""ci/property-times.toml, which the nightly property jobs and the coverage job's two shards deal
the property tests out by, against the property tests it times and the workflow jobs it plans for
(tools/property_times.py, tools/run_rules.py)."""

import ast
import re
import tomllib
from pathlib import Path

import pytest

from tools import property_times
from tools.run_rules import part_of

ROOT = Path(__file__).resolve().parents[2]
TIMES = tomllib.loads((ROOT / "ci/property-times.toml").read_text())
WORKFLOW = (ROOT / ".github/workflows/ledger-invariants.yml").read_text()


def property_tests() -> set[str]:
    """Each property test as pytest names it, without a parametrized case's id."""
    names = set()
    for path in sorted((ROOT / "tests/properties").glob("test_*.py")):
        for node in ast.parse(path.read_text()).body:
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and node.name.startswith(
                "test_"
            ):
                names.add(f"{path.relative_to(ROOT).as_posix()}::{node.name}")
    return names


def nightly_job() -> str:
    """The nightly property job of ledger-invariants.yml, up to the next job."""
    match = re.search(r"^  nightly:\n(.*?)(?=^  \S)", WORKFLOW, re.M | re.S)
    assert match, "ledger-invariants.yml has no nightly job"
    return match.group(1)


def test_every_property_test_has_its_time():
    tests, timed = property_tests(), set(TIMES["seconds"])
    assert not tests - timed, (
        f"no time in ci/property-times.toml for {sorted(tests - timed)}: run them with "
        "`pytest <file> -p no:cacheprovider --durations=0 > durations.log` and "
        "`python -m tools.property_times durations.log`"
    )
    assert not timed - tests, f"take these out of ci/property-times.toml: {sorted(timed - tests)}"


def test_the_scan_finds_the_property_tests():
    tests = property_tests()
    ledger = "tests/properties/test_ledger_properties.py"
    assert f"{ledger}::test_ledger_invariants_hold_under_random_postings" in tests
    assert all(name.startswith("tests/properties/test_") and "::test_" in name for name in tests)


def test_the_nightly_job_runs_the_shards_and_minutes_the_times_plan_for():
    job, shards = nightly_job(), TIMES["shards"]
    matrix = re.search(r"^ +shard: \[([0-9, ]+)\]$", job, re.M)
    assert matrix and [int(k) for k in matrix.group(1).split(",")] == list(range(1, shards + 1))
    assert f"PMS_TEST_SHARD: ${{{{ matrix.shard }}}}/{shards}" in job
    assert "PMS_TEST_SHARD_TIMES: ci/property-times.toml" in job
    limit = re.search(r"^    timeout-minutes: ([0-9]+)$", job, re.M)
    # The step stops itself at job_minutes; the job's own limit leaves room for setup after it.
    assert limit and int(limit.group(1)) >= TIMES["job_minutes"] + 10
    assert TIMES["job_minutes"] + 10 <= 360  # GitHub stops any job at six hours


def pr_job() -> str:
    """The PR's property job of ledger-invariants.yml, up to the next job."""
    match = re.search(r"^  properties:\n(.*?)(?=^  \S)", WORKFLOW, re.M | re.S)
    assert match, "ledger-invariants.yml has no properties job"
    return match.group(1)


def test_the_pr_job_runs_the_property_tests_in_two_halves_dealt_by_time():
    """Both halves run at once, so the job takes about its longer half: each half's PR seconds,
    plus setup, well inside the job's minutes."""
    job, seconds = pr_job(), TIMES["seconds"]
    assert 'PMS_TEST_SHARD="$half/2"' in job and "for half in 1 2; do" in job
    assert "PMS_TEST_SHARD_TIMES: ci/property-times.toml" in job
    limit = re.search(r"^    timeout-minutes: ([0-9]+)$", job, re.M)
    assert limit
    halves = [sum(seconds[name] for name in part_of(seconds, f"{k}/2")) for k in (1, 2)]
    assert sum(halves) == pytest.approx(sum(seconds.values()))
    assert max(halves) <= int(limit.group(1)) * 60 * 0.75


def test_the_coverage_shards_deal_the_property_tests_by_their_times():
    coverage = (ROOT / ".github/workflows/coverage.yml").read_text()
    assert "PMS_TEST_SHARD: ${{ matrix.shard }}/2" in coverage
    assert "PMS_TEST_SHARD_TIMES: ci/property-times.toml" in coverage


def test_every_nightly_job_fits_well_inside_its_minutes():
    """Each shard of each night's part, at the nightly profile, predicted from its tests' PR
    seconds times nightly_factor, within three quarters of job_minutes."""
    seconds, parts, shards = TIMES["seconds"], TIMES["rotations"], TIMES["shards"]
    budget = TIMES["job_minutes"] * 60 * 0.75
    jobs = [
        (sum(seconds[name] for name in part_of(seconds, f"{k}/{shards}", f"{r}/{parts}")), r, k)
        for r in range(1, parts + 1)
        for k in range(1, shards + 1)
    ]
    longest, r, k = max(jobs)
    predicted = longest * TIMES["nightly_factor"]
    assert predicted <= budget, (
        f"night {r}/{parts}, shard {k}/{shards}: {predicted / 60:.0f} minutes predicted, more "
        f"than three quarters of {TIMES['job_minutes']}; add a rotation or a shard"
    )


def test_every_property_runs_once_a_rotation():
    seconds, parts, shards = TIMES["seconds"], TIMES["rotations"], TIMES["shards"]
    runs = [
        name
        for r in range(1, parts + 1)
        for k in range(1, shards + 1)
        for name in part_of(seconds, f"{k}/{shards}", f"{r}/{parts}")
    ]
    assert sorted(runs) == sorted(seconds)


def test_durations_reads_each_tests_call_time_and_sums_its_cases():
    log = """
============================= slowest durations ==============================
151.21s call     tests/properties/test_a.py::test_one
2.50s call     tests/properties/test_b.py::test_two[case-1]
1.50s call     tests/properties/test_b.py::test_two[case-2]
0.40s setup    tests/properties/test_a.py::test_one
3.00s call     tests/api/test_c.py::test_three
"""
    assert property_times.durations(log) == {
        "tests/properties/test_a.py::test_one": 151.21,
        "tests/properties/test_b.py::test_two": 4.0,
    }


def test_rewrite_updates_and_adds_times_and_keeps_the_rest():
    times = '# kept\nrotations = 4\n\n[seconds]\n"t::b" = 2.0\n"t::a" = 9.0\n'
    assert property_times.rewrite(times, {"t::a": 7.25, "t::c": 1.0}) == (
        '# kept\nrotations = 4\n\n[seconds]\n"t::a" = 7.2\n"t::b" = 2.0\n"t::c" = 1.0\n'
    )


def test_the_command_writes_a_logs_times_into_the_file(tmp_path, monkeypatch, capsys):
    times = tmp_path / "property-times.toml"
    times.write_text('rotations = 4\n\n[seconds]\n"tests/properties/test_a.py::test_one" = 9.0\n')
    monkeypatch.setattr(property_times, "TIMES", times)
    monkeypatch.setattr(property_times, "ROOT", tmp_path)
    log = tmp_path / "durations.log"
    log.write_text("2.25s call     tests/properties/test_a.py::test_one\n")
    assert property_times.main([str(log)]) == 0
    assert '"tests/properties/test_a.py::test_one" = 2.2' in times.read_text()
    assert "ok: 1 property tests' times written to property-times.toml" in capsys.readouterr().out
    # A log without durations changes nothing, and the command wants exactly one log.
    log.write_text("nothing timed here\n")
    assert property_times.main([str(log)]) == 1
    assert property_times.main([]) == 2
    assert "usage: python -m tools.property_times DURATIONS_LOG" in capsys.readouterr().err


def test_rewrite_refuses_a_file_without_its_seconds_table():
    with pytest.raises(ValueError, match="no \\[seconds\\] table"):
        property_times.rewrite("rotations = 4\n", {"t::a": 1.0})
