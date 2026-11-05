"""Unapplied cash in the database (migration 20261106000240): money received into an operating
trust bank account's unapplied cash account, one per trust bank account, counted by the tie-out
as everything held is; applied to a lease as a payment from that account, or refunded through the
cash as a payout (disburse); never past what is left of it; and month-end not approved while any
is held, the three-way reconciliation showing it on a line of its own."""

import uuid
from datetime import UTC, date, datetime
from decimal import Decimal

import psycopg
import pytest
from helpers import (
    acting_as,
    add_staff,
    add_unit,
    bank_sums,
    charge,
    import_statement,
    make_pmc,
    match_lines,
    open_lease,
    post,
    prepare_bank,
    staff_named,
)

JAN_10 = date(2026, 1, 10)
JAN_31 = date(2026, 1, 31)


def one(conn, query, params=()):
    return conn.execute(query, params).fetchone()[0]


def unapplied_account(conn, pmc):
    return one(
        conn,
        "SELECT trust_open_ledger_account(%s, %s, 'unapplied_cash')",
        (pmc.pmc_id, pmc.operating_bank_id),
    )


def receive(conn, pmc, account, amount, on=JAN_10, payer="Unknown payer"):
    """Money received into the unapplied cash account, as the app records it."""
    (transfer,) = post(
        conn,
        pmc.pmc_id,
        f"unapplied/{uuid.uuid4()}",
        [(pmc.operating_cash, account, Decimal(amount))],
        datetime.combine(on, datetime.min.time(), UTC),
        {"kind": "unapplied cash"},
    )
    receipt = one(
        conn,
        "INSERT INTO trust_unapplied_receipts (pmc_id, bank_account_id, ledger_account_id,"
        " transfer_id, amount, received_on, payer, method) VALUES"
        " (%s, %s, %s, %s, %s, %s, %s, 'check') RETURNING id",
        (pmc.pmc_id, pmc.operating_bank_id, account, transfer, Decimal(amount), on, payer),
    )
    return receipt, transfer


def refund(conn, pmc, receipt, amount, key=None, staff=None):
    with acting_as(conn, staff) if staff else _nothing():
        return one(
            conn,
            "SELECT trust_refund_unapplied_cash(%s, %s, %s, 'Unknown payer', 'check', %s, now())",
            (pmc.pmc_id, receipt, Decimal(amount), key or str(uuid.uuid4())),
        )


class _nothing:
    def __enter__(self):
        return None

    def __exit__(self, *exc):
        return False


def balance(conn, account):
    return one(conn, "SELECT balance FROM pgledger_accounts WHERE id = %s", (account,))


def test_an_unapplied_cash_account_is_one_per_operating_bank_account_and_holds_nobodys(conn):
    pmc = make_pmc(conn)
    unapplied_account(conn, pmc)

    with pytest.raises(psycopg.errors.UniqueViolation):
        unapplied_account(conn, pmc)
    with pytest.raises(psycopg.errors.CheckViolation, match="belong in the operating"):
        conn.execute(
            "SELECT trust_open_ledger_account(%s, %s, 'unapplied_cash')",
            (pmc.pmc_id, pmc.deposit_bank_id),
        )
    with pytest.raises(psycopg.errors.CheckViolation, match="kind_shape"):
        conn.execute(
            "SELECT trust_open_ledger_account(%s, %s, 'unapplied_cash', %s, %s)",
            (pmc.pmc_id, pmc.operating_bank_id, pmc.owners[0].owner_id, pmc.owners[0].property_id),
        )


def test_received_unapplied_cash_is_counted_by_the_tie_out(conn):
    pmc = make_pmc(conn)
    account = unapplied_account(conn, pmc)
    receive(conn, pmc, account, "500.00")

    assert balance(conn, account) == Decimal("500.00")
    assert set(bank_sums(conn, pmc.pmc_id).values()) == {Decimal(0)}
    # Held money never leaves a trust bank account without its cash.
    with pytest.raises(psycopg.errors.CheckViolation, match="does not tie out"):
        post(conn, pmc.pmc_id, "away", [(account, pmc.tenant_deposit, Decimal("1.00"))])


def test_a_receipt_is_cash_into_the_unapplied_account_of_its_amount_and_day(conn):
    pmc = make_pmc(conn)
    account = unapplied_account(conn, pmc)
    (into_owner,) = post(
        conn, pmc.pmc_id, "x", [(pmc.operating_cash, pmc.owners[0].account, Decimal("5.00"))]
    )

    with pytest.raises(psycopg.errors.CheckViolation, match="into its unapplied cash account"):
        conn.execute(
            "INSERT INTO trust_unapplied_receipts (pmc_id, bank_account_id, ledger_account_id,"
            " transfer_id, amount, received_on, payer, method)"
            " VALUES (%s, %s, %s, %s, 5.00, current_date, 'X', 'cash')",
            (pmc.pmc_id, pmc.operating_bank_id, account, into_owner),
        )


def test_a_refund_is_a_payout_never_past_what_is_left_and_safe_to_retry(conn):
    pmc = make_pmc(conn)
    account = unapplied_account(conn, pmc)
    receipt, _ = receive(conn, pmc, account, "300.00")
    clerk = add_staff(conn, pmc.pmc_id, "Clerk 1")  # holds no role
    payer = staff_named(conn, pmc.pmc_id, "Payer 1", ["disburse"])

    with pytest.raises(psycopg.errors.InsufficientPrivilege, match="disburse"):
        refund(conn, pmc, receipt, "100.00", staff=clerk)
    first = refund(conn, pmc, receipt, "100.00", key="r-1", staff=payer)
    again = refund(conn, pmc, receipt, "100.00", key="r-1", staff=payer)
    with pytest.raises(psycopg.errors.CheckViolation, match=r"only \$200.00 of this unapplied"):
        refund(conn, pmc, receipt, "200.01", staff=payer)

    assert first == again
    assert balance(conn, account) == Decimal("200.00")
    assert one(conn, "SELECT trust_unapplied_left(%s)", (receipt,)) == Decimal("200.00")
    assert set(bank_sums(conn, pmc.pmc_id).values()) == {Decimal(0)}


def test_month_end_is_not_approved_while_unapplied_cash_is_held(conn):
    pmc = make_pmc(conn)
    account = unapplied_account(conn, pmc)
    receipt, transfer = receive(conn, pmc, account, "500.00")
    staff = staff_named(conn, pmc.pmc_id, "Preparer 1")
    approver = staff_named(conn, pmc.pmc_id, "Approver 1", ["approve", "disburse"])
    _, (line,) = import_statement(
        conn,
        pmc.pmc_id,
        pmc.operating_bank_id,
        staff,
        date(2026, 1, 1),
        JAN_31,
        [(JAN_10, "500.00", "Deposit", None)],
    )
    match_lines(conn, pmc.pmc_id, pmc.operating_bank_id, [line], [transfer], staff)
    prepared = prepare_bank(conn, pmc.pmc_id, pmc.operating_bank_id, JAN_31, staff)

    with (
        acting_as(conn, approver),
        pytest.raises(
            psycopg.errors.CheckViolation,
            match=r"\$500.00 of unapplied cash is held in this trust bank account: apply it to a"
            r" lease or refund it before approving",
        ),
    ):
        conn.execute("SELECT trust_approve_bank_reconciliation(%s, %s)", (pmc.pmc_id, prepared))
    # Refunded today, nothing is held: January is approved on its figures.
    refund(conn, pmc, receipt, "500.00", staff=approver)
    with acting_as(conn, approver):
        approved = one(
            conn, "SELECT trust_approve_bank_reconciliation(%s, %s)", (pmc.pmc_id, prepared)
        )

    rows = conn.execute(
        "SELECT item, amount FROM trust_report_three_way_reconciliation(%s, %s)",
        (pmc.pmc_id, approved),
    ).fetchall()
    found = dict(rows)
    # At January's end the books held the $500.00 as unapplied cash, on a line of its own.
    assert found["of which unapplied cash"] == Decimal("500.00")
    assert found["beneficiary ledgers"] == Decimal("500.00")
    assert found["difference: trust journal - beneficiary ledgers"] == Decimal("0.00")
    assert ("ledger", Decimal("500.00")) in [(i, a) for i, a in rows if i == "ledger"]


def test_a_payment_from_unapplied_cash_pays_the_lease_and_never_past_the_receipt(conn):
    pmc = make_pmc(conn)
    account = unapplied_account(conn, pmc)
    receipt, _ = receive(conn, pmc, account, "200.00")
    tenant = one(
        conn,
        "SELECT tenant_id FROM trust_ledger_accounts WHERE ledger_account_id = %s",
        (pmc.prepaid_rent,),
    )
    unit = add_unit(conn, pmc.pmc_id, pmc.owners[0].property_id)
    lease = open_lease(conn, pmc.pmc_id, unit, JAN_10, None, "1000.00", [tenant])
    rent_charge = charge(conn, pmc.pmc_id, lease, JAN_10, "1000.00")
    key = f"payment/{uuid.uuid4()}"
    (paid,) = post(
        conn,
        pmc.pmc_id,
        key,
        [(account, pmc.owners[0].account, Decimal("150.00"))],
        metadata={"key": key, "kind": "tenant payment"},
    )
    conn.execute("SELECT trust_apply_payment(%s, %s, %s, 150.00)", (pmc.pmc_id, rent_charge, paid))

    with pytest.raises(psycopg.errors.CheckViolation, match="the payment it posted"):
        conn.execute(
            "INSERT INTO trust_unapplied_applications (pmc_id, receipt_id, lease_id, amount,"
            " payment_key) VALUES (%s, %s, %s, 100.00, %s)",
            (pmc.pmc_id, receipt, lease, key),
        )
    conn.execute(
        "INSERT INTO trust_unapplied_applications (pmc_id, receipt_id, lease_id, amount,"
        " payment_key) VALUES (%s, %s, %s, 150.00, %s)",
        (pmc.pmc_id, receipt, lease, key),
    )

    assert one(conn, "SELECT trust_unapplied_left(%s)", (receipt,)) == Decimal("50.00")
    assert balance(conn, pmc.owners[0].account) == Decimal("150.00")
    assert set(bank_sums(conn, pmc.pmc_id).values()) == {Decimal(0)}


def test_unapplied_cash_is_never_rewritten(conn):
    pmc = make_pmc(conn)
    receive(conn, pmc, unapplied_account(conn, pmc), "10.00")

    for table in (
        "trust_unapplied_receipts",
        "trust_unapplied_applications",
        "trust_unapplied_refunds",
    ):
        with pytest.raises(psycopg.errors.RestrictViolation, match="append-only"):
            conn.execute(f"DELETE FROM {table}")  # noqa: S608
