"""Rent automation: the monthly rent run and late fees (migration 20261010000019).

trust_charge_rent_due charges every lease in effect on a due date its rent, once per lease and
date. trust_assess_late_fees charges each rent still unpaid at the end of its last day of grace
its late fee, once, under the property's terms in force on the rent's due date.
"""

from datetime import UTC, date, datetime
from decimal import Decimal

import psycopg
import pytest
from helpers import (
    add_late_fee_policy,
    add_unit,
    apply_payment,
    assess_late_fees,
    charge,
    charge_rent_due,
    end_lease,
    in_background,
    make_pmc,
    open_lease,
    reverse_payment,
    transfer_batch,
    wait_until_blocked,
)

JAN_1, FEB_1, MAR_1 = date(2026, 1, 1), date(2026, 2, 1), date(2026, 3, 1)
RENT = Decimal("1500.00")


def at(day, hour=12, minute=0, second=0):
    return datetime(day.year, day.month, day.day, hour, minute, second, tzinfo=UTC)


@pytest.fixture
def pmc(conn):
    return make_pmc(conn)


def tenant_of(conn, pmc):
    return conn.execute(
        "SELECT tenant_id FROM trust_ledger_accounts WHERE ledger_account_id = %s",
        (pmc.tenant_deposit,),
    ).fetchone()[0]


def lease_on(conn, pmc, starts_on, ends_on=None, rent=RENT, name="Unit 1"):
    """A lease of a new unit of Property 1."""
    unit = add_unit(conn, pmc.pmc_id, pmc.owners[0].property_id, name)
    return open_lease(conn, pmc.pmc_id, unit, starts_on, ends_on, rent, [tenant_of(conn, pmc)])


def charges(conn, lease, kind=None):
    return conn.execute(
        "SELECT due_on, kind, amount, memo FROM trust_charges WHERE lease_id = %s"
        " AND (%s::text IS NULL OR kind = %s) ORDER BY due_on, kind",
        (lease, kind, kind),
    ).fetchall()


def pay(conn, pmc, charge_id, amount, when):
    """Pay amount of a charge into Owner 1's account at `when` and match it; the transfer."""
    (paid,) = transfer_batch(conn, [(pmc.operating_cash, pmc.owners[0].account, amount)], when)
    apply_payment(conn, pmc.pmc_id, charge_id, paid, amount)
    return paid


def rent_due(conn, lease, due_on):
    return conn.execute(
        "SELECT id FROM trust_charges WHERE lease_id = %s AND kind = 'rent' AND due_on = %s",
        (lease, due_on),
    ).fetchone()[0]


# --- the rent run -------------------------------------------------------------------------


def test_the_run_charges_every_lease_in_effect_its_rent(conn, app_conn, pmc):
    first = lease_on(conn, pmc, JAN_1, rent="1500.00", name="Unit 1")
    second = lease_on(conn, pmc, JAN_1, rent="980.50", name="Unit 2")

    assert charge_rent_due(app_conn, pmc.pmc_id, FEB_1) == 2

    assert charges(conn, first) == [(FEB_1, "rent", RENT, "Rent due 2026-02-01")]
    assert charges(conn, second) == [(FEB_1, "rent", Decimal("980.50"), "Rent due 2026-02-01")]


def test_only_leases_in_effect_on_the_due_date_are_charged(conn, pmc):
    starts_that_day = lease_on(conn, pmc, FEB_1, name="Unit 1")
    starts_after = lease_on(conn, pmc, date(2026, 2, 2), name="Unit 2")
    ends_that_day = lease_on(conn, pmc, JAN_1, FEB_1, name="Unit 3")
    ended_before = lease_on(conn, pmc, JAN_1, date(2026, 1, 31), name="Unit 4")
    month_to_month = lease_on(conn, pmc, JAN_1, name="Unit 5")

    assert charge_rent_due(conn, pmc.pmc_id, FEB_1) == 3
    charged = {
        lease: bool(charges(conn, lease))
        for lease in (starts_that_day, starts_after, ends_that_day, ended_before, month_to_month)
    }
    assert charged == {
        starts_that_day: True,
        starts_after: False,
        ends_that_day: True,
        ended_before: False,
        month_to_month: True,
    }


def test_running_again_charges_nothing_more(conn, pmc):
    lease = lease_on(conn, pmc, JAN_1)
    by_hand = lease_on(conn, pmc, JAN_1, name="Unit 2")
    charge(conn, pmc.pmc_id, by_hand, FEB_1, "1400.00")  # this month's rent, entered by hand

    assert charge_rent_due(conn, pmc.pmc_id, FEB_1) == 1
    assert charge_rent_due(conn, pmc.pmc_id, FEB_1) == 0
    assert len(charges(conn, lease)) == 1
    assert charges(conn, by_hand) == [(FEB_1, "rent", Decimal("1400.00"), None)]


def test_the_run_charges_only_its_own_pmcs_leases(conn, pmc):
    other = make_pmc(conn)
    theirs = lease_on(conn, other, JAN_1)

    assert charge_rent_due(conn, pmc.pmc_id, FEB_1) == 0
    assert charges(conn, theirs) == []


def test_the_run_needs_a_due_date(conn, pmc):
    with pytest.raises(psycopg.errors.InvalidParameterValue, match="due date"):
        charge_rent_due(conn, pmc.pmc_id, None)


@pytest.mark.timing
def test_two_runs_at_once_charge_each_lease_once(conn, database_url, pmc):
    lease = lease_on(conn, pmc, JAN_1)

    with psycopg.connect(database_url) as first:
        assert charge_rent_due(first, pmc.pmc_id, FEB_1) == 1  # not committed yet
        thread, pid, outcome = in_background(
            database_url, lambda worker: charge_rent_due(worker, pmc.pmc_id, FEB_1)
        )
        wait_until_blocked(conn, pid)
        first.commit()
    thread.join(timeout=30)

    assert outcome == {"result": 0}
    assert len(charges(conn, lease)) == 1


# --- late fee terms ------------------------------------------------------------------------


def test_the_app_records_late_fee_terms(conn, app_conn, pmc):
    policy = add_late_fee_policy(
        app_conn, pmc.pmc_id, pmc.owners[0].property_id, JAN_1, 5, "50", "10", "75"
    )

    assert conn.execute(
        "SELECT grace_days, flat_fee, percent, maximum FROM trust_late_fee_policies WHERE id = %s",
        (policy,),
    ).fetchone() == (5, Decimal("50"), Decimal("10"), Decimal("75"))


@pytest.mark.parametrize(
    "terms",
    [
        {"grace_days": -1},
        {"grace_days": 366},
        {"flat_fee": "-1"},
        {"percent": "-0.01"},
        {"percent": "100.01"},
        {"maximum": "-1"},
    ],
)
def test_late_fee_terms_stay_in_range(conn, pmc, terms):
    values = {"grace_days": 5, "flat_fee": "0", "percent": "0", "maximum": None} | terms

    with pytest.raises(psycopg.errors.CheckViolation):
        add_late_fee_policy(conn, pmc.pmc_id, pmc.owners[0].property_id, JAN_1, **values)


def test_late_fee_terms_are_for_a_property_of_their_pmc_once_per_day(conn, pmc):
    add_late_fee_policy(conn, pmc.pmc_id, pmc.owners[0].property_id, JAN_1, 5)

    with pytest.raises(psycopg.errors.UniqueViolation):
        add_late_fee_policy(conn, pmc.pmc_id, pmc.owners[0].property_id, JAN_1, 3)
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        add_late_fee_policy(conn, pmc.pmc_id, make_pmc(conn).owners[0].property_id, JAN_1, 5)


# --- late fees -----------------------------------------------------------------------------


@pytest.fixture
def late(conn, pmc):
    """A lease of Property 1 at 1500.00 from Jan 1, rent charged for Feb 1, and late fee terms
    of 5 days' grace, 50.00 plus 10% of what is unpaid. (lease, February's rent charge)."""
    lease = lease_on(conn, pmc, JAN_1)
    add_late_fee_policy(conn, pmc.pmc_id, pmc.owners[0].property_id, JAN_1, 5, "50", "10")
    charge_rent_due(conn, pmc.pmc_id, FEB_1)
    return lease, rent_due(conn, lease, FEB_1)


def late_fees(conn, rent):
    return conn.execute(
        "SELECT unpaid, fee FROM trust_late_fees WHERE rent_charge_id = %s", (rent,)
    ).fetchall()


def test_rent_unpaid_when_its_grace_runs_out_is_charged_a_late_fee(conn, app_conn, pmc, late):
    lease, rent = late

    assert assess_late_fees(app_conn, pmc.pmc_id, date(2026, 2, 7)) == 1

    # 50.00 + 10% of 1500.00 = 200.00, due the first day the rent is late (Feb 7).
    assert charges(conn, lease, "fee") == [
        (date(2026, 2, 7), "fee", Decimal("200.00"), "Late fee, rent due 2026-02-01")
    ]
    assert late_fees(conn, rent) == [(RENT, Decimal("200.00"))]


def test_no_fee_until_the_grace_has_run_out(conn, pmc, late):
    # Due Feb 1, five days' grace: Feb 6 is the last day of grace, so the rent is late only
    # in a run as of Feb 7 or later.
    lease, _ = late

    assert assess_late_fees(conn, pmc.pmc_id, date(2026, 2, 6)) == 0
    assert charges(conn, lease, "fee") == []
    assert assess_late_fees(conn, pmc.pmc_id, date(2026, 2, 7)) == 1


@pytest.mark.parametrize(
    ("paid_at", "fee"),
    [
        (at(date(2026, 2, 6), 23, 59, 59), []),  # the last moment of grace: on time
        (at(date(2026, 2, 7), 0, 0, 0), [Decimal("200.00")]),  # the first late moment
    ],
)
def test_a_payment_counts_only_if_dated_before_the_grace_runs_out(conn, pmc, late, paid_at, fee):
    lease, rent = late
    pay(conn, pmc, rent, RENT, paid_at)

    assess_late_fees(conn, pmc.pmc_id, date(2026, 2, 20))

    assert [row[2] for row in charges(conn, lease, "fee")] == fee


def test_the_percent_is_of_what_is_left_unpaid(conn, pmc, late):
    lease, rent = late
    pay(conn, pmc, rent, "1000.00", at(date(2026, 2, 3)))

    assess_late_fees(conn, pmc.pmc_id, date(2026, 2, 7))

    assert late_fees(conn, rent) == [(Decimal("500.00"), Decimal("100.00"))]  # 50 + 10% of 500


def test_a_payment_that_bounced_before_the_grace_ran_out_does_not_count(conn, pmc, late):
    lease, rent = late
    paid = pay(conn, pmc, rent, RENT, at(date(2026, 2, 2)))
    (back,) = transfer_batch(
        conn, [(pmc.owners[0].account, pmc.operating_cash, "300.00")], at(date(2026, 2, 4))
    )
    reverse_payment(conn, pmc.pmc_id, rent, paid, back, "300.00")

    assess_late_fees(conn, pmc.pmc_id, date(2026, 2, 7))

    assert late_fees(conn, rent) == [(Decimal("300.00"), Decimal("80.00"))]  # 50 + 10% of 300


def test_a_bounce_after_the_grace_ran_out_changes_nothing(conn, pmc, late):
    lease, rent = late
    paid = pay(conn, pmc, rent, RENT, at(date(2026, 2, 2)))
    (back,) = transfer_batch(
        conn, [(pmc.owners[0].account, pmc.operating_cash, RENT)], at(date(2026, 2, 7), 0, 0)
    )
    reverse_payment(conn, pmc.pmc_id, rent, paid, back, RENT)

    assert assess_late_fees(conn, pmc.pmc_id, date(2026, 2, 20)) == 0


@pytest.mark.parametrize(
    ("maximum", "fee"),
    [(None, Decimal("200.00")), ("75", Decimal("75.00")), ("500", Decimal("200.00"))],
)
def test_the_cap_limits_the_fee(conn, pmc, maximum, fee):
    lease = lease_on(conn, pmc, JAN_1)
    add_late_fee_policy(conn, pmc.pmc_id, pmc.owners[0].property_id, JAN_1, 5, "50", "10", maximum)
    charge_rent_due(conn, pmc.pmc_id, FEB_1)

    assess_late_fees(conn, pmc.pmc_id, date(2026, 2, 7))

    assert [row[2] for row in charges(conn, lease, "fee")] == [fee]


@pytest.mark.parametrize(
    ("flat", "percent", "maximum"), [("0", "0", None), ("50", "10", "0"), ("0", "0.0003", None)]
)
def test_a_fee_of_nothing_is_not_charged(conn, pmc, flat, percent, maximum):
    # The last one rounds to 0.00: 0.0003% of 1500.00 is 0.0045.
    lease = lease_on(conn, pmc, JAN_1)
    add_late_fee_policy(
        conn, pmc.pmc_id, pmc.owners[0].property_id, JAN_1, 5, flat, percent, maximum
    )
    charge_rent_due(conn, pmc.pmc_id, FEB_1)

    assert assess_late_fees(conn, pmc.pmc_id, date(2026, 2, 7)) == 0
    assert charges(conn, lease, "fee") == []


def test_a_fee_rounds_to_the_cent(conn, pmc):
    lease = lease_on(conn, pmc, JAN_1, rent="1234.56")
    add_late_fee_policy(conn, pmc.pmc_id, pmc.owners[0].property_id, JAN_1, 0, "0", "3.333")
    charge_rent_due(conn, pmc.pmc_id, FEB_1)

    assess_late_fees(conn, pmc.pmc_id, date(2026, 2, 2))

    # 3.333% of 1234.56 = 41.1478848
    assert [row[2] for row in charges(conn, lease, "fee")] == [Decimal("41.15")]


def test_each_rent_is_charged_one_late_fee(conn, pmc, late):
    lease, _ = late

    assert assess_late_fees(conn, pmc.pmc_id, date(2026, 2, 7)) == 1
    assert assess_late_fees(conn, pmc.pmc_id, date(2026, 2, 7)) == 0
    assert assess_late_fees(conn, pmc.pmc_id, date(2026, 2, 28)) == 0
    assert len(charges(conn, lease, "fee")) == 1


def test_the_terms_in_force_on_the_rents_due_date_apply(conn, pmc):
    lease = lease_on(conn, pmc, JAN_1)
    property_id = pmc.owners[0].property_id
    add_late_fee_policy(conn, pmc.pmc_id, property_id, JAN_1, 5, "50")
    add_late_fee_policy(conn, pmc.pmc_id, property_id, date(2026, 2, 2), 0, "99")
    for due_on in (FEB_1, MAR_1):
        charge_rent_due(conn, pmc.pmc_id, due_on)

    assert assess_late_fees(conn, pmc.pmc_id, date(2026, 3, 10)) == 2
    assert [(row[0], row[2]) for row in charges(conn, lease, "fee")] == [
        (date(2026, 2, 7), Decimal("50.00")),  # February's rent: January's terms
        (date(2026, 3, 2), Decimal("99.00")),  # March's rent: the new terms, no grace
    ]


def test_without_terms_nothing_is_late(conn, pmc):
    lease = lease_on(conn, pmc, JAN_1)
    charge_rent_due(conn, pmc.pmc_id, FEB_1)
    # Terms that start after the rent was due don't reach back to it.
    add_late_fee_policy(conn, pmc.pmc_id, pmc.owners[0].property_id, date(2026, 2, 2), 0, "50")

    assert assess_late_fees(conn, pmc.pmc_id, date(2026, 12, 31)) == 0
    assert charges(conn, lease, "fee") == []


def test_only_rent_is_charged_late_fees(conn, pmc):
    lease = lease_on(conn, pmc, JAN_1)
    add_late_fee_policy(conn, pmc.pmc_id, pmc.owners[0].property_id, JAN_1, 0, "50")
    charge(conn, pmc.pmc_id, lease, FEB_1, "35.00", "fee")
    charge(conn, pmc.pmc_id, lease, FEB_1, "20.00", "credit")

    assert assess_late_fees(conn, pmc.pmc_id, date(2026, 3, 1)) == 0


def test_another_pmcs_rent_is_not_assessed(conn, pmc, late):
    other = make_pmc(conn)

    assert assess_late_fees(conn, other.pmc_id, date(2026, 2, 28)) == 0
    assert late_fees(conn, late[1]) == []


@pytest.mark.parametrize("isolation", ["REPEATABLE READ", "SERIALIZABLE"])
def test_late_fees_are_assessed_at_read_committed(database_url, pmc, late, isolation):
    with psycopg.connect(database_url) as tx:
        tx.execute(f"SET TRANSACTION ISOLATION LEVEL {isolation}")
        with pytest.raises(psycopg.errors.InvalidTransactionState, match="READ COMMITTED"):
            assess_late_fees(tx, pmc.pmc_id, date(2026, 2, 7))


def test_late_fees_need_a_day(conn, pmc):
    with pytest.raises(psycopg.errors.InvalidParameterValue, match="as of a day"):
        assess_late_fees(conn, pmc.pmc_id, None)


@pytest.mark.timing
def test_two_runs_at_once_charge_one_late_fee(conn, database_url, pmc, late):
    lease, _ = late

    with psycopg.connect(database_url) as first:
        assert assess_late_fees(first, pmc.pmc_id, date(2026, 2, 7)) == 1  # not committed yet
        thread, pid, outcome = in_background(
            database_url, lambda worker: assess_late_fees(worker, pmc.pmc_id, date(2026, 2, 7))
        )
        wait_until_blocked(conn, pid)
        first.commit()
    thread.join(timeout=30)

    assert outcome == {"result": 0}
    assert len(charges(conn, lease, "fee")) == 1


@pytest.mark.timing
def test_a_payment_matched_while_fees_are_assessed_waits_for_them(conn, database_url, pmc, late):
    # The run locks the rent charge; matching a payment to it waits, so the run's unpaid amount
    # and the matches committed afterwards never disagree mid-way.
    _, rent = late
    (paid,) = transfer_batch(
        conn, [(pmc.operating_cash, pmc.owners[0].account, RENT)], at(date(2026, 2, 3))
    )

    with psycopg.connect(database_url) as first:
        assess_late_fees(first, pmc.pmc_id, date(2026, 2, 7))  # not committed yet
        thread, pid, outcome = in_background(
            database_url, lambda worker: apply_payment(worker, pmc.pmc_id, rent, paid, RENT)
        )
        wait_until_blocked(conn, pid)
        first.commit()
    thread.join(timeout=30)

    assert outcome == {"result": RENT}
    assert late_fees(conn, rent) == [(RENT, Decimal("200.00"))]


@pytest.mark.parametrize(
    "statement",
    ["UPDATE trust_late_fee_policies SET grace_days = 9", "DELETE FROM trust_late_fees"],
)
def test_late_fee_terms_and_late_fees_are_kept_for_good(conn, pmc, late, statement):
    assess_late_fees(conn, pmc.pmc_id, date(2026, 2, 7))

    with pytest.raises(psycopg.errors.RestrictViolation, match="append-only"):
        conn.execute(statement)


def test_a_late_fee_is_paid_like_any_charge(conn, pmc, late):
    lease, _ = late
    assess_late_fees(conn, pmc.pmc_id, date(2026, 2, 7))
    (fee,) = conn.execute(
        "SELECT id FROM trust_charges WHERE lease_id = %s AND kind = 'fee'", (lease,)
    ).fetchone()

    pay(conn, pmc, fee, "200.00", at(date(2026, 2, 8)))

    assert conn.execute(
        "SELECT sum(amount) FROM trust_charge_payments WHERE charge_id = %s", (fee,)
    ).fetchone() == (Decimal("200.00"),)


def test_an_ended_lease_keeps_rent_charged_before_it_ended(conn, pmc):
    lease = lease_on(conn, pmc, JAN_1)
    charge_rent_due(conn, pmc.pmc_id, FEB_1)
    end_lease(conn, pmc.pmc_id, lease, date(2026, 2, 15))

    assert charge_rent_due(conn, pmc.pmc_id, MAR_1) == 0
    assert [row[0] for row in charges(conn, lease)] == [FEB_1]
