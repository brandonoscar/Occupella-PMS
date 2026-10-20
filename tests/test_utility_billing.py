"""Utility billing, RUBS and submeters (migration 20261020000096, the founder's review q021): a
property's utility bill for a period shared among the leases in effect in it by the plan in force
on its first day (by unit, square feet, occupants or submeter, less the owner's share), each
billed lease's share rounded down to the cent and charged to it as a fee. A vacant unit's days, a
lease not billed for the utility, the common areas and every part of a cent stay with the owner.
Every name is synthetic."""

import uuid
from datetime import date
from decimal import Decimal

import psycopg
import pytest
from helpers import (
    acting_as,
    add_staff,
    add_tenant,
    add_unit,
    bill_back_utility,
    bill_lease_for,
    enter_bill,
    make_pmc,
    open_lease,
    plan_utility,
    set_area,
    utility_shares,
)

SEP_1, SEP_16, SEP_30, OCT_1, OCT_31 = (
    date(2026, 9, 1),
    date(2026, 9, 16),
    date(2026, 9, 30),
    date(2026, 10, 1),
    date(2026, 10, 31),
)


@pytest.fixture
def pmc(conn):
    return make_pmc(conn)


@pytest.fixture
def prop(pmc):
    return pmc.owners[0].property_id


def lease_at(conn, pmc, unit, starts=SEP_1, ends=None, people=1, billed=("water",)):
    tenants = [
        add_tenant(conn, pmc.pmc_id, pmc.owners[0].property_id, f"Tenant {n + 1}")
        for n in range(people)
    ]
    lease = open_lease(conn, pmc.pmc_id, unit, starts, ends, "1000.00", tenants)
    for utility in billed:
        bill_lease_for(conn, pmc.pmc_id, lease, utility, starts)
    return lease


def units(conn, pmc, count):
    return [
        add_unit(conn, pmc.pmc_id, pmc.owners[0].property_id, f"Unit {n + 1}") for n in range(count)
    ]


def fees(conn, lease):
    return conn.execute(
        "SELECT due_on, kind, amount, memo FROM trust_charges WHERE lease_id = %s ORDER BY id",
        (lease,),
    ).fetchall()


# --- the methods -------------------------------------------------------------------------------


def test_by_unit_a_vacant_units_days_stay_with_the_owner(conn, pmc, prop):
    a, b, c, _vacant = units(conn, pmc, 4)
    first, second = lease_at(conn, pmc, a), lease_at(conn, pmc, b)
    late = lease_at(conn, pmc, c, starts=SEP_16)
    plan_utility(conn, pmc.pmc_id, prop, "water", SEP_1, "units")

    bill = bill_back_utility(conn, pmc.pmc_id, prop, "water", SEP_1, SEP_30, "300.00", OCT_1)

    # Four units for 30 days: 120 unit-days. Units 1 and 2 had 30 each, Unit 3 15 (from the
    # 16th); the vacant unit's 30 and Unit 3's first 15 stay with the owner (112.50).
    assert utility_shares(conn, bill) == {
        first: (True, Decimal(30), Decimal("75.00")),
        second: (True, Decimal(30), Decimal("75.00")),
        late: (True, Decimal(15), Decimal("37.50")),
    }
    assert fees(conn, first) == [
        (OCT_1, "fee", Decimal("75.00"), "Water 2026-09-01 to 2026-09-30, by unit")
    ]
    assert conn.execute("SELECT trust_lease_owed(%s)", (late,)).fetchone()[0] == Decimal("37.50")


def test_the_owners_share_and_each_part_of_a_cent_stay_with_the_owner(conn, pmc, prop):
    leases = [lease_at(conn, pmc, unit, billed=("sewer",)) for unit in units(conn, pmc, 3)]
    plan_utility(conn, pmc.pmc_id, prop, "sewer", SEP_1, "units", owner_percent="10")

    bill = bill_back_utility(conn, pmc.pmc_id, prop, "sewer", SEP_1, SEP_30, "100.01", OCT_1)

    # The tenants' pool is 90% of 100.01, 90.009; each lease's third is 30.003: 30.00 each.
    shares = utility_shares(conn, bill)
    assert {lease: shares[lease][2] for lease in leases} == dict.fromkeys(leases, Decimal("30.00"))
    assert [fee[3] for fee in fees(conn, leases[0])] == ["Sewer 2026-09-01 to 2026-09-30, by unit"]
    assert conn.execute(
        "SELECT method, owner_percent FROM trust_utility_bills WHERE id = %s", (bill,)
    ).fetchone() == ("units", Decimal(10))


def test_a_share_is_rounded_down_never_up(conn, pmc, prop):
    leases = [lease_at(conn, pmc, unit) for unit in units(conn, pmc, 3)]
    plan_utility(conn, pmc.pmc_id, prop, "water", SEP_1, "units")

    bill = bill_back_utility(conn, pmc.pmc_id, prop, "water", SEP_1, SEP_30, "200.00", OCT_1)

    # A third of 200.00 is 66.666...: 66.66 each, and the 0.02 left stays with the owner.
    shares = utility_shares(conn, bill)
    assert [shares[lease][2] for lease in leases] == [Decimal("66.66")] * 3


def test_by_square_feet_each_unit_pays_for_its_size(conn, pmc, prop):
    small, large, vacant = units(conn, pmc, 3)
    for unit, size in ((small, 600), (large, 1000), (vacant, 400)):
        set_area(conn, pmc.pmc_id, unit, size)
    one, two = lease_at(conn, pmc, small), lease_at(conn, pmc, large)
    plan_utility(conn, pmc.pmc_id, prop, "gas", SEP_1, "square_feet")
    for lease in (one, two):
        bill_lease_for(conn, pmc.pmc_id, lease, "gas", SEP_1)

    bill = bill_back_utility(conn, pmc.pmc_id, prop, "gas", SEP_1, SEP_30, "200.00", OCT_1)

    # 2,000 square feet: 600 pays 60.00, 1,000 pays 100.00, the vacant 400's 40.00 stays.
    shares = utility_shares(conn, bill)
    assert (shares[one][2], shares[two][2]) == (Decimal("60.00"), Decimal("100.00"))
    assert fees(conn, two)[-1][3] == "Gas 2026-09-01 to 2026-09-30, by square feet"


def test_by_square_feet_every_unit_needs_its_size(conn, pmc, prop):
    sized, unsized = units(conn, pmc, 2)
    set_area(conn, pmc.pmc_id, sized, 600)
    set_area(conn, pmc.pmc_id, sized, 650)  # the latest holds
    lease_at(conn, pmc, sized)
    plan_utility(conn, pmc.pmc_id, prop, "water", SEP_1, "square_feet")

    with pytest.raises(psycopg.errors.CheckViolation, match="Unit 2 has no square feet"):
        bill_back_utility(conn, pmc.pmc_id, prop, "water", SEP_1, SEP_30, "100.00", OCT_1)
    set_area(conn, pmc.pmc_id, unsized, 350)
    bill = bill_back_utility(conn, pmc.pmc_id, prop, "water", SEP_1, SEP_30, "100.00", OCT_1)
    assert [share[2] for share in utility_shares(conn, bill).values()] == [Decimal("65.00")]


def test_by_occupants_a_lease_not_billed_keeps_its_share_with_the_owner(conn, pmc, prop):
    a, b, c = units(conn, pmc, 3)
    pair = lease_at(conn, pmc, a, people=2)
    family = lease_at(conn, pmc, b)
    conn.execute(
        "INSERT INTO trust_lease_occupants (pmc_id, lease_id, display_name)"
        " VALUES (%s, %s, 'Occupant 1')",
        (pmc.pmc_id, family),
    )
    single = lease_at(conn, pmc, c, billed=())
    plan_utility(conn, pmc.pmc_id, prop, "water", SEP_1, "occupants")

    bill = bill_back_utility(conn, pmc.pmc_id, prop, "water", SEP_1, SEP_30, "250.00", OCT_1)

    # 2 + 2 + 1 people for 30 days: 150 person-days. The single tenant's lease isn't billed
    # for water: its 50.00 stays with the owner, not shifted to the others.
    assert utility_shares(conn, bill) == {
        pair: (True, Decimal(60), Decimal("100.00")),
        family: (True, Decimal(60), Decimal("100.00")),
        single: (False, Decimal(30), Decimal("0")),
    }
    assert fees(conn, single) == []


def test_by_occupants_people_who_left_or_were_taken_off_dont_count(conn, pmc, prop):
    a, b = units(conn, pmc, 2)
    pair = lease_at(conn, pmc, a, people=2)
    other = lease_at(conn, pmc, b)
    leaving = conn.execute(
        "SELECT tenant_id FROM trust_lease_tenants WHERE lease_id = %s ORDER BY tenant_id LIMIT 1",
        (pair,),
    ).fetchone()[0]
    conn.execute(
        "SELECT trust_remove_lease_tenant(%s, %s, %s, %s, NULL)",
        (pmc.pmc_id, pair, leaving, date(2026, 9, 10)),
    )
    occupant = conn.execute(
        "INSERT INTO trust_lease_occupants (pmc_id, lease_id, display_name)"
        " VALUES (%s, %s, 'Occupant 1') RETURNING id",
        (pmc.pmc_id, other),
    ).fetchone()[0]
    conn.execute(
        "INSERT INTO trust_lease_household_removals (pmc_id, occupant_id) VALUES (%s, %s)",
        (pmc.pmc_id, occupant),
    )
    plan_utility(conn, pmc.pmc_id, prop, "water", SEP_1, "occupants")

    bill = bill_back_utility(conn, pmc.pmc_id, prop, "water", SEP_1, SEP_30, "100.00", OCT_1)

    # One person on each lease at the period's end: 50.00 each.
    shares = utility_shares(conn, bill)
    assert (shares[pair][2], shares[other][2]) == (Decimal("50.00"), Decimal("50.00"))


def test_by_submeter_each_lease_pays_its_reading_of_the_master_meter(conn, pmc, prop):
    a, b = units(conn, pmc, 2)
    one, two = lease_at(conn, pmc, a), lease_at(conn, pmc, b)
    plan_utility(conn, pmc.pmc_id, prop, "water", SEP_1, "submeter")

    bill = bill_back_utility(
        conn,
        pmc.pmc_id,
        prop,
        "water",
        SEP_1,
        SEP_30,
        "500.00",
        OCT_1,
        master_usage="100",
        usage={one: "30", two: "50"},
    )

    # 30 and 50 of the master's 100: 150.00 and 250.00; the 20 the common areas used stays.
    shares = utility_shares(conn, bill)
    assert (shares[one][2], shares[two][2]) == (Decimal("150.00"), Decimal("250.00"))
    assert conn.execute(
        "SELECT master_usage FROM trust_utility_bills WHERE id = %s", (bill,)
    ).fetchone()[0] == Decimal(100)
    assert fees(conn, one)[-1][3] == "Water 2026-09-01 to 2026-09-30, by meter"


@pytest.mark.parametrize(
    ("master", "usage", "refused", "said"),
    [
        ("100", {"one": "60", "two": "50"}, psycopg.errors.CheckViolation, "more than the master"),
        ("100", {"one": "-1"}, psycopg.errors.InvalidParameterValue, "0 or more"),
        (
            "100",
            {"stranger": "1"},
            psycopg.errors.InvalidParameterValue,
            "no lease of the property",
        ),
        (None, {"one": "1"}, psycopg.errors.InvalidParameterValue, "master meter"),
    ],
)
def test_a_submetered_bill_reads_the_master_meter_and_the_units_meters(
    conn, pmc, prop, master, usage, refused, said
):
    a, b = units(conn, pmc, 2)
    leases = {
        "one": lease_at(conn, pmc, a),
        "two": lease_at(conn, pmc, b),
        "stranger": uuid.uuid4(),
    }
    plan_utility(conn, pmc.pmc_id, prop, "water", SEP_1, "submeter")

    with pytest.raises(refused, match=said):
        bill_back_utility(
            conn,
            pmc.pmc_id,
            prop,
            "water",
            SEP_1,
            SEP_30,
            "500.00",
            OCT_1,
            master_usage=master,
            usage={leases[k]: v for k, v in usage.items()},
        )


def test_a_master_reading_is_only_for_a_submetered_utility(conn, pmc, prop):
    lease_at(conn, pmc, units(conn, pmc, 1)[0])
    plan_utility(conn, pmc.pmc_id, prop, "water", SEP_1, "units")
    with pytest.raises(psycopg.errors.InvalidParameterValue, match="master meter"):
        bill_back_utility(
            conn, pmc.pmc_id, prop, "water", SEP_1, SEP_30, "50.00", OCT_1, master_usage="10"
        )


# --- plans, leases and bills -------------------------------------------------------------------


def test_the_plan_and_the_lease_in_force_on_each_day_decide(conn, pmc, prop):
    a, b = units(conn, pmc, 2)
    one, two = lease_at(conn, pmc, a), lease_at(conn, pmc, b, people=3)
    plan_utility(conn, pmc.pmc_id, prop, "water", SEP_1, "units")
    plan_utility(conn, pmc.pmc_id, prop, "water", OCT_1, "occupants")
    bill_lease_for(conn, pmc.pmc_id, one, "water", OCT_1, billed=False)

    september = bill_back_utility(conn, pmc.pmc_id, prop, "water", SEP_1, SEP_30, "100.00", OCT_1)
    october = bill_back_utility(
        conn, pmc.pmc_id, prop, "water", OCT_1, OCT_31, "100.00", date(2026, 11, 1)
    )

    # September by unit, both billed; October by occupants (1 and 3), the first not billed.
    assert {k: v[2] for k, v in utility_shares(conn, september).items()} == {
        one: Decimal("50.00"),
        two: Decimal("50.00"),
    }
    assert {k: v[2] for k, v in utility_shares(conn, october).items()} == {
        one: Decimal("0"),
        two: Decimal("75.00"),
    }
    assert (
        conn.execute("SELECT method FROM trust_utility_bills WHERE id = %s", (october,)).fetchone()[
            0
        ]
        == "occupants"
    )


@pytest.mark.parametrize("method", [None, "none"])
def test_a_utility_the_property_doesnt_bill_back_is_refused(conn, pmc, prop, method):
    lease_at(conn, pmc, units(conn, pmc, 1)[0])
    if method:
        plan_utility(conn, pmc.pmc_id, prop, "water", SEP_1, "units")
        plan_utility(conn, pmc.pmc_id, prop, "water", SEP_16, method)
    with pytest.raises(psycopg.errors.CheckViolation, match="bills no water back"):
        bill_back_utility(conn, pmc.pmc_id, prop, "water", SEP_16, SEP_30, "50.00", OCT_1)


def test_no_two_bills_cover_a_day(conn, pmc, prop):
    lease_at(conn, pmc, units(conn, pmc, 1)[0])
    plan_utility(conn, pmc.pmc_id, prop, "water", SEP_1, "units")
    bill_back_utility(conn, pmc.pmc_id, prop, "water", SEP_1, SEP_30, "50.00", OCT_1)

    with pytest.raises(psycopg.errors.UniqueViolation, match="already covers"):
        bill_back_utility(conn, pmc.pmc_id, prop, "water", SEP_30, OCT_31, "50.00", OCT_1)
    # Another utility, or the next period, is a bill of its own.
    plan_utility(conn, pmc.pmc_id, prop, "trash", SEP_1, "units")
    bill_back_utility(conn, pmc.pmc_id, prop, "trash", SEP_1, SEP_30, "20.00", OCT_1)
    bill_back_utility(conn, pmc.pmc_id, prop, "water", OCT_1, OCT_31, "50.00", OCT_1)


def test_a_bill_names_a_vendors_bill_of_the_property(conn, pmc, prop):
    lease_at(conn, pmc, units(conn, pmc, 1)[0])
    plan_utility(conn, pmc.pmc_id, prop, "water", SEP_1, "units")
    elsewhere = enter_bill(
        conn, pmc.pmc_id, pmc.vendor_id, pmc.owners[1].account, "W-1", SEP_30, OCT_31, "50.00"
    )
    with pytest.raises(psycopg.errors.InvalidParameterValue, match="no bill of the property"):
        bill_back_utility(
            conn, pmc.pmc_id, prop, "water", SEP_1, SEP_30, "50.00", OCT_1, bill_id=elsewhere
        )
    ours = enter_bill(
        conn, pmc.pmc_id, pmc.vendor_id, pmc.owners[0].account, "W-2", SEP_30, OCT_31, "50.00"
    )
    bill = bill_back_utility(
        conn, pmc.pmc_id, prop, "water", SEP_1, SEP_30, "50.00", OCT_1, "Meter 7", bill_id=ours
    )
    assert conn.execute(
        "SELECT bill_id, memo FROM trust_utility_bills WHERE id = %s", (bill,)
    ).fetchone() == (ours, "Meter 7")


@pytest.mark.parametrize(
    ("start", "end", "amount", "said"),
    [
        (SEP_30, SEP_1, "50.00", "a period of days"),
        (SEP_1, SEP_30, "0", "an amount above 0"),
    ],
)
def test_a_bill_is_for_a_period_and_an_amount(conn, pmc, prop, start, end, amount, said):
    plan_utility(conn, pmc.pmc_id, prop, "water", SEP_1, "units")
    with pytest.raises(psycopg.errors.InvalidParameterValue, match=said):
        bill_back_utility(conn, pmc.pmc_id, prop, "water", start, end, amount, OCT_1)


def test_a_property_of_another_company_is_not_billed(conn, pmc, prop):
    plan_utility(conn, pmc.pmc_id, prop, "water", SEP_1, "units")
    with pytest.raises(psycopg.errors.InvalidParameterValue, match="no property"):
        bill_back_utility(conn, make_pmc(conn).pmc_id, prop, "water", SEP_1, SEP_30, "5", OCT_1)


def test_a_property_with_no_one_to_bill_charges_nothing(conn, pmc, prop):
    plan_utility(conn, pmc.pmc_id, prop, "water", SEP_1, "occupants")
    bill = bill_back_utility(conn, pmc.pmc_id, prop, "water", SEP_1, SEP_30, "50.00", OCT_1)
    assert utility_shares(conn, bill) == {}


# --- records -----------------------------------------------------------------------------------


def test_utility_billing_is_append_only_audited_and_names_who_recorded_it(conn, pmc, prop):
    staff = add_staff(conn, pmc.pmc_id)
    lease_at(conn, pmc, units(conn, pmc, 1)[0])
    with acting_as(conn, staff):
        plan_utility(conn, pmc.pmc_id, prop, "water", SEP_1, "units")
        bill = bill_back_utility(conn, pmc.pmc_id, prop, "water", SEP_1, SEP_30, "50.00", OCT_1)
    assert (
        conn.execute(
            "SELECT recorded_by FROM trust_utility_bills WHERE id = %s", (bill,)
        ).fetchone()[0]
        == staff
    )
    for table in (
        "trust_unit_areas",
        "trust_utility_plans",
        "trust_lease_utilities",
        "trust_utility_bills",
        "trust_utility_shares",
    ):
        with pytest.raises(psycopg.errors.RestrictViolation):
            conn.execute(f"DELETE FROM {table}")  # noqa: S608 - the table names above
    audited = {
        row[0]
        for row in conn.execute(
            "SELECT table_name FROM trust_audit_log WHERE pmc_id = %s", (pmc.pmc_id,)
        ).fetchall()
    }
    assert {"trust_utility_plans", "trust_utility_bills", "trust_utility_shares"} <= audited


def test_the_app_bills_through_the_write_path_and_the_ai_only_reads(conn, app_conn, pmc, prop):
    lease_at(conn, pmc, units(conn, pmc, 1)[0])
    plan_utility(app_conn, pmc.pmc_id, prop, "water", SEP_1, "units")
    bill_back_utility(app_conn, pmc.pmc_id, prop, "water", SEP_1, SEP_30, "50.00", OCT_1)
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        app_conn.execute(
            "INSERT INTO trust_utility_bills (pmc_id, property_id, utility, period_start,"
            " period_end, amount, method, owner_percent, due_on)"
            " VALUES (%s, %s, 'trash', %s, %s, 1, 'units', 0, %s)",
            (pmc.pmc_id, prop, SEP_1, SEP_30, OCT_1),
        )
