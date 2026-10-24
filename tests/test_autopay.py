"""Autopay (migration 0151, src/pms/autopay.py): each month on the lease's rent due day, a saved
autopay starts its payment once (or records why none), stops by itself when it should, and each
payment it starts goes to the payments partner once, however often that is tried."""

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from uuid import UUID

import psycopg
import pytest
from helpers import (
    add_tenant,
    add_unit,
    charge,
    end_lease,
    hold_payments,
    make_pmc,
    open_lease,
)

from pms import autopay, payments

TODAY = datetime.now(UTC).date()
MONTH = TODAY.replace(day=1)
DAY = timedelta(days=1)
LONG_AGO = datetime.now(UTC) - timedelta(days=90)


@pytest.fixture
def pmc(conn):
    return make_pmc(conn, owners=1)


@pytest.fixture
def tenant(conn, pmc):
    return conn.execute(
        "SELECT tenant_id FROM trust_ledger_accounts WHERE ledger_account_id = %s",
        (pmc.prepaid_rent,),
    ).fetchone()[0]


@pytest.fixture
def lease(conn, pmc, tenant):
    """A lease due on the first, this month's 1,200.00 charged."""
    unit = add_unit(conn, pmc.pmc_id, pmc.owners[0].property_id)
    made = open_lease(conn, pmc.pmc_id, unit, MONTH - 60 * DAY, None, "1200.00", [tenant])
    charge(conn, pmc.pmc_id, made, MONTH, "1200.00")
    return made


def online(conn, pmc, on=True, payer="company"):
    conn.execute(
        "INSERT INTO trust_payment_settings (pmc_id, online, ach_fee_payer) VALUES (%s, %s, %s)",
        (pmc.pmc_id, on, payer),
    )


def set_up(conn, pmc, lease, tenant, rule="balance", amount="2000.00", saved_at=LONG_AGO):
    """An autopay, its bank account saved at `saved_at` (None: not saved)."""
    made = conn.execute(
        "INSERT INTO trust_autopay (pmc_id, lease_id, tenant_id, rule, amount, partner)"
        " VALUES (%s, %s, %s, %s, %s, 'fake') RETURNING id",
        (pmc.pmc_id, lease, tenant, rule, amount),
    ).fetchone()[0]
    if saved_at is not None:
        conn.execute(
            "INSERT INTO trust_autopay_accounts (pmc_id, autopay_id, partner_customer,"
            " partner_method, label, partner_event, saved_at)"
            " VALUES (%s, %s, 'fake_cus', 'fake_pm', 'Test bank ••••0000', %s, %s)",
            (pmc.pmc_id, made, f"evt_{made.hex}", saved_at),
        )
    return made


def run(app_conn, pmc, day=TODAY, partner="fake", sending=False, now=None):
    with app_conn.transaction():
        return autopay.start_due(
            app_conn, pmc.pmc_id, day, partner, sending, "http://testserver", now
        )


def runs(conn, autopay_id):
    return conn.execute(
        "SELECT r.due_on, p.amount, r.skipped FROM trust_autopay_runs AS r"
        " LEFT JOIN trust_online_payments AS p ON p.id = r.payment_id"
        " WHERE r.autopay_id = %s ORDER BY r.due_on",
        (autopay_id,),
    ).fetchall()


def stopped(conn, autopay_id):
    row = conn.execute(
        "SELECT why FROM trust_autopay_stops WHERE autopay_id = %s", (autopay_id,)
    ).fetchone()
    return None if row is None else row[0]


def report(conn, pmc, payment, kind):
    conn.execute(
        "INSERT INTO trust_online_payment_events (pmc_id, payment_id, kind, partner_event,"
        " partner_fee, happened_at) VALUES (%s, %s, %s, %s, %s, now())",
        (pmc.pmc_id, payment, kind, f"evt_{kind}_{payment.hex}", 0 if kind == "paid" else None),
    )


# --- starting what is due ---------------------------------------------------------------------


def test_a_balance_autopay_pays_what_is_owed_on_the_due_day_once(
    conn, app_conn, pmc, lease, tenant
):
    online(conn, pmc)
    mine = set_up(conn, pmc, lease, tenant)
    done = run(app_conn, pmc)
    assert (done.payments, done.skipped) == (1, 0)
    assert runs(conn, mine) == [(MONTH, Decimal("1200.00"), None)]
    payment = autopay.payment_id_for(mine, MONTH)
    row = conn.execute(
        "SELECT lease_id, tenant_id, amount, tenant_fee, partner, partner_ref"
        " FROM trust_online_payments WHERE id = %s",
        (payment,),
    ).fetchone()
    assert row == (
        lease,
        tenant,
        Decimal("1200.00"),
        Decimal("0"),
        "fake",
        f"autopay_{payment.hex}",
    )
    # A run again, the next day, or by hand starts nothing more for this due date.
    assert run(app_conn, pmc).payments == 0
    assert (
        run(app_conn, pmc, TODAY + DAY if TODAY.month == (TODAY + DAY).month else TODAY).payments
        == 0
    )
    assert len(runs(conn, mine)) == 1


def test_a_balance_autopay_pays_no_more_than_its_amount_or_than_is_owed_less_what_is_coming(
    conn, app_conn, pmc, lease, tenant
):
    online(conn, pmc, payer="tenant")
    # 500.00 on its way from a payment the tenant made by hand, not in the books yet.
    conn.execute(
        "INSERT INTO trust_online_payments (pmc_id, lease_id, tenant_id, amount, fee_payer, method,"
        " partner, partner_ref) VALUES (%s, %s, %s, 500, 'company', 'ach', 'fake', 'fake_by_hand')",
        (pmc.pmc_id, lease, tenant),
    )
    capped = set_up(conn, pmc, lease, tenant, amount="600.00")
    run(app_conn, pmc)
    assert runs(conn, capped) == [(MONTH, Decimal("600.00"), None)]
    fee = conn.execute(
        "SELECT tenant_fee FROM trust_online_payments WHERE id = %s",
        (autopay.payment_id_for(capped, MONTH),),
    ).fetchone()[0]
    assert fee == payments.tenant_fee(Decimal("600.00"))  # the company passes the fee on


def test_what_is_on_its_way_counts_and_a_failed_payment_does_not(
    conn, app_conn, pmc, lease, tenant
):
    online(conn, pmc)
    by_hand = conn.execute(
        "INSERT INTO trust_online_payments (pmc_id, lease_id, tenant_id, amount, fee_payer, method,"
        " partner, partner_ref) VALUES (%s, %s, %s, 300, 'company', 'ach', 'fake', 'fake_a')"
        " RETURNING id",
        (pmc.pmc_id, lease, tenant),
    ).fetchone()[0]
    report(conn, pmc, by_hand, "failed")
    conn.execute(
        "INSERT INTO trust_online_payments (pmc_id, lease_id, tenant_id, amount, fee_payer, method,"
        " partner, partner_ref) VALUES (%s, %s, %s, 200, 'company', 'ach', 'fake', 'fake_b')",
        (pmc.pmc_id, lease, tenant),
    )
    mine = set_up(conn, pmc, lease, tenant)
    run(app_conn, pmc)
    assert runs(conn, mine) == [(MONTH, Decimal("1000.00"), None)]


def test_a_payment_that_came_back_before_it_was_recorded_is_not_on_its_way(
    conn, app_conn, pmc, lease, tenant
):
    # Paid while the lease was on a payment hold (so never recorded), then it came back.
    online(conn, pmc)
    came_back = conn.execute(
        "INSERT INTO trust_online_payments (pmc_id, lease_id, tenant_id, amount, fee_payer, method,"
        " partner, partner_ref) VALUES (%s, %s, %s, 500, 'company', 'ach', 'fake', 'fake_c')"
        " RETURNING id",
        (pmc.pmc_id, lease, tenant),
    ).fetchone()[0]
    report(conn, pmc, came_back, "paid")
    report(conn, pmc, came_back, "returned")
    mine = set_up(conn, pmc, lease, tenant)
    run(app_conn, pmc)
    assert runs(conn, mine) == [(MONTH, Decimal("1200.00"), None)]


def test_a_payment_left_unpaid_on_the_partners_page_is_not_on_its_way(
    conn, app_conn, pmc, lease, tenant
):
    online(conn, pmc)
    left = conn.execute(
        "INSERT INTO trust_online_payments (pmc_id, lease_id, tenant_id, amount, fee_payer, method,"
        " partner, partner_ref) VALUES (%s, %s, %s, 400, 'company', 'ach', 'fake', 'fake_d')"
        " RETURNING id",
        (pmc.pmc_id, lease, tenant),
    ).fetchone()[0]
    report(conn, pmc, left, "expired")
    mine = set_up(conn, pmc, lease, tenant)
    run(app_conn, pmc)
    assert runs(conn, mine) == [(MONTH, Decimal("1200.00"), None)]


def test_a_fixed_autopay_pays_its_amount(conn, app_conn, pmc, lease, tenant):
    online(conn, pmc)
    mine = set_up(conn, pmc, lease, tenant, rule="fixed", amount="450.00")
    run(app_conn, pmc)
    assert runs(conn, mine) == [(MONTH, Decimal("450.00"), None)]


@pytest.mark.parametrize("why", ["payment hold", "nothing owed", "online payments off"])
def test_an_autopay_records_why_it_started_no_payment(conn, app_conn, pmc, tenant, why):
    unit = add_unit(conn, pmc.pmc_id, pmc.owners[0].property_id)
    lease = open_lease(conn, pmc.pmc_id, unit, MONTH - 60 * DAY, None, "1200.00", [tenant])
    online(conn, pmc, on=why != "online payments off")
    if why != "nothing owed":
        charge(conn, pmc.pmc_id, lease, MONTH, "1200.00")
    if why == "payment hold":
        hold_payments(conn, pmc.pmc_id, lease, MONTH - DAY)
    mine = set_up(conn, pmc, lease, tenant)
    done = run(app_conn, pmc)
    assert (done.payments, done.skipped) == (0, 1)
    assert runs(conn, mine) == [(MONTH, None, why)]


def test_no_partner_starts_and_records_nothing(conn, app_conn, pmc, lease, tenant):
    online(conn, pmc)
    mine = set_up(conn, pmc, lease, tenant)
    assert run(app_conn, pmc, partner=None).payments == 0
    assert runs(conn, mine) == []


def test_an_autopay_saved_after_this_months_due_date_pays_from_next_month(
    conn, app_conn, pmc, lease, tenant
):
    online(conn, pmc)
    mine = set_up(
        conn, pmc, lease, tenant, saved_at=datetime.combine(MONTH + DAY, datetime.min.time(), UTC)
    )
    if TODAY > MONTH:
        run(app_conn, pmc)
        assert runs(conn, mine) == []
    nxt = (MONTH + 32 * DAY).replace(day=1)
    charge(conn, pmc.pmc_id, lease, nxt, "1200.00")
    run(app_conn, pmc, day=nxt)
    assert [r[0] for r in runs(conn, mine)] == [nxt]


def test_an_unsaved_autopay_starts_nothing(conn, app_conn, pmc, lease, tenant):
    online(conn, pmc)
    mine = set_up(conn, pmc, lease, tenant, saved_at=None)
    run(app_conn, pmc)
    assert runs(conn, mine) == []


# --- stopping by itself -----------------------------------------------------------------------


def test_an_autopay_stops_by_itself_when_the_lease_ended_and_tells_the_tenant(
    conn, app_conn, pmc, lease, tenant
):
    online(conn, pmc)
    conn.execute(
        "INSERT INTO trust_contacts (pmc_id, tenant_id, email) VALUES (%s, %s, %s)",
        (pmc.pmc_id, tenant, "tenant.autopay@example.com"),
    )
    mine = set_up(conn, pmc, lease, tenant)
    end_lease(conn, pmc.pmc_id, lease, MONTH - DAY)
    done = run(app_conn, pmc, sending=True)
    assert (done.stopped, done.payments, done.emails) == (1, 0, 1)
    assert stopped(conn, mine) == "lease ended"
    subject, body = conn.execute(
        "SELECT subject, body FROM trust_emails WHERE kind = 'autopay_stopped' AND autopay_id = %s",
        (mine,),
    ).fetchone()
    assert subject.startswith("Autopay stopped for ")
    assert "your lease ended" in body and "Test bank ••••0000" in body
    assert run(app_conn, pmc, sending=True).emails == 0  # once


def test_an_autopay_stops_by_itself_after_two_payments_in_a_row_did_not_go_through(
    conn, app_conn, pmc, lease, tenant
):
    online(conn, pmc)
    mine = set_up(conn, pmc, lease, tenant)
    earlier = (MONTH - DAY).replace(day=1)
    charge(conn, pmc.pmc_id, lease, earlier, "1.00")
    for month in (earlier, MONTH):
        run(app_conn, pmc, day=month)
        report(conn, pmc, autopay.payment_id_for(mine, month), "failed")
    assert stopped(conn, mine) is None
    run(app_conn, pmc)
    assert stopped(conn, mine) == "failed twice"


def test_one_payment_that_did_not_go_through_does_not_stop_an_autopay(
    conn, app_conn, pmc, lease, tenant
):
    online(conn, pmc)
    mine = set_up(conn, pmc, lease, tenant)
    earlier = (MONTH - DAY).replace(day=1)
    charge(conn, pmc.pmc_id, lease, earlier, "1.00")
    run(app_conn, pmc, day=earlier)
    report(conn, pmc, autopay.payment_id_for(mine, earlier), "failed")
    run(app_conn, pmc, day=MONTH)
    report(conn, pmc, autopay.payment_id_for(mine, MONTH), "paid")
    run(app_conn, pmc)
    assert stopped(conn, mine) is None


def test_an_autopay_stops_by_itself_once_its_tenant_left_the_lease(conn, app_conn, pmc, tenant):
    online(conn, pmc)
    other = add_tenant(conn, pmc.pmc_id, pmc.owners[0].property_id, "Tenant 2")
    unit = add_unit(conn, pmc.pmc_id, pmc.owners[0].property_id)
    shared = open_lease(conn, pmc.pmc_id, unit, MONTH - 60 * DAY, None, "1200.00", [tenant, other])
    charge(conn, pmc.pmc_id, shared, MONTH, "1200.00")
    mine = set_up(conn, pmc, shared, tenant)
    conn.execute(
        "SELECT trust_remove_lease_tenant(%s, %s, %s, %s, NULL)",
        (pmc.pmc_id, shared, tenant, TODAY),
    )
    done = run(app_conn, pmc, sending=True)
    assert (done.stopped, done.payments, done.emails) == (1, 0, 0)  # no email on file: none
    assert stopped(conn, mine) == "tenant left"


def test_a_tenant_with_no_email_on_file_is_charged_but_not_emailed(
    conn, app_conn, pmc, lease, tenant
):
    online(conn, pmc)
    mine = set_up(conn, pmc, lease, tenant)
    done = run(app_conn, pmc, sending=True)
    assert (done.payments, done.emails) == (1, 0)
    assert runs(conn, mine) == [(MONTH, Decimal("1200.00"), None)]


def test_an_account_saved_at_another_partner_is_not_charged_by_this_server(
    conn, app_conn, pmc, lease, tenant
):
    online(conn, pmc)
    mine = set_up(conn, pmc, lease, tenant)
    assert run(app_conn, pmc, partner="stripe").payments == 0
    assert runs(conn, mine) == []  # left for a server with its partner, not marked done


def test_an_autopay_payment_tells_the_tenant_before_the_bank_is_debited(
    conn, app_conn, pmc, lease, tenant
):
    online(conn, pmc)
    conn.execute(
        "INSERT INTO trust_contacts (pmc_id, tenant_id, email) VALUES (%s, %s, %s)",
        (pmc.pmc_id, tenant, "tenant.autopay@example.com"),
    )
    mine = set_up(conn, pmc, lease, tenant)
    assert run(app_conn, pmc, sending=True).emails == 1
    to, subject, body, period = conn.execute(
        "SELECT to_address, subject, body, period FROM trust_emails"
        " WHERE kind = 'autopay_payment' AND autopay_id = %s",
        (mine,),
    ).fetchone()
    assert (to, period) == ("tenant.autopay@example.com", MONTH)
    assert subject.startswith("Autopay: $1,200.00 from your bank for ")
    assert f"due {MONTH:%m/%d/%Y}" in body


# --- what the database refuses ------------------------------------------------------------------


def test_one_autopay_on_at_a_time_for_a_lease_and_tenant(conn, pmc, lease, tenant):
    first = set_up(conn, pmc, lease, tenant)
    with pytest.raises(psycopg.errors.UniqueViolation):
        set_up(conn, pmc, lease, tenant)
    autopay.stop(conn, pmc.pmc_id, first, "tenant")
    assert not autopay.stop(conn, pmc.pmc_id, first, "tenant")  # once
    set_up(conn, pmc, lease, tenant)


def test_a_run_needs_a_saved_account_no_stop_and_the_autopays_own_rule(conn, pmc, lease, tenant):
    unsaved = set_up(conn, pmc, lease, tenant, saved_at=None)
    with pytest.raises(psycopg.errors.CheckViolation, match="no bank account saved"):
        conn.execute(
            "INSERT INTO trust_autopay_runs (pmc_id, autopay_id, due_on, skipped)"
            " VALUES (%s, %s, %s, 'nothing owed')",
            (pmc.pmc_id, unsaved, MONTH),
        )
    autopay.stop(conn, pmc.pmc_id, unsaved, "replaced")
    fixed = set_up(conn, pmc, lease, tenant, rule="fixed", amount="100.00")
    too_much = conn.execute(
        "INSERT INTO trust_online_payments (pmc_id, lease_id, tenant_id, amount, fee_payer, method,"
        " partner, partner_ref) VALUES (%s, %s, %s, 101, 'company', 'ach', 'fake', 'fake_x')"
        " RETURNING id",
        (pmc.pmc_id, lease, tenant),
    ).fetchone()[0]
    with pytest.raises(psycopg.errors.CheckViolation, match="by its own rule"):
        conn.execute(
            "INSERT INTO trust_autopay_runs (pmc_id, autopay_id, due_on, payment_id)"
            " VALUES (%s, %s, %s, %s)",
            (pmc.pmc_id, fixed, MONTH, too_much),
        )
    autopay.stop(conn, pmc.pmc_id, fixed, "tenant")
    with pytest.raises(psycopg.errors.CheckViolation, match="is stopped"):
        conn.execute(
            "INSERT INTO trust_autopay_runs (pmc_id, autopay_id, due_on, skipped)"
            " VALUES (%s, %s, %s, 'nothing owed')",
            (pmc.pmc_id, fixed, MONTH),
        )


def test_only_staff_stops_name_who_and_staff_stops_must(conn, pmc, lease, tenant):
    mine = set_up(conn, pmc, lease, tenant)
    with pytest.raises(psycopg.errors.CheckViolation):
        conn.execute(
            "INSERT INTO trust_autopay_stops (pmc_id, autopay_id, why) VALUES (%s, %s, 'staff')",
            (pmc.pmc_id, mine),
        )


# --- sending to the partner -------------------------------------------------------------------


class Counting(payments.FakePartner):
    """The fake partner, counting what it is asked to charge, failing while told to."""

    def __init__(self):
        self.charged: list[tuple[UUID, Decimal]] = []
        self.failing = False

    def charge(self, payment_id, amount, memo, account):
        if self.failing:
            raise payments.PartnerFailed("down")
        self.charged.append((payment_id, amount))
        return super().charge(payment_id, amount, memo, account)


def test_each_payment_goes_to_the_partner_once_and_waits_while_it_is_down(
    conn, app_conn, pmc, lease, tenant
):
    online(conn, pmc, payer="tenant")
    mine = set_up(conn, pmc, lease, tenant)
    run(app_conn, pmc)
    partner = Counting()
    partner.failing = True
    assert autopay.submit_waiting(app_conn, partner, pmc.pmc_id) == 0
    partner.failing = False
    assert autopay.submit_waiting(app_conn, partner, pmc.pmc_id) == 1
    assert autopay.submit_waiting(app_conn, partner, pmc.pmc_id) == 0
    payment = autopay.payment_id_for(mine, MONTH)
    fee = payments.tenant_fee(Decimal("1200.00"))
    assert partner.charged == [(payment, Decimal("1200.00") + fee)]  # the tenant's fee with it
    assert conn.execute(
        "SELECT partner_ref FROM trust_autopay_submissions WHERE payment_id = %s", (payment,)
    ).fetchone() == (f"fake_pi_{payment.hex}",)


def test_a_payment_started_at_another_partner_is_not_sent_to_this_one(
    conn, app_conn, pmc, lease, tenant
):
    online(conn, pmc)
    set_up(conn, pmc, lease, tenant)
    run(app_conn, pmc)

    class Other(Counting):
        name = "other"

    other = Other()
    assert autopay.submit_waiting(app_conn, other, pmc.pmc_id) == 0
    assert other.charged == []
    assert autopay.submit_waiting(app_conn, Counting(), pmc.pmc_id) == 1


def events(conn, payment):
    return conn.execute(
        "SELECT kind, partner_event FROM trust_online_payment_events WHERE payment_id = %s",
        (payment,),
    ).fetchall()


def test_a_payment_the_partner_never_took_is_given_up_and_the_tenant_told(
    conn, app_conn, pmc, lease, tenant
):
    online(conn, pmc)
    conn.execute(
        "INSERT INTO trust_contacts (pmc_id, tenant_id, email) VALUES (%s, %s, %s)",
        (pmc.pmc_id, tenant, "tenant.autopay@example.com"),
    )
    mine = set_up(conn, pmc, lease, tenant)
    run(app_conn, pmc)
    payment = autopay.payment_id_for(mine, MONTH)
    partner = Counting()
    tried = datetime.now(UTC) + timedelta(days=autopay.RETRY_DAYS, minutes=1)
    assert autopay.submit_waiting(app_conn, partner, pmc.pmc_id, now=tried) == 0
    # Not given up while a submit may still be trying it.
    assert run(app_conn, pmc, sending=True, now=tried).given_up == 0
    assert events(conn, payment) == []
    later = tried + timedelta(days=1)
    done = run(app_conn, pmc, sending=True, now=later)
    assert (done.given_up, done.emails, done.payments) == (1, 1, 0)
    assert events(conn, payment) == [("expired", autopay.GAVE_UP)]
    subject, body = conn.execute(
        "SELECT subject, body FROM trust_emails"
        " WHERE kind = 'autopay_not_sent' AND autopay_id = %s",
        (mine,),
    ).fetchone()
    assert subject.startswith("Autopay: your payment for ")
    assert f"due {MONTH:%m/%d/%Y}" in body and "nothing was taken" in body
    assert run(app_conn, pmc, sending=True, now=later).given_up == 0  # once
    # Never sent after, and no longer on its way: the lease's whole balance is due.
    assert autopay.submit_waiting(app_conn, partner, pmc.pmc_id) == 0
    assert partner.charged == []
    with app_conn.transaction():
        (active,) = autopay.active(app_conn, pmc.pmc_id, TODAY)
        assert autopay.to_pay(app_conn, pmc.pmc_id, active, MONTH) == Decimal("1200.00")
    # Should the partner have taken it after all, its report that the bank paid still counts.
    report(conn, pmc, payment, "paid")
    assert {kind for kind, _ in events(conn, payment)} == {"expired", "paid"}


def test_a_stopped_autopays_payment_is_never_sent(conn, app_conn, pmc, lease, tenant):
    online(conn, pmc)
    mine = set_up(conn, pmc, lease, tenant)
    # A second lease of the tenant's, with its own autopay.
    unit = add_unit(conn, pmc.pmc_id, pmc.owners[0].property_id)
    other = open_lease(conn, pmc.pmc_id, unit, MONTH - 60 * DAY, None, "800.00", [tenant])
    charge(conn, pmc.pmc_id, other, MONTH, "800.00")
    theirs = set_up(conn, pmc, other, tenant)
    run(app_conn, pmc)

    class Stopping(Counting):
        """Stops the other autopay while it sends the first payment: a stop that comes while
        payments are being sent."""

        def charge(self, payment_id, amount, memo, account):
            stopping = theirs if payment_id == autopay.payment_id_for(mine, MONTH) else mine
            with conn.transaction():
                autopay.stop(conn, pmc.pmc_id, stopping, "tenant")
            return super().charge(payment_id, amount, memo, account)

    partner = Stopping()
    assert autopay.submit_waiting(app_conn, partner, pmc.pmc_id) == 1
    assert [payment for payment, _ in partner.charged] == [autopay.payment_id_for(mine, MONTH)]
    # Stopped before it was sent: given up at the next run, never sent.
    assert run(app_conn, pmc).given_up == 1
    assert events(conn, autopay.payment_id_for(theirs, MONTH)) == [("expired", autopay.GAVE_UP)]
    assert autopay.submit_waiting(app_conn, Counting(), pmc.pmc_id) == 0


def test_the_payment_id_is_the_same_for_an_autopay_and_due_date():
    one = UUID(int=1)
    assert autopay.payment_id_for(one, date(2026, 11, 1)) == autopay.payment_id_for(
        one, date(2026, 11, 1)
    )
    assert autopay.payment_id_for(one, date(2026, 11, 1)) != autopay.payment_id_for(
        one, date(2026, 12, 1)
    )
