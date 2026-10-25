"""An owner's year-end package (migration 20261013000039).

trust_report_owner_year_end sums each of the owner's property accounts over a calendar year
(UTC): its opening balance, what was collected from tenants (net of bounces), the fees, bills,
draws and contributions, anything else, and its closing balance; then the same across the
owner's properties, and the rents for the owner's 1099-MISC. The golden case under
tests/golden/cases/ pins the layout; these check the rules.
"""

import uuid
from datetime import UTC, date, datetime
from decimal import Decimal

import psycopg
import pytest
from helpers import (
    add_agreement,
    add_unit,
    apply_payment,
    approve_bill,
    charge,
    contribute,
    deposit_interest_terms,
    draw_owner,
    enter_bill,
    language_sorted_database,
    make_pmc,
    open_account,
    open_lease,
    pay_deposit_interest,
    post_leasing_fee,
    post_management_fee,
    reverse_payment,
    set_aside_bill,
    transfer_batch,
)

PARTS = [
    "opening balance",
    "collected from tenants",
    "management fees",
    "leasing fees",
    "bills paid",
    "owner draws",
    "owner contributions",
    "security deposit interest",
    "other",
    "closing balance",
]


def at(month, day, year=2026, hour=12):
    return datetime(year, month, day, hour, tzinfo=UTC)


@pytest.fixture
def pmc(conn):
    return make_pmc(conn)


def package(conn, pmc, year=2026, owner=None):
    return conn.execute(
        "SELECT line, property, item, amount FROM trust_report_owner_year_end(%s, %s, %s)",
        (pmc.pmc_id, owner or pmc.owners[0].owner_id, year),
    ).fetchall()


def parts(conn, pmc, year=2026, prop="Property 1"):
    """One property's parts of the year, by name."""
    return {item: amount for _, p, item, amount in package(conn, pmc, year) if p == prop}


def lease_of(conn, pmc, rent="1500.00"):
    tenant = conn.execute(
        "SELECT tenant_id FROM trust_ledger_accounts WHERE ledger_account_id = %s",
        (pmc.tenant_deposit,),
    ).fetchone()[0]
    unit = add_unit(conn, pmc.pmc_id, pmc.owners[0].property_id)
    return open_lease(conn, pmc.pmc_id, unit, date(2025, 1, 1), None, rent, [tenant])


def paid(conn, pmc, lease, amount, when, kind="rent"):
    """A charge of the lease paid into Owner 1's account at `when`; (charge, transfer)."""
    charged = charge(conn, pmc.pmc_id, lease, when.date(), amount, kind)
    (transfer,) = transfer_batch(conn, [(pmc.operating_cash, pmc.owners[0].account, amount)], when)
    apply_payment(conn, pmc.pmc_id, charged, transfer, amount)
    return charged, transfer


def test_each_part_of_the_year_shows_under_its_property(conn, app_conn, pmc):
    account = pmc.owners[0].account
    add_agreement(
        conn, pmc.pmc_id, account, date(2025, 1, 1), "10", flat_fee="5", leasing_fee_percent="50"
    )
    lease = lease_of(conn, pmc)
    transfer_batch(conn, [(pmc.operating_cash, account, "300.00")], at(12, 20, 2025))
    paid(conn, pmc, lease, "1500.00", at(1, 3))
    paid(conn, pmc, lease, "25.00", at(1, 4), "fee")  # a fee collected is collected too
    post_leasing_fee(conn, pmc.pmc_id, lease, account, at(1, 5))  # 750.00
    post_management_fee(conn, pmc.pmc_id, account, date(2026, 1, 1), date(2026, 2, 1), at(1, 31))
    contribute(conn, pmc.pmc_id, account, "roof", "400.00", at(2, 1))
    bill = enter_bill(
        conn,
        pmc.pmc_id,
        pmc.vendor_id,
        account,
        "ROOF-1",
        date(2026, 2, 2),
        date(2026, 3, 1),
        "250.00",
    )
    approve_bill(conn, pmc.pmc_id, bill, date(2026, 2, 2), "Owner 1, by email")
    set_aside_bill(conn, pmc.pmc_id, bill, at(2, 3))
    draw_owner(conn, pmc.pmc_id, account, "march-draw", "100.00", at(3, 1))
    transfer_batch(conn, [(account, pmc.owners[1].account, "20.00")], at(6, 1))

    assert parts(conn, pmc) == dict(
        zip(
            PARTS,
            [
                Decimal("300.00"),
                Decimal("1525.00"),
                Decimal("-155.00"),  # 10% of the 1500.00 rent, + 5.00
                Decimal("-750.00"),
                Decimal("-250.00"),
                Decimal("-100.00"),
                Decimal("400.00"),
                Decimal("0.00"),
                Decimal("-20.00"),
                Decimal("950.00"),
            ],
            strict=True,
        )
    )
    assert package(app_conn, pmc)[-1][2:] == ("1099-MISC rents (box 1)", Decimal("1525.00"))


def test_the_closing_balance_is_the_ledgers_and_the_next_years_opening(conn, pmc):
    account = pmc.owners[0].account
    transfer_batch(conn, [(pmc.operating_cash, account, "300.00")], at(6, 1))
    transfer_batch(conn, [(pmc.operating_cash, account, "50.00")], at(1, 1, 2027, hour=0))

    assert parts(conn, pmc)["closing balance"] == Decimal("300.00")
    assert parts(conn, pmc, 2027)["opening balance"] == Decimal("300.00")
    assert parts(conn, pmc, 2027)["closing balance"] == Decimal("350.00")


def test_the_year_runs_in_utc_days_whatever_the_sessions_time_zone(conn, database_url, pmc):
    lease = lease_of(conn, pmc)
    paid(conn, pmc, lease, "100.00", datetime(2026, 12, 31, 23, 59, 59, tzinfo=UTC))
    paid(conn, pmc, lease, "40.00", datetime(2027, 1, 1, tzinfo=UTC))

    readings = []
    for zone in ("UTC", "America/Los_Angeles", "Pacific/Auckland"):
        with psycopg.connect(database_url, autocommit=True) as session:
            session.execute(f"SET TIME ZONE '{zone}'")
            readings.append(parts(session, pmc)["collected from tenants"])

    assert readings == [Decimal("100.00")] * 3
    assert parts(conn, pmc, 2027)["collected from tenants"] == Decimal("40.00")


def test_a_bounce_comes_off_the_year_it_bounced_in(conn, pmc):
    lease = lease_of(conn, pmc)
    rent, payment = paid(conn, pmc, lease, "1500.00", at(12, 30, 2025))
    (back,) = transfer_batch(
        conn, [(pmc.owners[0].account, pmc.operating_cash, "1500.00")], at(1, 2)
    )
    reverse_payment(conn, pmc.pmc_id, rent, payment, back, "1500.00")

    assert parts(conn, pmc, 2025)["collected from tenants"] == Decimal("1500.00")
    assert parts(conn, pmc, 2026)["collected from tenants"] == Decimal("-1500.00")
    assert package(conn, pmc, 2026)[-1][3] == Decimal("-1500.00")


def test_money_with_no_part_of_its_own_is_other(conn, pmc):
    account = pmc.owners[0].account
    transfer_batch(conn, [(pmc.operating_cash, account, "80.00")], at(3, 1))  # not matched

    assert (parts(conn, pmc)["collected from tenants"], parts(conn, pmc)["other"]) == (
        Decimal("0.00"),
        Decimal("80.00"),
    )


def test_interest_added_to_a_deposit_is_a_part_of_its_own_not_other(conn, app_conn, pmc):
    """Interest added to a tenant's deposit (migration 0122) leaves the owner's account: its own
    part of the year (migration 20261025000200). Interest credited against the rent moves no
    money."""
    lease = lease_of(conn, pmc)
    transfer_batch(conn, [(pmc.deposit_cash, pmc.tenant_deposit, "1200.00")], at(1, 1, 2025))
    deposit_interest_terms(conn, pmc.pmc_id, pmc.owners[0].property_id, date(2025, 1, 1), 1)
    transfer_batch(conn, [(pmc.operating_cash, pmc.owners[0].account, "100.00")], at(1, 1, 2025))

    added = pay_deposit_interest(
        app_conn, pmc.pmc_id, lease, date(2025, 12, 31), "deposit", "interest-2025", at(1, 5)
    )
    credited = pay_deposit_interest(
        app_conn, pmc.pmc_id, lease, date(2026, 6, 30), "rent_credit", "interest-june", at(7, 1)
    )

    # 1200.00 at 1% through 2025 is 12.00, out of the 100.00 the account held from 2025.
    assert (added, credited > 0) == (Decimal("12.00"), True)
    got = parts(conn, pmc)
    assert (got["opening balance"], got["closing balance"]) == (Decimal("100.00"), Decimal("88.00"))
    assert got["security deposit interest"] == Decimal("-12.00")
    assert got["other"] == Decimal("0.00")
    assert parts(conn, pmc, 2025)["security deposit interest"] == Decimal("0.00")


def test_every_property_of_the_owner_and_the_totals_across_them(conn, pmc):
    second = conn.execute(
        "INSERT INTO trust_properties (pmc_id, display_name) VALUES (%s, 'Property 0')"
        " RETURNING id",
        (pmc.pmc_id,),
    ).fetchone()[0]
    other_account = open_account(
        conn, pmc.pmc_id, pmc.operating_bank_id, "owner_property", pmc.owners[0].owner_id, second
    )
    transfer_batch(conn, [(pmc.operating_cash, pmc.owners[0].account, "100.00")], at(2, 1))
    transfer_batch(conn, [(pmc.operating_cash, other_account, "30.00")], at(2, 1))

    rows = package(conn, pmc)

    assert [r[0] for r in rows] == list(range(1, len(rows) + 1))
    assert [r[1:3] for r in rows[:3]] == [
        (rows[0][1], "PMC"),
        ("Owner 1", "owner"),
        ("2026, January 1 to December 31 UTC", "year"),
    ]
    assert [r[1] for r in rows[3:23]] == ["Property 0"] * 10 + ["Property 1"] * 10
    totals = {item: amount for _, p, item, amount in rows[23:33]}
    assert totals["all properties: other"] == Decimal("130.00")
    assert totals["all properties: closing balance"] == Decimal("130.00")
    assert rows[33][2:] == ("1099-MISC rents (box 1)", Decimal("0.00"))
    assert len(rows) == 34


def test_another_owners_money_is_not_in_the_package(conn, pmc):
    transfer_batch(conn, [(pmc.operating_cash, pmc.owners[1].account, "999.00")], at(2, 1))

    assert set(parts(conn, pmc).values()) == {Decimal("0.00")}
    assert {r[2] for r in package(conn, pmc) if r[1] == "Property 2"} == set()


def test_an_owner_with_no_property_has_a_package_of_nothing(conn, pmc):
    lone = conn.execute(
        "INSERT INTO trust_owners (pmc_id, display_name) VALUES (%s, 'Owner 9') RETURNING id",
        (pmc.pmc_id,),
    ).fetchone()[0]

    rows = package(conn, pmc, owner=lone)

    assert [(r[2], r[3]) for r in rows[3:]] == [
        *((f"all properties: {part}", Decimal("0.00")) for part in PARTS),
        ("1099-MISC rents (box 1)", Decimal("0.00")),
    ]


@pytest.mark.parametrize("year", [None, 1899, 9999])
def test_the_package_is_for_a_calendar_year(conn, pmc, year):
    with pytest.raises(psycopg.errors.InvalidParameterValue, match="calendar year"):
        package(conn, pmc, year)


@pytest.mark.parametrize("year", [1900, 9998])
def test_the_package_reads_every_year_from_1900_to_9998(conn, pmc, year):
    assert package(conn, pmc, year)[2][1] == f"{year}, January 1 to December 31 UTC"


def test_an_unknown_owner_or_another_pmcs_is_refused(conn, pmc):
    other = make_pmc(conn)
    for owner in (uuid.uuid4(), other.owners[0].owner_id):
        with pytest.raises(psycopg.errors.InvalidParameterValue, match="no owner"):
            package(conn, pmc, owner=owner)


def test_properties_list_in_the_same_order_on_any_server(database_url):
    with language_sorted_database(database_url) as conn:
        pmc = make_pmc(conn, owners=1)
        conn.execute(
            "UPDATE trust_properties SET display_name = 'property a' WHERE id = %s",
            (pmc.owners[0].property_id,),
        )
        second = conn.execute(
            "INSERT INTO trust_properties (pmc_id, display_name) VALUES (%s, 'Property b')"
            " RETURNING id",
            (pmc.pmc_id,),
        ).fetchone()[0]
        open_account(
            conn,
            pmc.pmc_id,
            pmc.operating_bank_id,
            "owner_property",
            pmc.owners[0].owner_id,
            second,
        )
        rows = package(conn, pmc)

    assert [r[1] for r in rows if r[2] == "opening balance"] == ["Property b", "property a"]
