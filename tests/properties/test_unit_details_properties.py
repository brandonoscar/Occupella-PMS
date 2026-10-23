"""Property-based tests for a unit's details (migration 20261022000124).

Hypothesis gives a vacant unit details recorded at several moments over three weeks (bedrooms,
bathrooms in halves, square feet and a market rent, each maybe left out), and days to run the
vacancy report on. A model says the report's unit line has, on any day, the details of the latest
row recorded by the end of that day (UTC), and none before the first.

It also gives a run of saves through the API's write path, some the same as the last, some empty:
a save writes a row exactly when it differs from what the unit has (with none yet, an empty save
writes nothing), and the unit then reads as the last save asked.
Run more examples locally with HYPOTHESIS_PROFILE=nightly.
"""

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import psycopg
from helpers import add_unit, make_pmc
from hypothesis import event, given
from hypothesis import strategies as st

from pms.api import unit_details

START = datetime(2026, 3, 1, tzinfo=UTC)
DETAILS = st.tuples(
    st.one_of(st.none(), st.integers(0, 20)),
    st.one_of(st.none(), st.integers(0, 40).map(lambda h: Decimal(h) / 2)),
    st.one_of(st.none(), st.integers(1, 100_000)),
    st.one_of(st.none(), st.integers(0, 1_000_000_000).map(lambda c: Decimal(c) / 100)),
)
# Saves at distinct minutes within three weeks of START.
SAVES = st.dictionaries(st.integers(0, 21 * 24 * 60), DETAILS, max_size=6)
DAYS = st.lists(st.integers(-1, 22), min_size=1, max_size=5, unique=True)


@given(saves=SAVES, days=DAYS)
def check_report(conn, saves, days):
    pmc = make_pmc(conn)
    unit = add_unit(conn, pmc.pmc_id, pmc.owners[0].property_id, "Unit 1")
    for minute, (beds, baths, size, rent) in saves.items():
        conn.execute(
            "INSERT INTO trust_unit_details"
            " (pmc_id, unit_id, bedrooms, bathrooms, square_feet, market_rent, created_at)"
            " VALUES (%s, %s, %s, %s, %s, %s, %s)",
            (pmc.pmc_id, unit, beds, baths, size, rent, START + timedelta(minutes=minute)),
        )
    for offset in days:
        day = START.date() + timedelta(days=offset)
        end = datetime(day.year, day.month, day.day, tzinfo=UTC) + timedelta(days=1)
        recorded = [m for m in saves if START + timedelta(minutes=m) < end]
        expected = saves[max(recorded)] if recorded else (None, None, None, None)
        event("details recorded by the day" if recorded else "none yet")
        assert conn.execute(
            "SELECT bedrooms, bathrooms, square_feet, market_rent"
            " FROM trust_report_vacancy(%s, %s) WHERE item = 'vacant'",
            (pmc.pmc_id, date(day.year, day.month, day.day)),
        ).fetchall() == [expected]


@given(
    asks=st.lists(st.sampled_from([0, 1, 2, 3]), max_size=8),
    pool=st.lists(DETAILS, min_size=3, max_size=3),
)
def check_saves(conn, asks, pool):
    pmc = make_pmc(conn)
    unit = add_unit(conn, pmc.pmc_id, pmc.owners[0].property_id, "Unit 1")
    choices = [*pool, (None, None, None, None)]  # the fourth asks for nothing at all
    has = unit_details.UnitDetails()
    rows = 0
    for ask in asks:
        beds, baths, size, rent = choices[ask]
        new = unit_details.NewUnitDetails(
            bedrooms=beds, bathrooms=baths, square_feet=size, market_rent=rent
        )
        asked = unit_details.shown(beds, baths, size, rent)
        wrote = unit_details.record_details(conn, pmc.pmc_id, unit, new)
        event("wrote" if wrote else "the same again: nothing written")
        assert wrote == (asked != has)
        rows += wrote
        has = asked
        assert unit_details.details_of(conn, [unit])[unit] == has
    assert conn.execute(
        "SELECT count(*) FROM trust_unit_details WHERE unit_id = %s", (unit,)
    ).fetchone() == (rows,)


def test_the_vacancy_report_has_the_details_recorded_by_the_end_of_its_day(database_url):
    with psycopg.connect(database_url, autocommit=True) as conn:
        check_report(conn)


def test_a_save_writes_only_what_changes_and_the_unit_reads_the_last(database_url):
    with psycopg.connect(database_url, autocommit=True) as conn:
        check_saves(conn)
