"""Who is on a lease besides its tenants, and a tenant leaving it (migration 20261018000080).

A tenant leaves a lease others stay on, once, within its days; their deposit moves to a tenant
who stays (inside the security-deposit account, so it still ties out) or stays under their name
until the lease's deposit is returned. The last tenant can't leave: the lease ends instead. A
renewal keeps the tenants who stayed, and an offer's guarantor signers become the lease's
guarantors, never its tenants. Occupants, pets, vehicles and guarantors are append-only rows a
removal takes off once. Every name here is synthetic."""

from datetime import UTC, date, datetime
from decimal import Decimal

import psycopg
import pytest
from helpers import (
    acting_as,
    add_staff,
    add_tenant,
    add_unit,
    bank_sums,
    charge,
    complete_offer,
    end_lease,
    make_pmc,
    offer_lease,
    open_application_link,
    open_lease,
    record_decision,
    record_screening,
    return_deposit,
    send_offer,
    sign_offer,
    submit_application,
    transfer_batch,
)

JAN_1, MAR_31, JUN_30, JUL_5 = (
    date(2026, 1, 1),
    date(2026, 3, 31),
    date(2026, 6, 30),
    date(2026, 7, 5),
)


def at(day):
    return datetime(day.year, day.month, day.day, 12, tzinfo=UTC)


@pytest.fixture
def pmc(conn):
    return make_pmc(conn)


@pytest.fixture
def tenants(conn, pmc):
    """Tenant 1 (holding a 1500.00 deposit), Tenant 2 and Tenant 3, all of Property 1."""
    first = conn.execute(
        "SELECT tenant_id FROM trust_ledger_accounts WHERE ledger_account_id = %s",
        (pmc.tenant_deposit,),
    ).fetchone()[0]
    transfer_batch(conn, [(pmc.deposit_cash, pmc.tenant_deposit, "1500.00")], at(JAN_1))
    prop = pmc.owners[0].property_id
    return (
        first,
        add_tenant(conn, pmc.pmc_id, prop, "Tenant 2"),
        add_tenant(conn, pmc.pmc_id, prop, "Tenant 3"),
    )


@pytest.fixture
def lease(conn, pmc, tenants):
    unit = add_unit(conn, pmc.pmc_id, pmc.owners[0].property_id)
    return open_lease(conn, pmc.pmc_id, unit, JAN_1, JUN_30, "1500.00", list(tenants))


def remove(conn, pmc_id, lease, tenant, left_on=MAR_31, deposit_to=None):
    return conn.execute(
        "SELECT trust_remove_lease_tenant(%s, %s, %s, %s, %s)",
        (pmc_id, lease, tenant, left_on, deposit_to),
    ).fetchone()[0]


def deposit_of(conn, tenant):
    return conn.execute(
        "SELECT coalesce(sum(a.balance), 0) FROM trust_ledger_accounts AS t"
        " JOIN pgledger_accounts AS a ON a.id = t.ledger_account_id"
        " WHERE t.tenant_id = %s AND t.kind = 'tenant_deposit'",
        (tenant,),
    ).fetchone()[0]


def moves(conn, lease):
    return conn.execute(
        "SELECT count(*) FROM pgledger_transfers WHERE metadata ->> 'kind' = 'deposit moved'"
        " AND metadata ->> 'lease_id' = %s",
        (str(lease),),
    ).fetchone()[0]


def test_a_leaving_tenants_deposit_moves_to_one_who_stays(conn, pmc, tenants, lease):
    first, second, _ = tenants
    staff = add_staff(conn, pmc.pmc_id, "Clerk 1")
    with acting_as(conn, staff):
        assert remove(conn, pmc.pmc_id, lease, first, deposit_to=second) == Decimal("1500.00")
    assert (deposit_of(conn, first), deposit_of(conn, second)) == (0, Decimal("1500.00"))
    # Inside the security-deposit account: its cash didn't move and every account ties out.
    assert conn.execute(
        "SELECT balance FROM pgledger_accounts WHERE id = %s", (pmc.deposit_cash,)
    ).fetchone() == (Decimal("-1500.00"),)
    assert all(total == 0 for total in bank_sums(conn, pmc.pmc_id).values())
    assert conn.execute(
        "SELECT left_on, deposit_to, moved, recorded_by FROM trust_lease_departures"
        " WHERE lease_id = %s",
        (lease,),
    ).fetchall() == [(MAR_31, second, Decimal("1500.00"), staff)]
    # The same again moves nothing more.
    assert remove(conn, pmc.pmc_id, lease, first, deposit_to=second) == Decimal("1500.00")
    assert moves(conn, lease) == 1


def test_a_deposit_left_under_their_name_is_settled_with_the_lease(conn, pmc, tenants, lease):
    first, _, _ = tenants
    assert remove(conn, pmc.pmc_id, lease, first) == 0
    assert deposit_of(conn, first) == Decimal("1500.00")
    charge(conn, pmc.pmc_id, lease, JUN_30, "200.00", kind="fee", memo="Cleaning")
    end_lease(conn, pmc.pmc_id, lease, JUN_30)
    return_deposit(conn, pmc.pmc_id, lease, "move-out-1", at(JUL_5))
    assert conn.execute(
        "SELECT tenant_id, charge_id IS NULL, amount FROM trust_deposit_return_lines"
        " WHERE lease_id = %s ORDER BY line_no",
        (lease,),
    ).fetchall() == [(first, False, Decimal("200.00")), (first, True, Decimal("1300.00"))]
    # Once the deposit is returned, nobody leaves the lease any more.
    with pytest.raises(psycopg.errors.CheckViolation, match="was returned already"):
        remove(conn, pmc.pmc_id, lease, tenants[1])


def test_a_departure_names_a_tenant_on_the_lease_within_its_days(conn, pmc, tenants, lease):
    first, second, third = tenants
    stranger = add_tenant(conn, pmc.pmc_id, pmc.owners[0].property_id, "Tenant 4")
    with pytest.raises(psycopg.errors.InvalidParameterValue):
        remove(conn, pmc.pmc_id, lease, stranger)
    with pytest.raises(psycopg.errors.CheckViolation, match="within its days"):
        remove(conn, pmc.pmc_id, lease, first, left_on=date(2025, 12, 31))
    with pytest.raises(psycopg.errors.CheckViolation, match="within its days"):
        remove(conn, pmc.pmc_id, lease, first, left_on=JUL_5)
    # Another company's lease is not found in this one.
    with pytest.raises(psycopg.errors.InvalidParameterValue):
        remove(conn, make_pmc(conn).pmc_id, lease, first)
    # The deposit goes to someone staying: not the leaver, not a stranger, not one who left.
    for to in (first, stranger):
        with pytest.raises(psycopg.errors.InvalidParameterValue):
            remove(conn, pmc.pmc_id, lease, first, deposit_to=to)
    remove(conn, pmc.pmc_id, lease, third)
    with pytest.raises(psycopg.errors.InvalidParameterValue):
        remove(conn, pmc.pmc_id, lease, first, deposit_to=third)
    # Leaving again on another day, or with the deposit going elsewhere, is refused.
    with pytest.raises(psycopg.errors.UniqueViolation, match="left this lease on"):
        remove(conn, pmc.pmc_id, lease, third, left_on=date(2026, 4, 30))
    with pytest.raises(psycopg.errors.UniqueViolation):
        remove(conn, pmc.pmc_id, lease, third, deposit_to=second)
    # The last tenant can't leave: the lease ends instead.
    remove(conn, pmc.pmc_id, lease, first)
    with pytest.raises(psycopg.errors.CheckViolation, match="last tenant on this lease"):
        remove(conn, pmc.pmc_id, lease, second)


def test_a_tenant_on_another_lease_keeps_their_deposit_with_it(conn, pmc, tenants, lease):
    first, second, _ = tenants
    other_unit = add_unit(conn, pmc.pmc_id, pmc.owners[0].property_id, name="Unit 2")
    open_lease(conn, pmc.pmc_id, other_unit, JAN_1, None, "900.00", [first])
    with pytest.raises(psycopg.errors.CheckViolation, match="on the lease of Unit 2 still"):
        remove(conn, pmc.pmc_id, lease, first, deposit_to=second)
    # Left under their name, nothing moves.
    assert remove(conn, pmc.pmc_id, lease, first) == 0
    assert deposit_of(conn, first) == Decimal("1500.00")


def test_departures_are_written_at_read_committed_and_by_the_function_only(
    conn, app_conn, pmc, tenants, lease
):
    with conn.transaction():
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
        with pytest.raises(psycopg.errors.InvalidTransactionState):
            remove(conn, pmc.pmc_id, lease, tenants[0])
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        app_conn.execute(
            "INSERT INTO trust_lease_departures (pmc_id, lease_id, tenant_id, left_on)"
            " VALUES (%s, %s, %s, %s)",
            (pmc.pmc_id, lease, tenants[0], MAR_31),
        )
    remove(conn, pmc.pmc_id, lease, tenants[0])
    with pytest.raises(psycopg.errors.RestrictViolation, match="append-only"):
        conn.execute("UPDATE trust_lease_departures SET left_on = %s", (JUN_30,))
    assert conn.execute(
        "SELECT count(*) FROM trust_audit_log WHERE table_name = 'trust_lease_departures'"
        " AND row_data ->> 'lease_id' = %s",
        (str(lease),),
    ).fetchone() == (1,)


def sign_all(conn, pmc_id, offer, staff):
    send_offer(conn, pmc_id, offer, staff)
    for (position,) in conn.execute(
        "SELECT position FROM trust_lease_offer_signers WHERE offer_id = %s", (offer,)
    ).fetchall():
        sign_offer(conn, pmc_id, offer, position)


def test_a_renewal_keeps_who_stayed_and_its_guarantor_becomes_the_guarantor(
    conn, pmc, tenants, lease
):
    first, second, third = tenants
    staff = add_staff(conn, pmc.pmc_id, "Clerk 1")
    remove(conn, pmc.pmc_id, lease, first)
    offer = offer_lease(
        conn,
        pmc.pmc_id,
        conn.execute("SELECT unit_id FROM trust_leases WHERE id = %s", (lease,)).fetchone()[0],
        staff,
        date(2026, 7, 1),
        date(2027, 6, 30),
        renews_lease_id=lease,
        signers=(("Tenant 2", "tenant2@example.com"),),
    )
    conn.execute(
        "INSERT INTO trust_lease_offer_signers (pmc_id, offer_id, position, display_name, email,"
        " role) VALUES (%s, %s, 2, 'Guarantor 1', 'guarantor1@example.com', 'guarantor')",
        (pmc.pmc_id, offer),
    )
    sign_all(conn, pmc.pmc_id, offer, staff)
    renewed = complete_offer(conn, pmc.pmc_id, offer, staff)
    assert {
        t
        for (t,) in conn.execute(
            "SELECT tenant_id FROM trust_lease_tenants WHERE lease_id = %s", (renewed,)
        ).fetchall()
    } == {second, third}
    assert conn.execute(
        "SELECT display_name, email, offer_id FROM trust_lease_guarantors WHERE lease_id = %s",
        (renewed,),
    ).fetchall() == [("Guarantor 1", "guarantor1@example.com", offer)]


def approved_application(conn, pmc, staff, unit):
    link = open_application_link(conn, pmc.pmc_id, unit, staff)
    application = submit_application(
        conn, pmc.pmc_id, link, applicants=(("Applicant 1", "applicant1@example.com"),)
    )
    screening = record_screening(conn, pmc.pmc_id, application, staff, "approve", [])
    record_decision(conn, pmc.pmc_id, application, staff, "approved", screening)
    return application


def test_a_new_leases_guarantor_signs_without_becoming_a_tenant(conn, pmc):
    staff = add_staff(conn, pmc.pmc_id, "Clerk 1")
    unit = add_unit(conn, pmc.pmc_id, pmc.owners[0].property_id, name="Unit 7")
    application = approved_application(conn, pmc, staff, unit)
    offer = offer_lease(
        conn,
        pmc.pmc_id,
        unit,
        staff,
        date(2026, 8, 1),
        application_id=application,
        signers=(("Applicant 1", "applicant1@example.com"),),
    )
    conn.execute(
        "INSERT INTO trust_lease_offer_signers (pmc_id, offer_id, position, display_name, email,"
        " role) VALUES (%s, %s, 2, 'Guarantor 1', 'guarantor1@example.com', 'guarantor')",
        (pmc.pmc_id, offer),
    )
    sign_all(conn, pmc.pmc_id, offer, staff)
    made = complete_offer(conn, pmc.pmc_id, offer, staff)
    assert conn.execute(
        "SELECT t.display_name FROM trust_lease_tenants AS lt"
        " JOIN trust_tenants AS t ON t.id = lt.tenant_id WHERE lt.lease_id = %s",
        (made,),
    ).fetchall() == [("Applicant 1",)]
    assert conn.execute(
        "SELECT display_name FROM trust_lease_guarantors WHERE lease_id = %s", (made,)
    ).fetchall() == [("Guarantor 1",)]


def test_an_offer_signed_by_guarantors_alone_makes_no_lease(conn, pmc):
    staff = add_staff(conn, pmc.pmc_id, "Clerk 1")
    unit = add_unit(conn, pmc.pmc_id, pmc.owners[0].property_id, name="Unit 8")
    application = approved_application(conn, pmc, staff, unit)
    offer = offer_lease(
        conn, pmc.pmc_id, unit, staff, date(2026, 8, 1), application_id=application, signers=()
    )
    conn.execute(
        "INSERT INTO trust_lease_offer_signers (pmc_id, offer_id, position, display_name, email,"
        " role) VALUES (%s, %s, 1, 'Guarantor 1', 'guarantor1@example.com', 'guarantor')",
        (pmc.pmc_id, offer),
    )
    sign_all(conn, pmc.pmc_id, offer, staff)
    with pytest.raises(psycopg.errors.CheckViolation, match="no tenant to make its lease with"):
        complete_offer(conn, pmc.pmc_id, offer, staff)
    with pytest.raises(psycopg.errors.CheckViolation):
        conn.execute(
            "INSERT INTO trust_lease_offer_signers (pmc_id, offer_id, position, display_name,"
            " email, role) VALUES (%s, %s, 2, 'Signer 2', 'signer2@example.com', 'landlord')",
            (pmc.pmc_id, offer),
        )


def test_household_rows_are_checked_taken_off_once_and_kept(conn, pmc, lease):
    staff = add_staff(conn, pmc.pmc_id, "Clerk 1")
    with acting_as(conn, staff):
        pet = conn.execute(
            "INSERT INTO trust_lease_pets (pmc_id, lease_id, display_name, kind, weight_lbs,"
            " assistance_animal) VALUES (%s, %s, 'Pet 1', 'Dog', 42.5, true) RETURNING id",
            (pmc.pmc_id, lease),
        ).fetchone()[0]
    for bad in (
        "INSERT INTO trust_lease_pets (pmc_id, lease_id, display_name, kind, weight_lbs)"
        " VALUES (%s, %s, 'Pet 2', 'Dog', 0)",
        "INSERT INTO trust_lease_pets (pmc_id, lease_id, display_name, kind, weight_lbs)"
        " VALUES (%s, %s, 'Pet 2', 'Dog', 4.25)",
        "INSERT INTO trust_lease_occupants (pmc_id, lease_id, display_name) VALUES (%s, %s, ' ')",
        "INSERT INTO trust_lease_vehicles (pmc_id, lease_id, make, plate)"
        " VALUES (%s, %s, 'Make 1', 'PLATE-TOO-LONG-1')",
        "INSERT INTO trust_lease_guarantors (pmc_id, lease_id, display_name, email)"
        " VALUES (%s, %s, 'Guarantor 1', 'Guarantor1@Example.com')",
    ):
        with pytest.raises(psycopg.errors.CheckViolation):
            conn.execute(bad, (pmc.pmc_id, lease))
    # Another company's lease takes none of them.
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        conn.execute(
            "INSERT INTO trust_lease_occupants (pmc_id, lease_id, display_name)"
            " VALUES (%s, %s, 'Occupant 1')",
            (make_pmc(conn).pmc_id, lease),
        )
    # A removal names exactly one, once.
    with pytest.raises(psycopg.errors.CheckViolation):
        conn.execute(
            "INSERT INTO trust_lease_household_removals (pmc_id) VALUES (%s)", (pmc.pmc_id,)
        )
    conn.execute(
        "INSERT INTO trust_lease_household_removals (pmc_id, pet_id) VALUES (%s, %s)",
        (pmc.pmc_id, pet),
    )
    with pytest.raises(psycopg.errors.UniqueViolation):
        conn.execute(
            "INSERT INTO trust_lease_household_removals (pmc_id, pet_id) VALUES (%s, %s)",
            (pmc.pmc_id, pet),
        )
    for table in ("trust_lease_pets", "trust_lease_household_removals"):
        with pytest.raises(psycopg.errors.RestrictViolation, match="append-only"):
            conn.execute(f"DELETE FROM {table}")
    assert conn.execute(
        "SELECT recorded_by, assistance_animal FROM trust_lease_pets WHERE id = %s", (pet,)
    ).fetchone() == (staff, True)
    assert conn.execute(
        "SELECT count(*) FROM trust_audit_log WHERE table_name = 'trust_lease_pets'"
        " AND row_data ->> 'id' = %s",
        (str(pet),),
    ).fetchone() == (1,)


def test_lease_break_fee_terms_are_one_a_day_and_in_bounds(conn, pmc):
    prop = pmc.owners[0].property_id
    insert = (
        "INSERT INTO trust_lease_break_fees (pmc_id, property_id, starts_on, flat_fee, rent_months)"
        " VALUES (%s, %s, %s, %s, %s)"
    )
    conn.execute(insert, (pmc.pmc_id, prop, JAN_1, Decimal("500.00"), Decimal("1.5")))
    with pytest.raises(psycopg.errors.UniqueViolation):
        conn.execute(insert, (pmc.pmc_id, prop, JAN_1, Decimal(0), Decimal(1)))
    for flat, months in ((Decimal("-1"), Decimal(0)), (Decimal(0), Decimal(13))):
        with pytest.raises(psycopg.errors.CheckViolation):
            conn.execute(insert, (pmc.pmc_id, prop, MAR_31, flat, months))
    with pytest.raises(psycopg.errors.RestrictViolation, match="append-only"):
        conn.execute("UPDATE trust_lease_break_fees SET flat_fee = 0")
