"""Rent first (migration 20261018000093; the founder, 2026-10-18; docs/law.md): a credit, a
payment and a deposit kept pay a lease's rent and monthly charges before its other charges, each
oldest first (Washington's RCW 59.18.283, the strictest state found). Before it, charges due the
same day were paid in the order of their random ids, so a late fee could come out either way.
The app's side (src/pms/api/rent.py) is in tests/api/test_rent_first_api.py. Every name here is
synthetic."""

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from helpers import (
    add_late_fee_policy,
    add_recurring_charge,
    add_unit,
    assess_late_fees,
    charge,
    charge_recurring_due,
    charge_rent_due,
    end_lease,
    make_pmc,
    open_lease,
    return_deposit,
    transfer_batch,
)

JAN_1, FEB_1, MAR_1, MAR_10, MAR_31 = (
    date(2026, 1, 1),
    date(2026, 2, 1),
    date(2026, 3, 1),
    date(2026, 3, 10),
    date(2026, 3, 31),
)


@pytest.fixture
def pmc(conn):
    return make_pmc(conn)


@pytest.fixture
def tenant(conn, pmc):
    return conn.execute(
        "SELECT tenant_id FROM trust_ledger_accounts WHERE ledger_account_id = %s",
        (pmc.tenant_deposit,),
    ).fetchone()[0]


def new_lease(conn, pmc, tenant, ends_on=None):
    unit = add_unit(conn, pmc.pmc_id, pmc.owners[0].property_id)
    return open_lease(conn, pmc.pmc_id, unit, JAN_1, ends_on, "1000.00", [tenant])


def open_of(conn, pmc, lease):
    return dict(
        conn.execute(
            "SELECT charge_id, open FROM trust_open_charges(%s, %s, 'infinity')",
            (pmc.pmc_id, lease),
        ).fetchall()
    )


def rank(conn, charge_id):
    return conn.execute(
        "SELECT trust_pay_rank(c) FROM trust_charges AS c WHERE c.id = %s", (charge_id,)
    ).fetchone()[0]


def test_a_charges_place_rent_then_monthly_charges_then_the_rest(conn, pmc, tenant):
    lease = new_lease(conn, pmc, tenant)
    add_recurring_charge(conn, pmc.pmc_id, lease, "35.00", MAR_1)
    charge_rent_due(conn, pmc.pmc_id, MAR_1)
    charge_recurring_due(conn, pmc.pmc_id, MAR_1)
    by_kind = dict(
        conn.execute(
            "SELECT CASE WHEN recurring_charge_id IS NULL THEN kind ELSE 'monthly' END, id"
            " FROM trust_charges WHERE lease_id = %s",
            (lease,),
        ).fetchall()
    )
    fee = charge(conn, pmc.pmc_id, lease, MAR_1, "20.00", "fee", "Lost key")
    credit = charge(conn, pmc.pmc_id, lease, MAR_1, "5.00", "credit", "Courtesy")
    # The lease ends mid-month after March was charged: its credits for the days left are
    # credits, and a fee for days back (moving the end later again) is the rent's own place.
    end_lease(conn, pmc.pmc_id, lease, date(2026, 3, 15))
    end_lease(conn, pmc.pmc_id, lease, MAR_31)
    back = dict(
        conn.execute(
            "SELECT o.kind, a.id FROM trust_lease_end_adjustments AS x"
            " JOIN trust_charges AS a ON a.id = x.adjustment_charge_id"
            " JOIN trust_charges AS o ON o.id = x.charge_id"
            " WHERE a.lease_id = %s AND a.kind = 'fee'",
            (lease,),
        ).fetchall()
    )

    assert (rank(conn, by_kind["rent"]), rank(conn, by_kind["monthly"])) == (0, 1)
    assert (rank(conn, back["rent"]), rank(conn, back["fee"])) == (1, 1)
    assert (rank(conn, fee), rank(conn, credit)) == (2, 2)


def test_a_late_fee_no_longer_depends_on_chance(conn):
    """The same lease and charges, again and again: the credit comes off the rent, never the
    fee due the same day, so the late fee is the same every time."""
    seen = set()
    for _ in range(12):
        pmc = make_pmc(conn)
        mine = conn.execute(
            "SELECT tenant_id FROM trust_ledger_accounts WHERE ledger_account_id = %s",
            (pmc.tenant_deposit,),
        ).fetchone()[0]
        lease = new_lease(conn, pmc, mine)
        add_late_fee_policy(conn, pmc.pmc_id, pmc.owners[0].property_id, JAN_1, 0, "25.00", "10")
        charge(conn, pmc.pmc_id, lease, MAR_1, "1000.00", "rent")
        charge(conn, pmc.pmc_id, lease, MAR_1, "35.00", "fee", "Lost key")
        charge(conn, pmc.pmc_id, lease, FEB_1, "35.00", "credit", "Courtesy")
        assert assess_late_fees(conn, pmc.pmc_id, MAR_10) == 1
        seen.add(
            conn.execute(
                "SELECT unpaid, fee FROM trust_late_fees WHERE pmc_id = %s", (pmc.pmc_id,)
            ).fetchone()
        )

    assert seen == {(Decimal("965.00"), Decimal("121.50"))}


def test_credits_come_off_the_rent_before_an_older_fee(conn, pmc, tenant):
    lease = new_lease(conn, pmc, tenant)
    fee = charge(conn, pmc.pmc_id, lease, JAN_1, "100.00", "fee", "Lost key")
    rent = charge(conn, pmc.pmc_id, lease, FEB_1, "1000.00", "rent")
    charge(conn, pmc.pmc_id, lease, FEB_1, "150.00", "credit", "Courtesy")

    assert open_of(conn, pmc, lease) == {fee: Decimal("100.00"), rent: Decimal("850.00")}
    # The delinquency report ages them the same way: the January fee stays 31-60 days late.
    row = conn.execute(
        "SELECT current_due, days_1_30, days_31_60, total FROM trust_report_delinquency(%s, %s)"
        " WHERE item = 'lease'",
        (pmc.pmc_id, date(2026, 2, 15)),
    ).fetchone()
    assert row == (Decimal("0.00"), Decimal("850.00"), Decimal("100.00"), Decimal("950.00"))


def test_a_deposit_kept_pays_the_rent_first_less_its_credits(conn, pmc, tenant):
    lease = new_lease(conn, pmc, tenant, date(2026, 2, 28))
    fee = charge(conn, pmc.pmc_id, lease, JAN_1, "100.00", "fee", "Lost key")
    rent = charge(conn, pmc.pmc_id, lease, FEB_1, "1000.00", "rent")
    charge(conn, pmc.pmc_id, lease, FEB_1, "200.00", "credit", "Courtesy")
    transfer_batch(
        conn, [(pmc.deposit_cash, pmc.tenant_deposit, "850.00")], datetime(2026, 1, 1, tzinfo=UTC)
    )

    back = return_deposit(conn, pmc.pmc_id, lease, "move-out", datetime(2026, 3, 5, tzinfo=UTC))

    # Owed 900.00: the rent's 800.00 (after the credit) first, then 50.00 of the fee.
    assert back == Decimal("0.00")
    assert conn.execute(
        "SELECT charge_id, amount FROM trust_deposit_return_lines WHERE lease_id = %s"
        " ORDER BY line_no",
        (lease,),
    ).fetchall() == [(rent, Decimal("800.00")), (fee, Decimal("50.00"))]
    assert open_of(conn, pmc, lease) == {rent: Decimal("0.00"), fee: Decimal("50.00")}


def test_a_monthly_charge_is_aged_with_the_rent_before_an_older_fee(conn, pmc, tenant):
    lease = new_lease(conn, pmc, tenant)
    add_recurring_charge(conn, pmc.pmc_id, lease, "50.00", FEB_1)
    charge(conn, pmc.pmc_id, lease, JAN_1, "100.00", "fee", "Lost key")
    charge_recurring_due(conn, pmc.pmc_id, FEB_1)
    charge(conn, pmc.pmc_id, lease, FEB_1, "50.00", "credit", "Courtesy")

    row = conn.execute(
        "SELECT current_due, days_1_30, days_31_60, total FROM trust_report_delinquency(%s, %s)"
        " WHERE item = 'lease'",
        (pmc.pmc_id, date(2026, 2, 15)),
    ).fetchone()

    # The credit pays the pet rent (a monthly charge, paid with the rent); the January fee
    # stays 31-60 days late.
    assert row == (Decimal("0.00"), Decimal("0.00"), Decimal("100.00"), Decimal("100.00"))


def test_on_one_day_the_rent_takes_a_credit_before_its_monthly_charge_whatever_their_ids(
    conn, pmc, tenant
):
    lease = new_lease(conn, pmc, tenant)
    monthly = add_recurring_charge(conn, pmc.pmc_id, lease, "35.00", MAR_1)
    # Charged in one transaction, so at the same time, the pet rent with the smaller id.
    with conn.transaction():
        pet = conn.execute(
            "INSERT INTO trust_charges (id, pmc_id, lease_id, due_on, kind, amount,"
            " recurring_charge_id) VALUES ('00000000-0000-4000-8000-000000000001', %s, %s, %s,"
            " 'fee', 35.00, %s) RETURNING id",
            (pmc.pmc_id, lease, MAR_1, monthly),
        ).fetchone()[0]
        rent = conn.execute(
            "INSERT INTO trust_charges (id, pmc_id, lease_id, due_on, kind, amount)"
            " VALUES ('ffffffff-0000-4000-8000-000000000001', %s, %s, %s, 'rent', 1000.00)"
            " RETURNING id",
            (pmc.pmc_id, lease, MAR_1),
        ).fetchone()[0]
    charge(conn, pmc.pmc_id, lease, FEB_1, "20.00", "credit", "Courtesy")

    assert open_of(conn, pmc, lease) == {rent: Decimal("980.00"), pet: Decimal("35.00")}


def test_a_deposit_kept_pays_what_is_left_of_a_partly_paid_rent(conn, pmc, tenant):
    lease = new_lease(conn, pmc, tenant, date(2026, 2, 28))
    january = charge(conn, pmc.pmc_id, lease, JAN_1, "1000.00", "rent")
    february = charge(conn, pmc.pmc_id, lease, FEB_1, "1000.00", "rent")
    (paid,) = transfer_batch(
        conn,
        [(pmc.operating_cash, pmc.owners[0].account, "600.00")],
        datetime(2026, 1, 5, tzinfo=UTC),
    )
    conn.execute("SELECT trust_apply_payment(%s, %s, %s, 600.00)", (pmc.pmc_id, january, paid))
    transfer_batch(
        conn, [(pmc.deposit_cash, pmc.tenant_deposit, "900.00")], datetime(2026, 1, 1, tzinfo=UTC)
    )

    return_deposit(conn, pmc.pmc_id, lease, "move-out", datetime(2026, 3, 5, tzinfo=UTC))

    assert conn.execute(
        "SELECT charge_id, amount FROM trust_deposit_return_lines WHERE lease_id = %s"
        " ORDER BY line_no",
        (lease,),
    ).fetchall() == [(january, Decimal("400.00")), (february, Decimal("500.00"))]
