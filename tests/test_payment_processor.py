"""The payment processors (src/pms/payments/processor.py): the fake one's checkout, Stripe's
signature check and the events it reads, and what it asks Stripe for a checkout. No request
leaves the machine: Stripe's answer is made here (NOT VERIFIED against Stripe itself, which needs
keys). The secrets are made up for each test."""

import hashlib
import hmac
import io
import json
import secrets
import time
import urllib.parse
from decimal import Decimal

import pytest

from pms.payments import processor
from pms.payments.processor import BadSignature, Event, StripeProcessor


def signed(body: bytes, secret: str, at: int) -> str:
    mac = hmac.new(secret.encode(), f"{at}.".encode() + body, hashlib.sha256).hexdigest()
    return f"t={at},v1={mac}"


def test_the_fake_checkout_is_a_page_of_the_pms_and_sends_no_webhook():
    fake = processor.FakeProcessor()
    first = fake.start("p1", Decimal("10.00"), "card", "Rent", "http://pms.test/")
    second = fake.start("p2", Decimal("10.00"), "card", "Rent", "http://pms.test/")

    assert first.url == f"/pay#{first.reference}" and first.reference.startswith("fake_")
    assert first.reference != second.reference
    with pytest.raises(BadSignature):
        fake.events(b"{}", {})


def test_a_stripe_signature_is_checked_in_time():
    secret, body, at = secrets.token_hex(16), b'{"id": "evt_1"}', 1_800_000_000
    good = signed(body, secret, at)

    processor.verify(body, good, secret, at + 299)
    processor.verify(body, f"{good},v1=00", secret, at)  # another v1 alongside is fine
    for header, now, problem in (
        (good, at + 301, "too old"),
        (good, at - 301, "too old"),
        (signed(body, "another", at), at, "doesn't match"),
        (signed(b'{"id": "evt_2"}', secret, at), at, "doesn't match"),
        (f"v1={good.split('v1=')[1]}", at, "no time"),
        ("t=soon,v1=00", at, "no time"),
    ):
        with pytest.raises(BadSignature, match=problem):
            processor.verify(body, header, secret, now)


def event(kind: str, **item: object) -> bytes:
    return json.dumps({"id": f"evt_{kind}", "type": kind, "data": {"object": item}}).encode()


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        (event("checkout.session.completed", id="cs_1", payment_status="paid",
               payment_intent="pi_1"),
         [Event("evt_checkout.session.completed", "succeeded", "cs_1", "pi_1")]),
        # A bank debit's checkout completes unpaid; its money comes days later.
        (event("checkout.session.completed", id="cs_1", payment_status="unpaid"), []),
        (event("checkout.session.async_payment_succeeded", id="cs_1", payment_intent="pi_1"),
         [Event("evt_checkout.session.async_payment_succeeded", "succeeded", "cs_1", "pi_1")]),
        (event("checkout.session.async_payment_failed", id="cs_1"),
         [Event("evt_checkout.session.async_payment_failed", "failed", "cs_1", None,
                "the bank debit didn't go through")]),
        (event("charge.dispute.closed", status="lost", payment_intent="pi_1"),
         [Event("evt_charge.dispute.closed", "returned", None, "pi_1")]),
        (event("charge.dispute.closed", status="won", payment_intent="pi_1"), []),
        (event("customer.created", id="cus_1"), []),
    ],
)  # fmt: skip
def test_stripe_events_say_what_came_of_a_payment(body, expected):
    secret = secrets.token_hex(16)
    stripe = StripeProcessor("made-up-key", secret)
    at = int(time.time())

    assert stripe.events(body, {"stripe-signature": signed(body, secret, at)}) == expected
    with pytest.raises(BadSignature):
        stripe.events(body, {"stripe-signature": signed(body, "another", at)})


def test_a_stripe_checkout_asks_for_the_amount_by_the_method_with_the_payment_named(monkeypatch):
    sent = []

    def urlopen(request, timeout):
        sent.append(request)
        return io.BytesIO(
            json.dumps({"id": "cs_9", "url": "https://checkout.example/cs_9"}).encode()
        )

    monkeypatch.setattr(processor.urllib.request, "urlopen", urlopen)
    stripe = StripeProcessor("made-up-key", "made-up-secret")

    card = stripe.start(
        "p1", Decimal("1125.50"), "card", "Rent – Elm Court, 1A", "http://pms.test/"
    )
    bank = stripe.start("p2", Decimal("0.07"), "bank debit", "Rent", "http://pms.test/")

    assert (card.reference, card.url) == ("cs_9", "https://checkout.example/cs_9")
    first, second = (dict(urllib.parse.parse_qsl(r.data.decode())) for r in sent)
    assert sent[0].full_url == "https://api.stripe.com/v1/checkout/sessions"
    assert sent[0].get_header("Authorization") == "Bearer made-up-key"
    assert sent[0].get_header("Idempotency-key") == "checkout-p1"
    assert first["line_items[0][price_data][unit_amount]"] == "112550"
    assert first["payment_method_types[0]"] == "card"
    assert first["metadata[payment_id]"] == first["client_reference_id"] == "p1"
    assert first["success_url"] == "http://pms.test/?paid=p1"
    assert first["line_items[0][price_data][product_data][name]"] == "Rent – Elm Court, 1A"
    assert (
        second["payment_method_types[0]"],
        second["line_items[0][price_data][unit_amount]"],
    ) == (
        "us_bank_account",
        "7",
    )
    assert bank.reference == "cs_9"


def test_the_server_names_its_processor():
    assert processor.processor_named("", {}) is None
    assert isinstance(processor.processor_named("fake", {}), processor.FakeProcessor)
    keys = {"PMS_STRIPE_SECRET_KEY": "made-up-key", "PMS_STRIPE_WEBHOOK_SECRET": "made-up-secret"}
    assert isinstance(processor.processor_named("stripe", keys), StripeProcessor)
    with pytest.raises(ValueError, match="PMS_STRIPE_WEBHOOK_SECRET"):
        processor.processor_named("stripe", {"PMS_STRIPE_SECRET_KEY": "made-up-key"})
    with pytest.raises(ValueError, match="no payment processor named"):
        processor.processor_named("paypal", {})


def test_the_fake_saves_on_its_page_and_its_charges_pay_at_once():
    fake = processor.FakeProcessor()
    saving = fake.save("a1", "card", "http://pms.test/")
    reference, paid = fake.charge("fake_saved_x", Decimal("10.00"), "card", "a1-2026-11-01", "Rent")

    assert saving.url == f"/pay#{saving.reference}" and saving.reference.startswith("fake_setup_")
    assert reference == "fake_charge_a1-2026-11-01"
    assert paid == Event(f"fake-event-{reference}", "succeeded", reference, reference)


def test_stripe_saves_to_a_customer_and_charges_off_the_session(monkeypatch):
    sent = []
    answers = iter([{"id": "cus_1"}, {"id": "cs_1", "url": "https://checkout.example/cs_1"},
                    {"id": "pi_1"}])  # fmt: skip

    def urlopen(request, timeout):
        sent.append(request)
        return io.BytesIO(json.dumps(next(answers)).encode())

    monkeypatch.setattr(processor.urllib.request, "urlopen", urlopen)
    stripe = StripeProcessor("made-up-key", "made-up-secret")

    saving = stripe.save("a1", "bank debit", "http://pms.test/")
    reference, immediate = stripe.charge(
        "cus_1/pm_1", Decimal("1500.00"), "card", "a1-2026-11-01", "Rent"
    )

    assert (saving.reference, reference, immediate) == ("cs_1", "pi_1", None)
    customer, session, intent = (dict(urllib.parse.parse_qsl(r.data.decode())) for r in sent)
    assert [r.full_url.removeprefix(processor.STRIPE_API) for r in sent] == [
        "/customers", "/checkout/sessions", "/payment_intents",
    ]  # fmt: skip
    assert [r.get_header("Idempotency-key") for r in sent] == [
        "customer-a1", "setup-a1", "charge-a1-2026-11-01",
    ]  # fmt: skip
    assert customer == {"metadata[autopay_id]": "a1"}
    assert (session["mode"], session["customer"], session["payment_method_types[0]"]) == (
        "setup", "cus_1", "us_bank_account",
    )  # fmt: skip
    assert (intent["customer"], intent["payment_method"], intent["amount"]) == (
        "cus_1",
        "pm_1",
        "150000",
    )
    assert (intent["off_session"], intent["confirm"]) == ("true", "true")


@pytest.mark.parametrize(
    ("item", "fetched", "expected"),
    [
        ({"id": "cs_1", "mode": "setup", "customer": "cus_1", "setup_intent": "seti_1"},
         {"payment_method": {"id": "pm_1", "card": {"last4": "4242"}}},
         Event("evt", "saved", "cs_1", "cus_1/pm_1", label="Card ••••4242")),
        ({"id": "cs_1", "mode": "setup", "customer": "cus_1", "setup_intent": "seti_1"},
         {"payment_method": {"id": "pm_2", "us_bank_account": {"last4": "6789"}}},
         Event("evt", "saved", "cs_1", "cus_1/pm_2", label="Bank account ••••6789")),
        ({"id": "cs_1", "mode": "setup", "customer": "cus_1", "setup_intent": "seti_1"},
         {"payment_method": None},
         Event("evt", "not saved", "cs_1", None, "nothing was saved")),
    ],
)  # fmt: skip
def test_stripe_says_what_a_setup_saved(monkeypatch, item, fetched, expected):
    monkeypatch.setattr(
        processor.urllib.request,
        "urlopen",
        lambda r, timeout: io.BytesIO(json.dumps(fetched).encode()),
    )
    secret = secrets.token_hex(16)
    body = json.dumps({"id": "evt", "type": "checkout.session.completed", "data": {"object": item}})
    stripe = StripeProcessor("made-up-key", secret)

    assert stripe.events(
        body.encode(), {"stripe-signature": signed(body.encode(), secret, int(time.time()))}
    ) == [expected]


@pytest.mark.parametrize(
    ("kind", "item", "expected"),
    [
        ("payment_intent.succeeded", {"id": "pi_1"}, Event("evt", "succeeded", "pi_1", "pi_1")),
        ("payment_intent.payment_failed",
         {"id": "pi_1", "last_payment_error": {"message": "Your card was declined."}},
         Event("evt", "failed", "pi_1", None, "Your card was declined.")),
        ("payment_intent.payment_failed", {"id": "pi_1"},
         Event("evt", "failed", "pi_1", None, "it was declined")),
    ],
)  # fmt: skip
def test_stripe_says_what_came_of_a_charge(kind, item, expected):
    secret = secrets.token_hex(16)
    body = json.dumps({"id": "evt", "type": kind, "data": {"object": item}}).encode()
    stripe = StripeProcessor("made-up-key", secret)

    assert stripe.events(body, {"stripe-signature": signed(body, secret, int(time.time()))}) == [
        expected
    ]
