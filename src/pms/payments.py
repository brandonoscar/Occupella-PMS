"""Rent paid online, through a payments partner behind one interface (migration 0150, ADR 0009).

A company's admin may enter its own Stripe keys in Company setup (migration 20261104000800, the
founder's call of 11/04): its secret key and its webhook's signing secret, sealed under the server's
PMS_DATA_KEY (src/pms/sealed.py), never sent back to a browser; its payments then go through its own
Stripe account (`Resolver`). Without them, the server's PMS_PAYMENTS picks the partner
(pms.api.Settings): "stripe", its keys from the environment (PMS_STRIPE_SECRET_KEY,
PMS_STRIPE_WEBHOOK_SECRET), never in Settings, which a log or an error could print; "fake" for
development and CI, which calls nothing; or unset for none, and no tenant can pay online.

The partner hosts the page where the tenant gives their bank account, so the PMS never sees an
account number, and it reports back what the bank did (`event`): paid, with the partner's fee;
failed; or returned after it was paid. The PMS records what it reports
(src/pms/api/online_payments.py); the partner moves the money.

For autopay (migration 0151, src/pms/autopay.py) the partner's page saves the tenant's bank account
instead (`save_account`), reported back as `Saved` with the partner's references and a label, and
the PMS later asks it to take a payment from that account (`charge`), reported like any other.

Bank (ACH) payments only for now. What Stripe calls each thing (Checkout Sessions with
us_bank_account, its webhook events and signature, a charge's balance transaction) was written from
its API as known in 2026 and not checked against a live account: NOT VERIFIED.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import ROUND_FLOOR, ROUND_HALF_UP, Decimal
from typing import Any, Literal, Protocol
from uuid import UUID

import psycopg

from pms import sealed

log = logging.getLogger(__name__)

CENT = Decimal("0.01")
# A bank payment's fee as Stripe published it: 0.8%, at most $5.00 (NOT VERIFIED against its
# current prices). The fake partner charges the same, so tests see a real-looking fee.
ACH_PERCENT = Decimal("0.008")
ACH_CAP = Decimal("5.00")

Kind = Literal["paid", "failed", "returned", "expired"]


@dataclass(frozen=True)
class Checkout:
    # The partner's own reference for the payment.
    ref: str
    # Where the tenant goes to pay.
    url: str


@dataclass(frozen=True)
class Event:
    """What the partner reported about one payment."""

    payment_id: UUID
    kind: Kind
    # The partner's own id of the report, kept with it.
    event_id: str
    happened_at: datetime
    # The partner's fee for a paid payment, kept out of what it pays into trust.
    fee: Decimal | None = None


@dataclass(frozen=True)
class Account:
    """A bank account the partner saved, as the partner names it: never an account number."""

    customer: str
    method: str
    # The tenant's authorization to debit it, when the partner keeps one apart.
    mandate: str | None = None


@dataclass(frozen=True)
class Saved:
    """The partner's report that it saved a tenant's bank account for an autopay."""

    autopay_id: UUID
    account: Account
    # What the tenant sees of it: the bank's name and the account's last four digits.
    label: str
    event_id: str
    happened_at: datetime


class PartnerFailed(Exception):
    """The partner could not be reached or answered with something it shouldn't."""


class NotTheirs(Exception):
    """A report that doesn't carry the partner's signature, or that can't be read."""


class Partner(Protocol):
    name: str

    def checkout(
        self, payment_id: UUID, lines: list[tuple[str, Decimal]], done_url: str
    ) -> Checkout: ...

    def save_account(self, autopay_id: UUID, done_url: str) -> Checkout: ...

    def charge(self, payment_id: UUID, amount: Decimal, memo: str, account: Account) -> str: ...

    def event(self, body: bytes, headers: Mapping[str, str]) -> Event | Saved | None: ...


def ach_fee(total: Decimal) -> Decimal:
    """The partner's fee on a bank payment of `total`: 0.8%, to the cent, at most $5.00."""
    return min((total * ACH_PERCENT).quantize(CENT, ROUND_HALF_UP), ACH_CAP)


def tenant_fee(amount: Decimal) -> Decimal:
    """What a tenant who pays the partner's fee adds to `amount`, so that the fee on the whole
    comes to no more than it: the smallest such cents."""
    fee = min((amount * ACH_PERCENT / (1 - ACH_PERCENT)).quantize(CENT, ROUND_FLOOR), ACH_CAP)
    while ach_fee(amount + fee) > fee:
        fee += CENT
    return fee


class FakePartner:
    """A stand-in for development and tests: it calls nothing. Its payment page is the PMS's own
    (/pay/fake#<payment id>, served only while it is the partner), where a button says what the
    bank did; pms.api.online_payments reads that as the partner's report. It charges what Stripe
    charges for a bank payment."""

    name = "fake"

    def checkout(
        self, payment_id: UUID, lines: list[tuple[str, Decimal]], done_url: str
    ) -> Checkout:
        base = done_url.split("/pay/", 1)[0]
        return Checkout(ref=f"fake_{payment_id.hex}", url=f"{base}/pay/fake#{payment_id}")

    def save_account(self, autopay_id: UUID, done_url: str) -> Checkout:
        base = done_url.split("/pay/", 1)[0]
        url = f"{base}/pay/fake-save#{autopay_id}"
        return Checkout(ref=f"fake_setup_{autopay_id.hex}", url=url)

    def charge(self, payment_id: UUID, amount: Decimal, memo: str, account: Account) -> str:
        return f"fake_pi_{payment_id.hex}"

    def event(self, body: bytes, headers: Mapping[str, str]) -> Event | Saved | None:
        raise NotTheirs("the fake partner reports through its own page, not a webhook")


def fake_saved(autopay_id: UUID, happened_at: datetime) -> Saved:
    """What the fake partner's page reports when the tenant saves a bank account: made-up
    references, and a label like a real one's."""
    return Saved(
        autopay_id,
        Account(f"fake_cus_{autopay_id.hex[:12]}", f"fake_pm_{autopay_id.hex[:12]}", None),
        "Test bank ••••0000",
        f"fake_saved_{autopay_id.hex}",
        happened_at,
    )


# The Stripe API version every call names (docs/edge-cases.md P-014): without it Stripe answers in
# the account's own default version, and one older than 2022-11-15 has no PaymentIntent
# latest_charge, so every paid report would fail on its fee. This one has every shape the PMS
# reads (latest_charge, Checkout's us_bank_account, SetupIntents). A webhook's own payload keeps
# the endpoint's version; the PMS reads only ids, statuses and metadata from it, and fetches the
# fee with this version. Not tried against Stripe from here (NOT VERIFIED); tests/test_payments.py
# pins that it is sent.
STRIPE_VERSION = "2024-06-20"


class StripePartner:
    """The company's own Stripe account (Stripe, Inc.'s API over HTTPS, no SDK): a Checkout Session
    takes the payment on Stripe's page, and Stripe's signed webhook reports what the bank did."""

    name = "stripe"
    api = "https://api.stripe.com/v1"

    def __init__(self, secret_key: str, webhook_secret: str, timeout: float = 20.0) -> None:
        if not secret_key or not webhook_secret:
            raise NoPartner(
                "PMS_PAYMENTS=stripe needs PMS_STRIPE_SECRET_KEY and PMS_STRIPE_WEBHOOK_SECRET"
            )
        self._key = secret_key
        self._webhook_secret = webhook_secret
        self.timeout = timeout

    def _call(self, method: str, path: str, form: list[tuple[str, str]], key: str = "") -> Any:
        data = urllib.parse.urlencode(form).encode() if method == "POST" else None
        url = f"{self.api}{path}" + ("" if data else "?" + urllib.parse.urlencode(form))
        request = urllib.request.Request(url, data=data, method=method)  # noqa: S310 (fixed https)
        request.add_header("Authorization", f"Bearer {self._key}")
        request.add_header("Stripe-Version", STRIPE_VERSION)
        if key:
            # Stripe's own retry key: the same payment asked twice is one session.
            request.add_header("Idempotency-Key", key)
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as answer:  # noqa: S310
                return json.load(answer)
        except urllib.error.HTTPError as refused:
            raise PartnerFailed(f"Stripe refused {method} {path}: HTTP {refused.code}") from None
        except (OSError, ValueError) as failed:
            raise PartnerFailed(f"Stripe did not answer {method} {path}: {failed}") from None

    def checkout(
        self, payment_id: UUID, lines: list[tuple[str, Decimal]], done_url: str
    ) -> Checkout:
        form = checkout_form(payment_id, lines, done_url)
        session = self._call("POST", "/checkout/sessions", form, key=f"checkout-{payment_id}")
        try:
            return Checkout(ref=str(session["id"]), url=str(session["url"]))
        except (KeyError, TypeError) as failed:
            raise PartnerFailed(f"Stripe's session came without {failed}") from None

    def save_account(self, autopay_id: UUID, done_url: str) -> Checkout:
        """A customer for the autopay, then a Checkout Session in setup mode on it: Stripe's page
        saves the tenant's bank account and their authorization to debit it."""
        customer = self._call(
            "POST",
            "/customers",
            [("metadata[autopay_id]", str(autopay_id))],
            key=f"customer-{autopay_id}",
        )
        try:
            customer_id = str(customer["id"])
        except (KeyError, TypeError) as failed:
            raise PartnerFailed(f"Stripe's customer came without {failed}") from None
        session = self._call(
            "POST",
            "/checkout/sessions",
            setup_form(autopay_id, customer_id, done_url),
            key=f"setup-{autopay_id}",
        )
        try:
            return Checkout(ref=str(session["id"]), url=str(session["url"]))
        except (KeyError, TypeError) as failed:
            raise PartnerFailed(f"Stripe's session came without {failed}") from None

    def charge(self, payment_id: UUID, amount: Decimal, memo: str, account: Account) -> str:
        """A payment from the saved account, confirmed off session. Asked again for the same
        payment (a run that stopped halfway, say), it finds the one made before instead of making a
        second: Stripe forgets a retry key after a day, so the customer's payments are looked at
        first."""
        listed = self._call(
            "GET", "/payment_intents", [("customer", account.customer), ("limit", "100")]
        )
        for intent in listed.get("data", []) if isinstance(listed, dict) else []:
            if (intent.get("metadata") or {}).get("payment_id") == str(payment_id):
                return str(intent["id"])
        made = self._call(
            "POST",
            "/payment_intents",
            charge_form(payment_id, amount, memo, account),
            key=f"autopay-{payment_id}",
        )
        try:
            return str(made["id"])
        except (KeyError, TypeError) as failed:
            raise PartnerFailed(f"Stripe's payment came without {failed}") from None

    def saved(self, setup_intent: str, event_id: str, happened: datetime) -> Saved | None:
        """The bank account a setup intent saved, once it succeeded; None before."""
        intent = self._call(
            "GET", f"/setup_intents/{setup_intent}", [("expand[]", "payment_method")]
        )
        try:
            if intent.get("status") != "succeeded":
                return None  # the account is still being verified; setup_intent.succeeded follows
            autopay = (intent.get("metadata") or {}).get("autopay_id")
            method = intent["payment_method"]
            bank = method.get("us_bank_account") or {}
            label = f"{bank.get('bank_name') or 'Bank account'} ••••{bank.get('last4') or '????'}"
            return Saved(
                UUID(str(autopay)),
                Account(str(intent["customer"]), str(method["id"]), intent.get("mandate")),
                label[:80],
                event_id,
                happened,
            )
        except (KeyError, TypeError, ValueError, AttributeError) as failed:
            raise PartnerFailed(f"Stripe's saved account can't be read: {failed}") from None

    def event(self, body: bytes, headers: Mapping[str, str]) -> Event | Saved | None:
        verify_signature(body, headers.get("stripe-signature", ""), self._webhook_secret)
        try:
            report = json.loads(body)
            kind_of = str(report["type"])
            subject = report["data"]["object"]
            happened = datetime.fromtimestamp(int(report["created"]), UTC)
            event_id = str(report["id"])
        except (KeyError, TypeError, ValueError) as failed:
            raise NotTheirs(f"Stripe's report can't be read: {failed}") from None
        if kind_of == "checkout.session.completed" and subject.get("mode") == "setup":
            if not subject.get("setup_intent"):
                return None
            return self.saved(str(subject["setup_intent"]), event_id, happened)
        if kind_of == "setup_intent.succeeded":
            if not (subject.get("metadata") or {}).get("autopay_id"):
                return None  # not an account the PMS asked to save
            return self.saved(str(subject["id"]), event_id, happened)
        kind = STRIPE_KINDS.get(kind_of)
        if kind is None:
            return None
        if kind_of == "checkout.session.completed" and subject.get("payment_status") != "paid":
            return None  # a bank payment is still clearing; the async events say how it ends
        if kind_of == "charge.dispute.created":
            intent = self._call("GET", f"/payment_intents/{subject.get('payment_intent')}", [])
        else:
            intent = None
        payment = payment_of(subject, intent)
        if payment is None:
            return None  # not a payment the PMS started
        if kind == "paid":
            # A payment's own report names itself; a session's names its payment.
            is_intent = subject.get("object") == "payment_intent"
            fee = self.fee_of(subject["id"] if is_intent else subject["payment_intent"])
        else:
            fee = None
        return Event(payment, kind, event_id, happened, fee)

    def fee_of(self, payment_intent: str) -> Decimal:
        """Stripe's fee on a payment, from its charge's balance transaction."""
        intent = self._call(
            "GET",
            f"/payment_intents/{payment_intent}",
            [("expand[]", "latest_charge.balance_transaction")],
        )
        try:
            cents = intent["latest_charge"]["balance_transaction"]["fee"]
            return (Decimal(int(cents)) / 100).quantize(CENT)
        except (KeyError, TypeError, ValueError) as failed:
            raise PartnerFailed(f"Stripe's payment came without its fee: {failed}") from None


# Stripe's reports the PMS reads, and what each says happened.
STRIPE_KINDS: dict[str, Kind] = {
    "checkout.session.completed": "paid",
    "checkout.session.async_payment_succeeded": "paid",
    "checkout.session.async_payment_failed": "failed",
    # An autopay's payment, made without a session (and a session's payment too, reported twice;
    # each kind counts once).
    "payment_intent.succeeded": "paid",
    "payment_intent.payment_failed": "failed",
    # A bank payment that comes back after it succeeded (NOT VERIFIED: Stripe's word for an ACH
    # return after success).
    "charge.dispute.created": "returned",
    # The tenant left Stripe's page without paying; it expires a day later.
    "checkout.session.expired": "expired",
}


# Every event the PMS reads from Stripe, which the company's webhook endpoint must be sent
# (docs/edge-cases.md P-012): one not subscribed to the bank's async reports never sees a bank
# payment paid. Settings, Company setup lists them.
STRIPE_EVENTS = tuple(sorted({*STRIPE_KINDS, "setup_intent.succeeded"}))


def checkout_form(
    payment_id: UUID, lines: list[tuple[str, Decimal]], done_url: str
) -> list[tuple[str, str]]:
    """A Checkout Session for a bank payment of `lines` (what each is, how much), the PMS's own
    id on it twice: on the session and on its payment."""
    form = [
        ("mode", "payment"),
        ("payment_method_types[0]", "us_bank_account"),
        ("success_url", f"{done_url}?paid=1"),
        ("cancel_url", f"{done_url}?cancelled=1"),
        ("client_reference_id", str(payment_id)),
        ("metadata[payment_id]", str(payment_id)),
        ("payment_intent_data[metadata][payment_id]", str(payment_id)),
    ]
    for n, (label, amount) in enumerate(lines):
        form += [
            (f"line_items[{n}][quantity]", "1"),
            (f"line_items[{n}][price_data][currency]", "usd"),
            (f"line_items[{n}][price_data][unit_amount]", str(int(amount * 100))),
            (f"line_items[{n}][price_data][product_data][name]", label[:200]),
        ]
    return form


def setup_form(autopay_id: UUID, customer: str, done_url: str) -> list[tuple[str, str]]:
    """A Checkout Session that saves a bank account for an autopay, on its customer."""
    return [
        ("mode", "setup"),
        ("currency", "usd"),
        ("customer", customer),
        ("payment_method_types[0]", "us_bank_account"),
        ("success_url", f"{done_url}?autopay=1"),
        ("cancel_url", f"{done_url}?cancelled=1"),
        ("client_reference_id", str(autopay_id)),
        ("metadata[autopay_id]", str(autopay_id)),
        ("setup_intent_data[metadata][autopay_id]", str(autopay_id)),
    ]


def charge_form(
    payment_id: UUID, amount: Decimal, memo: str, account: Account
) -> list[tuple[str, str]]:
    """A payment from a saved bank account, confirmed now, the tenant not there (off session)."""
    form = [
        ("amount", str(int(amount * 100))),
        ("currency", "usd"),
        ("customer", account.customer),
        ("payment_method", account.method),
        ("payment_method_types[0]", "us_bank_account"),
        ("confirm", "true"),
        ("off_session", "true"),
        ("description", memo[:200]),
        ("metadata[payment_id]", str(payment_id)),
    ]
    if account.mandate:
        form.append(("mandate", account.mandate))
    return form


def payment_of(subject: Any, intent: Any) -> UUID | None:
    """The PMS's own id of the payment a report is about: on the session, or on its payment."""
    found = None
    if isinstance(subject, dict):
        found = subject.get("client_reference_id") or (subject.get("metadata") or {}).get(
            "payment_id"
        )
    if found is None and isinstance(intent, dict):
        found = (intent.get("metadata") or {}).get("payment_id")
    try:
        return UUID(str(found)) if found else None
    except ValueError:
        return None


def verify_signature(
    body: bytes, header: str, secret: str, now: float | None = None, tolerance: int = 300
) -> None:
    """Refuses a report Stripe didn't sign with this endpoint's secret, or signed more than
    `tolerance` seconds ago: Stripe-Signature carries t=<when> and v1=<HMAC-SHA256 of
    "<when>.<body>">, maybe more than one v1 while a secret is being rolled."""
    parts = [part.split("=", 1) for part in header.split(",") if "=" in part]
    stamps = [value for name, value in parts if name.strip() == "t"]
    signatures = [value for name, value in parts if name.strip() == "v1"]
    if len(stamps) != 1 or not stamps[0].isdigit() or not signatures:
        raise NotTheirs("the report has no Stripe signature")
    signed = f"{stamps[0]}.".encode() + body
    expected = hmac.new(secret.encode(), signed, hashlib.sha256).hexdigest()
    if not any(hmac.compare_digest(expected, s.strip()) for s in signatures):
        raise NotTheirs("the report's signature isn't this endpoint's")
    if abs((time.time() if now is None else now) - int(stamps[0])) > tolerance:
        raise NotTheirs("the report was signed too long ago")


class NoPartner(Exception):
    """A partner was named that isn't one, or without what it needs."""


def partner_named(name: str, environ: Mapping[str, str]) -> Partner | None:
    """The partner called `name` ("fake" or "stripe"), or None for an empty name: none."""
    chosen = name.strip()
    if not chosen:
        return None
    if chosen == "fake":
        return FakePartner()
    if chosen == "stripe":
        return StripePartner(
            environ.get("PMS_STRIPE_SECRET_KEY", ""), environ.get("PMS_STRIPE_WEBHOOK_SECRET", "")
        )
    raise NoPartner(f"no payments partner called {chosen!r}; known: fake, stripe")


# --- a company's own keys ---------------------------------------------------------------------

# A Stripe secret key (or a restricted key) and a webhook's signing secret, as Stripe writes them
# (NOT VERIFIED against a live account). Nothing else is ever stored.
SECRET_KEY = re.compile(r"^(sk|rk)_(test|live)_[A-Za-z0-9]{16,300}$")
WEBHOOK_SECRET = re.compile(r"^whsec_[A-Za-z0-9+/=]{16,300}$")

LATEST_KEYS = (
    "SELECT id, secret_key_sealed, webhook_secret_sealed FROM trust_payment_keys"
    " WHERE pmc_id = %s ORDER BY created_at DESC, id DESC LIMIT 1"
)
EVERY_KEYS = (
    "SELECT id, secret_key_sealed, webhook_secret_sealed FROM trust_payment_keys"
    " WHERE pmc_id = %s AND secret_key_sealed IS NOT NULL ORDER BY created_at DESC, id DESC"
)


class BadKeys(ValueError):
    """Keys that aren't a Stripe secret key and a webhook signing secret."""


def check_keys(secret_key: str, webhook_secret: str) -> tuple[Literal["test", "live"], str]:
    """Whether the keys look like Stripe's: their mode (test or live) and the secret key's last
    four, the only parts ever shown. It never puts either key in what it raises."""
    found = SECRET_KEY.match(secret_key)
    if found is None:
        raise BadKeys("That isn't a Stripe secret key. It starts with sk_live_ or sk_test_.")
    if WEBHOOK_SECRET.match(webhook_secret) is None:
        raise BadKeys("That isn't a webhook signing secret. It starts with whsec_.")
    mode: Literal["test", "live"] = "live" if found.group(2) == "live" else "test"
    return mode, secret_key[-4:]


def seal_keys(row: UUID, secret_key: str, webhook_secret: str, key: bytes) -> tuple[bytes, bytes]:
    """Each sealed with the row's id and what it is bound in, so neither opens in another row or
    in the other's place."""
    return (
        sealed.seal(secret_key, f"{row}:secret key", key),
        sealed.seal(webhook_secret, f"{row}:webhook secret", key),
    )


def many_companies(conn: psycopg.Connection[Any]) -> bool:
    """Whether the database holds more than one company."""
    row = conn.execute("SELECT count(*) FROM (SELECT 1 FROM trust_pmcs LIMIT 2) AS two").fetchone()
    return row is not None and row[0] > 1


class Resolver:
    """Which partner takes a company's payments: its own Stripe account once an admin entered its
    keys, else the server's partner (PMS_PAYMENTS), else none. A company with keys of its own that
    don't open here (the server lost its PMS_DATA_KEY, or another server's) takes no payment
    online: it never falls back to the server's account, which isn't where its money goes.

    The server's own keys are one Stripe account, paying out to one company's trust account, so a
    real partner of the server's serves only a server holding one company (`server_for`): on a
    server with more, a company without keys of its own would take its tenants' rent into another
    company's account, so it takes none online until it enters its own. The fake partner, which
    moves no money, serves any."""

    def __init__(self, server: Partner | None, environ: Mapping[str, str] = os.environ) -> None:
        self.server = server
        self._environ = environ

    @classmethod
    def of(cls, partner: Partner | Resolver | None) -> Resolver:
        return partner if isinstance(partner, Resolver) else cls(partner)

    def own(self, conn: psycopg.Connection[Any], pmc_id: UUID) -> Literal[False] | Partner | None:
        """The company's own partner; None when it has none (it uses the server's); False when
        it has keys that don't open here."""
        row = conn.execute(LATEST_KEYS, (pmc_id,)).fetchone()
        if row is None or row[1] is None:
            return None
        return self._opened(pmc_id, row) or False

    def every_own(self, conn: psycopg.Connection[Any], pmc_id: UUID) -> list[Partner]:
        """Each set of keys the company ever entered that opens here, newest first: a report
        about a payment started under keys since changed or taken away still lands, signed with
        the secret it was started under."""
        rows = conn.execute(EVERY_KEYS, (pmc_id,)).fetchall()
        return [partner for row in rows if (partner := self._opened(pmc_id, row)) is not None]

    def _opened(self, pmc_id: UUID, row: Any) -> Partner | None:
        try:
            key = sealed.key_from(self._environ, "payments keys")
            secret = sealed.unseal(bytes(row[1]), f"{row[0]}:secret key", key)
            webhook = sealed.unseal(bytes(row[2]), f"{row[0]}:webhook secret", key)
        except (sealed.NoKey, sealed.Unreadable) as failed:
            log.error("company %s's payments keys don't open on this server: %s", pmc_id, failed)
            return None
        return StripePartner(secret, webhook)

    def server_for(self, conn: psycopg.Connection[Any]) -> Partner | None:
        """The server's partner, where it may take a company's payments: the fake one always, a
        real one only on a server holding one company."""
        if self.server is None or self.server.name == "fake":
            return self.server
        return None if many_companies(conn) else self.server

    def for_pmc(self, conn: psycopg.Connection[Any], pmc_id: UUID) -> Partner | None:
        own = self.own(conn, pmc_id)
        if own is False:
            return None
        return own if own is not None else self.server_for(conn)

    def name_for(self, conn: psycopg.Connection[Any], pmc_id: UUID) -> str | None:
        partner = self.for_pmc(conn, pmc_id)
        return None if partner is None else partner.name
