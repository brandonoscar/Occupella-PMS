"""Property-based tests for a late fee undone by a payment dated within grace (migration
20261115000320, docs/edge-cases.md D-078, D-079).

A check received on the last day of grace and recorded the next morning dated back, or a lockbox
line dated within grace brought in after the night, used to leave the night's late fee in place
until staff waived it. Now the payment works the fee out again as the night would have with it in
the books, and what would not have been charged is waived by itself. Hypothesis gives two
identical companies a lease, late fee terms (grace, flat, percent, maybe a cap) and February's
rent, and the same payments of parts of it at random moments before and after the grace ran out.
One records some of them before the night and the rest after it; the other records all of them
before the night. Then:
  - what the first is left owing of its late fee (the fee less what was waived of it) is exactly
    the fee the second was charged, or nothing when it was charged none;
  - each waiver is of the late fee, a credit of the company's day, saying why;
  - a run again charges nothing.
Run more examples locally with HYPOTHESIS_PROFILE=nightly.
"""

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import psycopg
from helpers import (
    add_late_fee_policy,
    add_unit,
    apply_payment,
    assess_late_fees,
    charge_rent_due,
    make_pmc,
    open_lease,
    transfer_batch,
)
from hypothesis import event, given
from hypothesis import strategies as st

JAN_1, FEB_1 = date(2026, 1, 1), date(2026, 2, 1)
RENT = Decimal("1500.00")
CENTS = st.integers(min_value=1, max_value=160_000).map(lambda c: Decimal(c).scaleb(-2))
# Hours from Feb 1, noon, and whether the payment reaches the books after the night.
PAYMENTS = st.lists(
    st.tuples(CENTS, st.integers(min_value=0, max_value=24 * 14), st.booleans()), max_size=6
)
TERMS = st.tuples(
    st.integers(min_value=0, max_value=10),
    st.integers(min_value=0, max_value=9_000).map(lambda c: Decimal(c).scaleb(-2)),
    st.sampled_from([Decimal("0"), Decimal("5"), Decimal("10")]),
    st.none() | st.sampled_from(["25.00", "120.00"]),
)


def company(conn, terms):
    pmc = make_pmc(conn)
    tenant = conn.execute(
        "SELECT tenant_id FROM trust_ledger_accounts WHERE ledger_account_id = %s",
        (pmc.prepaid_rent,),
    ).fetchone()[0]
    grace, flat, percent, maximum = terms
    prop = pmc.owners[0].property_id
    add_late_fee_policy(conn, pmc.pmc_id, prop, JAN_1, grace, str(flat), str(percent), maximum)
    lease = open_lease(
        conn, pmc.pmc_id, add_unit(conn, pmc.pmc_id, prop), JAN_1, None, RENT, [tenant]
    )
    charge_rent_due(conn, pmc.pmc_id, FEB_1)
    rent = conn.execute(
        "SELECT id FROM trust_charges WHERE lease_id = %s AND kind = 'rent'", (lease,)
    ).fetchone()[0]
    return pmc, lease, rent


def pay(conn, pmc, rent, amount, hours):
    when = datetime(2026, 2, 1, 12, tzinfo=UTC) + timedelta(hours=hours)
    (moved,) = transfer_batch(conn, [(pmc.operating_cash, pmc.owners[0].account, amount)], when)
    try:
        with conn.transaction():
            apply_payment(conn, pmc.pmc_id, rent, moved, amount)
    except psycopg.errors.CheckViolation:
        event("a payment past what was left refused")


def late_fee(conn, rent):
    """The late fee charged on the rent, and what of it was waived: (fee, waived) or None."""
    row = conn.execute(
        "SELECT f.fee, f.fee_charge_id FROM trust_late_fees AS f WHERE f.rent_charge_id = %s",
        (rent,),
    ).fetchone()
    if row is None:
        return None
    waived = conn.execute(
        "SELECT coalesce(sum(k.amount), 0) FROM trust_charge_waivers AS w"
        " JOIN trust_charges AS k ON k.id = w.credit_charge_id WHERE w.waived_charge_id = %s",
        (row[1],),
    ).fetchone()[0]
    return row[0], waived, row[1]


@given(terms=TERMS, payments=PAYMENTS)
def check(conn, terms, payments):
    ours, our_lease, our_rent = company(conn, terms)
    twin, _, twin_rent = company(conn, terms)
    night = date(2026, 2, 20)

    # Ours: some payments before the night, the rest after it; the twin's all before.
    for amount, hours, after in payments:
        if not after:
            pay(conn, ours, our_rent, amount, hours)
    for amount, hours, _after in sorted(payments, key=lambda p: p[2]):
        pay(conn, twin, twin_rent, amount, hours)
    assess_late_fees(conn, ours.pmc_id, night)
    assess_late_fees(conn, twin.pmc_id, night)
    for amount, hours, after in payments:
        if after:
            pay(conn, ours, our_rent, amount, hours)

    theirs = late_fee(conn, twin_rent)
    mine = late_fee(conn, our_rent)
    if mine is None:
        assert theirs is None
        event("no late fee")
    else:
        fee, waived, fee_charge = mine
        assert fee - waived == (Decimal(0) if theirs is None else theirs[0])
        event(
            "waived in full"
            if theirs is None and waived
            else "waived in part"
            if waived
            else "kept"
        )
        for kind, due_on, memo, reason in conn.execute(
            "SELECT k.kind, k.due_on, k.memo, w.reason FROM trust_charge_waivers AS w"
            " JOIN trust_charges AS k ON k.id = w.credit_charge_id"
            " WHERE w.waived_charge_id = %s",
            (fee_charge,),
        ).fetchall():
            assert kind == "credit"
            assert due_on == datetime.now(UTC).date()  # the company's day (UTC in the tests)
            assert memo.startswith("Waived: Late fee, rent due 2026-02-01")
            assert reason.startswith("Paid within its grace: a payment dated ")
    assert assess_late_fees(conn, ours.pmc_id, night) == 0
    assert our_lease is not None


def test_a_payment_dated_in_grace_leaves_the_late_fee_the_night_would_have_charged(database_url):
    with psycopg.connect(database_url, autocommit=True) as conn:
        check(conn)
