"""Application fees through the API (src/pms/api/application_fees.py, migration
20261022000160): a company asks each applicant a fee; an application's applicants pay it with
their status link through the processor (the fake one here; Stripe's answers made here, NOT
VERIFIED against Stripe itself); once it came it is recorded as the company's own fee in trust,
once per application; a failure records nothing. Secrets are made up for each test."""

import dataclasses
import hashlib
import hmac
import io
import json
import secrets
import time
import uuid
from datetime import date, timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from helpers import add_staff, add_unit
from people import join_as_owner, join_as_staff, join_as_tenant

from pms import accounts
from pms.api import create_app
from pms.payments import processor


@pytest.fixture
def settings(settings):
    """The app with the fake processor (the conftest's settings otherwise)."""
    return dataclasses.replace(settings, payment_processor="fake")


@pytest.fixture
def link(conn, pmc):
    """An open application link for Unit 1 of the PMC's first property."""
    unit = add_unit(conn, pmc.pmc_id, pmc.owners[0].property_id)
    token = secrets.token_urlsafe(32)
    conn.execute(
        "INSERT INTO trust_application_links (pmc_id, unit_id, token_sha256, monthly_rent,"
        " opened_by) VALUES (%s, %s, %s, 1500.00, %s)",
        (pmc.pmc_id, unit, accounts.digest(token), add_staff(conn, pmc.pmc_id)),
    )
    return token


def asks(conn, pmc, fee="35.00", cards=True, bank_debits=False):
    conn.execute(
        "INSERT INTO trust_application_fee_settings (pmc_id, per_applicant) VALUES (%s, %s)",
        (pmc.pmc_id, fee),
    )
    conn.execute(
        "INSERT INTO trust_online_payment_settings (pmc_id, cards, bank_debits)"
        " VALUES (%s, %s, %s)",
        (pmc.pmc_id, cards, bank_debits),
    )


def apply(client, token, people=2):
    answer = client.post(
        "/api/apply",
        json={
            "token": token,
            "move_in_on": (date.today() + timedelta(days=20)).isoformat(),
            "monthly_income": "4500.00",
            "applicants": [
                {"name": f"Applicant {n}", "email": f"applicant.{n}@example.com"}
                for n in range(people)
            ],
        },
    )
    assert answer.status_code == 201, answer.text
    return answer.json()["status_url"].split("#", 1)[1]


def fee_of(client, status):
    return client.post("/api/application/status", json={"status_token": status}).json()["fee"]


def held(conn, pmc):
    return conn.execute(
        "SELECT a.balance FROM pgledger_accounts AS a WHERE a.id = %s", (pmc.pmc_income,)
    ).fetchone()[0]


def test_applicants_pay_the_fee_and_it_is_the_companys_in_trust(client, conn, pmc, link):
    asks(conn, pmc)
    status = apply(client, link)
    before = held(conn, pmc)

    assert fee_of(client, status) == {"due": "70.00", "paid": None, "methods": ["card"]}
    started = client.post("/api/application/fee", json={"status_token": status, "method": "card"})
    reference = started.json()["url"].removeprefix("/pay#")
    assert started.status_code == 201, started.text
    assert reference.startswith("fake_fee_")
    paid = client.post(f"/api/application/fee/fake/{reference}", json={"outcome": "succeeded"})
    again = client.post(f"/api/application/fee/fake/{reference}", json={"outcome": "succeeded"})

    assert (paid.status_code, again.status_code) == (204, 404)
    assert fee_of(client, status) == {"due": None, "paid": "70.00", "methods": []}
    assert held(conn, pmc) - before == Decimal("70.00")
    kind = conn.execute(
        "SELECT t.metadata ->> 'kind' FROM pgledger_transfers AS t WHERE t.to_account_id = %s"
        " ORDER BY t.created_at DESC LIMIT 1",
        (pmc.pmc_income,),
    ).fetchone()
    assert kind == ("application fee",)
    # Paid once: nothing more is due, so nothing more starts.
    more = client.post("/api/application/fee", json={"status_token": status, "method": "card"})
    assert (more.status_code, more.json()["detail"]) == (409, "this application has no fee to pay")


def test_a_failure_records_nothing_and_staff_see_what_was_paid(
    client, other_client, conn, app_conn, pmc, link
):
    asks(conn, pmc, "40.00")
    status = apply(client, link, people=1)
    first = client.post("/api/application/fee", json={"status_token": status, "method": "card"})
    reference = first.json()["url"].removeprefix("/pay#")
    client.post(f"/api/application/fee/fake/{reference}", json={"outcome": "failed"})
    assert fee_of(client, status)["due"] == "40.00"
    second = client.post("/api/application/fee", json={"status_token": status, "method": "card"})
    reference = second.json()["url"].removeprefix("/pay#")
    client.post(f"/api/application/fee/fake/{reference}", json={"outcome": "succeeded"})

    join_as_staff(other_client, conn, app_conn, pmc)
    (shown,) = other_client.get("/api/leasing/applications?show=all").json()["items"]
    assert shown["fee_paid"] == "40.00"
    register = other_client.get(
        f"/api/bank-accounts/{pmc.operating_bank_id}/register",
        params={"start": "2000-01-01", "end": "2100-01-01"},
    ).json()["items"]
    assert [line["type"] for line in register if line["type"] == "Application fee"] == [
        "Application fee"
    ]


def test_staff_set_the_fee_each_applicant_pays(other_client, conn, app_conn, pmc):
    join_as_staff(other_client, conn, app_conn, pmc)
    assert other_client.get("/api/leasing/application-fee").json()["per_applicant"] == "0.00"

    kept = other_client.post("/api/leasing/application-fee", json={"per_applicant": "35.00"})
    bad = [
        other_client.post("/api/leasing/application-fee", json={"per_applicant": value})
        for value in ("-1", "1000.01", "1.005", "free")
    ]

    assert kept.status_code == 201
    assert kept.json() | {"recorded_at": None} == {
        "per_applicant": "35.00",
        "recorded_by": "Staff 1",
        "recorded_at": None,
    }
    assert [answer.status_code for answer in bad] == [422] * 4


@pytest.mark.parametrize(
    ("fee", "method", "status", "detail"),
    [
        ("0.00", "card", 409, "this application has no fee to pay"),
        ("35.00", "bank debit", 409, "its fee isn't taken by bank debit online"),
        ("35.00", "cash", 422, None),
    ],
)
def test_a_fee_not_asked_or_not_taken_that_way_is_refused(
    client, conn, pmc, link, fee, method, status, detail
):
    asks(conn, pmc, fee)
    token = apply(client, link)

    answer = client.post("/api/application/fee", json={"status_token": token, "method": method})

    assert answer.status_code == status, answer.text
    if detail:
        assert answer.json()["detail"] == detail


def test_a_status_link_that_isnt_one_pays_nothing(client, conn, pmc, link):
    asks(conn, pmc)
    for token in ("x" * 40, secrets.token_urlsafe(32)):
        answer = client.post("/api/application/fee", json={"status_token": token, "method": "card"})
        assert answer.status_code in (404, 422)
    stray = client.post("/api/application/fee/fake/fake_fee_none", json={"outcome": "succeeded"})
    assert stray.status_code == 404


@pytest.mark.parametrize("caller", ["nobody", "owner", "tenant"])
def test_only_staff_set_the_fee(client, conn, app_conn, pmc, caller):
    if caller == "owner":
        join_as_owner(client, app_conn, pmc)
    elif caller == "tenant":
        join_as_tenant(client, conn, app_conn, pmc)
    status = 401 if caller == "nobody" else 403
    assert client.get("/api/leasing/application-fee").status_code == status
    kept = client.post("/api/leasing/application-fee", json={"per_applicant": "1.00"})
    assert kept.status_code == status


def test_stripes_signed_event_records_the_fee(settings, monkeypatch, conn, pmc, link):
    secret = secrets.token_hex(16)
    monkeypatch.setenv("PMS_STRIPE_SECRET_KEY", "made-up-key")
    monkeypatch.setenv("PMS_STRIPE_WEBHOOK_SECRET", secret)
    monkeypatch.setattr(
        processor.urllib.request,
        "urlopen",
        lambda request, timeout: io.BytesIO(
            json.dumps({"id": "cs_fee", "url": "https://checkout.example/cs_fee"}).encode()
        ),
    )
    asks(conn, pmc)
    with TestClient(create_app(dataclasses.replace(settings, payment_processor="stripe"))) as c:
        status = apply(c, link, people=1)
        c.post("/api/application/fee", json={"status_token": status, "method": "card"})
        body = json.dumps({
            "id": f"evt_{uuid.uuid4().hex}", "type": "checkout.session.completed",
            "data": {"object": {"id": "cs_fee", "payment_status": "paid",
                                "payment_intent": "pi_9"}},
        }).encode()  # fmt: skip
        at = int(time.time())
        mac = hmac.new(secret.encode(), f"{at}.".encode() + body, hashlib.sha256).hexdigest()
        headers = {"stripe-signature": f"t={at},v1={mac}", "content-type": "application/json"}
        taken = c.post("/api/payments/stripe/webhook", content=body, headers=headers)
        assert taken.json() == {"taken": 1}
        assert fee_of(c, status)["paid"] == "35.00"
