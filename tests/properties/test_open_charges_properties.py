"""Property-based tests for one lease's open charges (migration 20261028000200).

trust_open_charges says what is open of each charge of a PMC's leases at a moment, or of one
lease's. Since migration 20261028000200 the two read different rows: a lease's charges by its
index, or every charge of the PMC. Hypothesis gives a PMC two or three leases, each with random
charges, then pays them (oldest open first, as the app does), waives parts of them, adds plain
credits and bounces payments, on any lease and in any order. At the end, at a moment in the
middle and far ahead:
  - each lease's open charges are exactly the PMC's open charges of that lease's charges;
  - together the leases' open charges are all of the PMC's, none twice;
  - what is open of a lease's charges is what it owes (`trust_lease_owed`), far ahead.

Run more examples locally with HYPOTHESIS_PROFILE=nightly.
"""

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import psycopg
from helpers import (
    add_unit,
    apply_payment,
    charge,
    make_pmc,
    open_lease,
    reverse_payment,
    transfer_batch,
)
from hypothesis import event, given
from hypothesis import strategies as st

JAN_1 = date(2026, 1, 1)
CENTS = st.integers(min_value=1, max_value=60_000).map(lambda c: Decimal(c).scaleb(-2))
LEASE = st.integers(min_value=0, max_value=2)
ACTIONS = st.lists(
    st.one_of(
        st.tuples(st.just("pay"), LEASE, CENTS),
        st.tuples(st.just("waive"), LEASE, st.integers(min_value=0, max_value=3), CENTS),
        st.tuples(st.just("credit"), LEASE, CENTS),
        st.tuples(st.just("bounce"), st.integers(min_value=0, max_value=10)),
    ),
    max_size=12,
)


def at(n: int) -> datetime:
    return datetime(2026, 1, 1, 12, tzinfo=UTC) + timedelta(hours=n)


def open_of(conn, pmc_id, lease, moment) -> dict:
    """charge -> what is open of it at the moment, of one lease's charges or (None) the PMC's."""
    return dict(
        conn.execute(
            "SELECT o.charge_id, o.open FROM trust_open_charges(%s, %s, %s) AS o",
            (pmc_id, lease, moment),
        ).fetchall()
    )


@given(
    amounts=st.lists(st.lists(CENTS, min_size=1, max_size=3), min_size=2, max_size=3),
    actions=ACTIONS,
    middle=st.integers(min_value=0, max_value=14),
)
def check(conn, amounts, actions, middle):
    pmc = make_pmc(conn)
    tenant = conn.execute(
        "SELECT tenant_id FROM trust_ledger_accounts WHERE ledger_account_id = %s",
        (pmc.prepaid_rent,),
    ).fetchone()[0]
    leases, charges = [], {}
    for n, fees in enumerate(amounts):
        unit = add_unit(conn, pmc.pmc_id, pmc.owners[0].property_id, f"Unit {n + 1}")
        lease = open_lease(conn, pmc.pmc_id, unit, JAN_1, None, "1000.00", [tenant])
        leases.append(lease)
        charges[lease] = [
            charge(conn, pmc.pmc_id, lease, JAN_1 + timedelta(days=d), fee, "fee", f"Fee {d}")
            for d, fee in enumerate(fees)
        ]
    matches: list[tuple[str, object, Decimal]] = []  # (transfer, charge, amount) not bounced
    step = 0
    for action in actions:
        step += 1
        if action[0] == "bounce":
            if matches:
                moved, c, part = matches.pop(action[1] % len(matches))
                (back,) = transfer_batch(
                    conn, [(pmc.owners[0].account, pmc.operating_cash, part)], at(step)
                )
                reverse_payment(conn, pmc.pmc_id, c, moved, back, part)
                event("bounced")
            continue
        lease = leases[action[1] % len(leases)]
        if action[0] == "pay":
            left = action[2]
            opened = open_of(conn, pmc.pmc_id, lease, "infinity")
            for c in charges[lease]:  # oldest first, as the app pays
                part = min(opened[c], left)
                if part <= 0:
                    continue
                (moved,) = transfer_batch(
                    conn, [(pmc.operating_cash, pmc.owners[0].account, part)], at(step)
                )
                apply_payment(conn, pmc.pmc_id, c, moved, part)
                matches.append((moved, c, part))
                left -= part
        elif action[0] == "waive":
            _, _, index, amount = action
            waived = charges[lease][index % len(charges[lease])]
            try:
                with conn.transaction():
                    credit = charge(conn, pmc.pmc_id, lease, JAN_1, amount, "credit", "Waived")
                    conn.execute(
                        "INSERT INTO trust_charge_waivers (credit_charge_id, pmc_id,"
                        " waived_charge_id, reason) VALUES (%s, %s, %s, 'Courtesy')",
                        (credit, pmc.pmc_id, waived),
                    )
                event("waived")
            except psycopg.errors.CheckViolation:
                event("waiver refused")
        else:
            charge(conn, pmc.pmc_id, lease, JAN_1, action[2], "credit", "Concession")

    for moment in (at(middle), at(99), "infinity"):
        every = open_of(conn, pmc.pmc_id, None, moment)
        seen: set = set()
        for lease in leases:
            own = open_of(conn, pmc.pmc_id, lease, moment)
            assert set(own) >= set(charges[lease])
            assert own == {c: left for c, left in every.items() if c in own}
            assert not seen & set(own)
            seen |= set(own)
        assert seen == set(every)
    for lease in leases:
        owed = conn.execute("SELECT trust_lease_owed(%s)", (lease,)).fetchone()[0]
        assert sum(open_of(conn, pmc.pmc_id, lease, "infinity").values(), Decimal(0)) == owed


def test_each_leases_open_charges_are_the_pmcs_of_its_charges(database_url):
    with psycopg.connect(database_url, autocommit=True) as conn:
        check(conn)
