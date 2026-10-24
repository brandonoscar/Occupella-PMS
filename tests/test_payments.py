"""The payments partners (src/pms/payments.py), without a database or the network: the fee on a
bank payment, the fee a tenant adds, Stripe's checkout request, its webhook's signature and what
each of its reports says."""

import hashlib
import hmac
import io
import json
import urllib.error
from decimal import Decimal
from uuid import UUID, uuid4

import pytest

from pms import payments

SECRET = "whsec_test_not_a_real_secret"


def signed(body: bytes, when: int, secret: str = SECRET) -> str:
    mac = hmac.new(secret.encode(), f"{when}.".encode() + body, hashlib.sha256).hexdigest()
    return f"t={when},v1={mac}"


@pytest.mark.parametrize(
    ("total", "fee"),
    [
        ("100.00", "0.80"),
        ("1.00", "0.01"),
        ("0.62", "0.00"),
        ("625.00", "5.00"),
        ("9000.00", "5.00"),
    ],
)
def test_a_bank_payments_fee_is_0_8_percent_to_the_cent_at_most_5(total, fee):
    assert payments.ach_fee(Decimal(total)) == Decimal(fee)


@pytest.mark.parametrize("amount", ["1.00", "99.99", "1000.00", "1234.56", "615.00", "9999.99"])
def test_a_tenants_fee_is_the_least_that_covers_the_fee_on_the_whole(amount):
    amount = Decimal(amount)
    fee = payments.tenant_fee(amount)
    assert payments.ach_fee(amount + fee) <= fee
    less = fee - Decimal("0.01")
    assert less < 0 or payments.ach_fee(amount + less) > less
    assert fee <= Decimal("5.00")


def test_the_fake_partner_pays_on_the_pms_own_page():
    payment = uuid4()
    started = payments.FakePartner().checkout(
        payment, [("Rent", Decimal("10.00"))], "http://pms.test/pay/done"
    )
    assert started == payments.Checkout(
        f"fake_{payment.hex}", f"http://pms.test/pay/fake#{payment}"
    )
    with pytest.raises(payments.NotTheirs):
        payments.FakePartner().event(b"{}", {})


def test_stripes_checkout_asks_a_bank_payment_with_the_pms_id_on_it_twice():
    payment = UUID("00000000-0000-4000-8000-000000000001")
    form = dict(
        payments.checkout_form(
            payment,
            [("Rent – Demo Unit 1", Decimal("1200.50")), ("Processing fee", Decimal("5.00"))],
            "https://pms.test/pay/done",
        )
    )
    assert form["mode"] == "payment"
    assert form["payment_method_types[0]"] == "us_bank_account"
    assert form["client_reference_id"] == str(payment)
    assert form["payment_intent_data[metadata][payment_id]"] == str(payment)
    assert form["line_items[0][price_data][unit_amount]"] == "120050"
    assert form["line_items[1][price_data][unit_amount]"] == "500"
    assert form["line_items[1][price_data][product_data][name]"] == "Processing fee"
    assert form["success_url"] == "https://pms.test/pay/done?paid=1"


def test_a_report_counts_only_with_this_endpoints_signature_and_while_fresh():
    body = b'{"id": "evt_1"}'
    payments.verify_signature(body, signed(body, 1000), SECRET, now=1100)
    # A secret being rolled: either signature will do.
    two = signed(body, 1000, "whsec_old") + "," + signed(body, 1000).split(",")[1]
    payments.verify_signature(body, two, SECRET, now=1000)
    for header, now in [
        (signed(body, 1000, "whsec_other"), 1000),  # another endpoint's secret
        (signed(b'{"id": "evt_2"}', 1000), 1000),  # another body
        (signed(body, 1000), 1301),  # signed more than five minutes ago
        ("", 1000),
        ("t=1000", 1000),
        ("v1=abc", 1000),
    ]:
        with pytest.raises(payments.NotTheirs):
            payments.verify_signature(body, header, SECRET, now=now)


class Stripe(payments.StripePartner):
    """Stripe with its API answered from a dict: (method, path) -> answer."""

    def __init__(self, answers):
        super().__init__("sk_test_not_real", SECRET)
        self.answers = answers
        self.calls = []

    def _call(self, method, path, form, key=""):
        self.calls.append((method, path, form, key))
        return self.answers[(method, path)]


def report(kind, subject, when=None):
    import time

    when = int(time.time()) if when is None else when
    body = json.dumps({"id": "evt_9", "type": kind, "created": when, "data": {"object": subject}})
    return body.encode(), {"stripe-signature": signed(body.encode(), when)}


def test_stripe_reports_a_bank_payment_paid_with_its_fee():
    payment = uuid4()
    stripe = Stripe(
        {
            ("GET", "/payment_intents/pi_1"): {
                "latest_charge": {"balance_transaction": {"fee": 500}},
                "metadata": {},
            }
        }
    )
    body, headers = report(
        "checkout.session.async_payment_succeeded",
        {"id": "cs_1", "client_reference_id": str(payment), "payment_intent": "pi_1"},
    )
    event = stripe.event(body, headers)
    assert event is not None
    assert (event.payment_id, event.kind, event.event_id, event.fee) == (
        payment,
        "paid",
        "evt_9",
        Decimal("5.00"),
    )


def test_stripe_reports_a_failure_a_return_and_nothing_else():
    payment = uuid4()
    stripe = Stripe({("GET", "/payment_intents/pi_2"): {"metadata": {"payment_id": str(payment)}}})
    failed = stripe.event(
        *report(
            "checkout.session.async_payment_failed",
            {"client_reference_id": str(payment), "payment_intent": "pi_2"},
        )
    )
    assert failed is not None and (failed.kind, failed.fee) == ("failed", None)
    # A return names the payment through Stripe's payment, not the session.
    returned = stripe.event(*report("charge.dispute.created", {"payment_intent": "pi_2"}))
    assert returned is not None and (returned.payment_id, returned.kind) == (payment, "returned")
    # The tenant left Stripe's page without paying: its session expired.
    expired = stripe.event(
        *report("checkout.session.expired", {"id": "cs_2", "client_reference_id": str(payment)})
    )
    assert expired is not None and (expired.payment_id, expired.kind, expired.fee) == (
        payment,
        "expired",
        None,
    )
    # A bank payment still clearing, a report the PMS doesn't read, a payment it didn't start.
    assert (
        stripe.event(
            *report(
                "checkout.session.completed",
                {"client_reference_id": str(payment), "payment_status": "unpaid"},
            )
        )
        is None
    )
    assert stripe.event(*report("customer.created", {})) is None
    assert (
        stripe.event(
            *report("checkout.session.async_payment_failed", {"client_reference_id": "not-ours"})
        )
        is None
    )


def test_stripes_report_must_be_signed_and_readable():
    stripe = Stripe({})
    body, headers = report("checkout.session.completed", {})
    with pytest.raises(payments.NotTheirs):
        stripe.event(body + b" ", headers)
    with pytest.raises(payments.NotTheirs):
        stripe.event(
            b"not json", {"stripe-signature": signed(b"not json", int(__import__("time").time()))}
        )


def test_a_partner_is_named_and_stripe_needs_its_keys():
    assert payments.partner_named("", {}) is None
    assert payments.partner_named("fake", {}).name == "fake"
    keys = {"PMS_STRIPE_SECRET_KEY": "sk_test_x", "PMS_STRIPE_WEBHOOK_SECRET": SECRET}
    assert payments.partner_named("stripe", keys).name == "stripe"
    with pytest.raises(payments.NoPartner):
        payments.partner_named("stripe", {})
    with pytest.raises(payments.NoPartner):
        payments.partner_named("paypal", {})


class Answer:
    """What urlopen hands back: a body, used as a context manager."""

    def __init__(self, body: bytes):
        self.body = body

    def __enter__(self):
        return io.BytesIO(self.body)

    def __exit__(self, *_):
        return False


def test_stripe_is_called_over_https_with_the_companys_key_and_a_retry_key(monkeypatch):
    sent = []

    def urlopen(request, timeout):
        sent.append((request, timeout))
        if request.get_method() == "POST":
            return Answer(b'{"id": "cs_9", "url": "https://checkout.test/cs_9"}')
        return Answer(b'{"latest_charge": {"balance_transaction": {"fee": 80}}}')

    monkeypatch.setattr(payments.urllib.request, "urlopen", urlopen)
    stripe = payments.StripePartner("sk_test_not_real", SECRET, timeout=3.0)
    payment = UUID(int=7)
    made = stripe.checkout(payment, [("Rent", Decimal("10.00"))], "http://testserver/pay/done")
    assert made == payments.Checkout(ref="cs_9", url="https://checkout.test/cs_9")
    assert stripe.fee_of("pi_9") == Decimal("0.80")
    (post, post_timeout), (get, _) = sent
    assert (post.get_method(), post.full_url, post_timeout) == (
        "POST",
        "https://api.stripe.com/v1/checkout/sessions",
        3.0,
    )
    assert post.get_header("Authorization") == "Bearer sk_test_not_real"
    assert post.get_header("Idempotency-key") == f"checkout-{payment}"
    assert f"client_reference_id={payment}".encode() in post.data
    # A read carries its form in the query string, and no retry key.
    assert (get.get_method(), get.data, get.get_header("Idempotency-key")) == ("GET", None, None)
    assert get.full_url == (
        "https://api.stripe.com/v1/payment_intents/pi_9"
        "?expand%5B%5D=latest_charge.balance_transaction"
    )


@pytest.mark.parametrize(
    "answer, says",
    [
        (
            urllib.error.HTTPError("https://api.stripe.com", 402, "Payment Required", {}, None),
            "HTTP 402",
        ),
        (urllib.error.URLError("no route"), "did not answer"),
        (Answer(b"<html>busy</html>"), "did not answer"),
    ],
)
def test_stripe_refusing_or_not_answering_is_the_partner_failing(monkeypatch, answer, says):
    def urlopen(request, timeout):
        if isinstance(answer, Exception):
            raise answer
        return answer

    monkeypatch.setattr(payments.urllib.request, "urlopen", urlopen)
    stripe = payments.StripePartner("sk_test_not_real", SECRET)
    with pytest.raises(payments.PartnerFailed, match=says):
        stripe.checkout(UUID(int=7), [("Rent", Decimal("10.00"))], "http://testserver/pay/done")


def test_an_answer_from_stripe_missing_what_the_pms_needs_is_the_partner_failing():
    stripe = Stripe(
        {
            ("POST", "/checkout/sessions"): {"id": "cs_1"},
            ("GET", "/payment_intents/pi_1"): {"latest_charge": None},
        }
    )
    with pytest.raises(payments.PartnerFailed, match="session came without"):
        stripe.checkout(UUID(int=7), [("Rent", Decimal("10.00"))], "http://testserver/pay/done")
    with pytest.raises(payments.PartnerFailed, match="without its fee"):
        stripe.fee_of("pi_1")
