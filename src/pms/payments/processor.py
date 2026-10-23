"""Payments taken online, through a processor behind one interface (migration
20261022000158; the founder, 2026-10-23: Stripe, holding off on its keys).

A processor takes a payment on its own page (a checkout the tenant is sent to) and later says
what came of it, in events the PMS takes once each. The server's PMS_PAYMENT_PROCESSOR picks one
(pms.api.Settings): "fake" for development and CI, which takes no money (its checkout is a page
of the PMS where the tenant says what happened); "stripe", with PMS_STRIPE_SECRET_KEY and
PMS_STRIPE_WEBHOOK_SECRET from the environment, never printed; or unset for none, and paying
online is then not offered. The processor bills its fees to the company, never to the money in
trust.

Stripe (NOT VERIFIED: no keys yet): a Checkout Session per payment, by card or by US bank account
(ACH debit), the PMS's payment id in its metadata; its webhook's events are signed (the
Stripe-Signature header, HMAC-SHA256 over "<time>.<body>", five minutes' tolerance) and say a
payment succeeded (checkout.session.completed when paid, or async_payment_succeeded for a bank
debit), failed (async_payment_failed) or came back (charge.dispute.closed, lost).

Autopay (migration 20261022000159): the processor saves a tenant's card or bank account (Stripe:
a customer and a setup Checkout Session; checkout.session.completed in setup mode says what was
saved, read from its SetupIntent) and charges it later, off the session (Stripe: a confirmed
PaymentIntent, its outcome in payment_intent.succeeded or payment_intent.payment_failed). The fake
processor saves on its /pay page and its charges pay at once.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import time
import urllib.parse
import urllib.request
from contextvars import ContextVar
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Literal, Protocol

Method = Literal["card", "bank debit"]
Outcome = Literal["succeeded", "failed", "returned", "saved", "not saved"]


@dataclass(frozen=True)
class Checkout:
    """Where the tenant pays, and the processor's reference for it."""

    reference: str
    url: str


@dataclass(frozen=True)
class Event:
    """What a processor says of a payment: its id (taken once), the checkout's reference or the
    processor's own payment, and for a success the processor's payment."""

    id: str
    outcome: Outcome
    reference: str | None
    processor_payment: str | None
    reason: str | None = None
    # For a card or account saved for autopay (saved): how it is shown, "Card ••••4242".
    label: str | None = None


class BadSignature(ValueError):
    """A webhook whose signature this server's secret doesn't make."""


class Processor(Protocol):
    @property
    def name(self) -> Literal["fake", "stripe"]: ...

    def start(
        self,
        payment_id: str,
        amount: Decimal,
        method: Method,
        description: str,
        return_url: str,
    ) -> Checkout: ...

    def events(self, body: bytes, headers: dict[str, str]) -> list[Event]: ...

    def save(self, autopay_id: str, method: Method, return_url: str) -> Checkout: ...

    def charge(
        self, saved: str, amount: Decimal, method: Method, key: str, description: str
    ) -> tuple[str, Event | None]: ...


class FakeProcessor:
    """Takes no money: its checkout is the PMS's own /pay page, where the tenant (or a test) says
    whether the payment went through; the API makes that the processor's event."""

    name: Literal["fake"] = "fake"

    def start(
        self,
        payment_id: str,
        amount: Decimal,
        method: Method,
        description: str,
        return_url: str,
    ) -> Checkout:
        reference = f"fake_{secrets.token_urlsafe(18)}"
        return Checkout(reference=reference, url=f"/pay#{reference}")

    def events(self, body: bytes, headers: dict[str, str]) -> list[Event]:
        raise BadSignature("the fake processor sends no webhook")

    def save(self, autopay_id: str, method: Method, return_url: str) -> Checkout:
        reference = f"fake_setup_{secrets.token_urlsafe(18)}"
        return Checkout(reference=reference, url=f"/pay#{reference}")

    def charge(
        self, saved: str, amount: Decimal, method: Method, key: str, description: str
    ) -> tuple[str, Event | None]:
        """A saved card or account always pays, at once."""
        reference = f"fake_charge_{key}"
        return reference, Event(f"fake-event-{reference}", "succeeded", reference, reference)


# The server's processor for the request being answered, set by the app for each one (as the
# email sender's is, pms.api.outbox): code that calls a route itself gets none.
CURRENT: ContextVar[Processor | None] = ContextVar("pms_payment_processor", default=None)

STRIPE_API = "https://api.stripe.com/v1"
TOLERANCE = 300  # seconds a signed webhook stays good


class StripeProcessor:
    name: Literal["stripe"] = "stripe"

    def __init__(self, secret_key: str, webhook_secret: str) -> None:
        self._key = secret_key
        self._webhook_secret = webhook_secret

    def start(
        self,
        payment_id: str,
        amount: Decimal,
        method: Method,
        description: str,
        return_url: str,
    ) -> Checkout:
        form = {
            "mode": "payment",
            "client_reference_id": payment_id,
            "metadata[payment_id]": payment_id,
            "payment_intent_data[metadata][payment_id]": payment_id,
            "payment_method_types[0]": "card" if method == "card" else "us_bank_account",
            "line_items[0][quantity]": "1",
            "line_items[0][price_data][currency]": "usd",
            "line_items[0][price_data][unit_amount]": str(int(amount * 100)),
            "line_items[0][price_data][product_data][name]": description[:250],
            "success_url": f"{return_url}?paid={payment_id}",
            "cancel_url": return_url,
        }
        request = urllib.request.Request(  # noqa: S310 (a fixed https URL)
            f"{STRIPE_API}/checkout/sessions",
            data=urllib.parse.urlencode(form).encode(),
            headers={
                "Authorization": f"Bearer {self._key}",
                "Idempotency-Key": f"checkout-{payment_id}",
            },
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=20) as answer:  # noqa: S310
            session = json.load(answer)
        return Checkout(reference=session["id"], url=session["url"])

    def _post(self, path: str, form: dict[str, str], key: str) -> dict[str, Any]:
        request = urllib.request.Request(  # noqa: S310 (a fixed https URL)
            f"{STRIPE_API}/{path}",
            data=urllib.parse.urlencode(form).encode(),
            headers={"Authorization": f"Bearer {self._key}", "Idempotency-Key": key},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=20) as answer:  # noqa: S310
            found: dict[str, Any] = json.load(answer)
        return found

    def _get(self, path: str) -> dict[str, Any]:
        request = urllib.request.Request(  # noqa: S310 (a fixed https URL)
            f"{STRIPE_API}/{path}", headers={"Authorization": f"Bearer {self._key}"}
        )
        with urllib.request.urlopen(request, timeout=20) as answer:  # noqa: S310
            found: dict[str, Any] = json.load(answer)
        return found

    def _saved(self, event_id: str, session: dict[str, Any]) -> Event:
        """A setup checkout completed: the customer and the card or bank account it saved, read
        from its SetupIntent with the payment method expanded."""
        setup = session.get("setup_intent")
        if isinstance(setup, str):
            setup = self._get(f"setup_intents/{setup}?expand[]=payment_method")
        method = (setup or {}).get("payment_method")
        if not isinstance(method, dict) or not method.get("id"):
            return Event(event_id, "not saved", session["id"], None, "nothing was saved")
        card, bank = method.get("card"), method.get("us_bank_account")
        last4 = (card or bank or {}).get("last4")
        label = ("Card" if card else "Bank account") + (f" ••••{last4}" if last4 else "")
        saved = f"{session.get('customer')}/{method['id']}"
        return Event(event_id, "saved", session["id"], saved, label=label)

    def save(self, autopay_id: str, method: Method, return_url: str) -> Checkout:
        """A customer for the tenant's autopay, and a Checkout Session that saves their card or
        bank account to it for charges later, off the session."""
        customer = self._post(
            "customers", {"metadata[autopay_id]": autopay_id}, f"customer-{autopay_id}"
        )
        session = self._post(
            "checkout/sessions",
            {
                "mode": "setup",
                "customer": customer["id"],
                "client_reference_id": autopay_id,
                "metadata[autopay_id]": autopay_id,
                "payment_method_types[0]": "card" if method == "card" else "us_bank_account",
                "success_url": f"{return_url}?autopay={autopay_id}",
                "cancel_url": return_url,
            },
            f"setup-{autopay_id}",
        )
        return Checkout(reference=session["id"], url=session["url"])

    def charge(
        self, saved: str, amount: Decimal, method: Method, key: str, description: str
    ) -> tuple[str, Event | None]:
        """A PaymentIntent on the saved card or account, confirmed off the session; what came of
        it comes in the webhook."""
        customer, _, payment_method = saved.partition("/")
        intent = self._post(
            "payment_intents",
            {
                "amount": str(int(amount * 100)),
                "currency": "usd",
                "customer": customer,
                "payment_method": payment_method,
                "payment_method_types[0]": "card" if method == "card" else "us_bank_account",
                "off_session": "true",
                "confirm": "true",
                "description": description[:250],
                "metadata[charge]": key,
            },
            f"charge-{key}",
        )
        return intent["id"], None

    def events(self, body: bytes, headers: dict[str, str]) -> list[Event]:
        verify(body, headers.get("stripe-signature", ""), self._webhook_secret, time.time())
        event = json.loads(body)
        kind, item = event.get("type"), event.get("data", {}).get("object", {})
        if kind == "checkout.session.completed" and item.get("mode") == "setup":
            return [self._saved(event["id"], item)]
        if kind == "payment_intent.succeeded":
            return [Event(event["id"], "succeeded", item["id"], item["id"])]
        if kind == "payment_intent.payment_failed":
            error = (item.get("last_payment_error") or {}).get("message") or "it was declined"
            return [Event(event["id"], "failed", item["id"], None, error[:400])]
        if kind == "checkout.session.completed" and item.get("payment_status") == "paid":
            return [Event(event["id"], "succeeded", item["id"], item.get("payment_intent"))]
        if kind == "checkout.session.async_payment_succeeded":
            return [Event(event["id"], "succeeded", item["id"], item.get("payment_intent"))]
        if kind == "checkout.session.async_payment_failed":
            return [
                Event(event["id"], "failed", item["id"], None, "the bank debit didn't go through")
            ]
        if kind == "charge.dispute.closed" and item.get("status") == "lost":
            return [Event(event["id"], "returned", None, item.get("payment_intent"))]
        return []


def verify(body: bytes, header: str, secret: str, now: float) -> None:
    """Stripe's webhook signature: the time it was sent and a v1 HMAC-SHA256 of "<time>.<body>"
    under the endpoint's secret, sent within the tolerance."""
    parts: dict[str, list[str]] = {}
    for item in header.split(","):
        key, _, value = item.strip().partition("=")
        parts.setdefault(key, []).append(value)
    try:
        sent = int(parts["t"][0])
    except (KeyError, ValueError):
        raise BadSignature("no time in the signature") from None
    if abs(now - sent) > TOLERANCE:
        raise BadSignature("the signature is too old")
    expected = hmac.new(secret.encode(), f"{sent}.".encode() + body, hashlib.sha256).hexdigest()
    if not any(hmac.compare_digest(expected, given) for given in parts.get("v1", [])):
        raise BadSignature("the signature doesn't match")


def processor_named(name: str, environ: dict[str, str]) -> Processor | None:
    """The server's processor: "fake", "stripe" (its keys from the environment), or none."""
    if name == "fake":
        return FakeProcessor()
    if name == "stripe":
        key, secret = environ.get("PMS_STRIPE_SECRET_KEY"), environ.get("PMS_STRIPE_WEBHOOK_SECRET")
        if not key or not secret:
            raise ValueError("stripe needs PMS_STRIPE_SECRET_KEY and PMS_STRIPE_WEBHOOK_SECRET")
        return StripeProcessor(key, secret)
    if name:
        raise ValueError(f"no payment processor named {name!r}")
    return None
