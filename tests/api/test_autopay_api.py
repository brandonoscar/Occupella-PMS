"""Autopay through the API (src/pms/api/autopay.py, migration 20261022000159): a tenant saves a
card or account with the processor for a lease (the fake one here; Stripe's answers made here,
NOT VERIFIED against Stripe itself), and Run rent now or the nightly run charges what the lease
owes once its rent is due, once per due date, recorded once the processor says the money came;
a lease owing nothing or on a payment hold is charged nothing; the tenant or staff stop it.
Secrets are made up for each test."""

import dataclasses
import hashlib
import hmac
import io
import json
import secrets
import time
import uuid
from datetime import UTC, date, datetime

import pytest
from fastapi.testclient import TestClient
from helpers import add_unit, charge, open_lease
from people import join_as_owner, join_as_staff, join_as_tenant, tenant_of

from pms import nightly
from pms.api import autopay as autopay_api
from pms.api import create_app
from pms.payments import processor

JAN_1 = date(2026, 1, 1)
WEBHOOK = "/api/payments/stripe/webhook"


@pytest.fixture
def settings(settings):
    """The app with the fake processor (the conftest's settings otherwise)."""
    return dataclasses.replace(settings, payment_processor="fake")


@pytest.fixture
def lease(conn, pmc):
    """The PMC's tenant rents Unit 1 from January at 1500, due on the first."""
    unit = add_unit(conn, pmc.pmc_id, pmc.owners[0].property_id, "Unit 1")
    return open_lease(conn, pmc.pmc_id, unit, JAN_1, None, "1500.00", [tenant_of(conn, pmc)])


def takes(conn, pmc):
    conn.execute(
        "INSERT INTO trust_online_payment_settings (pmc_id, cards, bank_debits)"
        " VALUES (%s, true, true)",
        (pmc.pmc_id,),
    )


def first_of_this_month():
    return datetime.now(UTC).date().replace(day=1)


def owes_this_month(conn, pmc, lease, amount="1500.00", kind="rent"):
    charge(conn, pmc.pmc_id, lease, first_of_this_month(), amount, kind)


def set_up(client, lease, method="card", outcome="saved"):
    started = client.post(f"/api/tenant/leases/{lease}/autopay", json={"method": method})
    assert started.status_code == 201, started.text
    reference = started.json()["url"].removeprefix("/pay#")
    said = client.post(f"/api/tenant/autopay/fake/{reference}", json={"outcome": outcome})
    assert said.status_code == 204, said.text
    return started.json()["autopay_id"]


def mine(client):
    return [(a["status"], a["method"], a["label"], a["reason"]) for a in
            client.get("/api/tenant/autopay").json()["autopay"]]  # fmt: skip


def balance(client, lease):
    (found,) = [x for x in client.get("/api/tenant/leases").json() if x["lease_id"] == str(lease)]
    return found["balance_due"], [(p["amount"], p["method"]) for p in found["payments"]]


def run_rent(staff_client):
    answer = staff_client.post(
        "/api/rent/run",
        json={"due_on": first_of_this_month().isoformat(), "late_fees_as_of": JAN_1.isoformat()},
    )
    assert answer.status_code == 200, answer.text
    return answer.json()["autopay"]


@pytest.fixture
def people(client, other_client, conn, app_conn, pmc, lease):
    """The tenant on the portal (client) and an admin (other_client); the company takes both."""
    takes(conn, pmc)
    join_as_tenant(client, conn, app_conn, pmc)
    join_as_staff(other_client, conn, app_conn, pmc)
    return client, other_client


def test_autopay_pays_what_the_lease_owes_once_rent_is_due_once_per_due_date(
    people, conn, pmc, lease
):
    tenant, staff = people
    assert client_methods(tenant) == ["card", "bank debit"]
    set_up(tenant, lease, "card")
    assert mine(tenant) == [("on", "card", "Card ••••4242", None)]
    owes_this_month(conn, pmc, lease)

    assert run_rent(staff) == 1
    assert balance(tenant, lease) == ("0.00", [("1500.00", "card")])
    # Once per due date: the same month again charges nothing.
    owes_this_month(conn, pmc, lease, "25.00", "fee")
    assert run_rent(staff) == 0
    assert balance(tenant, lease)[0] == "25.00"
    payments = tenant.get("/api/tenant/online-payments").json()["payments"]
    assert [(p["amount"], p["status"]) for p in payments] == [("1500.00", "succeeded")]


def client_methods(tenant):
    return tenant.get("/api/tenant/autopay").json()["methods"]


def test_nothing_is_charged_owing_nothing_on_a_hold_or_once_stopped(
    people, conn, app_conn, pmc, lease
):
    tenant, staff = people
    autopay = set_up(tenant, lease, "bank debit")
    assert mine(tenant)[0][2] == "Bank account ••••6789"
    today = datetime.now(UTC).date()

    # Owing nothing (no rent charged yet), nothing is charged.
    assert autopay_api.charge_due(app_conn, pmc.pmc_id, today, processor.FakeProcessor()) == 0
    conn.execute(
        "INSERT INTO trust_payment_holds (pmc_id, lease_id, starts_on, reason)"
        " VALUES (%s, %s, %s, 'Eviction filed')",
        (pmc.pmc_id, lease, JAN_1),
    )
    assert run_rent(staff) == 0  # this month's rent is charged, but the lease is on a hold
    assert balance(tenant, lease)[0] == "1500.00"

    stopped = tenant.post(f"/api/tenant/autopay/{autopay}/stop", json={})
    again = tenant.post(f"/api/tenant/autopay/{autopay}/stop", json={})
    assert (stopped.status_code, again.status_code) == (204, 409)
    assert mine(tenant) == [
        ("stopped", "bank debit", "Bank account ••••6789", "the tenant stopped it")
    ]


def test_one_autopay_a_lease_and_staff_stop_it_with_why(people, conn, pmc, lease):
    tenant, staff = people
    autopay = set_up(tenant, lease)
    second = tenant.post(f"/api/tenant/leases/{lease}/autopay", json={"method": "card"})
    assert (second.status_code, second.json()["detail"]) == (
        409,
        "this lease is on autopay already: stop it first",
    )

    listed = staff.get("/api/rent/autopay").json()
    stopped = staff.post(f"/api/rent/autopay/{autopay}/stop", json={"reason": "Card expired"})
    blank = staff.post(f"/api/rent/autopay/{autopay}/stop", json={"reason": " "})

    assert [(a["status"], a["label"]) for a in listed] == [("on", "Card ••••4242")]
    assert stopped.status_code == 204
    assert blank.status_code == 422
    assert mine(tenant)[0][0::3] == ("stopped", "Card expired")
    # Stopped, a new one may be set up.
    set_up(tenant, lease, "bank debit")
    assert [a[0] for a in mine(tenant)] == ["on", "stopped"]


def test_a_card_not_saved_is_kept_with_why_and_charges_nothing(people, conn, pmc, lease):
    tenant, staff = people
    set_up(tenant, lease, "card", outcome="not saved")
    owes_this_month(conn, pmc, lease)

    assert mine(tenant) == [("failed", "card", None, "you said it wasn't saved")]
    assert run_rent(staff) == 0


def test_the_nightly_run_charges_autopay_with_the_servers_processor(
    people, conn, app_conn, pmc, lease
):
    tenant, _ = people
    set_up(tenant, lease)
    owes_this_month(conn, pmc, lease)

    without = nightly.run_pmc(app_conn, pmc.pmc_id, datetime.now(UTC).date(), "http://pms.test")
    ran = nightly.run_pmc(
        app_conn, pmc.pmc_id, datetime.now(UTC).date(), "http://pms.test",
        payments=processor.FakeProcessor(),
    )  # fmt: skip

    assert (without.autopay, ran.autopay) == (0, 1)
    assert balance(tenant, lease)[0] == "0.00"


@pytest.mark.parametrize(
    ("body", "status"),
    [({"method": "cash"}, 422), ({}, 422), ({"method": "bank debit"}, 409)],
)
def test_bad_autopay_is_refused(client, conn, app_conn, pmc, lease, body, status):
    conn.execute(
        "INSERT INTO trust_online_payment_settings (pmc_id, cards, bank_debits)"
        " VALUES (%s, true, false)",
        (pmc.pmc_id,),
    )
    join_as_tenant(client, conn, app_conn, pmc)

    answer = client.post(f"/api/tenant/leases/{lease}/autopay", json=body)

    assert answer.status_code == status, answer.text
    assert mine(client) == []


def test_another_tenants_lease_or_autopay_is_not_found(people, conn, pmc, lease):
    tenant, staff = people
    some = uuid.uuid4()
    for answer in (
        tenant.post(f"/api/tenant/leases/{some}/autopay", json={"method": "card"}),
        tenant.post(f"/api/tenant/autopay/{some}/stop", json={}),
        tenant.post("/api/tenant/autopay/fake/fake_setup_x", json={"outcome": "saved"}),
        staff.post(f"/api/rent/autopay/{some}/stop", json={"reason": "x"}),
    ):
        assert answer.status_code == 404


@pytest.mark.parametrize("caller", ["nobody", "owner", "staff", "tenant"])
def test_each_side_reaches_its_own(client, conn, app_conn, pmc, lease, caller):
    if caller == "owner":
        join_as_owner(client, app_conn, pmc)
    elif caller == "staff":
        join_as_staff(client, conn, app_conn, pmc)
    elif caller == "tenant":
        join_as_tenant(client, conn, app_conn, pmc)
    refused = 401 if caller == "nobody" else 403
    some = uuid.uuid4()
    staff_side = [
        client.get("/api/rent/autopay").status_code,
        client.post(f"/api/rent/autopay/{some}/stop", json={"reason": "x"}).status_code,
    ]
    tenant_side = [
        client.get("/api/tenant/autopay").status_code,
        client.post(f"/api/tenant/leases/{lease}/autopay", json={"method": "card"}).status_code,
        client.post(f"/api/tenant/autopay/{some}/stop", json={}).status_code,
        client.post("/api/tenant/autopay/fake/x", json={"outcome": "saved"}).status_code,
    ]
    assert staff_side == ([200, 404] if caller == "staff" else [refused] * 2)
    if caller != "tenant":
        assert tenant_side == [refused] * 4


def sign(body: bytes, secret: str) -> dict[str, str]:
    at = int(time.time())
    mac = hmac.new(secret.encode(), f"{at}.".encode() + body, hashlib.sha256).hexdigest()
    return {"stripe-signature": f"t={at},v1={mac}", "content-type": "application/json"}


def test_stripe_saves_a_card_by_webhook_and_charges_it_off_the_session(
    settings, monkeypatch, conn, app_conn, pmc, lease
):
    secret = secrets.token_hex(16)
    monkeypatch.setenv("PMS_STRIPE_SECRET_KEY", "made-up-key")
    monkeypatch.setenv("PMS_STRIPE_WEBHOOK_SECRET", secret)
    asked = []

    def urlopen(request, timeout):
        path = request.full_url.removeprefix(processor.STRIPE_API)
        asked.append(path)
        answers = {
            "/customers": {"id": "cus_1"},
            "/checkout/sessions": {"id": "cs_1", "url": "https://checkout.example/cs_1"},
            "/setup_intents/seti_1?expand[]=payment_method": {
                "payment_method": {"id": "pm_1", "card": {"last4": "4242"}}
            },
            "/payment_intents": {"id": "pi_1"},
        }
        return io.BytesIO(json.dumps(answers[path]).encode())

    monkeypatch.setattr(processor.urllib.request, "urlopen", urlopen)
    takes(conn, pmc)
    with TestClient(
        create_app(dataclasses.replace(settings, payment_processor="stripe"))
    ) as client:
        join_as_tenant(client, conn, app_conn, pmc)
        started = client.post(f"/api/tenant/leases/{lease}/autopay", json={"method": "card"})
        assert started.json()["url"] == "https://checkout.example/cs_1"
        saved = json.dumps({
            "id": "evt_saved", "type": "checkout.session.completed",
            "data": {"object": {"id": "cs_1", "mode": "setup", "customer": "cus_1",
                                "setup_intent": "seti_1"}},
        }).encode()  # fmt: skip
        hook = client.post(
            "/api/payments/stripe/webhook", content=saved, headers=sign(saved, secret)
        )
        assert hook.json() == {"taken": 1}
        assert mine(client) == [("on", "card", "Card ••••4242", None)]

        owes_this_month(conn, pmc, lease)
        ran = nightly.run_pmc(
            app_conn, pmc.pmc_id, datetime.now(UTC).date(), "http://pms.test",
            payments=client.app.state.payments,
        )  # fmt: skip
        assert ran.autopay == 1
        assert balance(client, lease)[0] == "1500.00"  # nothing recorded until Stripe says so
        paid = json.dumps({
            "id": "evt_paid", "type": "payment_intent.succeeded",
            "data": {"object": {"id": "pi_1"}},
        }).encode()  # fmt: skip
        hook = client.post(WEBHOOK, content=paid, headers=sign(paid, secret))
        assert hook.json() == {"taken": 1}
        assert balance(client, lease) == ("0.00", [("1500.00", "card")])
    assert asked == [
        "/customers",
        "/checkout/sessions",
        "/setup_intents/seti_1?expand[]=payment_method",
        "/payment_intents",
    ]
