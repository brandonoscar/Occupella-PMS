"""Property-based tests for closing a year (migration 20261021000106; the founder's review q116).

Hypothesis works one company's operating account over the two years before this one: month-ends
approved through random month ends, year closes asked for either year, and charges and bills dated
on random days. A model of how far month-end is approved and the years closed says which requests
stand: a year closes once the account is approved through its December 31 and no later year is
closed (a year closed already stands again, even after a later one); a charge or bill stands when
dated after the last closed year. The database must take exactly those and refuse the rest, and
trust_closed_until must say what the model says after each.
Run more examples locally with HYPOTHESIS_PROFILE=nightly.
"""

from datetime import UTC, date, datetime, timedelta

import psycopg
from helpers import (
    acting_as,
    add_tenant,
    add_unit,
    approve_bank,
    charge,
    enter_bill,
    import_statement,
    make_pmc,
    open_lease,
    staff_named,
    transfer_batch,
)
from hypothesis import event, example, given
from hypothesis import strategies as st

THIS_YEAR = datetime.now(UTC).year
FIRST = date(THIS_YEAR - 2, 1, 1)
DAYS = (date(THIS_YEAR + 1, 1, 1) - FIRST).days  # through this year's end

# Days on and around each year's turn, where a close draws its line, more often than others.
EDGES = [
    (date(year, month, day) - FIRST).days
    for year in (THIS_YEAR - 2, THIS_YEAR - 1)
    for month, day in ((1, 1), (12, 31))
] + [(date(THIS_YEAR, 1, 1) - FIRST).days]
DAY = st.one_of(st.integers(0, DAYS - 1), st.sampled_from(EDGES))
REQUESTS = st.lists(
    st.one_of(
        # Through the end of month n; most often a December.
        st.tuples(st.just("approve"), st.one_of(st.integers(1, 24), st.sampled_from([12, 24]))),
        st.tuples(st.just("close"), st.sampled_from([THIS_YEAR - 2, THIS_YEAR - 1])),
        st.tuples(st.just("charge"), DAY),
        st.tuples(st.just("bill"), DAY),
    ),
    min_size=1,
    max_size=14,
)


def month_end(n: int) -> date:
    """The last day of the nth month from the first of two years ago."""
    year, month = THIS_YEAR - 2 + (n - 1) // 12, (n - 1) % 12 + 1
    return (date(year + month // 12, month % 12 + 1, 1)) - timedelta(days=1)


# Always tried, whatever else is drawn: the year before a closed one asked to close, and a charge
# and a bill on each side of a closed year's last day.
@example(
    requests=[
        ("approve", 24),
        ("close", THIS_YEAR - 1),
        ("close", THIS_YEAR - 2),
        ("close", THIS_YEAR - 1),
    ]
)
@example(
    requests=[("approve", 12), ("close", THIS_YEAR - 2)]
    + [(kind, day) for kind in ("charge", "bill") for day in EDGES[1:3]]
)
@given(requests=REQUESTS)
def check(conn, requests):
    pmc = make_pmc(conn)
    prop = pmc.owners[0].property_id
    tenant = add_tenant(conn, pmc.pmc_id, prop, "Tenant 1")
    unit = add_unit(conn, pmc.pmc_id, prop)
    lease = open_lease(conn, pmc.pmc_id, unit, FIRST, None, "900.00", [tenant])
    # The account moved money in the first year: both years touch it.
    transfer_batch(
        conn,
        [(pmc.operating_cash, pmc.owners[0].account, "100.00")],
        datetime(FIRST.year, 1, 5, 12, tzinfo=UTC),
    )
    importer = staff_named(conn, pmc.pmc_id, "Importer 1")
    closer = staff_named(conn, pmc.pmc_id, "Closer 1", ["approve"])
    approved: date | None = None
    closed: int | None = None
    closed_years: set[int] = set()
    charges = bills = 0

    for kind, value in requests:
        if kind == "approve":
            through = month_end(value)
            stands = approved is None or through > approved
            if stands:
                start = FIRST if approved is None else approved + timedelta(days=1)
                import_statement(
                    conn, pmc.pmc_id, pmc.operating_bank_id, importer, start, through, []
                )
                approve_bank(conn, pmc.pmc_id, pmc.operating_bank_id, through)
                approved = through
            event(f"approve {'stands' if stands else 'skipped'}")
            continue
        if kind == "close":
            # A year closed already answers its close again, even after a later one.
            stands = value in closed_years or (
                (closed is None or closed < value)
                and approved is not None
                and approved >= date(value, 12, 31)
            )
            write = ("SELECT trust_close_year(%s, %s)", (pmc.pmc_id, value))
        else:
            day = FIRST + timedelta(days=value)
            stands = closed is None or day > date(closed, 12, 31)
        try:
            # Named outside the transaction, so a refusal leaves nothing to undo in the session.
            with acting_as(conn, closer), conn.transaction():
                if kind == "close":
                    conn.execute(*write)
                elif kind == "charge":
                    charge(conn, pmc.pmc_id, lease, day, "10.00", "fee", "Fee")
                else:
                    reference = f"B-{bills}-{value}"
                    due = day + timedelta(days=10)
                    account = pmc.owners[0].account
                    enter_bill(
                        conn, pmc.pmc_id, pmc.vendor_id, account, reference, day, due, "20.00"
                    )
            took = True
        except psycopg.errors.CheckViolation:
            took = False
        event(f"{kind} {'stands' if stands else 'refused'}")
        assert took == stands, (kind, value, approved, closed)
        if took and kind == "close":
            closed_years.add(value)
            closed = max(closed_years)
        elif took and kind == "charge":
            charges += 1
        elif took:
            bills += 1

        until = conn.execute("SELECT trust_closed_until(%s)", (pmc.pmc_id,)).fetchone()[0]
        assert until == (None if closed is None else date(closed, 12, 31))
        assert conn.execute(
            "SELECT count(*) FROM trust_charges WHERE lease_id = %s", (lease,)
        ).fetchone() == (charges,)
        assert conn.execute(
            "SELECT count(*) FROM trust_bills WHERE pmc_id = %s", (pmc.pmc_id,)
        ).fetchone() == (bills,)


def test_a_closed_year_takes_no_charge_or_bill_and_closes_only_when_approved(database_url):
    with psycopg.connect(database_url, autocommit=True) as conn:
        check(conn)
