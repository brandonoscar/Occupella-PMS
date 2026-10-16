"""Property-based tests for the run that posts rent by itself (migration 20261016000067,
src/pms/nightly.py).

Hypothesis builds two identical companies: random leases (some starting later in the run's month,
some ended, some to come), monthly charges on some, and late fee terms or none. The nightly run
(`trust_run_rent` through `nightly.run_pmc`) on a random day of a random month runs in one; in the
other, the three runs it stands for are called one by one: rent and recurring charges due on the
month's first day, and late fees as of the day. Then:
  - the run charges exactly what the three runs charge, lease for lease, and says so in its
    record, which names nobody;
  - run again on the same day, it charges nothing new;
  - a staff member named without admin is refused, and nothing is charged.
Run more examples locally with HYPOTHESIS_PROFILE=nightly.
"""

from datetime import date, timedelta
from decimal import Decimal

import psycopg
from helpers import (
    add_late_fee_policy,
    add_recurring_charge,
    add_staff,
    add_tenant,
    add_unit,
    give_roles,
    make_pmc,
    open_lease,
)
from hypothesis import event, given
from hypothesis import strategies as st

from pms import nightly

URL = "http://localhost:8000"
CENTS = st.integers(min_value=1, max_value=400_000).map(lambda c: Decimal(c) / 100)
LEASES = st.lists(
    st.fixed_dictionaries(
        {
            # From the run's month's first day: before it, in it, or after it.
            "starts": st.integers(min_value=-70, max_value=40),
            "days": st.none() | st.integers(min_value=0, max_value=120),
            "rent": CENTS,
            # A monthly charge: none, or its amount and when it starts, from the lease's start.
            "recurring": st.none() | st.tuples(CENTS, st.integers(min_value=0, max_value=60)),
        }
    ),
    min_size=1,
    max_size=4,
)
LATE_FEES = st.none() | st.tuples(
    st.integers(min_value=0, max_value=10), st.integers(min_value=0, max_value=10_000)
)


def build(conn, month, leases, late_fees):
    """A company with the leases, in order, on units of one property."""
    pmc = make_pmc(conn)
    prop = pmc.owners[0].property_id
    made = []
    for n, lease in enumerate(leases):
        unit = add_unit(conn, pmc.pmc_id, prop, f"Unit {n}")
        tenant = add_tenant(conn, pmc.pmc_id, prop, f"Tenant {n}")
        starts = month + timedelta(days=lease["starts"])
        ends = None if lease["days"] is None else starts + timedelta(days=lease["days"])
        lease_id = open_lease(conn, pmc.pmc_id, unit, starts, ends, str(lease["rent"]), [tenant])
        if lease["recurring"] is not None:
            amount, after = lease["recurring"]
            add_recurring_charge(
                conn, pmc.pmc_id, lease_id, str(amount), starts + timedelta(days=after)
            )
        made.append(lease_id)
    if late_fees is not None:
        grace, flat = late_fees
        add_late_fee_policy(
            conn, pmc.pmc_id, prop, month - timedelta(days=90), grace, str(Decimal(flat) / 100)
        )
    return pmc, made


def charged(conn, leases):
    """Each lease's charges, by its place in the list."""
    found = []
    for n, lease_id in enumerate(leases):
        found += [
            (n, *row)
            for row in conn.execute(
                "SELECT due_on, kind, amount, memo FROM trust_charges WHERE lease_id = %s"
                " ORDER BY due_on, kind, amount, memo",
                (lease_id,),
            ).fetchall()
        ]
    return found


def one(conn, query, params):
    row = conn.execute(query, params).fetchone()
    assert row is not None
    return row[0]


@given(
    months=st.integers(min_value=0, max_value=23),
    day=st.integers(min_value=1, max_value=28),
    leases=LEASES,
    late_fees=LATE_FEES,
    by_hand_first=st.booleans(),
)
def check(conn, app_conn, months, day, leases, late_fees, by_hand_first):
    month = date(2026 + months // 12, 1 + months % 12, 1)
    on = month.replace(day=day)
    ours, our_leases = build(conn, month, leases, late_fees)
    twin, twin_leases = build(conn, month, leases, late_fees)

    if by_hand_first:
        # A staff member who isn't an admin, named: refused, and nothing is charged.
        clerk = add_staff(conn, ours.pmc_id, "Clerk")
        give_roles(conn, ours.pmc_id, add_staff(conn, ours.pmc_id, "Admin"), ["admin"])
        give_roles(conn, ours.pmc_id, clerk, ["approve"])
        refused = False
        with app_conn.transaction():
            app_conn.execute("SELECT set_config('trust.staff_id', %s, true)", (str(clerk),))
            try:
                with app_conn.transaction():
                    app_conn.execute("SELECT trust_run_rent(%s, %s, %s)", (ours.pmc_id, month, on))
            except psycopg.errors.InsufficientPrivilege:
                refused = True
        assert refused
        assert charged(conn, our_leases) == []

    ran = nightly.run_pmc(app_conn, ours.pmc_id, on, URL)
    rents = one(app_conn, "SELECT trust_charge_rent_due(%s, %s)", (twin.pmc_id, month))
    recurring = one(app_conn, "SELECT trust_charge_recurring_due(%s, %s)", (twin.pmc_id, month))
    late = one(app_conn, "SELECT trust_assess_late_fees(%s, %s)", (twin.pmc_id, on))

    assert (ran.rents, ran.recurring, ran.late_fees, ran.bills) == (rents, recurring, late, 0)
    assert charged(conn, our_leases) == charged(conn, twin_leases)
    event(f"rents charged: {min(rents, 2)}+")
    event("late fees charged" if late else "no late fee")

    again = nightly.run_pmc(app_conn, ours.pmc_id, on, URL)
    assert (again.rents, again.recurring, again.late_fees, again.bills) == (0, 0, 0, 0)
    assert conn.execute(
        "SELECT due_on, late_fees_as_of, rents, recurring, late_fees, recorded_by"
        " FROM trust_rent_runs WHERE pmc_id = %s ORDER BY created_at",
        (ours.pmc_id,),
    ).fetchall() == [(month, on, rents, recurring, late, None), (month, on, 0, 0, 0, None)]


def test_the_nightly_run_charges_what_the_three_runs_charge_and_once(database_url):
    with (
        psycopg.connect(database_url, autocommit=True) as conn,
        psycopg.connect(database_url, autocommit=True) as app_conn,
    ):
        app_conn.execute("SET ROLE trust_app")
        check(conn, app_conn)
