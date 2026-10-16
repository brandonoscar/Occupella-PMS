"""Archiving a property, unit, tenant or owner (migrations 20261016000063 and 0064, ADR 0005): a
row, the latest one holding; refused while anything is open on it (a lease in effect or to come,
money held, a bill not paid), and kept for good."""

from datetime import UTC, datetime, timedelta

import psycopg
import pytest
from helpers import (
    acting_as,
    add_unit,
    approve_bill,
    decline_bill,
    end_lease,
    enter_bill,
    make_pmc,
    open_lease,
    pay_bill,
    set_aside_bill,
    staff_named,
    transfer_batch,
)

TODAY = datetime.now(UTC).date()
DAY = timedelta(days=1)


@pytest.fixture
def pmc(conn):
    return make_pmc(conn)


def tenant_of(conn, pmc):
    return conn.execute(
        "SELECT tenant_id FROM trust_ledger_accounts WHERE ledger_account_id = %s",
        (pmc.tenant_deposit,),
    ).fetchone()[0]


def archive(
    conn, pmc, property_id=None, unit_id=None, archived=True, tenant_id=None, owner_id=None
):
    conn.execute(
        "INSERT INTO trust_archivals (pmc_id, property_id, unit_id, tenant_id, owner_id, archived)"
        " VALUES (%s, %s, %s, %s, %s, %s)",
        (pmc.pmc_id, property_id, unit_id, tenant_id, owner_id, archived),
    )


def archived(conn, property_id=None, unit_id=None, tenant_id=None, owner_id=None):
    return conn.execute(
        "SELECT trust_is_archived(%s, %s, %s, %s)", (property_id, unit_id, tenant_id, owner_id)
    ).fetchone()[0]


def refused(match):
    return pytest.raises(psycopg.errors.CheckViolation, match=match)


def test_a_unit_with_no_lease_is_archived_and_brought_back(conn, pmc):
    unit = add_unit(conn, pmc.pmc_id, pmc.owners[0].property_id, "Unit 9")
    assert archived(conn, unit_id=unit) is False

    archive(conn, pmc, unit_id=unit)
    assert archived(conn, unit_id=unit) is True
    with refused("archived already"):
        archive(conn, pmc, unit_id=unit)

    archive(conn, pmc, unit_id=unit, archived=False)
    assert archived(conn, unit_id=unit) is False
    with refused("not archived already"):
        archive(conn, pmc, unit_id=unit, archived=False)


def test_a_unit_with_a_lease_in_effect_or_to_come_stays(conn, pmc):
    prop = pmc.owners[0].property_id
    now, later, ended = (add_unit(conn, pmc.pmc_id, prop, f"Unit {n}") for n in (1, 2, 3))
    tenant = tenant_of(conn, pmc)
    open_lease(conn, pmc.pmc_id, now, TODAY - 30 * DAY, None, "1000.00", [tenant])
    open_lease(conn, pmc.pmc_id, later, TODAY + 10 * DAY, None, "1000.00", [tenant])
    old = open_lease(conn, pmc.pmc_id, ended, TODAY - 90 * DAY, None, "1000.00", [tenant])
    end_lease(conn, pmc.pmc_id, old, TODAY - DAY)

    for unit, name in ((now, "Unit 1"), (later, "Unit 2")):
        with refused(f"{name} has a lease in effect or to come"):
            archive(conn, pmc, unit_id=unit)
    # A lease that ended yesterday leaves nothing open.
    archive(conn, pmc, unit_id=ended)
    assert archived(conn, unit_id=ended) is True


def test_a_property_stays_while_a_lease_money_or_a_bill_is_open_on_it(conn, pmc):
    owner = pmc.owners[0]
    unit = add_unit(conn, pmc.pmc_id, owner.property_id, "Unit 1")
    tenant = tenant_of(conn, pmc)
    lease = open_lease(conn, pmc.pmc_id, unit, TODAY - 30 * DAY, None, "1000.00", [tenant])
    name = conn.execute(
        "SELECT display_name FROM trust_properties WHERE id = %s", (owner.property_id,)
    ).fetchone()[0]

    with refused(f"{name} has a lease in effect or to come"):
        archive(conn, pmc, property_id=owner.property_id)
    end_lease(conn, pmc.pmc_id, lease, TODAY - DAY)

    # Money held for its owner keeps it, and so does a bill not paid.
    transfer_batch(conn, [(pmc.operating_cash, owner.account, "100.00")])
    with refused("holds its owners' money"):
        archive(conn, pmc, property_id=owner.property_id)
    bill = enter_bill(
        conn, pmc.pmc_id, pmc.vendor_id, owner.account, "INV-1", TODAY, TODAY, "100.00"
    )
    declined = enter_bill(
        conn, pmc.pmc_id, pmc.vendor_id, owner.account, "INV-2", TODAY, TODAY, "40.00"
    )
    decline_bill(conn, pmc.pmc_id, declined, TODAY, "Owner 1")
    approve_bill(conn, pmc.pmc_id, bill, TODAY, "Owner 1")
    set_aside_bill(conn, pmc.pmc_id, bill, datetime.now(UTC))
    with refused("has a bill not paid"):
        archive(conn, pmc, property_id=owner.property_id)
    pay_bill(conn, pmc.pmc_id, bill, datetime.now(UTC))

    # The declined bill holds nothing back; with the owner's money paid out, it goes.
    archive(conn, pmc, property_id=owner.property_id)
    assert archived(conn, property_id=owner.property_id) is True


def test_a_tenant_on_no_lease_and_holding_nothing_is_archived(conn, pmc):
    tenant = tenant_of(conn, pmc)
    unit = add_unit(conn, pmc.pmc_id, pmc.owners[0].property_id, "Unit 1")
    lease = open_lease(conn, pmc.pmc_id, unit, TODAY - 30 * DAY, None, "1000.00", [tenant])
    with refused("Tenant 1 is on a lease in effect or to come"):
        archive(conn, pmc, tenant_id=tenant)
    end_lease(conn, pmc.pmc_id, lease, TODAY - DAY)

    # Prepaid rent or a deposit held for them keeps them.
    transfer_batch(conn, [(pmc.deposit_cash, pmc.tenant_deposit, "500.00")])
    with refused("Tenant 1 has prepaid rent or a deposit held"):
        archive(conn, pmc, tenant_id=tenant)
    transfer_batch(conn, [(pmc.tenant_deposit, pmc.deposit_cash, "500.00")])

    archive(conn, pmc, tenant_id=tenant)
    assert archived(conn, tenant_id=tenant) is True
    # Archiving a tenant leaves the rest as it was, and they come back.
    assert archived(conn, property_id=pmc.owners[0].property_id) is False
    archive(conn, pmc, tenant_id=tenant, archived=False)
    assert archived(conn, tenant_id=tenant) is False


def test_an_owner_stays_while_their_accounts_hold_money_or_a_bill(conn, pmc):
    owner = pmc.owners[1]
    transfer_batch(conn, [(pmc.operating_cash, owner.account, "100.00")])
    with refused("Owner 2 holds their money; draw it before archiving"):
        archive(conn, pmc, owner_id=owner.owner_id)
    transfer_batch(conn, [(owner.account, pmc.operating_cash, "100.00")])
    bill = enter_bill(
        conn, pmc.pmc_id, pmc.vendor_id, owner.account, "INV-9", TODAY, TODAY, "50.00"
    )
    with refused("Owner 2 has a bill not paid"):
        archive(conn, pmc, owner_id=owner.owner_id)
    decline_bill(conn, pmc.pmc_id, bill, TODAY, "Owner 2")

    archive(conn, pmc, owner_id=owner.owner_id)
    assert archived(conn, owner_id=owner.owner_id) is True
    with refused("archived already"):
        archive(conn, pmc, owner_id=owner.owner_id)
    # Another owner's money is theirs: Owner 1 is archived with Owner 2's account untouched.
    transfer_batch(conn, [(pmc.operating_cash, owner.account, "100.00")])
    archive(conn, pmc, owner_id=pmc.owners[0].owner_id)


def test_an_archival_is_of_one_record_of_the_pmc_and_names_who_made_it(conn, pmc):
    unit = add_unit(conn, pmc.pmc_id, pmc.owners[0].property_id, "Unit 1")
    with pytest.raises(psycopg.errors.CheckViolation):
        archive(conn, pmc, property_id=pmc.owners[0].property_id, unit_id=unit)
    with pytest.raises(psycopg.errors.CheckViolation):
        archive(conn, pmc)
    with pytest.raises(psycopg.errors.CheckViolation):
        archive(conn, pmc, tenant_id=tenant_of(conn, pmc), owner_id=pmc.owners[0].owner_id)
    other = make_pmc(conn)
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        archive(conn, other, unit_id=unit)
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        archive(conn, other, tenant_id=tenant_of(conn, pmc))
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        archive(conn, other, owner_id=pmc.owners[0].owner_id)

    manager = staff_named(conn, pmc.pmc_id, "Manager 1")
    with acting_as(conn, manager):
        archive(conn, pmc, unit_id=unit)
    assert conn.execute(
        "SELECT recorded_by FROM trust_archivals WHERE unit_id = %s", (unit,)
    ).fetchall() == [(manager,)]


@pytest.mark.parametrize(
    "statement", ["UPDATE trust_archivals SET archived = false", "DELETE FROM trust_archivals"]
)
def test_archivals_are_kept_for_good(conn, pmc, statement):
    archive(conn, pmc, unit_id=add_unit(conn, pmc.pmc_id, pmc.owners[0].property_id, "Unit 1"))
    with pytest.raises(psycopg.errors.RestrictViolation, match="append-only"):
        conn.execute(statement)
