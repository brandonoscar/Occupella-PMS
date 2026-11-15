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
        assert fee.on(checked) > connect.cost(connect.EXPRESS, "ach", checked)
    short = connect.below_cost(fee, "ach", connect.EXPRESS)
    assert short is not None and Decimal("625.00") < short < Decimal("1500.00")
    assert fee.on(short) <= connect.cost(connect.EXPRESS, "ach", short)


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


@pytest.mark.parametrize("text", ["0,0,0", "0.25,0,0", "0.9,0,0"])
def test_a_fee_at_break_even_is_refused_even_under_standard(text):
    """Under Standard a payment costs the platform nothing, so break-even is a fee of nothing:
    none at all, or a percent alone, which comes to $0.00 on a one-cent payment. Brandon's rule is
    above break-even, so each is refused, naming the payment where it lands on it."""
    with pytest.raises(connect.BadFee) as refused:
        connect.read_fee("ach", text)
    assert str(refused.value).startswith(
        "PMS_CONNECT_FEE_ACH isn't above what a payment of $0.01 costs the platform ($0.00)"
    )
    # A cent at least on every payment clears it.
    nothing = Decimal("0")
    assert connect.read_fee("ach", "0,0.01,0") == connect.Fee(nothing, Decimal("0.01"), nothing)
    assert connect.read_fee("ach", "0.25,0,0.01").on(Decimal("0.01")) == Decimal("0.01")


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


# --- Stripe saying something unexpected (the merge with main, 965b560) --------------------------


def test_stripe_refusing_or_not_answering_is_a_partner_failure_naming_no_account(monkeypatch):
    def refused(*args, **kwargs):
        raise urllib.error.HTTPError("https://api.stripe.com/v1/x", 402, "no", {}, None)  # type: ignore[arg-type]

    monkeypatch.setattr(connect.urllib.request, "urlopen", refused)
    with pytest.raises(payments.PartnerFailed, match="HTTP 402") as said:
        connect.stripe_call(
            "sk_test_x", "GET", "/accounts/acct_1Secret", [], account="acct_1Secret"
        )
    assert "acct_1Secret" not in str(said.value)

    def down(*args, **kwargs):
        raise OSError("connection reset")

    monkeypatch.setattr(connect.urllib.request, "urlopen", down)
    with pytest.raises(payments.PartnerFailed, match="did not answer GET"):
        connect.stripe_call("sk_test_x", "GET", "/accounts", [])


def test_an_account_or_link_stripe_answers_without_its_parts_is_a_partner_failure(monkeypatch):
    monkeypatch.setattr(connect, "stripe_call", lambda *a, **k: {"object": "unexpected"})
    platform = connect.StripePlatform("sk_test_" + "x" * 24, "whsec_" + "y" * 24)

    with pytest.raises(payments.PartnerFailed, match="account came without"):
        platform.create_account(uuid4(), 1)
    with pytest.raises(payments.PartnerFailed, match="link came without"):
        platform.onboarding_link("acct_1A", "https://example.test/r", "https://example.test/d")
    # An account object without its id reads as Stripe's failure, not as an account.
    with pytest.raises(payments.PartnerFailed, match="account came without"):
        platform.state("acct_1A")


def test_a_disputes_fee_reads_only_its_balance_transactions():
    # An id Stripe didn't expand says nothing; the withdrawal takes the fee, a reinstatement
    # gives it back (its fee negative).
    subject = {
        "balance_transactions": [
            "txn_unexpanded",
            {"amount": -100000, "fee": 1500},
            {"amount": 100000, "fee": -1500},
        ]
    }
    assert connect.dispute_fees(subject) == (Decimal("15.00"), Decimal("15.00"))
    assert connect.dispute_fees({}) == (Decimal("0.00"), Decimal("0.00"))


def test_the_fake_connected_account_takes_payments_only_at_connects_own_pages():
    fake = connect.FakeConnected("acct_fake_1")

    with pytest.raises(payments.PartnerFailed, match="starts at Connect's checkout"):
        fake.checkout(uuid4(), [("Rent", Decimal("1.00"))], "https://example.test/pay/done")
    with pytest.raises(payments.PartnerFailed, match="saves at Connect's page"):
        fake.save_account(uuid4(), "https://example.test/pay/done")
    with pytest.raises(payments.NotTheirs):
        fake.event(b"{}", {})
    assert connect.platform_of(payments.Resolver(None)) is None
    assert connect.is_connected(fake) and not connect.is_connected(payments.FakePartner())


# --- the fee floor under a model that costs the platform money -------------------------------


def test_a_fee_setting_below_what_the_model_costs_is_refused_naming_where(monkeypatch):
    # Under Standard a payment costs the platform nothing, so only a fee of nothing is refused.
    # Were the platform on Express, the default 0.50 falls short of the 2.33 a one-cent payment
    # costs it (2.00 active account, 0.25 payout, 0.075 dispute share), and the server refuses
    # to start.
    monkeypatch.setattr(connect, "CHOSEN", connect.EXPRESS)
    monkeypatch.setattr(connect.below_cost, "__defaults__", (connect.EXPRESS,))
    with pytest.raises(connect.BadFee) as refused:
        connect.read_fee("ach", "0,0.50,0.50")
    said = str(refused.value)
    assert said.startswith("PMS_CONNECT_FEE_ACH isn't above what a payment of $0.01 costs")
    assert "($2.33)" in said and "above break-even" in said
    with pytest.raises(connect.BadFee, match="PMS_CONNECT_FEE_CARD isn't above"):
        connect.fees_from({"PMS_CONNECT_FEE_CARD": "0,0.50,0.50"})
    # One that clears Express's cost at every amount it is checked at reads.
    covering = connect.read_fee("ach", "0.8,13,13")
    assert connect.below_cost(covering, "ach") is None
    assert covering.words() == "0.8% + $13.00, at least $13.00"
    # A percent with no fixed part reads as the percent and its minimum.
    assert connect.Fee(Decimal("0.0098"), Decimal("0"), Decimal("9.80")).words() == (
        "0.98%, at least $9.80"
    )


def test_a_small_card_payments_fee_steps_up_until_it_covers_stripes_rounding():
    # On 0.05 with Occupella's 0.50, the straight-line guess, 0.82, leaves Stripe's fee on the
    # whole (0.33 on 0.87) and 0.50 one cent short; 0.83 covers it (0.33 on 0.88).
    added = connect.gross_up("card", Decimal("0.05"), Decimal("0.50"))
    assert added == Decimal("0.83")
    assert connect.stripe_fee("card", Decimal("0.05") + added) + Decimal("0.50") <= added
    short = added - Decimal("0.01")
    assert connect.stripe_fee("card", Decimal("0.05") + short) + Decimal("0.50") > short


# --- a connected account's own calls ---------------------------------------------------------


@pytest.fixture
def calls(monkeypatch):
    """Stripe's answers by method and path (an exception is raised as Stripe failing), and each
    call made: its form, idempotency key, connected account and API version."""
    answers, made = {}, []

    def call(
        secret,
        method,
        path,
        form,
        key="",
        account=None,
        version=payments.STRIPE_VERSION,
        timeout=20.0,
    ):
        assert secret == PLATFORM_KEY
        made.append(
            {
                "method": method,
                "path": path,
                "form": list(form),
                "key": key,
                "account": account,
                "version": version,
            }
        )
        answer = answers[(method, path)]
        if isinstance(answer, Exception):
            raise answer
        return answer

    monkeypatch.setattr(connect, "stripe_call", call)
    return answers, made


def test_a_connected_accounts_checkout_is_a_direct_charge_on_it_carrying_occupellas_fee(calls):
    answers, made = calls
    partner = connect.StripeConnected(PLATFORM_KEY, WEBHOOK, "acct_Synthetic0001")
    payment = uuid4()
    lines = [("Rent – Unit 1", Decimal("1200.00")), ("Card fee", Decimal("36.00"))]
    answers[("POST", "/checkout/sessions")] = {
        "id": "cs_Synthetic1",
        "url": "https://checkout.stripe.test/c/cs_Synthetic1",
    }
    started = partner.connect_checkout(
        payment, lines, "https://pms.test/pay/done", "card", Decimal("0.50"), True
    )
    assert started == payments.Checkout(
        ref="cs_Synthetic1", url="https://checkout.stripe.test/c/cs_Synthetic1"
    )
    sent = made[-1]
    assert (sent["method"], sent["path"], sent["account"], sent["key"]) == (
        "POST",
        "/checkout/sessions",
        "acct_Synthetic0001",
        f"checkout-{payment}",
    )
    # The debit and prepaid filter needs its own API version; a bank payment keeps the usual one.
    assert sent["version"] == connect.FUNDING_FILTER_VERSION
    form = dict(sent["form"])
    assert form["payment_intent_data[application_fee_amount]"] == "50"
    assert form["client_reference_id"] == str(payment)
    partner.connect_checkout(
        payment, lines[:1], "https://pms.test/pay/done", "ach", Decimal("0.50"), False
    )
    assert made[-1]["version"] == payments.STRIPE_VERSION
    # A session without its page is Stripe's failure: the tenant is never sent nowhere.
    answers[("POST", "/checkout/sessions")] = {"id": "cs_Synthetic2"}
    with pytest.raises(payments.PartnerFailed, match="session came without 'url'"):
        partner.connect_checkout(
            payment, lines[:1], "https://pms.test/pay/done", "ach", Decimal("0.50"), False
        )
    # An autopay saves only at Connect's own page.
    with pytest.raises(payments.PartnerFailed, match="saves at Connect's page"):
        partner.save_account(uuid4(), "https://pms.test/pay/done")


@pytest.mark.parametrize(("method", "kind"), [("card", "card"), ("ach", "us_bank_account")])
def test_a_connected_accounts_autopay_saves_on_a_customer_of_its_own(calls, method, kind):
    answers, made = calls
    partner = connect.StripeConnected(PLATFORM_KEY, WEBHOOK, "acct_Synthetic0001")
    autopay = uuid4()
    answers[("POST", "/customers")] = {"id": "cus_Synthetic1", "object": "customer"}
    answers[("POST", "/checkout/sessions")] = {
        "id": "cs_SyntheticSetup1",
        "url": "https://checkout.stripe.test/c/cs_SyntheticSetup1",
    }
    page = partner.connect_save(autopay, "https://pms.test/pay/done", method)
    assert page.url == "https://checkout.stripe.test/c/cs_SyntheticSetup1"
    customer, session = made
    # The customer is made on the connected account, once per autopay however often it's asked.
    assert (customer["path"], customer["account"], customer["key"]) == (
        "/customers",
        "acct_Synthetic0001",
        f"customer-{autopay}",
    )
    assert customer["form"] == [("metadata[autopay_id]", str(autopay))]
    form = dict(session["form"])
    assert (session["account"], session["key"]) == ("acct_Synthetic0001", f"setup-{autopay}")
    assert (form["mode"], form["customer"], form["payment_method_types[0]"]) == (
        "setup",
        "cus_Synthetic1",
        kind,
    )
    assert form["setup_intent_data[metadata][autopay_id]"] == str(autopay)
    # A customer or a session Stripe answers without its id or page is Stripe's failure.
    answers[("POST", "/checkout/sessions")] = {"object": "checkout.session"}
    with pytest.raises(payments.PartnerFailed, match="session came without"):
        partner.connect_save(autopay, "https://pms.test/pay/done", method)
    answers[("POST", "/customers")] = {"object": "customer"}
    with pytest.raises(payments.PartnerFailed, match="customer came without 'id'"):
        partner.connect_save(autopay, "https://pms.test/pay/done", method)


class Started:
    """What the database says of an autopay payment started through Connect: its method,
    Occupella's fee and the connected account it was started on (charge_terms' one row)."""

    def __init__(self, row):
        self.row = row

    def execute(self, query, params):
        assert "trust_connected_payments" in query
        return self

    def fetchone(self):
        return self.row


def test_an_autopay_charge_is_off_session_on_its_account_with_its_fee_and_found_again(calls):
    answers, made = calls
    payment = uuid4()
    saved = payments.Account("cus_Synthetic1", "pm_Synthetic1", "mandate_Synthetic1")
    card = connect.StripeConnected(
        PLATFORM_KEY,
        WEBHOOK,
        "acct_Synthetic0001",
        Started(("card", Decimal("0.50"), "acct_Synthetic0001")),
    )
    answers[("GET", "/payment_intents")] = {"object": "list", "data": []}
    answers[("POST", "/payment_intents")] = {"id": "pi_Synthetic1", "object": "payment_intent"}
    assert card.charge(payment, Decimal("1200.00"), "Rent – Unit 1", saved) == "pi_Synthetic1"
    listed, made_intent = made
    assert listed["form"] == [("customer", "cus_Synthetic1"), ("limit", "100")]
    form = dict(made_intent["form"])
    assert (made_intent["account"], made_intent["key"]) == (
        "acct_Synthetic0001",
        f"autopay-{payment}",
    )
    assert (form["amount"], form["payment_method_types[0]"], form["off_session"]) == (
        "120000",
        "card",
        "true",
    )
    assert form["application_fee_amount"] == "50"
    assert "mandate" not in form  # a card has no bank mandate
    # By bank, the mandate the tenant agreed to goes with it.
    bank = connect.StripeConnected(
        PLATFORM_KEY,
        WEBHOOK,
        "acct_Synthetic0001",
        Started(("ach", Decimal("0.75"), "acct_Synthetic0001")),
    )
    bank.charge(payment, Decimal("1200.00"), "Rent – Unit 1", saved)
    form = dict(made[-1]["form"])
    assert (form["payment_method_types[0]"], form["mandate"]) == (
        "us_bank_account",
        "mandate_Synthetic1",
    )
    assert form["application_fee_amount"] == "75"
    # Taken before but its answer lost: found again by its payment id, never made twice.
    answers[("GET", "/payment_intents")] = {
        "object": "list",
        "data": [
            {"id": "pi_SomeoneElse", "metadata": {"payment_id": str(uuid4())}},
            {"id": "pi_Synthetic1", "metadata": {"payment_id": str(payment)}},
        ],
    }
    before = len(made)
    assert bank.charge(payment, Decimal("1200.00"), "Rent – Unit 1", saved) == "pi_Synthetic1"
    assert [call["method"] for call in made[before:]] == ["GET"]
    # A payment Stripe answers without its id is Stripe's failure, so it's tried again later.
    answers[("GET", "/payment_intents")] = {"object": "list", "data": []}
    answers[("POST", "/payment_intents")] = {"object": "payment_intent"}
    with pytest.raises(payments.PartnerFailed, match="payment came without 'id'"):
        bank.charge(payment, Decimal("1200.00"), "Rent – Unit 1", saved)


def test_an_autopay_charge_is_refused_on_any_account_but_the_one_it_was_started_on(calls):
    answers, made = calls
    saved = payments.Account("cus_Synthetic1", "pm_Synthetic1")
    other = connect.StripeConnected(
        PLATFORM_KEY,
        WEBHOOK,
        "acct_Synthetic0002",
        Started(("ach", Decimal("0.50"), "acct_Synthetic0001")),
    )
    with pytest.raises(payments.PartnerFailed, match="wasn't started on this connected account"):
        other.charge(uuid4(), Decimal("100.00"), "Rent", saved)
    unknown = connect.StripeConnected(PLATFORM_KEY, WEBHOOK, "acct_Synthetic0001", Started(None))
    with pytest.raises(payments.PartnerFailed, match="wasn't started on this connected account"):
        unknown.charge(uuid4(), Decimal("100.00"), "Rent", saved)
    outside = connect.StripeConnected(PLATFORM_KEY, WEBHOOK, "acct_Synthetic0001")
    with pytest.raises(payments.PartnerFailed, match="charged within a request"):
        outside.charge(uuid4(), Decimal("100.00"), "Rent", saved)
    assert made == []  # nothing was asked of Stripe


def test_reports_a_connected_account_cant_act_on_read_as_nothing_or_as_stripes_failure(calls):
    answers, made = calls
    partner = connect.StripeConnected(PLATFORM_KEY, WEBHOOK, "acct_Synthetic0001")
    autopay = uuid4()
    # A setup session without its setup intent, or one for a setup intent the PMS didn't start.
    assert partner.read(report("checkout.session.completed", {"mode": "setup"})) == []
    assert (
        partner.read(report("setup_intent.succeeded", {"id": "seti_Other", "metadata": {}})) == []
    )
    # A bank account still being verified (micro-deposits) isn't saved yet.
    answers[("GET", "/setup_intents/seti_Synthetic1")] = {
        "status": "requires_action",
        "metadata": {"autopay_id": str(autopay)},
    }
    setup = {"mode": "setup", "setup_intent": "seti_Synthetic1"}
    assert partner.read(report("checkout.session.completed", setup)) == []
    assert dict(made[-1]["form"]) == {"expand[]": "payment_method"}
    # Saved: a bank account reads as its bank and last four, with the mandate it was given.
    answers[("GET", "/setup_intents/seti_Synthetic1")] = {
        "status": "succeeded",
        "metadata": {"autopay_id": str(autopay)},
        "customer": "cus_Synthetic1",
        "mandate": "mandate_Synthetic1",
        "payment_method": {
            "id": "pm_Synthetic1",
            "us_bank_account": {"bank_name": "Synthetic Test Bank", "last4": "6789"},
        },
    }
    [saved] = partner.read(report("checkout.session.completed", setup))
    assert (saved.autopay_id, saved.label) == (autopay, "Synthetic Test Bank ••••6789")
    assert saved.account == payments.Account(
        "cus_Synthetic1", "pm_Synthetic1", "mandate_Synthetic1"
    )
    answers[("GET", "/setup_intents/seti_Synthetic1")]["payment_method"] = {"id": "pm_Synthetic2"}
    [unnamed] = partner.read(report("checkout.session.completed", setup))
    assert unnamed.label == "Bank account ••••????"
    # Its payment method not expanded: Stripe's answer can't be read, so it's sent again.
    answers[("GET", "/setup_intents/seti_Synthetic1")]["payment_method"] = "pm_Synthetic1"
    with pytest.raises(payments.PartnerFailed, match="saved account can't be read"):
        partner.read(report("checkout.session.completed", setup))
    # A payment, or a dispute on one, that no PMS payment started: nothing to record.
    assert (
        partner.read(report("payment_intent.succeeded", {"id": "pi_Other", "metadata": {}})) == []
    )
    answers[("GET", "/payment_intents/pi_Other")] = {"id": "pi_Other", "metadata": {}}
    dispute = {"id": "du_Other", "payment_intent": "pi_Other", "status": "needs_response"}
    assert partner.read(report("charge.dispute.created", dispute)) == []
