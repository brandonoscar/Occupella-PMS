"""tools/started_yesterday.py replays a run that crossed midnight UTC: modules load the day
before, tests run today. Each test runs pytest on a small module of its own in a subprocess."""

import os
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

from tools import started_yesterday
from tools.started_yesterday import loaded_at

ROOT = Path(__file__).resolve().parents[2]

MODULE = """
from datetime import UTC, datetime, timedelta

LOADED = datetime.now(UTC)


def test_the_day_it_loaded_and_the_day_it_runs():
    ran = datetime.now(UTC)
    assert (ran.date() - LOADED.date()).days == DAYS, (LOADED, ran)
    assert timedelta(0) < ran - LOADED < timedelta(days=1, minutes=2)
"""


def run(tmp_path: Path, days: int, **env: str) -> subprocess.CompletedProcess[str]:
    (tmp_path / "test_clock.py").write_text(MODULE.replace("DAYS", str(days)))
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "-p",
            "no:cacheprovider",
            "-p",
            "tools.started_yesterday",
            str(tmp_path / "test_clock.py"),
        ],
        capture_output=True,
        text=True,
        check=False,
        cwd=tmp_path,
        env={**os.environ, "PYTHONPATH": str(ROOT), **env},
    )


def test_with_the_variable_the_modules_load_a_minute_before_last_midnight(tmp_path):
    done = run(tmp_path, 1, PMS_TEST_STARTED_YESTERDAY="1")

    assert done.returncode == 0, done.stdout + done.stderr


def test_without_it_the_clock_is_never_moved(tmp_path):
    done = run(tmp_path, 0, PMS_TEST_STARTED_YESTERDAY="")

    assert done.returncode == 0, done.stdout + done.stderr


def test_loaded_at_is_the_minute_before_the_midnight_that_began_the_day():
    now = datetime(2026, 11, 3, 16, 30, 5, tzinfo=UTC)

    assert loaded_at(now) == datetime(2026, 11, 2, 23, 59, tzinfo=UTC)
    assert loaded_at(now.replace(hour=0, minute=0, second=0)) == loaded_at(now)
    assert loaded_at(now + timedelta(hours=8)) == datetime(2026, 11, 3, 23, 59, tzinfo=UTC)


def test_in_this_process_the_clock_moves_back_until_collection_ends(monkeypatch):
    monkeypatch.setenv("PMS_TEST_STARTED_YESTERDAY", "1")
    before = datetime.now(UTC)

    started_yesterday.pytest_configure(None)
    try:
        moved = datetime.now(UTC)
    finally:
        started_yesterday.pytest_collection_finish(None)
    after = datetime.now(UTC)

    assert timedelta(0) <= moved - loaded_at(before) < timedelta(seconds=30)
    assert timedelta(0) <= after - before < timedelta(seconds=30)
    assert started_yesterday._traveller is None
