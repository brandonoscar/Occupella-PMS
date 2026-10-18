"""One vendor invoice shared across properties (migration 20261018000083; the founder's review,
q062).

Its parts are ordinary bills, one per owner's property account, so each follows every bill rule
on its own: the owner's approval limit, set aside from that owner's money, paid through its trust
bank account's cash. At commit the parts (1 to n, two at least, each in an account of its own, of
the invoice's vendor under its reference and part number, with one date, due day and memo) come
to the invoice. A vendor's reference is one bill or one shared invoice, never both. Every name
here is synthetic."""

from datetime import UTC, date, datetime
from decimal import Decimal

import psycopg
import pytest
from helpers import (
    acting_as,
    add_agreement,
    add_staff,
    approve_bill,
    balance,
    bank_sums,
    contribute,
    enter_bill,
    make_pmc,
    pay_bill,
    set_aside_bill,
)

SEP_1, OCT_5, OCT_20 = date(2026, 9, 1), date(2026, 10, 5), date(2026, 10, 20)


def at(day):
    return datetime(day.year, day.month, day.day, tzinfo=UTC)


@pytest.fixture
def pmc(conn):
    pmc = make_pmc(conn)
    for n, owner in enumerate(pmc.owners):
        add_agreement(conn, pmc.pmc_id, owner.account, SEP_1, approval_limit="500.00")
        contribute(conn, pmc.pmc_id, owner.account, f"in-{n}", "1000.00", at(SEP_1))
    return pmc


def share(conn, pmc, reference, amount, parts):
    """An invoice and its parts in one transaction. Each part is (account, amount) or a dict
    naming what differs from the invoice: account, amount, part, reference, vendor, bill_date,
    memo."""
    with conn.transaction():
        shared = conn.execute(
            "INSERT INTO trust_shared_bills (pmc_id, vendor_id, reference, amount)"
            " VALUES (%s, %s, %s, %s) RETURNING id",
            (pmc.pmc_id, pmc.vendor_id, reference, Decimal(amount)),
        ).fetchone()[0]
        bills = []
        for n, part in enumerate(parts, 1):
            if not isinstance(part, dict):
                part = {"account": part[0], "amount": part[1]}
            number = part.get("part", n)
            bill = enter_bill(
                conn,
                pmc.pmc_id,
                part.get("vendor", pmc.vendor_id),
                part["account"],
                part.get("reference", f"{reference}/{number}"),
                part.get("bill_date", OCT_5),
                OCT_20,
                part["amount"],
                part.get("memo", "Roof repair"),
            )
            conn.execute(
                "INSERT INTO trust_shared_bill_parts (shared_bill_id, part, pmc_id, bill_id)"
                " VALUES (%s, %s, %s, %s)",
                (shared, number, pmc.pmc_id, bill),
            )
            bills.append(bill)
    return shared, bills


def test_an_invoice_shared_by_two_properties_is_a_bill_for_each(conn, pmc):
    one, two = pmc.owners
    _, (first, second) = share(
        conn, pmc, "ROOF-1", "900.00", [(one.account, "400.00"), (two.account, "500.00")]
    )
    assert conn.execute(
        "SELECT reference, ledger_account_id, amount FROM trust_bills WHERE id = ANY(%s)"
        " ORDER BY reference",
        ([first, second],),
    ).fetchall() == [
        ("ROOF-1/1", one.account, Decimal("400.00")),
        ("ROOF-1/2", two.account, Decimal("500.00")),
    ]
    # Each part is set aside from its own owner's money and paid on its own.
    for bill in (first, second):
        set_aside_bill(conn, pmc.pmc_id, bill, at(OCT_20))
        pay_bill(conn, pmc.pmc_id, bill, at(OCT_20))
    assert (balance(conn, one.account), balance(conn, two.account)) == (
        Decimal("600.00"),
        Decimal("500.00"),
    )
    assert balance(conn, pmc.vendor_payable) == Decimal("0.00")
    # Book cash is kept negative (pgledger): 2000.00 in, 900.00 out.
    assert balance(conn, pmc.operating_cash) == Decimal("-1100.00")
    assert all(total == 0 for total in bank_sums(conn, pmc.pmc_id).values())


def test_each_part_waits_for_its_own_owners_approval(conn, pmc):
    one, two = pmc.owners
    _, (small, large) = share(
        conn, pmc, "ROOF-2", "900.00", [(one.account, "300.00"), (two.account, "600.00")]
    )
    set_aside_bill(conn, pmc.pmc_id, small, at(OCT_20))
    with pytest.raises(psycopg.errors.CheckViolation, match="approval limit"):
        set_aside_bill(conn, pmc.pmc_id, large, at(OCT_20))
    approve_bill(conn, pmc.pmc_id, large, OCT_5, "Owner 2")
    set_aside_bill(conn, pmc.pmc_id, large, at(OCT_20))
    assert balance(conn, pmc.vendor_payable) == Decimal("900.00")


def test_the_parts_come_to_the_invoice(conn, pmc):
    one, two = pmc.owners
    with pytest.raises(psycopg.errors.CheckViolation, match="come to 800.00, not its 900.00"):
        share(conn, pmc, "ROOF-3", "900.00", [(one.account, "400.00"), (two.account, "400.00")])
    # Nothing of it stays.
    assert conn.execute(
        "SELECT count(*) FROM trust_bills WHERE reference LIKE 'ROOF-3%%'"
    ).fetchone() == (0,)


@pytest.mark.parametrize(
    "parts",
    [
        # One part is a bill of its own, not a share.
        [{"account": 0, "amount": "900.00"}],
        # Parts number 1 to n, with no gap.
        [{"account": 0, "amount": "400.00"}, {"account": 1, "amount": "500.00", "part": 3}],
    ],
)
def test_an_invoice_is_shared_in_parts_1_to_n_two_at_least(conn, pmc, parts):
    for part in parts:
        part["account"] = pmc.owners[part["account"]].account
    with pytest.raises(psycopg.errors.CheckViolation, match="parts 1 to n, two at least"):
        share(conn, pmc, "ROOF-4", "900.00", parts)


def test_each_part_is_in_an_account_of_its_own(conn, pmc):
    one = pmc.owners[0].account
    with pytest.raises(psycopg.errors.CheckViolation, match="account of their own"):
        share(conn, pmc, "ROOF-5", "900.00", [(one, "400.00"), (one, "500.00")])


@pytest.mark.parametrize(
    "differs",
    [
        {"reference": "ROOF-6/9"},
        {"reference": "OTHER-1"},
        {"bill_date": date(2026, 10, 6)},
        {"memo": "Gutters"},
        {"vendor": "other"},
    ],
)
def test_the_parts_are_the_invoices_vendor_reference_date_and_memo(conn, pmc, differs):
    one, two = pmc.owners
    if differs.get("vendor") == "other":
        differs["vendor"] = conn.execute(
            "INSERT INTO trust_vendors (pmc_id, display_name) VALUES (%s, 'Vendor 2') RETURNING id",
            (pmc.pmc_id,),
        ).fetchone()[0]
    with pytest.raises(psycopg.errors.CheckViolation, match="parts are the vendor's"):
        share(
            conn,
            pmc,
            "ROOF-6",
            "900.00",
            [(one.account, "400.00"), {"account": two.account, "amount": "500.00", **differs}],
        )


def test_no_part_is_added_once_the_invoice_is_in(conn):
    pmc = make_pmc(conn, owners=3)
    one, two, three = pmc.owners
    shared, _ = share(
        conn, pmc, "ROOF-7", "900.00", [(one.account, "400.00"), (two.account, "500.00")]
    )
    with (
        pytest.raises(psycopg.errors.CheckViolation, match="come to 1000.00, not its 900.00"),
        conn.transaction(),
    ):
        third = enter_bill(
            conn,
            pmc.pmc_id,
            pmc.vendor_id,
            three.account,
            "ROOF-7/3",
            OCT_5,
            OCT_20,
            "100.00",
            "Roof repair",
        )
        conn.execute(
            "INSERT INTO trust_shared_bill_parts (shared_bill_id, part, pmc_id, bill_id)"
            " VALUES (%s, 3, %s, %s)",
            (shared, pmc.pmc_id, third),
        )


def test_a_vendors_reference_is_one_bill_or_one_shared_invoice(conn, pmc):
    one, two = pmc.owners
    enter_bill(conn, pmc.pmc_id, pmc.vendor_id, one.account, "ROOF-8", OCT_5, OCT_20, "90.00")
    with pytest.raises(psycopg.errors.UniqueViolation, match="has a bill ROOF-8 already"):
        share(conn, pmc, "ROOF-8", "900.00", [(one.account, "400.00"), (two.account, "500.00")])
    share(conn, pmc, "ROOF-9", "900.00", [(one.account, "400.00"), (two.account, "500.00")])
    with pytest.raises(psycopg.errors.UniqueViolation, match="shared across properties"):
        enter_bill(conn, pmc.pmc_id, pmc.vendor_id, one.account, "ROOF-9", OCT_5, OCT_20, "90.00")
    with pytest.raises(psycopg.errors.UniqueViolation):
        share(conn, pmc, "ROOF-9", "900.00", [(one.account, "400.00"), (two.account, "500.00")])
    # Another vendor's reference is its own.
    other = conn.execute(
        "INSERT INTO trust_vendors (pmc_id, display_name) VALUES (%s, 'Vendor 2') RETURNING id",
        (pmc.pmc_id,),
    ).fetchone()[0]
    enter_bill(conn, pmc.pmc_id, other, one.account, "ROOF-9", OCT_5, OCT_20, "90.00")


def test_a_part_is_of_its_invoices_company(conn, pmc):
    other = make_pmc(conn)
    one, two = pmc.owners
    shared, _ = share(
        conn, pmc, "ROOF-10", "900.00", [(one.account, "400.00"), (two.account, "500.00")]
    )
    theirs = enter_bill(
        conn, other.pmc_id, other.vendor_id, other.owners[0].account, "X-1", OCT_5, OCT_20, "1.00"
    )
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        conn.execute(
            "INSERT INTO trust_shared_bill_parts (shared_bill_id, part, pmc_id, bill_id)"
            " VALUES (%s, 3, %s, %s)",
            (shared, pmc.pmc_id, theirs),
        )


def test_shared_invoices_are_append_only_audited_and_name_who(conn, pmc):
    one, two = pmc.owners
    clerk = add_staff(conn, pmc.pmc_id, "Clerk 1")
    with acting_as(conn, clerk):
        shared, _ = share(
            conn, pmc, "ROOF-11", "900.00", [(one.account, "400.00"), (two.account, "500.00")]
        )
    assert conn.execute(
        "SELECT recorded_by FROM trust_shared_bills WHERE id = %s", (shared,)
    ).fetchone() == (clerk,)
    assert conn.execute(
        "SELECT table_name, count(*) FROM trust_audit_log WHERE pmc_id = %s"
        " AND table_name IN ('trust_shared_bills', 'trust_shared_bill_parts')"
        " GROUP BY table_name ORDER BY table_name",
        (pmc.pmc_id,),
    ).fetchall() == [("trust_shared_bill_parts", 2), ("trust_shared_bills", 1)]
    for query in (
        "UPDATE trust_shared_bills SET amount = 1",
        "DELETE FROM trust_shared_bill_parts",
    ):
        with pytest.raises(psycopg.errors.RestrictViolation, match="append-only"):
            conn.execute(query)
