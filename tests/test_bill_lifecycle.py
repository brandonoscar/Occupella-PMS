"""A bill's life in plain steps (ADR 0010, migration 0152): held from its owner's balance until it
is paid, paid whole or in parts, or voided by staff while nothing was taken for it.

trust_bill_holds lays each owner's property account's balance over its open bills dated by today,
the one due first first: what each holds, what the balance is short of it and what the owner must
send for it and those before it. trust_pay_bill_part pays a part once per request key; the first
payment takes the whole bill and its markup from the owner, as setting it aside did. A void is a
staff member's row of trust_bill_declines, refused once money was taken for the bill.
"""

import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import psycopg
import pytest
from helpers import (
    acting_as,
    add_agreement,
    add_staff,
    approve_bill,
    balance,
    decline_bill,
    draw_owner,
    enter_bill,
    in_background,
    make_pmc,
    pay_bill,
    set_aside_bill,
    transfer_batch,
    wait_until_blocked,
)

TODAY = datetime.now(UTC).date()
DAY = timedelta(days=1)
NOW = datetime.now(UTC)


def at(day: date) -> datetime:
    return datetime(day.year, day.month, day.day, tzinfo=UTC)


@pytest.fixture
def pmc(conn):
    return make_pmc(conn)


@pytest.fixture
def owner(conn, pmc):
    """Owner 1's account, holding 500.00 since a month ago, bills up to 1000.00 needing no
    approval."""
    account = pmc.owners[0].account
    add_agreement(conn, pmc.pmc_id, account, TODAY - 60 * DAY, approval_limit="1000")
    transfer_batch(conn, [(pmc.operating_cash, account, "500.00")], at(TODAY - 30 * DAY))
    return account


def bill(conn, pmc, account, amount, reference, due_in=10, dated=-2):
    return enter_bill(
        conn,
        pmc.pmc_id,
        pmc.vendor_id,
        account,
        reference,
        TODAY + dated * DAY,
        TODAY + due_in * DAY,
        amount,
    )


def holds(conn, pmc):
    """Each open bill's (held, short, needed), by bill."""
    return {
        bill_id: (held, short, needed)
        for bill_id, held, short, needed in conn.execute(
            "SELECT bill_id, held, short, needed FROM trust_bill_holds(%s)", (pmc.pmc_id,)
        ).fetchall()
    }


def part(conn, pmc, bill_id, amount, key, day=TODAY):
    return conn.execute(
        "SELECT trust_pay_bill_part(%s, %s, %s, %s, %s)",
        (pmc.pmc_id, bill_id, Decimal(amount), key, at(day)),
    ).fetchone()[0]


def void(conn, pmc, bill_id, staff, reason="Entered twice"):
    with acting_as(conn, staff):
        conn.execute(
            "INSERT INTO trust_bill_declines (pmc_id, bill_id, declined_on, declined_by, reason,"
            " void) VALUES (%s, %s, %s, 'Staff 1', %s, true)",
            (pmc.pmc_id, bill_id, TODAY, reason),
        )


# --- holds ------------------------------------------------------------------------------------


def test_the_balance_is_held_for_the_bill_due_first_and_the_rest_is_short(conn, pmc, owner):
    later = bill(conn, pmc, owner, "300.00", "B-2", due_in=20)
    first = bill(conn, pmc, owner, "350.00", "B-1", due_in=5)
    third = bill(conn, pmc, owner, "100.00", "B-3", due_in=30)

    held = holds(conn, pmc)

    # 500.00 covers B-1 whole, then 150.00 of B-2; B-3 holds nothing. To pay B-2 the owner must
    # send its 150.00; to pay B-3, that and its own 100.00.
    assert held[first] == (Decimal("350.00"), Decimal("0.00"), Decimal("0.00"))
    assert held[later] == (Decimal("150.00"), Decimal("150.00"), Decimal("150.00"))
    assert held[third] == (Decimal("0.00"), Decimal("100.00"), Decimal("250.00"))
    assert balance(conn, owner) == Decimal("500.00")  # nothing moved


def test_a_bill_holds_its_markup_and_a_bill_dated_later_holds_nothing_yet(conn, pmc, owner):
    add_agreement(
        conn, pmc.pmc_id, owner, TODAY - 59 * DAY, approval_limit="1000", bill_markup_percent="10"
    )
    marked = bill(conn, pmc, owner, "200.00", "M-1")
    conn.execute(
        "INSERT INTO trust_bill_markups (bill_id, pmc_id) VALUES (%s, %s)", (marked, pmc.pmc_id)
    )
    coming = bill(conn, pmc, owner, "50.00", "F-1", dated=3)

    held = holds(conn, pmc)

    assert held[marked] == (Decimal("220.00"), Decimal("0.00"), Decimal("0.00"))
    assert coming not in held


def test_money_that_arrives_clears_what_is_short(conn, pmc, owner):
    short = bill(conn, pmc, owner, "800.00", "BIG-1")
    before = holds(conn, pmc)[short]

    transfer_batch(conn, [(pmc.operating_cash, owner, "300.00")], NOW)

    assert before == (Decimal("500.00"), Decimal("300.00"), Decimal("300.00"))
    assert holds(conn, pmc)[short] == (Decimal("800.00"), Decimal("0.00"), Decimal("0.00"))


def test_a_draw_never_pays_what_is_held(conn, pmc, owner):
    bill(conn, pmc, owner, "350.00", "B-1")

    with pytest.raises(psycopg.errors.CheckViolation, match="150.00 is available"):
        draw_owner(conn, pmc.pmc_id, owner, "draw-1", "151.00", NOW)
    draw_owner(conn, pmc.pmc_id, owner, "draw-2", "150.00", NOW)

    assert holds(conn, pmc) == {
        b: (Decimal("350.00"), Decimal("0.00"), Decimal("0.00")) for b in holds(conn, pmc)
    }


def test_paid_and_void_bills_hold_nothing(conn, pmc, owner):
    paid = bill(conn, pmc, owner, "100.00", "P-1", due_in=1)
    gone = bill(conn, pmc, owner, "100.00", "V-1", due_in=2)
    left = bill(conn, pmc, owner, "450.00", "L-1", due_in=3)
    staff = add_staff(conn, pmc.pmc_id)
    before = holds(conn, pmc)[left]

    pay_bill(conn, pmc.pmc_id, paid, NOW)
    void(conn, pmc, gone, staff)

    assert before == (Decimal("300.00"), Decimal("150.00"), Decimal("150.00"))
    assert set(holds(conn, pmc)) == {left}
    # 400.00 left after the 100.00 paid; the void's hold went back to the balance.
    assert holds(conn, pmc)[left] == (Decimal("400.00"), Decimal("50.00"), Decimal("50.00"))


# --- paying in parts ----------------------------------------------------------------------------


def test_the_first_part_takes_the_whole_bill_from_the_owner_and_each_pays_its_part(
    conn, pmc, owner
):
    bill_id = bill(conn, pmc, owner, "400.00", "P-1")

    first = part(conn, pmc, bill_id, "150.00", "pay-1")
    again = part(conn, pmc, bill_id, "150.00", "pay-1")  # a retry
    after_first = (balance(conn, owner), balance(conn, pmc.vendor_payable))
    second = part(conn, pmc, bill_id, "250.00", "pay-2")

    assert again == first
    assert after_first == (Decimal("100.00"), Decimal("250.00"))
    assert (balance(conn, owner), balance(conn, pmc.vendor_payable)) == (
        Decimal("100.00"),
        Decimal("0.00"),
    )
    assert conn.execute(
        "SELECT p.part, p.request_key, t.amount, t.metadata ->> 'memo'"
        " FROM trust_bill_payments AS p JOIN pgledger_transfers AS t ON t.id = p.transfer_id"
        " WHERE p.bill_id = %s AND p.step = 'paid' ORDER BY p.part",
        (bill_id,),
    ).fetchall() == [
        (1, "pay-1", Decimal("150.00"), "Bill P-1 paid in part"),
        (2, "pay-2", Decimal("250.00"), "Bill P-1 paid"),
    ]
    assert second != first
    # Paid in full: trust_pay_bill returns the last payment, and no part is left.
    assert pay_bill(conn, pmc.pmc_id, bill_id, NOW) == second
    with pytest.raises(psycopg.errors.CheckViolation, match="paid in full already"):
        part(conn, pmc, bill_id, "1.00", "pay-3")


@pytest.mark.timing
def test_a_first_payment_waits_for_a_posting_before_taking_any_of_its_accounts(
    conn, database_url, pmc, owner
):
    """A first payment posts twice (the owner's account to the vendor's, then the vendor's to the
    cash), while a posting of one transfer (another bill of the vendor paid, a draw) locks its
    accounts in id order. The payment locks every account it moves up front, in that order, so
    it never holds the vendor's account while it waits for the cash: the nightly soak found that
    deadlock."""
    bill_id = bill(conn, pmc, owner, "100.00", "LOCK-1")
    assert pmc.operating_cash < pmc.vendor_payable  # paying the vendor locks the cash first

    with psycopg.connect(database_url) as posting:
        posting.execute("SET lock_timeout = '10s'")
        # Another bill of the vendor being paid, part way: it holds the cash.
        posting.execute(
            "SELECT 1 FROM pgledger_accounts WHERE id = %s FOR UPDATE", (pmc.operating_cash,)
        )
        thread, pid, outcome = in_background(
            database_url, lambda worker: part(worker, pmc, bill_id, "100.00", "lock-1")
        )
        wait_until_blocked(conn, pid)
        # Then the vendor's account: free, as the payment waits for the cash before taking it.
        posting.execute(
            "SELECT 1 FROM pgledger_accounts WHERE id = %s FOR UPDATE", (pmc.vendor_payable,)
        )
        posting.commit()
    thread.join(timeout=30)

    assert "error" not in outcome, outcome
    assert (balance(conn, owner), balance(conn, pmc.vendor_payable)) == (
        Decimal("400.00"),
        Decimal("0.00"),
    )


def test_a_part_is_never_more_than_is_left_and_a_key_pays_one_payment(conn, pmc, owner):
    bill_id = bill(conn, pmc, owner, "400.00", "P-1")
    other = bill(conn, pmc, owner, "50.00", "P-2")
    part(conn, pmc, bill_id, "300.00", "pay-1")

    with pytest.raises(psycopg.errors.CheckViolation, match="has 100.00 left to pay"):
        part(conn, pmc, bill_id, "100.01", "pay-2")
    with pytest.raises(psycopg.errors.UniqueViolation, match="already paid 300.00"):
        part(conn, pmc, bill_id, "99.00", "pay-1")
    with pytest.raises(psycopg.errors.UniqueViolation, match="already paid 300.00"):
        part(conn, pmc, other, "300.00", "pay-1")
    for bad in ("0", "-1.00", "1.001"):
        with pytest.raises(psycopg.errors.InvalidParameterValue, match="positive amount in cents"):
            part(conn, pmc, bill_id, bad, f"pay-{bad}")
    with pytest.raises(psycopg.errors.InvalidParameterValue, match="needs a request key"):
        part(conn, pmc, bill_id, "1.00", "")


def test_trust_pay_bill_pays_what_is_left_of_a_bill_paid_in_part(conn, pmc, owner):
    bill_id = bill(conn, pmc, owner, "400.00", "P-1")
    part(conn, pmc, bill_id, "150.00", "pay-1")

    rest = pay_bill(conn, pmc.pmc_id, bill_id, NOW)

    assert conn.execute(
        "SELECT amount FROM pgledger_transfers WHERE id = %s", (rest,)
    ).fetchone() == (Decimal("250.00"),)


def test_a_bill_set_aside_before_the_change_pays_in_parts_from_what_was_taken(conn, pmc, owner):
    bill_id = bill(conn, pmc, owner, "400.00", "OLD-1")
    set_aside_bill(conn, pmc.pmc_id, bill_id, at(TODAY - DAY))

    part(conn, pmc, bill_id, "100.00", "pay-1")
    with pytest.raises(psycopg.errors.InvalidParameterValue, match="paid before that"):
        part(conn, pmc, bill_id, "100.00", "pay-2", day=TODAY - 2 * DAY)

    # Taken once, before: the payment took nothing more from the owner.
    assert balance(conn, owner) == Decimal("100.00")
    assert balance(conn, pmc.vendor_payable) == Decimal("300.00")


def test_a_short_owner_pays_no_part(conn, pmc, owner):
    bill_id = bill(conn, pmc, owner, "800.00", "BIG-1")

    with pytest.raises(psycopg.errors.CheckViolation):
        part(conn, pmc, bill_id, "100.00", "pay-1")
    assert balance(conn, owner) == Decimal("500.00")


def test_a_part_waits_for_the_owners_approval(conn, pmc, owner):
    add_agreement(conn, pmc.pmc_id, owner, TODAY - 59 * DAY, approval_limit="100")
    bill_id = bill(conn, pmc, owner, "300.00", "BIG-1")

    with pytest.raises(psycopg.errors.CheckViolation, match="record the owner's approval first"):
        part(conn, pmc, bill_id, "100.00", "pay-1")
    approve_bill(conn, pmc.pmc_id, bill_id, TODAY, "Owner 1")
    part(conn, pmc, bill_id, "100.00", "pay-1")


def test_each_part_counts_toward_the_vendors_year_and_the_reports_show_what_is_left(
    conn, pmc, owner
):
    bill_id = bill(conn, pmc, owner, "400.00", "P-1")
    part(conn, pmc, bill_id, "150.00", "pay-1")

    year = conn.execute(
        "SELECT payments, paid FROM trust_report_vendor_payments(%s, %s)"
        " WHERE vendor = 'Vendor 1' AND payments IS NOT NULL",
        (pmc.pmc_id, TODAY.year),
    ).fetchall()
    unpaid = conn.execute(
        "SELECT reference, amount, set_aside FROM trust_report_unpaid_bills(%s, %s)"
        " WHERE item = 'bill'",
        (pmc.pmc_id, TODAY),
    ).fetchall()
    directory = conn.execute(
        "SELECT unpaid_bills, unpaid, paid_this_year FROM trust_report_vendor_directory(%s, %s)"
        " WHERE item = 'vendor'",
        (pmc.pmc_id, TODAY),
    ).fetchall()

    assert year == [(1, Decimal("150.00"))]
    assert unpaid == [("P-1", Decimal("250.00"), Decimal("250.00"))]
    assert directory == [(1, Decimal("250.00"), Decimal("150.00"))]


def test_an_owner_with_a_bill_paid_in_part_is_not_archived(conn, pmc, owner):
    bill_id = bill(conn, pmc, owner, "400.00", "P-1")
    part(conn, pmc, bill_id, "150.00", "pay-1")
    transfer_batch(conn, [(owner, pmc.operating_cash, "100.00")], NOW)  # nothing left held

    with pytest.raises(psycopg.errors.CheckViolation, match="has a bill not paid"):
        conn.execute(
            "INSERT INTO trust_archivals (pmc_id, owner_id, archived) VALUES (%s, %s, true)",
            (pmc.pmc_id, pmc.owners[0].owner_id),
        )


# --- voids ----------------------------------------------------------------------------------------


def test_staff_void_a_bill_entered_by_mistake_naming_who(conn, pmc, owner):
    staff = add_staff(conn, pmc.pmc_id)
    bill_id = bill(conn, pmc, owner, "200.00", "DUP-1")
    approve_bill(conn, pmc.pmc_id, bill_id, TODAY, "Owner 1")  # a void may follow an approval

    void(conn, pmc, bill_id, staff)

    assert conn.execute(
        "SELECT void, recorded_by, reason FROM trust_bill_declines WHERE bill_id = %s", (bill_id,)
    ).fetchone() == (True, staff, "Entered twice")
    with pytest.raises(psycopg.errors.CheckViolation, match="is void"):
        pay_bill(conn, pmc.pmc_id, bill_id, NOW)
    with pytest.raises(psycopg.errors.UniqueViolation, match="is void already"):
        void(conn, pmc, bill_id, staff)
    with pytest.raises(psycopg.errors.CheckViolation, match="declined or voided"):
        conn.execute(
            "INSERT INTO trust_bill_markups (bill_id, pmc_id) VALUES (%s, %s)",
            (bill_id, pmc.pmc_id),
        )


def test_a_void_names_a_staff_member_and_is_never_the_owners(conn, pmc, owner):
    bill_id = bill(conn, pmc, owner, "200.00", "DUP-1")

    with pytest.raises(psycopg.errors.CheckViolation, match="trust_bill_declines_void_check"):
        void(conn, pmc, bill_id, None)
    staff = add_staff(conn, pmc.pmc_id)
    with acting_as(conn, staff), pytest.raises(psycopg.errors.CheckViolation):
        conn.execute(
            "INSERT INTO trust_bill_declines (pmc_id, bill_id, declined_on, declined_by, reason,"
            " void, owner_id) VALUES (%s, %s, %s, 'Owner 1', 'No', true, %s)",
            (pmc.pmc_id, bill_id, TODAY, pmc.owners[0].owner_id),
        )


def test_no_void_once_money_was_taken_for_the_bill(conn, pmc, owner):
    staff = add_staff(conn, pmc.pmc_id)
    partly = bill(conn, pmc, owner, "200.00", "P-1")
    old = bill(conn, pmc, owner, "100.00", "OLD-1")
    part(conn, pmc, partly, "50.00", "pay-1")
    set_aside_bill(conn, pmc.pmc_id, old, NOW)

    for bill_id in (partly, old):
        with pytest.raises(psycopg.errors.CheckViolation, match="can't be voided"):
            void(conn, pmc, bill_id, staff)


def test_a_declined_bill_is_void_already_and_a_void_one_takes_no_decision(conn, pmc, owner):
    staff = add_staff(conn, pmc.pmc_id)
    declined = bill(conn, pmc, owner, "200.00", "D-1")
    voided = bill(conn, pmc, owner, "200.00", "V-1")
    decline_bill(conn, pmc.pmc_id, declined, TODAY, "Owner 1")
    void(conn, pmc, voided, staff)

    with pytest.raises(psycopg.errors.UniqueViolation, match="declined by its owner"):
        void(conn, pmc, declined, staff)
    with pytest.raises(psycopg.errors.UniqueViolation, match="is void already"):
        approve_bill(conn, pmc.pmc_id, voided, TODAY, "Owner 1")
    with pytest.raises(psycopg.errors.UniqueViolation, match="is void already"):
        decline_bill(conn, pmc.pmc_id, voided, TODAY, "Owner 1")


def test_a_void_releases_what_a_draw_held_back_from_its_day(conn, pmc, owner):
    staff = add_staff(conn, pmc.pmc_id)
    bill_id = bill(conn, pmc, owner, "300.00", "DUP-1")
    unpaid = conn.execute("SELECT trust_unpaid_bills(%s, %s)", (owner, TODAY)).fetchone()[0]

    void(conn, pmc, bill_id, staff)

    assert unpaid == Decimal("300.00")
    assert conn.execute("SELECT trust_unpaid_bills(%s, %s)", (owner, TODAY)).fetchone() == (
        Decimal("0"),
    )
    draw_owner(conn, pmc.pmc_id, owner, "draw-1", "500.00", NOW)


# --- the rest -------------------------------------------------------------------------------------


def test_a_check_number_is_digits_and_only_a_checks(conn, pmc, owner):
    bill_id = bill(conn, pmc, owner, "100.00", "C-1")
    transfer = pay_bill(conn, pmc.pmc_id, bill_id, NOW)

    for method, number in (("check", "12a"), ("check", "1" * 16), ("EFT", "1042")):
        with pytest.raises(psycopg.errors.CheckViolation):
            conn.execute(
                "INSERT INTO trust_payout_methods (pmc_id, transfer_id, method, check_number)"
                " VALUES (%s, %s, %s, %s)",
                (pmc.pmc_id, transfer, method, number),
            )
    conn.execute(
        "INSERT INTO trust_payout_methods (pmc_id, transfer_id, method, check_number)"
        " VALUES (%s, %s, 'check', '001042')",
        (pmc.pmc_id, transfer),
    )


def test_an_owner_is_asked_for_a_bills_money_once_a_day(conn, pmc, owner):
    bill_id = bill(conn, pmc, owner, "800.00", "BIG-1")

    def ask():
        conn.execute(
            "INSERT INTO trust_emails (pmc_id, kind, bill_id, owner_id, to_address, subject, body)"
            " VALUES (%s, 'bill_funds_needed', %s, %s, 'owner@example.com', 'Please send', 'x')",
            (pmc.pmc_id, bill_id, pmc.owners[0].owner_id),
        )

    ask()
    with pytest.raises(psycopg.errors.UniqueViolation):
        ask()
    with pytest.raises(psycopg.errors.CheckViolation):
        conn.execute(
            "INSERT INTO trust_emails (pmc_id, kind, owner_id, to_address, subject, body)"
            " VALUES (%s, 'bill_funds_needed', %s, 'owner@example.com', 'Please send', 'x')",
            (pmc.pmc_id, pmc.owners[0].owner_id),
        )


def test_the_app_pays_parts_and_reads_holds_but_the_ai_does_neither(
    conn, app_conn, ai_conn, pmc, owner
):
    bill_id = bill(conn, pmc, owner, "100.00", "A-1")
    conn.commit()

    part(app_conn, pmc, bill_id, "40.00", f"pay-{uuid.uuid4()}")
    assert app_conn.execute(
        "SELECT count(*) FROM trust_bill_holds(%s)", (pmc.pmc_id,)
    ).fetchone() == (0,)
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        ai_conn.execute("SELECT * FROM trust_bill_holds(%s)", (pmc.pmc_id,))
