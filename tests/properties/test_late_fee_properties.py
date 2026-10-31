"""Property-based tests for which rents a late fee run charges (migration 20261031000200).

trust_assess_late_fees charges a rent still open at the end of its last day of grace its late
fee, once. Since migration 20261031000200 it passes over, before working out what is open, each
rent its own payments by then (less what of them bounced by then) covered. Hypothesis gives a
PMC two or three leases with a rent due on Feb 1 and terms of five days' grace, then pays each
rent in parts at random moments before and after its grace ran out, bounces some payments at
random moments after them, and adds credits and waivers due before or after. Then, as of a day
after the grace:
  - a rent is charged a late fee exactly when what is open of it at the end of its grace
    (trust_open_charges at that moment, worked out before the run) is above zero, and none
    otherwise;
  - each fee is the terms' flat fee plus their percent of what was open, to the cent, never
    over their maximum, and records what was open;
  - a run again charges nothing.

Run more examples locally with HYPOTHESIS_PROFILE=nightly.
"""

from datetime import UTC, date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal

import psycopg
from helpers import (
    add_late_fee_policy,
    add_unit,
    apply_payment,
    assess_late_fees,
    charge,
    charge_rent_due,
    make_pmc,
    open_lease,
    reverse_payment,
    transfer_batch,
)
from hypothesis import event, given
from hypothesis import strategies as st

JAN_1, FEB_1 = date(2026, 1, 1), date(2026, 2, 1)
GRACE_ENDS = datetime(2026, 2, 7, tzinfo=UTC)  # due Feb 1, five days' grace
RENT = Decimal("1500.00")
CENTS = st.integers(min_value=1, max_value=160_000).map(lambda c: Decimal(c).scaleb(-2))
# Hours from Feb 1, noon: up to 5 days and 11 hours is inside the grace, from 5.5 days late.
HOURS = st.integers(min_value=0, max_value=24 * 10)
LEASE = st.integers(min_value=0, max_value=2)
ACTIONS = st.lists(
    st.one_of(
        st.tuples(st.just("pay"), LEASE, CENTS, HOURS),
        st.tuples(st.just("bounce"), st.integers(min_value=0, max_value=10), HOURS),
        st.tuples(st.just("credit"), LEASE, CENTS, st.booleans()),
        st.tuples(st.just("waive"), LEASE, CENTS, st.booleans()),
    ),
    max_size=14,
)


def at(hours: int) -> datetime:
    return datetime(2026, 2, 1, 12, tzinfo=UTC) + timedelta(hours=hours)


@given(leases=st.integers(min_value=2, max_value=3), actions=ACTIONS)
def check(conn, leases, actions):
    pmc = make_pmc(conn)
    tenant = conn.execute(
        "SELECT tenant_id FROM trust_ledger_accounts WHERE ledger_account_id = %s",
        (pmc.prepaid_rent,),
    ).fetchone()[0]
    add_late_fee_policy(
        conn, pmc.pmc_id, pmc.owners[0].property_id, JAN_1, 5, "50", "10", maximum="175"
    )
    ids = []
    for n in range(leases):
        unit = add_unit(conn, pmc.pmc_id, pmc.owners[0].property_id, f"Unit {n + 1}")
        ids.append(open_lease(conn, pmc.pmc_id, unit, JAN_1, None, RENT, [tenant]))
    charge_rent_due(conn, pmc.pmc_id, FEB_1)
    rents = {
        lease: conn.execute(
            "SELECT id FROM trust_charges WHERE lease_id = %s AND kind = 'rent'", (lease,)
        ).fetchone()[0]
        for lease in ids
    }
    paid: list[tuple[object, str, Decimal, int]] = []  # (rent, transfer, amount, hour) unbounced
    for action in actions:
        if action[0] == "pay":
            _, n, amount, hour = action
            rent = rents[ids[n % len(ids)]]
            (moved,) = transfer_batch(
                conn, [(pmc.operating_cash, pmc.owners[0].account, amount)], at(hour)
            )
            try:
                with conn.transaction():
                    apply_payment(conn, pmc.pmc_id, rent, moved, amount)
            except psycopg.errors.CheckViolation:
                event("a payment past what was left refused")
                continue
            paid.append((rent, moved, amount, hour))
        elif action[0] == "bounce":
            if not paid:
                continue
            _, index, later = action
            rent, moved, amount, hour = paid.pop(index % len(paid))
            (back,) = transfer_batch(
                conn, [(pmc.owners[0].account, pmc.operating_cash, amount)], at(hour + later)
            )
            reverse_payment(conn, pmc.pmc_id, rent, moved, back, amount)
            event("bounced")
        else:
            _, n, amount, early = action
            lease = ids[n % len(ids)]
            due = date(2026, 2, 3) if early else date(2026, 2, 12)
            try:
                with conn.transaction():
                    credit = charge(conn, pmc.pmc_id, lease, due, amount, "credit", "Concession")
                    if action[0] == "waive":
                        conn.execute(
                            "INSERT INTO trust_charge_waivers (credit_charge_id, pmc_id,"
                            " waived_charge_id, reason) VALUES (%s, %s, %s, 'Courtesy')",
                            (credit, pmc.pmc_id, rents[lease]),
                        )
            except psycopg.errors.CheckViolation:
                event("a waiver past what was left refused")

    # What is open of each rent at the end of its grace, as the run is to count it.
    open_then = {}
    for lease, rent in rents.items():
        opened = dict(
            conn.execute(
                "SELECT o.charge_id, o.open FROM trust_open_charges(%s, %s, %s) AS o",
                (pmc.pmc_id, lease, GRACE_ENDS),
            ).fetchall()
        )
        open_then[rent] = opened[rent]

    charged = assess_late_fees(conn, pmc.pmc_id, date(2026, 2, 20))

    fees = {
        rent: (unpaid, fee)
        for rent, unpaid, fee in conn.execute(
            "SELECT rent_charge_id, unpaid, fee FROM trust_late_fees WHERE pmc_id = %s",
            (pmc.pmc_id,),
        )
    }
    owed = {rent: left for rent, left in open_then.items() if left > 0}
    assert set(fees) == set(owed)
    assert charged == len(owed)
    for rent, left in owed.items():
        # Postgres rounds a half cent up (round(numeric)), not to even as Python's round does.
        fee = (Decimal("50") + left / 10).quantize(Decimal("0.01"), ROUND_HALF_UP)
        assert fees[rent] == (left, min(fee, Decimal("175")))
    event(f"{len(owed)} of {len(rents)} charged")
    assert assess_late_fees(conn, pmc.pmc_id, date(2026, 2, 21)) == 0


def test_a_rent_is_charged_a_late_fee_exactly_when_open_at_the_end_of_its_grace(database_url):
    with psycopg.connect(database_url, autocommit=True) as conn:
        check(conn)
