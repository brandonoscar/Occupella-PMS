"""Property-based tests for what an owner's balance is short of each bill (trust_bill_holds,
migration 20261104000300; the founder's review of 11/03, item 3).

Hypothesis gives an owner's property account some balance and a reserve (maybe none), then
enters open bills with due days in any order. trust_bill_holds must:
  - never lay a bill over the reserve: what all bills hold together is what is above the reserve,
    or all of them if that is less;
  - lay the balance over the bills in the order they are due: a bill holds something only once
    every bill due before it is held whole;
  - say each bill is short of its amount less what it holds, and that the owner needs to send
    what the bills due up to it come to, less what is above the reserve (never below 0);
and nothing moves.

Run more examples locally with HYPOTHESIS_PROFILE=nightly.
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import psycopg
from helpers import add_agreement, balance, enter_bill, make_pmc, transfer_batch
from hypothesis import given
from hypothesis import strategies as st

MONEY = st.integers(min_value=0, max_value=3_000_000).map(lambda c: Decimal(c) / 100)
BILL = st.integers(min_value=1, max_value=3_000_000).map(lambda c: Decimal(c) / 100)


@given(
    held=MONEY,
    reserve=st.one_of(st.none(), MONEY),
    bills=st.lists(st.tuples(BILL, st.integers(min_value=0, max_value=60)), min_size=1, max_size=5),
)
def check(conn, held, reserve, bills):
    today = conn.execute("SELECT (now() AT TIME ZONE 'UTC')::date").fetchone()[0]
    pmc = make_pmc(conn)
    account = pmc.owners[0].account
    add_agreement(
        conn,
        pmc.pmc_id,
        account,
        today - timedelta(days=60),
        reserve=str(reserve or 0),
        approval_limit="100000000",
    )
    if held:
        transfer_batch(
            conn,
            [(pmc.operating_cash, account, held)],
            datetime.now(UTC) - timedelta(days=30),
        )
    made = [
        (
            enter_bill(
                conn,
                pmc.pmc_id,
                pmc.vendor_id,
                account,
                f"B-{n}",
                today - timedelta(days=1),
                today + timedelta(days=due),
                amount,
            ),
            amount,
            due,
            n,
        )
        for n, (amount, due) in enumerate(bills)
    ]
    rows = {
        bill: (h, s, need)
        for bill, h, s, need in conn.execute(
            "SELECT bill_id, held, short, needed FROM trust_bill_holds(%s)", (pmc.pmc_id,)
        ).fetchall()
    }

    above = max(held - (reserve or 0), Decimal(0))
    total = sum((a for _, a, _, _ in made), Decimal(0))
    assert sum((rows[b][0] for b, *_ in made), Decimal(0)) == min(total, above)
    # In the order they are due (then by reference, as the function breaks ties).
    through = Decimal(0)
    for bill, amount, _, _ in sorted(made, key=lambda m: (m[2], f"B-{m[3]}")):
        got_held, got_short, got_needed = rows[bill]
        through += amount
        assert got_held == min(amount, max(above - (through - amount), Decimal(0)))
        assert got_short == amount - got_held
        assert got_needed == max(through - above, Decimal(0))
    assert balance(conn, account) == held  # nothing moved


def test_a_bill_is_never_laid_over_the_reserve_and_the_first_due_is_held_first(database_url):
    with psycopg.connect(database_url, autocommit=True) as conn:
        check(conn)
