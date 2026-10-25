"""An owner's decision on a bill (migration 20261013000041): approved, or declined.

A declined bill is never set aside or paid, and from the day it was declined it holds nothing
back from the owner's draws and no longer shows as unpaid. A bill takes one decision, a decline
only before it is set aside, and a decision taken in the portal names the owner of the bill's
property account.
"""

from datetime import UTC, date, datetime
from decimal import Decimal

import psycopg
import pytest
from helpers import (
    add_agreement,
    approve_bill,
    decline_bill,
    draw_owner,
    enter_bill,
    in_background,
    make_pmc,
    set_aside_bill,
    transfer_batch,
    wait_until_blocked,
)

JAN_1, JAN_15, JAN_20 = date(2026, 1, 1), date(2026, 1, 15), date(2026, 1, 20)


def at(day, hour=12):
    return datetime(day.year, day.month, day.day, hour, tzinfo=UTC)


@pytest.fixture
def pmc(conn):
    return make_pmc(conn)


@pytest.fixture
def funded(conn, pmc):
    """Owner 1's account holding 1000.00, with a 500.00 approval limit and a 100.00 reserve."""
    account = pmc.owners[0].account
    add_agreement(conn, pmc.pmc_id, account, JAN_1, reserve="100", approval_limit="500")
    transfer_batch(conn, [(pmc.operating_cash, account, "1000.00")], at(JAN_1))
    return account


def bill(conn, pmc, account, amount="600.00", reference="ROOF-1"):
    return enter_bill(conn, pmc.pmc_id, pmc.vendor_id, account, reference, JAN_15, JAN_20, amount)


def unpaid_report(conn, pmc, day):
    return conn.execute(
        "SELECT reference, amount FROM trust_report_unpaid_bills(%s, %s) WHERE item = 'bill'",
        (pmc.pmc_id, day),
    ).fetchall()


def owner_bills(conn, pmc, day):
    return conn.execute(
        "SELECT bills, available FROM trust_report_owner_balances(%s, %s)"
        " WHERE property = 'Property 1' AND bills IS NOT NULL",
        (pmc.pmc_id, day),
    ).fetchone()


def test_a_declined_bill_is_never_set_aside(conn, app_conn, pmc, funded):
    roof = bill(conn, pmc, funded)
    decline_bill(
        app_conn,
        pmc.pmc_id,
        roof,
        JAN_20,
        "Owner 1, in the owner portal",
        "Get a second quote",
        pmc.owners[0].owner_id,
    )

    with pytest.raises(psycopg.errors.CheckViolation, match="declined by Owner 1, in the owner"):
        set_aside_bill(conn, pmc.pmc_id, roof, at(JAN_20))
    assert conn.execute(
        "SELECT declined_on, reason FROM trust_bill_declines WHERE bill_id = %s", (roof,)
    ).fetchone() == (JAN_20, "Get a second quote")


def test_a_declined_bill_under_the_limit_is_not_set_aside_either(conn, pmc, funded):
    small = bill(conn, pmc, funded, amount="80.00")
    decline_bill(conn, pmc.pmc_id, small, JAN_15, "Owner 1, by phone")

    with pytest.raises(psycopg.errors.CheckViolation, match="declined"):
        set_aside_bill(conn, pmc.pmc_id, small, at(JAN_15))


def test_from_the_day_it_is_declined_a_bill_holds_nothing_back(conn, pmc, funded):
    roof = bill(conn, pmc, funded)
    decline_bill(conn, pmc.pmc_id, roof, JAN_20, "Owner 1, by email")

    # Before the decline: the 600.00 bill is kept back with the 100.00 reserve.
    assert unpaid_report(conn, pmc, JAN_15) == [("ROOF-1", Decimal("600.00"))]
    assert owner_bills(conn, pmc, JAN_15) == (Decimal("600.00"), Decimal("300.00"))
    assert draw_owner(conn, pmc.pmc_id, funded, "before", None, at(JAN_15)) == Decimal("300.00")
    # From it: nothing kept back for the bill.
    assert unpaid_report(conn, pmc, JAN_20) == []
    assert owner_bills(conn, pmc, JAN_20) == (Decimal("0.00"), Decimal("600.00"))
    assert draw_owner(conn, pmc.pmc_id, funded, "after", None, at(JAN_20)) == Decimal("600.00")


@pytest.mark.parametrize("first", ["approve", "decline"])
def test_a_bill_takes_one_decision(conn, pmc, funded, first):
    roof = bill(conn, pmc, funded)
    decide = {
        "approve": lambda: approve_bill(conn, pmc.pmc_id, roof, JAN_15, "Owner 1, by email"),
        "decline": lambda: decline_bill(conn, pmc.pmc_id, roof, JAN_15, "Owner 1, by email"),
    }
    decide[first]()

    for then in ("approve", "decline"):
        with pytest.raises(psycopg.errors.UniqueViolation, match="decision already"):
            decide[then]()


def test_a_bill_set_aside_is_not_declined(conn, pmc, funded):
    small = bill(conn, pmc, funded, amount="80.00")
    set_aside_bill(conn, pmc.pmc_id, small, at(JAN_15))

    with pytest.raises(
        psycopg.errors.CheckViolation, match="taken from the owner.s account for bill .* already"
    ):
        decline_bill(conn, pmc.pmc_id, small, JAN_20, "Owner 1, by email")


@pytest.mark.parametrize("decision", ["approve", "decline"])
def test_a_decision_in_the_portal_is_the_bills_owners(conn, pmc, funded, decision):
    roof = bill(conn, pmc, funded)
    other = make_pmc(conn)
    act = approve_bill if decision == "approve" else decline_bill

    with pytest.raises(psycopg.errors.CheckViolation, match="only the owner"):
        act(
            conn,
            pmc.pmc_id,
            roof,
            JAN_15,
            "Owner 2, in the owner portal",
            owner_id=pmc.owners[1].owner_id,
        )
    with pytest.raises(psycopg.errors.CheckViolation, match="only the owner"):  # another PMC's
        act(
            conn,
            pmc.pmc_id,
            roof,
            JAN_15,
            "Owner, in the owner portal",
            owner_id=other.owners[0].owner_id,
        )
    act(
        conn,
        pmc.pmc_id,
        roof,
        JAN_15,
        "Owner 1, in the owner portal",
        owner_id=pmc.owners[0].owner_id,
    )


def test_a_bill_of_another_pmc_is_not_decided(conn, pmc, funded):
    other = make_pmc(conn)
    roof = bill(conn, pmc, funded)

    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        decline_bill(conn, other.pmc_id, roof, JAN_15, "Owner 1, by email")


@pytest.mark.parametrize(
    ("column", "value"),
    [("declined_by", ""), ("reason", ""), ("reason", "x" * 1001), ("declined_by", "x" * 201)],
)
def test_a_decline_says_who_and_why(conn, pmc, funded, column, value):
    roof = bill(conn, pmc, funded)
    given = {"declined_by": "Owner 1, by email", "reason": "Too much"} | {column: value}

    with pytest.raises(psycopg.errors.CheckViolation):
        decline_bill(conn, pmc.pmc_id, roof, JAN_15, given["declined_by"], given["reason"])


@pytest.mark.parametrize(
    "statement",
    ["UPDATE trust_bill_declines SET reason = 'x'", "DELETE FROM trust_bill_declines"],
)
def test_declines_are_kept_for_good(conn, pmc, funded, statement):
    decline_bill(conn, pmc.pmc_id, bill(conn, pmc, funded), JAN_15, "Owner 1, by email")

    with pytest.raises(psycopg.errors.RestrictViolation, match="append-only"):
        conn.execute(statement)


def test_the_ai_reads_declines_and_cannot_write_one(conn, ai_conn, pmc, funded):
    roof = bill(conn, pmc, funded)
    decline_bill(conn, pmc.pmc_id, roof, JAN_15, "Owner 1, by email")

    assert ai_conn.execute(
        "SELECT count(*) FROM trust_bill_declines WHERE bill_id = %s", (roof,)
    ).fetchone() == (1,)
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        decline_bill(ai_conn, pmc.pmc_id, bill(conn, pmc, funded, reference="R2"), JAN_15, "AI")


@pytest.mark.timing
def test_a_decline_waits_for_a_set_aside_in_flight_and_is_then_refused(
    conn, database_url, pmc, funded
):
    small = bill(conn, pmc, funded, amount="80.00")

    with psycopg.connect(database_url) as first:
        set_aside_bill(first, pmc.pmc_id, small, at(JAN_15))  # not committed yet
        thread, pid, outcome = in_background(
            database_url,
            lambda worker: decline_bill(worker, pmc.pmc_id, small, JAN_15, "Owner 1, by email"),
        )
        wait_until_blocked(conn, pid)
        first.commit()
    thread.join(timeout=30)

    assert isinstance(outcome.get("error"), psycopg.errors.CheckViolation)
    assert "already; it can't be declined" in str(outcome["error"])
