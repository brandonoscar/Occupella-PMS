"""Rent paid online through the API (src/pms/api/online_payments.py): a company turns it on and
chooses who pays the partner's fee; a tenant pays a lease from the portal on the partner's page;
the partner's report records it as staff would (the payment, the tenant's fee into the company's
account, the partner's fee out of trust from it), once however often it is sent; a return is a
bounce; what the books can't take yet waits for staff."""

import hashlib
import hmac
import json
import time
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import psycopg
import pytest
from fastapi.testclient import TestClient
from helpers import (
    add_tenant,
    add_unit,
    approve_bank,
    balance,
    charge,
    import_statement,
    open_lease,
    staff_named,
)
from people import join_as_staff, join_as_tenant, tenant_of

from pms import email, payments
from pms.api import Settings, create_app
from pms.api import online_payments as op

TODAY = datetime.now(UTC).date()
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
    """A lease of the PMC's first owner's property, its rent of 1,200.00 due on the first."""
    unit = add_unit(conn, pmc.pmc_id, pmc.owners[0].property_id)
    lease = open_lease(
        conn, pmc.pmc_id, unit, TODAY - 60 * DAY, None, "1200.00", [tenant_of(conn, pmc)]
    )
    charge(conn, pmc.pmc_id, lease, TODAY - 10 * DAY, "1200.00")
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


def pay(tenant_client, lease, amount="1200.00"):
    return ok(
        tenant_client.post(f"/api/payments/leases/{lease}/checkout", json={"amount": amount}), 201
    )


def bank_says(client, payment, outcome):
    return ok(client.post(f"/api/payments/fake/{payment}", json={"outcome": outcome}))


def keys(conn, pmc):
    return {
        key
        for (key,) in conn.execute(
            "SELECT idempotency_key FROM trust_idempotency_keys WHERE pmc_id = %s", (pmc.pmc_id,)
        ).fetchall()
    }


# --- the company's choices ----------------------------------------------------------------


def test_staff_turn_online_payments_on_and_choose_who_pays_the_fee(client, conn, staff, pmc):
    assert ok(client.get("/api/payments/settings")) == {
        "online": False,
        "ach_fee_payer": "company",
        "partner": "fake",
        "changed_at": None,
    }
    turn_on(client, "tenant")
    turn_on(client, "tenant")  # the same again writes nothing
    shown = ok(client.get("/api/payments/settings"))
    assert (shown["online"], shown["ach_fee_payer"]) == (True, "tenant")
    rows = conn.execute(
        "SELECT count(*), max(recorded_by::text) FROM trust_payment_settings WHERE pmc_id = %s",
        (pmc.pmc_id,),
    ).fetchone()
    assert rows == (1, str(staff.person_id))


def test_only_staff_choose_and_only_what_there_is(client, tenant_client, staff, tenant):
    refused(tenant_client.get("/api/payments/settings"), 403)
    refused(
        tenant_client.post(
            "/api/payments/settings", json={"online": True, "ach_fee_payer": "company"}
        ),
        403,
    )
    refused(
        client.post("/api/payments/settings", json={"online": True, "ach_fee_payer": "owner"}), 422
    )
    refused(client.post("/api/payments/settings", json={"online": "maybe"}), 422)


def test_signed_out_nobody_sees_or_changes_anything(other_client, lease):
    refused(other_client.get("/api/payments/settings"), 401)
    refused(other_client.get(f"/api/payments/leases/{lease}"), 401)
    refused(
        other_client.post(f"/api/payments/leases/{lease}/checkout", json={"amount": "1.00"}), 401
    )
    refused(other_client.get("/api/payments/waiting"), 401)


# --- a tenant pays ------------------------------------------------------------------------


def test_a_tenant_pays_online_only_once_the_company_takes_it(
    client, tenant_client, staff, tenant, lease
):
    view = ok(tenant_client.get(f"/api/payments/leases/{lease}"))
    assert (view["available"], view["why_not"]) == (
        False,
        "your manager doesn't take payments online",
    )
    assert view["owed"] == "1200.00"
    refused(
        tenant_client.post(f"/api/payments/leases/{lease}/checkout", json={"amount": "100.00"}),
        409,
        "your manager doesn't take payments online",
    )
    turn_on(client)
    assert ok(tenant_client.get(f"/api/payments/leases/{lease}"))["available"] is True
    started = pay(tenant_client, lease)
    assert started["url"] == f"http://testserver/pay/fake#{started['id']}"
    view = ok(tenant_client.get(f"/api/payments/leases/{lease}"))
    assert [(p["amount"], p["tenant_fee"], p["status"]) for p in view["payments"]] == [
        ("1200.00", "0.00", "started")
    ]


def test_the_tenant_sees_their_fee_before_paying_when_they_pay_it(
    client, tenant_client, staff, tenant, lease
):
    turn_on(client, "company")
    assert (
        ok(tenant_client.get(f"/api/payments/leases/{lease}/fee?amount=1000.00"))["tenant_fee"]
        == "0.00"
    )
    turn_on(client, "tenant")
    quote = ok(tenant_client.get(f"/api/payments/leases/{lease}/fee?amount=500.00"))
    assert quote == {"amount": "500.00", "tenant_fee": "4.03", "total": "504.03"}
    # The partner's fee stops at 5.00, and so does the tenant's.
    assert (
        ok(tenant_client.get(f"/api/payments/leases/{lease}/fee?amount=1000.00"))["tenant_fee"]
        == "5.00"
    )
    refused(tenant_client.get(f"/api/payments/leases/{lease}/fee?amount=0"), 422)
    refused(tenant_client.get(f"/api/payments/leases/{lease}/fee?amount=1.005"), 422)


def test_a_tenant_pays_only_a_lease_they_are_on_and_a_sane_amount(
    client, tenant_client, conn, staff, tenant, lease, pmc
):
    turn_on(client)
    place = pmc.owners[1].property_id
    neighbour = add_tenant(conn, pmc.pmc_id, place, "Other Tenant")
    other = open_lease(
        conn,
        pmc.pmc_id,
        add_unit(conn, pmc.pmc_id, place),
        TODAY - DAY,
        None,
        "900.00",
        [neighbour],
    )
    refused(
        tenant_client.post(f"/api/payments/leases/{other}/checkout", json={"amount": "1.00"}), 404
    )
    refused(tenant_client.get(f"/api/payments/leases/{other}"), 404)
    refused(client.post(f"/api/payments/leases/{lease}/checkout", json={"amount": "1.00"}), 403)
    for amount in ["0", "-5.00", "100000.01", "12.345", "lots"]:
        refused(
            tenant_client.post(f"/api/payments/leases/{lease}/checkout", json={"amount": amount}),
            422,
        )


def test_a_lease_on_a_payment_hold_takes_no_payment_online(
    client, tenant_client, conn, staff, tenant, lease, pmc
):
    turn_on(client)
    conn.execute(
        "INSERT INTO trust_payment_holds (pmc_id, lease_id, starts_on, reason)"
        " VALUES (%s, %s, %s, 'filed')",
        (pmc.pmc_id, lease, TODAY - DAY),
    )
    assert ok(tenant_client.get(f"/api/payments/leases/{lease}"))["why_not"] == (
        "this lease takes no payment online now: ask your manager"
    )
    refused(
        tenant_client.post(f"/api/payments/leases/{lease}/checkout", json={"amount": "1.00"}), 409
    )


# --- the partner reports ------------------------------------------------------------------


def test_paid_is_recorded_as_staff_record_a_payment_and_only_once(
    client, tenant_client, conn, staff, tenant, lease, pmc
):
    turn_on(client, "tenant")
    started = pay(tenant_client, lease, "1000.00")
    payment = started["id"]
    owner_before = balance(conn, pmc.owners[0].account)
    assert ok(tenant_client.get(f"/api/payments/fake/{payment}"))["total"] == "1005.00"
    assert bank_says(tenant_client, payment, "paid") == {"recorded": True}
    assert bank_says(tenant_client, payment, "paid") == {"recorded": True}  # sent again
    assert balance(conn, pmc.owners[0].account) - owner_before == Decimal("1000.00")
    # The tenant's 5.00 into the company's account, the partner's 5.00 (its cap) out.
    assert balance(conn, pmc.pmc_income) == Decimal("0.00")
    assert {f"payment/{payment}", f"online-fee/{payment}", f"partner-fee/{payment}"} <= keys(
        conn, pmc
    )
    assert (
        conn.execute(
            "SELECT count(*) FROM trust_online_payment_events WHERE payment_id = %s", (payment,)
        ).fetchone()[0]
        == 1
    )
    view = ok(tenant_client.get(f"/api/payments/leases/{lease}"))
    assert view["owed"] == "200.00"
    assert view["payments"][0]["status"] == "paid"
    # The lease's payments, as the tenant and staff read them, say it was paid online.
    leases = ok(tenant_client.get("/api/tenant/leases"))
    (mine,) = [x for x in leases if x["lease_id"] == str(lease)]
    assert [(p["amount"], p["method"]) for p in mine["payments"]] == [("1000.00", "online")]
    staff_view = ok(client.get(f"/api/leases/{lease}"))
    assert [(p["amount"], p["method"]) for p in staff_view["payments"]] == [("1000.00", "online")]
    # A failure after it was paid changes nothing.
    assert bank_says(tenant_client, payment, "failed") == {"recorded": True}
    assert ok(tenant_client.get(f"/api/payments/leases/{lease}"))["payments"][0]["status"] == "paid"
    assert ok(client.get("/api/payments/waiting")) == []


def test_a_payment_made_online_emails_its_receipt_once(
    client, tenant_client, conn, staff, tenant, lease, pmc
):
    # ADR 0010 phase 1c: every payment's receipt, online ones too, while the server sends email.
    turn_on(client)
    tenant_client.app.state.email = email.FakeSender()
    payment = pay(tenant_client, lease, "1000.00")["id"]

    bank_says(tenant_client, payment, "paid")
    bank_says(tenant_client, payment, "paid")  # sent again: no second receipt

    (transfer,) = conn.execute(
        "SELECT id FROM pgledger_transfers WHERE metadata ->> 'key' = %s ORDER BY id",
        (f"payment/{payment}",),
    ).fetchall()
    assert conn.execute(
        "SELECT kind, lease_id, transfer_id FROM trust_emails WHERE pmc_id = %s",
        (pmc.pmc_id,),
    ).fetchall() == [("payment_receipt", lease, transfer[0])]


def test_a_failed_payment_moves_nothing(client, tenant_client, conn, staff, tenant, lease, pmc):
    turn_on(client)
    payment = pay(tenant_client, lease)["id"]
    before = keys(conn, pmc)
    bank_says(tenant_client, payment, "failed")
    assert keys(conn, pmc) == before
    assert (
        ok(tenant_client.get(f"/api/payments/leases/{lease}"))["payments"][0]["status"] == "failed"
    )
    refused(tenant_client.post(f"/api/payments/fake/{payment}", json={"outcome": "lost"}), 422)


def test_a_payment_the_tenant_left_unpaid_reads_not_finished_and_moves_nothing(
    client, tenant_client, conn, staff, tenant, lease, pmc
):
    turn_on(client)
    left = pay(tenant_client, lease)["id"]
    before = keys(conn, pmc)
    assert bank_says(tenant_client, left, "expired") == {"recorded": True}
    assert bank_says(tenant_client, left, "expired") == {"recorded": True}  # sent again
    assert keys(conn, pmc) == before
    statuses = [
        p["status"] for p in ok(tenant_client.get(f"/api/payments/leases/{lease}"))["payments"]
    ]
    assert statuses == ["expired"]
    # A report that the bank paid counts even after one that the page expired: the money moved.
    assert bank_says(tenant_client, left, "paid") == {"recorded": True}
    assert f"payment/{left}" in keys(conn, pmc)
    assert ok(tenant_client.get(f"/api/payments/leases/{lease}"))["payments"][0]["status"] == "paid"
    # One that reached the bank doesn't expire after.
    failed = pay(tenant_client, lease)["id"]
    bank_says(tenant_client, failed, "failed")
    assert bank_says(tenant_client, failed, "expired") == {"recorded": True}
    kinds = conn.execute(
        "SELECT array_agg(kind) FROM trust_online_payment_events WHERE payment_id = %s", (failed,)
    ).fetchone()[0]
    assert kinds == ["failed"]


def test_the_company_covers_the_partners_fee_its_account_cant(
    client, tenant_client, conn, staff, tenant, lease, pmc
):
    turn_on(client, "company")
    payment = pay(tenant_client, lease, "500.00")["id"]
    bank_says(tenant_client, payment, "paid")
    assert f"payment/{payment}" in keys(conn, pmc)
    waiting = ok(client.get("/api/payments/waiting"))
    assert [(w["what"], w["company_owes"], w["company_holds"]) for w in waiting] == [
        ("partner fee", "4.00", "0.00")
    ]
    bank = waiting[0]["bank_account_id"]
    cover = {"bank_account_id": bank, "amount": "4.01", "request_id": str(uuid4())}
    refused(
        client.post("/api/payments/partner-fees/cover", json=cover),
        422,
        "what waits here needs 4.00 from the company, no more",
    )
    refused(
        tenant_client.post("/api/payments/partner-fees/cover", json=cover | {"amount": "4.00"}), 403
    )
    assert ok(client.post("/api/payments/partner-fees/cover", json=cover | {"amount": "4.00"})) == {
        "still_waiting": 0
    }
    # Sent again it deposits nothing more; the same request id for another amount is refused.
    assert ok(client.post("/api/payments/partner-fees/cover", json=cover | {"amount": "4.00"})) == {
        "still_waiting": 0
    }
    refused(
        client.post("/api/payments/partner-fees/cover", json=cover | {"amount": "3.00"}),
        409,
        "this request id was used for something else",
    )
    assert ok(client.get("/api/payments/waiting")) == []
    assert balance(conn, pmc.pmc_income) == Decimal("0.00")


def test_a_return_is_a_bounce_and_the_tenants_fee_goes_back_out(
    client, tenant_client, conn, staff, tenant, lease, pmc
):
    turn_on(client, "tenant")
    payment = pay(tenant_client, lease, "1000.00")["id"]
    bank_says(tenant_client, payment, "returned")  # not paid yet: nothing came back
    assert f"bounce/payment/{payment}" not in keys(conn, pmc)
    owner_before = balance(conn, pmc.owners[0].account)
    bank_says(tenant_client, payment, "paid")
    bank_says(tenant_client, payment, "returned")
    assert balance(conn, pmc.owners[0].account) == owner_before
    assert f"bounce/payment/{payment}" in keys(conn, pmc)
    # The tenant's 5.00 came back out of the company's account, which held nothing: it waits.
    waiting = ok(client.get("/api/payments/waiting"))
    assert [(w["what"], w["company_owes"]) for w in waiting] == [("fee refund", "5.00")]
    ok(
        client.post(
            "/api/payments/partner-fees/cover",
            json={
                "bank_account_id": waiting[0]["bank_account_id"],
                "amount": "5.00",
                "request_id": str(uuid4()),
            },
        )
    )
    assert ok(client.get("/api/payments/waiting")) == []
    assert ok(tenant_client.get(f"/api/payments/leases/{lease}"))["owed"] == "1200.00"


def test_a_payment_paid_during_a_hold_waits_even_when_it_would_all_be_prepaid_rent(
    client, tenant_client, conn, staff, tenant, pmc
):
    # A lease that owes nothing: the payment would be prepaid rent, matched to no charge.
    unit = add_unit(conn, pmc.pmc_id, pmc.owners[0].property_id, "Unit 7")
    paid_up = open_lease(
        conn, pmc.pmc_id, unit, TODAY - 60 * DAY, None, "900.00", [tenant_of(conn, pmc)]
    )
    turn_on(client)
    payment = pay(tenant_client, paid_up, "300.00")["id"]
    conn.execute(
        "INSERT INTO trust_payment_holds (pmc_id, lease_id, starts_on, reason)"
        " VALUES (%s, %s, %s, 'filed')",
        (pmc.pmc_id, paid_up, TODAY),
    )
    assert bank_says(tenant_client, payment, "paid") == {"recorded": True}
    assert f"payment/{payment}" not in keys(conn, pmc)
    assert [w["what"] for w in ok(client.get("/api/payments/waiting"))] == ["payment"]
    ok(client.post(f"/api/payments/{payment}/record", json={"past_hold": True}), 204)
    assert f"payment/{payment}" in keys(conn, pmc)


def test_a_payment_paid_during_a_hold_waits_for_staff_to_accept_it(
    client, tenant_client, conn, staff, tenant, lease, pmc
):
    turn_on(client)
    payment = pay(tenant_client, lease, "300.00")["id"]
    conn.execute(
        "INSERT INTO trust_payment_holds (pmc_id, lease_id, starts_on, reason)"
        " VALUES (%s, %s, %s, 'filed')",
        (pmc.pmc_id, lease, TODAY - DAY),
    )
    bank_says(tenant_client, payment, "paid")
    assert f"payment/{payment}" not in keys(conn, pmc)
    assert [w["what"] for w in ok(client.get("/api/payments/waiting"))] == ["payment"]
    refused(client.post(f"/api/payments/{payment}/record", json={}), 409)
    refused(tenant_client.post(f"/api/payments/{payment}/record", json={"past_hold": True}), 403)
    refused(client.post(f"/api/payments/{uuid4()}/record", json={}), 404)
    ok(client.post(f"/api/payments/{payment}/record", json={"past_hold": True}), 204)
    assert f"payment/{payment}" in keys(conn, pmc)
    assert [w["what"] for w in ok(client.get("/api/payments/waiting"))] == ["partner fee"]


def test_a_partner_that_does_not_answer_starts_no_payment(
    client, tenant_client, conn, staff, tenant, lease, pmc, monkeypatch
):
    turn_on(client)

    def down(self, payment_id, lines, done_url):
        raise payments.PartnerFailed("down")

    monkeypatch.setattr(payments.FakePartner, "checkout", down)
    refused(
        tenant_client.post(f"/api/payments/leases/{lease}/checkout", json={"amount": "100.00"}),
        502,
        "the payments partner didn't answer: try again soon",
    )
    started = conn.execute(
        "SELECT count(*) FROM trust_online_payments WHERE lease_id = %s", (lease,)
    ).fetchone()
    assert started == (0,)


def test_a_payment_not_in_the_books_has_nothing_to_undo(
    client, tenant_client, conn, staff, tenant, lease, pmc
):
    turn_on(client)
    payment = pay(tenant_client, lease, "300.00")["id"]
    row = op.row_of(conn, payment)
    assert row is not None
    assert op.record_return(conn, row, TODAY) is False
    assert op.bounce_key(row.id) not in keys(conn, pmc)


def test_the_fake_partners_page_is_only_the_fake_partners(
    client, tenant_client, staff, tenant, lease
):
    refused(tenant_client.get(f"/api/payments/fake/{uuid4()}"), 404)
    refused(tenant_client.post(f"/api/payments/fake/{uuid4()}", json={"outcome": "paid"}), 404)
    refused(tenant_client.post("/api/payments/webhook/fake", json={}), 404)


# --- Stripe's webhook ---------------------------------------------------------------------


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


def stripe_report(browser, kind, subject, secret=SECRET, created=None):
    now = int(time.time())
    body = json.dumps(
        {
            "id": f"evt_{uuid4().hex}",
            "type": kind,
            "created": now if created is None else created,
            "data": {"object": subject},
        }
    ).encode()
    mac = hmac.new(secret.encode(), f"{now}.".encode() + body, hashlib.sha256).hexdigest()
    return browser.post(
        "/api/payments/webhook/stripe",
        content=body,
        headers={"stripe-signature": f"t={now},v1={mac}", "content-type": "application/json"},
    )


def test_stripes_signed_report_records_the_payment(stripe_app, conn, app_conn, pmc, lease):
    browser, tenant_browser, answers = stripe_app
    staff = join_as_staff(browser, conn, app_conn, pmc)
    turn_on(browser)
    answers[("POST", "/checkout/sessions")] = {
        "id": "cs_test_1",
        "url": "https://checkout.test/cs_1",
    }
    join_as_tenant(tenant_browser, conn, app_conn, pmc)
    started = pay(tenant_browser, lease, "1000.00")
    assert started["url"] == "https://checkout.test/cs_1"
    payment = started["id"]
    subject = {"id": "cs_test_1", "client_reference_id": payment, "payment_intent": "pi_1"}
    # Stripe can't say its fee yet: the report is answered "send it again" and records nothing.
    answers[("GET", "/payment_intents/pi_1")] = {"latest_charge": None}
    refused(
        stripe_report(browser, "checkout.session.async_payment_succeeded", subject),
        502,
        "the payments partner didn't answer: send it again",
    )
    assert f"payment/{payment}" not in keys(conn, pmc)
    answers[("GET", "/payment_intents/pi_1")] = {
        "latest_charge": {"balance_transaction": {"fee": 800}}
    }
    assert ok(stripe_report(browser, "checkout.session.async_payment_succeeded", subject)) == {
        "recorded": True
    }
    assert f"payment/{payment}" in keys(conn, pmc)
    # A forged report, one about a payment the PMS didn't start, one it doesn't read.
    refused(
        stripe_report(browser, "checkout.session.async_payment_failed", subject, "whsec_x"), 400
    )
    other = subject | {"client_reference_id": str(uuid4())}
    assert ok(stripe_report(browser, "checkout.session.async_payment_succeeded", other)) == {
        "recorded": False
    }
    assert ok(stripe_report(browser, "customer.created", {})) == {"recorded": False}
    refused(browser.post("/api/payments/webhook/paypal", json={}), 404)
    assert staff  # signed in as staff to turn it on


def test_a_payment_whose_day_month_end_closed_is_recorded_in_the_next_open_one(
    stripe_app, conn, app_conn, pmc, lease
):
    # Paid yesterday during a hold, then month-end closed yesterday: accepted past the hold now,
    # it goes into the books on the first day left open, as a late correction does.
    browser, tenant_browser, answers = stripe_app
    join_as_staff(browser, conn, app_conn, pmc)
    turn_on(browser)
    answers[("POST", "/checkout/sessions")] = {"id": "cs_test_2", "url": "https://checkout.test/2"}
    join_as_tenant(tenant_browser, conn, app_conn, pmc)
    payment = pay(tenant_browser, lease, "300.00")["id"]
    conn.execute(
        "INSERT INTO trust_payment_holds (pmc_id, lease_id, starts_on, reason)"
        " VALUES (%s, %s, %s, 'filed')",
        (pmc.pmc_id, lease, TODAY - 2 * DAY),
    )
    answers[("GET", "/payment_intents/pi_2")] = {
        "latest_charge": {"balance_transaction": {"fee": 240}}
    }
    yesterday = int(datetime.combine(TODAY - DAY, datetime.min.time(), UTC).timestamp()) + 43200
    subject = {"id": "cs_test_2", "client_reference_id": payment, "payment_intent": "pi_2"}
    paid = stripe_report(
        browser, "checkout.session.async_payment_succeeded", subject, created=yesterday
    )
    assert ok(paid) == {"recorded": True}
    assert f"payment/{payment}" not in keys(conn, pmc)  # it waits for the hold
    # The bank's statement through yesterday shows nothing yet; month-end closes that day.
    clerk = staff_named(conn, pmc.pmc_id, "Clerk 1")
    import_statement(
        conn, pmc.pmc_id, pmc.operating_bank_id, clerk, TODAY - 9 * DAY, TODAY - DAY, []
    )
    approve_bank(conn, pmc.pmc_id, pmc.operating_bank_id, TODAY - DAY)
    ok(browser.post(f"/api/payments/{payment}/record", json={"past_hold": True}), 204)
    dated = conn.execute(
        "SELECT (event_at AT TIME ZONE 'UTC')::date FROM pgledger_transfers"
        " WHERE metadata ->> 'key' = %s",
        (f"payment/{payment}",),
    ).fetchall()
    assert dated and {day for (day,) in dated} == {TODAY}


def test_a_refusal_from_the_books_reaches_staff_in_words(
    client, tenant_client, conn, staff, tenant, lease, pmc, monkeypatch
):
    turn_on(client)
    payment = pay(tenant_client, lease, "300.00")["id"]
    conn.execute(
        "INSERT INTO trust_payment_holds (pmc_id, lease_id, starts_on, reason)"
        " VALUES (%s, %s, %s, 'filed')",
        (pmc.pmc_id, lease, TODAY - DAY),
    )
    bank_says(tenant_client, payment, "paid")

    def refused_by_the_books(*_, **__):
        raise psycopg.errors.CheckViolation("trust: the books refuse this payment")

    monkeypatch.setattr(op, "record_payment", refused_by_the_books)
    refused(
        client.post(f"/api/payments/{payment}/record", json={"past_hold": True}),
        409,
        "the books refuse this payment",
    )
    assert f"payment/{payment}" not in keys(conn, pmc)


def test_a_server_without_a_partner_takes_no_payment_online(
    conn, app_conn, pmc, lease, database_url
):
    bare = Settings(database_url=database_url, public_url="http://testserver", cookie_secure=False)
    with TestClient(create_app(bare)) as browser, TestClient(create_app(bare)) as tenant_browser:
        join_as_staff(browser, conn, app_conn, pmc)
        turn_on(browser)
        assert ok(browser.get("/api/payments/settings"))["partner"] is None
        join_as_tenant(tenant_browser, conn, app_conn, pmc)
        assert ok(tenant_browser.get(f"/api/payments/leases/{lease}"))["why_not"] == (
            "this server takes no payments online"
        )
        refused(
            tenant_browser.post(f"/api/payments/leases/{lease}/checkout", json={"amount": "1.00"}),
            409,
            "this server takes no payments online",
        )
        refused(browser.post("/api/payments/webhook/stripe", json={}), 404)
