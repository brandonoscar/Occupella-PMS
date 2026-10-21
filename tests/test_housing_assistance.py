"""A housing authority's part of a lease's rent (migration 20261020000095, the founder's review
q034): as rent is charged for a month the lease's assistance is in force, the authority's part
becomes its own charge and the tenant is charged the rest, so the tenant's charges, late fees,
deposit and balance are the tenant's part alone (24 CFR 982.310(b)); the authority's payments pay
its charges into the owners' accounts and count as rent collected. Every name is synthetic."""

import uuid
from datetime import UTC, date, datetime
from decimal import Decimal

import psycopg
import pytest
from helpers import (
    acting_as,
    add_agreement,
    add_authority,
    add_late_fee_policy,
    add_staff,
    add_tenant,
    add_unit,
    apply_assistance_payment,
    apply_payment,
    assess_late_fees,
    assist,
    authority_charges,
    balance,
    bank_sums,
    charge_rent_due,
    complete_offer,
    end_lease,
    make_pmc,
    offer_lease,
    open_lease,
    post_management_fee,
    return_deposit,
    send_offer,
    sign_offer,
    transfer_batch,
)

SEP_1, OCT_1, OCT_15, OCT_16, NOV_1 = (
    date(2026, 9, 1),
    date(2026, 10, 1),
    date(2026, 10, 15),
    date(2026, 10, 16),
    date(2026, 11, 1),
)


def at(day, hour=12):
    return datetime(day.year, day.month, day.day, hour, tzinfo=UTC)


@pytest.fixture
def pmc(conn):
    return make_pmc(conn)


@pytest.fixture
def authority(conn, pmc):
    return add_authority(conn, pmc.pmc_id)


def lease_of(conn, pmc, starts=SEP_1, ends=None, rent="1000.00", tenant=None):
    tenant = tenant or add_tenant(conn, pmc.pmc_id, pmc.owners[0].property_id, "Tenant A")
    unit = add_unit(conn, pmc.pmc_id, pmc.owners[0].property_id)
    return open_lease(conn, pmc.pmc_id, unit, starts, ends, rent, [tenant])


def tenant_rent(conn, lease):
    return conn.execute(
        "SELECT due_on, amount, memo FROM trust_charges WHERE lease_id = %s AND kind = 'rent'"
        " ORDER BY due_on",
        (lease,),
    ).fetchall()


def base_charge(conn, lease, due_on=OCT_1):
    return conn.execute(
        "SELECT id FROM trust_assistance_charges WHERE lease_id = %s AND due_on = %s"
        " AND adjusts_id IS NULL",
        (lease, due_on),
    ).fetchone()[0]


def open_of(conn, charge_id):
    return conn.execute("SELECT trust_assistance_open(%s)", (charge_id,)).fetchone()[0]


# --- the split ---------------------------------------------------------------------------------


def test_rent_charged_in_an_assisted_month_splits_between_tenant_and_authority(
    conn, app_conn, pmc, authority
):
    lease = lease_of(conn, pmc)
    assist(conn, pmc.pmc_id, lease, authority, OCT_1, "800.00", contract="HAP-0001")

    # The rent run, as the app runs it.
    app_conn.execute("SELECT trust_run_rent(%s, %s, %s)", (pmc.pmc_id, OCT_1, OCT_1))

    assert tenant_rent(conn, lease) == [
        (OCT_1, Decimal("200.00"), "Rent due 2026-10-01, the tenant's part")
    ]
    assert authority_charges(conn, lease) == [(OCT_1, "charge", Decimal("800.00"), "rent")]
    assert conn.execute(
        "SELECT housing_authority_id, monthly_rent, monthly_assistance, memo"
        " FROM trust_assistance_charges WHERE lease_id = %s",
        (lease,),
    ).fetchone() == (authority, Decimal("1000.00"), Decimal("800.00"), "Rent due 2026-10-01")

    # Running again charges nothing new, the tenant's part or the authority's.
    app_conn.execute("SELECT trust_run_rent(%s, %s, %s)", (pmc.pmc_id, OCT_1, OCT_1))
    assert len(tenant_rent(conn, lease)) == 1
    assert len(authority_charges(conn, lease)) == 1


def test_a_part_month_splits_in_proportion(conn, pmc, authority):
    lease = lease_of(conn, pmc, starts=OCT_16)
    assist(conn, pmc.pmc_id, lease, authority, OCT_1, "800.00")

    charge_rent_due(conn, pmc.pmc_id, OCT_1)

    # 16 days of 31 of 1,000.00 is 516.13: the authority's 80% of it is 412.90, the tenant 103.23.
    assert [(d, a) for d, a, _ in tenant_rent(conn, lease)] == [(OCT_16, Decimal("103.23"))]
    assert authority_charges(conn, lease) == [(OCT_16, "charge", Decimal("412.90"), "rent")]


def test_a_part_too_small_for_a_cent_leaves_the_tenant_the_rent(conn, pmc, authority):
    # One day of 1,000.00 is 32.26; a cent a month of assistance is 0.0003 of it: no cent.
    lease = lease_of(conn, pmc, starts=date(2026, 10, 31))
    assist(conn, pmc.pmc_id, lease, authority, OCT_1, "0.01")

    charge_rent_due(conn, pmc.pmc_id, OCT_1)

    assert [(d, a) for d, a, _ in tenant_rent(conn, lease)] == [
        (date(2026, 10, 31), Decimal("32.26"))
    ]
    assert authority_charges(conn, lease) == []


def test_when_the_authority_pays_all_of_the_rent_the_tenant_is_charged_nothing(
    conn, pmc, authority
):
    lease = lease_of(conn, pmc)
    assist(conn, pmc.pmc_id, lease, authority, OCT_1, "1000.00")

    assert charge_rent_due(conn, pmc.pmc_id, OCT_1) == 0
    assert charge_rent_due(conn, pmc.pmc_id, OCT_1) == 0

    assert tenant_rent(conn, lease) == []
    assert authority_charges(conn, lease) == [(OCT_1, "charge", Decimal("1000.00"), "rent")]
    # The month is charged, the authority's part of it: no new due day starts in it.
    with pytest.raises(psycopg.errors.CheckViolation, match="charged already"):
        conn.execute(
            "INSERT INTO trust_rent_due_days (pmc_id, lease_id, starts_on, due_day)"
            " VALUES (%s, %s, %s, 5)",
            (pmc.pmc_id, lease, OCT_1),
        )


def test_assistance_from_a_later_month_takes_over_and_0_ends_it(conn, pmc, authority):
    lease = lease_of(conn, pmc)
    assist(conn, pmc.pmc_id, lease, authority, OCT_1, "800.00")
    assist(conn, pmc.pmc_id, lease, authority, NOV_1, "0")

    charge_rent_due(conn, pmc.pmc_id, SEP_1)
    charge_rent_due(conn, pmc.pmc_id, OCT_1)
    charge_rent_due(conn, pmc.pmc_id, NOV_1)

    assert [(d, a) for d, a, _ in tenant_rent(conn, lease)] == [
        (SEP_1, Decimal("1000.00")),
        (OCT_1, Decimal("200.00")),
        (NOV_1, Decimal("1000.00")),
    ]
    assert authority_charges(conn, lease) == [(OCT_1, "charge", Decimal("800.00"), "rent")]


def test_rent_lowered_below_the_assistance_is_the_authoritys_alone(conn, pmc, authority):
    lease = lease_of(conn, pmc)
    assist(conn, pmc.pmc_id, lease, authority, OCT_1, "800.00")
    conn.execute(
        "INSERT INTO trust_rent_changes (pmc_id, lease_id, effective_on, monthly_rent,"
        " notice_given_on, served_by) VALUES (%s, %s, %s, 700, %s, 'mail')",
        (pmc.pmc_id, lease, NOV_1, OCT_1),
    )

    charge_rent_due(conn, pmc.pmc_id, NOV_1)

    # The authority pays no more than the rent: all of the 700.00, the tenant nothing.
    assert tenant_rent(conn, lease) == []
    assert authority_charges(conn, lease) == [(NOV_1, "charge", Decimal("700.00"), "rent")]


def test_assistance_starts_on_the_first_of_a_month_of_the_lease_not_yet_charged(
    conn, pmc, authority
):
    lease = lease_of(conn, pmc, ends=date(2026, 12, 31))
    with pytest.raises(psycopg.errors.CheckViolation):  # not the first of a month
        assist(conn, pmc.pmc_id, lease, authority, OCT_15, "800.00")
    with pytest.raises(psycopg.errors.CheckViolation, match="in a month of the lease"):
        assist(conn, pmc.pmc_id, lease, authority, date(2026, 8, 1), "800.00")
    with pytest.raises(psycopg.errors.CheckViolation, match="in a month of the lease"):
        assist(conn, pmc.pmc_id, lease, authority, date(2027, 1, 1), "800.00")
    with pytest.raises(psycopg.errors.CheckViolation, match="no more than the rent"):
        assist(conn, pmc.pmc_id, lease, authority, OCT_1, "1000.01")
    with pytest.raises(psycopg.errors.CheckViolation):  # cents only
        assist(conn, pmc.pmc_id, lease, authority, OCT_1, "800.001")

    charge_rent_due(conn, pmc.pmc_id, OCT_1)
    with pytest.raises(psycopg.errors.CheckViolation, match="charged already"):
        assist(conn, pmc.pmc_id, lease, authority, OCT_1, "800.00")
    assist(conn, pmc.pmc_id, lease, authority, NOV_1, "800.00")


def test_an_authority_and_its_assistance_are_the_companys_own(conn, pmc, authority):
    elsewhere = make_pmc(conn)
    lease = lease_of(conn, pmc)
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        assist(conn, pmc.pmc_id, lease, add_authority(conn, elsewhere.pmc_id), OCT_1, "800.00")
    with pytest.raises(psycopg.errors.UniqueViolation):
        add_authority(conn, pmc.pmc_id, "SYNTHETIC housing authority")


# --- what is the tenant's ----------------------------------------------------------------------


def test_late_fees_count_only_the_tenants_part(conn, pmc, authority):
    lease = lease_of(conn, pmc)
    add_late_fee_policy(conn, pmc.pmc_id, pmc.owners[0].property_id, SEP_1, 5, percent="10")
    assist(conn, pmc.pmc_id, lease, authority, OCT_1, "800.00")
    charge_rent_due(conn, pmc.pmc_id, OCT_1)

    # The authority hasn't paid; the tenant owes 200.00 and is charged 10% of it, no more.
    assert assess_late_fees(conn, pmc.pmc_id, date(2026, 10, 10)) == 1
    assert conn.execute(
        "SELECT amount FROM trust_charges WHERE lease_id = %s AND kind = 'fee'", (lease,)
    ).fetchall() == [(Decimal("20.00"),)]


def test_a_tenant_who_paid_their_part_is_not_late_while_the_authority_owes(conn, pmc, authority):
    lease = lease_of(conn, pmc)
    add_late_fee_policy(conn, pmc.pmc_id, pmc.owners[0].property_id, SEP_1, 5, flat_fee="50")
    assist(conn, pmc.pmc_id, lease, authority, OCT_1, "800.00")
    charge_rent_due(conn, pmc.pmc_id, OCT_1)
    rent = conn.execute(
        "SELECT id FROM trust_charges WHERE lease_id = %s AND kind = 'rent'", (lease,)
    ).fetchone()[0]
    (paid,) = transfer_batch(
        conn, [(pmc.operating_cash, pmc.owners[0].account, "200.00")], at(OCT_1)
    )
    apply_payment(conn, pmc.pmc_id, rent, paid, "200.00")

    assert assess_late_fees(conn, pmc.pmc_id, date(2026, 10, 10)) == 0
    assert conn.execute("SELECT trust_lease_owed(%s)", (lease,)).fetchone()[0] == 0


def test_the_deposit_keeps_only_what_the_tenant_owes(conn, app_conn, pmc, authority):
    tenant = conn.execute(
        "SELECT tenant_id FROM trust_ledger_accounts WHERE ledger_account_id = %s",
        (pmc.tenant_deposit,),
    ).fetchone()[0]
    lease = lease_of(conn, pmc, ends=date(2026, 10, 31), tenant=tenant)
    transfer_batch(conn, [(pmc.deposit_cash, pmc.tenant_deposit, "1500.00")], at(SEP_1))
    assist(conn, pmc.pmc_id, lease, authority, OCT_1, "800.00")
    charge_rent_due(conn, pmc.pmc_id, OCT_1)

    # The authority's 800.00 stays the authority's to pay: the deposit keeps the tenant's 200.00.
    assert return_deposit(
        app_conn, pmc.pmc_id, lease, "move-out-1", at(date(2026, 11, 5)), pmc.owners[0].account
    ) == Decimal("1300.00")
    assert balance(conn, pmc.owners[0].account) == Decimal("200.00")
    assert open_of(conn, base_charge(conn, lease)) == Decimal("800.00")


# --- the authority's payments ------------------------------------------------------------------


def test_an_authority_payment_pays_its_charge_into_the_owners_account(
    conn, app_conn, pmc, authority
):
    lease = lease_of(conn, pmc)
    assist(conn, pmc.pmc_id, lease, authority, OCT_1, "800.00")
    charge_rent_due(conn, pmc.pmc_id, OCT_1)
    owed = base_charge(conn, lease)
    (paid,) = transfer_batch(
        conn, [(pmc.operating_cash, pmc.owners[0].account, "800.00")], at(date(2026, 10, 3))
    )

    assert apply_assistance_payment(app_conn, pmc.pmc_id, owed, paid, "500.00") == 500
    assert open_of(conn, owed) == Decimal("300.00")
    # The same match again returns it; a different amount under it is refused.
    assert apply_assistance_payment(app_conn, pmc.pmc_id, owed, paid, "500.00") == 500
    with pytest.raises(psycopg.errors.UniqueViolation, match="already pays"):
        apply_assistance_payment(app_conn, pmc.pmc_id, owed, paid, "300.00")
    assert open_of(conn, owed) == Decimal("300.00")
    # Never past what is open, nor past the payment.
    (more,) = transfer_batch(
        conn, [(pmc.operating_cash, pmc.owners[0].account, "900.00")], at(date(2026, 10, 3))
    )
    with pytest.raises(psycopg.errors.CheckViolation, match="more than is open"):
        apply_assistance_payment(app_conn, pmc.pmc_id, owed, more, "300.01")
    assert apply_assistance_payment(app_conn, pmc.pmc_id, owed, more, "300.00") == 300
    assert open_of(conn, owed) == 0
    # The rest of the payment pays November's part, no more than is left of it.
    charge_rent_due(conn, pmc.pmc_id, NOV_1)
    november = base_charge(conn, lease, NOV_1)
    with pytest.raises(psycopg.errors.CheckViolation, match="pays charges already"):
        apply_assistance_payment(app_conn, pmc.pmc_id, november, more, "600.01")
    assert apply_assistance_payment(app_conn, pmc.pmc_id, november, more, "600.00") == 600
    assert open_of(conn, november) == Decimal("200.00")
    assert set(bank_sums(conn, pmc.pmc_id).values()) == {Decimal(0)}


def test_an_authority_payment_comes_from_cash_into_the_propertys_owner(
    conn, app_conn, pmc, authority
):
    lease = lease_of(conn, pmc)
    assist(conn, pmc.pmc_id, lease, authority, OCT_1, "800.00")
    charge_rent_due(conn, pmc.pmc_id, OCT_1)
    owed = base_charge(conn, lease)
    (elsewhere,) = transfer_batch(
        conn, [(pmc.operating_cash, pmc.owners[1].account, "800.00")], at(OCT_1)
    )
    with pytest.raises(psycopg.errors.InvalidParameterValue, match="did not pay into"):
        apply_assistance_payment(app_conn, pmc.pmc_id, owed, elsewhere, "800.00")
    transfer_batch(conn, [(pmc.operating_cash, pmc.prepaid_rent, "800.00")], at(OCT_1))
    (from_prepaid,) = transfer_batch(
        conn, [(pmc.prepaid_rent, pmc.owners[0].account, "800.00")], at(OCT_1)
    )
    with pytest.raises(psycopg.errors.InvalidParameterValue, match="did not pay into"):
        apply_assistance_payment(app_conn, pmc.pmc_id, owed, from_prepaid, "800.00")


def test_an_authority_payment_names_a_charge_of_the_authority_and_a_transfer(
    conn, app_conn, pmc, authority
):
    lease = lease_of(conn, pmc)
    assist(conn, pmc.pmc_id, lease, authority, OCT_1, "800.00")
    charge_rent_due(conn, pmc.pmc_id, OCT_1)
    owed = base_charge(conn, lease)
    (paid,) = transfer_batch(
        conn, [(pmc.operating_cash, pmc.owners[0].account, "800.00")], at(date(2026, 10, 3))
    )
    end_lease(conn, pmc.pmc_id, lease, OCT_15)
    (adjustment,) = conn.execute(
        "SELECT id FROM trust_assistance_charges WHERE adjusts_id = %s", (owed,)
    ).fetchone()
    refused = psycopg.errors.InvalidParameterValue

    with pytest.raises(refused, match="positive amount"):
        apply_assistance_payment(app_conn, pmc.pmc_id, owed, paid, "0")
    with pytest.raises(refused, match="no housing authority charge"):
        apply_assistance_payment(app_conn, pmc.pmc_id, uuid.uuid4(), paid, "100.00")
    with pytest.raises(refused, match="no housing authority charge"):
        apply_assistance_payment(app_conn, make_pmc(conn).pmc_id, owed, paid, "100.00")
    with pytest.raises(refused, match="an adjustment is not paid"):
        apply_assistance_payment(app_conn, pmc.pmc_id, adjustment, paid, "100.00")
    with pytest.raises(refused, match="no transfer"):
        apply_assistance_payment(app_conn, pmc.pmc_id, owed, "no-such-transfer", "100.00")
    with app_conn.transaction():
        app_conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
        with pytest.raises(psycopg.errors.InvalidTransactionState, match="READ COMMITTED"):
            apply_assistance_payment(app_conn, pmc.pmc_id, owed, paid, "100.00")
        raise psycopg.Rollback()
    assert open_of(conn, owed) == Decimal("387.10")


def test_assistance_and_its_charges_name_a_lease_and_a_charge_that_exist(conn, pmc, authority):
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        assist(conn, pmc.pmc_id, uuid.uuid4(), authority, OCT_1, "800.00")
    lease = lease_of(conn, pmc)
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        conn.execute(
            "INSERT INTO trust_assistance_charges (pmc_id, lease_id, housing_authority_id,"
            " due_on, kind, amount, memo, source, adjusts_id)"
            " VALUES (%s, %s, %s, %s, 'credit', 1, 'By hand', 'staff', %s)",
            (pmc.pmc_id, lease, authority, OCT_1, uuid.uuid4()),
        )


def test_a_transfer_pays_a_tenants_charges_or_an_authoritys_never_both(
    conn, app_conn, pmc, authority
):
    lease = lease_of(conn, pmc)
    assist(conn, pmc.pmc_id, lease, authority, OCT_1, "800.00")
    charge_rent_due(conn, pmc.pmc_id, OCT_1)
    owed = base_charge(conn, lease)
    rent = conn.execute(
        "SELECT id FROM trust_charges WHERE lease_id = %s AND kind = 'rent'", (lease,)
    ).fetchone()[0]
    (paid,) = transfer_batch(
        conn, [(pmc.operating_cash, pmc.owners[0].account, "1000.00")], at(OCT_1)
    )
    apply_assistance_payment(app_conn, pmc.pmc_id, owed, paid, "800.00")
    with pytest.raises(psycopg.errors.InvalidParameterValue, match="housing authority's payment"):
        apply_payment(conn, pmc.pmc_id, rent, paid, "200.00")

    (tenants,) = transfer_batch(
        conn, [(pmc.operating_cash, pmc.owners[0].account, "200.00")], at(OCT_1)
    )
    apply_payment(conn, pmc.pmc_id, rent, tenants, "200.00")
    with pytest.raises(psycopg.errors.InvalidParameterValue, match="tenant's payment"):
        apply_assistance_payment(app_conn, pmc.pmc_id, owed, tenants, "0.01")


# --- staff's charges and credits, and a lease's end --------------------------------------------


def test_staff_charge_the_authority_and_credit_no_more_than_is_open(conn, app_conn, pmc, authority):
    lease = lease_of(conn, pmc)
    staff = add_staff(conn, pmc.pmc_id)

    def enter(kind, amount, adjusts=None, due_on=OCT_1):
        with acting_as(app_conn, staff):
            return app_conn.execute(
                "INSERT INTO trust_assistance_charges (pmc_id, lease_id, housing_authority_id,"
                " due_on, kind, amount, memo, source, adjusts_id)"
                " VALUES (%s, %s, %s, %s, %s, %s, 'By hand', 'staff', %s) RETURNING id",
                (pmc.pmc_id, lease, authority, due_on, kind, Decimal(amount), adjusts),
            ).fetchone()[0]

    owed = enter("charge", "450.00")
    assert conn.execute(
        "SELECT recorded_by FROM trust_assistance_charges WHERE id = %s", (owed,)
    ).fetchone() == (staff,)
    with pytest.raises(psycopg.errors.CheckViolation, match="more than is open"):
        enter("credit", "450.01", owed)
    credit = enter("credit", "50.00", owed)
    assert open_of(conn, owed) == Decimal("400.00")
    with pytest.raises(psycopg.errors.CheckViolation, match="not another adjustment"):
        enter("credit", "1.00", credit)
    with pytest.raises(psycopg.errors.CheckViolation):  # a credit names what it takes off
        enter("credit", "1.00")
    other = lease_of(conn, pmc)
    with (
        pytest.raises(psycopg.errors.CheckViolation, match="same lease and authority"),
        acting_as(app_conn, staff),
    ):
        app_conn.execute(
            "INSERT INTO trust_assistance_charges (pmc_id, lease_id, housing_authority_id,"
            " due_on, kind, amount, memo, source, adjusts_id)"
            " VALUES (%s, %s, %s, %s, 'credit', 1, 'By hand', 'staff', %s)",
            (pmc.pmc_id, other, authority, OCT_1, owed),
        )


def test_a_lease_of_another_company_is_not_ended(conn, pmc):
    lease = lease_of(conn, pmc)
    with pytest.raises(psycopg.errors.InvalidParameterValue, match="no lease"):
        end_lease(conn, make_pmc(conn).pmc_id, lease, OCT_15)


def test_a_lease_end_moves_both_parts_to_its_days(conn, pmc, authority):
    lease = lease_of(conn, pmc)
    assist(conn, pmc.pmc_id, lease, authority, OCT_1, "800.00")
    charge_rent_due(conn, pmc.pmc_id, OCT_1)

    end_lease(conn, pmc.pmc_id, lease, OCT_15)

    # 15 days of 31: 483.87, the authority's part 387.10, the tenant's 96.77.
    rent_charges = conn.execute(
        "SELECT kind, amount FROM trust_charges WHERE lease_id = %s ORDER BY created_at", (lease,)
    ).fetchall()
    assert rent_charges == [("rent", Decimal("200.00")), ("credit", Decimal("103.23"))]
    assert authority_charges(conn, lease) == [
        (OCT_1, "charge", Decimal("800.00"), "rent"),
        (OCT_1, "credit", Decimal("412.90"), "lease end"),
    ]
    assert conn.execute("SELECT trust_lease_owed(%s)", (lease,)).fetchone()[0] == Decimal("96.77")
    assert open_of(conn, base_charge(conn, lease)) == Decimal("387.10")

    # The end cleared: the days back, both parts.
    end_lease(conn, pmc.pmc_id, lease, None)
    assert conn.execute("SELECT trust_lease_owed(%s)", (lease,)).fetchone()[0] == Decimal("200.00")
    assert open_of(conn, base_charge(conn, lease)) == Decimal("800.00")


# --- renewals ---------------------------------------------------------------------------------


def renew(conn, pmc, lease, starts_on, rent):
    staff = add_staff(conn, pmc.pmc_id, "Leasing 1")
    unit = conn.execute("SELECT unit_id FROM trust_leases WHERE id = %s", (lease,)).fetchone()[0]
    offer = offer_lease(conn, pmc.pmc_id, unit, staff, starts_on, rent=rent, renews_lease_id=lease)
    send_offer(conn, pmc.pmc_id, offer, staff)
    sign_offer(conn, pmc.pmc_id, offer, 1)
    return complete_offer(conn, pmc.pmc_id, offer, staff)


def assistance_of(conn, lease):
    return conn.execute(
        "SELECT housing_authority_id, starts_on, amount, contract FROM trust_rent_assistance"
        " WHERE lease_id = %s ORDER BY starts_on",
        (lease,),
    ).fetchall()


def test_a_renewal_keeps_the_assistance_and_splits_its_part_month(conn, pmc, authority):
    lease = lease_of(conn, pmc)
    assist(conn, pmc.pmc_id, lease, authority, OCT_1, "800.00", contract="HAP-0001")
    charge_rent_due(conn, pmc.pmc_id, OCT_1)

    # Signed after October was charged: the old lease's days after the 15th come off both parts,
    # and the renewal is charged its 16 days at 1,100.00 (567.74), split as the old lease's was.
    renewed = renew(conn, pmc, lease, OCT_16, "1100.00")

    assert assistance_of(conn, renewed) == [(authority, OCT_1, Decimal("800.00"), "HAP-0001")]
    assert [(d, a) for d, a, _ in tenant_rent(conn, renewed)] == [(OCT_16, Decimal("154.84"))]
    assert authority_charges(conn, renewed) == [(OCT_16, "charge", Decimal("412.90"), "rent")]
    assert authority_charges(conn, lease) == [
        (OCT_1, "charge", Decimal("800.00"), "rent"),
        (OCT_1, "credit", Decimal("412.90"), "lease end"),
    ]

    # Each month after: the authority's 800.00 of the new rent, the tenant the rest.
    charge_rent_due(conn, pmc.pmc_id, NOV_1)
    assert [(d, a) for d, a, _ in tenant_rent(conn, renewed)][-1] == (NOV_1, Decimal("300.00"))
    assert authority_charges(conn, renewed)[-1] == (NOV_1, "charge", Decimal("800.00"), "rent")


def test_a_renewal_at_less_rent_than_the_assistance_keeps_it_to_the_rent(conn, pmc, authority):
    lease = lease_of(conn, pmc, ends=date(2026, 10, 31))
    assist(conn, pmc.pmc_id, lease, authority, OCT_1, "800.00")

    renewed = renew(conn, pmc, lease, NOV_1, "750.00")
    charge_rent_due(conn, pmc.pmc_id, NOV_1)

    assert assistance_of(conn, renewed) == [(authority, NOV_1, Decimal("750.00"), None)]
    assert tenant_rent(conn, renewed) == []
    assert authority_charges(conn, renewed) == [(NOV_1, "charge", Decimal("750.00"), "rent")]


def test_a_renewal_after_the_assistance_ended_has_none(conn, pmc, authority):
    lease = lease_of(conn, pmc)
    assist(conn, pmc.pmc_id, lease, authority, OCT_1, "800.00")
    assist(conn, pmc.pmc_id, lease, authority, NOV_1, "0")

    renewed = renew(conn, pmc, lease, NOV_1, "1000.00")

    assert assistance_of(conn, renewed) == []


# --- rent collected ----------------------------------------------------------------------------


def test_the_authoritys_rent_counts_as_rent_collected(conn, app_conn, pmc, authority):
    lease = lease_of(conn, pmc)
    account = pmc.owners[0].account
    add_agreement(conn, pmc.pmc_id, account, SEP_1, fee_percent="10")
    assist(conn, pmc.pmc_id, lease, authority, OCT_1, "800.00")
    charge_rent_due(conn, pmc.pmc_id, OCT_1)
    rent = conn.execute(
        "SELECT id FROM trust_charges WHERE lease_id = %s AND kind = 'rent'", (lease,)
    ).fetchone()[0]
    tenants, authoritys = transfer_batch(
        conn,
        [(pmc.operating_cash, account, "200.00"), (pmc.operating_cash, account, "800.00")],
        at(date(2026, 10, 3)),
    )
    apply_payment(conn, pmc.pmc_id, rent, tenants, "200.00")
    apply_assistance_payment(app_conn, pmc.pmc_id, base_charge(conn, lease), authoritys, "800.00")

    # 10% of 1,000.00 collected.
    assert post_management_fee(
        conn, pmc.pmc_id, account, OCT_1, NOV_1, at(date(2026, 11, 2))
    ) == Decimal("100.00")
    owner = pmc.owners[0].owner_id
    statement = dict(
        conn.execute(
            "SELECT item, amount FROM trust_report_income_statement(%s, %s, %s, %s)"
            " WHERE property IS NOT NULL",
            (pmc.pmc_id, owner, OCT_1, date(2026, 10, 31)),
        ).fetchall()
    )
    assert statement["income: rent"] == Decimal("1000.00")
    year_end = conn.execute(
        "SELECT item, amount FROM trust_report_owner_year_end(%s, %s, 2026)"
        " WHERE property IS NOT NULL",
        (pmc.pmc_id, owner),
    ).fetchall()
    assert ("collected from tenants", Decimal("1000.00")) in year_end


# --- records -----------------------------------------------------------------------------------


def test_assistance_is_append_only_audited_and_names_who_recorded_it(
    conn, app_conn, pmc, authority
):
    lease = lease_of(conn, pmc)
    staff = add_staff(conn, pmc.pmc_id)
    with acting_as(app_conn, staff):
        aid = app_conn.execute(
            "INSERT INTO trust_rent_assistance"
            " (pmc_id, lease_id, housing_authority_id, starts_on, amount)"
            " VALUES (%s, %s, %s, %s, 800) RETURNING id",
            (pmc.pmc_id, lease, authority, OCT_1),
        ).fetchone()[0]
    assert conn.execute(
        "SELECT recorded_by FROM trust_rent_assistance WHERE id = %s", (aid,)
    ).fetchone() == (staff,)
    for table in (
        "trust_housing_authorities",
        "trust_rent_assistance",
        "trust_assistance_charges",
        "trust_assistance_payments",
    ):
        with pytest.raises(psycopg.errors.RestrictViolation, match="append-only"):
            conn.execute(f"DELETE FROM {table}")  # noqa: S608
    assert conn.execute(
        "SELECT count(*) FROM trust_audit_log WHERE table_name = 'trust_rent_assistance'"
        " AND row_data ->> 'id' = %s",
        (str(aid),),
    ).fetchone() == (1,)


def test_the_app_records_assistance_and_the_ai_only_reads_it(
    conn, app_conn, ai_conn, pmc, authority
):
    lease = lease_of(conn, pmc)
    assist(app_conn, pmc.pmc_id, lease, authority, OCT_1, "800.00")
    assert ai_conn.execute(
        "SELECT amount FROM trust_rent_assistance WHERE lease_id = %s", (lease,)
    ).fetchone() == (Decimal("800.00"),)
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        add_authority(ai_conn, pmc.pmc_id, "Another authority")
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        app_conn.execute(
            "INSERT INTO trust_assistance_payments (transfer_id, assistance_charge_id, pmc_id,"
            " amount) VALUES ('x', gen_random_uuid(), %s, 1)",
            (pmc.pmc_id,),
        )
