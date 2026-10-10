"""trust_report_delinquency: as of the end of a day (UTC), every lease that owes money, by how
long it has been past due (migration 20261010000020).

The golden case under tests/golden/cases/ pins the full layout; these check the rules behind it.
"""

import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import psycopg
import pytest
from helpers import (
    add_tenant,
    add_unit,
    apply_payment,
    charge,
    language_sorted_database,
    make_pmc,
    open_lease,
    reverse_payment,
    transfer_batch,
)

JAN_1, APR_30 = date(2026, 1, 1), date(2026, 4, 30)


def at(day, hour=12):
    return datetime(day.year, day.month, day.day, hour, tzinfo=UTC)


@pytest.fixture
def pmc(conn):
    return make_pmc(conn)


def tenant_of(conn, pmc):
    return conn.execute(
        "SELECT tenant_id FROM trust_ledger_accounts WHERE ledger_account_id = %s",
        (pmc.tenant_deposit,),
    ).fetchone()[0]


def lease_on(conn, pmc, name="Unit 1", tenants=None):
    unit = add_unit(conn, pmc.pmc_id, pmc.owners[0].property_id, name)
    return open_lease(
        conn, pmc.pmc_id, unit, JAN_1, None, "1500.00", tenants or [tenant_of(conn, pmc)]
    )


def report(conn, pmc, as_of=APR_30):
    return conn.execute(
        "SELECT line, item, property, unit, tenants, current_due, days_1_30, days_31_60,"
        " days_61_90, over_90, total FROM trust_report_delinquency(%s, %s)",
        (pmc.pmc_id, as_of),
    ).fetchall()


def buckets(rows):
    """(unit, current, 1-30, 31-60, 61-90, over 90, total) per lease line."""
    return [(r[3], *r[5:]) for r in rows if r[1] == "lease"]


def pay(conn, pmc, charge_id, amount, when):
    (paid,) = transfer_batch(conn, [(pmc.operating_cash, pmc.owners[0].account, amount)], when)
    apply_payment(conn, pmc.pmc_id, charge_id, paid, amount)
    return paid


Z = Decimal("0.00")


@pytest.mark.parametrize("mistake", ["no date", "unknown PMC"])
def test_a_missing_date_or_an_unknown_pmc_is_refused(conn, pmc, mistake):
    pmc_id, as_of = (pmc.pmc_id, None) if mistake == "no date" else (uuid.uuid4(), APR_30)

    with pytest.raises(psycopg.errors.InvalidParameterValue, match="as of a day|no PMC"):
        conn.execute("SELECT * FROM trust_report_delinquency(%s, %s)", (pmc_id, as_of))


@pytest.mark.parametrize(
    ("late", "bucket"),
    [(0, 0), (1, 1), (30, 1), (31, 2), (60, 2), (61, 3), (90, 3), (91, 4)],
)
def test_each_charge_falls_in_the_bucket_of_its_days_past_due(conn, pmc, late, bucket):
    lease = lease_on(conn, pmc)
    charge(conn, pmc.pmc_id, lease, APR_30 - timedelta(days=late), "100.00")

    expected = [Z] * 5
    expected[bucket] = Decimal("100.00")
    assert buckets(report(conn, pmc)) == [("Unit 1", *expected, Decimal("100.00"))]


def test_charges_not_yet_due_and_paid_charges_owe_nothing(conn, pmc):
    lease = lease_on(conn, pmc)
    charge(conn, pmc.pmc_id, lease, date(2026, 5, 1), "100.00")  # not due yet
    paid = charge(conn, pmc.pmc_id, lease, date(2026, 4, 1), "200.00")
    pay(conn, pmc, paid, "200.00", at(date(2026, 4, 2)))

    assert buckets(report(conn, pmc)) == []


def test_a_part_payment_leaves_the_rest_in_its_bucket(conn, pmc):
    lease = lease_on(conn, pmc)
    rent = charge(conn, pmc.pmc_id, lease, date(2026, 4, 1), "1500.00")
    pay(conn, pmc, rent, "1000.00", at(date(2026, 4, 3)))

    assert buckets(report(conn, pmc)) == [
        ("Unit 1", Z, Decimal("500.00"), Z, Z, Z, Decimal("500.00"))
    ]


def test_payments_and_bounces_count_as_of_the_end_of_the_day(conn, pmc):
    lease = lease_on(conn, pmc)
    rent = charge(conn, pmc.pmc_id, lease, date(2026, 4, 1), "1500.00")
    paid = pay(conn, pmc, rent, "1500.00", datetime(2026, 4, 30, 23, 59, tzinfo=UTC))
    (back,) = transfer_batch(
        conn,
        [(pmc.owners[0].account, pmc.operating_cash, "1500.00")],
        datetime(2026, 5, 1, 0, 0, tzinfo=UTC),
    )
    reverse_payment(conn, pmc.pmc_id, rent, paid, back, "1500.00")

    assert buckets(report(conn, pmc, date(2026, 4, 29)))[0][-1] == Decimal("1500.00")
    assert buckets(report(conn, pmc, APR_30)) == []  # paid by its last minute
    assert buckets(report(conn, pmc, date(2026, 5, 1)))[0][-1] == Decimal("1500.00")  # bounced


def test_credits_and_money_paid_ahead_come_off_the_oldest_charges_first(conn, pmc):
    lease = lease_on(conn, pmc)
    charge(conn, pmc.pmc_id, lease, date(2026, 1, 1), "300.00")  # 119 days: over 90
    charge(conn, pmc.pmc_id, lease, date(2026, 3, 1), "300.00")  # 60 days: 31-60
    charge(conn, pmc.pmc_id, lease, date(2026, 4, 30), "300.00")  # today: current
    charge(conn, pmc.pmc_id, lease, date(2026, 4, 15), "250.00", "credit")
    ahead = charge(conn, pmc.pmc_id, lease, date(2026, 5, 1), "300.00")  # not due yet
    pay(conn, pmc, ahead, "100.00", at(date(2026, 4, 20)))

    # 350.00 comes off: all 300.00 of January's, then 50.00 of March's.
    assert buckets(report(conn, pmc)) == [
        ("Unit 1", Decimal("300.00"), Z, Decimal("250.00"), Z, Z, Decimal("550.00"))
    ]


def test_a_lease_in_credit_owes_nothing(conn, pmc):
    lease = lease_on(conn, pmc)
    charge(conn, pmc.pmc_id, lease, date(2026, 4, 1), "100.00")
    charge(conn, pmc.pmc_id, lease, date(2026, 4, 2), "150.00", "credit")

    assert buckets(report(conn, pmc)) == []


def test_each_lease_owing_is_listed_with_its_tenants_and_a_total(conn, pmc):
    tenant_2 = add_tenant(conn, pmc.pmc_id, pmc.owners[0].property_id, "tenant 2")
    first = lease_on(conn, pmc, "Unit 1", [tenant_of(conn, pmc), tenant_2])
    second = lease_on(conn, pmc, "Unit 2")
    lease_on(conn, pmc, "Unit 3")  # owes nothing
    charge(conn, pmc.pmc_id, first, date(2026, 4, 20), "100.00")
    charge(conn, pmc.pmc_id, second, date(2026, 1, 20), "40.00")

    rows = report(conn, pmc)

    assert [row[0] for row in rows] == list(range(1, len(rows) + 1))
    assert [(r[3], r[4]) for r in rows if r[1] == "lease"] == [
        ("Unit 1", "Tenant 1, tenant 2"),
        ("Unit 2", "Tenant 1"),
    ]
    assert rows[-1][1:2] + rows[-1][5:] == (
        "total",
        Z,
        Decimal("100.00"),
        Z,
        Z,
        Decimal("40.00"),
        Decimal("140.00"),
    )


def test_a_pmc_owed_nothing_shows_a_zero_total(conn, pmc):
    rows = report(conn, pmc)

    assert [r[1] for r in rows] == ["PMC", "as of", "total"]
    assert rows[2][5:] == (Z,) * 6


def test_each_leases_total_is_its_balance_due_on_the_rent_roll(conn, pmc):
    lease = lease_on(conn, pmc)
    for due, amount, kind in [
        (date(2026, 2, 1), "1500.00", "rent"),
        (date(2026, 3, 1), "1500.00", "rent"),
        (date(2026, 3, 9), "75.00", "fee"),
        (date(2026, 3, 20), "200.00", "credit"),
        (date(2026, 6, 1), "1500.00", "rent"),
    ]:
        made = charge(conn, pmc.pmc_id, lease, due, amount, kind)
        if due == date(2026, 6, 1):
            pay(conn, pmc, made, "400.00", at(date(2026, 3, 25)))  # paid ahead

    owed = buckets(report(conn, pmc))[0][-1]
    roll = conn.execute(
        "SELECT balance_due FROM trust_report_rent_roll(%s, %s) WHERE item = 'unit'",
        (pmc.pmc_id, APR_30),
    ).fetchone()[0]
    assert owed == roll == Decimal("2475.00")


def test_the_report_reads_the_same_in_any_time_zone_and_for_every_role(
    conn, app_conn, ai_conn, database_url, pmc
):
    lease = lease_on(conn, pmc)
    rent = charge(conn, pmc.pmc_id, lease, date(2026, 4, 1), "1500.00")
    pay(conn, pmc, rent, "1500.00", datetime(2026, 4, 30, 23, 30, tzinfo=UTC))  # May in Auckland

    readings = [report(app_conn, pmc), report(ai_conn, pmc)]
    for zone in ("UTC", "America/Los_Angeles", "Pacific/Auckland"):
        with psycopg.connect(database_url, autocommit=True) as session:
            session.execute(f"SET TIME ZONE '{zone}'")
            readings.append(report(session, pmc))

    assert all(reading == readings[0] for reading in readings)
    assert buckets(readings[0]) == []


def test_properties_and_units_list_in_the_same_order_on_any_server(database_url):
    with language_sorted_database(database_url) as conn:
        pmc = make_pmc(conn)
        for name in ("unit a", "Unit b"):
            lease = lease_on(conn, pmc, name)
            charge(conn, pmc.pmc_id, lease, date(2026, 4, 1), "10.00")
        rows = report(conn, pmc)

    assert [b[0] for b in buckets(rows)] == ["Unit b", "unit a"]


def test_a_credit_due_on_the_day_comes_off(conn, pmc):
    lease = lease_on(conn, pmc)
    charge(conn, pmc.pmc_id, lease, date(2026, 4, 1), "100.00")
    charge(conn, pmc.pmc_id, lease, APR_30, "40.00", "credit")

    assert buckets(report(conn, pmc)) == [
        ("Unit 1", Z, Decimal("60.00"), Z, Z, Z, Decimal("60.00"))
    ]


def test_a_payment_toward_a_charge_due_on_the_day_counts_once(conn, pmc):
    lease = lease_on(conn, pmc)
    charge(conn, pmc.pmc_id, lease, date(2026, 4, 1), "300.00")
    today = charge(conn, pmc.pmc_id, lease, APR_30, "300.00")
    pay(conn, pmc, today, "100.00", at(APR_30))

    assert buckets(report(conn, pmc)) == [
        ("Unit 1", Decimal("200.00"), Decimal("300.00"), Z, Z, Z, Decimal("500.00"))
    ]


def test_a_payment_dated_after_the_day_does_not_count(conn, pmc):
    lease = lease_on(conn, pmc)
    rent = charge(conn, pmc.pmc_id, lease, date(2026, 4, 1), "1500.00")
    pay(conn, pmc, rent, "1500.00", datetime(2026, 5, 1, 0, 0, tzinfo=UTC))

    assert buckets(report(conn, pmc))[0][-1] == Decimal("1500.00")


def id_starting(prefix):
    """A fresh lease id that sorts by its first eight hex digits."""
    return uuid.UUID(prefix + uuid.uuid4().hex[8:])


@pytest.mark.parametrize("later_first", [True, False])
@pytest.mark.parametrize("later_sorts_first", [True, False])
def test_two_leases_of_one_unit_list_oldest_first(conn, pmc, later_first, later_sorts_first):
    # Inserted in either order, with ids that sort either way: only the start date may decide.
    unit = add_unit(conn, pmc.pmc_id, pmc.owners[0].property_id, "Unit 1")
    later_id = id_starting("00000000" if later_sorts_first else "ffffffff")
    earlier_id = id_starting("77777777")
    leases = {
        "later": (later_id, date(2026, 3, 1), None),
        "earlier": (earlier_id, JAN_1, date(2026, 2, 28)),
    }
    for which in ("later", "earlier") if later_first else ("earlier", "later"):
        lease_id, starts_on, ends_on = leases[which]
        conn.execute(
            "INSERT INTO trust_leases (id, pmc_id, unit_id, starts_on, ends_on, monthly_rent)"
            " VALUES (%s, %s, %s, %s, %s, 1)",
            (lease_id, pmc.pmc_id, unit, starts_on, ends_on),
        )
    charge(conn, pmc.pmc_id, later_id, date(2026, 4, 1), "1500.00")
    charge(conn, pmc.pmc_id, earlier_id, date(2026, 2, 1), "1400.00")

    assert [b[-1] for b in buckets(report(conn, pmc))] == [Decimal("1400.00"), Decimal("1500.00")]
