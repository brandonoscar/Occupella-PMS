"""Late fees on rent paid online (migration 20261113000100, docs/edge-cases.md D-080, D-081).

A bank payment takes days to settle, and the PMS records it on the day the partner reports it
paid. For late fees a payment online counts from the day the tenant started it (or their autopay
did): while one started by the end of a rent's grace is on its way, whether the rent is late
waits; once it lands, it counts as paid on the day it was started; if it fails, expires or is given
up, the rent is late as it would be with no payment; if it comes back after it was paid, that is a
bounce, as a staff payment's is."""

from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

import pytest
from helpers import (
    add_late_fee_policy,
    add_nsf_fee_policy,
    add_unit,
    assess_late_fees,
    charge,
    hold_payments,
    make_pmc,
    open_lease,
)

from pms import autopay, payments
from pms.api import online_payments as op

TODAY = datetime.now(UTC).date()
DAY = timedelta(days=1)
DUE = TODAY - 12 * DAY
RENT = Decimal("1200.00")


@pytest.fixture
def pmc(conn):
    return make_pmc(conn, owners=1)


@pytest.fixture
def tenant(conn, pmc):
    return conn.execute(
        "SELECT tenant_id FROM trust_ledger_accounts WHERE ledger_account_id = %s",
        (pmc.prepaid_rent,),
    ).fetchone()[0]


def lease_owing(conn, pmc, tenant, due=DUE, grace=3):
    """A lease with a 1,200.00 rent due on `due`, its property's late fee 50.00 after `grace`
    days."""
    add_late_fee_policy(conn, pmc.pmc_id, pmc.owners[0].property_id, due - 60 * DAY, grace, "50")
    unit = add_unit(conn, pmc.pmc_id, pmc.owners[0].property_id)
    lease = open_lease(conn, pmc.pmc_id, unit, due - 30 * DAY, None, "1200.00", [tenant])
    return lease, charge(conn, pmc.pmc_id, lease, due, RENT)


def started(conn, pmc, lease, tenant, at, amount=RENT, ref=None) -> UUID:
    """A payment online the tenant started at `at`."""
    payment = uuid4()
    conn.execute(
        "INSERT INTO trust_online_payments (id, pmc_id, lease_id, tenant_id, amount, tenant_fee,"
        " fee_payer, method, partner, partner_ref, created_at)"
        " VALUES (%s, %s, %s, %s, %s, 0, 'company', 'ach', 'fake', %s, %s)",
        (payment, pmc.pmc_id, lease, tenant, amount, ref or f"fake_{payment.hex}", at),
    )
    return payment


def reports(conn, payment, kind, at):
    fee = Decimal("0.00") if kind == "paid" else None
    assert op.apply(conn, payments.Event(payment, kind, f"evt_{uuid4().hex}", at, fee), "fake")


def noon(day: date) -> datetime:
    return datetime.combine(day, time(12), UTC)


def late_fees(conn, rent):
    return conn.execute(
        "SELECT unpaid, fee FROM trust_late_fees WHERE rent_charge_id = %s", (rent,)
    ).fetchall()


def fee_charges(conn, lease):
    return conn.execute(
        "SELECT due_on, amount FROM trust_charges WHERE lease_id = %s AND kind = 'fee'"
        " ORDER BY due_on, amount",
        (lease,),
    ).fetchall()


def test_rent_paid_online_on_the_due_day_that_settles_after_the_grace_is_not_late(
    conn, pmc, tenant
):
    """D-080: paid on the due day, reported paid five days later, two days past the grace."""
    lease, rent = lease_owing(conn, pmc, tenant)
    payment = started(conn, pmc, lease, tenant, noon(DUE))
    # Each night while it settles: nothing charged, however the grace ran out.
    for night in (DUE + 4 * DAY, DUE + 5 * DAY):
        assert assess_late_fees(conn, pmc.pmc_id, night) == 0
    reports(conn, payment, "paid", noon(DUE + 5 * DAY))
    paid_on = conn.execute(
        "SELECT (t.event_at AT TIME ZONE 'UTC')::date FROM pgledger_transfers AS t"
        " WHERE t.metadata ->> 'key' = %s",
        (op.payment_key(payment),),
    ).fetchone()[0]
    # The books still date the money on the day the bank paid it...
    assert paid_on == DUE + 5 * DAY
    # ...and the rent it paid is not late: the run passes it over without working out what is
    # open of it, as a rent its own payments covered in time.
    with conn.transaction():
        conn.execute("SET LOCAL track_functions = 'all'")
        assert assess_late_fees(conn, pmc.pmc_id, DUE + 6 * DAY) == 0
        worked_out = conn.execute(
            "SELECT coalesce(sum(calls), 0) FROM pg_stat_xact_user_functions"
            " WHERE funcname = 'trust_open_charges'"
        ).fetchone()[0]
    assert worked_out == 0
    assert late_fees(conn, rent) == []


def test_part_paid_online_in_time_is_late_only_for_what_it_left(conn, pmc, tenant):
    """A payment of part of the rent started in time counts for that part: the fee, flat here,
    records only what it left open."""
    add_late_fee_policy(conn, pmc.pmc_id, pmc.owners[0].property_id, DUE - 90 * DAY, 3, "0", "10")
    unit = add_unit(conn, pmc.pmc_id, pmc.owners[0].property_id)
    lease = open_lease(conn, pmc.pmc_id, unit, DUE - 30 * DAY, None, "1200.00", [tenant])
    rent = charge(conn, pmc.pmc_id, lease, DUE, RENT)
    payment = started(conn, pmc, lease, tenant, noon(DUE + DAY), Decimal("1000.00"))
    reports(conn, payment, "paid", noon(DUE + 6 * DAY))
    assert assess_late_fees(conn, pmc.pmc_id, DUE + 7 * DAY) == 1
    assert late_fees(conn, rent) == [(Decimal("200.00"), Decimal("20.00"))]


def test_a_payment_failing_after_the_grace_is_late_from_the_end_of_the_grace(conn, pmc, tenant):
    """While it settles nothing is charged; once it fails the rent was never paid, and its late
    fee is charged, due the day after the grace as always."""
    lease, rent = lease_owing(conn, pmc, tenant)
    payment = started(conn, pmc, lease, tenant, noon(DUE))
    assert assess_late_fees(conn, pmc.pmc_id, DUE + 5 * DAY) == 0
    reports(conn, payment, "failed", noon(DUE + 6 * DAY))
    assert assess_late_fees(conn, pmc.pmc_id, DUE + 7 * DAY) == 1
    assert late_fees(conn, rent) == [(RENT, Decimal("50.00"))]
    assert fee_charges(conn, lease) == [(DUE + 4 * DAY, Decimal("50.00"))]


@pytest.mark.parametrize("ends", ["expired", "given up"])
def test_a_payment_that_never_reached_the_bank_holds_nothing_back(conn, pmc, tenant, ends):
    """Left unpaid on the partner's page (it expires), or an autopay payment the PMS gave up
    sending: no longer on its way, so the rent is late."""
    lease, rent = lease_owing(conn, pmc, tenant)
    payment = started(conn, pmc, lease, tenant, noon(DUE))
    if ends == "expired":
        reports(conn, payment, "expired", noon(DUE + DAY))
    else:
        op.apply(conn, payments.Event(payment, "expired", autopay.GAVE_UP, noon(DUE + DAY)), "fake")
    assert assess_late_fees(conn, pmc.pmc_id, DUE + 5 * DAY) == 1
    assert late_fees(conn, rent) == [(RENT, Decimal("50.00"))]


def test_a_payment_that_came_back_after_the_grace_is_a_bounce(conn, pmc, tenant):
    """Paid after the grace, then returned: it counted as paid in time, as a staff payment that
    bounced after the grace does, so no late fee; the property's NSF fee is charged."""
    add_nsf_fee_policy(conn, pmc.pmc_id, pmc.owners[0].property_id, DUE - 60 * DAY, "35")
    lease, rent = lease_owing(conn, pmc, tenant)
    payment = started(conn, pmc, lease, tenant, noon(DUE + DAY))
    reports(conn, payment, "paid", noon(DUE + 5 * DAY))
    reports(conn, payment, "returned", noon(DUE + 8 * DAY))
    assert assess_late_fees(conn, pmc.pmc_id, DUE + 9 * DAY) == 0
    assert late_fees(conn, rent) == []
    assert fee_charges(conn, lease) == [(DUE + 8 * DAY, Decimal("35.00"))]


def test_a_payment_that_came_back_within_the_grace_leaves_the_rent_late(conn, pmc, tenant):
    lease, rent = lease_owing(conn, pmc, tenant)
    payment = started(conn, pmc, lease, tenant, noon(DUE))
    reports(conn, payment, "paid", noon(DUE + DAY))
    reports(conn, payment, "returned", noon(DUE + 2 * DAY))
    assert assess_late_fees(conn, pmc.pmc_id, DUE + 5 * DAY) == 1
    assert late_fees(conn, rent) == [(RENT, Decimal("50.00"))]


def test_a_payment_started_after_the_grace_is_late_as_before(conn, pmc, tenant):
    lease, rent = lease_owing(conn, pmc, tenant)
    payment = started(conn, pmc, lease, tenant, noon(DUE + 4 * DAY))
    # On its way, but started too late to count: the run charges the fee now.
    assert assess_late_fees(conn, pmc.pmc_id, DUE + 5 * DAY) == 1
    reports(conn, payment, "paid", noon(DUE + 6 * DAY))
    assert late_fees(conn, rent) == [(RENT, Decimal("50.00"))]


def test_a_payment_with_no_word_for_two_weeks_holds_nothing_back(conn, pmc, tenant):
    """One the partner has said nothing of for 14 days is no longer counted as on its way, as
    autopay and Banking count it (op.NO_WORD_DAYS): the rent is late until its report comes."""
    long_ago = TODAY - 30 * DAY
    lease, rent = lease_owing(conn, pmc, tenant, due=long_ago)
    started(conn, pmc, lease, tenant, noon(long_ago))
    assert assess_late_fees(conn, pmc.pmc_id, TODAY) == 1
    assert late_fees(conn, rent) == [(RENT, Decimal("50.00"))]


def test_a_payment_reported_paid_that_waits_for_staff_holds_the_fee(conn, pmc, tenant):
    """Paid during a payment hold, it waits on Banking for staff, however long: the bank paid
    it, so whether the rent is late waits too."""
    lease, rent = lease_owing(conn, pmc, tenant, due=TODAY - 40 * DAY)
    hold_payments(conn, pmc.pmc_id, lease, TODAY - 45 * DAY)
    payment = started(conn, pmc, lease, tenant, noon(TODAY - 40 * DAY))
    reports(conn, payment, "paid", noon(TODAY - 38 * DAY))
    assert not op.posted(conn, pmc.pmc_id, op.payment_key(payment))
    assert assess_late_fees(conn, pmc.pmc_id, TODAY) == 0
    assert late_fees(conn, rent) == []


def test_started_counts_by_the_companys_day(conn):
    """Paid at 9 pm in New York on the last day of the grace (the next day in UTC): in time."""
    pmc = make_pmc(conn, owners=1, time_zone="America/New_York")
    tenant = conn.execute(
        "SELECT tenant_id FROM trust_ledger_accounts WHERE ledger_account_id = %s",
        (pmc.prepaid_rent,),
    ).fetchone()[0]
    lease, rent = lease_owing(conn, pmc, tenant)
    evening = datetime.combine(DUE + 3 * DAY, time(21), ZoneInfo("America/New_York"))
    assert evening.astimezone(UTC).date() == DUE + 4 * DAY
    payment = started(conn, pmc, lease, tenant, evening)
    reports(conn, payment, "paid", noon(DUE + 7 * DAY))
    assert assess_late_fees(conn, pmc.pmc_id, DUE + 8 * DAY) == 0
    assert late_fees(conn, rent) == []


def test_an_autopay_payment_started_on_the_due_day_is_not_late(conn, app_conn, pmc, tenant):
    """D-081: autopay starts its payment on the rent's due day, and the bank settles it two to
    four business days later, past a short grace: it counts from the day autopay started it."""
    month = TODAY.replace(day=1)
    grace = (TODAY - month).days  # the grace ends today, whatever day of the month it is
    add_late_fee_policy(conn, pmc.pmc_id, pmc.owners[0].property_id, month - 60 * DAY, grace, "50")
    unit = add_unit(conn, pmc.pmc_id, pmc.owners[0].property_id)
    lease = open_lease(conn, pmc.pmc_id, unit, month - 60 * DAY, None, "1200.00", [tenant])
    rent = charge(conn, pmc.pmc_id, lease, month, RENT)
    conn.execute(
        "INSERT INTO trust_payment_settings (pmc_id, online, ach_fee_payer)"
        " VALUES (%s, true, 'company')",
        (pmc.pmc_id,),
    )
    mine = conn.execute(
        "INSERT INTO trust_autopay (pmc_id, lease_id, tenant_id, rule, amount, partner)"
        " VALUES (%s, %s, %s, 'balance', '2000.00', 'fake') RETURNING id",
        (pmc.pmc_id, lease, tenant),
    ).fetchone()[0]
    conn.execute(
        "INSERT INTO trust_autopay_accounts (pmc_id, autopay_id, partner_customer,"
        " partner_method, label, partner_event, saved_at)"
        " VALUES (%s, %s, 'fake_cus', 'fake_pm', 'Test bank ••••0000', %s, %s)",
        (pmc.pmc_id, mine, f"evt_{mine.hex}", datetime.now(UTC) - 90 * DAY),
    )
    with app_conn.transaction():
        assert autopay.start_due(app_conn, pmc.pmc_id, TODAY, "fake").payments == 1
    payment = conn.execute(
        "SELECT payment_id FROM trust_autopay_runs WHERE autopay_id = %s", (mine,)
    ).fetchone()[0]
    # The nights while the bank settles it, the grace over: nothing charged.
    assert assess_late_fees(conn, pmc.pmc_id, TODAY + 2 * DAY) == 0
    reports(conn, payment, "paid", noon(TODAY + 3 * DAY))
    assert assess_late_fees(conn, pmc.pmc_id, TODAY + 4 * DAY) == 0
    assert late_fees(conn, rent) == []
