"""Asking the payments partner about payments it sent no report of (src/pms/look_ups.py,
docs/edge-cases.md C-090).

Stripe gives up on a webhook after three days, and a bank payment it never reported never reached
the books. Once a company's day, the nightly run asks the partner about each payment online with
no report a week after it started, and records what it says as its report would have: once, under
the payment's own keys, so the report that comes after (or came before) records nothing twice."""

from datetime import UTC, datetime, time, timedelta
from decimal import Decimal
from uuid import UUID, uuid4

import psycopg
import pytest
from helpers import add_unit, assess_late_fees, balance, charge, make_pmc, open_lease

from pms import look_ups, nightly, payments
from pms.api import online_payments as op
from pms.api import owner_payments

NOW = datetime.now(UTC)
TODAY = NOW.date()
DAY = timedelta(days=1)
RENT = Decimal("1200.00")


@pytest.fixture(autouse=True)
def one_company(monkeypatch):
    # The server's own Stripe account serves only a server holding one company (P-005); the tests'
    # database holds many.
    monkeypatch.setattr(payments, "many_companies", lambda conn: False)


class Stripe:
    """A partner answering each look-up as told (a kind, None while on its way, or a failure),
    keeping what it was asked."""

    name = "stripe"

    def __init__(self, says=None, fee="0.00"):
        self.says = says
        self.fee = Decimal(fee)
        self.asked: list[str] = []

    def look_up(self, payment_id, ref, now):
        self.asked.append(ref)
        if isinstance(self.says, Exception):
            raise self.says
        if self.says is None:
            return None
        fee = self.fee if self.says == "paid" else None
        return payments.Event(payment_id, self.says, f"look-up/{ref}", now - DAY, fee)


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
    unit = add_unit(conn, pmc.pmc_id, pmc.owners[0].property_id)
    made = open_lease(conn, pmc.pmc_id, unit, TODAY - 90 * DAY, None, "1200.00", [tenant])
    charge(conn, pmc.pmc_id, made, TODAY - 10 * DAY, RENT)
    return made


def started(conn, pmc, lease, tenant, days_ago, partner="stripe", ref=None) -> UUID:
    """A tenant's payment online of the rent, started `days_ago`, its Checkout Session `ref`."""
    payment = uuid4()
    conn.execute(
        "INSERT INTO trust_online_payments (id, pmc_id, lease_id, tenant_id, amount, tenant_fee,"
        " fee_payer, method, partner, partner_ref, created_at)"
        " VALUES (%s, %s, %s, %s, %s, 0, 'company', 'ach', %s, %s, %s)",
        (
            payment,
            pmc.pmc_id,
            lease,
            tenant,
            RENT,
            partner,
            ref or f"cs_test_{payment.hex}",
            NOW - days_ago * DAY,
        ),
    )
    return payment


def look(conn, pmc, partner, now=NOW):
    return look_ups.look_up_waiting(conn, payments.Resolver(partner), [pmc.pmc_id], now=now)


def postings(conn, key):
    return conn.execute(
        "SELECT count(*) FROM pgledger_transfers WHERE metadata ->> 'key' = %s", (key,)
    ).fetchone()[0]


def reports(conn, payment, events="trust_online_payment_events"):
    return conn.execute(
        f"SELECT kind, partner_event FROM {events} WHERE payment_id = %s",  # noqa: S608
        (payment,),
    ).fetchall()


def test_a_paid_payment_stripe_never_reported_is_found_and_recorded_once(conn, pmc, lease, tenant):
    payment = started(conn, pmc, lease, tenant, days_ago=8)
    stripe = Stripe("paid")

    looked = look(conn, pmc, stripe)

    assert (looked.asked, looked.recorded, looked.unanswered) == (1, 1, 0)
    assert stripe.asked == [f"cs_test_{payment.hex}"]
    assert reports(conn, payment) == [("paid", f"look-up/cs_test_{payment.hex}")]
    assert postings(conn, op.payment_key(payment)) == 1
    assert balance(conn, pmc.owners[0].account) == RENT
    # Paid on the day it was started, as late fees count it (D-080): the rent isn't late.
    assert assess_late_fees(conn, pmc.pmc_id, TODAY) == 0

    # The report Stripe gave up on, sent again by hand from Stripe's dashboard, and the next
    # night's look: nothing twice.
    late = payments.Event(payment, "paid", "evt_sent_again", NOW - DAY, Decimal("0.00"))
    assert op.apply(conn, late, "stripe")
    look(conn, pmc, stripe, NOW + DAY)

    assert stripe.asked == [f"cs_test_{payment.hex}"]
    assert reports(conn, payment) == [("paid", f"look-up/cs_test_{payment.hex}")]
    assert postings(conn, op.payment_key(payment)) == 1
    assert balance(conn, pmc.owners[0].account) == RENT


def test_a_payment_reported_already_is_never_asked_about(conn, pmc, lease, tenant):
    payment = started(conn, pmc, lease, tenant, days_ago=8)
    report = payments.Event(payment, "paid", "evt_on_time", NOW - 5 * DAY, Decimal("0.00"))
    assert op.apply(conn, report, "stripe")
    stripe = Stripe("paid")

    assert look(conn, pmc, stripe).asked == 0
    assert stripe.asked == []
    assert postings(conn, op.payment_key(payment)) == 1


def test_only_payments_a_week_to_two_months_old_of_a_real_partner_are_asked(
    conn, pmc, lease, tenant
):
    asked = started(conn, pmc, lease, tenant, days_ago=8)
    started(conn, pmc, lease, tenant, days_ago=6)  # a bank payment may still be settling
    started(conn, pmc, lease, tenant, days_ago=61)  # left to staff on Banking
    started(conn, pmc, lease, tenant, days_ago=8, partner="fake", ref="fake_1")  # moves no money
    stripe = Stripe(None)

    assert look(conn, pmc, stripe).asked == 1
    assert stripe.asked == [f"cs_test_{asked.hex}"]
    # Another company's payments aren't this company's look.
    other = make_pmc(conn, owners=1)
    assert look(conn, other, stripe).asked == 0
    assert look_ups.look_up_waiting(conn, payments.Resolver(stripe), [], now=NOW).asked == 0
    assert look_ups.look_up_waiting(conn, None, [pmc.pmc_id], now=NOW).asked == 0


@pytest.mark.parametrize("kind", ["failed", "expired"])
def test_a_payment_that_failed_or_expired_with_no_report_is_recorded_so(
    conn, pmc, lease, tenant, kind
):
    payment = started(conn, pmc, lease, tenant, days_ago=9)

    assert look(conn, pmc, Stripe(kind)).recorded == 1
    assert [k for k, _ in reports(conn, payment)] == [kind]
    assert postings(conn, op.payment_key(payment)) == 0
    assert balance(conn, pmc.owners[0].account) == 0


def test_a_payment_still_on_its_way_is_asked_again_the_next_day(conn, pmc, lease, tenant):
    payment = started(conn, pmc, lease, tenant, days_ago=8)
    stripe = Stripe(None)

    assert look(conn, pmc, stripe).recorded == 0
    stripe.says = "paid"
    assert look(conn, pmc, stripe, NOW + DAY).recorded == 1
    assert stripe.asked == [f"cs_test_{payment.hex}"] * 2
    assert postings(conn, op.payment_key(payment)) == 1


def test_a_partner_that_doesnt_answer_leaves_it_for_the_next_day(conn, pmc, lease, tenant):
    payment = started(conn, pmc, lease, tenant, days_ago=8)

    looked = look(conn, pmc, Stripe(payments.PartnerFailed("Stripe refused GET: HTTP 404")))

    assert (looked.asked, looked.recorded, looked.unanswered) == (1, 0, 1)
    assert reports(conn, payment) == []


def test_a_company_no_partner_here_can_ask_leaves_it_on_banking(conn, pmc, lease, tenant):
    payment = started(conn, pmc, lease, tenant, days_ago=8)

    # The server's partner is the fake one, which holds no Stripe payment, or there is none; and
    # the company entered no keys.
    for server in (payments.FakePartner(), None):
        looked = look_ups.look_up_waiting(conn, payments.Resolver(server), [pmc.pmc_id], now=NOW)
        assert (looked.asked, looked.recorded, looked.unanswered) == (1, 0, 1)
    assert reports(conn, payment) == []


def test_an_answer_the_books_refuse_is_logged_and_the_rest_go_on(
    conn, pmc, lease, tenant, monkeypatch
):
    first = started(conn, pmc, lease, tenant, days_ago=9)
    second = started(conn, pmc, lease, tenant, days_ago=8)
    real = op.apply

    def apply(conn, event, partner):
        if event.payment_id == first:
            raise psycopg.errors.RaiseException("refused")
        return real(conn, event, partner)

    monkeypatch.setattr(op, "apply", apply)

    assert look(conn, pmc, Stripe("paid")).recorded == 1
    assert reports(conn, first) == []
    assert postings(conn, op.payment_key(second)) == 1


def test_each_set_of_keys_is_asked_until_one_holds_the_payment():
    payment = uuid4()
    down = Stripe(payments.PartnerFailed("Stripe refused GET: HTTP 404"))
    holds = Stripe("expired")

    partner, event = look_ups.ask([down, holds], payment, "cs_test_1", NOW)

    assert partner is holds and event is not None and event.kind == "expired"
    assert down.asked == holds.asked == ["cs_test_1"]
    assert look_ups.ask([down], payment, "cs_test_1", NOW) is None
    assert look_ups.ask([], payment, "cs_test_1", NOW) is None


def test_an_autopay_payment_is_asked_about_once_it_reached_the_partner(conn, pmc, lease, tenant):
    payment = started(conn, pmc, lease, tenant, days_ago=8, ref=f"autopay_{uuid4().hex}")
    mine = conn.execute(
        "INSERT INTO trust_autopay (pmc_id, lease_id, tenant_id, rule, amount, partner)"
        " VALUES (%s, %s, %s, 'fixed', %s, 'stripe') RETURNING id",
        (pmc.pmc_id, lease, tenant, RENT),
    ).fetchone()[0]
    conn.execute(
        "INSERT INTO trust_autopay_accounts (pmc_id, autopay_id, partner_customer,"
        " partner_method, label, partner_event, saved_at)"
        " VALUES (%s, %s, 'cus_test', 'pm_test', 'Test bank ••••0000', %s, %s)",
        (pmc.pmc_id, mine, f"evt_{mine.hex}", NOW - 90 * DAY),
    )
    conn.execute(
        "INSERT INTO trust_autopay_runs (pmc_id, autopay_id, due_on, payment_id)"
        " VALUES (%s, %s, %s, %s)",
        (pmc.pmc_id, mine, TODAY - 8 * DAY, payment),
    )
    stripe = Stripe("paid")

    # Not sent to the partner yet: autopay gives it up itself (src/pms/autopay.py).
    assert look(conn, pmc, stripe).asked == 0
    conn.execute(
        "INSERT INTO trust_autopay_submissions (payment_id, pmc_id, partner_ref)"
        " VALUES (%s, %s, 'pi_test_autopay')",
        (payment, pmc.pmc_id),
    )
    assert look(conn, pmc, stripe).recorded == 1
    assert stripe.asked == ["pi_test_autopay"]
    assert postings(conn, op.payment_key(payment)) == 1


def test_an_owner_payment_stripe_never_reported_is_found_and_recorded_once(conn, pmc):
    account = pmc.owners[0].account
    payment = conn.execute(
        "INSERT INTO trust_owner_online_payments (pmc_id, ledger_account_id, amount, method,"
        " partner, partner_ref, created_at) VALUES (%s, %s, 300, 'ach', 'stripe', %s, %s)"
        " RETURNING id",
        (pmc.pmc_id, account, f"cs_test_owner_{uuid4().hex}", NOW - 8 * DAY),
    ).fetchone()[0]
    stripe = Stripe("paid")

    assert look(conn, pmc, stripe).recorded == 1
    late = payments.Event(payment, "paid", "evt_sent_again", NOW - DAY, Decimal("0.00"))
    assert owner_payments.apply(conn, late, "stripe")
    assert look(conn, pmc, stripe, NOW + DAY).asked == 0

    assert [k for k, _ in reports(conn, payment, "trust_owner_online_payment_events")] == ["paid"]
    contributions = conn.execute(
        "SELECT count(*) FROM trust_owner_contributions WHERE request_key = %s",
        (owner_payments.contribution_key(payment),),
    ).fetchone()[0]
    assert contributions == 1
    assert balance(conn, account) == Decimal("300.00")


def test_the_nightly_run_asks_once_a_company_day(database_url, conn, pmc, lease, tenant):
    """As trust_app, from the scheduler: the look-up goes with each company's run, so an hourly
    look whose company's day hasn't begun since asks nothing."""
    payment = started(conn, pmc, lease, tenant, days_ago=8)
    stripe = Stripe(None)
    clock = [datetime.combine(TODAY, time(12), UTC)]
    runs = nightly.Scheduler(
        database_url,
        "trust_app",
        "https://rentals.example.com",
        None,
        lambda: clock[0],
        pmcs=[pmc.pmc_id],
        payments=payments.Resolver(stripe),
    )

    runs.run_once()
    clock[0] += timedelta(hours=1)
    runs.run_once(begun_only=True)
    assert stripe.asked == [f"cs_test_{payment.hex}"]

    stripe.says = "paid"
    clock[0] += DAY
    runs.run_once(begun_only=True)
    assert stripe.asked == [f"cs_test_{payment.hex}"] * 2
    assert postings(conn, op.payment_key(payment)) == 1


# --- what Stripe's objects say (src/pms/payments.py) -------------------------------------------

PAYMENT = uuid4()


def intent(status, **more):
    return {"id": "pi_test_1", "status": status, "metadata": {"payment_id": str(PAYMENT)}, **more}


def session(status, payment_intent=None):
    return {
        "id": "cs_test_1",
        "status": status,
        "client_reference_id": str(PAYMENT),
        "expires_at": int((NOW - DAY).timestamp()),
        "payment_intent": payment_intent,
    }


PAID = intent(
    "succeeded",
    latest_charge={
        "balance_transaction": {"fee": 500, "created": int((NOW - 2 * DAY).timestamp())}
    },
)


def test_stripe_s_objects_say_what_its_report_would_have():
    said = payments.looked_up(PAYMENT, "cs_test_1", session("complete", PAID), PAID, NOW)
    assert said == payments.Event(
        PAYMENT,
        "paid",
        "look-up/cs_test_1",
        datetime.fromtimestamp(int((NOW - 2 * DAY).timestamp()), UTC),
        Decimal("5.00"),
    )
    autopay = payments.looked_up(PAYMENT, "pi_test_1", None, PAID, NOW)
    assert (
        autopay is not None and autopay.kind == "paid" and autopay.event_id == "look-up/pi_test_1"
    )
    expired = payments.looked_up(PAYMENT, "cs_test_1", session("expired"), None, NOW)
    assert expired is not None and expired.kind == "expired" and expired.happened_at < NOW
    error = {"code": "insufficient_funds"}
    for failed in (intent("requires_payment_method", last_payment_error=error), intent("canceled")):
        found = payments.looked_up(PAYMENT, "pi_test_1", None, failed, NOW)
        assert found == payments.Event(PAYMENT, "failed", "look-up/pi_test_1", NOW)
    # Still on its way, or not paid on yet: nothing to say.
    for waiting in (
        (session("open"), None),
        (session("complete", intent("processing")), intent("processing")),
        (None, intent("requires_payment_method")),
        (None, intent("requires_action")),
    ):
        assert payments.looked_up(PAYMENT, "cs_test_1", *waiting, NOW) is None
    # Another payment's object says nothing of this one.
    assert payments.looked_up(uuid4(), "pi_test_1", None, PAID, NOW) is None


def test_a_paid_payment_without_its_fee_is_asked_again_later():
    with pytest.raises(payments.PartnerFailed, match="without its fee"):
        payments.looked_up(PAYMENT, "pi_test_1", None, intent("succeeded"), NOW)


def test_a_paid_payment_is_never_dated_after_now_or_before_it_can_be_read():
    assert payments.moment(int((NOW + DAY).timestamp()), NOW) == NOW
    assert payments.moment(None, NOW) == NOW
    assert payments.moment("soon", NOW) == NOW
    assert payments.moment(10**20, NOW) == NOW


def test_stripe_is_asked_for_the_session_or_the_payment_and_nothing_else(monkeypatch):
    stripe = payments.StripePartner("sk_test_" + "x" * 24, "whsec_" + "y" * 24)
    calls = []

    def call(method, path, form, key=""):
        calls.append((method, path, form))
        return session("complete", PAID) if path.startswith("/checkout") else PAID

    monkeypatch.setattr(stripe, "_call", call)

    assert stripe.look_up(PAYMENT, "cs_test_1", NOW).kind == "paid"
    assert stripe.look_up(PAYMENT, "pi_test_1", NOW).kind == "paid"
    # Nothing but a session's or a payment's id goes in a path.
    for odd in ("../customers", "cs_test_1/x", "pi_", "ch_test_1", "cs_" + "a" * 201):
        assert stripe.look_up(PAYMENT, odd, NOW) is None
    assert calls == [
        (
            "GET",
            "/checkout/sessions/cs_test_1",
            [("expand[]", "payment_intent.latest_charge.balance_transaction")],
        ),
        ("GET", "/payment_intents/pi_test_1", [("expand[]", "latest_charge.balance_transaction")]),
    ]


def test_the_partners_asked_are_the_company_s_own_then_the_server_s(conn, pmc):
    stripe = Stripe()
    resolver = payments.Resolver(stripe)

    assert look_ups.partners_of(conn, resolver, pmc.pmc_id, "stripe") == [stripe]
    assert look_ups.partners_of(conn, resolver, pmc.pmc_id, "fake") == []
    assert look_ups.partners_of(conn, payments.Resolver(None), pmc.pmc_id, "stripe") == []
