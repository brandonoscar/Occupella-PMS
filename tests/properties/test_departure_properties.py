"""Property-based tests for a tenant leaving a lease others stay on (migration 20261018000080).

Hypothesis gives a lease two to four tenants, each holding a random deposit, and then has random
tenants leave, each naming a random tenant (or nobody) to take over their deposit. A model keeps
who has left and what each holds:
  - a departure is written exactly when its tenant hasn't left, someone else stays, and the one
    taking the deposit (if any) is another tenant who stays; a refused one changes nothing;
  - the leaver's deposit moves, all of it, to the one named, and nothing else moves;
  - what the lease's tenants hold together never changes, the security-deposit account ties out,
    and at least one tenant always stays.

Run more examples locally with HYPOTHESIS_PROFILE=nightly.
"""

from datetime import date
from decimal import Decimal

import psycopg
from helpers import add_tenant, add_unit, bank_sums, make_pmc, open_account, open_lease, transfer
from hypothesis import event, given
from hypothesis import strategies as st

JAN_1 = date(2026, 1, 1)
CENTS = st.integers(min_value=0, max_value=300_000).map(lambda c: Decimal(c).scaleb(-2))
LEAVES = st.lists(
    st.tuples(st.integers(min_value=0, max_value=3), st.none() | st.integers(0, 3)),
    max_size=6,
)


def held(conn, tenant):
    return conn.execute(
        "SELECT coalesce(sum(a.balance), 0) FROM trust_ledger_accounts AS t"
        " JOIN pgledger_accounts AS a ON a.id = t.ledger_account_id"
        " WHERE t.tenant_id = %s AND t.kind = 'tenant_deposit'",
        (tenant,),
    ).fetchone()[0]


@given(deposits=st.lists(CENTS, min_size=2, max_size=4), leaves=LEAVES)
def check(conn, deposits, leaves):
    pmc = make_pmc(conn)
    prop = pmc.owners[0].property_id
    tenants = [add_tenant(conn, pmc.pmc_id, prop, f"Tenant {n}") for n in range(len(deposits))]
    for tenant, amount in zip(tenants, deposits, strict=True):
        if amount > 0:
            account = open_account(
                conn, pmc.pmc_id, pmc.deposit_bank_id, "tenant_deposit", tenant_id=tenant
            )
            transfer(conn, pmc.deposit_cash, account, amount)
    lease = open_lease(
        conn, pmc.pmc_id, add_unit(conn, pmc.pmc_id, prop), JAN_1, None, "1000.00", tenants
    )
    model = dict(zip(tenants, deposits, strict=True))
    left: set = set()
    total = sum(deposits)

    for who, to in leaves:
        leaver = tenants[who % len(tenants)]
        taker = None if to is None else tenants[to % len(tenants)]
        staying = [t for t in tenants if t not in left]
        allowed = (
            leaver not in left
            and len(staying) > 1
            and (taker is None or (taker != leaver and taker not in left))
        )
        try:
            with conn.transaction():
                moved = conn.execute(
                    "SELECT trust_remove_lease_tenant(%s, %s, %s, %s, %s)",
                    (pmc.pmc_id, lease, leaver, date(2026, 3, 1), taker),
                ).fetchone()[0]
        except (
            psycopg.errors.CheckViolation,
            psycopg.errors.InvalidParameterValue,
            psycopg.errors.UniqueViolation,
        ):
            assert not allowed, "a departure the rules allow was refused"
            event("departure refused")
            continue
        if leaver in left:
            # The same departure again: it returns what moved the first time, moving nothing.
            event("same departure again")
            continue
        assert allowed, "a departure the rules refuse was written"
        expected = model[leaver] if taker is not None else Decimal(0)
        assert moved == expected
        if taker is not None:
            model[taker] += model[leaver]
            model[leaver] = Decimal(0)
            event("deposit moved" if expected > 0 else "nothing to move")
        else:
            event("deposit stayed")
        left.add(leaver)

    assert {t: held(conn, t) for t in tenants} == model
    assert sum(model.values()) == total
    assert len(left) < len(tenants)
    assert all(amount == 0 for amount in bank_sums(conn, pmc.pmc_id).values())
    assert conn.execute(
        "SELECT count(*) FROM trust_lease_departures WHERE lease_id = %s", (lease,)
    ).fetchone() == (len(left),)


def test_a_leaving_tenants_deposit_moves_whole_and_someone_always_stays(database_url):
    with psycopg.connect(database_url, autocommit=True) as conn:
        check(conn)
