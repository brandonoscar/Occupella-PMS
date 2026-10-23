"""Rent paid online through the API (src/pms/api/online_payments.py, migration
20261022000158): a tenant pays what the lease owes through the server's processor (the fake one
here, and Stripe with its answers made here: NOT VERIFIED against Stripe itself), and once the
processor says the money came it is recorded as staff would record it; a failure records nothing,
a lease on a payment hold refuses it for staff to refund, a dispute lost bounces it, and each
processor event is taken once. Secrets are made up for each test."""

import dataclasses
import hashlib
import hmac
import io
import json
import secrets
import time
import uuid
from datetime import date

import pytest
from fastapi.testclient import TestClient
from helpers import add_unit, charge, make_pmc, open_lease
from people import join_as_owner, join_as_staff, join_as_tenant, tenant_of

from pms.api import create_app
from pms.payments import processor

JAN_1, FEB_1 = date(2026, 1, 1), date(2026, 2, 1)


@pytest.fixture
def settings(settings):
    """The app with the fake processor (the conftest's settings otherwise)."""
    return dataclasses.replace(settings, payment_processor="fake")


@pytest.fixture
def lease(conn, pmc):
    """The PMC's tenant rents Unit 1 from January at 1500; February's rent is open."""
    unit = add_unit(conn, pmc.pmc_id, pmc.owners[0].property_id, "Unit 1")
    lease_id = open_lease(conn, pmc.pmc_id, unit, JAN_1, None, "1500.00", [tenant_of(conn, pmc)])
    charge(conn, pmc.pmc_id, lease_id, FEB_1, "1500.00")
    return lease_id


def takes(conn, pmc, cards=True, bank_debits=True):
    conn.execute(
        "INSERT INTO trust_online_payment_settings (pmc_id, cards, bank_debits)"
        " VALUES (%s, %s, %s)",
        (pmc.pmc_id, cards, bank_debits),
    )


@pytest.fixture
def tenant(client, conn, app_conn, pmc, lease):
    takes(conn, pmc)
    return join_as_tenant(client, conn, app_conn, pmc)


def owed(client, lease):
    (found,) = [x for x in client.get("/api/tenant/leases").json() if x["lease_id"] == str(lease)]
    return found["balance_due"], found["payments"]


def start(client, lease, amount="1500.00", method="card"):
    return client.post(
        f"/api/tenant/leases/{lease}/online-payments", json={"amount": amount, "method": method}
    )


def statuses(client):
    return [(p["status"], p["amount"], p["method"], p["reason"]) for p in
            client.get("/api/tenant/online-payments").json()["payments"]]  # fmt: skip


def test_a_tenant_pays_by_card_and_it_is_recorded_once_the_processor_says_it_came(
    client, conn, pmc, lease, tenant
):
    assert client.get("/api/tenant/online-payments").json()["methods"] == ["card", "bank debit"]

    started = start(client, lease)
    reference = started.json()["url"].removeprefix("/pay#")
    assert started.status_code == 201, started.text
    assert started.json()["url"].startswith("/pay#fake_")
    # Nothing is recorded until the processor says the money came.
    assert owed(client, lease)[0] == "1500.00"
    assert statuses(client) == [("started", "1500.00", "card", None)]

    paid = client.post(
        f"/api/tenant/online-payments/fake/{reference}", json={"outcome": "succeeded"}
    )
    again = client.post(
        f"/api/tenant/online-payments/fake/{reference}", json={"outcome": "succeeded"}
    )

    assert (paid.status_code, again.status_code) == (204, 409)
    balance, payments = owed(client, lease)
    assert balance == "0.00"
    assert [(p["amount"], p["method"]) for p in payments] == [("1500.00", "card")]
    assert statuses(client) == [("succeeded", "1500.00", "card", None)]
    matched = conn.execute(
        "SELECT sum(cp.amount) FROM trust_charge_payments AS cp"
        " JOIN trust_charges AS c ON c.id = cp.charge_id WHERE c.lease_id = %s",
        (lease,),
    ).fetchone()[0]
    assert matched == 1500
    receipt = client.get(f"/api/tenant/payments/{payments[0]['transfer_id']}/receipt")
    assert receipt.status_code == 200


def test_a_failure_or_a_lease_on_hold_records_nothing(client, conn, pmc, lease, tenant):
    failed = start(client, lease, "100.00", "bank debit").json()["url"].removeprefix("/pay#")
    held = start(client, lease, "200.00").json()["url"].removeprefix("/pay#")
    conn.execute(
        "INSERT INTO trust_payment_holds (pmc_id, lease_id, starts_on, reason)"
        " VALUES (%s, %s, %s, 'Eviction filed')",
        (pmc.pmc_id, lease, FEB_1),
    )

    client.post(f"/api/tenant/online-payments/fake/{failed}", json={"outcome": "failed"})
    client.post(f"/api/tenant/online-payments/fake/{held}", json={"outcome": "succeeded"})

    assert owed(client, lease)[0] == "1500.00"
    (refused, gone) = statuses(client)
    assert gone == ("failed", "100.00", "bank debit", "you said it didn't go through")
    assert refused[:3] == ("refused", "200.00", "card")
    assert refused[3].endswith("refund it at the payment processor")
    # A lease on a hold takes no new payment online.
    blocked = start(client, lease, "10.00")
    assert blocked.status_code == 409
    assert blocked.json()["detail"] == "this lease takes no payment online now: call the office"


@pytest.mark.parametrize(
    ("amount", "method", "status", "detail"),
    [
        ("1500.01", "card", 422, "pay at most what the lease owes now, $1,500.00"),
        ("0", "card", 422, None),
        ("10.001", "card", 422, None),
        ("10.00", "cash", 422, None),
        ("10.00", "bank debit", 409, "takes no bank debit online"),
    ],
)
def test_bad_payments_are_refused_and_start_nothing(
    client, conn, app_conn, pmc, lease, amount, method, status, detail
):
    takes(conn, pmc, bank_debits=False)
    join_as_tenant(client, conn, app_conn, pmc)

    answer = start(client, lease, amount, method)

    assert answer.status_code == status, answer.text
    if detail is not None:
        assert answer.json()["detail"].endswith(detail)
    assert statuses(client) == []


def test_another_lease_or_payment_is_not_theirs(client, other_client, conn, app_conn, pmc, tenant):
    other = make_pmc(conn)
    unit = add_unit(conn, other.pmc_id, other.owners[0].property_id, "Unit 9")
    theirs = open_lease(conn, other.pmc_id, unit, JAN_1, None, "900.00", [tenant_of(conn, other)])

    assert start(client, theirs).status_code == 404
    assert start(client, uuid.uuid4()).status_code == 404
    missing = client.post("/api/tenant/online-payments/fake/fake_none", json={"outcome": "failed"})
    assert missing.status_code == 404


def test_without_a_processor_nothing_is_offered(conn, app_conn, pmc, lease, settings):
    takes(conn, pmc)
    with TestClient(create_app(dataclasses.replace(settings, payment_processor=""))) as plain:
        join_as_tenant(plain, conn, app_conn, pmc)
        assert plain.get("/api/tenant/online-payments").json()["methods"] == []
        assert start(plain, lease).status_code == 409
        fake = plain.post("/api/tenant/online-payments/fake/x", json={"outcome": "failed"})
        assert fake.status_code == 404


def test_staff_turn_methods_on_and_see_the_online_payments(
    client, other_client, conn, app_conn, pmc, lease, tenant
):
    start(client, lease, "25.00")
    join_as_staff(other_client, conn, app_conn, pmc)

    kept = other_client.post(
        "/api/rent/online-payments/settings", json={"cards": True, "bank_debits": False}
    )
    shown = other_client.get("/api/rent/online-payments").json()

    assert kept.status_code == 201
    assert kept.json() | {"recorded_at": None} == {
        "cards": True,
        "bank_debits": False,
        "recorded_by": "Staff 1",
        "recorded_at": None,
    }
    assert shown["processor"] == "fake"
    assert [(p["tenant"], p["amount"], p["status"]) for p in shown["payments"]] == [
        (shown["payments"][0]["tenant"], "25.00", "started")
    ]
    assert client.get("/api/tenant/online-payments").json()["methods"] == ["card"]
    bad = other_client.post("/api/rent/online-payments/settings", json={"cards": "maybe"})
    assert bad.status_code == 422


@pytest.mark.parametrize("caller", ["nobody", "owner", "staff", "tenant"])
def test_each_side_reaches_its_own(client, conn, app_conn, pmc, lease, caller):
    if caller == "owner":
        join_as_owner(client, app_conn, pmc)
    elif caller == "staff":
        join_as_staff(client, conn, app_conn, pmc)
    elif caller == "tenant":
        join_as_tenant(client, conn, app_conn, pmc)
    staff_side = [
        client.get("/api/rent/online-payments"),
        client.post(
            "/api/rent/online-payments/settings", json={"cards": True, "bank_debits": True}
        ),
    ]
    tenant_side = [
        client.get("/api/tenant/online-payments"),
        start(client, lease),
        client.post("/api/tenant/online-payments/fake/x", json={"outcome": "failed"}),
    ]
    refused = 401 if caller == "nobody" else 403
    allowed = [200, 201] if caller == "staff" else [refused] * 2
    assert [a.status_code for a in staff_side] == allowed
    if caller != "tenant":
        assert [a.status_code for a in tenant_side] == [refused] * 3


def sign(body: bytes, secret: str) -> dict[str, str]:
    at = int(time.time())
    mac = hmac.new(secret.encode(), f"{at}.".encode() + body, hashlib.sha256).hexdigest()
    return {"stripe-signature": f"t={at},v1={mac}", "content-type": "application/json"}


def stripe_event(kind: str, **item: object) -> bytes:
    return json.dumps(
        {"id": f"evt_{uuid.uuid4().hex}", "type": kind, "data": {"object": item}}
    ).encode()


@pytest.fixture
def stripe(settings, monkeypatch):
    """The app with Stripe, its answers made here; and the webhook's secret."""
    secret = secrets.token_hex(16)
    monkeypatch.setenv("PMS_STRIPE_SECRET_KEY", "made-up-key")
    monkeypatch.setenv("PMS_STRIPE_WEBHOOK_SECRET", secret)
    sessions = iter(f"cs_{n}" for n in range(100))

    def urlopen(request, timeout):
        made = next(sessions)
        return io.BytesIO(
            json.dumps({"id": made, "url": f"https://checkout.example/{made}"}).encode()
        )

    monkeypatch.setattr(processor.urllib.request, "urlopen", urlopen)
    with TestClient(create_app(dataclasses.replace(settings, payment_processor="stripe"))) as made:
        yield made, secret


def test_stripes_signed_events_record_a_payment_once_and_a_dispute_lost_bounces_it(
    stripe, conn, app_conn, pmc, lease
):
    client, secret = stripe
    takes(conn, pmc)
    join_as_tenant(client, conn, app_conn, pmc)
    started = start(client, lease, "1500.00", "card")
    assert started.json()["url"] == "https://checkout.example/cs_0"
    paid = stripe_event(
        "checkout.session.completed", id="cs_0", payment_status="paid", payment_intent="pi_7"
    )

    taken = client.post("/api/payments/stripe/webhook", content=paid, headers=sign(paid, secret))
    again = client.post("/api/payments/stripe/webhook", content=paid, headers=sign(paid, secret))
    forged = client.post("/api/payments/stripe/webhook", content=paid, headers=sign(paid, "x"))

    assert (taken.json(), again.json()) == ({"taken": 1}, {"taken": 0})
    assert forged.status_code == 400
    assert owed(client, lease)[0] == "0.00"
    assert statuses(client)[0][0] == "succeeded"

    lost = stripe_event("charge.dispute.closed", status="lost", payment_intent="pi_7")
    returned = client.post("/api/payments/stripe/webhook", content=lost, headers=sign(lost, secret))

    assert returned.json() == {"taken": 1}
    balance, payments = owed(client, lease)
    assert balance == "1500.00"
    assert payments[0]["bounced"] is True
    assert statuses(client)[0][0] == "returned"
    # An event about no payment of this server's is taken as nothing.
    stray = stripe_event("checkout.session.completed", id="cs_99", payment_status="paid")
    assert client.post(
        "/api/payments/stripe/webhook", content=stray, headers=sign(stray, secret)
    ).json() == {"taken": 0}


def test_the_webhook_is_stripes_alone(client, conn):
    body = stripe_event("checkout.session.completed", id="cs_0", payment_status="paid")
    answer = client.post("/api/payments/stripe/webhook", content=body, headers=sign(body, "x"))
    assert answer.status_code == 404
