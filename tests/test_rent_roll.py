"""trust_report_rent_roll: one line per unit as of the end of a day (UTC), then totals that tie
back to the trust ledger (migration 20261010000013).

The golden case under tests/golden/cases/ pins the full layout; these check the rules behind it.
"""

import uuid
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
    language_sorted_database,
    make_pmc,
    open_account,
    open_lease,
    reverse_payment,
    transfer_batch,
)

JAN_1 = date(2026, 1, 1)
JAN_31 = date(2026, 1, 31)
FEB_1 = date(2026, 2, 1)
RENT = Decimal("1500.00")
TOTALS = ["total: current leases", "not on a current lease", "total: all tenants"]


def roll(conn, pmc, as_of):
    return conn.execute(
        "SELECT line, item, property, unit, tenants, detail, monthly_rent, deposits, prepaid,"
        " charged, paid, balance_due FROM trust_report_rent_roll(%s, %s)",
        (pmc.pmc_id, as_of),
    ).fetchall()


def units(rows):
    """(unit, tenants, detail, rent, deposits, prepaid, charged, paid, due) per unit line."""
    return [tuple(row[3:]) for row in rows if row[1] == "unit"]


def totals(rows):
    """(detail, rent, deposits, prepaid, charged, paid, due) per summary line."""
    return {row[1]: (row[5], *row[6:]) for row in rows if row[1] in TOTALS}


def tenant_of(conn, pmc):
    return conn.execute(
        "SELECT tenant_id FROM trust_ledger_accounts WHERE ledger_account_id = %s",
        (pmc.tenant_deposit,),
    ).fetchone()[0]


def pay(conn, pmc, charge_id, amount, when, source=None):
    source = pmc.operating_cash if source is None else source
    transfer_id = transfer_batch(conn, [(source, pmc.owners[0].account, amount)], event_at=when)[0]
    apply_payment(conn, pmc.pmc_id, charge_id, transfer_id, amount)


def at(day, hour=12, minute=0):
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=UTC)


@pytest.mark.parametrize("mistake", ["no date", "unknown PMC"])
def test_a_missing_date_or_an_unknown_pmc_is_refused_not_shown_as_empty(conn, mistake):
    pmc = make_pmc(conn)
    pmc_id, as_of = (pmc.pmc_id, None) if mistake == "no date" else (uuid.uuid4(), JAN_31)

    with pytest.raises(psycopg.errors.InvalidParameterValue, match="as of a day|no PMC"):
        conn.execute("SELECT * FROM trust_report_rent_roll(%s, %s)", (pmc_id, as_of))


def test_a_pmc_with_no_units_gets_a_roll_of_zeros(conn):
    pmc = make_pmc(conn)

    rows = roll(conn, pmc, JAN_31)

    assert [(line, item, detail) for line, item, _, _, _, detail, *_ in rows[:2]] == [
        (
            1,
            "PMC",
            conn.execute(
                "SELECT display_name FROM trust_pmcs WHERE pmc_id = %s", (pmc.pmc_id,)
            ).fetchone()[0],
        ),
        (2, "as of", "2026-01-31, end of day UTC"),
    ]
    zero = Decimal("0.00")
    assert totals(rows) == {
        "total: current leases": ("0 of 0 units leased", zero, zero, zero, zero, zero, zero),
        "not on a current lease": (None, None, zero, zero, zero, zero, zero),
        "total: all tenants": (None, None, zero, zero, zero, zero, zero),
    }


def test_a_lease_is_on_the_roll_from_its_first_day_through_its_last(conn):
    pmc = make_pmc(conn)
    unit = add_unit(conn, pmc.pmc_id, pmc.owners[0].property_id)
    lease = open_lease(conn, pmc.pmc_id, unit, JAN_1, None, RENT, [tenant_of(conn, pmc)])

    def detail(as_of):
        (row,) = units(roll(conn, pmc, as_of))
        return row[2]

    assert detail(date(2025, 12, 31)) == "vacant"
    assert detail(JAN_1) == "2026-01-01, month to month"
    end_lease(conn, pmc.pmc_id, lease, JAN_31)
    assert detail(JAN_31) == "2026-01-01 to 2026-01-31"
    assert detail(FEB_1) == "vacant"


def test_a_vacant_unit_reads_vacant_with_its_amounts_left_blank(conn):
    pmc = make_pmc(conn)
    add_unit(conn, pmc.pmc_id, pmc.owners[0].property_id)

    rows = roll(conn, pmc, JAN_31)

    assert units(rows) == [("Unit 1", None, "vacant", None, None, None, None, None, None)]
    assert totals(rows)["total: current leases"][0] == "0 of 1 units leased"


def test_charges_count_by_due_date_and_payments_by_their_transfers_date(conn):
    pmc = make_pmc(conn)
    unit = add_unit(conn, pmc.pmc_id, pmc.owners[0].property_id)
    lease = open_lease(conn, pmc.pmc_id, unit, JAN_1, None, RENT, [tenant_of(conn, pmc)])
    january = charge(conn, pmc.pmc_id, lease, JAN_1, RENT)
    charge(conn, pmc.pmc_id, lease, date(2026, 1, 10), "50.00", "fee", "Late fee")
    charge(conn, pmc.pmc_id, lease, date(2026, 1, 15), "25.00", "credit", "Late fee waived")
    february = charge(conn, pmc.pmc_id, lease, FEB_1, RENT)
    pay(conn, pmc, january, "1000.00", at(date(2026, 1, 3)))
    pay(conn, pmc, january, "500.00", at(FEB_1, 0))  # dated the first moment of Feb 1
    pay(conn, pmc, february, "300.00", at(JAN_31, 23, 59))  # paid ahead, before it is due

    def money(as_of):
        (row,) = units(roll(conn, pmc, as_of))
        return row[6:]

    # (charged, paid, balance due)
    assert money(JAN_1) == (RENT, Decimal("0.00"), RENT)
    assert money(JAN_31) == (Decimal("1525.00"), Decimal("1300.00"), Decimal("225.00"))
    assert money(FEB_1) == (Decimal("3025.00"), Decimal("1800.00"), Decimal("1225.00"))


def test_a_bounced_payment_counts_as_unpaid_from_its_reversals_date(conn):
    pmc = make_pmc(conn)
    unit = add_unit(conn, pmc.pmc_id, pmc.owners[0].property_id)
    lease = open_lease(conn, pmc.pmc_id, unit, JAN_1, None, RENT, [tenant_of(conn, pmc)])
    rent = charge(conn, pmc.pmc_id, lease, JAN_1, RENT)
    owner = pmc.owners[0].account
    check = transfer_batch(conn, [(pmc.operating_cash, owner, RENT)], at(date(2026, 1, 3)))[0]
    apply_payment(conn, pmc.pmc_id, rent, check, RENT)
    returned = transfer_batch(conn, [(owner, pmc.operating_cash, RENT)], at(date(2026, 1, 8)))[0]
    reverse_payment(conn, pmc.pmc_id, rent, check, returned, RENT)

    def money(as_of):
        (row,) = units(roll(conn, pmc, as_of))
        return row[6:]

    # (charged, paid, balance due): paid on the 3rd, returned on the 8th.
    assert money(date(2026, 1, 7)) == (RENT, RENT, Decimal("0.00"))
    assert money(date(2026, 1, 8)) == (RENT, Decimal("0.00"), RENT)
    assert totals(roll(conn, pmc, JAN_31))["total: all tenants"][4:] == (
        RENT,
        Decimal("0.00"),
        RENT,
    )


def test_paid_ahead_reads_as_a_negative_balance(conn):
    pmc = make_pmc(conn)
    unit = add_unit(conn, pmc.pmc_id, pmc.owners[0].property_id)
    lease = open_lease(conn, pmc.pmc_id, unit, JAN_1, None, RENT, [tenant_of(conn, pmc)])
    february = charge(conn, pmc.pmc_id, lease, FEB_1, RENT)
    pay(conn, pmc, february, RENT, at(date(2026, 1, 28)))

    (row,) = units(roll(conn, pmc, JAN_31))

    assert row[6:] == (Decimal("0.00"), RENT, -RENT)


def test_co_tenants_hold_deposits_and_prepaid_rent_together(conn):
    pmc = make_pmc(conn)
    property_id = pmc.owners[0].property_id
    unit = add_unit(conn, pmc.pmc_id, property_id)
    first = tenant_of(conn, pmc)
    second = add_tenant(conn, pmc.pmc_id, property_id, "Tenant 2")
    second_deposit = open_account(
        conn, pmc.pmc_id, pmc.deposit_bank_id, "tenant_deposit", tenant_id=second
    )
    open_lease(conn, pmc.pmc_id, unit, JAN_1, None, RENT, [second, first])
    transfer_batch(
        conn,
        [
            (pmc.deposit_cash, pmc.tenant_deposit, "750.00"),
            (pmc.deposit_cash, second_deposit, "750.00"),
        ],
        event_at=at(date(2025, 12, 20)),
    )
    transfer_batch(conn, [(pmc.operating_cash, pmc.prepaid_rent, "200.00")], at(JAN_31, 23, 59))
    transfer_batch(conn, [(pmc.operating_cash, pmc.prepaid_rent, "99.00")], at(FEB_1, 0))

    (row,) = units(roll(conn, pmc, JAN_31))

    assert row[1] == "Tenant 1, Tenant 2"
    assert row[4:6] == (Decimal("1500.00"), Decimal("200.00"))


def test_money_for_tenants_off_the_roll_still_adds_up_to_the_ledger(conn):
    pmc = make_pmc(conn)
    property_id = pmc.owners[0].property_id
    unit = add_unit(conn, pmc.pmc_id, property_id)
    former = tenant_of(conn, pmc)  # moved out Jan 15, owes 200.00, deposit not yet returned
    current = add_tenant(conn, pmc.pmc_id, property_id, "Tenant 2")
    waiting = add_tenant(conn, pmc.pmc_id, property_id, "Tenant 3")  # on no lease yet
    waiting_prepaid = open_account(
        conn, pmc.pmc_id, pmc.operating_bank_id, "prepaid_rent", tenant_id=waiting
    )
    old = open_lease(conn, pmc.pmc_id, unit, date(2025, 1, 1), date(2026, 1, 15), RENT, [former])
    new = open_lease(conn, pmc.pmc_id, unit, date(2026, 1, 16), None, "1600.00", [current])
    charge(conn, pmc.pmc_id, old, JAN_1, "200.00", "fee", "Cleaning")
    charge(conn, pmc.pmc_id, new, date(2026, 1, 16), "1600.00")
    transfer_batch(conn, [(pmc.deposit_cash, pmc.tenant_deposit, RENT)], at(date(2025, 1, 1)))
    transfer_batch(conn, [(pmc.operating_cash, waiting_prepaid, "400.00")], at(date(2026, 1, 20)))

    rows = roll(conn, pmc, JAN_31)

    zero = Decimal("0.00")
    assert units(rows) == [
        (
            "Unit 1",
            "Tenant 2",
            "2026-01-16, month to month",
            Decimal("1600.00"),
            zero,
            zero,
            Decimal("1600.00"),
            zero,
            Decimal("1600.00"),
        ),
    ]
    assert totals(rows) == {
        "total: current leases": (
            "1 of 1 units leased",
            Decimal("1600.00"),
            zero,
            zero,
            Decimal("1600.00"),
            zero,
            Decimal("1600.00"),
        ),
        "not on a current lease": (
            None,
            None,
            RENT,
            Decimal("400.00"),
            Decimal("200.00"),
            zero,
            Decimal("200.00"),
        ),
        "total: all tenants": (
            None,
            None,
            RENT,
            Decimal("400.00"),
            Decimal("1800.00"),
            zero,
            Decimal("1800.00"),
        ),
    }
    held = conn.execute(
        "SELECT t.kind, sum(a.balance) FROM trust_ledger_accounts t"
        " JOIN pgledger_accounts a ON a.id = t.ledger_account_id"
        " WHERE t.pmc_id = %s AND t.kind IN ('tenant_deposit', 'prepaid_rent') GROUP BY t.kind",
        (pmc.pmc_id,),
    ).fetchall()
    assert dict(held) == {"tenant_deposit": RENT, "prepaid_rent": Decimal("400.00")}


def test_a_tenant_on_two_current_leases_is_counted_once_in_the_total(conn):
    # A home and a garage, both leased to Tenant 1: each line shows the deposit the tenant
    # holds, and the total counts it once.
    pmc = make_pmc(conn)
    property_id = pmc.owners[0].property_id
    tenant = tenant_of(conn, pmc)
    for name in ("Home", "Garage"):
        unit = add_unit(conn, pmc.pmc_id, property_id, name)
        open_lease(conn, pmc.pmc_id, unit, JAN_1, None, "100.00", [tenant])
    transfer_batch(conn, [(pmc.deposit_cash, pmc.tenant_deposit, "500.00")], at(JAN_1))

    rows = roll(conn, pmc, JAN_31)

    assert [row[4] for row in units(rows)] == [Decimal("500.00"), Decimal("500.00")]
    assert totals(rows)["total: current leases"][2] == Decimal("500.00")
    assert totals(rows)["total: all tenants"][2] == Decimal("500.00")


def test_the_roll_reads_the_same_in_any_time_zone(conn, database_url):
    pmc = make_pmc(conn)
    unit = add_unit(conn, pmc.pmc_id, pmc.owners[0].property_id)
    lease = open_lease(conn, pmc.pmc_id, unit, JAN_1, None, RENT, [tenant_of(conn, pmc)])
    rent = charge(conn, pmc.pmc_id, lease, JAN_1, RENT)
    pay(conn, pmc, rent, RENT, at(JAN_31, 23, 30))  # Feb 1 already in Auckland

    readings = []
    for zone in ("UTC", "America/Los_Angeles", "Pacific/Auckland"):
        with psycopg.connect(database_url, autocommit=True) as session:
            session.execute(f"SET TIME ZONE '{zone}'")
            readings.append(roll(session, pmc, JAN_31))

    assert readings[0] == readings[1] == readings[2]
    assert units(readings[0])[0][7] == RENT


def test_the_app_role_can_run_the_roll(conn, app_conn):
    pmc = make_pmc(conn)
    unit = add_unit(conn, pmc.pmc_id, pmc.owners[0].property_id)
    open_lease(conn, pmc.pmc_id, unit, JAN_1, None, RENT, [tenant_of(conn, pmc)])

    assert roll(app_conn, pmc, JAN_31) == roll(conn, pmc, JAN_31)


def test_units_and_tenants_list_in_the_same_order_on_any_server(database_url):
    # Language rules put "unit a" before "Unit b"; byte order puts "Unit b" first. The same
    # goes for properties and tenants. The roll must use byte order on every server.
    with language_sorted_database(database_url) as conn:
        pmc = make_pmc(conn)
        first, second = (owner.property_id for owner in pmc.owners)
        conn.execute(
            "UPDATE trust_properties SET display_name = 'property a' WHERE id = %s", (first,)
        )
        conn.execute(
            "UPDATE trust_properties SET display_name = 'Property b' WHERE id = %s", (second,)
        )
        for name in ("unit a", "Unit b"):
            add_unit(conn, pmc.pmc_id, first, name)
        garage = add_unit(conn, pmc.pmc_id, second, "Garage")
        tenants = [add_tenant(conn, pmc.pmc_id, second, name) for name in ("tenant a", "Tenant b")]
        open_lease(conn, pmc.pmc_id, garage, JAN_1, None, RENT, tenants)
        rows = roll(conn, pmc, JAN_31)

    assert [(row[2], row[3]) for row in rows if row[1] == "unit"] == [
        ("Property b", "Garage"),
        ("property a", "Unit b"),
        ("property a", "unit a"),
    ]
    assert units(rows)[0][1] == "Tenant b, tenant a"
