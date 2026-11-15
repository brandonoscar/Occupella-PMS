"""Property-based tests for the nights a server missed (src/pms/nightly.py, `catch_up`;
docs/edge-cases.md D-016, D-017, H-089).

Hypothesis builds two identical companies: random leases, each due on the first or on a day of
its own (1 to 28), some with a monthly charge, and late fee terms or none. Both run on a random
day; then one runs every night up to a later day in a later month, while the other's server was
down in between and runs only on that day. Then:
  - the one night back charges exactly what the nights every day charged, lease for lease: no
    rent or monthly charge due on a night nobody ran is lost; but a rent first charged that night
    after its grace had run out takes its grace from that night (migration 20261115000310,
    D-083), so it has no late fee yet where a night every day charged one;
  - run again on the same day, it charges nothing new.
Run more examples locally with HYPOTHESIS_PROFILE=nightly.
"""

from datetime import date, timedelta
from decimal import Decimal

import psycopg
from helpers import (
    add_late_fee_policy,
    add_recurring_charge,
    add_tenant,
    add_unit,
    make_pmc,
    open_lease,
)
from hypothesis import event, given
from hypothesis import strategies as st

from pms import nightly

URL = "http://localhost:8000"
CENTS = st.integers(min_value=100, max_value=400_000).map(lambda c: Decimal(c) / 100)
LEASES = st.lists(
    st.fixed_dictionaries(
        {
            # From the first night's day: before it, or between the nights.
            "starts": st.integers(min_value=-60, max_value=30),
            "due_day": st.integers(min_value=1, max_value=28),
            "rent": CENTS,
            "recurring": st.none() | CENTS,
        }
    ),
    min_size=1,
    max_size=3,
)


def build(conn, first, leases, late_fees):
    pmc = make_pmc(conn)
    prop = pmc.owners[0].property_id
    made = []
    for n, lease in enumerate(leases):
        unit = add_unit(conn, pmc.pmc_id, prop, f"Unit {n}")
        tenant = add_tenant(conn, pmc.pmc_id, prop, f"Tenant {n}")
        starts = first + timedelta(days=lease["starts"])
        lease_id = open_lease(conn, pmc.pmc_id, unit, starts, None, str(lease["rent"]), [tenant])
        if lease["due_day"] != 1:
            conn.execute(
                "INSERT INTO trust_rent_due_days (pmc_id, lease_id, starts_on, due_day)"
                " VALUES (%s, %s, %s, %s)",
                (pmc.pmc_id, lease_id, starts.replace(day=1), lease["due_day"]),
            )
        if lease["recurring"] is not None:
            add_recurring_charge(conn, pmc.pmc_id, lease_id, str(lease["recurring"]), starts)
        made.append(lease_id)
    if late_fees is not None:
        add_late_fee_policy(conn, pmc.pmc_id, prop, first - timedelta(days=120), late_fees, "25.00")
    return pmc, made


def charged(conn, leases):
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


@given(
    first=st.dates(min_value=date(2026, 1, 1), max_value=date(2027, 12, 31)),
    down=st.integers(min_value=1, max_value=40),
    leases=LEASES,
    late_fees=st.none() | st.integers(min_value=0, max_value=10),
)
def check(conn, app_conn, first, down, leases, late_fees):
    back = first + timedelta(days=down)
    if back.replace(day=1) == first.replace(day=1):
        back = (first.replace(day=1) + timedelta(days=32)).replace(day=1) + timedelta(days=down % 5)
    ours, our_leases = build(conn, first, leases, late_fees)
    twin, twin_leases = build(conn, first, leases, late_fees)

    nightly.run_pmc(app_conn, ours.pmc_id, first, URL)
    day = first
    while day <= back:
        nightly.run_pmc(app_conn, twin.pmc_id, day, URL)
        day += timedelta(days=1)
    ran = nightly.run_pmc(app_conn, ours.pmc_id, back, URL)

    # The rents the night back charged after their grace ran out, by lease and due day: their
    # grace runs from the night back, so none has its late fee yet (D-083).
    fresh = {
        (our_leases.index(lease), due_on)
        for lease, due_on, charged_on in conn.execute(
            "SELECT c.lease_id, c.due_on, l.charged_on FROM trust_rents_charged_late AS l"
            " JOIN trust_charges AS c ON c.id = l.rent_charge_id WHERE l.pmc_id = %s",
            (ours.pmc_id,),
        ).fetchall()
        if late_fees is not None
        and charged_on == back
        and charged_on > due_on + timedelta(days=late_fees)
    }
    if fresh:
        event("a rent charged the night back after its grace: its grace from then")

    def late_fee_for(row):
        n, _, kind, _, memo = row
        return (
            kind == "fee"
            and memo is not None
            and memo.startswith("Late fee, rent due ")
            and (
                n,
                date.fromisoformat(memo.removeprefix("Late fee, rent due ")),
            )
            in fresh
        )

    ours_charged = charged(conn, our_leases)
    assert not any(late_fee_for(row) for row in ours_charged)
    assert ours_charged == [row for row in charged(conn, twin_leases) if not late_fee_for(row)]
    event(f"months missed: {(back.year - first.year) * 12 + back.month - first.month}")
    event("rent charged on the night back" if ran.rents else "no rent on the night back")

    again = nightly.run_pmc(app_conn, ours.pmc_id, back, URL)
    assert (again.rents, again.recurring, again.late_fees) == (0, 0, 0)


def test_a_night_after_the_server_was_down_charges_what_every_night_would(database_url):
    with (
        psycopg.connect(database_url, autocommit=True) as conn,
        psycopg.connect(database_url, autocommit=True) as app_conn,
    ):
        app_conn.execute("SET ROLE trust_app")
        check(conn, app_conn)
