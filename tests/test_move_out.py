"""Move-out and the security deposit's return (migration 20261012000035).

Once a lease has ended, trust_return_deposit keeps from its tenants' deposits what the lease
still owes, paid into the owner's account and matched to its charges oldest first (the cash
moving from the security-deposit trust bank account to the owner's), and returns the rest to
each tenant; once per lease. trust_report_deposit_return is the itemized statement; its golden
case pins the layout.
"""

from datetime import UTC, date, datetime
from decimal import Decimal

import psycopg
import pytest
from helpers import (
    acting_as,
    add_staff,
    add_tenant,
    add_unit,
    apply_payment,
    approve,
    balance,
    bank_sums,
    charge,
    give_roles,
    hold_payments,
    in_background,
    make_pmc,
    open_account,
    open_lease,
    return_deposit,
    transfer_batch,
    wait_until_blocked,
)

JAN_1, JUN_1, JUN_30 = date(2026, 1, 1), date(2026, 6, 1), date(2026, 6, 30)
JUL_1, JUL_5 = date(2026, 7, 1), date(2026, 7, 5)


def at(day, hour=12):
    return datetime(day.year, day.month, day.day, hour, tzinfo=UTC)


def refused(match, error=psycopg.errors.CheckViolation):
    return pytest.raises(error, match=match)


@pytest.fixture
def pmc(conn):
    return make_pmc(conn)


@pytest.fixture
def tenant(conn, pmc):
    return conn.execute(
        "SELECT tenant_id FROM trust_ledger_accounts WHERE ledger_account_id = %s",
        (pmc.tenant_deposit,),
    ).fetchone()[0]


@pytest.fixture
def lease(conn, pmc, tenant):
    """Tenant 1's lease of Unit 1 at Property 1, January to June, with a 1500.00 deposit."""
    unit = add_unit(conn, pmc.pmc_id, pmc.owners[0].property_id)
    lease_id = open_lease(conn, pmc.pmc_id, unit, JAN_1, JUN_30, "1000.00", [tenant])
    transfer_batch(conn, [(pmc.deposit_cash, pmc.tenant_deposit, "1500.00")], at(JAN_1))
    return lease_id


def returned(conn, lease_id):
    return conn.execute(
        "SELECT held, kept, returned FROM trust_deposit_returns WHERE lease_id = %s", (lease_id,)
    ).fetchone()


def lines(conn, lease_id):
    return conn.execute(
        "SELECT x.line_no, t.display_name, c.memo, x.amount FROM trust_deposit_return_lines AS x"
        " JOIN trust_tenants AS t ON t.id = x.tenant_id"
        " LEFT JOIN trust_charges AS c ON c.id = x.charge_id"
        " WHERE x.lease_id = %s ORDER BY x.line_no",
        (lease_id,),
    ).fetchall()


def open_amounts(conn, lease_id):
    return conn.execute(
        "SELECT c.memo, c.amount - coalesce((SELECT sum(p.amount) FROM trust_charge_payments AS p"
        " WHERE p.charge_id = c.id), 0) FROM trust_charges AS c"
        " WHERE c.lease_id = %s AND c.kind <> 'credit' ORDER BY c.due_on",
        (lease_id,),
    ).fetchall()


# --- what is kept and what goes back --------------------------------------------------------


def test_a_deposit_pays_what_the_lease_owes_and_the_rest_goes_back(conn, app_conn, pmc, lease):
    charge(conn, pmc.pmc_id, lease, JUN_1, "1000.00", "rent", "June rent")
    charge(conn, pmc.pmc_id, lease, JUL_1, "320.00", "fee", "Damage: broken window")
    charge(conn, pmc.pmc_id, lease, JUL_1, "100.00", "credit", "Carpet cleaning refund")
    owner = pmc.owners[0].account
    cash = (balance(conn, pmc.operating_cash), balance(conn, pmc.deposit_cash))

    back = return_deposit(app_conn, pmc.pmc_id, lease, "move-out-1", at(JUL_5))

    # Owed: 1000 + 320 - 100 = 1220 kept for the owner; 280 back to the tenant. The credit
    # comes off the rent first (migration 0093): 900.00 of June's rent, then the damage.
    assert back == Decimal("280.00")
    assert returned(conn, lease) == (Decimal("1500.00"), Decimal("1220.00"), Decimal("280.00"))
    assert lines(conn, lease) == [
        (1, "Tenant 1", "June rent", Decimal("900.00")),
        (2, "Tenant 1", "Damage: broken window", Decimal("320.00")),
        (3, "Tenant 1", None, Decimal("280.00")),
    ]
    assert open_amounts(conn, lease) == [
        ("June rent", Decimal("100.00")),
        ("Damage: broken window", Decimal("0.00")),
    ]
    assert balance(conn, pmc.tenant_deposit) == 0
    assert balance(conn, owner) == Decimal("1220.00")
    # Book cash is -balance: the kept 1220.00 moved from the security-deposit trust bank account
    # to the operating one, and the 280.00 returned left the bank. Both still tie out.
    assert cash[0] - balance(conn, pmc.operating_cash) == Decimal("1220.00")
    assert cash[1] - balance(conn, pmc.deposit_cash) == Decimal("-1500.00")
    assert set(bank_sums(conn, pmc.pmc_id).values()) == {0}


def test_when_the_lease_owes_more_than_is_held_all_is_kept(conn, app_conn, pmc, lease):
    charge(conn, pmc.pmc_id, lease, JUN_1, "1000.00", "rent", "June rent")
    charge(conn, pmc.pmc_id, lease, JUL_1, "900.00", "fee", "Damage")

    assert return_deposit(app_conn, pmc.pmc_id, lease, "move-out-1", at(JUL_5)) == 0
    assert returned(conn, lease) == (Decimal("1500.00"), Decimal("1500.00"), Decimal("0.00"))
    assert open_amounts(conn, lease) == [
        ("June rent", Decimal("0.00")),
        ("Damage", Decimal("400.00")),
    ]


def test_when_nothing_is_owed_all_goes_back(conn, app_conn, pmc, lease):
    paid = transfer_batch(
        conn, [(pmc.operating_cash, pmc.owners[0].account, "1000.00")], at(JUN_1)
    )[0]
    apply_payment(
        conn, pmc.pmc_id, charge(conn, pmc.pmc_id, lease, JUN_1, "1000.00"), paid, "1000.00"
    )

    assert return_deposit(app_conn, pmc.pmc_id, lease, "move-out-1", at(JUL_5)) == Decimal(
        "1500.00"
    )
    assert lines(conn, lease) == [(1, "Tenant 1", None, Decimal("1500.00"))]


def test_co_tenants_deposits_are_kept_from_in_name_order_and_each_gets_their_rest(
    conn, app_conn, pmc, tenant
):
    second = add_tenant(conn, pmc.pmc_id, pmc.owners[0].property_id, "Tenant 0")
    seconds = open_account(
        conn, pmc.pmc_id, pmc.deposit_bank_id, "tenant_deposit", tenant_id=second
    )
    unit = add_unit(conn, pmc.pmc_id, pmc.owners[0].property_id)
    lease = open_lease(conn, pmc.pmc_id, unit, JAN_1, JUN_30, "1000.00", [tenant, second])
    transfer_batch(
        conn,
        [(pmc.deposit_cash, pmc.tenant_deposit, "500.00"), (pmc.deposit_cash, seconds, "400.00")],
        at(JAN_1),
    )
    charge(conn, pmc.pmc_id, lease, JUL_1, "600.00", "fee", "Cleaning")

    assert return_deposit(app_conn, pmc.pmc_id, lease, "move-out-1", at(JUL_5)) == Decimal("300.00")
    assert lines(conn, lease) == [
        (1, "Tenant 0", "Cleaning", Decimal("400.00")),
        (2, "Tenant 1", "Cleaning", Decimal("200.00")),
        (3, "Tenant 1", None, Decimal("300.00")),
    ]


def test_a_bounced_payment_is_owed_again_and_credits_never_make_it_owe_less_than_nothing(
    conn, app_conn, pmc, lease, tenant
):
    june = charge(conn, pmc.pmc_id, lease, JUN_1, "1000.00", "rent", "June rent")
    paid = transfer_batch(
        conn, [(pmc.operating_cash, pmc.owners[0].account, "1000.00")], at(JUN_1)
    )[0]
    apply_payment(conn, pmc.pmc_id, june, paid, "1000.00")
    back = transfer_batch(
        conn, [(pmc.owners[0].account, pmc.operating_cash, "1000.00")], at(date(2026, 6, 3))
    )[0]
    conn.execute(
        "SELECT trust_reverse_payment(%s, %s, %s, %s, %s)",
        (pmc.pmc_id, june, paid, back, Decimal("1000.00")),
    )
    assert return_deposit(app_conn, pmc.pmc_id, lease, "move-out-1", at(JUL_5)) == Decimal("500.00")
    # The bounced rent is open again, and the deposit paid it.
    assert lines(conn, lease) == [
        (1, "Tenant 1", "June rent", Decimal("1000.00")),
        (2, "Tenant 1", None, Decimal("500.00")),
    ]

    unit = add_unit(conn, pmc.pmc_id, pmc.owners[0].property_id, "Unit 2")
    other = add_tenant(conn, pmc.pmc_id, pmc.owners[0].property_id, "Tenant 2")
    deposit = open_account(conn, pmc.pmc_id, pmc.deposit_bank_id, "tenant_deposit", tenant_id=other)
    credited = open_lease(conn, pmc.pmc_id, unit, JAN_1, JUN_30, "1000.00", [other])
    transfer_batch(conn, [(pmc.deposit_cash, deposit, "300.00")], at(JAN_1))
    charge(conn, pmc.pmc_id, credited, JUN_30, "50.00", "credit", "Goodwill")
    assert return_deposit(app_conn, pmc.pmc_id, credited, "move-out-2", at(JUL_5)) == Decimal(
        "300.00"
    )


# --- when, and once ---------------------------------------------------------------------------


def test_a_deposit_goes_back_only_once_the_lease_has_ended(conn, app_conn, pmc, lease, tenant):
    with refused("has not ended by 2026-06-30; end it first"):
        return_deposit(app_conn, pmc.pmc_id, lease, "move-out-1", at(JUN_30))
    month_to_month = open_lease(
        conn,
        pmc.pmc_id,
        add_unit(conn, pmc.pmc_id, pmc.owners[0].property_id, "Unit 2"),
        JUL_1,
        None,
        "1000.00",
        [add_tenant(conn, pmc.pmc_id, pmc.owners[0].property_id, "T2")],
    )
    with refused("has not ended by 2026-07-05"):
        return_deposit(app_conn, pmc.pmc_id, month_to_month, "move-out-2", at(JUL_5))

    return_deposit(app_conn, pmc.pmc_id, lease, "move-out-1", at(JUL_1))


def test_a_tenant_still_on_another_lease_keeps_their_deposit(conn, app_conn, pmc, lease, tenant):
    unit = conn.execute("SELECT unit_id FROM trust_leases WHERE id = %s", (lease,)).fetchone()[0]
    renewal = open_lease(conn, pmc.pmc_id, unit, JUL_1, None, "1050.00", [tenant])

    # Month to month, or ending later: either way the deposit stays with the renewal.
    for ends_on in (None, date(2026, 12, 31)):
        if ends_on:
            conn.execute("SELECT trust_end_lease(%s, %s, %s)", (pmc.pmc_id, renewal, ends_on))
        with refused("Tenant 1 is on lease .* still, and their deposit stays with it"):
            return_deposit(app_conn, pmc.pmc_id, lease, "move-out-1", at(JUL_5))
    # Once the renewal has ended too, the deposit goes back.
    return_deposit(app_conn, pmc.pmc_id, renewal, "move-out-2", at(date(2027, 1, 2)))


def test_the_same_request_again_returns_nothing_twice_and_another_is_refused(
    conn, app_conn, pmc, lease
):
    first = return_deposit(app_conn, pmc.pmc_id, lease, "move-out-1", at(JUL_5))
    transfers = conn.execute("SELECT count(*) FROM pgledger_transfers").fetchone()

    assert return_deposit(app_conn, pmc.pmc_id, lease, "move-out-1", at(JUL_5)) == first
    with refused("was returned already", psycopg.errors.UniqueViolation):
        return_deposit(app_conn, pmc.pmc_id, lease, "move-out-2", at(JUL_5))
    assert conn.execute("SELECT count(*) FROM pgledger_transfers").fetchone() == transfers


@pytest.mark.parametrize(
    ("key", "when", "why"),
    [("", at(JUL_5), "needs a request key"), ("move-out-1", None, "the time it was paid")],
)
def test_a_return_says_its_request_and_when(app_conn, pmc, lease, key, when, why):
    with refused(why, psycopg.errors.InvalidParameterValue):
        return_deposit(app_conn, pmc.pmc_id, lease, key, when)


def test_a_lease_with_no_deposit_held_has_nothing_to_return(conn, app_conn, pmc, tenant):
    unit = add_unit(conn, pmc.pmc_id, pmc.owners[0].property_id)
    lease = open_lease(conn, pmc.pmc_id, unit, JAN_1, JUN_30, "1000.00", [tenant])
    with refused("no security deposit is held for lease"):
        return_deposit(app_conn, pmc.pmc_id, lease, "move-out-1", at(JUL_5))


def test_another_pmcs_lease_and_statement_are_not_found(conn, app_conn, pmc, lease):
    other = make_pmc(conn).pmc_id
    with refused("no lease", psycopg.errors.InvalidParameterValue):
        return_deposit(app_conn, other, lease, "move-out-1", at(JUL_5))
    return_deposit(app_conn, pmc.pmc_id, lease, "move-out-1", at(JUL_5))
    statement = "SELECT * FROM trust_report_deposit_return(%s, %s)"
    assert app_conn.execute(statement, (pmc.pmc_id, lease)).fetchall()
    with refused("has not been returned", psycopg.errors.InvalidParameterValue):
        app_conn.execute(statement, (other, lease))


def test_a_return_is_made_at_read_committed(conn, pmc, lease):
    with conn.transaction():
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
        with refused("at READ COMMITTED", psycopg.errors.InvalidTransactionState):
            return_deposit(conn, pmc.pmc_id, lease, "move-out-1", at(JUL_5))


# --- where kept money goes ------------------------------------------------------------------


def test_kept_money_goes_to_the_one_owner_account_or_the_one_named(conn, app_conn, pmc, lease):
    charge(conn, pmc.pmc_id, lease, JUL_1, "100.00", "fee", "Cleaning")
    second = open_account(
        conn,
        pmc.pmc_id,
        pmc.operating_bank_id,
        "owner_property",
        pmc.owners[1].owner_id,
        pmc.owners[0].property_id,
    )
    for named in (None, pmc.owners[1].account):
        with refused("is not the one owner's account", psycopg.errors.InvalidParameterValue):
            return_deposit(app_conn, pmc.pmc_id, lease, "move-out-1", at(JUL_5), named)

    return_deposit(app_conn, pmc.pmc_id, lease, "move-out-1", at(JUL_5), second)
    assert balance(conn, second) == Decimal("100.00")


# --- who, and what refuses it ----------------------------------------------------------------


def test_returning_a_deposit_takes_a_staff_member_who_may_disburse(conn, app_conn, pmc, lease):
    clerk = add_staff(conn, pmc.pmc_id, "Clerk 1")
    with (
        acting_as(app_conn, clerk),
        refused("Clerk 1 may not pay out money", psycopg.errors.InsufficientPrivilege),
    ):
        return_deposit(app_conn, pmc.pmc_id, lease, "move-out-1", at(JUL_5))
    assert returned(conn, lease) is None

    give_roles(conn, pmc.pmc_id, clerk, ["disburse"])
    with acting_as(app_conn, clerk):
        return_deposit(app_conn, pmc.pmc_id, lease, "move-out-1", at(JUL_5))
    assert conn.execute(
        "SELECT returned_by FROM trust_deposit_returns WHERE lease_id = %s", (lease,)
    ).fetchone() == (clerk,)


def test_a_payment_hold_on_the_lease_refuses_keeping_for_its_charges(conn, app_conn, pmc, lease):
    charge(conn, pmc.pmc_id, lease, JUN_1, "1000.00", "rent", "June rent")
    hold_payments(conn, pmc.pmc_id, lease, JUN_1)
    with refused("is on a payment hold"):
        return_deposit(app_conn, pmc.pmc_id, lease, "move-out-1", at(JUL_5))
    assert returned(conn, lease) is None


def test_a_return_dated_in_a_closed_period_is_refused(conn, app_conn, pmc, lease):
    approve(conn, pmc.pmc_id, pmc.deposit_bank_id, at(JAN_1, 0), at(date(2026, 8, 1), 0))
    with refused("closed"):
        return_deposit(app_conn, pmc.pmc_id, lease, "move-out-1", at(JUL_5))


def test_returns_are_append_only_and_written_only_by_the_function(conn, app_conn, pmc, lease):
    return_deposit(app_conn, pmc.pmc_id, lease, "move-out-1", at(JUL_5))
    for table in ("trust_deposit_returns", "trust_deposit_return_lines"):
        for query in (f"UPDATE {table} SET pmc_id = pmc_id", f"DELETE FROM {table}"):
            with refused("append-only", psycopg.errors.RestrictViolation):
                conn.execute(query)
    with refused("permission denied", psycopg.errors.InsufficientPrivilege):
        app_conn.execute(
            "INSERT INTO trust_deposit_returns (lease_id, pmc_id, request_key, returned_at, held,"
            " kept, returned) VALUES (%s, %s, 'k', now(), 1, 0, 1)",
            (lease, pmc.pmc_id),
        )


@pytest.mark.timing
def test_a_deposit_paid_in_meanwhile_waits_for_the_return(conn, database_url, pmc, lease):
    with psycopg.connect(database_url) as first:
        return_deposit(first, pmc.pmc_id, lease, "move-out-1", at(JUL_5))
        thread, pid, outcome = in_background(
            database_url,
            lambda worker: transfer_batch(
                worker, [(pmc.deposit_cash, pmc.tenant_deposit, "50.00")], at(JUL_5, 13)
            ),
        )
        wait_until_blocked(conn, pid)
        first.commit()
    thread.join()

    assert "error" not in outcome, outcome
    # The return took what was held then; the late payment stays held.
    assert balance(conn, pmc.tenant_deposit) == Decimal("50.00")


@pytest.mark.timing
def test_the_return_waits_for_the_leases_rent_work(conn, database_url, pmc, lease):
    with psycopg.connect(database_url) as rent_work:
        rent_work.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended('rent of lease ' || %s, 0))",
            (str(lease),),
        )
        thread, pid, outcome = in_background(
            database_url,
            lambda worker: return_deposit(worker, pmc.pmc_id, lease, "move-out-1", at(JUL_5)),
        )
        wait_until_blocked(conn, pid)
        rent_work.commit()
    thread.join()

    assert outcome == {"result": Decimal("1500.00")}


@pytest.mark.timing
def test_a_lease_given_more_days_meanwhile_is_not_returned(conn, database_url, pmc, lease):
    with psycopg.connect(database_url) as extending:
        extending.execute(
            "SELECT trust_end_lease(%s, %s, %s)", (pmc.pmc_id, lease, date(2026, 12, 31))
        )
        thread, pid, outcome = in_background(
            database_url,
            lambda worker: return_deposit(worker, pmc.pmc_id, lease, "move-out-1", at(JUL_5)),
        )
        wait_until_blocked(conn, pid)
        extending.commit()
    thread.join()

    assert "has not ended by 2026-07-05" in str(outcome["error"])
