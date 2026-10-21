"""Property-based tests for a deposit's kind (migration 20261018000092; the founder's review,
q043).

Hypothesis gives one or two tenants of a lease a security, pet and key deposit each of any size
(maybe none), maybe has the first leave the lease with their deposits going to the second, charges
the lease what it charges, and returns the deposits after it ended. A model works out each
account's money; the database must agree:
  - each kind is held in a deposit account of its own, and the register names each;
  - a tenant who leaves moves every kind of deposit to the staying tenant's of the same kind;
  - the return keeps what the lease owes, or everything held if that is less, from the tenants in
    name order and from each tenant's security deposit first, then the key and pet deposits, and
    returns the rest of each kind, named by its kind on the statement;
  - every deposit account is emptied and every trust bank account ties out.

Run more examples locally with HYPOTHESIS_PROFILE=nightly.
"""

from datetime import UTC, date, datetime
from decimal import Decimal

import psycopg
from helpers import (
    add_tenant,
    add_unit,
    balance,
    bank_sums,
    charge,
    make_pmc,
    open_lease,
    return_deposit,
    transfer_batch,
)
from hypothesis import event, given
from hypothesis import strategies as st

JAN_1, MAR_1, JUN_30 = date(2026, 1, 1), date(2026, 3, 1), date(2026, 6, 30)
MOVE_OUT = datetime(2026, 7, 5, 12, tzinfo=UTC)
MONEY = st.integers(min_value=1, max_value=200_000).map(lambda c: Decimal(c) / 100)
HELD = st.one_of(st.just(Decimal(0)), MONEY)
# The order a return keeps from a tenant's deposits in, and how the register and the statement
# name each.
KINDS = ("security", "key", "pet")
ITEM = {"security": "deposit", "key": "key deposit", "pet": "pet deposit"}
SUFFIX = {"security": "", "key": " (key deposit)", "pet": " (pet deposit)"}


@given(
    held=st.lists(st.tuples(HELD, HELD, HELD), min_size=1, max_size=2).filter(
        lambda tenants: sum(sum(kinds) for kinds in tenants) > 0
    ),
    leaves=st.booleans(),
    owed=st.one_of(st.just(Decimal(0)), MONEY),
)
def check(conn, app, held, leaves, owed):
    pmc = make_pmc(conn)
    place = pmc.owners[0].property_id
    unit = add_unit(conn, pmc.pmc_id, place)
    # Tenants named so their order by name is their order here.
    tenants = [add_tenant(conn, pmc.pmc_id, place, f"Tenant {n}") for n in range(len(held))]
    lease = open_lease(conn, pmc.pmc_id, unit, JAN_1, JUN_30, "1000.00", tenants)
    accounts = {}
    for tenant, amounts in zip(tenants, held, strict=True):
        for kind, amount in zip(KINDS, amounts, strict=True):
            accounts[(tenant, kind)] = app.execute(
                "SELECT trust_open_deposit_account(%s, %s, %s, %s)",
                (pmc.pmc_id, pmc.deposit_bank_id, tenant, kind),
            ).fetchone()[0]
            if amount:
                transfer_batch(
                    conn,
                    [(pmc.deposit_cash, accounts[(tenant, kind)], amount)],
                    datetime(2026, 1, 1, tzinfo=UTC),
                )
    model = {
        (tenant, kind): amount
        for tenant, amounts in zip(tenants, held, strict=True)
        for kind, amount in zip(KINDS, amounts, strict=True)
    }
    assert {key: balance(conn, account) for key, account in accounts.items()} == model
    register = conn.execute(
        "SELECT item, tenant, held FROM trust_report_security_deposits(%s, %s)"
        " WHERE held IS NOT NULL AND tenant IS NOT NULL",
        (pmc.pmc_id, MAR_1),
    ).fetchall()
    assert sorted(register) == sorted(
        (ITEM[kind], f"Tenant {tenants.index(tenant)}", amount)
        for (tenant, kind), amount in model.items()
        if amount
    )

    if leaves and len(tenants) == 2:
        moved = app.execute(
            "SELECT trust_remove_lease_tenant(%s, %s, %s, %s, %s)",
            (pmc.pmc_id, lease, tenants[0], MAR_1, tenants[1]),
        ).fetchone()[0]
        assert moved == sum(held[0], Decimal(0))
        for kind in KINDS:
            model[(tenants[1], kind)] += model[(tenants[0], kind)]
            model[(tenants[0], kind)] = Decimal(0)
        event("a tenant left, their deposits to the other's")
        assert {key: balance(conn, account) for key, account in accounts.items()} == model
        assert set(bank_sums(conn, pmc.pmc_id).values()) == {0}

    if owed:
        charge(conn, pmc.pmc_id, lease, JUN_30, owed, "fee", "Carpet cleaned")
    total = sum(model.values(), Decimal(0))
    kept = min(owed, total)
    event("all kept" if kept == total else "all returned" if kept == 0 else "kept and returned")

    back = app.execute(
        "SELECT trust_return_deposit(%s, %s, NULL, 'move-out', %s)", (pmc.pmc_id, lease, MOVE_OUT)
    ).fetchone()[0]

    # Kept from the tenants in name order, each tenant's security deposit first.
    left, returned = kept, {}
    for tenant in tenants:
        for kind in KINDS:
            take = min(left, model[(tenant, kind)])
            left -= take
            if model[(tenant, kind)] - take:
                returned[f"Tenant {tenants.index(tenant)}{SUFFIX[kind]}"] = (
                    model[(tenant, kind)] - take
                )
    assert back == total - kept
    assert {key: balance(conn, account) for key, account in accounts.items()} == {
        key: Decimal(0) for key in accounts
    }
    assert set(bank_sums(conn, pmc.pmc_id).values()) == {0}
    statement = conn.execute(
        "SELECT detail, amount FROM trust_report_deposit_return(%s, %s) WHERE item = 'returned to'",
        (pmc.pmc_id, lease),
    ).fetchall()
    assert dict(statement) == returned
    assert len(statement) == len(returned)
    # The same request again: nothing more.
    assert return_deposit(app, pmc.pmc_id, lease, "move-out", MOVE_OUT) == back


def test_each_kind_of_deposit_is_held_moved_and_returned_as_the_model_says(database_url):
    with (
        psycopg.connect(database_url, autocommit=True) as conn,
        psycopg.connect(database_url, autocommit=True) as app,
    ):
        app.execute("SET ROLE trust_app")
        check(conn, app)
