"""Charge types (migration 20261020000100; the founder's review q133).

A PMC's own kinds of fee, a name once per PMC whatever its case and none of the income
statement's own income lines. A fee, once or every month, may name one of its PMC's types, never
a credit or rent; a charge the rent run writes for a recurring charge counts under that one's
type. The income statement puts what was collected on each type, net of bounces, on a line of
its own after the other income (the golden case pins the layout), and the budget beside it
counts that line as income.
"""

from datetime import UTC, date, datetime
from decimal import Decimal

import psycopg
import pytest
from helpers import (
    add_recurring_charge,
    add_unit,
    apply_payment,
    charge_recurring_due,
    make_pmc,
    open_lease,
    post,
    reverse_payment,
    transfer_batch,
)

JAN_1 = date(2026, 1, 1)
YEAR = (JAN_1, date(2026, 12, 31))


def at(month, day, hour=12):
    return datetime(2026, month, day, hour, tzinfo=UTC)


@pytest.fixture
def pmc(conn):
    return make_pmc(conn)


@pytest.fixture
def lease(conn, pmc):
    tenant = conn.execute(
        "SELECT tenant_id FROM trust_ledger_accounts WHERE ledger_account_id = %s",
        (pmc.prepaid_rent,),
    ).fetchone()[0]
    unit = add_unit(conn, pmc.pmc_id, pmc.owners[0].property_id)
    return open_lease(conn, pmc.pmc_id, unit, JAN_1, None, "1500.00", [tenant])


def charge_type(conn, pmc_id, name):
    return conn.execute(
        "INSERT INTO trust_charge_types (pmc_id, name) VALUES (%s, %s) RETURNING id",
        (pmc_id, name),
    ).fetchone()[0]


def fee(conn, pmc, lease, due_on, amount, kind_of=None, memo="Fee"):
    return conn.execute(
        "INSERT INTO trust_charges (pmc_id, lease_id, due_on, kind, amount, memo, charge_type_id)"
        " VALUES (%s, %s, %s, 'fee', %s, %s, %s) RETURNING id",
        (pmc.pmc_id, lease, due_on, Decimal(amount), memo, kind_of),
    ).fetchone()[0]


def paid(conn, pmc, charged, amount, when):
    (transfer,) = transfer_batch(conn, [(pmc.operating_cash, pmc.owners[0].account, amount)], when)
    apply_payment(conn, pmc.pmc_id, charged, transfer, amount)
    return transfer


def income(conn, pmc):
    return {
        item: amount
        for _, prop, item, _, amount in conn.execute(
            "SELECT line, property, item, schedule_e, amount"
            " FROM trust_report_income_statement(%s, %s, %s, %s)",
            (pmc.pmc_id, pmc.owners[0].owner_id, *YEAR),
        ).fetchall()
        if prop == "Property 1" and (item.startswith("income:") or item == "total income")
    }


def test_each_type_collected_on_is_a_line_of_its_own_net_of_bounces(conn, app_conn, pmc, lease):
    cleaning, pets, _unused = (
        charge_type(conn, pmc.pmc_id, name) for name in ("Cleaning", "Pet rent", "Unused")
    )
    clean = fee(conn, pmc, lease, JAN_1, "80.00", cleaning)
    key = fee(conn, pmc, lease, JAN_1, "10.00")
    conn.execute(
        "INSERT INTO trust_recurring_charges (pmc_id, lease_id, kind, amount, memo, starts_on,"
        " charge_type_id) VALUES (%s, %s, 'fee', 25.00, 'Dog', %s, %s)",
        (pmc.pmc_id, lease, JAN_1, pets),
    )
    add_recurring_charge(conn, pmc.pmc_id, lease, "40.00", JAN_1, "Parking")
    charge_recurring_due(conn, pmc.pmc_id, JAN_1)
    dog, parking = (
        conn.execute(
            "SELECT id FROM trust_charges WHERE lease_id = %s AND memo LIKE %s", (lease, memo)
        ).fetchone()[0]
        for memo in ("Dog%", "Parking%")
    )
    for charged, amount in [(clean, "80.00"), (key, "10.00"), (dog, "25.00"), (parking, "40.00")]:
        paid(conn, pmc, charged, amount, at(1, 5))
    # Another cleaning fee, paid by a payment that came back.
    meta = {"kind": "tenant payment", "lease_id": str(lease), "reference": None, "key": "p9"}
    second = fee(conn, pmc, lease, JAN_1, "20.00", cleaning, "Carpet")
    (sent,) = post(
        conn,
        pmc.pmc_id,
        "p9",
        [(pmc.operating_cash, pmc.owners[0].account, "20.00")],
        at(1, 9),
        meta,
    )
    apply_payment(conn, pmc.pmc_id, second, sent, "20.00")
    (back,) = post(
        conn,
        pmc.pmc_id,
        "bounce/p9",
        [(pmc.owners[0].account, pmc.operating_cash, "20.00")],
        at(1, 10),
        {**meta, "kind": "bounced payment", "key": "bounce/p9"},
    )
    reverse_payment(conn, pmc.pmc_id, second, sent, back, "20.00")

    found = income(app_conn, pmc)

    assert found == {
        "income: rent": Decimal("0.00"),
        "income: recurring charges": Decimal("40.00"),
        "income: late fees": Decimal("0.00"),
        "income: NSF fees": Decimal("0.00"),
        "income: other charges": Decimal("10.00"),
        "income: Cleaning": Decimal("80.00"),  # 20.00 more paid and bounced
        "income: Pet rent": Decimal("25.00"),
        "total income": Decimal("155.00"),
    }


def test_the_budget_beside_the_statement_counts_a_types_line_as_income(conn, app_conn, pmc, lease):
    paid(
        conn,
        pmc,
        fee(conn, pmc, lease, JAN_1, "60.00", charge_type(conn, pmc.pmc_id, "Fob")),
        "60.00",
        at(1, 5),
    )

    rows = {
        item: (budget, actual)
        for _, prop, item, budget, actual, _ in app_conn.execute(
            "SELECT * FROM trust_report_budget_vs_actual(%s, %s, %s, %s)",
            (pmc.pmc_id, pmc.owners[0].owner_id, *YEAR),
        ).fetchall()
        if prop == "Property 1"
    }
    items = list(rows)

    assert rows["income: Fob"] == (Decimal("0.00"), Decimal("60.00"))
    assert rows["total income"][1] == Decimal("60.00")
    assert rows["total expenses"][1] == Decimal("0.00")
    assert rows["net income"][1] == Decimal("60.00")
    # After the other income, before its total.
    assert items.index("income: other charges") < items.index("income: Fob")
    assert items.index("income: Fob") < items.index("total income")


@pytest.mark.parametrize(
    "name", ["Rent", "late fees", "NSF Fees", "Other charges", "recurring charges", " Pad", ""]
)
def test_a_type_is_never_one_of_the_statements_lines_or_untrimmed(conn, pmc, name):
    with pytest.raises(psycopg.errors.CheckViolation), conn.transaction():
        charge_type(conn, pmc.pmc_id, name)


def test_a_name_is_once_per_pmc_whatever_its_case(conn, pmc):
    charge_type(conn, pmc.pmc_id, "Parking")
    with pytest.raises(psycopg.errors.UniqueViolation), conn.transaction():
        charge_type(conn, pmc.pmc_id, "PARKING")
    charge_type(conn, make_pmc(conn).pmc_id, "Parking")


def test_only_a_fee_has_a_type_and_only_of_its_own_pmc(conn, pmc, lease):
    mine = charge_type(conn, pmc.pmc_id, "Cleaning")
    theirs = charge_type(conn, make_pmc(conn).pmc_id, "Cleaning")
    one_off = (
        "INSERT INTO trust_charges (pmc_id, lease_id, due_on, kind, amount, memo, charge_type_id)"
        " VALUES (%s, %s, %s, %s, 10.00, 'Clean', %s)"
    )
    monthly = (
        "INSERT INTO trust_recurring_charges (pmc_id, lease_id, kind, amount, memo, starts_on,"
        " charge_type_id) VALUES (%s, %s, %s, 10.00, 'Clean', %s, %s)"
    )
    for kind in ("credit", "rent"):
        with pytest.raises(psycopg.errors.CheckViolation), conn.transaction():
            conn.execute(one_off, (pmc.pmc_id, lease, JAN_1, kind, mine))
    with pytest.raises(psycopg.errors.CheckViolation), conn.transaction():
        conn.execute(monthly, (pmc.pmc_id, lease, "credit", JAN_1, mine))
    with pytest.raises(psycopg.errors.ForeignKeyViolation), conn.transaction():
        conn.execute(one_off, (pmc.pmc_id, lease, JAN_1, "fee", theirs))
    with pytest.raises(psycopg.errors.ForeignKeyViolation), conn.transaction():
        conn.execute(monthly, (pmc.pmc_id, lease, "fee", JAN_1, theirs))


def test_types_are_never_rewritten(conn, pmc):
    kind_of = charge_type(conn, pmc.pmc_id, "Cleaning")
    for change in (
        "UPDATE trust_charge_types SET name = 'Deep cleaning' WHERE id = %s",
        "DELETE FROM trust_charge_types WHERE id = %s",
    ):
        with pytest.raises(psycopg.errors.RestrictViolation), conn.transaction():
            conn.execute(change, (kind_of,))
