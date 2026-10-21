"""Property-based tests for returning security deposits at move-out (migration 20261012000035).

Hypothesis makes a lease of one to three tenants with deposits of any size, charges (rent, fees
and credits) due over its months, some partly paid, and returns the deposits after it ended. A
model works out what the lease owed; the database must agree:
  - what is kept is what the lease owed, or every deposit if that was less, and it went into the
    owner's account matched to what is open of the rent first, then of the other charges, each
    oldest first and paid in full before the next, the lease's credits coming off the rent
    first too (migration 0093);
  - what goes back is the rest, to the tenants whose deposits it was, kept from in name order;
  - every deposit is emptied, every trust bank account still ties out, and the lease owes what it
    owed less what was kept;
  - the same request again changes nothing.

Run more examples locally with HYPOTHESIS_PROFILE=nightly.
"""

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import psycopg
from helpers import (
    add_tenant,
    add_unit,
    apply_payment,
    balance,
    bank_sums,
    charge,
    make_pmc,
    open_account,
    open_lease,
    return_deposit,
    transfer_batch,
)
from hypothesis import event, given
from hypothesis import strategies as st

JAN_1, JUN_30 = date(2026, 1, 1), date(2026, 6, 30)
MOVE_OUT = datetime(2026, 7, 5, 12, tzinfo=UTC)
MONEY = st.integers(min_value=1, max_value=200_000).map(lambda c: Decimal(c) / 100)

CHARGES = st.lists(
    st.tuples(
        st.sampled_from(["rent", "fee", "credit"]),
        MONEY,
        st.integers(min_value=0, max_value=180),  # days after Jan 1
        st.integers(min_value=0, max_value=100),  # percent of it paid (not for a credit)
    ),
    max_size=6,
)


@given(
    deposits=st.lists(st.one_of(st.just(Decimal(0)), MONEY), min_size=1, max_size=3).filter(
        lambda held: sum(held) > 0
    ),
    charges=CHARGES,
)
def check(conn, app, deposits, charges):
    pmc = make_pmc(conn)
    owner = pmc.owners[0].account
    unit = add_unit(conn, pmc.pmc_id, pmc.owners[0].property_id)
    # Tenants named so their order by name is their order here.
    tenants, accounts = [], []
    for n, amount in enumerate(deposits):
        tenant = add_tenant(conn, pmc.pmc_id, pmc.owners[0].property_id, f"Tenant {n}")
        account = open_account(
            conn, pmc.pmc_id, pmc.deposit_bank_id, "tenant_deposit", tenant_id=tenant
        )
        if amount:
            transfer_batch(
                conn, [(pmc.deposit_cash, account, amount)], datetime(2026, 1, 1, tzinfo=UTC)
            )
        tenants.append(tenant)
        accounts.append(account)
    lease = open_lease(conn, pmc.pmc_id, unit, JAN_1, JUN_30, "1000.00", tenants)

    owed = Decimal(0)
    credits = Decimal(0)
    bills = []  # (not rent, due_on, id, left unpaid)
    rent_days = set()
    for n, (kind, amount, days, percent) in enumerate(charges):
        due = JAN_1 + timedelta(days=days)
        if kind == "rent" and due in rent_days:
            kind = "fee"  # rent is charged once per due date
        if kind == "rent":
            rent_days.add(due)
        made = charge(conn, pmc.pmc_id, lease, due, amount, kind, f"Charge {n}")
        if kind == "credit":
            owed -= amount
            credits += amount
            continue
        paid = (amount * percent / 100).quantize(Decimal("0.01"))
        if paid:
            (moved,) = transfer_batch(
                conn, [(pmc.operating_cash, owner, paid)], datetime(2026, 1, 1, tzinfo=UTC)
            )
            apply_payment(conn, pmc.pmc_id, made, moved, paid)
        owed += amount - paid
        bills.append((kind != "rent", due, made, amount - paid))
    owed = max(owed, Decimal(0))
    held = sum(deposits, Decimal(0))
    kept = min(owed, held)
    event("all kept" if kept == held else "all returned" if kept == 0 else "kept and returned")
    event(f"{len(deposits)} tenants")
    owner_before = balance(conn, owner)

    back = return_deposit(app, pmc.pmc_id, lease, "move-out", MOVE_OUT)

    assert back == held - kept
    assert conn.execute(
        "SELECT held, kept, returned FROM trust_deposit_returns WHERE lease_id = %s", (lease,)
    ).fetchone() == (held, kept, held - kept)
    assert [balance(conn, a) for a in accounts] == [0] * len(accounts)
    assert balance(conn, owner) - owner_before == kept
    assert set(bank_sums(conn, pmc.pmc_id).values()) == {0}
    assert conn.execute("SELECT trust_lease_owed(%s)", (lease,)).fetchone() == (owed - kept,)

    lines = conn.execute(
        "SELECT tenant_id, charge_id, amount FROM trust_deposit_return_lines WHERE lease_id = %s"
        " ORDER BY line_no",
        (lease,),
    ).fetchall()
    # Each tenant's deposit, all of it, kept or returned; kept from in name order.
    for tenant, amount in zip(tenants, deposits, strict=True):
        assert sum((a for t, _, a in lines if t == tenant), Decimal(0)) == amount
    taken = [tenants.index(t) for t, c, _ in lines if c is not None]
    assert taken == sorted(taken)
    returned_to = {tenants.index(t): a for t, c, a in lines if c is None}
    left = kept
    for n, amount in enumerate(deposits):
        take = min(left, amount)
        left -= take
        assert returned_to.get(n, Decimal(0)) == amount - take
    # Kept for the rent first, then the other charges, each oldest first and in full before the
    # next, of what is open of each once the credits came off them in the same order.
    paid_of = {}
    for _, made, amount in lines:
        if made is not None:
            paid_of[made] = paid_of.get(made, Decimal(0)) + amount
    charged = dict(
        conn.execute(
            "SELECT id, created_at FROM trust_charges WHERE lease_id = %s", (lease,)
        ).fetchall()
    )
    # The order money pays them: rent first, then by due date, when charged and id.
    bills.sort(key=lambda b: (b[0], b[1], charged[b[2]], b[2]))
    still, left = {}, credits
    for _, _, made, unpaid in bills:
        still[made] = unpaid - min(unpaid, left)
        left -= unpaid - still[made]
    rest = kept
    for _, _, made, _ in bills:
        due_part = min(rest, still[made])
        assert paid_of.get(made, Decimal(0)) == due_part
        rest -= due_part
    event("kept for a fee" if any(paid_of.get(b[2]) for b in bills if b[0]) else "no fee kept for")

    # The same request again: nothing more.
    assert return_deposit(app, pmc.pmc_id, lease, "move-out", MOVE_OUT) == back
    assert conn.execute(
        "SELECT count(*) FROM trust_deposit_return_lines WHERE lease_id = %s", (lease,)
    ).fetchone() == (len(lines),)


def test_a_deposit_return_keeps_what_is_owed_and_gives_back_the_rest(database_url):
    with (
        psycopg.connect(database_url, autocommit=True) as conn,
        psycopg.connect(database_url, autocommit=True) as app,
    ):
        app.execute("SET ROLE trust_app")
        check(conn, app)
