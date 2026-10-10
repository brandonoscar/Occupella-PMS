"""Units, leases, charges and payment matching (migration 20261010000012).

A unit belongs to a property; a lease holds one unit for one or more of the property's tenants,
and two leases of a unit never overlap. Charges (rent, fees, credits) record what a lease's
tenants owe, outside the trust ledger. A payment posted to the ledger is then matched to the
charges it pays: never past a charge's amount, never past what the transfer moved, and the same
match retried returns the original.
"""

from datetime import UTC, date, datetime
from decimal import Decimal

import psycopg
import pytest
from helpers import (
    add_tenant,
    add_unit,
    apply_payment,
    charge,
    end_lease,
    in_background,
    make_pmc,
    open_lease,
    transfer_batch,
    wait_until_blocked,
)

JAN_1 = date(2026, 1, 1)
JAN_31 = date(2026, 1, 31)
FEB_1 = date(2026, 2, 1)
DEC_31 = date(2026, 12, 31)
RENT = Decimal("1500.00")
PAID_ON = datetime(2026, 1, 3, tzinfo=UTC)


@pytest.fixture
def pmc(conn):
    return make_pmc(conn)


@pytest.fixture
def unit(conn, pmc):
    return add_unit(conn, pmc.pmc_id, pmc.owners[0].property_id)


@pytest.fixture
def tenant(conn, pmc):
    return conn.execute(
        "SELECT tenant_id FROM trust_ledger_accounts WHERE ledger_account_id = %s",
        (pmc.tenant_deposit,),
    ).fetchone()[0]


@pytest.fixture
def lease(conn, pmc, unit, tenant):
    return open_lease(conn, pmc.pmc_id, unit, JAN_1, DEC_31, RENT, [tenant])


def lease_row(conn, lease_id):
    return conn.execute(
        "SELECT unit_id, starts_on, ends_on, monthly_rent FROM trust_leases WHERE id = %s",
        (lease_id,),
    ).fetchone()


def tenants_on(conn, lease_id):
    rows = conn.execute(
        "SELECT tenant_id FROM trust_lease_tenants WHERE lease_id = %s", (lease_id,)
    ).fetchall()
    return {row[0] for row in rows}


def matches(conn, pmc):
    return conn.execute(
        "SELECT charge_id, transfer_id, amount FROM trust_charge_payments WHERE pmc_id = %s"
        " ORDER BY created_at, transfer_id",
        (pmc.pmc_id,),
    ).fetchall()


def pay_rent(conn, pmc, amount=RENT, source=None, when=PAID_ON):
    """Rent received into Owner 1's account for Property 1: the transfer id."""
    source = pmc.operating_cash if source is None else source
    return transfer_batch(conn, [(source, pmc.owners[0].account, amount)], event_at=when)[0]


# --- units ---------------------------------------------------------------------------------


def test_the_app_adds_and_renames_units(app_conn, pmc):
    unit = add_unit(app_conn, pmc.pmc_id, pmc.owners[0].property_id, "Unit A")
    app_conn.execute("UPDATE trust_units SET display_name = 'Unit B' WHERE id = %s", (unit,))

    assert app_conn.execute(
        "SELECT display_name FROM trust_units WHERE id = %s", (unit,)
    ).fetchone() == ("Unit B",)


def test_a_unit_belongs_to_a_property_of_its_own_pmc(conn, pmc):
    other = make_pmc(conn)

    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        add_unit(conn, pmc.pmc_id, other.owners[0].property_id)


# --- leases --------------------------------------------------------------------------------


def test_a_lease_holds_a_unit_for_its_tenants(conn, app_conn, pmc, unit, tenant):
    co_tenant = add_tenant(conn, pmc.pmc_id, pmc.owners[0].property_id, "Tenant 2")

    # The app opens it; a tenant named twice is on the lease once.
    lease = open_lease(app_conn, pmc.pmc_id, unit, JAN_1, None, RENT, [tenant, co_tenant, tenant])

    assert lease_row(conn, lease) == (unit, JAN_1, None, RENT)
    assert tenants_on(conn, lease) == {tenant, co_tenant}


@pytest.mark.parametrize("where", ["unknown", "another PMC's"])
def test_a_lease_of_a_unit_outside_the_pmc_is_refused(conn, pmc, tenant, where):
    other = make_pmc(conn)
    unit = add_unit(conn, other.pmc_id, other.owners[0].property_id)
    unit = unit if where == "another PMC's" else pmc.pmc_id  # a uuid that is no unit

    with pytest.raises(psycopg.errors.InvalidParameterValue, match="no unit"):
        open_lease(conn, pmc.pmc_id, unit, JAN_1, None, RENT, [tenant])


@pytest.mark.parametrize("tenants", [[], None])
def test_a_lease_needs_a_tenant(conn, pmc, unit, tenants):
    with pytest.raises(psycopg.errors.InvalidParameterValue, match="at least one tenant"):
        conn.execute(
            "SELECT trust_open_lease(%s, %s, %s, NULL, %s, %s::uuid[])",
            (pmc.pmc_id, unit, JAN_1, RENT, tenants),
        )


@pytest.mark.parametrize("who", ["another property's", "another PMC's", "unknown"])
def test_every_tenant_on_a_lease_is_a_tenant_of_the_units_property(conn, pmc, unit, tenant, who):
    other = make_pmc(conn)
    stranger = {
        "another property's": add_tenant(conn, pmc.pmc_id, pmc.owners[1].property_id, "Tenant 2"),
        "another PMC's": add_tenant(conn, other.pmc_id, other.owners[0].property_id, "Tenant 3"),
        "unknown": unit,  # a uuid that is no tenant
    }[who]

    with pytest.raises(psycopg.errors.InvalidParameterValue, match="tenant of the unit"):
        open_lease(conn, pmc.pmc_id, unit, JAN_1, None, RENT, [tenant, stranger])
    assert (
        conn.execute("SELECT count(*) FROM trust_leases WHERE unit_id = %s", (unit,)).fetchone()[0]
        == 0
    )


@pytest.mark.parametrize(
    ("first", "second"),
    [
        ((JAN_1, DEC_31), (JAN_1, DEC_31)),  # the same term
        ((JAN_1, DEC_31), (DEC_31, None)),  # starts on the other's last day
        ((FEB_1, DEC_31), (JAN_1, FEB_1)),  # ends on the other's first day
        ((JAN_1, None), (date(2030, 1, 1), None)),  # the first is month to month
        ((FEB_1, FEB_1), (JAN_1, None)),  # the second covers it
    ],
)
def test_two_leases_of_one_unit_never_overlap(conn, pmc, unit, tenant, first, second):
    held = open_lease(conn, pmc.pmc_id, unit, *first, RENT, [tenant])

    with pytest.raises(psycopg.errors.ExclusionViolation, match=f"overlap lease {held}"):
        open_lease(conn, pmc.pmc_id, unit, *second, RENT, [tenant])


def test_leases_may_follow_each_other_day_by_day_and_other_units_are_free(conn, pmc, unit, tenant):
    open_lease(conn, pmc.pmc_id, unit, JAN_1, JAN_31, RENT, [tenant])
    open_lease(conn, pmc.pmc_id, unit, FEB_1, None, RENT, [tenant])
    other_unit = add_unit(conn, pmc.pmc_id, pmc.owners[0].property_id, "Unit 2")
    open_lease(conn, pmc.pmc_id, other_unit, JAN_1, None, RENT, [tenant])


def test_the_owner_writing_leases_directly_is_held_to_the_same_rule(conn, pmc, unit, lease):
    with pytest.raises(psycopg.errors.ExclusionViolation, match="overlap"):
        conn.execute(
            "INSERT INTO trust_leases (pmc_id, unit_id, starts_on, monthly_rent)"
            " VALUES (%s, %s, %s, %s)",
            (pmc.pmc_id, unit, FEB_1, RENT),
        )


def test_a_lease_ends_on_or_after_its_first_day(conn, pmc, unit, tenant, lease):
    with pytest.raises(psycopg.errors.CheckViolation):
        open_lease(conn, pmc.pmc_id, unit, date(2027, 2, 1), date(2027, 1, 31), RENT, [tenant])
    with pytest.raises(psycopg.errors.CheckViolation):
        end_lease(conn, pmc.pmc_id, lease, date(2025, 12, 31))


@pytest.mark.parametrize("rent", ["0", "-1500.00"])
def test_monthly_rent_is_positive(conn, pmc, unit, tenant, rent):
    with pytest.raises(psycopg.errors.CheckViolation):
        open_lease(conn, pmc.pmc_id, unit, JAN_1, None, rent, [tenant])


def test_ending_a_lease_moves_its_last_day_either_way(conn, app_conn, pmc, lease):
    end_lease(app_conn, pmc.pmc_id, lease, JAN_31)  # an early move-out
    assert lease_row(conn, lease)[2] == JAN_31
    end_lease(app_conn, pmc.pmc_id, lease, None)  # month to month after all
    assert lease_row(conn, lease)[2] is None


def test_a_lease_cannot_be_stretched_over_the_next_one(conn, pmc, unit, tenant, lease):
    end_lease(conn, pmc.pmc_id, lease, JAN_31)
    after = open_lease(conn, pmc.pmc_id, unit, FEB_1, None, RENT, [tenant])

    with pytest.raises(psycopg.errors.ExclusionViolation, match=f"overlap lease {after}"):
        end_lease(conn, pmc.pmc_id, lease, None)
    assert lease_row(conn, lease)[2] == JAN_31


def test_ending_a_lease_outside_the_pmc_is_refused(conn, pmc, lease):
    other = make_pmc(conn)

    with pytest.raises(psycopg.errors.InvalidParameterValue, match="no lease"):
        end_lease(conn, other.pmc_id, lease, JAN_31)
    assert lease_row(conn, lease)[2] == DEC_31


@pytest.mark.parametrize("isolation", ["REPEATABLE READ", "SERIALIZABLE"])
def test_leases_are_written_at_read_committed(database_url, pmc, unit, tenant, isolation):
    # From an older snapshot, the overlap check could miss a lease committed while it waited.
    with psycopg.connect(database_url) as tx:
        tx.execute(f"SET TRANSACTION ISOLATION LEVEL {isolation}")
        with pytest.raises(psycopg.errors.InvalidTransactionState, match="READ COMMITTED"):
            open_lease(tx, pmc.pmc_id, unit, JAN_1, None, RENT, [tenant])


@pytest.mark.timing
def test_a_lease_waits_for_one_in_progress_on_the_unit_and_is_then_refused(
    conn, database_url, pmc, unit, tenant
):
    with psycopg.connect(database_url) as first:
        held = open_lease(first, pmc.pmc_id, unit, JAN_1, None, RENT, [tenant])  # not committed
        thread, pid, outcome = in_background(
            database_url,
            lambda worker: open_lease(worker, pmc.pmc_id, unit, FEB_1, None, RENT, [tenant]),
        )
        wait_until_blocked(conn, pid)
        first.commit()
    thread.join(timeout=30)

    assert isinstance(outcome.get("error"), psycopg.errors.ExclusionViolation), outcome
    assert f"overlap lease {held}" in str(outcome["error"])


@pytest.mark.parametrize("statement", ["UPDATE", "DELETE"])
def test_a_leases_tenants_are_kept_for_good(conn, lease, statement):
    query = (
        "UPDATE trust_lease_tenants SET tenant_id = tenant_id WHERE lease_id = %s"
        if statement == "UPDATE"
        else "DELETE FROM trust_lease_tenants WHERE lease_id = %s"
    )
    with pytest.raises(psycopg.errors.RestrictViolation, match="append-only"):
        conn.execute(query, (lease,))


# --- charges -------------------------------------------------------------------------------


def test_the_app_charges_rent_fees_and_credits(conn, app_conn, pmc, lease):
    for kind in ("rent", "fee", "credit"):
        charge(app_conn, pmc.pmc_id, lease, FEB_1, "50.00", kind)

    assert conn.execute(
        "SELECT kind FROM trust_charges WHERE lease_id = %s ORDER BY kind", (lease,)
    ).fetchall() == [("credit",), ("fee",), ("rent",)]


def test_rent_is_charged_once_per_due_date_so_a_retried_run_cannot_bill_twice(conn, pmc, lease):
    charge(conn, pmc.pmc_id, lease, FEB_1, RENT)
    charge(conn, pmc.pmc_id, lease, FEB_1, "35.00", "fee")
    charge(conn, pmc.pmc_id, lease, FEB_1, "35.00", "fee")  # two late fees are two fees

    with pytest.raises(psycopg.errors.UniqueViolation, match="one_rent_per_due_date"):
        charge(conn, pmc.pmc_id, lease, FEB_1, RENT)


@pytest.mark.parametrize(("kind", "amount"), [("rent", "0"), ("credit", "-5.00"), ("late", "5")])
def test_a_charge_is_a_known_kind_with_a_positive_amount(conn, pmc, lease, kind, amount):
    with pytest.raises(psycopg.errors.CheckViolation):
        charge(conn, pmc.pmc_id, lease, FEB_1, amount, kind)


def test_a_charge_stays_inside_its_leases_pmc(conn, pmc, lease):
    other = make_pmc(conn)

    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        charge(conn, other.pmc_id, lease, FEB_1, RENT)


@pytest.mark.parametrize(
    ("table", "statement"),
    [
        ("trust_charges", "UPDATE trust_charges SET amount = amount + 1"),
        ("trust_charges", "DELETE FROM trust_charges"),
        ("trust_charge_payments", "UPDATE trust_charge_payments SET amount = amount + 1"),
        ("trust_charge_payments", "DELETE FROM trust_charge_payments"),
    ],
)
def test_charges_and_matches_are_kept_for_good(conn, pmc, lease, table, statement):
    paid = charge(conn, pmc.pmc_id, lease, JAN_1, RENT)
    apply_payment(conn, pmc.pmc_id, paid, pay_rent(conn, pmc), RENT)

    with pytest.raises(psycopg.errors.RestrictViolation, match="append-only"):
        conn.execute(statement)


# --- payment matching ----------------------------------------------------------------------


def test_a_payment_is_matched_to_the_charge_it_pays(conn, app_conn, pmc, lease):
    rent = charge(conn, pmc.pmc_id, lease, JAN_1, RENT)
    payment = pay_rent(conn, pmc)

    assert apply_payment(app_conn, pmc.pmc_id, rent, payment, RENT) == RENT
    assert matches(conn, pmc) == [(rent, payment, RENT)]


def test_the_same_match_retried_returns_the_original(conn, pmc, lease):
    rent = charge(conn, pmc.pmc_id, lease, JAN_1, RENT)
    payment = pay_rent(conn, pmc)
    apply_payment(conn, pmc.pmc_id, rent, payment, "1000.00")

    assert apply_payment(conn, pmc.pmc_id, rent, payment, "1000.00") == Decimal("1000.00")
    with pytest.raises(psycopg.errors.UniqueViolation, match="already pays 1000.00"):
        apply_payment(conn, pmc.pmc_id, rent, payment, "1500.00")
    assert matches(conn, pmc) == [(rent, payment, Decimal("1000.00"))]


def test_a_charge_is_never_paid_past_its_amount(conn, pmc, lease):
    rent = charge(conn, pmc.pmc_id, lease, JAN_1, RENT)
    apply_payment(conn, pmc.pmc_id, rent, pay_rent(conn, pmc, "1000.00"), "1000.00")
    apply_payment(conn, pmc.pmc_id, rent, pay_rent(conn, pmc, "400.00"), "400.00")
    last = pay_rent(conn, pmc, "100.01")

    with pytest.raises(psycopg.errors.CheckViolation, match="too much"):
        apply_payment(conn, pmc.pmc_id, rent, last, "100.01")
    assert apply_payment(conn, pmc.pmc_id, rent, last, "100.00") == Decimal("100.00")


def test_a_transfer_never_pays_more_than_it_moved(conn, pmc, lease):
    rent = charge(conn, pmc.pmc_id, lease, JAN_1, RENT)
    fee = charge(conn, pmc.pmc_id, lease, JAN_1, "50.00", "fee")
    payment = pay_rent(conn, pmc, "1525.00")
    apply_payment(conn, pmc.pmc_id, rent, payment, RENT)

    with pytest.raises(psycopg.errors.CheckViolation, match="moved 1525.00"):
        apply_payment(conn, pmc.pmc_id, fee, payment, "50.00")
    assert apply_payment(conn, pmc.pmc_id, fee, payment, "25.00") == Decimal("25.00")


def test_rent_paid_from_a_tenants_prepaid_rent_is_matched(conn, pmc, lease):
    transfer_batch(conn, [(pmc.operating_cash, pmc.prepaid_rent, RENT)], event_at=PAID_ON)
    rent = charge(conn, pmc.pmc_id, lease, FEB_1, RENT)
    applied = pay_rent(conn, pmc, source=pmc.prepaid_rent, when=datetime(2026, 2, 1, tzinfo=UTC))

    assert apply_payment(conn, pmc.pmc_id, rent, applied, RENT) == RENT


def test_a_deposit_kept_at_move_out_is_matched_to_what_it_pays(conn, pmc, lease):
    transfer_batch(conn, [(pmc.deposit_cash, pmc.tenant_deposit, RENT)], event_at=PAID_ON)
    damages = charge(conn, pmc.pmc_id, lease, DEC_31, "320.00", "fee", "Damages")
    # The kept part of the deposit moves to the owner, and its cash with it.
    kept = transfer_batch(
        conn,
        [
            (pmc.tenant_deposit, pmc.owners[0].account, "320.00"),
            (pmc.operating_cash, pmc.deposit_cash, "320.00"),
        ],
        event_at=datetime(2027, 1, 5, tzinfo=UTC),
    )[0]

    assert apply_payment(conn, pmc.pmc_id, damages, kept, "320.00") == Decimal("320.00")


def wrong_transfers(conn, pmc):
    """Transfers that did not pay this lease's rent, by what is wrong with them."""
    stranger = add_tenant(conn, pmc.pmc_id, pmc.owners[0].property_id, "Tenant 2")
    strangers_prepaid = conn.execute(
        "SELECT trust_open_ledger_account(%s, %s, 'prepaid_rent', NULL, NULL, %s)",
        (pmc.pmc_id, pmc.operating_bank_id, stranger),
    ).fetchone()[0]
    cash, owner_1, owner_2 = pmc.operating_cash, pmc.owners[0].account, pmc.owners[1].account
    transfer_batch(
        conn,
        [
            (cash, pmc.prepaid_rent, RENT),
            (cash, strangers_prepaid, RENT),
            (cash, owner_1, RENT),
            (cash, owner_2, RENT),
        ],
        event_at=PAID_ON,
    )
    return {
        "into another property's owner": (cash, owner_2),
        "into the PMC's fees": (cash, pmc.pmc_income),
        "into prepaid rent": (cash, pmc.prepaid_rent),
        "from another tenant's prepaid rent": (strangers_prepaid, owner_1),
        "from another owner's account": (owner_2, owner_1),
    }


@pytest.mark.parametrize(
    "wrong",
    [
        "into another property's owner",
        "into the PMC's fees",
        "into prepaid rent",
        "from another tenant's prepaid rent",
        "from another owner's account",
    ],
)
def test_a_transfer_that_did_not_pay_the_leases_rent_is_refused(conn, pmc, lease, wrong):
    rent = charge(conn, pmc.pmc_id, lease, JAN_1, RENT)
    source, target = wrong_transfers(conn, pmc)[wrong]
    transfer_id = transfer_batch(conn, [(source, target, "10.00")], event_at=PAID_ON)[0]

    with pytest.raises(psycopg.errors.InvalidParameterValue, match="did not pay into"):
        apply_payment(conn, pmc.pmc_id, rent, transfer_id, "10.00")
    assert matches(conn, pmc) == []


def test_a_credit_is_not_paid(conn, pmc, lease):
    credit = charge(conn, pmc.pmc_id, lease, JAN_1, "100.00", "credit")

    with pytest.raises(psycopg.errors.InvalidParameterValue, match="credit is not paid"):
        apply_payment(conn, pmc.pmc_id, credit, pay_rent(conn, pmc, "100.00"), "100.00")


@pytest.mark.parametrize("amount", ["0", "-1.00", None])
def test_a_match_applies_a_positive_amount(conn, pmc, lease, amount):
    rent = charge(conn, pmc.pmc_id, lease, JAN_1, RENT)

    with pytest.raises(psycopg.errors.InvalidParameterValue, match="positive amount"):
        apply_payment(conn, pmc.pmc_id, rent, pay_rent(conn, pmc), amount)


def test_a_charge_outside_the_pmc_or_an_unknown_transfer_is_refused(conn, pmc, lease):
    other = make_pmc(conn)
    rent = charge(conn, pmc.pmc_id, lease, JAN_1, RENT)
    payment = pay_rent(conn, pmc)

    with pytest.raises(psycopg.errors.InvalidParameterValue, match="no charge"):
        apply_payment(conn, other.pmc_id, rent, payment, RENT)
    with pytest.raises(psycopg.errors.InvalidParameterValue, match="no transfer"):
        apply_payment(conn, pmc.pmc_id, rent, "pglt_not_a_transfer", RENT)


def test_the_app_matches_only_through_the_function(conn, app_conn, pmc, lease):
    rent = charge(conn, pmc.pmc_id, lease, JAN_1, RENT)

    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        app_conn.execute(
            "INSERT INTO trust_charge_payments (pmc_id, charge_id, transfer_id, amount)"
            " VALUES (%s, %s, %s, %s)",
            (pmc.pmc_id, rent, pay_rent(conn, pmc), RENT),
        )


@pytest.mark.parametrize("isolation", ["REPEATABLE READ", "SERIALIZABLE"])
def test_payments_are_matched_at_read_committed(conn, database_url, pmc, lease, isolation):
    rent = charge(conn, pmc.pmc_id, lease, JAN_1, RENT)
    payment = pay_rent(conn, pmc)

    with psycopg.connect(database_url) as tx:
        tx.execute(f"SET TRANSACTION ISOLATION LEVEL {isolation}")
        with pytest.raises(psycopg.errors.InvalidTransactionState, match="READ COMMITTED"):
            apply_payment(tx, pmc.pmc_id, rent, payment, RENT)


@pytest.mark.timing
def test_two_payments_at_once_cannot_overpay_a_charge(conn, database_url, pmc, lease):
    rent = charge(conn, pmc.pmc_id, lease, JAN_1, RENT)
    first_payment, second_payment = pay_rent(conn, pmc), pay_rent(conn, pmc)

    with psycopg.connect(database_url) as first:
        apply_payment(first, pmc.pmc_id, rent, first_payment, RENT)  # not committed yet
        thread, pid, outcome = in_background(
            database_url,
            lambda worker: apply_payment(worker, pmc.pmc_id, rent, second_payment, RENT),
        )
        wait_until_blocked(conn, pid)
        first.commit()
    thread.join(timeout=30)

    assert isinstance(outcome.get("error"), psycopg.errors.CheckViolation), outcome
    assert matches(conn, pmc) == [(rent, first_payment, RENT)]
