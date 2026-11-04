"""A company's day (migration 20261104000900, the founder's review of 11/03, item 13): the date in
the company's own time zone, America/New_York unless it picked another in Company setup. It is what
"today" is for the company's money, its pages and its nightly run, so rent paid at 9 pm Eastern on
October 31 is dated October 31. The books date money by its day (a transfer's event_at is that day
at 00:00 UTC), so the day here is the day every report shows.

Every zone a company may pick is at or behind UTC: a company's day is never after UTC's.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Final, Literal
from uuid import UUID

import psycopg

DEFAULT: Final = "America/New_York"
TimeZone = Literal[
    "America/New_York",
    "America/Chicago",
    "America/Denver",
    "America/Phoenix",
    "America/Los_Angeles",
    "America/Anchorage",
    "Pacific/Honolulu",
    "America/Puerto_Rico",
    "UTC",
]
# The zones a company may pick (migration 20261104000900's list), as Company setup names them.
ZONES: dict[str, str] = {
    "America/New_York": "Eastern",
    "America/Chicago": "Central",
    "America/Denver": "Mountain",
    "America/Phoenix": "Arizona",
    "America/Los_Angeles": "Pacific",
    "America/Anchorage": "Alaska",
    "Pacific/Honolulu": "Hawaii",
    "America/Puerto_Rico": "Atlantic (Puerto Rico)",
    "UTC": "UTC",
}


def today(conn: psycopg.Connection[Any], pmc_id: UUID) -> date:
    """The company's day now."""
    row = conn.execute("SELECT trust_pmc_day(%s)", (pmc_id,)).fetchone()
    assert row is not None
    day: date = row[0]
    return day


def day_at(conn: psycopg.Connection[Any], pmc_id: UUID, at: datetime) -> date:
    """The company's day at a moment."""
    row = conn.execute("SELECT trust_pmc_day(%s, %s)", (pmc_id, at)).fetchone()
    assert row is not None
    day: date = row[0]
    return day


def time_zone(conn: psycopg.Connection[Any], pmc_id: UUID) -> str:
    """The company's time zone, as an IANA name."""
    row = conn.execute("SELECT trust_pmc_time_zone(%s)", (pmc_id,)).fetchone()
    assert row is not None
    return str(row[0])
