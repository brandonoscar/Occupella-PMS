"""Property-based tests for late fee terms entered from a day gone by (migration 20261115000200,
docs/edge-cases.md M-049, D-082).

Hypothesis gives a lease six months of rent (each paid in full on a random day, or never) and the
property one to three sets of late fee terms, each in force from a random day and entered (on
file) on another random day, maybe long after it starts. Then one run as of a day after them all.
A model picks, for each rent, the terms in force on its due date among those on file by the day
its fee would come due (the day after its last day of grace):
  - a rent is charged a late fee exactly when such terms exist and it was not paid by the end of
    their grace, and the fee is theirs, dated that day: never under terms entered after it;
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
    charge,
    make_pmc,
    open_lease,
    transfer_batch,
)
from hypothesis import event, given
from hypothesis import strategies as st

JAN_1 = date(2026, 1, 1)
DUES = [date(2026, month, 1) for month in range(1, 7)]
RENT = Decimal("1000.00")
DAY = st.integers(min_value=0, max_value=210)  # days from Jan 1, into late July
TERMS = st.lists(
    st.tuples(DAY, DAY, st.integers(min_value=0, max_value=10)),  # starts, entered, grace
    min_size=1,
    max_size=3,
    unique_by=lambda t: t[0],
)
PAID = st.lists(st.none() | st.integers(min_value=0, max_value=20), min_size=6, max_size=6)


@given(terms=TERMS, paid=PAID)
def check(conn, terms, paid):
    pmc = make_pmc(conn)
    prop = pmc.owners[0].property_id
    tenant = conn.execute(
        "SELECT tenant_id FROM trust_ledger_accounts WHERE ledger_account_id = %s",
        (pmc.prepaid_rent,),
    ).fetchone()[0]
    lease = open_lease(
        conn, pmc.pmc_id, add_unit(conn, pmc.pmc_id, prop), JAN_1, None, RENT, [tenant]
    )
    made = []
    for n, (starts, entered, grace) in enumerate(terms):
        policy = add_late_fee_policy(
            conn,
            pmc.pmc_id,
            prop,
            JAN_1 + timedelta(days=starts),
            grace,
            str(10 + n),  # each terms' own flat fee
            entered_at=datetime.combine(JAN_1 + timedelta(days=entered), datetime.min.time(), UTC)
            + timedelta(hours=18),
        )
        made.append(
            (policy, JAN_1 + timedelta(days=starts), JAN_1 + timedelta(days=entered), grace)
        )
    rents = []
    for due, late in zip(DUES, paid, strict=True):
        rent = charge(conn, pmc.pmc_id, lease, due, RENT)
        on = None if late is None else due + timedelta(days=late)
        if on is not None:
            (moved,) = transfer_batch(
                conn,
                [(pmc.operating_cash, pmc.owners[0].account, RENT)],
                datetime.combine(on, datetime.min.time(), UTC) + timedelta(hours=12),
            )
            apply_payment(conn, pmc.pmc_id, rent, moved, RENT)
        rents.append((rent, due, on))

    assess_late_fees(conn, pmc.pmc_id, date(2026, 8, 1))

    for rent, due, on in rents:
        on_file = [
            (policy, starts, grace)
            for policy, starts, entered, grace in made
            if starts <= due and entered <= due + timedelta(days=grace + 1)
        ]
        terms_then = max(on_file, key=lambda t: t[1], default=None)
        backdated = any(
            starts <= due and entered > due + timedelta(days=grace + 1)
            for _, starts, entered, grace in made
        )
        fee = conn.execute(
            "SELECT f.policy_id, c.due_on FROM trust_late_fees AS f"
            " JOIN trust_charges AS c ON c.id = f.fee_charge_id WHERE f.rent_charge_id = %s",
            (rent,),
        ).fetchone()
        if terms_then is None or (on is not None and on <= due + timedelta(days=terms_then[2])):
            event("backdated terms charged nothing" if backdated else "no fee")
            assert fee is None, (due, on, made)
        else:
            event("a fee under the terms on file then")
            assert fee == (terms_then[0], due + timedelta(days=terms_then[2] + 1)), (due, made)

    assert assess_late_fees(conn, pmc.pmc_id, date(2026, 8, 1)) == 0


def test_late_fees_count_only_terms_on_file_by_the_day_a_fee_comes_due(database_url):
    with psycopg.connect(database_url, autocommit=True) as conn:
        check(conn)
