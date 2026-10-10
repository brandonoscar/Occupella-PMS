"""trust_ai_agent, the role behind the API key Occupella will hold (migration 20261010000015).

The AI never moves money: it reads the books and runs the reports, and the database refuses
every write it tries. tests/test_privileges.py pins the grants; these act as the role.
"""

from datetime import UTC, date, datetime

import psycopg
import pytest
from helpers import (
    add_agreement,
    add_late_fee_policy,
    add_unit,
    add_work_order,
    apply_payment,
    approve,
    assess_late_fees,
    charge,
    charge_rent_due,
    enter_bill,
    make_pmc,
    open_lease,
    open_with_balances,
    post,
    set_aside_bill,
    snapshot,
    transfer_batch,
)
from psycopg import sql
from test_privileges import TABLES

JAN = datetime(2026, 1, 1, tzinfo=UTC)
JAN_3 = datetime(2026, 1, 3, tzinfo=UTC)
FEB = datetime(2026, 2, 1, tzinfo=UTC)
FEB_2 = datetime(2026, 2, 2, tzinfo=UTC)
# The books fixture's bill, found by the AI's own read grant.
THE_BILL = "(SELECT id FROM trust_bills WHERE pmc_id = %s AND reference = 'INV-1')"


@pytest.fixture
def books(conn):
    """A PMC with a month of activity: rent received and matched to its charge, a reconciled
    January. (pmc, lease id, charge id, rent transfer id, reconciliation id)."""
    pmc = make_pmc(conn)
    tenant = conn.execute(
        "SELECT tenant_id FROM trust_ledger_accounts WHERE ledger_account_id = %s",
        (pmc.tenant_deposit,),
    ).fetchone()[0]
    unit = add_unit(conn, pmc.pmc_id, pmc.owners[0].property_id)
    lease = open_lease(conn, pmc.pmc_id, unit, date(2026, 1, 1), None, "1500.00", [tenant])
    rent = charge(conn, pmc.pmc_id, lease, date(2026, 1, 1), "1500.00")
    (paid,) = transfer_batch(conn, [(pmc.operating_cash, pmc.owners[0].account, "1500.00")], JAN_3)
    apply_payment(conn, pmc.pmc_id, rent, paid, "1500.00")
    reconciliation = approve(conn, pmc.pmc_id, pmc.operating_bank_id, JAN, FEB)
    # A bill within the approval limit, set aside and ready to pay: each bill write the AI
    # tries below is one the app could make.
    add_agreement(conn, pmc.pmc_id, pmc.owners[0].account, date(2026, 1, 1), approval_limit="100")
    bill = enter_bill(
        conn, pmc.pmc_id, pmc.vendor_id, pmc.owners[0].account, "INV-1", FEB, FEB, "10.00"
    )
    set_aside_bill(conn, pmc.pmc_id, bill, FEB_2)
    add_work_order(conn, pmc.pmc_id, pmc.owners[0].property_id, None, "Broken heater", FEB)
    return pmc, lease, rent, paid, reconciliation


REPORTS = {
    "three-way reconciliation": (
        "SELECT * FROM trust_report_three_way_reconciliation(%s, %s)",
        lambda pmc, reconciliation: (pmc.pmc_id, reconciliation),
    ),
    "owner statement": (
        "SELECT * FROM trust_report_owner_statement(%s, %s, %s, %s)",
        lambda pmc, _: (pmc.pmc_id, pmc.owners[0].owner_id, JAN, FEB),
    ),
    "rent roll": (
        "SELECT * FROM trust_report_rent_roll(%s, %s)",
        lambda pmc, _: (pmc.pmc_id, date(2026, 1, 31)),
    ),
    "owner balances": (
        "SELECT * FROM trust_report_owner_balances(%s, %s)",
        lambda pmc, _: (pmc.pmc_id, date(2026, 2, 28)),
    ),
    "unpaid bills": (
        "SELECT * FROM trust_report_unpaid_bills(%s, %s)",
        lambda pmc, _: (pmc.pmc_id, date(2026, 2, 28)),
    ),
    "delinquency": (
        "SELECT * FROM trust_report_delinquency(%s, %s)",
        lambda pmc, _: (pmc.pmc_id, date(2026, 2, 28)),
    ),
    "tenant ledger": (
        "SELECT * FROM trust_report_tenant_ledger(%s,"
        " (SELECT id FROM trust_leases WHERE pmc_id = %s), %s)",
        lambda pmc, _: (pmc.pmc_id, pmc.pmc_id, date(2026, 2, 28)),
    ),
    "trial balance": (
        "SELECT * FROM trust_report_trial_balance(%s, %s)",
        lambda pmc, _: (pmc.pmc_id, date(2026, 2, 28)),
    ),
    "security deposits": (
        "SELECT * FROM trust_report_security_deposits(%s, %s)",
        lambda pmc, _: (pmc.pmc_id, date(2026, 2, 28)),
    ),
    "general ledger": (
        "SELECT * FROM trust_report_general_ledger(%s, %s, %s)",
        lambda pmc, _: (pmc.pmc_id, date(2026, 1, 1), date(2026, 2, 28)),
    ),
}


@pytest.mark.parametrize("report", REPORTS)
def test_the_ai_role_runs_every_report_and_sees_what_the_owner_sees(conn, ai_conn, books, report):
    pmc, *_, reconciliation = books
    query, params = REPORTS[report]

    rows = ai_conn.execute(query, params(pmc, reconciliation)).fetchall()

    assert rows == conn.execute(query, params(pmc, reconciliation)).fetchall()
    assert rows  # not empty: the role reads the records the report is built from


def test_the_ai_role_reads_balances_live(conn, ai_conn, books):
    pmc, *_ = books
    query = "SELECT balance FROM pgledger_accounts WHERE id = %s"

    # 1500.00 of rent, less the 10.00 bill set aside.
    assert ai_conn.execute(query, (pmc.owners[0].account,)).fetchone()[0] == 1490


WRITES = {
    "post with a key": lambda c, pmc, *_: post(
        c, pmc.pmc_id, "ai-1", [(pmc.operating_cash, pmc.owners[0].account, "1.00")]
    ),
    "post through pgledger": lambda c, pmc, *_: transfer_batch(
        c, [(pmc.operating_cash, pmc.owners[0].account, "1.00")]
    ),
    "open a ledger account": lambda c, pmc, *_: c.execute(
        "SELECT trust_open_ledger_account(%s, %s, 'pmc_income', NULL, NULL, NULL)",
        (pmc.pmc_id, pmc.operating_bank_id),
    ),
    "open a vendor account": lambda c, pmc, *_: c.execute(
        "SELECT trust_open_vendor_account(%s, %s, %s)",
        (pmc.pmc_id, pmc.operating_bank_id, pmc.vendor_id),
    ),
    "approve a reconciliation": lambda c, pmc, *_: approve(
        c, pmc.pmc_id, pmc.deposit_bank_id, JAN, FEB
    ),
    "open a lease": lambda c, pmc, lease, *_: c.execute(
        "SELECT trust_open_lease(%s, (SELECT unit_id FROM trust_leases WHERE id = %s),"
        " '2030-01-01', NULL, 1, ARRAY[]::uuid[])",
        (pmc.pmc_id, lease),
    ),
    "end a lease": lambda c, pmc, lease, *_: c.execute(
        "SELECT trust_end_lease(%s, %s, '2026-06-30')", (pmc.pmc_id, lease)
    ),
    "charge a lease": lambda c, pmc, lease, *_: charge(c, pmc.pmc_id, lease, FEB, "35.00", "fee"),
    "match a payment": lambda c, pmc, lease, rent, paid: apply_payment(
        c, pmc.pmc_id, rent, paid, "1.00"
    ),
    "reverse a payment": lambda c, pmc, lease, rent, paid: c.execute(
        "SELECT trust_reverse_payment(%s, %s, %s, %s, 1)", (pmc.pmc_id, rent, paid, paid)
    ),
    "record a management agreement": lambda c, pmc, *_: c.execute(
        "INSERT INTO trust_management_agreements (pmc_id, ledger_account_id, starts_on)"
        " VALUES (%s, %s, '2026-01-01')",
        (pmc.pmc_id, pmc.owners[0].account),
    ),
    "take a management fee": lambda c, pmc, *_: c.execute(
        "SELECT trust_post_management_fee(%s, %s, '2026-01-01', '2026-02-01', now())",
        (pmc.pmc_id, pmc.owners[0].account),
    ),
    "take a leasing fee": lambda c, pmc, lease, *_: c.execute(
        "SELECT trust_post_leasing_fee(%s, %s, %s, now())",
        (pmc.pmc_id, lease, pmc.owners[0].account),
    ),
    "pay the owner": lambda c, pmc, *_: c.execute(
        "SELECT trust_draw_owner(%s, %s, 'ai-draw', NULL, now())",
        (pmc.pmc_id, pmc.owners[0].account),
    ),
    "enter a bill": lambda c, pmc, *_: enter_bill(
        c, pmc.pmc_id, pmc.vendor_id, pmc.owners[0].account, "AI-1", FEB, FEB, "1.00"
    ),
    "approve a bill": lambda c, pmc, *_: c.execute(
        "INSERT INTO trust_bill_approvals (pmc_id, bill_id, approved_on, approved_by)"
        f" VALUES (%s, {THE_BILL}, '2026-02-01', 'The AI')",
        (pmc.pmc_id, pmc.pmc_id),
    ),
    "set a bill aside": lambda c, pmc, *_: c.execute(
        f"SELECT trust_set_aside_bill(%s, {THE_BILL}, now())", (pmc.pmc_id, pmc.pmc_id)
    ),
    "pay a bill": lambda c, pmc, *_: c.execute(
        f"SELECT trust_pay_bill(%s, {THE_BILL}, now())", (pmc.pmc_id, pmc.pmc_id)
    ),
    "open a work order": lambda c, pmc, *_: add_work_order(
        c, pmc.pmc_id, pmc.owners[0].property_id, None, "Leaking tap", FEB
    ),
    "record a work order step": lambda c, pmc, *_: c.execute(
        "INSERT INTO trust_work_order_steps (pmc_id, work_order_id, step, taken_at)"
        " SELECT %s, id, 'cancelled', now() FROM trust_work_orders WHERE pmc_id = %s",
        (pmc.pmc_id, pmc.pmc_id),
    ),
    "run the rent": lambda c, pmc, *_: charge_rent_due(c, pmc.pmc_id, FEB.date()),
    "record late fee terms": lambda c, pmc, *_: add_late_fee_policy(
        c, pmc.pmc_id, pmc.owners[0].property_id, FEB.date(), 5, "50"
    ),
    "assess late fees": lambda c, pmc, *_: assess_late_fees(c, pmc.pmc_id, FEB.date()),
    "open with balances carried over": lambda c, pmc, *_: open_with_balances(
        c, pmc.pmc_id, pmc.deposit_bank_id, JAN.date(), {pmc.tenant_deposit: "1.00"}, "1.00"
    ),
}


@pytest.mark.parametrize("write", WRITES)
def test_the_ai_role_cannot_move_money_or_change_a_record(conn, ai_conn, books, write):
    pmc, lease, rent, paid, _ = books
    before = snapshot(conn, pmc.accounts())

    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        WRITES[write](ai_conn, pmc, lease, rent, paid)

    assert snapshot(conn, pmc.accounts()) == before


def kind_of(conn, table):
    """'table', 'updatable view', or 'read-only view': a view that joins tables (pgledger's
    entries view) refuses every write, from any role, before grants are looked at."""
    relkind, updatable = conn.execute(
        "SELECT relkind, pg_relation_is_updatable(oid, false) FROM pg_class"
        " WHERE relnamespace = 'public'::regnamespace AND relname = %s",
        (table,),
    ).fetchone()
    if relkind in ("r", "p"):
        return "table"
    return "updatable view" if updatable else "read-only view"


@pytest.mark.parametrize("table", TABLES)
@pytest.mark.parametrize("statement", ["INSERT", "UPDATE", "DELETE", "TRUNCATE"])
def test_the_ai_role_writes_no_table(conn, ai_conn, table, statement):
    kind = kind_of(conn, table)
    if statement == "TRUNCATE" and kind != "table":
        statement = "DELETE"  # a view can't be truncated, by anyone
    column = conn.execute(
        "SELECT column_name FROM information_schema.columns WHERE table_name = %s"
        " ORDER BY ordinal_position LIMIT 1",
        (table,),
    ).fetchone()[0]
    query = {
        "INSERT": "INSERT INTO {t} DEFAULT VALUES",
        "UPDATE": "UPDATE {t} SET {c} = {c} WHERE false",
        "DELETE": "DELETE FROM {t} WHERE false",
        "TRUNCATE": "TRUNCATE {t}",
    }[statement]
    refused = (
        psycopg.errors.ObjectNotInPrerequisiteState
        if kind == "read-only view"
        else psycopg.errors.InsufficientPrivilege
    )

    with pytest.raises(refused):
        ai_conn.execute(sql.SQL(query).format(t=sql.Identifier(table), c=sql.Identifier(column)))
