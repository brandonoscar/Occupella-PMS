"""Stripe Connect through the API (src/pms/api/connect.py, docs/adr/0014-stripe-connect.md): an
admin connects the company's own Stripe account through the platform's onboarding; its tenants pay
by bank or card and its owners add money by either, each a direct charge carrying Occupella's fee,
which leaves trust from the company's fee account with Stripe's, never from an owner's; a card
dispute is recorded as a return, and one the company wins puts the payment back; the platform's
one webhook maps each report's account to its company. Every key here is made up, and spelled in
pieces so no secret scanner mistakes it for one."""

import hashlib
import hmac
import json
import time
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from helpers import acting_as, add_unit, balance, bank_sums, charge, open_lease, post
from people import join_as_owner, join_as_staff, join_as_tenant, tenant_of

from pms import connect, deployment, payments
from pms.api import Settings, create_app
from pms.api import connect as connect_api

TODAY = datetime.now(UTC).date()
DAY = timedelta(days=1)
PLATFORM_TEST_KEY = "sk_" + "test_" + "SyntheticPlatform0000A1b2"
PLATFORM_LIVE_KEY = "sk_" + "live_" + "SyntheticPlatform0000Z9y8"
CONNECT_WEBHOOK = "whsec_" + "SyntheticConnect000000001"


@pytest.fixture
def settings(database_url):
    return Settings(
        database_url=database_url,
        public_url="http://testserver",
        cookie_secure=False,
        payments="fake",
        connect="fake",
    )


@pytest.fixture
def staff(client, conn, app_conn, pmc):
    return join_as_staff(client, conn, app_conn, pmc)


@pytest.fixture
def tenant_client(settings):
    with TestClient(create_app(settings)) as browser:
        yield browser


@pytest.fixture
def owner_client(settings):
    with TestClient(create_app(settings)) as browser:
        yield browser


@pytest.fixture
def lease(conn, pmc):
    """A lease of the PMC's first owner's property, 1,200.00 owed."""
    unit = add_unit(conn, pmc.pmc_id, pmc.owners[0].property_id)
    made = open_lease(
        conn, pmc.pmc_id, unit, TODAY - 60 * DAY, None, "1200.00", [tenant_of(conn, pmc)]
    )
    charge(conn, pmc.pmc_id, made, TODAY - 10 * DAY, "1200.00")
    return made


@pytest.fixture
def tenant(tenant_client, conn, app_conn, pmc, lease):
    return join_as_tenant(tenant_client, conn, app_conn, pmc)


def ok(answer, status=200):
    assert answer.status_code == status, answer.text
    return answer.json() if answer.content else None


def refused(answer, status, detail=None):
    assert answer.status_code == status, answer.text
    if detail is not None:
        assert answer.json()["detail"] == detail


def turn_on(client, payer="company"):
    ok(client.post("/api/payments/settings", json={"online": True, "ach_fee_payer": payer}), 204)


def connect_company(client, outcome="finished"):
    """An admin presses Connect Stripe and finishes onboarding on the fake platform's page."""
    url = ok(client.post("/api/payments/connect/start", json={}))["url"]
    assert url.startswith("http://testserver/pay/fake-connect#fake_acct_")
    account = url.split("#", 1)[1]
    ok(
        client.post(f"/api/payments/connect/fake/accounts/{account}", json={"outcome": outcome}),
        204,
    )
    return account


def fund_company(conn, pmc, amount="50.00"):
    """The company's fee account holds some earned fees, so the costs it pays don't wait."""
    post(
        conn, pmc.pmc_id, f"fees/{uuid4()}", [(pmc.operating_cash, pmc.pmc_income, Decimal(amount))]
    )


def pay(tenant_client, lease, amount, method):
    return ok(
        tenant_client.post(
            f"/api/payments/connect/leases/{lease}/checkout",
            json={"amount": amount, "method": method},
        ),
        201,
    )


def says(client, payment, outcome, status=200):
    return ok(
        client.post(f"/api/payments/connect/fake/payments/{payment}", json={"outcome": outcome}),
        status,
    )


# --- Company setup ------------------------------------------------------------------------------


def test_an_admin_connects_the_companys_stripe_account_and_its_payments_go_through_it(
    client, conn, staff, pmc
):
    shown = ok(client.get("/api/payments/connect"))
    assert (shown["offered"], shown["platform"], shown["connected"], shown["using"]) == (
        True,
        "fake_connect",
        False,
        False,
    )
    assert shown["fees"] == [
        {"method": "ach", "words": "$0.50"},
        {"method": "card", "words": "$0.50"},
    ]
    # Onboarding left wanting: connected, but no charge goes through it yet.
    account = connect_company(client, "wanting")
    shown = ok(client.get("/api/payments/connect"))
    assert (shown["connected"], shown["charges_enabled"], shown["using"]) == (True, False, False)
    assert shown["requirements_due"] == ["external_account"]
    assert shown["account_ends"] == account[-4:]
    # Back to Stripe's onboarding: the same account, a new link; finished, it takes charges.
    again = ok(client.post("/api/payments/connect/start", json={}))["url"]
    assert again.endswith(account)
    ok(
        client.post(f"/api/payments/connect/fake/accounts/{account}", json={"outcome": "finished"}),
        204,
    )
    shown = ok(client.get("/api/payments/connect"))
    assert (shown["charges_enabled"], shown["payouts_enabled"], shown["using"]) == (
        True,
        True,
        True,
    )
    assert shown["connected_by"] == "Staff 1"
    assert ok(client.get("/api/payments/settings"))["partner"] == "fake_connect"
    # Nothing on the company's rows names a key; one account, one row.
    rows = conn.execute(
        "SELECT account_id, partner, livemode, recorded_by FROM trust_connected_accounts"
        " WHERE pmc_id = %s",
        (pmc.pmc_id,),
    ).fetchall()
    assert rows == [(account, "fake_connect", False, staff.person_id)]
    # Refresh asks the fake platform nothing; disconnected, the server's partner holds again.
    assert ok(client.post("/api/payments/connect/refresh", json={}))["using"] is True
    ok(client.post("/api/payments/connect/disconnect", json={}), 204)
    shown = ok(client.get("/api/payments/connect"))
    assert (shown["connected"], shown["using"]) == (False, False)
    assert ok(client.get("/api/payments/settings"))["partner"] == "fake"


def test_only_an_admin_connects_and_only_staff_see_it(
    client, other_client, conn, app_conn, pmc, staff
):
    clerk = TestClient(other_client.app)
    join_as_staff(clerk, conn, app_conn, pmc, name="Clerk", roles=())
    assert ok(clerk.get("/api/payments/connect"))["may_change"] is False
    refused(
        clerk.post("/api/payments/connect/start", json={}),
        403,
        "Only an admin can connect or disconnect the company's Stripe account.",
    )
    connect_company(client)
    refused(clerk.post("/api/payments/connect/disconnect", json={}), 403)
    # The clerk still chooses who pays the card fee (Company setup's area, setup).
    ok(clerk.post("/api/payments/connect/card-fees", json={"card_fee_payer": "tenant"}), 204)
    # No session, or a tenant's, sees nothing.
    with TestClient(client.app) as nobody:
        refused(nobody.get("/api/payments/connect"), 401)
        refused(nobody.post("/api/payments/connect/start", json={}), 401)
    owner_browser = TestClient(other_client.app)
    join_as_owner(owner_browser, app_conn, pmc)
    refused(owner_browser.get("/api/payments/connect"), 403)
    refused(
        owner_browser.post("/api/payments/connect/card-fees", json={"card_fee_payer": "tenant"}),
        403,
    )


def test_card_fees_are_the_companys_until_it_chooses_and_each_state_rule_shows(
    client, conn, staff, pmc
):
    assert ok(client.get("/api/payments/connect"))["card_fee_payer"] == "company"
    refused(client.post("/api/payments/connect/card-fees", json={"card_fee_payer": "owner"}), 422)
    refused(client.post("/api/payments/connect/card-fees", json={}), 422)
    ok(client.post("/api/payments/connect/card-fees", json={"card_fee_payer": "tenant"}), 204)
    ok(client.post("/api/payments/connect/card-fees", json={"card_fee_payer": "tenant"}), 204)
    count = conn.execute(
        "SELECT count(*) FROM trust_card_fee_settings WHERE pmc_id = %s", (pmc.pmc_id,)
    ).fetchone()
    assert count == (1,)  # the same again writes nothing
    for owner, state in zip(pmc.owners, ("CT", "CO"), strict=True):
        conn.execute(
            "INSERT INTO trust_property_addresses (pmc_id, property_id, line_1, city, state, zip)"
            " VALUES (%s, %s, '1 Synthetic Way', 'Demo City', %s, '00001')",
            (pmc.pmc_id, owner.property_id, state),
        )
    rules = ok(client.get("/api/payments/connect"))["rules"]
    assert [(r["state"], r["card"]) for r in rules] == [("CO", "capped"), ("CT", "banned")]
    assert "2%" in rules[0]["says"] and "HB25-1090" in rules[0]["says"]


def test_a_server_without_a_platform_offers_no_connect(database_url, conn, app_conn, pmc):
    plain = Settings(database_url=database_url, public_url="http://testserver", cookie_secure=False)
    with TestClient(create_app(plain)) as browser:
        join_as_staff(browser, conn, app_conn, pmc)
        shown = ok(browser.get("/api/payments/connect"))
        assert (shown["offered"], shown["platform"]) == (False, None)
        refused(
            browser.post("/api/payments/connect/start", json={}),
            404,
            "This server doesn't offer Stripe Connect.",
        )
        refused(
            browser.post(
                "/api/payments/connect/webhook",
                content=b"{}",
                headers={"content-type": "application/json"},
            ),
            404,
        )
        refused(browser.get("/api/payments/connect/fake/payments/" + str(uuid4())), 404)


# --- a tenant pays --------------------------------------------------------------------------------


def test_a_tenant_pays_by_bank_and_the_fees_leave_trust_from_the_companys_account(
    client, tenant_client, conn, staff, tenant, lease, pmc
):
    turn_on(client)
    # Before the company connects, the lease's payments don't go through Connect.
    assert ok(tenant_client.get(f"/api/payments/connect/leases/{lease}"))["connect"] is False
    refused(
        tenant_client.post(
            f"/api/payments/connect/leases/{lease}/checkout",
            json={"amount": "100.00", "method": "ach"},
        ),
        409,
        "This company takes payments through its own Stripe keys: pay by bank on Pay online.",
    )
    connect_company(client)
    fund_company(conn, pmc)
    methods = ok(tenant_client.get(f"/api/payments/connect/leases/{lease}?amount=1200.00"))
    assert methods["connect"] is True
    assert [(m["method"], m["fee_payer"], m["tenant_fee"]) for m in methods["methods"]] == [
        ("ach", "company", "0.00"),
        ("card", "company", "0.00"),
    ]
    started = pay(tenant_client, lease, "1200.00", "ach")
    assert started["url"] == f"http://testserver/pay/fake-connect-pay#{started['id']}"
    page = ok(tenant_client.get(f"/api/payments/connect/fake/payments/{started['id']}"))
    assert (page["method"], page["total"], page["platform_fee"], page["status"]) == (
        "ach",
        "1200.00",
        "0.50",
        "started",
    )
    owner_before = balance(conn, pmc.owners[0].account)
    company_before = balance(conn, pmc.pmc_income)
    assert says(tenant_client, started["id"], "paid") == {"recorded": True}
    assert says(tenant_client, started["id"], "paid") == {"recorded": True}  # once
    # The owner gets the whole payment; Stripe's 5.00 and Occupella's 0.50 leave from the
    # company's account.
    assert balance(conn, pmc.owners[0].account) - owner_before == Decimal("1200.00")
    assert company_before - balance(conn, pmc.pmc_income) == Decimal("5.50")
    assert set(bank_sums(conn, pmc.pmc_id).values()) == {Decimal(0)}
    recorded = conn.execute(
        "SELECT method, platform_fee, partner FROM trust_online_payments WHERE id = %s",
        (started["id"],),
    ).fetchone()
    assert recorded == ("ach", Decimal("0.50"), "fake_connect")


def test_a_tenant_pays_by_card_the_fee_on_top_where_allowed_and_never_more_than_3_percent(
    client, tenant_client, conn, staff, tenant, lease, pmc
):
    turn_on(client)
    connect_company(client)
    fund_company(conn, pmc)
    ok(client.post("/api/payments/connect/card-fees", json={"card_fee_payer": "tenant"}), 204)
    methods = ok(tenant_client.get(f"/api/payments/connect/leases/{lease}?amount=1000.00"))
    card = methods["methods"][1]
    # Stripe's 29.30 and Occupella's 0.50, grossed up, top 3% of 1,000.00: the tenant pays 30.00.
    assert (card["fee_payer"], card["tenant_fee"], card["total"]) == ("tenant", "30.00", "1030.00")
    # Autopay by card: the company's, whatever it chose.
    assert methods["autopay"][1]["fee_payer"] == "company"
    assert methods["autopay"][1]["why_company"] == "a card fee on autopay is the company's"
    started = pay(tenant_client, lease, "1000.00", "card")
    row = conn.execute(
        "SELECT tenant_fee, fee_payer, platform_fee FROM trust_online_payments WHERE id = %s",
        (started["id"],),
    ).fetchone()
    assert row == (Decimal("30.00"), "tenant", Decimal("0.50"))
    company_before = balance(conn, pmc.pmc_income)
    says(tenant_client, started["id"], "paid")
    # In: the tenant's 30.00. Out: Stripe's 2.9% + 0.30 on 1,030.00 (30.17) and Occupella's
    # 0.50; the company pays the 0.67 the cap left.
    assert balance(conn, pmc.pmc_income) - company_before == Decimal("-0.67")
    assert set(bank_sums(conn, pmc.pmc_id).values()) == {Decimal(0)}


def test_a_state_that_bans_a_card_surcharge_makes_the_card_fee_the_companys(
    client, tenant_client, conn, staff, tenant, lease, pmc
):
    turn_on(client)
    connect_company(client)
    ok(client.post("/api/payments/connect/card-fees", json={"card_fee_payer": "tenant"}), 204)
    conn.execute(
        "INSERT INTO trust_property_addresses (pmc_id, property_id, line_1, city, state, zip)"
        " VALUES (%s, %s, '1 Synthetic Way', 'Demo City', 'MA', '00001')",
        (pmc.pmc_id, pmc.owners[0].property_id),
    )
    card = ok(tenant_client.get(f"/api/payments/connect/leases/{lease}?amount=500.00"))["methods"][
        1
    ]
    assert (card["fee_payer"], card["tenant_fee"]) == ("company", "0.00")
    assert card["why_company"].startswith("Massachusetts General Laws")


def test_what_isnt_a_payment_is_refused(client, tenant_client, staff, tenant, lease):
    turn_on(client)
    connect_company(client)
    url = f"/api/payments/connect/leases/{lease}/checkout"
    refused(tenant_client.post(url, json={"amount": "100.00", "method": "cash"}), 422)
    refused(tenant_client.post(url, json={"amount": "0", "method": "ach"}), 422)
    refused(tenant_client.post(url, json={"amount": "1.001", "method": "ach"}), 422)
    refused(tenant_client.post(url, json={"method": "ach"}), 422)
    refused(
        tenant_client.post(url, json={"amount": "10000.01", "method": "card"}),
        422,
        "A card payment is $10,000.00 at most: pay more by bank.",
    )
    refused(tenant_client.get(f"/api/payments/connect/leases/{lease}?amount=-1"), 422)
    # Another lease reads as not found; staff and nobody can't pay as a tenant.
    refused(
        tenant_client.post(
            f"/api/payments/connect/leases/{uuid4()}/checkout",
            json={"amount": "1.00", "method": "ach"},
        ),
        404,
    )
    refused(client.post(url, json={"amount": "1.00", "method": "ach"}), 403)
    with TestClient(client.app) as nobody:
        refused(nobody.post(url, json={"amount": "1.00", "method": "ach"}), 401)
        refused(nobody.get(f"/api/payments/connect/leases/{lease}"), 401)


def test_a_card_dispute_is_a_return_with_its_fee_and_one_won_puts_the_payment_back(
    client, tenant_client, conn, staff, tenant, lease, pmc
):
    turn_on(client)
    connect_company(client)
    fund_company(conn, pmc, "100.00")
    started = pay(tenant_client, lease, "1000.00", "card")
    # A dispute before the payment is paid: nothing to return yet, Stripe sends it again.
    refused(
        tenant_client.post(
            f"/api/payments/connect/fake/payments/{started['id']}", json={"outcome": "disputed"}
        ),
        409,
    )
    says(tenant_client, started["id"], "paid")
    owner_after_paid = balance(conn, pmc.owners[0].account)
    company_after_paid = balance(conn, pmc.pmc_income)
    says(tenant_client, started["id"], "disputed")
    # Recorded the way a bank return is: the owner's money back out, the lease owing again, and
    # the 15.00 dispute fee out of trust from the company's account.
    assert balance(conn, pmc.owners[0].account) == owner_after_paid - Decimal("1000.00")
    assert company_after_paid - balance(conn, pmc.pmc_income) == Decimal("15.00")
    assert ok(tenant_client.get(f"/api/payments/leases/{lease}"))["owed"] == "1200.00"
    assert (
        ok(tenant_client.get(f"/api/payments/connect/fake/payments/{started['id']}"))["dispute"]
        == "opened"
    )
    # A dispute can't be won twice or lost after it was won.
    says(tenant_client, started["id"], "won")
    says(tenant_client, started["id"], "lost")
    assert (
        ok(tenant_client.get(f"/api/payments/connect/fake/payments/{started['id']}"))["dispute"]
        == "won"
    )
    # Won: the payment is back with the owner, the dispute fee back in the company's account.
    assert balance(conn, pmc.owners[0].account) == owner_after_paid
    assert balance(conn, pmc.pmc_income) == company_after_paid
    assert ok(tenant_client.get(f"/api/payments/leases/{lease}"))["owed"] != "1200.00"
    assert ok(client.get("/api/payments/waiting")) == []
    assert set(bank_sums(conn, pmc.pmc_id).values()) == {Decimal(0)}


def test_a_dispute_fee_the_company_cant_cover_waits_and_a_lost_dispute_moves_nothing_more(
    client, tenant_client, conn, staff, tenant, lease, pmc
):
    turn_on(client, "tenant")
    connect_company(client)
    started = pay(tenant_client, lease, "200.00", "ach")
    says(tenant_client, started["id"], "paid")
    says(tenant_client, started["id"], "disputed")
    waiting = ok(client.get("/api/payments/waiting"))
    assert ("dispute fee", "15.00") in [(w["what"], w["company_owes"]) for w in waiting]
    needed = waiting[0]["cover_needed"]
    ok(
        client.post(
            "/api/payments/partner-fees/cover",
            json={
                "bank_account_id": waiting[0]["bank_account_id"],
                "amount": needed,
                "request_id": str(uuid4()),
            },
        )
    )
    assert ok(client.get("/api/payments/waiting")) == []
    before = balance(conn, pmc.pmc_income)
    says(tenant_client, started["id"], "lost")
    assert balance(conn, pmc.pmc_income) == before
    assert set(bank_sums(conn, pmc.pmc_id).values()) == {Decimal(0)}


def test_a_tenant_sets_up_autopay_by_card_and_its_payment_carries_occupellas_fee(
    client, tenant_client, conn, staff, tenant, lease, pmc
):
    turn_on(client)
    connect_company(client)
    url = f"/api/payments/connect/leases/{lease}/autopay"
    refused(
        tenant_client.post(url, json={"rule": "fixed", "amount": "100.00", "method": "check"}), 422
    )
    refused(
        tenant_client.post(url, json={"rule": "fixed", "amount": "20000.00", "method": "card"}), 422
    )
    refused(client.post(url, json={"rule": "fixed", "amount": "100.00", "method": "card"}), 403)
    made = ok(
        tenant_client.post(url, json={"rule": "fixed", "amount": "100.00", "method": "card"}), 201
    )
    assert made["url"] == f"http://testserver/pay/fake-connect-save#{made['id']}"
    page = ok(tenant_client.get(f"/api/payments/connect/fake/autopay/{made['id']}"))
    assert (page["method"], page["saved"]) == ("card", False)
    ok(tenant_client.post(f"/api/payments/connect/fake/autopay/{made['id']}", json={}), 204)
    shown = ok(tenant_client.get(f"/api/payments/leases/{lease}/autopay"))["autopays"][0]
    assert shown["account"] == "Test card ••••4242"
    # Another, its card saved before this month's due day (as the fake page would have then).
    made = ok(
        tenant_client.post(url, json={"rule": "fixed", "amount": "100.00", "method": "card"}), 201
    )
    conn.execute(
        "INSERT INTO trust_autopay_accounts (pmc_id, autopay_id, partner_customer, partner_method,"
        " label, partner_event, saved_at) VALUES (%s, %s, 'fake_cus', 'fake_pm',"
        " 'Test card ••••4242', %s, %s)",
        (pmc.pmc_id, made["id"], f"evt_{made['id']}", datetime.now(UTC) - timedelta(days=90)),
    )
    # Run rent now starts the month's payment by card, the fee the company's.
    month = TODAY.replace(day=1)
    done = ok(
        client.post("/api/rent/run", json={"due_on": str(month), "late_fees_as_of": str(TODAY)})
    )
    assert done["autopay"] == 1
    row = conn.execute(
        "SELECT p.method, p.tenant_fee, p.platform_fee, p.partner, c.account_id IS NOT NULL"
        " FROM trust_autopay_runs AS r JOIN trust_online_payments AS p ON p.id = r.payment_id"
        " LEFT JOIN trust_connected_payments AS c ON c.online_payment_id = p.id"
        " WHERE r.autopay_id = %s",
        (made["id"],),
    ).fetchone()
    assert row == ("card", Decimal("0.00"), Decimal("0.50"), "fake_connect", True)
    # After the request, the payment went to the company's connected account, once.
    taken = conn.execute(
        "SELECT s.partner_ref FROM trust_autopay_submissions AS s"
        " JOIN trust_autopay_runs AS r ON r.payment_id = s.payment_id WHERE r.autopay_id = %s",
        (made["id"],),
    ).fetchall()
    assert len(taken) == 1 and taken[0][0].startswith("fake_connect_pi_")


# --- an owner adds money --------------------------------------------------------------------------


def test_an_owner_adds_money_by_card_and_the_fees_are_the_companys(
    client, owner_client, conn, app_conn, staff, pmc
):
    turn_on(client)
    join_as_owner(owner_client, app_conn, pmc)
    assert ok(owner_client.get("/api/payments/connect/owner"))["connect"] is False
    connect_company(client)
    fund_company(conn, pmc)
    assert ok(owner_client.get("/api/payments/connect/owner")) == {
        "connect": True,
        "card_max": "10000.00",
    }
    account = pmc.owners[0].account
    url = "/api/payments/connect/owner/checkout"
    refused(
        owner_client.post(url, json={"account_id": account, "amount": "50.00", "method": "wire"}),
        422,
    )
    refused(
        owner_client.post(
            url, json={"account_id": pmc.owners[1].account, "amount": "50.00", "method": "card"}
        ),
        404,
    )
    refused(
        client.post(url, json={"account_id": account, "amount": "50.00", "method": "card"}), 403
    )
    started = ok(
        owner_client.post(url, json={"account_id": account, "amount": "500.00", "method": "card"}),
        201,
    )
    page = ok(owner_client.get(f"/api/payments/connect/fake/payments/{started['id']}"))
    assert (page["total"], page["tenant_fee"], page["done"]) == (
        "500.00",
        "0.00",
        "/pay/owner-done",
    )
    owner_before = balance(conn, account)
    company_before = balance(conn, pmc.pmc_income)
    says(owner_client, started["id"], "paid")
    assert balance(conn, account) - owner_before == Decimal("500.00")
    # Stripe's 14.80 and Occupella's 0.50: the company's.
    assert company_before - balance(conn, pmc.pmc_income) == Decimal("15.30")
    says(owner_client, started["id"], "disputed")
    assert balance(conn, account) == owner_before
    says(owner_client, started["id"], "won")
    assert balance(conn, account) - owner_before == Decimal("500.00")
    assert set(bank_sums(conn, pmc.pmc_id).values()) == {Decimal(0)}


# --- the platform's webhook -------------------------------------------------------------------


@pytest.fixture
def stripe(monkeypatch):
    """Stripe's answers by method and path, and each call made: its form, account and version."""
    answers, calls = {}, []

    def call(secret, method, path, form, key="", account=None, version="", timeout=20.0):
        calls.append(
            {
                "method": method,
                "path": path,
                "form": dict(form),
                "account": account,
                "version": version,
            }
        )
        return answers[(method, path)]

    monkeypatch.setattr(connect, "stripe_call", call)
    return answers, calls


def stripe_settings(
    database_url, monkeypatch, key=PLATFORM_TEST_KEY, public_url="http://testserver"
):
    monkeypatch.setenv("PMS_STRIPE_CONNECT_SECRET_KEY", key)
    monkeypatch.setenv("PMS_STRIPE_CONNECT_WEBHOOK_SECRET", CONNECT_WEBHOOK)
    return Settings(
        database_url=database_url,
        public_url=public_url,
        cookie_secure=False,
        connect="stripe",
    )


def signed(client, account, kind, subject, livemode=False):
    now = int(time.time())
    body = json.dumps(
        {
            "id": f"evt_{uuid4().hex}",
            "type": kind,
            "created": now,
            "livemode": livemode,
            "account": account,
            "data": {"object": subject},
        }
    ).encode()
    mac = hmac.new(CONNECT_WEBHOOK.encode(), f"{now}.".encode() + body, hashlib.sha256).hexdigest()
    return client.post(
        "/api/payments/connect/webhook",
        content=body,
        headers={"stripe-signature": f"t={now},v1={mac}", "content-type": "application/json"},
    )


def test_the_platforms_webhook_maps_each_report_to_the_company_that_connected_its_account(
    database_url, monkeypatch, stripe, conn, app_conn, pmc, lease
):
    answers, calls = stripe
    answers[("POST", "/accounts")] = {"id": "acct_Synthetic0001"}
    answers[("POST", "/account_links")] = {"url": "https://connect.test/onboarding"}
    with TestClient(create_app(stripe_settings(database_url, monkeypatch))) as browser:
        join_as_staff(browser, conn, app_conn, pmc)
        ok(
            browser.post(
                "/api/payments/settings", json={"online": True, "ach_fee_payer": "company"}
            ),
            204,
        )
        assert ok(browser.post("/api/payments/connect/start", json={})) == {
            "url": "https://connect.test/onboarding"
        }
        created = calls[0]["form"]
        assert created["controller[fees][payer]"] == "account"
        assert created["controller[losses][payments]"] == "stripe"
        assert created["controller[stripe_dashboard][type]"] == "full"
        # An account no company connected is refused, so Stripe sends it again; a bad signature too.
        refused(
            signed(
                browser, "acct_SomeoneElse0001", "account.updated", {"id": "acct_SomeoneElse0001"}
            ),
            404,
        )
        bad = browser.post(
            "/api/payments/connect/webhook",
            content=b"{}",
            headers={"stripe-signature": "t=1,v1=00", "content-type": "application/json"},
        )
        refused(bad, 400)
        # Stripe says the account takes charges: from then on the company's payments go through it.
        subject = {
            "id": "acct_Synthetic0001",
            "charges_enabled": True,
            "payouts_enabled": True,
            "details_submitted": True,
            "requirements": {"currently_due": [], "past_due": []},
        }
        assert ok(signed(browser, "acct_Synthetic0001", "account.updated", subject)) == {
            "recorded": True
        }
        assert ok(browser.get("/api/payments/connect"))["using"] is True
        # A tenant pays by bank: a direct charge on the company's account with Occupella's fee.
        tenant_browser = TestClient(browser.app)
        join_as_tenant(tenant_browser, conn, app_conn, pmc)
        answers[("POST", "/checkout/sessions")] = {
            "id": "cs_Synthetic1",
            "url": "https://checkout.test/1",
        }
        started = pay(tenant_browser, lease, "1200.00", "ach")
        session = calls[-1]
        assert session["account"] == "acct_Synthetic0001"
        assert session["form"]["payment_intent_data[application_fee_amount]"] == "50"
        assert session["form"]["payment_method_types[0]"] == "us_bank_account"
        paid = {
            "id": "cs_Synthetic1",
            "object": "checkout.session",
            "payment_status": "paid",
            "client_reference_id": started["id"],
            "payment_intent": "pi_Synthetic1",
        }
        answers[("GET", "/payment_intents/pi_Synthetic1")] = {
            "latest_charge": {"balance_transaction": {"fee": 550}}
        }
        owner_before = balance(conn, pmc.owners[0].account)
        assert ok(signed(browser, "acct_Synthetic0001", "checkout.session.completed", paid)) == {
            "recorded": True
        }
        assert balance(conn, pmc.owners[0].account) - owner_before == Decimal("1200.00")
        # A dispute: returned, with its fee from the balance transaction.
        answers[("GET", "/payment_intents/pi_Synthetic1")] = {
            "metadata": {"payment_id": started["id"]}
        }
        dispute = {
            "id": "du_Synthetic1",
            "payment_intent": "pi_Synthetic1",
            "status": "needs_response",
            "balance_transactions": [{"amount": -120000, "fee": 1500}],
        }
        ok(signed(browser, "acct_Synthetic0001", "charge.dispute.created", dispute))
        kinds = conn.execute(
            "SELECT kind, fee FROM trust_connect_disputes WHERE online_payment_id = %s",
            (started["id"],),
        ).fetchall()
        assert kinds == [("opened", Decimal("15.00"))]
        assert balance(conn, pmc.owners[0].account) == owner_before


def test_a_test_mode_report_or_account_never_books_money_on_a_live_server(
    database_url, monkeypatch, stripe, conn, app_conn, pmc
):
    # A live server refuses the platform's test key at start (P-048).
    with pytest.raises(deployment.KeysNotLive, match="PMS_STRIPE_CONNECT_SECRET_KEY"):
        create_app(
            stripe_settings(database_url, monkeypatch, public_url="https://rentals.example.org")
        )
    answers, _ = stripe
    live = stripe_settings(
        database_url, monkeypatch, key=PLATFORM_LIVE_KEY, public_url="https://rentals.example.org"
    )
    answers[("POST", "/accounts")] = {"id": "acct_SyntheticLive1"}
    answers[("POST", "/account_links")] = {"url": "https://connect.test/onboarding"}
    with TestClient(create_app(live)) as browser:
        join_as_staff(browser, conn, app_conn, pmc)
        # An account connected in test mode before the server went live takes no payment here.
        staff_id = conn.execute(
            "SELECT id FROM trust_staff WHERE pmc_id = %s ORDER BY created_at LIMIT 1",
            (pmc.pmc_id,),
        ).fetchone()[0]
        conn.execute("SELECT set_config('trust.staff_id', %s, false)", (str(staff_id),))
        conn.execute(
            "INSERT INTO trust_connected_accounts (pmc_id, account_id, partner, livemode)"
            " VALUES (%s, 'acct_SyntheticTest1', 'stripe_connect', false)",
            (pmc.pmc_id,),
        )
        conn.execute(
            "INSERT INTO trust_connected_account_states (pmc_id, account_id, charges_enabled,"
            " payouts_enabled, details_submitted, reported_at)"
            " VALUES (%s, 'acct_SyntheticTest1', true, true, true, now())",
            (pmc.pmc_id,),
        )
        conn.execute("SELECT set_config('trust.staff_id', '', false)")
        assert ok(browser.get("/api/payments/connect"))["using"] is False
        # Its test-mode report is acknowledged and never recorded.
        subject = {"id": "acct_SyntheticTest1", "charges_enabled": False}
        assert ok(signed(browser, "acct_SyntheticTest1", "account.updated", subject, False)) == {
            "recorded": False
        }
        # Connect Stripe on the live server makes a live account in place of the test one.
        ok(browser.post("/api/payments/connect/start", json={}))
        latest = connect.connection_of(conn, pmc.pmc_id)
        assert (latest.account, latest.livemode) == ("acct_SyntheticLive1", True)


def test_disconnecting_stops_autopays_saved_at_the_account_and_another_accounts_report_never_lands(
    client, tenant_client, conn, staff, tenant, lease, pmc
):
    turn_on(client)
    account = connect_company(client)
    made = ok(
        tenant_client.post(
            f"/api/payments/connect/leases/{lease}/autopay",
            json={"rule": "fixed", "amount": "100.00", "method": "ach"},
        ),
        201,
    )
    ok(tenant_client.post(f"/api/payments/connect/fake/autopay/{made['id']}", json={}), 204)
    started = pay(tenant_client, lease, "100.00", "ach")
    # A report from another connected account about this payment is acknowledged, never booked.
    other = connect.FakeConnected("fake_acct_SomeoneElse01", conn)
    event = payments.Event(
        UUID(started["id"]), "paid", f"evt_{uuid4().hex}", datetime.now(UTC), Decimal("0.50")
    )
    assert connect_api.record_items(conn, other, pmc.pmc_id, [event]) is False
    assert (
        ok(tenant_client.get(f"/api/payments/connect/fake/payments/{started['id']}"))["status"]
        == "started"
    )
    # Disconnected: the autopay saved at the account stops, saying why.
    ok(client.post("/api/payments/connect/disconnect", json={}), 204)
    shown = ok(tenant_client.get(f"/api/payments/leases/{lease}/autopay"))["autopays"][0]
    assert shown["stopped"] == "account changed"
    # A payment started through it still reports in, though the account is disconnected.
    says(tenant_client, started["id"], "paid")
    assert (
        ok(tenant_client.get(f"/api/payments/connect/fake/payments/{started['id']}"))["status"]
        == "paid"
    )
    refused(client.post("/api/payments/connect/disconnect", json={}), 404)
    assert account.startswith("fake_acct_")


def test_an_autopay_saved_at_an_account_the_company_replaced_stops_at_its_due_day(
    client, tenant_client, conn, staff, tenant, lease, pmc
):
    turn_on(client)
    connect_company(client)
    made = ok(
        tenant_client.post(
            f"/api/payments/connect/leases/{lease}/autopay",
            json={"rule": "fixed", "amount": "100.00", "method": "card"},
        ),
        201,
    )
    conn.execute(
        "INSERT INTO trust_autopay_accounts (pmc_id, autopay_id, partner_customer, partner_method,"
        " label, partner_event, saved_at) VALUES (%s, %s, 'fake_cus', 'fake_pm',"
        " 'Test card ••••4242', %s, %s)",
        (pmc.pmc_id, made["id"], f"evt_{made['id']}", datetime.now(UTC) - timedelta(days=90)),
    )
    # The company now takes payments through another account (connected anew, in another mode
    # before, say): the card saved at the first can't be charged there.
    with acting_as(conn, staff.person_id):
        conn.execute(
            "INSERT INTO trust_connected_accounts (pmc_id, account_id, partner, livemode)"
            " VALUES (%s, 'fake_acct_Replacement01', 'fake_connect', false)",
            (pmc.pmc_id,),
        )
    conn.execute(
        "INSERT INTO trust_connected_account_states (pmc_id, account_id, charges_enabled,"
        " payouts_enabled, details_submitted, reported_at)"
        " VALUES (%s, 'fake_acct_Replacement01', true, true, true, now())",
        (pmc.pmc_id,),
    )
    month = TODAY.replace(day=1)
    done = ok(
        client.post("/api/rent/run", json={"due_on": str(month), "late_fees_as_of": str(TODAY)})
    )
    assert done["autopay"] == 0
    shown = ok(tenant_client.get(f"/api/payments/leases/{lease}/autopay"))["autopays"][0]
    assert shown["stopped"] == "account changed"
    # Run rent now names the admin who ran it; the nightly run would name nobody.
    assert conn.execute(
        "SELECT why, recorded_by FROM trust_autopay_stops WHERE autopay_id = %s", (made["id"],)
    ).fetchone() == ("account changed", staff.person_id)
