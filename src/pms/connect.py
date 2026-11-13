"""Stripe Connect for the hosted product (docs/adr/0014-stripe-connect.md; ADR 0009, Hosted).

Occupella runs a platform Stripe account. Its secret key and the signing secret of its one
Connect webhook come only from the server's environment (PMS_STRIPE_CONNECT_SECRET_KEY,
PMS_STRIPE_CONNECT_WEBHOOK_SECRET; never Settings, a log or the database). Each company connects
its own Stripe account from Company setup through Stripe's hosted onboarding (an Account Link),
with no key pasted: a Standard account, made with the controller properties Stripe documents as
Standard's (the account pays Stripe's fees, Stripe carries its negative balances, its own full
dashboard, Stripe collects its requirements). The PMS keeps only the account's id
(`trust_connected_accounts`) and what Stripe says of it (`trust_connected_account_states`).

Tenants and owners pay by bank (ACH) or card through **direct charges** on the company's account
(`Stripe-Account`), each with Occupella's fee as the charge's application fee: the payment lands in
the company's Stripe balance and pays out to its own trust bank account, never through Occupella's
balance. What reaches trust is the charge less Stripe's fee less Occupella's; the books move both
out of trust from the company's fee account, never an owner's (src/pms/api/online_payments.py), as
they always moved the processor's fee. Who pays them follows the company's choice: the tenant on
top (a bank fee by `trust_payment_settings.ach_fee_payer`, a card fee by `trust_card_fee_settings`
where the state and the card networks allow a surcharge, `card_fee_terms`), or the company, from
its fees in trust. An owner's payment is always the company's (the founder, 2026-11-02).

Occupella's fee is a server setting per method (PMS_CONNECT_FEE_ACH, PMS_CONNECT_FEE_CARD:
"percent,fixed,minimum"), refused below what a payment costs the platform under the model chosen
(`cost`, `CHOSEN`), so it always lands above break-even; the ADR works the arithmetic.

PMS_CONNECT picks the platform: "stripe" (the keys above), "fake" (development and CI: onboarding,
bank and card payments and disputes on the PMS's own pages, calling nothing), or none, and every
company pays through its own keys or the server's partner as before (pms.payments.Resolver).

What Stripe calls each thing (Accounts with controller properties, Account Links, application_fee
_amount, Checkout's card funding filter, Connect webhooks' `account`, a dispute's balance
transactions) was written from Stripe's documentation as search excerpts showed it on 2026-11-13,
not from the pages themselves and not against a live account: NOT VERIFIED.
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import ROUND_CEILING, ROUND_FLOOR, ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any, Literal
from uuid import UUID, uuid4

import psycopg

from pms import deployment, payments

log = logging.getLogger(__name__)

CENT = Decimal("0.01")
ZERO = Decimal(0)
Method = Literal["ach", "card"]
METHODS: tuple[Method, ...] = ("ach", "card")
# The Connect partners' names, as payments, autopays and connected accounts record them.
PARTNERS = ("stripe_connect", "fake_connect")
# A card payment is 10,000.00 at most (the migration's check): its fee stays within what a
# payment's fee columns hold, and rent above it goes by bank.
CARD_MAX = Decimal("10000.00")


# --- what Stripe charges, as published -------------------------------------------------------


@dataclass(frozen=True)
class Price:
    """Stripe's processing price for a method: a percent (a fraction) and a fixed part, maybe
    capped."""

    percent: Decimal
    fixed: Decimal
    cap: Decimal | None = None

    def on(self, total: Decimal) -> Decimal:
        fee = (total * self.percent + self.fixed).quantize(CENT, ROUND_HALF_UP)
        return fee if self.cap is None else min(fee, self.cap)


# Stripe's US standard prices (stripe.com/pricing, as search excerpts showed it on 2026-11-13;
# NOT VERIFIED against the page): domestic cards 2.9% + 30 cents, ACH Direct Debit 0.8% capped
# at $5.00, and a $15.00 dispute fee. International cards (+1.5%) and currency conversion (+1%)
# aren't priced in: a tenant's card is taken as domestic, and the books record the fee Stripe
# actually kept, whatever it is.
STRIPE_PRICES: dict[Method, Price] = {
    "ach": Price(Decimal("0.008"), ZERO, Decimal("5.00")),
    "card": Price(Decimal("0.029"), Decimal("0.30")),
}
DISPUTE_FEE = Decimal("15.00")


@dataclass(frozen=True)
class Model:
    """What the platform itself pays Stripe under a Connect model, for each payment it takes on a
    connected account (stripe.com/connect/pricing, as search excerpts showed it on 2026-11-13;
    NOT VERIFIED against the page)."""

    name: str
    # Whether the platform pays Stripe's processing fee on a direct charge (controller.fees.payer
    # "application"), or the connected account does ("account").
    platform_pays_processing: bool
    # Connect's own fees on the platform: per monthly active account, and per payout sent.
    per_active_account_month: Decimal
    payout_percent: Decimal
    payout_fixed: Decimal
    # Whether the platform pays the dispute fee (and carries the account's losses).
    platform_pays_disputes: bool


# Standard: the connected account pays Stripe's fees, disputes and its own negative balances
# (controller.losses.payments "stripe"); "Stripe handles pricing for your users": no Connect fee.
STANDARD = Model("Standard, direct charges", False, ZERO, ZERO, ZERO, False)
# Express (or Custom): the platform handles pricing, pays processing on direct charges, $2.00 per
# monthly active account and 0.25% + 25 cents per payout, and is liable for disputes and losses.
EXPRESS = Model(
    "Express, direct charges", True, Decimal("2.00"), Decimal("0.0025"), Decimal("0.25"), True
)
# The model Occupella uses (docs/adr/0014-stripe-connect.md).
CHOSEN = STANDARD

# How the costs that aren't per payment are spread over payments, at their worst for the
# platform: an account active in a month with one payment in it, each payment paid out on its
# own, and disputes at 1 in 100 card payments and 1 in 200 bank payments (Stripe's own dispute
# rates aren't published; card networks act at about 1%, NOT VERIFIED).
PAYMENTS_PER_ACTIVE_ACCOUNT_MONTH = 1
PAYOUTS_PER_PAYMENT = 1
DISPUTE_RATE: dict[Method, Decimal] = {"ach": Decimal("0.005"), "card": Decimal("0.01")}


def cost(model: Model, method: Method, amount: Decimal) -> Decimal:
    """What one payment of `amount` by `method` costs the platform under `model`, to the cent,
    rounded up: Stripe's processing on it (when the platform pays it), the month's active-account
    fee spread over the account's payments, the payout fee on what is paid out, and the dispute
    fee spread over payments. Zero under Standard."""
    total = ZERO
    processing = STRIPE_PRICES[method].on(amount)
    if model.platform_pays_processing:
        total += processing
    total += model.per_active_account_month / PAYMENTS_PER_ACTIVE_ACCOUNT_MONTH
    paid_out = amount - (processing if not model.platform_pays_processing else ZERO)
    total += (paid_out * model.payout_percent + model.payout_fixed) * PAYOUTS_PER_PAYMENT
    if model.platform_pays_disputes:
        total += DISPUTE_FEE * DISPUTE_RATE[method]
    return total.quantize(CENT, ROUND_CEILING)


# --- Occupella's fee ------------------------------------------------------------------------


@dataclass(frozen=True)
class Fee:
    """Occupella's fee on a payment: a percent (a fraction) of what goes to the lease or the owner
    plus a fixed part, never below a minimum."""

    percent: Decimal
    fixed: Decimal
    minimum: Decimal

    def on(self, amount: Decimal) -> Decimal:
        return max(self.minimum, (amount * self.percent + self.fixed).quantize(CENT, ROUND_HALF_UP))

    def words(self) -> str:
        """ "0.25% + $0.30, at least $0.50", or "$0.50" for a flat fee."""
        parts = []
        if self.percent:
            parts.append(f"{(self.percent * 100).normalize():f}%")
        if self.fixed or not parts:
            parts.append(f"${self.fixed:,.2f}")
        said = " + ".join(parts)
        if self.minimum > self.fixed or (self.percent and self.minimum):
            said += f", at least ${self.minimum:,.2f}"
        return said


# The margin above cost: under Standard the platform's cost is nothing, so the default fee is the
# margin alone, 50 cents a payment by either method (docs/adr/0014-stripe-connect.md).
MARGIN = Decimal("0.50")
DEFAULT_FEES: dict[Method, Fee] = {
    "ach": Fee(ZERO, MARGIN, MARGIN),
    "card": Fee(ZERO, MARGIN, MARGIN),
}
# Amounts each fee is checked against cost at: a cent, the ACH cap's turning point, rents, the
# card ceiling and the largest payment.
CHECKED_AT = tuple(
    Decimal(a)
    for a in ("0.01", "1.00", "50.00", "625.00", "1500.00", "4000.00", "10000.00", "100000.00")
)
# A fee setting past these is a typo, not a price: the most such a fee comes to on the largest
# payment (100,000.00) stays within what a payment's fee columns hold (1,000.00).
MOST_PERCENT = Decimal("0.9")
MOST_FIXED = Decimal("50.00")


class BadFee(ValueError):
    """A fee setting that can't be read, or that falls below what a payment costs."""


def turning_points(fee: Fee, method: Method) -> list[Decimal]:
    """Where a fee or a cost changes slope: both are straight lines between these, so the fee
    stays above the cost everywhere when it does at each of them and at the ends (CHECKED_AT
    holds the ends). The fee's minimum stops binding at (minimum - fixed) / percent; Stripe's
    price reaches its cap at (cap - fixed) / percent."""
    found = []
    if fee.percent > 0 and fee.minimum > fee.fixed:
        found.append(((fee.minimum - fee.fixed) / fee.percent).quantize(CENT, ROUND_CEILING))
    price = STRIPE_PRICES[method]
    if price.cap is not None and price.percent > 0:
        found.append(((price.cap - price.fixed) / price.percent).quantize(CENT, ROUND_CEILING))
    return [
        point + step
        for point in found
        for step in (-CENT, ZERO, CENT)
        if CHECKED_AT[0] <= point + step <= CHECKED_AT[-1]
    ]


def below_cost(fee: Fee, method: Method, model: Model = CHOSEN) -> Decimal | None:
    """The first amount at which `fee` falls below the platform's cost under `model`, or None:
    checked at the ends, at rents in between, and at every point either changes slope
    (turning_points), each a cent either side for rounding."""
    for amount in sorted({*CHECKED_AT, *turning_points(fee, method)}):
        if fee.on(amount) < cost(model, method, amount):
            return amount
    return None


def read_fee(method: Method, text: str) -> Fee:
    """A fee from its setting, "percent,fixed,minimum" (percent in percent: "0.25" is 0.25%),
    refused when it can't be read, is negative or absurd, or falls below cost."""
    parts = [part.strip() for part in text.split(",")]
    try:
        if len(parts) != 3:
            raise InvalidOperation
        percent, fixed, minimum = (Decimal(part) for part in parts)
    except InvalidOperation:
        raise BadFee(
            f"PMS_CONNECT_FEE_{method.upper()} is 'percent,fixed,minimum', like 0,0.50,0.50"
        ) from None
    if not all(value.is_finite() for value in (percent, fixed, minimum)):
        raise BadFee(f"PMS_CONNECT_FEE_{method.upper()} names a number that isn't one")
    if (
        min(percent, fixed, minimum) < 0
        or percent > MOST_PERCENT
        or max(fixed, minimum) > (MOST_FIXED)
    ):
        raise BadFee(
            f"PMS_CONNECT_FEE_{method.upper()} is out of range: a percent of 0 to {MOST_PERCENT},"
            f" and fixed and minimum amounts of 0 to {MOST_FIXED}"
        )
    fee = Fee(
        (percent / 100).quantize(Decimal("0.000001")),
        fixed.quantize(CENT, ROUND_HALF_UP),
        minimum.quantize(CENT, ROUND_HALF_UP),
    )
    short = below_cost(fee, method)
    if short is not None:
        raise BadFee(
            f"PMS_CONNECT_FEE_{method.upper()} falls below what a payment of ${short:,.2f} costs"
            f" the platform (${cost(CHOSEN, method, short):,.2f}): Occupella's fee always lands"
            " above break-even (docs/adr/0014-stripe-connect.md)"
        )
    return fee


def fees_from(environ: Mapping[str, str]) -> dict[Method, Fee]:
    """Occupella's fee on each method: its setting where one is given, else the default."""
    return {
        method: (
            read_fee(method, environ[f"PMS_CONNECT_FEE_{method.upper()}"])
            if environ.get(f"PMS_CONNECT_FEE_{method.upper()}", "").strip()
            else DEFAULT_FEES[method]
        )
        for method in METHODS
    }


# --- what a tenant pays on top ----------------------------------------------------------------


def stripe_fee(method: Method, total: Decimal) -> Decimal:
    """Stripe's fee on a charge of `total`, at its published price."""
    return STRIPE_PRICES[method].on(total)


def gross_up(method: Method, amount: Decimal, platform_fee: Decimal) -> Decimal:
    """What a payer who pays the fees adds to `amount` so that Stripe's fee on the whole and
    Occupella's fee come to no more than it: the smallest such cents."""
    price = STRIPE_PRICES[method]

    def covers(added: Decimal) -> bool:
        return stripe_fee(method, amount + added) + platform_fee <= added

    guess = ((amount * price.percent + price.fixed + platform_fee) / (1 - price.percent)).quantize(
        CENT, ROUND_FLOOR
    )
    if price.cap is not None:
        guess = min(guess, price.cap + platform_fee)
    # Stripe's fee grows a cent at a time as what it is taken on grows, so covering only gets
    # easier: down while one cent less still covers, then up until it does.
    added = max(guess, ZERO)
    while added > 0 and covers(added - CENT):
        added -= CENT
    while not covers(added):
        added += CENT
    return added


# Card surcharges (docs/law.md, "Rent paid online"; research of 2026-11-13 from merchant-compliance
# summaries, NOT VERIFIED against the statutes). Visa caps a US surcharge at 3% (Mastercard at
# 4%), never more than the merchant's cost; no debit or prepaid card is surcharged anywhere. A
# state that bans a surcharge to a consumer bans it to a tenant; a state's lower cap holds there.
NETWORK_CAP = Decimal("0.03")
SURCHARGE_BANNED: dict[str, str] = {
    "CT": "Connecticut General Statutes 42-133ff bans a card surcharge",
    "MA": "Massachusetts General Laws chapter 140D, section 28A bans a card surcharge",
    "ME": "Maine Revised Statutes title 9-A, section 8-509 bans a card surcharge",
    "PR": "Puerto Rico bans a card surcharge",
}
SURCHARGE_CAPPED: dict[str, tuple[Decimal, str]] = {
    "CO": (Decimal("0.02"), "Colorado Revised Statutes 5-2-212 caps a card surcharge at 2%"),
    "OK": (Decimal("0.02"), "Oklahoma caps a card surcharge at 2% (NOT VERIFIED)"),
}
# Where a fee for paying online is allowed only beside a free way to pay (docs/law.md): staff
# still record checks, money orders and cash, which is one.
FREE_WAY_TO_PAY: dict[str, str] = {
    "CA": "California Civil Code 1947.3: a tenant always has a way to pay that is neither cash"
    " nor online",
    "OR": "Oregon ORS 90.302: a card fee passes to a tenant only when the landlord also takes cash"
    " or check, and SB 1523 lets no tenant be required to pay online",
    "IL": "Illinois Public Act 103-0809: a portal charging a fee needs a free way to pay",
    "CO": "Colorado HB25-1090: no processing charge unless a free way to pay exists",
}


@dataclass(frozen=True)
class CardTerms:
    """Who pays a card payment's fees, and how much of them the tenant may be asked for."""

    tenant_pays: bool
    # The most a tenant's card fee may be, as a fraction of the payment.
    cap: Decimal
    # Why the company pays though it chose the tenant, in words; or None.
    why_company: str | None


def card_fee_terms(chosen: str, state: str | None, recurring: bool) -> CardTerms:
    """Whether a tenant pays the card fee: only when the company chose so, the property's state
    allows a surcharge, and it isn't autopay (a fee on a recurring card payment is the company's:
    Visa reportedly forbids a convenience fee on one, docs/law.md, NOT VERIFIED). The tenant's
    part is capped at the networks' 3%, or the state's lower cap."""
    banned = SURCHARGE_BANNED.get(state or "")
    capped = SURCHARGE_CAPPED.get(state or "")
    cap = min(NETWORK_CAP, capped[0]) if capped else NETWORK_CAP
    if chosen != "tenant":
        return CardTerms(False, cap, None)
    if banned is not None:
        return CardTerms(False, cap, banned)
    if recurring:
        return CardTerms(False, cap, "a card fee on autopay is the company's")
    return CardTerms(True, cap, None)


def tenant_card_fee(amount: Decimal, platform_fee: Decimal, cap: Decimal) -> Decimal:
    """What a tenant who pays the card fee adds: Stripe's fee and Occupella's, never more than
    the cap (the company pays the rest)."""
    return min(gross_up("card", amount, platform_fee), (amount * cap).quantize(CENT, ROUND_FLOOR))


# --- what Stripe reports ----------------------------------------------------------------------


@dataclass(frozen=True)
class AccountState:
    """What Stripe says of a connected account: whether it takes charges and pays out, and what
    it still needs."""

    account: str
    charges_enabled: bool
    payouts_enabled: bool
    details_submitted: bool
    requirements_due: tuple[str, ...]
    # Stripe's event, or None when the PMS asked Stripe itself.
    event_id: str | None
    reported_at: datetime


DisputeKind = Literal["opened", "won", "lost"]


@dataclass(frozen=True)
class Dispute:
    """A card dispute (or a bank's late return, as Stripe reports one) on a payment."""

    payment_id: UUID
    dispute_id: str
    kind: DisputeKind
    # Opened: the dispute fee Stripe took. Won: what of it came back. Lost: None.
    fee: Decimal | None
    event_id: str
    happened_at: datetime


Item = payments.Event | payments.Saved | Dispute | AccountState


@dataclass(frozen=True)
class Report:
    """A Connect webhook's report, read and checked: the connected account it is about, and the
    event as Stripe wrote it."""

    account: str | None
    kind: str
    subject: dict[str, Any]
    event_id: str
    happened_at: datetime


# Every event the platform's Connect endpoint must be sent (Settings, Company setup lists them).
CONNECT_EVENTS = tuple(
    sorted(
        {
            *payments.STRIPE_KINDS,
            "setup_intent.succeeded",
            "account.updated",
            "charge.dispute.closed",
            "charge.dispute.funds_reinstated",
        }
    )
)


def cents_of(value: Any) -> Decimal:
    return (Decimal(int(value)) / 100).quantize(CENT)


def dispute_fees(subject: Mapping[str, Any]) -> tuple[Decimal, Decimal]:
    """A dispute's fee as its balance transactions say: what was taken when funds were withdrawn,
    and what of it came back when they were reinstated."""
    taken, back = ZERO, ZERO
    for moved in subject.get("balance_transactions") or []:
        if not isinstance(moved, dict):
            continue
        fee = cents_of(moved.get("fee") or 0)
        if int(moved.get("amount") or 0) < 0:
            taken += fee
        else:
            back -= fee  # a reinstatement's fee is negative when the fee comes back
    return taken, max(back, ZERO)


def account_state(subject: Mapping[str, Any], event_id: str | None, at: datetime) -> AccountState:
    requirements = subject.get("requirements") or {}
    due = sorted(
        {
            str(item)[:200]
            for key in ("currently_due", "past_due")
            for item in (requirements.get(key) or [])
        }
    )[:100]
    return AccountState(
        str(subject["id"]),
        bool(subject.get("charges_enabled")),
        bool(subject.get("payouts_enabled")),
        bool(subject.get("details_submitted")),
        tuple(due),
        event_id,
        at,
    )


# --- talking to Stripe ------------------------------------------------------------------------

API = "https://api.stripe.com/v1"
# Checkout's card funding filter (payment_method_options.card.restrictions.funding_types_blocked)
# came in this API version, per Stripe's changelog as a search showed it on 2026-11-13 (NOT
# VERIFIED): only the session that blocks a debit or prepaid card names it.
FUNDING_FILTER_VERSION = "2026-08-26.dahlia"


def stripe_call(
    secret_key: str,
    method: str,
    path: str,
    form: list[tuple[str, str]],
    key: str = "",
    account: str | None = None,
    version: str = payments.STRIPE_VERSION,
    timeout: float = 20.0,
) -> Any:
    """One call to Stripe's API, as the platform, on a connected account when `account` names
    one. Neither key nor account is ever put in what it raises."""
    data = urllib.parse.urlencode(form).encode() if method == "POST" else None
    url = f"{API}{path}" + ("" if data else "?" + urllib.parse.urlencode(form))
    request = urllib.request.Request(url, data=data, method=method)  # noqa: S310 (fixed https)
    request.add_header("Authorization", f"Bearer {secret_key}")
    request.add_header("Stripe-Version", version)
    if account:
        request.add_header("Stripe-Account", account)
    if key:
        request.add_header("Idempotency-Key", key)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as answer:  # noqa: S310
            return json.load(answer)
    except urllib.error.HTTPError as refused:
        raise payments.PartnerFailed(
            f"Stripe refused {method} {path.split('/acct_')[0]}: HTTP {refused.code}"
        ) from None
    except (OSError, ValueError) as failed:
        raise payments.PartnerFailed(f"Stripe did not answer {method}: {failed}") from None


def account_form(pmc_id: UUID) -> list[tuple[str, str]]:
    """A Standard account (by its controller properties), in the US, named for the company."""
    return [
        ("country", "US"),
        ("controller[stripe_dashboard][type]", "full"),
        ("controller[fees][payer]", "account"),
        ("controller[losses][payments]", "stripe"),
        ("controller[requirement_collection]", "stripe"),
        ("metadata[pmc_id]", str(pmc_id)),
    ]


def payment_types(method: Method) -> str:
    return "card" if method == "card" else "us_bank_account"


def connect_checkout_form(
    payment_id: UUID,
    lines: list[tuple[str, Decimal]],
    done_url: str,
    method: Method,
    platform_fee: Decimal,
    credit_only: bool,
) -> list[tuple[str, str]]:
    """payments.checkout_form for a direct charge: the method's payment type, Occupella's fee as
    the application fee, and, when the tenant pays a card fee, no debit or prepaid card."""
    form = [
        (name, payment_types(method) if name == "payment_method_types[0]" else value)
        for name, value in payments.checkout_form(payment_id, lines, done_url)
    ]
    form.append(("payment_intent_data[application_fee_amount]", str(int(platform_fee * 100))))
    if credit_only:
        form += [
            ("payment_method_options[card][restrictions][funding_types_blocked][0]", "debit"),
            ("payment_method_options[card][restrictions][funding_types_blocked][1]", "prepaid"),
        ]
    return form


class StripeConnected(payments.StripePartner):
    """A company's connected account, through the platform: every call names the account
    (Stripe-Account), each payment carries Occupella's fee, and reports come through the
    platform's Connect webhook, never one of the company's own."""

    name = "stripe_connect"

    def __init__(
        self, secret_key: str, webhook_secret: str, account: str, conn: Any = None
    ) -> None:
        super().__init__(secret_key, webhook_secret)
        self.account = account
        # The request's connection: an autopay's charge reads its payment's method and fee.
        self.conn = conn

    def _call(self, method: str, path: str, form: list[tuple[str, str]], key: str = "") -> Any:
        return stripe_call(self._key, method, path, form, key, account=self.account)

    def checkout(
        self, payment_id: UUID, lines: list[tuple[str, Decimal]], done_url: str
    ) -> payments.Checkout:
        raise payments.PartnerFailed("a payment through Connect starts at Connect's checkout")

    def save_account(self, autopay_id: UUID, done_url: str) -> payments.Checkout:
        raise payments.PartnerFailed("an autopay through Connect saves at Connect's page")

    def connect_checkout(
        self,
        payment_id: UUID,
        lines: list[tuple[str, Decimal]],
        done_url: str,
        method: Method,
        platform_fee: Decimal,
        credit_only: bool,
    ) -> payments.Checkout:
        session = stripe_call(
            self._key,
            "POST",
            "/checkout/sessions",
            connect_checkout_form(payment_id, lines, done_url, method, platform_fee, credit_only),
            key=f"checkout-{payment_id}",
            account=self.account,
            version=FUNDING_FILTER_VERSION if credit_only else payments.STRIPE_VERSION,
        )
        try:
            return payments.Checkout(ref=str(session["id"]), url=str(session["url"]))
        except (KeyError, TypeError) as failed:
            raise payments.PartnerFailed(f"Stripe's session came without {failed}") from None

    def connect_save(self, autopay_id: UUID, done_url: str, method: Method) -> payments.Checkout:
        """A customer on the connected account, then a setup session saving a bank account or a
        card for the autopay."""
        customer = self._call(
            "POST",
            "/customers",
            [("metadata[autopay_id]", str(autopay_id))],
            key=f"customer-{autopay_id}",
        )
        try:
            customer_id = str(customer["id"])
        except (KeyError, TypeError) as failed:
            raise payments.PartnerFailed(f"Stripe's customer came without {failed}") from None
        form = [
            (name, payment_types(method) if name == "payment_method_types[0]" else value)
            for name, value in payments.setup_form(autopay_id, customer_id, done_url)
        ]
        session = self._call("POST", "/checkout/sessions", form, key=f"setup-{autopay_id}")
        try:
            return payments.Checkout(ref=str(session["id"]), url=str(session["url"]))
        except (KeyError, TypeError) as failed:
            raise payments.PartnerFailed(f"Stripe's session came without {failed}") from None

    def charge(
        self, payment_id: UUID, amount: Decimal, memo: str, account: payments.Account
    ) -> str:
        """An autopay's payment from the saved bank account or card, off session, on the account
        it was saved at, with its fee; found again rather than made twice (as StripePartner)."""
        method, fee = charge_terms(self.conn, payment_id, self.account)
        listed = self._call(
            "GET", "/payment_intents", [("customer", account.customer), ("limit", "100")]
        )
        for intent in listed.get("data", []) if isinstance(listed, dict) else []:
            if (intent.get("metadata") or {}).get("payment_id") == str(payment_id):
                return str(intent["id"])
        form = [
            (name, payment_types(method) if name == "payment_method_types[0]" else value)
            for name, value in payments.charge_form(payment_id, amount, memo, account)
            if not (method == "card" and name == "mandate")
        ]
        form.append(("application_fee_amount", str(int(fee * 100))))
        made = self._call("POST", "/payment_intents", form, key=f"autopay-{payment_id}")
        try:
            return str(made["id"])
        except (KeyError, TypeError) as failed:
            raise payments.PartnerFailed(f"Stripe's payment came without {failed}") from None

    def saved(self, setup_intent: str, event_id: str, happened: datetime) -> payments.Saved | None:
        """A saved bank account or card: a card reads as its brand and last four."""
        intent = self._call(
            "GET", f"/setup_intents/{setup_intent}", [("expand[]", "payment_method")]
        )
        try:
            if intent.get("status") != "succeeded":
                return None
            autopay = (intent.get("metadata") or {}).get("autopay_id")
            method = intent["payment_method"]
            card = method.get("card")
            if card:
                brand = str(card.get("brand") or "Card").title()
                label = f"{brand} ••••{card.get('last4') or '????'}"
            else:
                bank = method.get("us_bank_account") or {}
                label = (
                    f"{bank.get('bank_name') or 'Bank account'} ••••{bank.get('last4') or '????'}"
                )
            return payments.Saved(
                UUID(str(autopay)),
                payments.Account(str(intent["customer"]), str(method["id"]), intent.get("mandate")),
                label[:80],
                event_id,
                happened,
            )
        except (KeyError, TypeError, ValueError, AttributeError) as failed:
            raise payments.PartnerFailed(
                f"Stripe's saved account can't be read: {failed}"
            ) from None

    def event(self, body: bytes, headers: Mapping[str, str]) -> payments.Event | None:
        raise payments.NotTheirs("a connected account reports through the platform's webhook")

    def read(self, report: Report) -> list[Item]:
        """What a Connect report says, about this account."""
        subject, kind = report.subject, report.kind
        if kind == "account.updated":
            return [account_state(subject, report.event_id, report.happened_at)]
        if kind == "checkout.session.completed" and subject.get("mode") == "setup":
            if not subject.get("setup_intent"):
                return []
            saved = self.saved(str(subject["setup_intent"]), report.event_id, report.happened_at)
            return [] if saved is None else [saved]
        if kind == "setup_intent.succeeded":
            if not (subject.get("metadata") or {}).get("autopay_id"):
                return []
            saved = self.saved(str(subject["id"]), report.event_id, report.happened_at)
            return [] if saved is None else [saved]
        if kind.startswith("charge.dispute."):
            return self.dispute(report)
        paid_kind = payments.STRIPE_KINDS.get(kind)
        if paid_kind is None:
            return []
        if kind == "checkout.session.completed" and subject.get("payment_status") != "paid":
            return []
        payment = payments.payment_of(subject, None)
        if payment is None:
            return []
        fee = None
        if paid_kind == "paid":
            is_intent = subject.get("object") == "payment_intent"
            fee = self.fee_of(subject["id"] if is_intent else subject["payment_intent"])
        return [payments.Event(payment, paid_kind, report.event_id, report.happened_at, fee)]

    def dispute(self, report: Report) -> list[Item]:
        """A dispute's reports: created (the payment came back, and the dispute fee was taken),
        closed won or lost, and funds reinstated (won)."""
        subject = report.subject
        intent_id = subject.get("payment_intent")
        if not intent_id:
            return []  # a charge the PMS didn't start through a payment
        intent = self._call("GET", f"/payment_intents/{intent_id}", [])
        payment = payments.payment_of(None, intent)
        if payment is None:
            return []
        dispute_id = str(subject.get("id") or "")[:200] or report.event_id
        taken, back = dispute_fees(subject)
        at = report.happened_at
        if report.kind == "charge.dispute.created":
            return [
                payments.Event(payment, "returned", report.event_id, at),
                Dispute(payment, dispute_id, "opened", taken, report.event_id, at),
            ]
        status = subject.get("status")
        if report.kind == "charge.dispute.funds_reinstated" or (
            report.kind == "charge.dispute.closed" and status == "won"
        ):
            return [Dispute(payment, dispute_id, "won", back, report.event_id, at)]
        if report.kind == "charge.dispute.closed" and status == "lost":
            return [Dispute(payment, dispute_id, "lost", None, report.event_id, at)]
        return []


def charge_terms(conn: Any, payment_id: UUID, account: str) -> tuple[Method, Decimal]:
    """An autopay payment's method and Occupella's fee, as it was started, refused when it was
    started on another connected account than the one asked to take it."""
    if conn is None:
        raise payments.PartnerFailed("a payment through Connect is charged within a request")
    row = conn.execute(
        "SELECT p.method, p.platform_fee, c.account_id FROM trust_online_payments AS p"
        " JOIN trust_connected_payments AS c ON c.online_payment_id = p.id WHERE p.id = %s",
        (payment_id,),
    ).fetchone()
    if row is None or row[2] != account:
        raise payments.PartnerFailed("this payment wasn't started on this connected account")
    method: Method = "card" if row[0] == "card" else "ach"
    return method, Decimal(row[1])


class StripePlatform:
    """Occupella's platform account: it makes companies' accounts and their onboarding links,
    reads what Stripe says of them, and reads the Connect webhook's signed reports."""

    name = "stripe_connect"

    def __init__(self, secret_key: str, webhook_secret: str) -> None:
        if not secret_key or not webhook_secret:
            raise payments.NoPartner(
                "PMS_CONNECT=stripe needs PMS_STRIPE_CONNECT_SECRET_KEY and"
                " PMS_STRIPE_CONNECT_WEBHOOK_SECRET"
            )
        self._key = secret_key
        self._webhook_secret = webhook_secret
        # Whether its key moves real money; a report from the other mode is never booked.
        self.live = deployment.live_key(secret_key)

    def create_account(self, pmc_id: UUID, nth: int) -> str:
        made = stripe_call(
            self._key, "POST", "/accounts", account_form(pmc_id), key=f"account-{pmc_id}-{nth}"
        )
        try:
            return str(made["id"])
        except (KeyError, TypeError) as failed:
            raise payments.PartnerFailed(f"Stripe's account came without {failed}") from None

    def onboarding_link(self, account: str, refresh_url: str, return_url: str) -> str:
        link = stripe_call(
            self._key,
            "POST",
            "/account_links",
            [
                ("account", account),
                ("refresh_url", refresh_url),
                ("return_url", return_url),
                ("type", "account_onboarding"),
            ],
        )
        try:
            return str(link["url"])
        except (KeyError, TypeError) as failed:
            raise payments.PartnerFailed(f"Stripe's link came without {failed}") from None

    def state(self, account: str) -> AccountState:
        found = stripe_call(self._key, "GET", f"/accounts/{account}", [])
        try:
            return account_state(found, None, datetime.now(UTC))
        except (KeyError, TypeError) as failed:
            raise payments.PartnerFailed(f"Stripe's account came without {failed}") from None

    def bound(self, account: str, conn: Any = None) -> StripeConnected:
        return StripeConnected(self._key, self._webhook_secret, account, conn)

    def report(self, body: bytes, headers: Mapping[str, str]) -> Report | None:
        """The Connect webhook's report, its signature checked; None for one from the other mode
        (acknowledged and never booked, docs/edge-cases.md P-048) or about the platform's own
        account."""
        payments.verify_signature(body, headers.get("stripe-signature", ""), self._webhook_secret)
        try:
            found = json.loads(body)
            if not isinstance(found, dict):
                raise TypeError("a report is an object")
            report = Report(
                found.get("account"),
                str(found["type"]),
                dict(found["data"]["object"]),
                str(found["id"]),
                datetime.fromtimestamp(int(found["created"]), UTC),
            )
        except (KeyError, TypeError, ValueError) as failed:
            raise payments.NotTheirs(f"Stripe's report can't be read: {failed}") from None
        if found.get("livemode") is not self.live:
            log.warning(
                "Stripe Connect report %s is %s mode, not the platform's %s mode: ignored",
                report.event_id,
                "live" if found.get("livemode") is True else "test or unstated",
                "live" if self.live else "test",
            )
            return None
        if not report.account:
            return None  # the platform's own account: nothing of a company's
        return report


# --- the fake platform ------------------------------------------------------------------------


class FakeConnected:
    """A company's connected account at the fake platform: its pages are the PMS's own
    (/pay/fake-connect-pay#<payment>, /pay/fake-connect-save#<autopay>), where buttons say what
    the bank, the card network or a dispute did; it calls nothing."""

    name = "fake_connect"
    live = False

    def __init__(self, account: str, conn: Any = None) -> None:
        self.account = account
        self.conn = conn

    @staticmethod
    def _base(done_url: str) -> str:
        return done_url.split("/pay/", 1)[0]

    def checkout(
        self, payment_id: UUID, lines: list[tuple[str, Decimal]], done_url: str
    ) -> payments.Checkout:
        raise payments.PartnerFailed("a payment through Connect starts at Connect's checkout")

    def save_account(self, autopay_id: UUID, done_url: str) -> payments.Checkout:
        raise payments.PartnerFailed("an autopay through Connect saves at Connect's page")

    def connect_checkout(
        self,
        payment_id: UUID,
        lines: list[tuple[str, Decimal]],
        done_url: str,
        method: Method,
        platform_fee: Decimal,
        credit_only: bool,
    ) -> payments.Checkout:
        return payments.Checkout(
            ref=f"fake_connect_{payment_id.hex}",
            url=f"{self._base(done_url)}/pay/fake-connect-pay#{payment_id}",
        )

    def connect_save(self, autopay_id: UUID, done_url: str, method: Method) -> payments.Checkout:
        return payments.Checkout(
            ref=f"fake_connect_setup_{autopay_id.hex}",
            url=f"{self._base(done_url)}/pay/fake-connect-save#{autopay_id}",
        )

    def charge(
        self, payment_id: UUID, amount: Decimal, memo: str, account: payments.Account
    ) -> str:
        charge_terms(self.conn, payment_id, self.account)
        return f"fake_connect_pi_{payment_id.hex}"

    def event(self, body: bytes, headers: Mapping[str, str]) -> payments.Event | None:
        raise payments.NotTheirs("the fake platform reports through its own pages")


class FakePlatform:
    """Development and CI's platform: onboarding is the PMS's own page (/pay/fake-connect#<acct>),
    where a button finishes it or leaves something Stripe would still need."""

    name = "fake_connect"
    live = False

    def create_account(self, pmc_id: UUID, nth: int) -> str:
        return f"fake_acct_{uuid4().hex[:16]}"

    def onboarding_link(self, account: str, refresh_url: str, return_url: str) -> str:
        base = return_url.split("/settings/", 1)[0]
        return f"{base}/pay/fake-connect#{account}"

    def state(self, account: str) -> AccountState:
        raise payments.PartnerFailed("the fake platform says what an account is on its page")

    def bound(self, account: str, conn: Any = None) -> FakeConnected:
        return FakeConnected(account, conn)


Platform = StripePlatform | FakePlatform
Connected = StripeConnected | FakeConnected


def fake_saved(autopay_id: UUID, method: Method, happened_at: datetime) -> payments.Saved:
    """What the fake platform's page reports when a tenant saves a bank account or a card."""
    label = "Test card ••••4242" if method == "card" else "Test bank ••••0000"
    return payments.Saved(
        autopay_id,
        payments.Account(f"fake_cus_{autopay_id.hex[:12]}", f"fake_pm_{autopay_id.hex[:12]}"),
        label,
        f"fake_saved_{autopay_id.hex}",
        happened_at,
    )


class NoPlatform(ValueError):
    """PMS_CONNECT names no platform, or one without what it needs."""


def platform_named(name: str, environ: Mapping[str, str]) -> Platform | None:
    """The platform PMS_CONNECT names: "stripe", "fake", or none."""
    chosen = name.strip()
    if not chosen:
        return None
    if chosen == "fake":
        return FakePlatform()
    if chosen == "stripe":
        try:
            return StripePlatform(
                environ.get("PMS_STRIPE_CONNECT_SECRET_KEY", ""),
                environ.get("PMS_STRIPE_CONNECT_WEBHOOK_SECRET", ""),
            )
        except payments.NoPartner as missing:
            raise NoPlatform(str(missing)) from None
    raise NoPlatform(f"no Connect platform called {chosen!r}; known: fake, stripe")


# --- which partner takes a company's payments -------------------------------------------------

LATEST_ACCOUNT = (
    "SELECT account_id, partner, livemode FROM trust_connected_accounts"
    " WHERE pmc_id = %s ORDER BY created_at DESC, id DESC LIMIT 1"
)
LATEST_STATE = (
    "SELECT charges_enabled, payouts_enabled, details_submitted, requirements_due, reported_at"
    " FROM trust_connected_account_states WHERE pmc_id = %s AND account_id = %s"
    " ORDER BY reported_at DESC, created_at DESC, id DESC LIMIT 1"
)


@dataclass(frozen=True)
class Connection:
    """A company's connected account as the books know it."""

    account: str
    partner: str
    livemode: bool
    charges_enabled: bool = False
    payouts_enabled: bool = False
    details_submitted: bool = False
    requirements_due: tuple[str, ...] = field(default_factory=tuple)
    reported_at: datetime | None = None


def connection_of(conn: psycopg.Connection[Any], pmc_id: UUID) -> Connection | None:
    """The company's connected account, with what Stripe last said of it; None when it has none
    (never connected, or disconnected)."""
    row = conn.execute(LATEST_ACCOUNT, (pmc_id,)).fetchone()
    if row is None or row[0] is None:
        return None
    state = conn.execute(LATEST_STATE, (pmc_id, row[0])).fetchone()
    if state is None:
        return Connection(row[0], row[1], row[2])
    return Connection(
        row[0], row[1], row[2], state[0], state[1], state[2], tuple(state[3]), state[4]
    )


class ConnectResolver(payments.Resolver):
    """payments.Resolver, with the platform: a company whose connected account (through this
    platform, in its mode) takes charges pays through it; any other company as before, through
    its own keys or the server's partner (a company still onboarding keeps those until Stripe
    says its account takes charges)."""

    def __init__(
        self,
        server: payments.Partner | None,
        platform: Platform,
        environ: Mapping[str, str],
        live: bool = False,
    ) -> None:
        super().__init__(server, environ, live)
        self.platform = platform

    def connected(self, conn: psycopg.Connection[Any], pmc_id: UUID) -> Connection | None:
        """The company's connected account that may take its payments now: through this
        platform, in its mode (a test account never takes money on a live platform, P-048), and
        taking charges."""
        found = connection_of(conn, pmc_id)
        if (
            found is None
            or found.partner != self.platform.name
            or found.livemode != self.platform.live
            or not found.charges_enabled
        ):
            return None
        return found

    def for_pmc(self, conn: psycopg.Connection[Any], pmc_id: UUID) -> payments.Partner | None:
        found = self.connected(conn, pmc_id)
        if found is not None:
            partner: payments.Partner = self.platform.bound(found.account, conn)
            return partner
        return super().for_pmc(conn, pmc_id)


def resolver_from(
    payments_name: str, connect_name: str, environ: Mapping[str, str], live: bool
) -> payments.Resolver:
    """The server's resolver: with a Connect platform when PMS_CONNECT names one, its fee
    settings read now so a bad one refuses to start (they are read from the environment again
    where each payment starts)."""
    server = payments.partner_named(payments_name, environ)
    platform = platform_named(connect_name, environ)
    if platform is None:
        return payments.Resolver(server, environ, live=live)
    fees_from(environ)
    return ConnectResolver(server, platform, environ, live=live)


def platform_of(resolver: Any) -> Platform | None:
    return resolver.platform if isinstance(resolver, ConnectResolver) else None


def is_connected(partner: Any) -> bool:
    return isinstance(partner, StripeConnected | FakeConnected)
