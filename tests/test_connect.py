"""Stripe Connect's own rules (src/pms/connect.py, docs/adr/0014-stripe-connect.md): what a payment
costs the platform under each model, Occupella's fee and its settings, what a tenant adds for the
fees, where a card surcharge is the company's, and how Stripe's calls and reports read. Nothing
here calls Stripe: every answer is made up, every key synthetic and spelled in pieces."""

import hashlib
import hmac
import io
import json
import time
import urllib.error
from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

import pytest

from pms import connect, payments

PLATFORM_KEY = "sk_" + "test_" + "SyntheticPlatform0000A1b2"
WEBHOOK = "whsec_" + "SyntheticConnect000000001"
AMOUNTS = [Decimal(a) for a in ("0.01", "1.00", "99.99", "625.00", "1500.00", "10000.00")]


# --- what a payment costs the platform ------------------------------------------------------


@pytest.mark.parametrize("method", connect.METHODS)
@pytest.mark.parametrize("amount", AMOUNTS + [Decimal("100000.00")])
def test_under_standard_a_payment_costs_the_platform_nothing(method, amount):
    assert connect.CHOSEN is connect.STANDARD
    assert connect.cost(connect.STANDARD, method, amount) == 0


def test_under_express_a_1500_rent_costs_what_the_adr_works_out():
    # docs/adr/0014-stripe-connect.md, "The fee": ACH 5.00 + 2.00 + 4.00 + 0.075; card 43.80 +
    # 2.00 + 4.00 + 0.15, each rounded up to the cent.
    assert connect.cost(connect.EXPRESS, "ach", Decimal("1500.00")) == Decimal("11.08")
    assert connect.cost(connect.EXPRESS, "card", Decimal("1500.00")) == Decimal("49.95")


@pytest.mark.parametrize("method", connect.METHODS)
def test_the_default_fees_land_above_break_even(method):
    """Brandon's rule: Occupella's fee always lands slightly above what a payment costs it. This
    fails if the defaults ever fall below the computed cost under the model chosen."""
    fee = connect.DEFAULT_FEES[method]
    assert connect.below_cost(fee, method) is None
    for amount in AMOUNTS + [Decimal("100000.00")]:
        assert fee.on(amount) > connect.cost(connect.CHOSEN, method, amount)
        assert fee.on(amount) - connect.cost(connect.CHOSEN, method, amount) >= connect.MARGIN


@pytest.mark.parametrize("method", connect.METHODS)
def test_the_defaults_would_not_cover_express(method):
    # Switching models is a decision: the fees would have to be raised with it.
    assert connect.below_cost(connect.DEFAULT_FEES[method], method, connect.EXPRESS) is not None


def test_below_cost_finds_a_fee_short_between_the_rents_checked():
    # 0.98%, at least 9.80, against Express's ACH cost: above it at 625.00 and at 1,500.00, short
    # only around 1,000.00, where the minimum gives way: a point no fixed list holds.
    fee = connect.Fee(Decimal("0.0098"), Decimal("0"), Decimal("9.80"))
    for checked in (Decimal("625.00"), Decimal("1500.00")):
        assert fee.on(checked) >= connect.cost(connect.EXPRESS, "ach", checked)
    short = connect.below_cost(fee, "ach", connect.EXPRESS)
    assert short is not None and Decimal("625.00") < short < Decimal("1500.00")
    assert fee.on(short) < connect.cost(connect.EXPRESS, "ach", short)


# --- Occupella's fee setting ------------------------------------------------------------------


def test_a_fee_setting_reads_as_percent_fixed_and_minimum():
    fee = connect.read_fee("card", " 0.25 , 0.30 , 0.50 ")
    assert fee == connect.Fee(Decimal("0.0025"), Decimal("0.30"), Decimal("0.50"))
    assert fee.on(Decimal("10.00")) == Decimal("0.50")
    assert fee.on(Decimal("1000.00")) == Decimal("2.80")
    assert fee.words() == "0.25% + $0.30, at least $0.50"
    assert connect.DEFAULT_FEES["ach"].words() == "$0.50"


@pytest.mark.parametrize(
    "text",
    ["", "0.50", "0,0.50", "a,b,c", "-1,0,0", "0,-0.01,0", "1,0,0", "0,60,0", "0,0,51", "NaN,0,0"],
)
def test_a_fee_setting_that_isnt_one_is_refused(text):
    with pytest.raises(connect.BadFee):
        connect.read_fee("ach", text)


def test_the_server_reads_each_fee_from_its_environment_or_takes_the_default():
    assert connect.fees_from({}) == connect.DEFAULT_FEES
    fees = connect.fees_from({"PMS_CONNECT_FEE_CARD": "0.1,0.25,0.40"})
    assert fees["ach"] == connect.DEFAULT_FEES["ach"]
    assert fees["card"] == connect.Fee(Decimal("0.001"), Decimal("0.25"), Decimal("0.40"))
    with pytest.raises(connect.BadFee, match="PMS_CONNECT_FEE_ACH"):
        connect.resolver_from("", "fake", {"PMS_CONNECT_FEE_ACH": "nope"}, live=False)


# --- what a tenant adds ---------------------------------------------------------------------


def test_a_tenant_paying_the_bank_fee_adds_exactly_what_covers_stripes_and_occupellas():
    added = connect.gross_up("ach", Decimal("1500.00"), Decimal("0.50"))
    assert added == Decimal("5.50")  # Stripe's cap and the fee
    small = connect.gross_up("ach", Decimal("100.00"), Decimal("0.50"))
    assert connect.stripe_fee("ach", Decimal("100.00") + small) + Decimal("0.50") <= small
    less = small - Decimal("0.01")
    assert connect.stripe_fee("ach", Decimal("100.00") + less) + Decimal("0.50") > less


def test_where_a_card_fee_is_the_companys():
    company = connect.card_fee_terms("company", "TX", False)
    assert (company.tenant_pays, company.why_company) == (False, None)
    banned = connect.card_fee_terms("tenant", "CT", False)
    assert banned.tenant_pays is False and banned.why_company.startswith("Connecticut")
    autopay = connect.card_fee_terms("tenant", "NY", True)
    assert (autopay.tenant_pays, autopay.why_company) == (
        False,
        "a card fee on autopay is the company's",
    )
    capped = connect.card_fee_terms("tenant", "CO", False)
    assert (capped.tenant_pays, capped.cap) == (True, Decimal("0.02"))
    anywhere = connect.card_fee_terms("tenant", None, False)
    assert (anywhere.tenant_pays, anywhere.cap) == (True, Decimal("0.03"))


def test_a_tenants_card_fee_is_never_more_than_the_cap():
    # Stripe's and Occupella's on 100.00 come to 3.81 grossed up; 3% is 3.00.
    assert connect.tenant_card_fee(Decimal("100.00"), Decimal("0.50"), Decimal("0.03")) == Decimal(
        "3.00"
    )
    # Colorado's 2% on 1,000.00.
    assert connect.tenant_card_fee(Decimal("1000.00"), Decimal("0.50"), Decimal("0.02")) == Decimal(
        "20.00"
    )


# --- what Stripe says ---------------------------------------------------------------------------


def test_a_disputes_fee_reads_from_its_balance_transactions():
    opened = {"balance_transactions": [{"amount": -100000, "fee": 1500}]}
    assert connect.dispute_fees(opened) == (Decimal("15.00"), Decimal("0.00"))
    won = {
        "balance_transactions": [
            {"amount": -100000, "fee": 1500},
            {"amount": 100000, "fee": -1500},
        ]
    }
    assert connect.dispute_fees(won) == (Decimal("15.00"), Decimal("15.00"))
    kept = {
        "balance_transactions": [{"amount": -100000, "fee": 1500}, {"amount": 100000, "fee": 0}]
    }
    assert connect.dispute_fees(kept) == (Decimal("15.00"), Decimal("0.00"))
    assert connect.dispute_fees({}) == (Decimal("0"), Decimal("0"))


def test_an_accounts_state_reads_what_stripe_still_needs():
    at = datetime.now(UTC)
    state = connect.account_state(
        {
            "id": "acct_Synthetic0001",
            "charges_enabled": True,
            "payouts_enabled": False,
            "details_submitted": True,
            "requirements": {
                "currently_due": ["external_account"],
                "past_due": ["tos_acceptance.date"],
            },
        },
        "evt_1",
        at,
    )
    assert state == connect.AccountState(
        "acct_Synthetic0001",
        True,
        False,
        True,
        ("external_account", "tos_acceptance.date"),
        "evt_1",
        at,
    )


def test_a_direct_charges_session_carries_the_fee_the_method_and_no_debit_card_when_asked():
    payment = uuid4()
    form = dict(
        connect.connect_checkout_form(
            payment,
            [("Rent", Decimal("100.00"))],
            "https://x.test/pay/done",
            "card",
            Decimal("0.50"),
            True,
        )
    )
    assert form["payment_method_types[0]"] == "card"
    assert form["payment_intent_data[application_fee_amount]"] == "50"
    assert form["payment_intent_data[metadata][payment_id]"] == str(payment)
    assert form["payment_method_options[card][restrictions][funding_types_blocked][0]"] == "debit"
    assert form["payment_method_options[card][restrictions][funding_types_blocked][1]"] == "prepaid"
    bank = dict(
        connect.connect_checkout_form(
            payment,
            [("Rent", Decimal("100.00"))],
            "https://x.test/pay/done",
            "ach",
            Decimal("0.50"),
            False,
        )
    )
    assert bank["payment_method_types[0]"] == "us_bank_account"
    assert not any("funding_types_blocked" in name for name in bank)


def test_a_standard_account_is_made_with_standards_controller_properties():
    pmc = uuid4()
    assert dict(connect.account_form(pmc)) == {
        "country": "US",
        "controller[stripe_dashboard][type]": "full",
        "controller[fees][payer]": "account",
        "controller[losses][payments]": "stripe",
        "controller[requirement_collection]": "stripe",
        "metadata[pmc_id]": str(pmc),
    }


class Answer(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *rest):
        return False


def test_each_call_names_the_connected_account_and_never_its_key_in_a_refusal(monkeypatch):
    sent = []

    def urlopen(request, timeout):
        sent.append(request)
        if "/refused" in request.full_url:
            raise urllib.error.HTTPError(request.full_url, 402, "no", {}, None)
        return Answer(json.dumps({"id": "cs_1"}).encode())

    monkeypatch.setattr(connect.urllib.request, "urlopen", urlopen)
    assert connect.stripe_call(
        PLATFORM_KEY,
        "POST",
        "/checkout/sessions",
        [("a", "1")],
        key="k1",
        account="acct_Synthetic0001",
        version=connect.FUNDING_FILTER_VERSION,
    ) == {"id": "cs_1"}
    headers = dict(sent[0].header_items())
    assert headers["Stripe-account"] == "acct_Synthetic0001"
    assert headers["Stripe-version"] == connect.FUNDING_FILTER_VERSION
    assert headers["Idempotency-key"] == "k1"
    with pytest.raises(payments.PartnerFailed) as failed:
        connect.stripe_call(PLATFORM_KEY, "GET", "/refused", [])
    assert PLATFORM_KEY not in str(failed.value)
    assert "402" in str(failed.value)


@pytest.fixture
def stripe(monkeypatch):
    answers = {}

    def call(secret, method, path, form, key="", account=None, version="", timeout=20.0):
        return answers[(method, path)]

    monkeypatch.setattr(connect, "stripe_call", call)
    return answers


def report(kind, subject, account="acct_Synthetic0001"):
    return connect.Report(account, kind, subject, f"evt_{uuid4().hex}", datetime.now(UTC))


def test_a_connected_accounts_reports_read_as_payments_saves_and_disputes(stripe):
    partner = connect.StripeConnected(PLATFORM_KEY, WEBHOOK, "acct_Synthetic0001")
    payment = uuid4()
    stripe[("GET", "/payment_intents/pi_1")] = {
        "latest_charge": {"balance_transaction": {"fee": 4430}},
        "metadata": {"payment_id": str(payment)},
    }
    paid = partner.read(
        report(
            "checkout.session.completed",
            {
                "object": "checkout.session",
                "payment_status": "paid",
                "client_reference_id": str(payment),
                "payment_intent": "pi_1",
            },
        )
    )
    assert [(e.kind, e.fee) for e in paid] == [("paid", Decimal("44.30"))]
    clearing = report(
        "checkout.session.completed",
        {"payment_status": "unpaid", "client_reference_id": str(payment), "payment_intent": "pi_1"},
    )
    assert partner.read(clearing) == []
    failed = partner.read(
        report(
            "payment_intent.payment_failed",
            {"id": "pi_1", "metadata": {"payment_id": str(payment)}},
        )
    )
    assert [e.kind for e in failed] == ["failed"]
    autopay = uuid4()
    stripe[("GET", "/setup_intents/seti_1")] = {
        "status": "succeeded",
        "metadata": {"autopay_id": str(autopay)},
        "customer": "cus_1",
        "mandate": None,
        "payment_method": {"id": "pm_1", "card": {"brand": "visa", "last4": "4242"}},
    }
    saved = partner.read(
        report("setup_intent.succeeded", {"id": "seti_1", "metadata": {"autopay_id": str(autopay)}})
    )
    assert [(s.autopay_id, s.label, s.account.method) for s in saved] == [
        (autopay, "Visa ••••4242", "pm_1")
    ]
    dispute = {
        "id": "du_1",
        "payment_intent": "pi_1",
        "status": "needs_response",
        "balance_transactions": [{"amount": -100000, "fee": 1500}],
    }
    opened = partner.read(report("charge.dispute.created", dispute))
    assert [type(i).__name__ for i in opened] == ["Event", "Dispute"]
    assert opened[0].kind == "returned"
    assert (opened[1].kind, opened[1].fee, opened[1].dispute_id) == (
        "opened",
        Decimal("15.00"),
        "du_1",
    )
    won = partner.read(
        report(
            "charge.dispute.funds_reinstated",
            {
                **dispute,
                "status": "won",
                "balance_transactions": [
                    {"amount": -100000, "fee": 1500},
                    {"amount": 100000, "fee": -1500},
                ],
            },
        )
    )
    assert [(d.kind, d.fee) for d in won] == [("won", Decimal("15.00"))]
    lost = partner.read(report("charge.dispute.closed", {**dispute, "status": "lost"}))
    assert [(d.kind, d.fee) for d in lost] == [("lost", None)]
    assert (
        partner.read(report("charge.dispute.closed", {**dispute, "status": "under_review"})) == []
    )
    assert partner.read(report("charge.dispute.created", {"id": "du_2"})) == []  # no payment
    assert partner.read(report("invoice.paid", {"id": "in_1"})) == []
    updated = partner.read(
        report("account.updated", {"id": "acct_Synthetic0001", "charges_enabled": True})
    )
    assert isinstance(updated[0], connect.AccountState) and updated[0].charges_enabled
    # Its payments start at Connect's own checkout, never the keys' path, and report only
    # through the platform's webhook.
    with pytest.raises(payments.PartnerFailed):
        partner.checkout(payment, [], "https://x.test/pay/done")
    with pytest.raises(payments.NotTheirs):
        partner.event(b"{}", {})


def signed(body, secret=WEBHOOK, when=None):
    now = int(time.time()) if when is None else when
    mac = hmac.new(secret.encode(), f"{now}.".encode() + body, hashlib.sha256).hexdigest()
    return {"stripe-signature": f"t={now},v1={mac}"}


def test_the_platforms_webhook_reads_only_its_own_signed_reports_of_its_own_mode():
    platform = connect.StripePlatform(PLATFORM_KEY, WEBHOOK)
    assert platform.live is False

    def body(livemode=False, account="acct_Synthetic0001"):
        return json.dumps(
            {
                "id": "evt_1",
                "type": "account.updated",
                "created": int(time.time()),
                "livemode": livemode,
                "account": account,
                "data": {"object": {"id": account}},
            }
        ).encode()

    read = platform.report(body(), signed(body()))
    assert (read.account, read.kind) == ("acct_Synthetic0001", "account.updated")
    with pytest.raises(payments.NotTheirs):
        platform.report(body(), signed(body(), "whsec_" + "SomeoneElse00000000000"))
    # A live report to a test platform (P-048), or the platform's own account's: never booked.
    assert platform.report(body(True), signed(body(True))) is None
    assert platform.report(body(account=None), signed(body(account=None))) is None
    with pytest.raises(payments.NotTheirs):
        platform.report(b"[]", signed(b"[]"))


def test_pms_connect_names_a_platform_or_none():
    assert connect.platform_named("", {}) is None
    assert isinstance(connect.platform_named(" fake ", {}), connect.FakePlatform)
    with pytest.raises(connect.NoPlatform, match="PMS_STRIPE_CONNECT_SECRET_KEY"):
        connect.platform_named("stripe", {})
    with pytest.raises(connect.NoPlatform, match="known: fake, stripe"):
        connect.platform_named("paypal", {})
    found = connect.platform_named(
        "stripe",
        {
            "PMS_STRIPE_CONNECT_SECRET_KEY": PLATFORM_KEY,
            "PMS_STRIPE_CONNECT_WEBHOOK_SECRET": WEBHOOK,
        },
    )
    assert isinstance(found, connect.StripePlatform)
    plain = connect.resolver_from("fake", "", {}, live=False)
    assert type(plain) is payments.Resolver
    with_connect = connect.resolver_from("fake", "fake", {}, live=False)
    assert isinstance(with_connect, connect.ConnectResolver)
    assert connect.platform_of(with_connect).name == "fake_connect"
    assert connect.platform_of(plain) is None


def test_the_fake_platforms_pages_are_the_pms_own():
    fake = connect.FakePlatform()
    account = fake.create_account(uuid4(), 0)
    assert account.startswith("fake_acct_")
    assert (
        fake.onboarding_link(
            account,
            "http://x.test/settings/setup?connect=again",
            "http://x.test/settings/setup?connect=back",
        )
        == f"http://x.test/pay/fake-connect#{account}"
    )
    bound = fake.bound(account)
    payment = uuid4()
    page = bound.connect_checkout(
        payment, [], "http://x.test/pay/done", "card", Decimal("0.50"), False
    )
    assert page.url == f"http://x.test/pay/fake-connect-pay#{payment}"
    assert connect.fake_saved(payment, "card", datetime.now(UTC)).label == "Test card ••••4242"
    assert connect.fake_saved(payment, "ach", datetime.now(UTC)).label == "Test bank ••••0000"
    with pytest.raises(payments.PartnerFailed):
        fake.state(account)
    with pytest.raises(payments.PartnerFailed):
        bound.charge(payment, Decimal("1.00"), "Rent", payments.Account("c", "m"))
