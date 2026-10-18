"""Owners' fees (migration 20261018000082; the founder's review, q049 to q051).

The nightly run takes last month's management fees by itself once a company turns it on, from
its day of the month, each account once; one it can't take from is left for staff. Staff take
other fees from an owner (an inspection, a lease-up, another) into the company's fee account,
once per request key, only from what the owner holds. The income statement and the year-end
package count a lease-up fee with the leasing fees and the others with the management fees.
Every name here is synthetic."""

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
    give_roles,
    make_pmc,
    post_management_fee,
)

from pms import nightly

OCT_1, NOV_1 = date(2026, 10, 1), date(2026, 11, 1)


def at(day):
    return datetime(day.year, day.month, day.day, tzinfo=UTC)


@pytest.fixture
def pmc(conn):
    pmc = make_pmc(conn)
    for owner in pmc.owners:
        add_agreement(conn, pmc.pmc_id, owner.account, date(2026, 9, 1), minimum_fee="100.00")
    contribute(conn, pmc.pmc_id, pmc.owners[0].account, "in-1", "500.00", at(OCT_1))
    return pmc


def turn_on(conn, pmc_id, automatic=True, day=5, by=None):
    with acting_as(conn, by):
        conn.execute(
            "INSERT INTO trust_management_fee_settings (pmc_id, automatic, day_of_month)"
            " VALUES (%s, %s, %s)",
            (pmc_id, automatic, day),
        )


def take_monthly(conn, pmc_id, day):
    return conn.execute(
        "SELECT trust_take_monthly_management_fees(%s, %s)", (pmc_id, day)
    ).fetchone()[0]


def fees(conn, pmc_id):
    return conn.execute(
        "SELECT ledger_account_id, period_start, period_end, fee FROM trust_management_fees"
        " WHERE pmc_id = %s ORDER BY ledger_account_id",
        (pmc_id,),
    ).fetchall()


def test_nothing_is_taken_until_a_company_turns_it_on_and_its_day_comes(conn, pmc):
    assert take_monthly(conn, pmc.pmc_id, date(2026, 11, 20)) == 0  # never turned on
    turn_on(conn, pmc.pmc_id, day=5)
    assert take_monthly(conn, pmc.pmc_id, date(2026, 11, 4)) == 0  # its day hasn't come
    turn_on(conn, pmc.pmc_id, automatic=False, day=5)
    assert take_monthly(conn, pmc.pmc_id, date(2026, 11, 5)) == 0  # turned off again
    assert fees(conn, pmc.pmc_id) == []


def test_last_months_fee_is_taken_once_from_each_account_that_holds_it(conn, pmc):
    turn_on(conn, pmc.pmc_id, day=5)
    first, second = pmc.owners

    # Owner 2 holds nothing: their fee is left, and Owner 1's taken.
    assert take_monthly(conn, pmc.pmc_id, date(2026, 11, 5)) == 1
    assert fees(conn, pmc.pmc_id) == [(first.account, OCT_1, NOV_1, Decimal("100.00"))]
    assert balance(conn, first.account) == Decimal("400.00")
    assert balance(conn, pmc.pmc_income) == Decimal("100.00")
    assert conn.execute(
        "SELECT t.event_at, t.metadata ->> 'memo' FROM trust_management_fees AS f"
        " JOIN pgledger_transfers AS t ON t.id = f.transfer_id WHERE f.ledger_account_id = %s",
        (first.account,),
    ).fetchone() == (at(date(2026, 11, 5)), "Management fee 2026-10-01 to 2026-10-31")
    # Once per account and month, however many nights.
    assert take_monthly(conn, pmc.pmc_id, date(2026, 11, 6)) == 0
    # Once Owner 2 has the money, the next night takes theirs.
    contribute(conn, pmc.pmc_id, second.account, "in-2", "150.00", at(date(2026, 11, 6)))
    assert take_monthly(conn, pmc.pmc_id, date(2026, 11, 7)) == 1
    assert [f[0] for f in fees(conn, pmc.pmc_id)] == sorted([first.account, second.account])
    assert all(total == 0 for total in bank_sums(conn, pmc.pmc_id).values())


def test_a_fee_taken_by_hand_or_terms_from_mid_month_are_left_as_they_are(conn, pmc):
    turn_on(conn, pmc.pmc_id, day=1)
    first, second = pmc.owners
    # Staff took Owner 1's October by hand, for a period of their own.
    post_management_fee(conn, pmc.pmc_id, first.account, OCT_1, date(2026, 10, 16), at(OCT_1))
    # Owner 2's terms in force from mid-October only.
    other = make_pmc(conn, owners=1)
    add_agreement(conn, other.pmc_id, other.owners[0].account, date(2026, 10, 15), "10")
    contribute(conn, other.pmc_id, other.owners[0].account, "in-3", "500.00", at(OCT_1))
    turn_on(conn, other.pmc_id, day=1)

    assert take_monthly(conn, pmc.pmc_id, NOV_1) == 0  # Owner 2 holds nothing either
    assert take_monthly(conn, other.pmc_id, NOV_1) == 0
    assert fees(conn, other.pmc_id) == []
    assert [(f[0], f[2]) for f in fees(conn, pmc.pmc_id)] == [(first.account, date(2026, 10, 16))]
    # In December, November's comes from both, terms in force all of it.
    contribute(conn, pmc.pmc_id, second.account, "in-4", "150.00", at(NOV_1))
    assert take_monthly(conn, pmc.pmc_id, date(2026, 12, 1)) == 2
    assert take_monthly(conn, other.pmc_id, date(2026, 12, 1)) == 1


def test_the_nightly_run_takes_them_as_the_app(conn, app_conn, pmc):
    turn_on(conn, pmc.pmc_id, day=2)
    first = nightly.run_pmc(app_conn, pmc.pmc_id, date(2026, 11, 2), "https://pms.example.com")
    again = nightly.run_pmc(app_conn, pmc.pmc_id, date(2026, 11, 3), "https://pms.example.com")
    assert (first.management_fees, again.management_fees) == (1, 0)
    assert [f[0] for f in fees(conn, pmc.pmc_id)] == [pmc.owners[0].account]


def test_settings_are_append_only_audited_name_who_and_keep_to_the_calendar(conn, pmc):
    clerk = add_staff(conn, pmc.pmc_id, "Clerk 1")
    turn_on(conn, pmc.pmc_id, day=28, by=clerk)
    assert conn.execute(
        "SELECT recorded_by FROM trust_management_fee_settings WHERE pmc_id = %s",
        (pmc.pmc_id,),
    ).fetchall() == [(clerk,)]
    assert conn.execute(
        "SELECT count(*) FROM trust_audit_log WHERE table_name = 'trust_management_fee_settings'"
        " AND pmc_id = %s",
        (pmc.pmc_id,),
    ).fetchone() == (1,)
    for day in (0, 29):
        with pytest.raises(psycopg.errors.CheckViolation):
            turn_on(conn, pmc.pmc_id, day=day)
    with pytest.raises(psycopg.errors.RestrictViolation, match="append-only"):
        conn.execute("UPDATE trust_management_fee_settings SET automatic = false")
    with pytest.raises(psycopg.errors.InvalidParameterValue):
        take_monthly(conn, pmc.pmc_id, None)


# --- other fees from an owner ---------------------------------------------------------------------


def take_fee(conn, pmc, key, amount="75.00", kind="inspection", memo="Move-in inspection", n=0):
    return conn.execute(
        "SELECT trust_take_owner_fee(%s, %s, %s, %s, %s, %s, %s)",
        (
            pmc.pmc_id,
            pmc.owners[n].account,
            kind,
            None if amount is None else Decimal(amount),
            memo,
            key,
            at(date(2026, 10, 20)),
        ),
    ).fetchone()[0]


def test_a_fee_moves_from_the_owner_to_the_companys_fee_account_once_per_key(conn, pmc):
    transfer = take_fee(conn, pmc, "fee-1")
    assert balance(conn, pmc.owners[0].account) == Decimal("425.00")
    assert balance(conn, pmc.pmc_income) == Decimal("75.00")
    assert conn.execute(
        "SELECT from_account_id, to_account_id, amount, metadata ->> 'memo'"
        " FROM pgledger_transfers WHERE id = %s",
        (transfer,),
    ).fetchone() == (
        pmc.owners[0].account,
        pmc.pmc_income,
        Decimal("75.00"),
        "Inspection fee: Move-in inspection",
    )
    # The same request again takes nothing more; another fee under its key is refused.
    assert take_fee(conn, pmc, "fee-1") == transfer
    with pytest.raises(psycopg.errors.UniqueViolation, match="already took a fee"):
        take_fee(conn, pmc, "fee-1", amount="80.00")
    assert balance(conn, pmc.owners[0].account) == Decimal("425.00")
    take_fee(conn, pmc, "fee-2", amount="150.00", kind="lease_up", memo="Unit 1 leased")
    assert conn.execute(
        "SELECT kind, amount, memo FROM trust_owner_fees WHERE pmc_id = %s ORDER BY created_at",
        (pmc.pmc_id,),
    ).fetchall() == [
        ("inspection", Decimal("75.00"), "Move-in inspection"),
        ("lease_up", Decimal("150.00"), "Unit 1 leased"),
    ]
    assert all(total == 0 for total in bank_sums(conn, pmc.pmc_id).values())


def test_a_fee_is_only_what_the_owner_holds_of_a_kind_with_what_it_is_for(conn, pmc):
    with pytest.raises(psycopg.errors.CheckViolation, match="below zero"):
        take_fee(conn, pmc, "fee-3", amount="500.01")
    with pytest.raises(psycopg.errors.CheckViolation, match="below zero"):
        take_fee(conn, pmc, "fee-4", n=1)  # Owner 2 holds nothing
    for key, changes in (
        ("fee-5", {"kind": "markup"}),
        ("fee-6", {"amount": "0"}),
        ("fee-7", {"amount": None}),
        ("fee-8", {"memo": "  "}),
        ("", {}),
    ):
        with pytest.raises(psycopg.errors.InvalidParameterValue):
            take_fee(conn, pmc, key, **changes)
    other = make_pmc(conn)
    with pytest.raises(psycopg.errors.InvalidParameterValue, match="not an owner's property"):
        conn.execute(
            "SELECT trust_take_owner_fee(%s, %s, 'other', 1, 'x', 'fee-9', now())",
            (other.pmc_id, pmc.owners[0].account),
        )
    assert conn.execute(
        "SELECT count(*) FROM trust_owner_fees WHERE pmc_id = %s", (pmc.pmc_id,)
    ).fetchone() == (0,)


def test_only_staff_who_disburse_take_a_fee_at_read_committed(conn, pmc):
    clerk = add_staff(conn, pmc.pmc_id, "Clerk 2")
    with acting_as(conn, clerk), pytest.raises(psycopg.errors.InsufficientPrivilege):
        take_fee(conn, pmc, "fee-10")
    give_roles(conn, pmc.pmc_id, clerk, ["disburse"])
    with acting_as(conn, clerk):
        take_fee(conn, pmc, "fee-11")
    assert conn.execute(
        "SELECT recorded_by FROM trust_owner_fees WHERE request_key = 'fee-11'"
    ).fetchone() == (clerk,)
    with conn.transaction():
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
        with pytest.raises(psycopg.errors.InvalidTransactionState, match="READ COMMITTED"):
            take_fee(conn, pmc, "fee-12")
        raise psycopg.Rollback()


def test_owner_fees_are_append_only_and_audited(conn, pmc):
    take_fee(conn, pmc, "fee-13")
    assert conn.execute(
        "SELECT count(*) FROM trust_audit_log WHERE table_name = 'trust_owner_fees'"
        " AND pmc_id = %s",
        (pmc.pmc_id,),
    ).fetchone() == (1,)
    for query in ("UPDATE trust_owner_fees SET amount = 1", "DELETE FROM trust_owner_fees"):
        with pytest.raises(psycopg.errors.RestrictViolation, match="append-only"):
            conn.execute(query)


def test_the_reports_count_a_lease_up_fee_as_leasing_and_the_others_as_management(conn, pmc):
    take_fee(conn, pmc, "fee-14", amount="75.00", kind="inspection")
    take_fee(conn, pmc, "fee-15", amount="20.00", kind="other", memo="Key copies")
    take_fee(conn, pmc, "fee-16", amount="150.00", kind="lease_up", memo="Unit 1 leased")
    owner = pmc.owners[0].owner_id
    statement = dict(
        conn.execute(
            "SELECT item, amount FROM trust_report_income_statement(%s, %s, %s, %s)"
            " WHERE property = 'Property 1'",
            (pmc.pmc_id, owner, OCT_1, date(2026, 10, 31)),
        ).fetchall()
    )
    assert statement["expense: management fees"] == Decimal("95.00")
    assert statement["expense: leasing fees"] == Decimal("150.00")
    year = dict(
        conn.execute(
            "SELECT item, amount FROM trust_report_owner_year_end(%s, %s, 2026)"
            " WHERE property = 'Property 1'",
            (pmc.pmc_id, owner),
        ).fetchall()
    )
    assert (year["management fees"], year["leasing fees"], year["other"]) == (
        Decimal("-95.00"),
        Decimal("-150.00"),
        Decimal("0.00"),
    )
