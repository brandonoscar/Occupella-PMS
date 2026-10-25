"""A markup on a bill (migration 20261018000083; the founder's review, q065; docs/law.md).

The owner's management agreement says what percent of a bill the company may add (0 unless it
says so). Staff add it to a bill before it is set aside: the agreement's percent in force on the
bill's date, to the cent. Setting the bill aside then moves the markup from the owner's account
into the company's fee account with the bill's own money, both or neither; until then draws hold
it back with the bill. The reports count it with the management fees. Every name here is
synthetic."""

from datetime import UTC, date, datetime
from decimal import Decimal

import psycopg
import pytest
from helpers import (
    acting_as,
    add_agreement,
    add_staff,
    balance,
    bank_sums,
    contribute,
    decline_bill,
    draw_owner,
    enter_bill,
    make_pmc,
    pay_bill,
    set_aside_bill,
)

SEP_1, OCT_1, OCT_5, OCT_20 = (
    date(2026, 9, 1),
    date(2026, 10, 1),
    date(2026, 10, 5),
    date(2026, 10, 20),
)


def at(day):
    return datetime(day.year, day.month, day.day, tzinfo=UTC)


@pytest.fixture
def pmc(conn):
    pmc = make_pmc(conn)
    add_agreement(
        conn,
        pmc.pmc_id,
        pmc.owners[0].account,
        SEP_1,
        approval_limit="1000.00",
        bill_markup_percent="10",
    )
    contribute(conn, pmc.pmc_id, pmc.owners[0].account, "in-1", "1000.00", at(SEP_1))
    return pmc


def bill(conn, pmc, reference, amount="250.00", bill_date=OCT_5, n=0):
    return enter_bill(
        conn, pmc.pmc_id, pmc.vendor_id, pmc.owners[n].account, reference, bill_date, OCT_20, amount
    )


def mark_up(conn, pmc, bill_id, percent=None, amount=None):
    return conn.execute(
        "INSERT INTO trust_bill_markups (bill_id, pmc_id, percent, amount)"
        " VALUES (%s, %s, %s, %s) RETURNING percent, amount",
        (bill_id, pmc.pmc_id, percent, amount),
    ).fetchone()


def test_the_markup_is_taken_with_the_bills_money_into_the_companys_fee_account(conn, pmc):
    fix = bill(conn, pmc, "FIX-1")
    assert mark_up(conn, pmc, fix) == (Decimal("10"), Decimal("25.00"))
    set_aside_bill(conn, pmc.pmc_id, fix, at(OCT_20))
    assert balance(conn, pmc.owners[0].account) == Decimal("725.00")
    assert balance(conn, pmc.vendor_payable) == Decimal("250.00")
    assert balance(conn, pmc.pmc_income) == Decimal("25.00")
    assert conn.execute(
        "SELECT t.amount, t.metadata ->> 'memo' FROM trust_bill_markup_fees AS f"
        " JOIN pgledger_transfers AS t ON t.id = f.transfer_id WHERE f.bill_id = %s",
        (fix,),
    ).fetchone() == (Decimal("25.00"), "Markup of 10% on bill FIX-1")
    # Set aside again (a retry) takes nothing more; the vendor is paid the bill alone.
    set_aside_bill(conn, pmc.pmc_id, fix, at(OCT_20))
    pay_bill(conn, pmc.pmc_id, fix, at(OCT_20))
    assert balance(conn, pmc.pmc_income) == Decimal("25.00")
    # Book cash is kept negative (pgledger): 1000.00 in, 250.00 out.
    assert balance(conn, pmc.operating_cash) == Decimal("-750.00")
    assert all(total == 0 for total in bank_sums(conn, pmc.pmc_id).values())


def test_the_percent_is_the_agreements_on_the_bills_date_whatever_is_given(conn, pmc):
    add_agreement(
        conn, pmc.pmc_id, pmc.owners[0].account, date(2026, 10, 10), bill_markup_percent="12.5"
    )
    before = bill(conn, pmc, "FIX-2", amount="99.99", bill_date=OCT_5)
    after = bill(conn, pmc, "FIX-3", amount="99.99", bill_date=date(2026, 10, 12))
    assert mark_up(conn, pmc, before, Decimal("50"), Decimal("1.00")) == (
        Decimal("10"),
        Decimal("10.00"),
    )
    # 12.5% of 99.99 is 12.49875: to the cent.
    assert mark_up(conn, pmc, after) == (Decimal("12.5"), Decimal("12.50"))


def test_a_markup_only_where_the_agreement_sets_one(conn, pmc):
    # Owner 2 has no agreement; then one with no markup.
    theirs = bill(conn, pmc, "FIX-4", n=1)
    with pytest.raises(psycopg.errors.CheckViolation, match="sets no markup on bills"):
        mark_up(conn, pmc, theirs)
    add_agreement(conn, pmc.pmc_id, pmc.owners[1].account, SEP_1)
    with pytest.raises(psycopg.errors.CheckViolation, match="sets no markup on bills"):
        mark_up(conn, pmc, theirs)
    # A bill dated before the agreement starts is under none.
    early = bill(conn, pmc, "FIX-5", bill_date=date(2026, 8, 31))
    with pytest.raises(psycopg.errors.CheckViolation, match="sets no markup on bills"):
        mark_up(conn, pmc, early)
    with pytest.raises(psycopg.errors.CheckViolation, match="bill_markup_percent"):
        add_agreement(conn, pmc.pmc_id, pmc.owners[1].account, OCT_1, bill_markup_percent="101")


def test_a_markup_comes_once_before_set_aside_and_never_on_a_declined_bill(conn, pmc):
    aside = bill(conn, pmc, "FIX-6")
    set_aside_bill(conn, pmc.pmc_id, aside, at(OCT_20))
    with pytest.raises(psycopg.errors.CheckViolation, match="taken for bill .* already"):
        mark_up(conn, pmc, aside)
    declined = bill(conn, pmc, "FIX-7")
    decline_bill(conn, pmc.pmc_id, declined, OCT_5, "Owner 1", "Not this one")
    with pytest.raises(psycopg.errors.CheckViolation, match="declined"):
        mark_up(conn, pmc, declined)
    twice = bill(conn, pmc, "FIX-8")
    mark_up(conn, pmc, twice)
    with pytest.raises(psycopg.errors.UniqueViolation):
        mark_up(conn, pmc, twice)
    tiny = bill(conn, pmc, "FIX-9", amount="0.04")
    with pytest.raises(psycopg.errors.CheckViolation, match="comes to nothing"):
        mark_up(conn, pmc, tiny)
    other = make_pmc(conn)
    with pytest.raises(psycopg.errors.InvalidParameterValue, match="no bill"):
        mark_up(conn, other, twice)


def test_draws_hold_a_bill_back_with_its_markup_until_it_is_set_aside(conn, pmc):
    fix = bill(conn, pmc, "FIX-10", amount="300.00")
    mark_up(conn, pmc, fix)
    account = pmc.owners[0].account
    assert conn.execute("SELECT trust_unpaid_bills(%s, %s)", (account, OCT_20)).fetchone() == (
        Decimal("330.00"),
    )
    assert conn.execute(
        "SELECT bills, available FROM trust_report_owner_balances(%s, %s)"
        " WHERE item = 'owner property' AND property = 'Property 1'",
        (pmc.pmc_id, OCT_20),
    ).fetchone() == (Decimal("330.00"), Decimal("670.00"))
    with pytest.raises(psycopg.errors.CheckViolation, match="670.00 is available"):
        draw_owner(conn, pmc.pmc_id, account, "draw-1", "700.00", at(OCT_20))
    assert draw_owner(conn, pmc.pmc_id, account, "draw-2", None, at(OCT_20)) == Decimal("670.00")


def test_an_owner_short_of_the_markup_has_nothing_set_aside(conn, pmc):
    account = pmc.owners[1].account
    add_agreement(
        conn, pmc.pmc_id, account, SEP_1, approval_limit="1000.00", bill_markup_percent="10"
    )
    contribute(conn, pmc.pmc_id, account, "in-2", "260.00", at(SEP_1))
    fix = bill(conn, pmc, "FIX-11", n=1)
    mark_up(conn, pmc, fix)
    with pytest.raises(psycopg.errors.CheckViolation):
        set_aside_bill(conn, pmc.pmc_id, fix, at(OCT_20))
    assert balance(conn, account) == Decimal("260.00")
    assert conn.execute(
        "SELECT count(*) FROM trust_bill_payments WHERE bill_id = %s", (fix,)
    ).fetchone() == (0,)


def test_markups_are_append_only_audited_and_name_who(conn, pmc):
    clerk = add_staff(conn, pmc.pmc_id, "Clerk 1")
    fix = bill(conn, pmc, "FIX-12")
    with acting_as(conn, clerk):
        mark_up(conn, pmc, fix)
    set_aside_bill(conn, pmc.pmc_id, fix, at(OCT_20))
    assert conn.execute(
        "SELECT recorded_by FROM trust_bill_markups WHERE bill_id = %s", (fix,)
    ).fetchone() == (clerk,)
    assert conn.execute(
        "SELECT table_name, count(*) FROM trust_audit_log WHERE pmc_id = %s"
        " AND table_name IN ('trust_bill_markups', 'trust_bill_markup_fees')"
        " GROUP BY table_name ORDER BY table_name",
        (pmc.pmc_id,),
    ).fetchall() == [("trust_bill_markup_fees", 1), ("trust_bill_markups", 1)]
    for query in (
        "UPDATE trust_bill_markups SET amount = 1",
        "DELETE FROM trust_bill_markup_fees",
    ):
        with pytest.raises(psycopg.errors.RestrictViolation, match="append-only"):
            conn.execute(query)


def test_a_markup_is_added_at_read_committed(conn, pmc):
    fix = bill(conn, pmc, "FIX-13")
    with conn.transaction():
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
        with pytest.raises(psycopg.errors.InvalidTransactionState):
            mark_up(conn, pmc, fix)
        raise psycopg.Rollback()


def test_the_reports_count_a_markup_with_the_management_fees(conn, pmc):
    fix = bill(conn, pmc, "FIX-14")
    mark_up(conn, pmc, fix)
    set_aside_bill(conn, pmc.pmc_id, fix, at(OCT_20))
    owner = pmc.owners[0].owner_id
    statement = dict(
        conn.execute(
            "SELECT item, amount FROM trust_report_income_statement(%s, %s, %s, %s)"
            " WHERE property = 'Property 1'",
            (pmc.pmc_id, owner, OCT_1, date(2026, 10, 31)),
        ).fetchall()
    )
    assert statement["expense: management fees"] == Decimal("25.00")
    assert statement["expense: bills not in an expense account"] == Decimal("250.00")
    year = dict(
        conn.execute(
            "SELECT item, amount FROM trust_report_owner_year_end(%s, %s, 2026)"
            " WHERE property = 'Property 1'",
            (pmc.pmc_id, owner),
        ).fetchall()
    )
    assert (year["management fees"], year["bills paid"], year["other"]) == (
        Decimal("-25.00"),
        Decimal("-250.00"),
        Decimal("0.00"),
    )
