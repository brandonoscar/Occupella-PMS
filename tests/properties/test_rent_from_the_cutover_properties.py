"""Property-based tests for rent from the cutover (migration 20261106100000; docs/edge-cases.md
M-144).

Hypothesis builds a company with random leases (starting before, on or after the cutover, some
due on another day of the month, some with a monthly charge), maybe opens its operating trust
bank account with the old system's balances on a random day, and runs the nightly run's rent
(`trust_run_rent` for the month of the day, as of it) for random days of two months.

Invariants (ci/registry.toml maps them to this test):
  - nothing is charged due before the cutover of the lease's property's trust bank account: that
    month is the old system's;
  - no month is missed: a lease in effect in a month a run was run for, whose rent comes due by
    that run on or after the cutover, has its rent in that month, once;
  - a company with no opening balances is charged as before;
  - running every day again charges nothing new.

Run more examples locally with HYPOTHESIS_PROFILE=nightly.
"""

from collections import Counter
from datetime import date, timedelta
from decimal import Decimal

import psycopg
from helpers import (
    add_recurring_charge,
    add_tenant,
    add_unit,
    make_pmc,
    open_lease,
    open_with_balances,
)
from hypothesis import event, given
from hypothesis import strategies as st

START = date(2026, 3, 1)  # the first of two months, March and April
RENT = st.integers(min_value=10_000, max_value=400_000).map(lambda c: Decimal(c) / 100)
LEASES = st.lists(
    st.fixed_dictionaries(
        {
            "starts": st.integers(min_value=-40, max_value=55),  # days from March 1
            "days": st.none() | st.integers(min_value=5, max_value=90),
            "rent": RENT,
            "due_day": st.just(1) | st.integers(min_value=2, max_value=28),
            "monthly": st.booleans(),
        }
    ),
    min_size=1,
    max_size=4,
)
CUTOVER = st.none() | st.integers(min_value=0, max_value=60)  # days from March 1, or no switch
DAYS = st.lists(st.integers(min_value=0, max_value=60), min_size=1, max_size=6)


def month_of(day):
    return (day.year, day.month)


@given(leases=LEASES, cutover=CUTOVER, days=DAYS)
def check(conn, app_conn, leases, cutover, days):
    pmc = make_pmc(conn)
    prop = pmc.owners[0].property_id
    made = []
    for n, lease in enumerate(leases):
        unit = add_unit(conn, pmc.pmc_id, prop, f"Unit {n}")
        tenant = add_tenant(conn, pmc.pmc_id, prop, f"Tenant {n}")
        starts = START + timedelta(days=lease["starts"])
        ends = None if lease["days"] is None else starts + timedelta(days=lease["days"])
        lease_id = open_lease(conn, pmc.pmc_id, unit, starts, ends, str(lease["rent"]), [tenant])
        if lease["due_day"] != 1:
            conn.execute(
                "INSERT INTO trust_rent_due_days (pmc_id, lease_id, starts_on, due_day)"
                " VALUES (%s, %s, %s, %s)",
                (pmc.pmc_id, lease_id, starts.replace(day=1), lease["due_day"]),
            )
        if lease["monthly"]:
            add_recurring_charge(conn, pmc.pmc_id, lease_id, "25.00", starts)
        made.append((lease_id, lease["due_day"]))

    opened = None if cutover is None else START + timedelta(days=cutover)
    event("no switch" if opened is None else "a switch")
    if opened is not None:
        open_with_balances(
            conn,
            pmc.pmc_id,
            pmc.operating_bank_id,
            opened,
            {pmc.owners[0].account: "500.00"},
            "500.00",
        )

    ran = [START + timedelta(days=d) for d in days]
    for day in ran:
        app_conn.execute("SELECT trust_run_rent(%s, %s, %s)", (pmc.pmc_id, day.replace(day=1), day))

    def charges():
        return conn.execute(
            "SELECT lease_id, due_on, kind, recurring_charge_id FROM trust_charges"
            " WHERE pmc_id = %s",
            (pmc.pmc_id,),
        ).fetchall()

    found = charges()
    if opened is not None:
        early = [c for c in found if c[1] < opened]
        assert early == [], early
    rents = Counter((lease, month_of(due)) for lease, due, kind, _ in found if kind == "rent")
    assert all(n == 1 for n in rents.values()), rents
    for lease_id, due_day in made:
        for day in ran:
            due = conn.execute(
                "SELECT trust_rent_due_on(%s, %s)", (lease_id, day.replace(day=1))
            ).fetchone()[0]
            if due is None or (opened is not None and due < opened):
                continue
            if due_day != 1 and due > day:
                continue  # not due yet on that run
            event("a month the PMS charges")
            assert rents[(lease_id, month_of(day))] == 1, (lease_id, day, due)

    for day in ran:
        app_conn.execute("SELECT trust_run_rent(%s, %s, %s)", (pmc.pmc_id, day.replace(day=1), day))
    assert sorted(charges(), key=str) == sorted(found, key=str)


def test_rent_is_charged_from_the_cutover_and_never_for_the_old_systems_days(database_url):
    with (
        psycopg.connect(database_url, autocommit=True) as conn,
        psycopg.connect(database_url, autocommit=True) as app_conn,
    ):
        app_conn.execute("SET ROLE trust_app")
        check(conn, app_conn)
