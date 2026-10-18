"""Property-based tests for waiving a charge (migration 20261018000079).

Hypothesis gives a lease random charges and then pays them (oldest open first, as the app does),
waives parts of them, adds plain credits and bounces payments, in any order. A model keeps what
was paid (net of bounces) and waived of each charge:
  - a waiver is written exactly when it is no more than is left of its charge once payments and
    earlier waivers count; a payment never matches past that either;
  - a charge's waivers come off it first: what is open of a charge is never more than its amount
    less what was paid and waived of it, and never below zero;
  - what is open of all the lease's charges together is what the lease owes
    (`trust_lease_owed`), and what the delinquency report says it owes far ahead.

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
ACTIONS = st.lists(
    st.one_of(
        st.tuples(st.just("pay"), CENTS),
        st.tuples(st.just("waive"), st.integers(min_value=0, max_value=5), CENTS),
        st.tuples(st.just("credit"), CENTS),
        st.tuples(st.just("bounce"), st.integers(min_value=0, max_value=10)),
    ),
    max_size=12,
)


def at(n: int) -> datetime:
    return datetime(2026, 1, 1, 12, tzinfo=UTC) + timedelta(hours=n)


@given(amounts=st.lists(CENTS, min_size=1, max_size=4), actions=ACTIONS)
def check(conn, amounts, actions):
    pmc = make_pmc(conn)
    tenant = conn.execute(
        "SELECT tenant_id FROM trust_ledger_accounts WHERE ledger_account_id = %s",
        (pmc.prepaid_rent,),
    ).fetchone()[0]
    unit = add_unit(conn, pmc.pmc_id, pmc.owners[0].property_id)
    lease = open_lease(conn, pmc.pmc_id, unit, JAN_1, None, "1000.00", [tenant])
    charges = [
        (
            charge(conn, pmc.pmc_id, lease, JAN_1 + timedelta(days=n), amount, "fee", f"Fee {n}"),
            amount,
        )
        for n, amount in enumerate(amounts)
    ]
    paid = {c: Decimal(0) for c, _ in charges}
    waived = {c: Decimal(0) for c, _ in charges}
    matches: list[tuple[str, object, Decimal]] = []  # (transfer, charge, amount) not bounced
    step = 0
    for action in actions:
        step += 1
        if action[0] == "pay":
            left = action[1]
            opened = dict(
                conn.execute(
                    "SELECT o.charge_id, o.open FROM trust_open_charges(%s, %s, 'infinity') AS o",
                    (pmc.pmc_id, lease),
                ).fetchall()
            )
            for c, _ in charges:  # oldest first, as the app pays
                part = min(opened[c], left)
                if part <= 0:
                    continue
                (moved,) = transfer_batch(
                    conn, [(pmc.operating_cash, pmc.owners[0].account, part)], at(step)
                )
                apply_payment(conn, pmc.pmc_id, c, moved, part)
                paid[c] += part
                matches.append((moved, c, part))
                left -= part
            continue
        if action[0] == "waive":
            _, index, amount = action
            c, full = charges[index % len(charges)]
            fits = paid[c] + waived[c] + amount <= full
            try:
                with conn.transaction():
                    credit = charge(conn, pmc.pmc_id, lease, JAN_1, amount, "credit", "Waived")
                    conn.execute(
                        "INSERT INTO trust_charge_waivers (credit_charge_id, pmc_id,"
                        " waived_charge_id, reason) VALUES (%s, %s, %s, 'Courtesy')",
                        (credit, pmc.pmc_id, c),
                    )
            except psycopg.errors.CheckViolation:
                assert not fits
                event("waiver refused")
                continue
            assert fits
            waived[c] += amount
            event("waived")
            continue
        if action[0] == "credit":
            charge(conn, pmc.pmc_id, lease, JAN_1, action[1], "credit", "Concession")
            continue
        if matches:
            moved, c, part = matches.pop(action[1] % len(matches))
            (back,) = transfer_batch(
                conn, [(pmc.owners[0].account, pmc.operating_cash, part)], at(step)
            )
            reverse_payment(conn, pmc.pmc_id, c, moved, back, part)
            paid[c] -= part
            event("bounced")

    opened = dict(
        conn.execute(
            "SELECT o.charge_id, o.open FROM trust_open_charges(%s, %s, 'infinity') AS o",
            (pmc.pmc_id, lease),
        ).fetchall()
    )
    for c, amount in charges:
        assert 0 <= opened[c] <= amount - paid[c] - waived[c]
    owed = conn.execute("SELECT trust_lease_owed(%s)", (lease,)).fetchone()[0]
    assert sum(opened.values(), Decimal(0)) == owed
    reported = conn.execute(
        "SELECT total FROM trust_report_delinquency(%s, %s) WHERE item = 'lease'",
        (pmc.pmc_id, date(2027, 1, 1)),
    ).fetchone()
    assert (reported[0] if reported else Decimal(0)) == owed
    # A payment never matches past what was paid and waived of a charge.
    for c, amount in charges:
        if paid[c] + waived[c] == amount:
            (moved,) = transfer_batch(
                conn, [(pmc.operating_cash, pmc.owners[0].account, Decimal("0.01"))], at(99)
            )
            try:
                with conn.transaction():
                    apply_payment(conn, pmc.pmc_id, c, moved, Decimal("0.01"))
            except psycopg.errors.CheckViolation:
                event("settled charge refused a payment")
            else:
                raise AssertionError("a payment matched past what was paid and waived")
            break


def test_a_waiver_comes_off_its_own_charge_and_never_past_it(database_url):
    with psycopg.connect(database_url, autocommit=True) as conn:
        check(conn)
