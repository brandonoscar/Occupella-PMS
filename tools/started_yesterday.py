"""PMS_TEST_STARTED_YESTERDAY=1: a test run that began the day before, replayed now.

A run that starts before midnight UTC and goes past it loads every test module on the first day
and runs the tests on the second. A test that keeps a day it worked out when its module loaded
(TODAY = datetime.now(UTC).date(), and what comes from it) and compares it with the database's
own clock (now(), current_date, created_at) then fails, though the PMS is right: PR #197's
coverage shards failed so on 11/02. This pytest plugin replays that on any day: with the
variable set, the modules load with the clock at one minute before midnight UTC, the day before
(time-machine, MIT, tests only), and the tests run on the real clock. The nightly runs the whole
suite so (ledger-invariants.yml); tests/conftest.py loads the plugin, and
`pytest -p tools.started_yesterday` loads it anywhere.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from typing import Any

import time_machine

_traveller: Any = None


def loaded_at(now: datetime) -> datetime:
    """When the modules load: a minute before the midnight (UTC) that began now's day."""
    midnight = now.astimezone(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    return midnight - timedelta(minutes=1)


def pytest_configure(config: Any) -> None:
    global _traveller
    if os.environ.get("PMS_TEST_STARTED_YESTERDAY") == "1" and _traveller is None:
        _traveller = time_machine.travel(loaded_at(datetime.now(UTC)), tick=True)
        _traveller.start()


def pytest_collection_finish(session: Any) -> None:
    global _traveller
    if _traveller is not None:
        _traveller.stop()
        _traveller = None
