"""Interest on security deposits (migration 20261022000122; the founder's review q041): a
property's yearly rate from a day; each day's deposit at that day's rate over the days in its year,
to the cent per deposit; paid for days in a row, once per request key, as a credit against a
lease's rent (moving no money) or added to its deposits from the owner's account with its cash;
none once the deposit went back; append-only, and the AI role reads it. Every name is synthetic."""

from datetime import UTC, date, datetime
from decimal import Decimal

import psycopg
import pytest
from helpers import (
    acting_as,
    add_staff,
    add_tenant,
    add_unit,
    balance,
    bank_sums,
    deposit_interest_terms,
    give_roles,
    make_pmc,
    open_account,
    open_lease,
    pay_deposit_interest,
    return_deposit,
    set_ownership,
    transfer_batch,
)

JAN_1_2025, JUN_30_2025, DEC_31_2025 = date(2025, 1, 1), date(2025, 6, 30), date(2025, 12, 31)
JAN_5_2026 = datetime(2026, 1, 5, 12, tzinfo=UTC)


def at(day: date) -> datetime:
    return datetime(day.year, day.month, day.day, 12, tzinfo=UTC)


@pytest.fixture
def pmc(conn):
    return make_pmc(conn)


@pytest.fixture
def lease(conn, pmc):
    """Tenant 1's lease of Property 1's unit 1A from 2025-01-01, month to month, with 1200.00 of
    deposit since that day; the property's deposits earn 1% a year from then."""
    unit = add_unit(conn, pmc.pmc_id, pmc.owners[0].property_id, "1A")
    tenant = conn.execute(
        "SELECT tenant_id FROM trust_ledger_accounts WHERE ledger_account_id = %s",
        (pmc.tenant_deposit,),
    ).fetchone()[0]
    made = open_lease(conn, pmc.pmc_id, unit, JAN_1_2025, None, "1500.00", [tenant])
    transfer_batch(conn, [(pmc.deposit_cash, pmc.tenant_deposit, Decimal(1200))], at(JAN_1_2025))
    deposit_interest_terms(conn, pmc.pmc_id, pmc.owners[0].property_id, JAN_1_2025, 1)
    return made


def fund(conn, pmc, account, amount, day=JAN_1_2025):
    transfer_batch(conn, [(pmc.operating_cash, account, Decimal(amount))], at(day))


def due(conn, lease, start, through):
    return conn.execute(
        "SELECT tenant, held, interest FROM trust_deposit_interest_due(%s, %s, %s)",
        (lease, start, through),
    ).fetchall()


def payments(conn, lease):
    return conn.execute(
        "SELECT from_on, through_on, amount, paid_as FROM trust_deposit_interest_payments"
        " WHERE lease_id = %s ORDER BY from_on",
        (lease,),
    ).fetchall()


def refused(match, error=psycopg.errors.CheckViolation):
    return pytest.raises(error, match=match)


# --- what it comes to --------------------------------------------------------------------------


def test_each_days_deposit_earns_that_days_rate_over_the_days_in_its_year(conn, pmc, lease):
    deposit_interest_terms(conn, pmc.pmc_id, pmc.owners[0].property_id, date(2025, 7, 1), 2)
    transfer_batch(
        conn, [(pmc.deposit_cash, pmc.tenant_deposit, Decimal(300))], at(date(2025, 10, 1))
    )
    # 1200 x 1% x 181/365 = 5.950685 (January to June), 1200 x 2% x 184/365 = 12.098630 (July
    # to December) and 300 x 2% x 92/365 = 1.512329 (from October 1): 19.561644.
    assert due(conn, lease, JAN_1_2025, DEC_31_2025) == [
        ("Tenant 1", Decimal("1500.00"), Decimal("19.56"))
    ]
    # Before its terms' first day a property's deposits earn nothing; a day's deposit is what
    # it held at that day's end.
    assert due(conn, lease, date(2024, 12, 1), date(2024, 12, 31))[0][2] == Decimal("0.00")
    assert due(conn, lease, date(2025, 10, 1), date(2025, 10, 1))[0][2] == Decimal("0.08")


# --- paid as a rent credit ---------------------------------------------------------------------


def test_a_rent_credit_pays_the_days_once_and_the_next_payment_follows(conn, app_conn, pmc, lease):
    before = bank_sums(conn, pmc.pmc_id), balance(conn, pmc.tenant_deposit)

    paid = pay_deposit_interest(
        app_conn, pmc.pmc_id, lease, DEC_31_2025, "rent_credit", "interest-2025", JAN_5_2026
    )

    assert paid == Decimal("12.00")  # 1200 x 1% for the whole of 2025
    assert conn.execute(
        "SELECT c.kind, c.amount, c.due_on, c.memo FROM trust_deposit_interest_payments AS p"
        " JOIN trust_charges AS c ON c.id = p.charge_id WHERE p.lease_id = %s",
        (lease,),
    ).fetchall() == [
        (
            "credit",
            Decimal("12.00"),
            date(2026, 1, 5),
            "Security deposit interest, 01/01/2025 to 12/31/2025",
        )
    ]
    # A credit moves no money: the lease owes less.
    assert (bank_sums(conn, pmc.pmc_id), balance(conn, pmc.tenant_deposit)) == before
    assert balance(conn, pmc.owners[0].account) == 0

    # The same request again pays nothing more; its key names one payment.
    again = pay_deposit_interest(
        app_conn, pmc.pmc_id, lease, DEC_31_2025, "rent_credit", "interest-2025", JAN_5_2026
    )
    assert again == paid
    assert len(payments(conn, lease)) == 1
    with refused("paid other deposit interest", psycopg.errors.UniqueViolation):
        pay_deposit_interest(
            app_conn,
            pmc.pmc_id,
            lease,
            date(2026, 1, 4),
            "rent_credit",
            "interest-2025",
            JAN_5_2026,
        )
    # Days are paid once, in a row, and only days gone by.
    with refused("is paid through 2025-12-31; pay from 2026-01-01 on"):
        pay_deposit_interest(
            app_conn, pmc.pmc_id, lease, DEC_31_2025, "rent_credit", "again", JAN_5_2026
        )
    with refused("2026-01-10 is after 2026-01-05"):
        pay_deposit_interest(
            app_conn, pmc.pmc_id, lease, date(2026, 1, 10), "rent_credit", "later", JAN_5_2026
        )
    pay_deposit_interest(
        app_conn, pmc.pmc_id, lease, date(2026, 1, 4), "rent_credit", "four-days", JAN_5_2026
    )
    assert payments(conn, lease) == [
        (JAN_1_2025, DEC_31_2025, Decimal("12.00"), "rent_credit"),
        (date(2026, 1, 1), date(2026, 1, 4), Decimal("0.13"), "rent_credit"),
    ]


# --- added to the deposit ----------------------------------------------------------------------


def test_added_to_the_deposit_it_comes_from_the_owner_with_its_cash(conn, app_conn, pmc, lease):
    owner = pmc.owners[0].account
    fund(conn, pmc, owner, 100)

    paid = pay_deposit_interest(
        app_conn, pmc.pmc_id, lease, DEC_31_2025, "deposit", "interest-2025", JAN_5_2026
    )

    assert paid == Decimal("12.00")
    assert balance(conn, pmc.tenant_deposit) == Decimal("1212.00")
    assert balance(conn, owner) == Decimal("88.00")
    assert balance(conn, pmc.deposit_cash) == Decimal("-1212.00")
    assert balance(conn, pmc.operating_cash) == Decimal("-88.00")
    assert set(bank_sums(conn, pmc.pmc_id).values()) == {0}
    assert conn.execute(
        "SELECT x.deposit_account, x.owner_account, x.amount, tr.metadata ->> 'memo'"
        " FROM trust_deposit_interest_lines AS x"
        " JOIN pgledger_transfers AS tr ON tr.id = x.transfer_id WHERE x.pmc_id = %s",
        (pmc.pmc_id,),
    ).fetchall() == [(pmc.tenant_deposit, owner, Decimal("12.00"), "Security deposit interest")]
    # Left in the deposit, it earns interest itself.
    paid = pay_deposit_interest(
        app_conn,
        pmc.pmc_id,
        lease,
        date(2026, 12, 31),
        "deposit",
        "interest-2026",
        datetime(2027, 1, 2, 12, tzinfo=UTC),
    )
    assert paid == Decimal("12.12")


def test_an_owner_short_of_it_is_refused_and_a_staff_member_named_must_disburse(
    conn, app_conn, pmc, lease
):
    with refused("would go below zero"):
        pay_deposit_interest(
            app_conn, pmc.pmc_id, lease, DEC_31_2025, "deposit", "short", JAN_5_2026
        )
    fund(conn, pmc, pmc.owners[0].account, 50)
    clerk = add_staff(conn, pmc.pmc_id, "Clerk 1")
    with (
        acting_as(app_conn, clerk),
        refused("Clerk 1 may not pay out money", psycopg.errors.InsufficientPrivilege),
    ):
        pay_deposit_interest(
            app_conn, pmc.pmc_id, lease, DEC_31_2025, "deposit", "clerk", JAN_5_2026
        )
    # A credit against rent moves no money, and takes no role.
    with acting_as(app_conn, clerk):
        pay_deposit_interest(
            app_conn, pmc.pmc_id, lease, JUN_30_2025, "rent_credit", "clerk-credit", JAN_5_2026
        )
    give_roles(conn, pmc.pmc_id, clerk, ["disburse"])
    with acting_as(app_conn, clerk):
        pay_deposit_interest(
            app_conn, pmc.pmc_id, lease, DEC_31_2025, "deposit", "clerk", JAN_5_2026
        )
    assert conn.execute(
        "SELECT paid_by FROM trust_deposit_interest_payments"
        " WHERE pmc_id = %s AND request_key = 'clerk'",
        (pmc.pmc_id,),
    ).fetchone() == (clerk,)


def test_owners_in_shares_each_pay_their_share_and_two_without_are_named(
    conn, app_conn, pmc, lease
):
    first = pmc.owners[0].account
    second = open_account(
        conn,
        pmc.pmc_id,
        pmc.operating_bank_id,
        "owner_property",
        pmc.owners[1].owner_id,
        pmc.owners[0].property_id,
    )
    fund(conn, pmc, first, 100)
    fund(conn, pmc, second, 100)
    with refused("is not the one owner's account", psycopg.errors.InvalidParameterValue):
        pay_deposit_interest(
            app_conn, pmc.pmc_id, lease, JUN_30_2025, "deposit", "half", JAN_5_2026
        )
    # 1200 x 1% x 181/365 = 5.95, from the account named.
    pay_deposit_interest(
        app_conn, pmc.pmc_id, lease, JUN_30_2025, "deposit", "half", JAN_5_2026, second
    )
    assert balance(conn, second) == Decimal("94.05")

    set_ownership(
        conn,
        pmc.pmc_id,
        pmc.owners[0].property_id,
        date(2026, 1, 1),
        [
            (first, Decimal(60)),
            (second, Decimal(40)),
        ],
    )
    # What was added on January 5 earns from then: 1200 x 1% x 184/365 = 6.049315, so 6.05 by
    # their shares.
    paid = pay_deposit_interest(
        app_conn, pmc.pmc_id, lease, DEC_31_2025, "deposit", "rest", JAN_5_2026
    )
    assert paid == Decimal("6.05")
    assert (balance(conn, first), balance(conn, second)) == (Decimal("96.37"), Decimal("91.63"))
    assert set(bank_sums(conn, pmc.pmc_id).values()) == {0}


# --- at move-out -------------------------------------------------------------------------------


def test_an_ended_lease_takes_it_with_its_deposit_and_nothing_after_the_return(conn, app_conn, pmc):
    prop = pmc.owners[0].property_id
    tenant = add_tenant(conn, pmc.pmc_id, prop, "Tenant 2")
    deposit = open_account(
        conn, pmc.pmc_id, pmc.deposit_bank_id, "tenant_deposit", tenant_id=tenant
    )
    unit = add_unit(conn, pmc.pmc_id, prop, "2B")
    lease = open_lease(conn, pmc.pmc_id, unit, JAN_1_2025, DEC_31_2025, "1500.00", [tenant])
    transfer_batch(conn, [(pmc.deposit_cash, deposit, Decimal(1200))], at(JAN_1_2025))
    deposit_interest_terms(conn, pmc.pmc_id, prop, JAN_1_2025, 1)
    fund(conn, pmc, pmc.owners[0].account, 100)

    with refused("is not in effect on 2026-01-05; add its interest to the deposit"):
        pay_deposit_interest(
            app_conn, pmc.pmc_id, lease, DEC_31_2025, "rent_credit", "credit", JAN_5_2026
        )
    # Held until it goes back, the deposit earns to the day before: 12.00 for 2025 and
    # 1200 x 1% x 4/365 = 0.13 for January 1 to 4.
    paid = pay_deposit_interest(
        app_conn, pmc.pmc_id, lease, date(2026, 1, 4), "deposit", "move-out", JAN_5_2026
    )
    assert paid == Decimal("12.13")
    assert return_deposit(app_conn, pmc.pmc_id, lease, "return", JAN_5_2026) == Decimal("1212.13")
    with refused("was returned already"):
        pay_deposit_interest(
            app_conn, pmc.pmc_id, lease, date(2026, 1, 5), "deposit", "after", JAN_5_2026
        )


# --- refused in words --------------------------------------------------------------------------


def test_what_is_refused(conn, app_conn, pmc, lease):
    unit = add_unit(conn, pmc.pmc_id, pmc.owners[1].property_id, "9Z")
    tenant = add_tenant(conn, pmc.pmc_id, pmc.owners[1].property_id, "Tenant 9")
    no_terms = open_lease(conn, pmc.pmc_id, unit, JAN_1_2025, None, "900.00", [tenant])
    with refused("no interest is due on lease"):
        pay_deposit_interest(
            app_conn, pmc.pmc_id, no_terms, DEC_31_2025, "rent_credit", "none", JAN_5_2026
        )
    with refused("not cash", psycopg.errors.InvalidParameterValue):
        pay_deposit_interest(app_conn, pmc.pmc_id, lease, DEC_31_2025, "cash", "k", JAN_5_2026)
    with refused("needs a request key", psycopg.errors.InvalidParameterValue):
        pay_deposit_interest(app_conn, pmc.pmc_id, lease, DEC_31_2025, "deposit", "", JAN_5_2026)
    with refused("no lease", psycopg.errors.InvalidParameterValue):
        pay_deposit_interest(
            app_conn, make_pmc(conn).pmc_id, lease, DEC_31_2025, "deposit", "k", JAN_5_2026
        )
    with (
        app_conn.transaction(),
        refused("at READ COMMITTED", psycopg.errors.InvalidTransactionState),
    ):
        app_conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
        pay_deposit_interest(app_conn, pmc.pmc_id, lease, DEC_31_2025, "deposit", "k", JAN_5_2026)
    for percent in (Decimal(-1), Decimal(26), Decimal("1.00001")):
        with refused("annual_percent_check"):
            deposit_interest_terms(
                conn, pmc.pmc_id, pmc.owners[0].property_id, DEC_31_2025, percent
            )
    with pytest.raises(psycopg.errors.UniqueViolation):
        deposit_interest_terms(conn, pmc.pmc_id, pmc.owners[0].property_id, JAN_1_2025, 2)


def test_a_lease_its_tenants_moved_from_pays_none(conn, app_conn, pmc, lease):
    # Their deposits moved with them to the new lease, which pays the interest from then on.
    unit = add_unit(conn, pmc.pmc_id, pmc.owners[0].property_id, "1B")
    moved = conn.execute(
        "SELECT trust_transfer_lease(%s, %s, %s, %s, NULL, 1500.00, NULL, %s)",
        (pmc.pmc_id, lease, unit, date(2025, 7, 1), date(2025, 6, 15)),
    ).fetchone()[0]
    with refused(f"tenants moved to lease {moved}, and their deposits with them"):
        pay_deposit_interest(
            app_conn, pmc.pmc_id, lease, JUN_30_2025, "deposit", "moved", JAN_5_2026
        )


# --- the report --------------------------------------------------------------------------------


def test_the_report_lists_each_lease_from_its_next_payment(conn, app_conn, ai_conn, pmc, lease):
    def report(connection, day):
        return connection.execute(
            "SELECT * FROM trust_report_deposit_interest(%s, %s)", (pmc.pmc_id, day)
        ).fetchall()

    assert report(app_conn, DEC_31_2025) == [
        (
            1,
            lease,
            "Property 1",
            "1A",
            "Tenant 1",
            Decimal("1200.00"),
            Decimal(1),
            JAN_1_2025,
            Decimal("12.00"),
        )
    ]
    pay_deposit_interest(
        app_conn, pmc.pmc_id, lease, DEC_31_2025, "rent_credit", "paid", JAN_5_2026
    )
    assert report(ai_conn, DEC_31_2025) == [
        (
            1,
            lease,
            "Property 1",
            "1A",
            "Tenant 1",
            Decimal("1200.00"),
            Decimal(1),
            date(2026, 1, 1),
            Decimal("0.00"),
        )
    ]
    with refused("as of a day", psycopg.errors.InvalidParameterValue):
        report(app_conn, None)
    with refused("no PMC", psycopg.errors.InvalidParameterValue):
        app_conn.execute(
            "SELECT * FROM trust_report_deposit_interest(gen_random_uuid(), %s)", (DEC_31_2025,)
        )


# --- kept as made ------------------------------------------------------------------------------


def test_nothing_is_rewritten_and_the_ai_reads_it_but_pays_none(
    conn, app_conn, ai_conn, pmc, lease
):
    fund(conn, pmc, pmc.owners[0].account, 100)
    pay_deposit_interest(app_conn, pmc.pmc_id, lease, DEC_31_2025, "deposit", "k", JAN_5_2026)
    for table in (
        "trust_deposit_interest_terms",
        "trust_deposit_interest_payments",
        "trust_deposit_interest_lines",
    ):
        for change in (f"UPDATE {table} SET pmc_id = pmc_id", f"DELETE FROM {table}"):
            with refused("append-only", psycopg.errors.RestrictViolation):
                conn.execute(change)
        assert ai_conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0] >= 1
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        app_conn.execute(
            "INSERT INTO trust_deposit_interest_payments (pmc_id, lease_id, from_on, through_on,"
            " amount, paid_as, request_key, paid_at)"
            " VALUES (%s, %s, '2026-01-01', '2026-01-02', 1, 'deposit', 'x', now())",
            (pmc.pmc_id, lease),
        )
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        pay_deposit_interest(
            ai_conn, pmc.pmc_id, lease, date(2026, 1, 4), "rent_credit", "ai", JAN_5_2026
        )
