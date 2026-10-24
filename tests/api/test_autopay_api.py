"""Autopay through the API (src/pms/api/autopay.py, src/pms/autopay.py): a tenant sets it up from
the portal and saves a bank account on the partner's page; an admin's Run rent now (as the nightly
run) starts the payment due and it goes to the partner after the request commits; the partner's
report records it as any payment online; the tenant or staff turn it off."""

import hashlib
import hmac
import json
import time
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from helpers import add_unit, charge, end_lease, hold_payments, open_lease
from people import join_as_staff, join_as_tenant, tenant_of

from pms import autopay, payments
from pms.api import Settings, create_app

TODAY = datetime.now(UTC).date()
MONTH = TODAY.replace(day=1)
DAY = timedelta(days=1)
SECRET = "whsec_test_not_a_real_secret"


@pytest.fixture
def settings(database_url):
    return Settings(
        database_url=database_url,
        public_url="http://testserver",
        cookie_secure=False,
        payments="fake",
    )


@pytest.fixture
def staff(client, conn, app_conn, pmc):
    return join_as_staff(client, conn, app_conn, pmc)


@pytest.fixture
def tenant_client(settings):
    with TestClient(create_app(settings)) as browser:
        yield browser


@pytest.fixture
def lease(conn, pmc):
    """A lease due on the first, this month's 1,200.00 charged."""
    unit = add_unit(conn, pmc.pmc_id, pmc.owners[0].property_id)
    lease = open_lease(
        conn, pmc.pmc_id, unit, MONTH - 60 * DAY, None, "1200.00", [tenant_of(conn, pmc)]
    )
    charge(conn, pmc.pmc_id, lease, MONTH, "1200.00")
    return lease


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


def set_up(tenant_client, lease, rule="balance", amount="1500.00"):
    return ok(
        tenant_client.post(
            f"/api/payments/leases/{lease}/autopay", json={"rule": rule, "amount": amount}
        ),
        201,
    )


def saved_long_ago(conn, pmc, autopay_id):
    """The account saved before this month's due date, as the fake page would have."""
    conn.execute(
        "INSERT INTO trust_autopay_accounts (pmc_id, autopay_id, partner_customer, partner_method,"
        " label, partner_event, saved_at) VALUES (%s, %s, 'fake_cus', 'fake_pm',"
        " 'Test bank ••••0000', %s, %s)",
        (pmc.pmc_id, autopay_id, f"evt_{autopay_id}", datetime.now(UTC) - timedelta(days=90)),
    )


def view(tenant_client, lease):
    return ok(tenant_client.get(f"/api/payments/leases/{lease}/autopay"))


# --- the tenant sets it up --------------------------------------------------------------------


def test_a_tenant_sets_up_autopay_and_saves_a_bank_account_on_the_partners_page(
    client, tenant_client, staff, tenant, lease
):
    turn_on(client, "tenant")
    shown = view(tenant_client, lease)
    assert (shown["available"], shown["fee_payer"], shown["autopays"]) == (True, "tenant", [])
    assert shown["next_due"] == str((MONTH + 32 * DAY).replace(day=1))
    started = set_up(tenant_client, lease)
    assert started["url"] == f"http://testserver/pay/fake-save#{started['id']}"
    mine = view(tenant_client, lease)["autopays"]
    assert [(a["rule"], a["amount"], a["account"], a["stopped"]) for a in mine] == [
        ("balance", "1500.00", None, None)
    ]
    # The fake partner's page saves a made-up account.
    page = f"/api/payments/fake-save/{started['id']}"
    assert ok(tenant_client.get(page))["saved"] is False
    ok(tenant_client.post(page, json={}), 204)
    ok(tenant_client.post(page, json={}), 204)  # saved once
    assert ok(tenant_client.get(page))["saved"] is True
    assert view(tenant_client, lease)["autopays"][0]["account"] == "Test bank ••••0000"
    # Setting up another turns the first off.
    again = set_up(tenant_client, lease, "fixed", "1200.00")
    both = view(tenant_client, lease)["autopays"]
    assert [(a["id"], a["stopped"]) for a in both] == [
        (again["id"], None),
        (started["id"], "replaced"),
    ]


def test_the_tenant_turns_it_off(client, tenant_client, staff, tenant, lease):
    turn_on(client)
    set_up(tenant_client, lease)
    ok(tenant_client.post(f"/api/payments/leases/{lease}/autopay/stop", json={}), 204)
    assert view(tenant_client, lease)["autopays"][0]["stopped"] == "tenant"
    refused(
        tenant_client.post(f"/api/payments/leases/{lease}/autopay/stop", json={}),
        404,
        "you have no autopay of this lease on",
    )


def test_setting_up_again_after_turning_it_off_keeps_the_old_one_as_it_stopped(
    client, tenant_client, staff, tenant, lease
):
    turn_on(client)
    first = set_up(tenant_client, lease)["id"]
    ok(tenant_client.post(f"/api/payments/leases/{lease}/autopay/stop", json={}), 204)
    second = set_up(tenant_client, lease)["id"]
    stops = {a["id"]: a["stopped"] for a in view(tenant_client, lease)["autopays"]}
    assert stops == {first: "tenant", second: None}


def test_a_partner_that_does_not_answer_saves_no_autopay(
    client, tenant_client, conn, staff, tenant, lease, monkeypatch
):
    turn_on(client)

    def down(self, autopay_id, done_url):
        raise payments.PartnerFailed("down")

    monkeypatch.setattr(payments.FakePartner, "save_account", down)
    refused(
        tenant_client.post(
            f"/api/payments/leases/{lease}/autopay", json={"rule": "balance", "amount": "1500.00"}
        ),
        502,
        "the payments partner didn't answer: try again soon",
    )
    assert conn.execute(
        "SELECT count(*) FROM trust_autopay WHERE lease_id = %s", (lease,)
    ).fetchone() == (0,)


def test_a_lease_with_no_rent_due_ahead_shows_no_next_due_day(
    client, tenant_client, conn, staff, tenant, lease, pmc
):
    turn_on(client)
    end_lease(conn, pmc.pmc_id, lease, TODAY)
    assert view(tenant_client, lease)["next_due"] is None


def test_no_autopay_while_payments_online_are_off_or_the_lease_is_on_hold(
    client, conn, tenant_client, staff, tenant, lease, pmc
):
    assert view(tenant_client, lease)["why_not"] == "your manager doesn't take payments online"
    refused(
        tenant_client.post(
            f"/api/payments/leases/{lease}/autopay", json={"rule": "fixed", "amount": "1.00"}
        ),
        409,
        "your manager doesn't take payments online",
    )
    turn_on(client)
    hold_payments(conn, pmc.pmc_id, lease, TODAY - DAY)
    assert view(tenant_client, lease)["available"] is False
    refused(
        tenant_client.post(
            f"/api/payments/leases/{lease}/autopay", json={"rule": "fixed", "amount": "1.00"}
        ),
        409,
    )


@pytest.mark.parametrize(
    "body",
    [
        {"rule": "weekly", "amount": "100.00"},
        {"rule": "fixed", "amount": "0"},
        {"rule": "fixed", "amount": "100000.01"},
        {"rule": "balance", "amount": "1.005"},
        {"rule": "balance"},
        {"amount": "10.00"},
    ],
)
def test_bad_autopay_input_is_refused(client, tenant_client, staff, tenant, lease, body):
    turn_on(client)
    refused(tenant_client.post(f"/api/payments/leases/{lease}/autopay", json=body), 422)


def test_a_tenant_sees_and_sets_up_only_their_own_leases_autopay(
    client, tenant_client, conn, app_conn, staff, pmc
):
    unit = add_unit(conn, pmc.pmc_id, pmc.owners[0].property_id, "Unit 9")
    someone = conn.execute(
        "INSERT INTO trust_tenants (pmc_id, property_id, display_name)"
        " VALUES (%s, %s, 'Tenant 9') RETURNING id",
        (pmc.pmc_id, pmc.owners[0].property_id),
    ).fetchone()[0]
    theirs = open_lease(conn, pmc.pmc_id, unit, MONTH, None, "900.00", [someone])
    join_as_tenant(tenant_client, conn, app_conn, pmc)
    turn_on(client)
    refused(tenant_client.get(f"/api/payments/leases/{theirs}/autopay"), 404)
    refused(
        tenant_client.post(
            f"/api/payments/leases/{theirs}/autopay", json={"rule": "fixed", "amount": "1.00"}
        ),
        404,
    )
    refused(tenant_client.post(f"/api/payments/leases/{theirs}/autopay/stop", json={}), 404)


def test_staff_and_nobody_use_none_of_the_tenants_autopay_pages(client, other_client, staff, lease):
    refused(client.get(f"/api/payments/leases/{lease}/autopay"), 403)
    refused(
        client.post(f"/api/payments/leases/{lease}/autopay", json={"rule": "fixed", "amount": "1"}),
        403,
    )
    refused(other_client.get(f"/api/payments/leases/{lease}/autopay"), 401)
    refused(other_client.post(f"/api/payments/leases/{lease}/autopay/stop", json={}), 401)
    refused(other_client.get(f"/api/payments/autopay?lease={lease}"), 401)
    refused(other_client.post(f"/api/payments/autopay/{uuid4()}/stop", json={}), 401)


# --- the payment, from Run rent now to the books ------------------------------------------------


def test_run_rent_now_starts_the_autopay_payment_which_goes_to_the_partner_and_the_books(
    client, tenant_client, conn, staff, tenant, lease, pmc
):
    turn_on(client)
    mine = set_up(tenant_client, lease)["id"]
    saved_long_ago(conn, pmc, mine)
    done = ok(
        client.post("/api/rent/run", json={"due_on": str(MONTH), "late_fees_as_of": str(TODAY)})
    )
    assert done["autopay"] == 1
    payment = autopay.payment_id_for(UUID(mine), MONTH)
    # After the request committed, the payment went to the (fake) partner.
    assert conn.execute(
        "SELECT partner_ref FROM trust_autopay_submissions WHERE payment_id = %s", (payment,)
    ).fetchone() == (f"fake_pi_{payment.hex}",)
    runs = view(tenant_client, lease)["autopays"][0]["runs"]
    assert runs == [
        {
            "due_on": str(MONTH),
            "amount": "1200.00",
            "tenant_fee": "0.00",
            "status": "started",
            "skipped": None,
        }
    ]
    queued = ok(client.get("/api/payments/fake"))
    assert str(payment) in [q["id"] for q in queued]
    ok(client.post(f"/api/payments/fake/{payment}", json={"outcome": "paid"}))
    assert view(tenant_client, lease)["autopays"][0]["runs"][0]["status"] == "paid"
    assert conn.execute(
        "SELECT 1 FROM pgledger_transfers WHERE metadata ->> 'key' = %s", (f"payment/{payment}",)
    ).fetchone()
    # Run again: nothing more for this due date.
    again = ok(
        client.post("/api/rent/run", json={"due_on": str(MONTH), "late_fees_as_of": str(TODAY)})
    )
    assert again["autopay"] == 0


# --- staff ------------------------------------------------------------------------------------


def test_staff_see_a_leases_autopays_and_turn_one_off_naming_who(
    client, tenant_client, conn, staff, tenant, lease
):
    turn_on(client)
    mine = set_up(tenant_client, lease)["id"]
    listed = ok(client.get(f"/api/payments/autopay?lease={lease}"))
    assert [(a["id"], a["tenant"], a["stopped"]) for a in listed] == [(mine, "Tenant 1", None)]
    ok(client.post(f"/api/payments/autopay/{mine}/stop", json={}), 204)
    assert conn.execute(
        "SELECT why, recorded_by FROM trust_autopay_stops WHERE autopay_id = %s", (mine,)
    ).fetchone() == ("staff", staff.person_id)
    refused(
        client.post(f"/api/payments/autopay/{mine}/stop", json={}),
        409,
        "this autopay is off already",
    )
    refused(client.post(f"/api/payments/autopay/{uuid4()}/stop", json={}), 404)
    refused(client.get(f"/api/payments/autopay?lease={uuid4()}"), 404)
    refused(client.get("/api/payments/autopay?lease=not-a-lease"), 422)


def test_a_tenant_uses_none_of_staffs_autopay_pages(client, tenant_client, staff, tenant, lease):
    turn_on(client)
    mine = set_up(tenant_client, lease)["id"]
    refused(tenant_client.get(f"/api/payments/autopay?lease={lease}"), 403)
    refused(tenant_client.post(f"/api/payments/autopay/{mine}/stop", json={}), 403)


def test_the_fake_partners_pages_are_there_only_with_the_fake_partner(
    conn, app_conn, pmc, lease, database_url
):
    bare = Settings(database_url=database_url, public_url="http://testserver", cookie_secure=False)
    with TestClient(create_app(bare)) as browser:
        refused(browser.get("/api/payments/fake"), 404)
        refused(browser.get(f"/api/payments/fake-save/{uuid4()}"), 404)
        refused(browser.post(f"/api/payments/fake-save/{uuid4()}", json={}), 404)


# --- Stripe saves the account ------------------------------------------------------------------


@pytest.fixture
def stripe_app(database_url, monkeypatch):
    monkeypatch.setenv("PMS_STRIPE_SECRET_KEY", "sk_test_not_real")
    monkeypatch.setenv("PMS_STRIPE_WEBHOOK_SECRET", SECRET)
    answers = {}
    monkeypatch.setattr(
        payments.StripePartner,
        "_call",
        lambda self, method, path, form, key="": answers[(method, path)],
    )
    settings = Settings(
        database_url=database_url,
        public_url="http://testserver",
        cookie_secure=False,
        payments="stripe",
    )
    with TestClient(create_app(settings)) as browser, TestClient(create_app(settings)) as tenant:
        yield browser, tenant, answers


def stripe_report(browser, kind, subject):
    now = int(time.time())
    body = json.dumps(
        {"id": f"evt_{uuid4().hex}", "type": kind, "created": now, "data": {"object": subject}}
    ).encode()
    mac = hmac.new(SECRET.encode(), f"{now}.".encode() + body, hashlib.sha256).hexdigest()
    return browser.post(
        "/api/payments/webhook/stripe",
        content=body,
        headers={"stripe-signature": f"t={now},v1={mac}", "content-type": "application/json"},
    )


def test_stripe_saves_the_bank_account_and_its_report_records_it(
    stripe_app, conn, app_conn, pmc, lease
):
    browser, tenant_browser, answers = stripe_app
    join_as_staff(browser, conn, app_conn, pmc)
    turn_on(browser)
    join_as_tenant(tenant_browser, conn, app_conn, pmc)
    answers[("POST", "/customers")] = {"id": "cus_1"}
    answers[("POST", "/checkout/sessions")] = {"id": "cs_setup_1", "url": "https://checkout.test/s"}
    started = set_up(tenant_browser, lease)
    assert started["url"] == "https://checkout.test/s"
    answers[("GET", "/setup_intents/seti_1")] = {
        "status": "succeeded",
        "customer": "cus_1",
        "mandate": "mandate_1",
        "metadata": {"autopay_id": started["id"]},
        "payment_method": {
            "id": "pm_1",
            "us_bank_account": {"bank_name": "STRIPE TEST BANK", "last4": "6789"},
        },
    }
    subject = {"id": "cs_setup_1", "mode": "setup", "setup_intent": "seti_1"}
    assert ok(stripe_report(browser, "checkout.session.completed", subject)) == {"recorded": True}
    assert conn.execute(
        "SELECT partner_customer, partner_method, partner_mandate, label"
        " FROM trust_autopay_accounts WHERE autopay_id = %s",
        (started["id"],),
    ).fetchone() == ("cus_1", "pm_1", "mandate_1", "STRIPE TEST BANK ••••6789")
    # Still being verified: nothing yet; an account for no autopay of the PMS's: not recorded.
    answers[("GET", "/setup_intents/seti_2")] = {"status": "requires_action"}
    pending = {"id": "cs_setup_2", "mode": "setup", "setup_intent": "seti_2"}
    assert ok(stripe_report(browser, "checkout.session.completed", pending)) == {"recorded": False}
    answers[("GET", "/setup_intents/seti_3")] = answers[("GET", "/setup_intents/seti_1")] | {
        "metadata": {"autopay_id": str(uuid4())}
    }
    stranger = {"id": "seti_3", "metadata": {"autopay_id": "x"}}
    assert ok(stripe_report(browser, "setup_intent.succeeded", stranger)) == {"recorded": False}
