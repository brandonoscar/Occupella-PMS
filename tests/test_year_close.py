"""Closing a year (migration 20261021000106; the founder's review q116, the founder's call on
10/21: lock it all).

A year closes once, by a named staff member who holds approve, when every trust bank account it
touched is approved through December 31 and no journal entry dated in it waits. From then on no
charge, housing authority's charge or bill is dated in it or before.
"""

from datetime import UTC, date, datetime

import psycopg
import pytest
from helpers import (
    acting_as,
    add_authority,
    add_tenant,
    add_unit,
    approve_bank,
    assist,
    charge,
    decline_entry,
    enter_bill,
    import_statement,
    in_background,
    make_pmc,
    open_lease,
    propose_entry,
    staff_named,
    transfer_batch,
    wait_until_blocked,
)

DEC_1, DEC_31 = date(2025, 12, 1), date(2025, 12, 31)


@pytest.fixture
def pmc(conn):
    return make_pmc(conn)


@pytest.fixture
def closer(conn, pmc):
    return staff_named(conn, pmc.pmc_id, "Closer 1", ["approve"])


def close(conn, pmc_id, year, staff):
    with acting_as(conn, staff):
        return conn.execute("SELECT trust_close_year(%s, %s)", (pmc_id, year)).fetchone()[0]


def december(conn, pmc, bank=None, year=2025):
    """The month-end of December of a year approved for a trust bank account (operating by
    default), from an empty statement: what the books moved is in transit."""
    bank = bank or pmc.operating_bank_id
    staff = staff_named(conn, pmc.pmc_id, "Importer 1")
    import_statement(conn, pmc.pmc_id, bank, staff, date(year, 12, 1), date(year, 12, 31), [])
    approve_bank(conn, pmc.pmc_id, bank, date(year, 12, 31))


def rent_in(conn, pmc, day):
    """Rent received into the operating account on a day."""
    at = datetime(day.year, day.month, day.day, 12, tzinfo=UTC)
    return transfer_batch(conn, [(pmc.operating_cash, pmc.owners[0].account, "100.00")], at)[0]


def a_lease(conn, pmc):
    prop = pmc.owners[0].property_id
    unit = add_unit(conn, pmc.pmc_id, prop)
    tenant = add_tenant(conn, pmc.pmc_id, prop, "Tenant 9")
    return open_lease(conn, pmc.pmc_id, unit, date(2025, 11, 1), None, "1000.00", [tenant])


def test_a_year_closes_once_every_account_it_touched_is_approved_through_its_last_day(
    conn, pmc, closer
):
    rent_in(conn, pmc, date(2025, 12, 5))

    with pytest.raises(psycopg.errors.CheckViolation) as early:
        close(conn, pmc.pmc_id, 2025, closer)
    december(conn, pmc)
    closed = close(conn, pmc.pmc_id, 2025, closer)

    assert "approve month-end through 12/31/2025 first: Synthetic operating trust account" in str(
        early.value
    )
    # The deposit account moved nothing in 2025: it holds nothing back. The same call again
    # returns the same close, naming who closed it.
    assert close(conn, pmc.pmc_id, 2025, closer) == closed
    assert conn.execute(
        "SELECT year, closed_by FROM trust_year_closes WHERE pmc_id = %s", (pmc.pmc_id,)
    ).fetchall() == [(2025, closer)]
    assert conn.execute("SELECT trust_closed_until(%s)", (pmc.pmc_id,)).fetchone() == (DEC_31,)


def test_an_account_opened_after_the_year_holds_nothing_back_but_one_in_it_does(conn, pmc, closer):
    december(conn, pmc)
    # The deposit account had money in November 2025, and no month-end since.
    transfer_batch(
        conn,
        [(pmc.deposit_cash, pmc.tenant_deposit, "500.00")],
        datetime(2025, 11, 3, 12, tzinfo=UTC),
    )

    with pytest.raises(psycopg.errors.CheckViolation, match="Synthetic security_deposit"):
        close(conn, pmc.pmc_id, 2025, closer)
    december(conn, pmc, pmc.deposit_bank_id)
    close(conn, pmc.pmc_id, 2025, closer)


def test_after_a_close_nothing_is_charged_or_billed_in_the_closed_year(conn, pmc, closer):
    lease = a_lease(conn, pmc)
    authority = add_authority(conn, pmc.pmc_id)
    assist(conn, pmc.pmc_id, lease, authority, date(2025, 11, 1), "300.00")
    charge(conn, pmc.pmc_id, lease, date(2025, 12, 1), "1000.00")  # before the close: it stays
    december(conn, pmc)
    close(conn, pmc.pmc_id, 2025, closer)

    refused = []
    for late in (
        lambda: charge(conn, pmc.pmc_id, lease, date(2025, 12, 15), "40.00", "fee", "Late fee"),
        lambda: charge(conn, pmc.pmc_id, lease, date(2024, 6, 1), "10.00", "credit", "Old"),
        lambda: enter_bill(
            conn,
            pmc.pmc_id,
            pmc.vendor_id,
            pmc.owners[0].account,
            "OLD-1",
            DEC_31,
            date(2026, 1, 10),
            "80.00",
        ),  # fmt: skip
        lambda: conn.execute(
            "INSERT INTO trust_assistance_charges (pmc_id, lease_id, housing_authority_id,"
            " due_on, kind, amount, memo, source) VALUES (%s, %s, %s, %s, 'charge', 25.00,"
            " 'Owed for December', 'staff')",
            (pmc.pmc_id, lease, authority, date(2025, 12, 20)),
        ),
    ):
        with pytest.raises(psycopg.errors.CheckViolation) as no:
            late()
        refused.append(str(no.value).splitlines()[0])
    charge(conn, pmc.pmc_id, lease, date(2026, 1, 1), "40.00", "fee", "Late fee")
    enter_bill(
        conn, pmc.pmc_id, pmc.vendor_id, pmc.owners[0].account, "NEW-1", date(2026, 1, 2),
        date(2026, 1, 10), "80.00",
    )  # fmt: skip

    then = "can't be added; date it 01/01/2026 or later"
    assert refused == [
        f"trust: 2025 is closed: a charge dated 12/15/2025 {then}",
        f"trust: 2025 is closed: a charge dated 06/01/2024 {then}",
        f"trust: 2025 is closed: a bill dated 12/31/2025 {then}",
        f"trust: 2025 is closed: a housing authority's charge dated 12/20/2025 {then}",
    ]
    assert conn.execute(
        "SELECT count(*) FROM trust_charges WHERE lease_id = %s AND due_on = %s",
        (lease, DEC_1),
    ).fetchone() == (1,)


def test_a_year_not_over_or_with_nothing_reconciled_is_not_closed(conn, pmc, closer):
    this_year = datetime.now(UTC).year

    with pytest.raises(psycopg.errors.InvalidParameterValue, match="has not ended yet"):
        close(conn, pmc.pmc_id, this_year, closer)
    with pytest.raises(psycopg.errors.CheckViolation, match="there is no 2025 to close"):
        close(conn, pmc.pmc_id, 2025, closer)
    with pytest.raises(psycopg.errors.InvalidParameterValue, match="not a year to close"):
        close(conn, pmc.pmc_id, 1999, closer)


def test_journal_entries_waiting_in_the_year_hold_the_close_until_decided(conn, pmc, closer):
    december(conn, pmc)
    proposer = staff_named(conn, pmc.pmc_id, "Proposer 1")
    entry = propose_entry(
        conn, pmc.pmc_id, proposer, date(2025, 12, 10), "A correction",
        [(pmc.owners[0].account, pmc.owners[1].account, "1.00")],
    )  # fmt: skip

    with pytest.raises(psycopg.errors.CheckViolation) as waiting:
        close(conn, pmc.pmc_id, 2025, closer)
    decline_entry(conn, pmc.pmc_id, entry, closer)
    close(conn, pmc.pmc_id, 2025, closer)

    assert "1 journal entry dated in 2025 or before waits for a decision" in str(waiting.value)


def test_only_a_named_staff_member_who_holds_approve_closes_a_year(conn, pmc):
    december(conn, pmc)
    clerk = staff_named(conn, pmc.pmc_id, "Clerk 1")

    with pytest.raises(psycopg.errors.InsufficientPrivilege, match="name the staff member"):
        conn.execute("SELECT trust_close_year(%s, 2025)", (pmc.pmc_id,))
    with pytest.raises(psycopg.errors.InsufficientPrivilege, match="may not close a year"):
        close(conn, pmc.pmc_id, 2025, clerk)


def test_a_later_close_closes_every_year_before_and_nothing_reopens(conn, pmc, closer):
    december(conn, pmc, year=2024)
    staff = staff_named(conn, pmc.pmc_id, "Importer 1")
    import_statement(conn, pmc.pmc_id, pmc.operating_bank_id, staff, date(2025, 1, 1), DEC_31, [])
    approve_bank(conn, pmc.pmc_id, pmc.operating_bank_id, DEC_31)
    close(conn, pmc.pmc_id, 2025, closer)

    with pytest.raises(psycopg.errors.CheckViolation, match="2025 is closed already"):
        close(conn, pmc.pmc_id, 2024, closer)
    for change in ("UPDATE trust_year_closes SET year = 2024", "DELETE FROM trust_year_closes"):
        with pytest.raises(psycopg.errors.RestrictViolation, match="append-only"):
            conn.execute(change)


def test_the_app_closes_through_the_function_and_the_ai_reads_the_closes(
    conn, app_conn, ai_conn, pmc, closer
):
    december(conn, pmc)
    close(app_conn, pmc.pmc_id, 2025, closer)

    assert ai_conn.execute(
        "SELECT year FROM trust_year_closes WHERE pmc_id = %s", (pmc.pmc_id,)
    ).fetchall() == [(2025,)]
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        app_conn.execute(
            "INSERT INTO trust_year_closes (pmc_id, year, closed_by) VALUES (%s, 2024, %s)",
            (pmc.pmc_id, closer),
        )
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        close(ai_conn, pmc.pmc_id, 2025, closer)


@pytest.mark.timing
def test_a_charge_dated_in_the_year_waits_for_a_close_under_way_and_then_is_refused(
    conn, database_url, pmc, closer
):
    lease = a_lease(conn, pmc)
    december(conn, pmc)
    with psycopg.connect(database_url) as closing:
        close(closing, pmc.pmc_id, 2025, closer)  # not committed yet
        thread, pid, outcome = in_background(
            database_url,
            lambda w: charge(w, pmc.pmc_id, lease, date(2025, 12, 20), "5.00", "fee", "Key"),
        )
        wait_until_blocked(conn, pid)
        closing.commit()
    thread.join(timeout=30)

    assert isinstance(outcome.get("error"), psycopg.errors.CheckViolation), outcome
    assert "2025 is closed" in str(outcome["error"])
