"""A lease's tenants moving to another unit (migration 20261018000091; the founder's review, q010
and q042).

trust_transfer_lease ends the lease the day before the move and opens one at the other unit for
the tenants staying on it, moving them to its property; their deposits stay held for the new
lease, and what the old lease still owes stays owed to its owner. trust_return_deposit keeps what
every lease they moved from still owes first, each lease's for its own owner. The API's side is
in tests/api/test_lease_transfers_api.py. Every name here is synthetic."""

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
    charge,
    hold_payments,
    make_pmc,
    open_account,
    open_lease,
    release_hold,
    return_deposit,
    transfer_batch,
)

JAN_1, MAR_1, MAR_25, MAR_31 = (
    date(2026, 1, 1),
    date(2026, 3, 1),
    date(2026, 3, 25),
    date(2026, 3, 31),
)
APR_1, JUN_1, JUN_30, JUL_5 = (
    date(2026, 4, 1),
    date(2026, 6, 1),
    date(2026, 6, 30),
    date(2026, 7, 5),
)


def at(day, hour=12):
    return datetime(day.year, day.month, day.day, hour, tzinfo=UTC)


def refused(match, error=psycopg.errors.CheckViolation):
    return pytest.raises(error, match=match)


def move(
    conn,
    pmc_id,
    lease,
    unit,
    moved_on=APR_1,
    ends_on=None,
    rent="1100.00",
    owner=None,
    agreed=MAR_25,
):
    return conn.execute(
        "SELECT trust_transfer_lease(%s, %s, %s, %s, %s, %s, %s, %s)",
        (pmc_id, lease, unit, moved_on, ends_on, rent, owner, agreed),
    ).fetchone()[0]


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
    """Tenant 1's lease of Unit 1 at Property 1 (Owner 1's) from January, month to month, with a
    2500.00 deposit and March's rent of 1000.00 unpaid."""
    unit = add_unit(conn, pmc.pmc_id, pmc.owners[0].property_id)
    lease_id = open_lease(conn, pmc.pmc_id, unit, JAN_1, None, "1000.00", [tenant])
    transfer_batch(conn, [(pmc.deposit_cash, pmc.tenant_deposit, "2500.00")], at(JAN_1))
    charge(conn, pmc.pmc_id, lease_id, MAR_1, "1000.00", memo="March rent")
    return lease_id


@pytest.fixture
def there(conn, pmc):
    """Unit 7 at Property 2, Owner 2's."""
    return add_unit(conn, pmc.pmc_id, pmc.owners[1].property_id, "Unit 7")


def test_a_move_ends_the_lease_and_opens_one_at_the_other_unit_for_its_tenants(
    conn, pmc, tenant, lease, there
):
    new = move(conn, pmc.pmc_id, lease, there)

    old_end = conn.execute("SELECT ends_on FROM trust_leases WHERE id = %s", (lease,)).fetchone()
    opened = conn.execute(
        "SELECT unit_id, starts_on, ends_on, monthly_rent FROM trust_leases WHERE id = %s", (new,)
    ).fetchone()
    on_it = conn.execute(
        "SELECT tenant_id FROM trust_lease_tenants WHERE lease_id = %s", (new,)
    ).fetchall()
    home = conn.execute("SELECT property_id FROM trust_tenants WHERE id = %s", (tenant,)).fetchone()
    moved = conn.execute(
        "SELECT pmc_id, to_lease_id, moved_on, owner_account, deposit_agreed_on, recorded_by"
        " FROM trust_lease_transfers WHERE from_lease_id = %s",
        (lease,),
    ).fetchone()
    chain = conn.execute("SELECT * FROM trust_lease_moved_from(%s)", (new,)).fetchall()

    assert old_end == (MAR_31,)
    assert opened == (there, APR_1, None, Decimal("1100.00"))
    assert on_it == [(tenant,)]
    assert home == (pmc.owners[1].property_id,)  # one record, now the other property's tenant
    assert moved == (pmc.pmc_id, new, APR_1, pmc.owners[0].account, MAR_25, None)
    assert chain == [(lease, pmc.owners[0].account)]
    # Nothing moved in the books: the deposit is the tenant's, the old lease still owes March.
    assert balance(conn, pmc.tenant_deposit) == Decimal("2500.00")
    assert conn.execute("SELECT trust_lease_owed(%s)", (lease,)).fetchone() == (Decimal("1000.00"),)


def test_the_same_move_again_is_the_move_made_and_another_is_refused(conn, pmc, lease, there):
    new = move(conn, pmc.pmc_id, lease, there)
    elsewhere = add_unit(conn, pmc.pmc_id, pmc.owners[1].property_id, "Unit 8")

    again = move(conn, pmc.pmc_id, lease, there)

    assert again == new
    with refused("moved already", psycopg.errors.UniqueViolation):
        move(conn, pmc.pmc_id, lease, elsewhere)
    with refused("moved already", psycopg.errors.UniqueViolation):
        move(conn, pmc.pmc_id, lease, there, moved_on=date(2026, 4, 2))


def test_a_move_within_the_property_keeps_the_tenants_its_own(conn, pmc, tenant, lease):
    next_door = add_unit(conn, pmc.pmc_id, pmc.owners[0].property_id, "Unit 2")

    new = move(conn, pmc.pmc_id, lease, next_door)

    assert conn.execute(
        "SELECT property_id FROM trust_tenants WHERE id = %s", (tenant,)
    ).fetchone() == (pmc.owners[0].property_id,)
    assert conn.execute("SELECT unit_id FROM trust_leases WHERE id = %s", (new,)).fetchone() == (
        next_door,
    )


def test_a_lease_ending_the_day_before_moves_as_it_is(conn, pmc, lease, there):
    conn.execute("SELECT trust_end_lease(%s, %s, %s)", (pmc.pmc_id, lease, MAR_31))

    move(conn, pmc.pmc_id, lease, there)

    assert conn.execute("SELECT ends_on FROM trust_leases WHERE id = %s", (lease,)).fetchone() == (
        MAR_31,
    )


def test_a_lease_ending_later_ends_the_day_before_the_move(conn, pmc, lease, there):
    conn.execute("SELECT trust_end_lease(%s, %s, %s)", (pmc.pmc_id, lease, JUN_30))

    move(conn, pmc.pmc_id, lease, there)

    assert conn.execute("SELECT ends_on FROM trust_leases WHERE id = %s", (lease,)).fetchone() == (
        MAR_31,
    )


def test_what_each_lease_owes_is_kept_from_the_deposit_for_its_own_owner(
    conn, pmc, tenant, lease, there
):
    """March's 1000.00 stays owed to Owner 1, June's 1100.00 is Owner 2's: the deposit (2500.00)
    pays Owner 1 first, then Owner 2, and 400.00 goes back."""
    new = move(conn, pmc.pmc_id, lease, there, ends_on=JUN_30)
    charge(conn, pmc.pmc_id, new, JUN_1, "1100.00", memo="June rent")

    back = return_deposit(conn, pmc.pmc_id, new, "back", at(JUL_5))

    kept = conn.execute(
        "SELECT c.lease_id, c.memo, x.amount FROM trust_deposit_return_lines AS x"
        " JOIN trust_charges AS c ON c.id = x.charge_id WHERE x.lease_id = %s ORDER BY x.line_no",
        (new,),
    ).fetchall()
    paid = conn.execute(
        "SELECT a.ledger_account_id, sum(p.amount) FROM trust_charge_payments AS p"
        " JOIN pgledger_transfers AS t ON t.id = p.transfer_id"
        " JOIN trust_ledger_accounts AS a ON a.ledger_account_id = t.to_account_id"
        " WHERE p.charge_id IN (SELECT id FROM trust_charges WHERE lease_id IN (%s, %s))"
        " GROUP BY a.ledger_account_id ORDER BY 1",
        (lease, new),
    ).fetchall()

    assert back == Decimal("400.00")
    assert kept == [
        (lease, "March rent", Decimal("1000.00")),
        (new, "June rent", Decimal("1100.00")),
    ]
    assert sorted(paid) == sorted(
        [(pmc.owners[0].account, Decimal("1000.00")), (pmc.owners[1].account, Decimal("1100.00"))]
    )
    assert (balance(conn, pmc.owners[0].account), balance(conn, pmc.owners[1].account)) == (
        Decimal("1000.00"),
        Decimal("1100.00"),
    )
    assert balance(conn, pmc.tenant_deposit) == 0
    assert set(bank_sums(conn, pmc.pmc_id).values()) == {0}
    assert conn.execute("SELECT trust_lease_owed(%s)", (lease,)).fetchone() == (0,)
    assert conn.execute("SELECT trust_lease_owed(%s)", (new,)).fetchone() == (0,)


def test_a_deposit_short_of_both_pays_the_older_lease_first(conn, pmc, lease, there):
    new = move(conn, pmc.pmc_id, lease, there, ends_on=JUN_30)
    charge(conn, pmc.pmc_id, new, JUN_1, "1800.00", memo="June rent")

    back = return_deposit(conn, pmc.pmc_id, new, "back", at(JUL_5))

    assert back == 0
    assert (balance(conn, pmc.owners[0].account), balance(conn, pmc.owners[1].account)) == (
        Decimal("1000.00"),
        Decimal("1500.00"),
    )


def test_two_tenants_deposits_pay_the_older_lease_then_the_new_one(conn, pmc, tenant, there):
    """Tenant A's 600.00 and Tenant B's 900.00: the old lease's 1000.00 takes A's and 400.00 of
    B's, the new lease's 500.00 the next 500.00, and B gets 0.00 back."""
    a = add_tenant(conn, pmc.pmc_id, pmc.owners[0].property_id, "Tenant A")
    b = add_tenant(conn, pmc.pmc_id, pmc.owners[0].property_id, "Tenant B")
    for who, amount in ((a, "600.00"), (b, "900.00")):
        account = open_account(
            conn, pmc.pmc_id, pmc.deposit_bank_id, "tenant_deposit", tenant_id=who
        )
        transfer_batch(conn, [(pmc.deposit_cash, account, amount)], at(JAN_1))
    unit = add_unit(conn, pmc.pmc_id, pmc.owners[0].property_id, "Unit 6")
    old = open_lease(conn, pmc.pmc_id, unit, JAN_1, None, "1000.00", [a, b])
    charge(conn, pmc.pmc_id, old, MAR_1, "1000.00", memo="March rent")
    new = move(conn, pmc.pmc_id, old, there, ends_on=JUN_30)
    charge(conn, pmc.pmc_id, new, JUN_1, "500.00", memo="June rent")

    back = return_deposit(conn, pmc.pmc_id, new, "back", at(JUL_5))

    assert back == 0
    assert (balance(conn, pmc.owners[0].account), balance(conn, pmc.owners[1].account)) == (
        Decimal("1000.00"),
        Decimal("500.00"),
    )
    assert conn.execute(
        "SELECT t.display_name, c.memo, x.amount FROM trust_deposit_return_lines AS x"
        " JOIN trust_tenants AS t ON t.id = x.tenant_id"
        " JOIN trust_charges AS c ON c.id = x.charge_id WHERE x.lease_id = %s ORDER BY x.line_no",
        (new,),
    ).fetchall() == [
        ("Tenant A", "March rent", Decimal("600.00")),
        ("Tenant B", "March rent", Decimal("400.00")),
        ("Tenant B", "June rent", Decimal("500.00")),
    ]
    del tenant


def test_two_moves_keep_for_the_oldest_lease_first(conn, pmc, tenant, lease, there):
    middle = move(conn, pmc.pmc_id, lease, there)
    charge(conn, pmc.pmc_id, middle, date(2026, 5, 1), "300.00", memo="May fee", kind="fee")
    last_unit = add_unit(conn, pmc.pmc_id, pmc.owners[0].property_id, "Unit 3")
    last = move(conn, pmc.pmc_id, middle, last_unit, moved_on=JUN_1, ends_on=JUN_30)

    chain = conn.execute("SELECT * FROM trust_lease_moved_from(%s)", (last,)).fetchall()
    back = return_deposit(conn, pmc.pmc_id, last, "back", at(JUL_5))

    assert chain == [(lease, pmc.owners[0].account), (middle, pmc.owners[1].account)]
    assert back == Decimal("1200.00")
    assert (balance(conn, pmc.owners[0].account), balance(conn, pmc.owners[1].account)) == (
        Decimal("1000.00"),
        Decimal("300.00"),
    )
    assert conn.execute(
        "SELECT property_id FROM trust_tenants WHERE id = %s", (tenant,)
    ).fetchone() == (pmc.owners[0].property_id,)


def test_a_lease_moved_from_has_no_return_of_its_own(conn, pmc, lease, there):
    new = move(conn, pmc.pmc_id, lease, there)

    with refused("moved to lease"):
        return_deposit(conn, pmc.pmc_id, lease, "back", at(JUL_5))
    assert new is not None


def test_the_statement_names_the_lease_moved_from_and_its_home(conn, pmc, lease, there):
    new = move(conn, pmc.pmc_id, lease, there, ends_on=JUN_30)
    return_deposit(conn, pmc.pmc_id, new, "back", at(JUL_5))

    report = conn.execute(
        "SELECT item, detail, amount FROM trust_report_deposit_return(%s, %s)", (pmc.pmc_id, new)
    ).fetchall()

    assert ("moved from", "Property 1 / Unit 1, 2026-01-01 to 2026-03-31", None) in report
    assert (
        "kept for",
        "2026-03-01 rent: March rent (Property 1 / Unit 1)",
        Decimal("1000.00"),
    ) in report


# --- what a move refuses ---------------------------------------------------------------------


def test_a_move_goes_to_another_unit_of_the_company(conn, pmc, lease):
    own_unit = conn.execute("SELECT unit_id FROM trust_leases WHERE id = %s", (lease,)).fetchone()[
        0
    ]
    other = make_pmc(conn)
    theirs = add_unit(conn, other.pmc_id, other.owners[0].property_id)

    with refused("at its own unit it is renewed", psycopg.errors.InvalidParameterValue):
        move(conn, pmc.pmc_id, lease, own_unit)
    with refused("no unit", psycopg.errors.InvalidParameterValue):
        move(conn, pmc.pmc_id, lease, theirs)
    with refused("no lease", psycopg.errors.InvalidParameterValue):
        move(conn, other.pmc_id, lease, theirs)


def test_a_move_needs_its_day_and_the_lease_in_effect_the_day_before(conn, pmc, lease, there):
    with refused("needs its day", psycopg.errors.InvalidParameterValue):
        move(conn, pmc.pmc_id, lease, there, moved_on=None)
    with refused("not in effect"):
        move(conn, pmc.pmc_id, lease, there, moved_on=JAN_1)
    conn.execute("SELECT trust_end_lease(%s, %s, %s)", (pmc.pmc_id, lease, date(2026, 3, 30)))
    with refused("not in effect"):
        move(conn, pmc.pmc_id, lease, there)


def test_a_move_runs_at_read_committed(conn, pmc, lease, there):
    with (
        refused("move a lease at READ COMMITTED", psycopg.errors.InvalidTransactionState),
        conn.transaction(),
    ):
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
        move(conn, pmc.pmc_id, lease, there)


def test_a_lease_on_a_payment_hold_stays_until_the_hold_is_released(conn, pmc, lease, there):
    hold = hold_payments(conn, pmc.pmc_id, lease, MAR_1)

    with refused("payment hold"), conn.transaction():
        move(conn, pmc.pmc_id, lease, there)
    release_hold(conn, pmc.pmc_id, hold, APR_1)  # in force through the move's first day
    with refused("payment hold"), conn.transaction():
        move(conn, pmc.pmc_id, lease, there)

    later = conn.execute(
        "SELECT trust_transfer_lease(%s, %s, %s, %s, NULL, 1100.00, NULL, %s)",
        (pmc.pmc_id, lease, there, date(2026, 4, 2), MAR_25),
    ).fetchone()
    assert later is not None


def test_the_tenants_agree_in_writing_to_their_deposit_staying_held(conn, pmc, lease, there):
    with refused("agreed in writing"), conn.transaction():
        move(conn, pmc.pmc_id, lease, there, agreed=None)
    with refused("a day that has come"), conn.transaction():
        move(conn, pmc.pmc_id, lease, there, agreed=date(2999, 1, 1))


def test_tenants_holding_no_deposit_agree_to_nothing(conn, pmc, there):
    unit = add_unit(conn, pmc.pmc_id, pmc.owners[0].property_id, "Unit 4")
    tenant = add_tenant(conn, pmc.pmc_id, pmc.owners[0].property_id, "Tenant 4")
    lease = open_lease(conn, pmc.pmc_id, unit, JAN_1, None, "900.00", [tenant])

    assert move(conn, pmc.pmc_id, lease, there, agreed=None) is not None


def test_a_lease_whose_deposit_was_returned_moved_out(conn, pmc, lease, there):
    conn.execute("SELECT trust_end_lease(%s, %s, %s)", (pmc.pmc_id, lease, MAR_31))
    return_deposit(conn, pmc.pmc_id, lease, "back", at(JUL_5))

    with refused("was returned"):
        move(conn, pmc.pmc_id, lease, there)


def test_a_tenant_with_another_lease_running_moves_property_with_it_ended(
    conn, pmc, tenant, lease, there
):
    garage = add_unit(conn, pmc.pmc_id, pmc.owners[0].property_id, "Garage 1")
    open_lease(conn, pmc.pmc_id, garage, JAN_1, None, "50.00", [tenant])
    next_door = add_unit(conn, pmc.pmc_id, pmc.owners[0].property_id, "Unit 2")

    with refused("move to another property with every lease"), conn.transaction():
        move(conn, pmc.pmc_id, lease, there)
    # Within the property, nothing about their home changes.
    assert move(conn, pmc.pmc_id, lease, next_door) is not None


def test_a_tenant_who_left_with_a_deposit_under_their_name_keeps_the_lease_here(
    conn, pmc, tenant, lease, there
):
    other = add_tenant(conn, pmc.pmc_id, pmc.owners[0].property_id, "Tenant 2")
    unit = add_unit(conn, pmc.pmc_id, pmc.owners[0].property_id, "Unit 5")
    shared = open_lease(conn, pmc.pmc_id, unit, JAN_1, None, "1000.00", [tenant, other])
    theirs = open_account(conn, pmc.pmc_id, pmc.deposit_bank_id, "tenant_deposit", tenant_id=other)
    transfer_batch(conn, [(pmc.deposit_cash, theirs, "300.00")], at(JAN_1))
    conn.execute(
        "SELECT trust_remove_lease_tenant(%s, %s, %s, %s, NULL)", (pmc.pmc_id, shared, other, MAR_1)
    )

    with refused("left lease .* with a deposit"):
        move(conn, pmc.pmc_id, shared, there)
    del lease


def test_the_old_lease_names_its_owners_account(conn, pmc, lease, there):
    second = open_account(
        conn,
        pmc.pmc_id,
        pmc.operating_bank_id,
        "owner_property",
        pmc.owners[1].owner_id,
        pmc.owners[0].property_id,
    )

    with refused("not the one owner", psycopg.errors.InvalidParameterValue), conn.transaction():
        move(conn, pmc.pmc_id, lease, there)
    with refused("not the one owner", psycopg.errors.InvalidParameterValue), conn.transaction():
        move(conn, pmc.pmc_id, lease, there, owner=pmc.owners[1].account)
    new = move(conn, pmc.pmc_id, lease, there, owner=second)
    assert conn.execute(
        "SELECT owner_account FROM trust_lease_transfers WHERE to_lease_id = %s", (new,)
    ).fetchone() == (second,)


def test_the_unit_moved_to_has_no_lease_then(conn, pmc, lease, there):
    someone = add_tenant(conn, pmc.pmc_id, pmc.owners[1].property_id, "Tenant 9")
    open_lease(conn, pmc.pmc_id, there, JAN_1, JUN_30, "1100.00", [someone])

    with pytest.raises(psycopg.errors.ExclusionViolation):
        move(conn, pmc.pmc_id, lease, there)


# --- the record --------------------------------------------------------------------------------


def test_a_move_is_append_only_audited_and_names_who(conn, pmc, lease, there):
    staff = add_staff(conn, pmc.pmc_id, "Manager 1")
    with acting_as(conn, staff):
        new = move(conn, pmc.pmc_id, lease, there)

    for change in (
        "UPDATE trust_lease_transfers SET moved_on = moved_on + 1",
        "DELETE FROM trust_lease_transfers",
    ):
        with refused("append-only", psycopg.errors.RestrictViolation), conn.transaction():
            conn.execute(change)
    assert conn.execute(
        "SELECT recorded_by FROM trust_lease_transfers WHERE to_lease_id = %s", (new,)
    ).fetchone() == (staff,)
    assert conn.execute(
        "SELECT count(*) FROM trust_audit_log WHERE table_name = 'trust_lease_transfers'"
        " AND staff_id = %s",
        (staff,),
    ).fetchone() == (1,)
    # The tenant's move to Property 2 is in the log too, naming who.
    assert conn.execute(
        "SELECT count(*) FROM trust_audit_log WHERE table_name = 'trust_tenants'"
        " AND action = 'update' AND staff_id = %s",
        (staff,),
    ).fetchone() == (1,)


def test_a_move_names_two_leases_of_its_company(conn, pmc, lease):
    with refused("from_lease_id <> to_lease_id|check", psycopg.errors.CheckViolation):
        conn.execute(
            "INSERT INTO trust_lease_transfers (from_lease_id, pmc_id, to_lease_id, moved_on,"
            " owner_account) VALUES (%s, %s, %s, %s, %s)",
            (lease, pmc.pmc_id, lease, APR_1, pmc.owners[0].account),
        )


def test_a_deposit_short_of_two_leases_moved_from_pays_the_oldest_first(conn, pmc, lease, there):
    middle = move(conn, pmc.pmc_id, lease, there)
    charge(conn, pmc.pmc_id, middle, date(2026, 5, 1), "2000.00", memo="May fee", kind="fee")
    last_unit = add_unit(conn, pmc.pmc_id, pmc.owners[0].property_id, "Unit 3")
    last = move(conn, pmc.pmc_id, middle, last_unit, moved_on=JUN_1, ends_on=JUN_30)

    assert return_deposit(conn, pmc.pmc_id, last, "back", at(JUL_5)) == 0
    # Lease 1 (Owner 1) owed 1000.00 and is paid first; the middle lease (Owner 2) gets the rest.
    assert (balance(conn, pmc.owners[0].account), balance(conn, pmc.owners[1].account)) == (
        Decimal("1000.00"),
        Decimal("1500.00"),
    )
