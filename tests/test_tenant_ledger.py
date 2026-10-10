"""trust_report_tenant_ledger: one lease's charges, payments and bounced payments through the end
of a day (UTC), in date order, with the balance after each (migration 20261010000020).

The golden case under tests/golden/cases/ pins the full layout; these check the rules behind it.
"""

import uuid
from datetime import UTC, date, datetime
from decimal import Decimal

import psycopg
import pytest
from helpers import (
    add_unit,
    apply_payment,
    charge,
    make_pmc,
    open_lease,
    reverse_payment,
    transfer_batch,
)

JAN_1, MAR_31 = date(2026, 1, 1), date(2026, 3, 31)


@pytest.fixture
def pmc(conn):
    return make_pmc(conn)


@pytest.fixture
def lease(conn, pmc):
    tenant = conn.execute(
        "SELECT tenant_id FROM trust_ledger_accounts WHERE ledger_account_id = %s",
        (pmc.tenant_deposit,),
    ).fetchone()[0]
    unit = add_unit(conn, pmc.pmc_id, pmc.owners[0].property_id)
    return open_lease(conn, pmc.pmc_id, unit, JAN_1, None, "1500.00", [tenant])


def ledger(conn, pmc, lease, as_of=MAR_31):
    return conn.execute(
        "SELECT line, on_date, item, detail, charged, paid, balance"
        " FROM trust_report_tenant_ledger(%s, %s, %s)",
        (pmc.pmc_id, lease, as_of),
    ).fetchall()


def body(rows):
    """(date, item, charged, paid, balance) per entry, without the header and closing lines."""
    return [(r[1], r[2], r[4], r[5], r[6]) for r in rows[1:-1]]


def pay(conn, pmc, charges, when, memo=None):
    """One payment into Owner 1's account at `when`, matched to (charge, amount) pairs."""
    total = sum((Decimal(a) for _, a in charges), Decimal(0))
    (paid,) = conn.execute(
        "SELECT t.id FROM pgledger_create_transfers("
        " ARRAY[(%s, %s, %s)::transfer_request], %s, jsonb_build_object('memo', %s::text)) AS t",
        (pmc.operating_cash, pmc.owners[0].account, total, when, memo),
    ).fetchone()
    for charge_id, amount in charges:
        apply_payment(conn, pmc.pmc_id, charge_id, paid, amount)
    return paid


D = Decimal


@pytest.mark.parametrize("mistake", ["no date", "unknown lease", "another PMC's lease"])
def test_a_missing_date_or_a_lease_not_of_the_pmc_is_refused(conn, pmc, lease, mistake):
    args = {
        "no date": (pmc.pmc_id, lease, None),
        "unknown lease": (pmc.pmc_id, uuid.uuid4(), MAR_31),
        "another PMC's lease": (make_pmc(conn).pmc_id, lease, MAR_31),
    }[mistake]

    with pytest.raises(psycopg.errors.InvalidParameterValue, match="through a day|no lease"):
        conn.execute("SELECT * FROM trust_report_tenant_ledger(%s, %s, %s)", args)


def test_charges_payments_and_bounces_run_in_date_order_with_a_balance(conn, pmc, lease):
    jan = charge(conn, pmc.pmc_id, lease, date(2026, 1, 1), "1500.00", memo="January rent")
    feb = charge(conn, pmc.pmc_id, lease, date(2026, 2, 1), "1500.00", memo="February rent")
    charge(conn, pmc.pmc_id, lease, date(2026, 2, 10), "100.00", "credit", "Repair credit")
    pay(conn, pmc, [(jan, "1500.00")], datetime(2026, 1, 3, tzinfo=UTC), "Check 101")
    feb_paid = pay(conn, pmc, [(feb, "1400.00")], datetime(2026, 2, 5, tzinfo=UTC))
    (back,) = transfer_batch(
        conn,
        [(pmc.owners[0].account, pmc.operating_cash, "1400.00")],
        datetime(2026, 2, 12, tzinfo=UTC),
    )
    reverse_payment(conn, pmc.pmc_id, feb, feb_paid, back, "1400.00")

    rows = ledger(conn, pmc, lease)

    assert [row[0] for row in rows] == list(range(1, len(rows) + 1))
    assert rows[0][2:4] == ("lease", "Unit 1, 2026-01-01, month to month")
    assert body(rows) == [
        (date(2026, 1, 1), "rent", D("1500.00"), D("0.00"), D("1500.00")),
        (date(2026, 1, 3), "payment", D("0.00"), D("1500.00"), D("0.00")),
        (date(2026, 2, 1), "rent", D("1500.00"), D("0.00"), D("1500.00")),
        (date(2026, 2, 5), "payment", D("0.00"), D("1400.00"), D("100.00")),
        (date(2026, 2, 10), "credit", D("-100.00"), D("0.00"), D("0.00")),
        (date(2026, 2, 12), "bounced payment", D("0.00"), D("-1400.00"), D("1400.00")),
    ]
    assert rows[1][3] == "January rent" and rows[2][3] == "Check 101"
    assert rows[-1][2:] == ("balance", "end of day UTC", D("2900.00"), D("1500.00"), D("1400.00"))


def test_one_payment_across_several_charges_is_one_line(conn, pmc, lease):
    jan = charge(conn, pmc.pmc_id, lease, date(2026, 1, 1), "1500.00")
    fee = charge(conn, pmc.pmc_id, lease, date(2026, 1, 1), "50.00", "fee")
    pay(conn, pmc, [(jan, "1500.00"), (fee, "50.00")], datetime(2026, 1, 4, tzinfo=UTC))

    assert [r[1:3] for r in body(ledger(conn, pmc, lease))] == [
        ("rent", D("1500.00")),
        ("fee", D("50.00")),
        ("payment", D("0.00")),
    ]
    assert body(ledger(conn, pmc, lease))[-1][3] == D("1550.00")


def test_on_one_day_charges_come_before_payments_and_payments_before_bounces(conn, pmc, lease):
    rent = charge(conn, pmc.pmc_id, lease, date(2026, 2, 1), "1500.00")
    paid = pay(conn, pmc, [(rent, "1500.00")], datetime(2026, 2, 1, 0, 0, tzinfo=UTC))
    (back,) = transfer_batch(
        conn,
        [(pmc.owners[0].account, pmc.operating_cash, "1500.00")],
        datetime(2026, 2, 1, 0, 0, tzinfo=UTC),
    )
    reverse_payment(conn, pmc.pmc_id, rent, paid, back, "1500.00")

    assert [r[1] for r in body(ledger(conn, pmc, lease))] == ["rent", "payment", "bounced payment"]


def test_the_ledger_ends_at_the_end_of_the_day(conn, pmc, lease):
    rent = charge(conn, pmc.pmc_id, lease, date(2026, 3, 31), "1500.00")
    charge(conn, pmc.pmc_id, lease, date(2026, 4, 1), "1500.00")  # not due by then
    pay(conn, pmc, [(rent, "1500.00")], datetime(2026, 4, 1, 0, 0, tzinfo=UTC))  # April's

    assert [r[1] for r in body(ledger(conn, pmc, lease))] == ["rent"]
    assert ledger(conn, pmc, lease)[-1][-1] == D("1500.00")


def test_the_closing_balance_is_the_rent_rolls_balance_due(conn, pmc, lease):
    jan = charge(conn, pmc.pmc_id, lease, date(2026, 1, 1), "1500.00")
    charge(conn, pmc.pmc_id, lease, date(2026, 2, 1), "1500.00")
    ahead = charge(conn, pmc.pmc_id, lease, date(2026, 5, 1), "1500.00")
    pay(conn, pmc, [(jan, "1500.00"), (ahead, "200.00")], datetime(2026, 1, 2, tzinfo=UTC))

    closing = ledger(conn, pmc, lease)[-1][-1]
    roll = conn.execute(
        "SELECT balance_due FROM trust_report_rent_roll(%s, %s) WHERE item = 'unit'",
        (pmc.pmc_id, MAR_31),
    ).fetchone()[0]
    assert closing == roll == D("1300.00")


def test_a_lease_with_nothing_on_it_has_a_zero_balance(conn, pmc, lease):
    rows = ledger(conn, pmc, lease)

    assert [r[2] for r in rows] == ["lease", "balance"]
    assert rows[-1][4:] == (D("0.00"), D("0.00"), D("0.00"))


def test_the_ledger_reads_the_same_in_any_time_zone_and_for_every_role(
    conn, app_conn, ai_conn, database_url, pmc, lease
):
    rent = charge(conn, pmc.pmc_id, lease, date(2026, 3, 1), "1500.00")
    pay(conn, pmc, [(rent, "1500.00")], datetime(2026, 3, 31, 23, 30, tzinfo=UTC))

    readings = [ledger(app_conn, pmc, lease), ledger(ai_conn, pmc, lease)]
    for zone in ("UTC", "America/Los_Angeles", "Pacific/Auckland"):
        with psycopg.connect(database_url, autocommit=True) as session:
            session.execute(f"SET TIME ZONE '{zone}'")
            readings.append(ledger(session, pmc, lease))

    assert all(reading == readings[0] for reading in readings)
    assert body(readings[0])[-1][0] == date(2026, 3, 31)  # the payment's UTC day
