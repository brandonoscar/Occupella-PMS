"""Property-based tests for the grace of a rent charged late (migration 20261115000310,
docs/edge-cases.md D-083).

A rent first charged after its own grace had run out (a lease entered late, a month the nightly
run catches up, an admin's Run rent now for an earlier month) used to be charged its late fee the
same night, before the tenant could see it. Now its grace counts from the day the run charged it.
Hypothesis gives a company leases due on the first, some starting later in the month, and late fee
terms; its first night falls on a random day of the month, so its rents are charged on time, in
their grace or after it. The tenants pay parts at random moments, and a later night comes. Then:
  - the first night charges no late fee on a rent it charged;
  - a rent charged after its due day is noted with the run's day, once; one charged on or before
    it is not;
  - each rent's grace ends its grace days after its due day, or, charged after that had passed,
    after the day it was charged, and its fee (the terms' flat fee) is charged on the later night
    exactly when its own payments by the end of that grace left it open and that end has passed,
    due the day after it.
Run more examples locally with HYPOTHESIS_PROFILE=nightly.
"""

from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal

import psycopg
from helpers import (
    add_late_fee_policy,
    add_tenant,
    add_unit,
    apply_payment,
    make_pmc,
    open_lease,
    transfer_batch,
)
from hypothesis import event, given
from hypothesis import strategies as st

from pms import nightly

URL = "http://localhost:8000"
RENT = Decimal("900.00")
CENTS = st.integers(min_value=1, max_value=95_000).map(lambda c: Decimal(c).scaleb(-2))
LEASES = st.lists(st.integers(min_value=-60, max_value=0), min_size=1, max_size=3)
PAYMENTS = st.lists(st.tuples(st.integers(0, 2), CENTS, st.integers(0, 24 * 40)), max_size=6)


@given(
    months=st.integers(min_value=0, max_value=11),
    day=st.integers(min_value=1, max_value=28),
    grace=st.integers(min_value=0, max_value=10),
    later=st.integers(min_value=0, max_value=27),
    leases=LEASES,
    payments=PAYMENTS,
)
def check(conn, app_conn, months, day, grace, later, leases, payments):
    month = date(2026, 1 + months, 1)
    first_night = month.replace(day=day)
    pmc = make_pmc(conn)
    prop = pmc.owners[0].property_id
    add_late_fee_policy(conn, pmc.pmc_id, prop, month - timedelta(days=90), grace, "45.00")
    ids = []
    for n, starts in enumerate(leases):
        unit = add_unit(conn, pmc.pmc_id, prop, f"Unit {n}")
        tenant = add_tenant(conn, pmc.pmc_id, prop, f"Tenant {n}")
        ids.append(
            open_lease(
                conn, pmc.pmc_id, unit, month + timedelta(days=starts), None, str(RENT), [tenant]
            )
        )

    first = nightly.run_pmc(app_conn, pmc.pmc_id, first_night, URL)
    rents = [
        conn.execute(
            "SELECT id, due_on FROM trust_charges WHERE lease_id = %s AND kind = 'rent'", (lease,)
        ).fetchone()
        for lease in ids
    ]
    assert first.late_fees == 0  # none on a rent the night charged

    paid: list[tuple[object, datetime, Decimal]] = []
    for n, amount, hours in payments:
        rent, due = rents[n % len(rents)]
        when = datetime.combine(due, time(12), UTC) + timedelta(hours=hours)
        (moved,) = transfer_batch(conn, [(pmc.operating_cash, pmc.owners[0].account, amount)], when)
        try:
            with conn.transaction():
                apply_payment(conn, pmc.pmc_id, rent, moved, amount)
            paid.append((rent, when, amount))
        except psycopg.errors.CheckViolation:
            event("a payment past what was left refused")

    second_night = first_night + timedelta(days=later)
    nightly.run_pmc(app_conn, pmc.pmc_id, second_night, URL)

    noted = dict(
        conn.execute(
            "SELECT rent_charge_id, charged_on FROM trust_rents_charged_late WHERE pmc_id = %s",
            (pmc.pmc_id,),
        ).fetchall()
    )
    fees = dict(
        conn.execute(
            "SELECT f.rent_charge_id, c.due_on FROM trust_late_fees AS f"
            " JOIN trust_charges AS c ON c.id = f.fee_charge_id WHERE f.pmc_id = %s",
            (pmc.pmc_id,),
        ).fetchall()
    )
    for rent, due in rents:
        if due < first_night:
            assert noted.get(rent) == first_night
        else:
            assert rent not in noted
        late = due + timedelta(days=grace) < first_night
        event("charged after its grace" if late else "charged in time")
        ends = (first_night if late else due) + timedelta(days=grace)
        cutoff = datetime.combine(ends + timedelta(days=1), time(0), UTC)
        unpaid = RENT - sum((a for r, w, a in paid if r == rent and w < cutoff), Decimal(0))
        if ends < second_night and unpaid > 0:
            assert fees.get(rent) == ends + timedelta(days=1)
            event("late fee")
        else:
            assert rent not in fees


def test_a_rent_charged_after_its_grace_takes_its_grace_from_that_day(database_url):
    with (
        psycopg.connect(database_url, autocommit=True) as conn,
        psycopg.connect(database_url, autocommit=True) as app_conn,
    ):
        app_conn.execute("SET ROLE trust_app")
        check(conn, app_conn)
