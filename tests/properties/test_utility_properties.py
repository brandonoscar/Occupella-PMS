"""Property-based tests for utility billing (migration 20261020000096, the founder's review q021).

Hypothesis gives a property one to five units (each with random square feet), each vacant or
leased for part or all of September with one to four people, billed for water or not; a plan
(by unit, square feet, occupants or submeter, with readings that come to no more than the master
meter's) and an owner's share; and a bill. A model works the shares out exactly, with fractions:
the tenants' pool is the bill less the owner's share, and each billed lease's share is its
weight's part of the pool rounded down to the cent. After billing it back:
  - each lease's share is the model's, and a lease not billed is charged nothing;
  - the shares never come to more than the pool, nor the pool to more than the bill;
  - each share above 0 is a fee charged to its lease, its balance up by exactly that;
  - a second bill for any day of the period is refused.

Run more examples locally with HYPOTHESIS_PROFILE=nightly.
"""

from datetime import date, timedelta
from decimal import Decimal
from fractions import Fraction

import psycopg
import pytest
from helpers import (
    add_tenant,
    add_unit,
    bill_back_utility,
    bill_lease_for,
    make_pmc,
    open_lease,
    plan_utility,
    set_area,
    utility_shares,
)
from hypothesis import event, given
from hypothesis import strategies as st

START, END = date(2026, 9, 1), date(2026, 9, 30)
DAYS = (END - START).days + 1


@st.composite
def homes(draw):
    """Each unit: its square feet, and its lease (None: vacant) as (start offset, days or None
    for no end, people, billed)."""
    count = draw(st.integers(min_value=1, max_value=5))
    units = []
    for _ in range(count):
        area = draw(st.integers(min_value=100, max_value=3000))
        lease = draw(
            st.one_of(
                st.none(),
                st.tuples(
                    st.integers(min_value=-20, max_value=29),
                    st.one_of(st.none(), st.integers(min_value=0, max_value=60)),
                    st.integers(min_value=1, max_value=4),
                    st.booleans(),
                ),
            )
        )
        units.append((area, lease))
    return units


def cents_down(value: Fraction) -> Decimal:
    return Decimal(value.numerator * 100 // value.denominator) / 100


@given(
    units=homes(),
    method=st.sampled_from(["units", "square_feet", "occupants", "submeter"]),
    owner_cents=st.integers(min_value=0, max_value=5000),  # 0.00% to 50.00%
    amount_cents=st.integers(min_value=1, max_value=2_000_000),
    readings=st.lists(st.integers(min_value=0, max_value=500), min_size=5, max_size=5),
    spare=st.integers(min_value=0, max_value=500),
)
def check(conn, app, units, method, owner_cents, amount_cents, readings, spare):
    pmc = make_pmc(conn)
    prop = pmc.owners[0].property_id
    owner_percent = Decimal(owner_cents) / 100
    amount = Decimal(amount_cents) / 100
    plan_utility(conn, pmc.pmc_id, prop, "water", START, method, owner_percent)

    leases = []  # (lease, unit index, days in the period, people, billed)
    for n, (area, lease) in enumerate(units):
        unit = add_unit(conn, pmc.pmc_id, prop, f"Unit {n + 1}")
        set_area(conn, pmc.pmc_id, unit, area)
        if lease is None:
            continue
        offset, length, people, billed = lease
        starts = START + timedelta(days=offset)
        ends = None if length is None else starts + timedelta(days=length)
        if ends is not None and ends < START:
            continue
        tenants = [add_tenant(conn, pmc.pmc_id, prop, f"Tenant {n}-{k}") for k in range(people)]
        made = open_lease(conn, pmc.pmc_id, unit, starts, ends, "1000.00", tenants)
        if billed:
            bill_lease_for(conn, pmc.pmc_id, made, "water", min(starts, START))
        days = (min(END, ends or END) - max(START, starts)).days + 1
        leases.append((made, n, days, people, billed))
    event(f"{method}, {len(units)} units, {len(leases)} leased")

    # The model.
    usage = {lease: readings[n] for lease, n, *_ in leases}
    weight = {
        lease: {
            "units": Fraction(days),
            "square_feet": Fraction(units[n][0] * days),
            "occupants": Fraction(people * days),
            "submeter": Fraction(usage[lease]),
        }[method]
        for lease, n, days, people, _ in leases
    }
    master = sum(usage.values()) + spare
    total = {
        "units": Fraction(len(units) * DAYS),
        "square_feet": Fraction(sum(area for area, _ in units) * DAYS),
        "occupants": sum(weight.values(), Fraction(0)),
        "submeter": Fraction(master),
    }[method]
    pool = Fraction(amount_cents, 100) * (100 - Fraction(owner_cents, 100)) / 100
    want = {
        lease: cents_down(pool * weight[lease] / total) if billed and total > 0 else Decimal(0)
        for lease, _, _, _, billed in leases
    }
    if method == "submeter" and master == 0:
        event("no reading: the master meter read nothing")
        return

    owed_before = {
        lease: app.execute("SELECT trust_lease_owed(%s)", (lease,)).fetchone()[0]
        for lease, *_ in leases
    }
    bill = bill_back_utility(
        app,
        pmc.pmc_id,
        prop,
        "water",
        START,
        END,
        amount,
        date(2026, 10, 5),
        master_usage=master if method == "submeter" else None,
        usage=usage if method == "submeter" else None,
    )

    shares = utility_shares(conn, bill)
    assert {lease: share[2] for lease, share in shares.items()} == want
    assert sum(want.values(), Decimal(0)) <= pool <= amount
    for lease, _, _, _, billed in leases:
        assert shares[lease][0] == billed
        owed = app.execute("SELECT trust_lease_owed(%s)", (lease,)).fetchone()[0]
        assert owed == owed_before[lease] + want[lease]
        charged = conn.execute(
            "SELECT count(*), coalesce(sum(c.amount), 0) FROM trust_utility_shares AS s"
            " JOIN trust_charges AS c ON c.id = s.charge_id"
            " WHERE s.utility_bill_id = %s AND s.lease_id = %s AND c.kind = 'fee'",
            (bill, lease),
        ).fetchone()
        assert charged == ((1, want[lease]) if want[lease] > 0 else (0, Decimal(0)))
    event("someone charged" if any(want.values()) else "no one charged")

    # Any day of the period again is refused.
    with pytest.raises(psycopg.errors.UniqueViolation):
        bill_back_utility(
            app,
            pmc.pmc_id,
            prop,
            "water",
            END,
            END + timedelta(days=30),
            "1.00",
            END,
            master_usage=1 if method == "submeter" else None,
        )


def test_a_utility_bill_shares_to_the_cent_and_never_past_the_bill(database_url):
    with (
        psycopg.connect(database_url, autocommit=True) as conn,
        psycopg.connect(database_url, autocommit=True) as app,
    ):
        app.execute("SET ROLE trust_app")
        check(conn, app)
