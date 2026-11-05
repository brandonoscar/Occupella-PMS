"""Owners pay in online through the API (src/pms/api/owner_payments.py): once the company takes
payments online, an owner pays into one of their property accounts from the portal on the
partner's page; the partner's report records it as the owner's contribution, once however often
it is sent; the company pays the partner's fee from its fees in trust, never the owner (the
founder, 2026-11-02); a return is taken back out of the owner's account; what the books can't take
yet waits for staff on Banking with the tenants' payments."""

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
    add_agreement,
    approve_bank,
    balance,
    contribute,
    draw_owner,
    import_statement,
    post,
    staff_named,
)
from people import join_as_owner, join_as_staff

from pms import payments
from pms.api import Settings, create_app

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
def owner_client(settings):
    with TestClient(create_app(settings)) as browser:
        yield browser


@pytest.fixture
def owner(owner_client, app_conn, pmc):
    return join_as_owner(owner_client, app_conn, pmc)


def ok(answer, status=200):
    assert answer.status_code == status, answer.text
    return answer.json() if answer.content else None


def refused(answer, status, detail=None):
    assert answer.status_code == status, answer.text
    if detail is not None:
        assert answer.json()["detail"] == detail


def turn_on(client, payer="company"):
    ok(client.post("/api/payments/settings", json={"online": True, "ach_fee_payer": payer}), 204)


def pay(owner_client, account, amount="500.00"):
    return ok(
        owner_client.post(
            "/api/payments/owner/checkout", json={"account_id": account, "amount": amount}
        ),
        201,
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


def contributions(conn, pmc):
    return conn.execute(
        "SELECT request_key, ledger_account_id, amount::text FROM trust_owner_contributions"
        " WHERE pmc_id = %s ORDER BY created_at",
        (pmc.pmc_id,),
    ).fetchall()


def fund_company(conn, pmc, amount):
    """The company's fees in the operating trust account: a contribution, then a fee."""
    contribute(conn, pmc.pmc_id, pmc.owners[1].account, f"fund/{uuid4()}", amount)
    post(conn, pmc.pmc_id, f"fee/{uuid4()}", [(pmc.owners[1].account, pmc.pmc_income, amount)])


# --- what the owner sees ----------------------------------------------------------------------


def test_an_owner_sees_their_accounts_and_pays_only_once_the_company_takes_it(
    client, owner_client, conn, staff, owner, pmc
):
    add_agreement(conn, pmc.pmc_id, pmc.owners[0].account, TODAY - 30 * DAY, reserve="250.00")
    view = ok(owner_client.get("/api/payments/owner"))
    assert (view["available"], view["why_not"], view["how_to_pay"]) == (False, "off", None)
    (account,) = view["accounts"]  # their own account only, not the other owner's
    assert account["account_id"] == pmc.owners[0].account
    assert (account["balance"], account["reserve"], account["short"]) == (
        "0.00",
        "250.00",
        "250.00",
    )
    refused(
        owner_client.post(
            "/api/payments/owner/checkout",
            json={"account_id": pmc.owners[0].account, "amount": "250.00"},
        ),
        409,
        "Online payments aren't available.",
    )
    turn_on(client)
    assert ok(owner_client.get("/api/payments/owner"))["available"] is True
    started = pay(owner_client, pmc.owners[0].account, "250.00")
    assert started["url"] == f"http://testserver/pay/fake#{started['id']}"
    shown = ok(owner_client.get(f"/api/payments/fake/{started['id']}"))
    assert (shown["total"], shown["tenant_fee"], shown["done"]) == (
        "250.00",
        "0.00",
        "/pay/owner-done",
    )
    assert shown["memo"].startswith("Owner contribution – ")
    view = ok(owner_client.get("/api/payments/owner"))
    assert [(p["amount"], p["status"]) for p in view["payments"]] == [("250.00", "started")]
    # Nothing is in the books until the bank pays.
    assert contributions(conn, pmc) == []


def test_an_owner_pays_only_into_an_account_of_theirs_and_a_sane_amount(
    client, owner_client, other_client, staff, owner, pmc
):
    turn_on(client)
    refused(
        pay_answer(owner_client, pmc.owners[1].account), 404, "no such property account of yours"
    )
    refused(pay_answer(owner_client, pmc.pmc_income), 404, "no such property account of yours")
    refused(pay_answer(owner_client, pmc.owners[0].account, "0"), 422)
    refused(pay_answer(owner_client, pmc.owners[0].account, "100000.01"), 422)
    refused(pay_answer(owner_client, pmc.owners[0].account, "1.001"), 422)
    refused(pay_answer(owner_client, "x" * 101), 422)
    # Staff and the signed out don't pay as an owner.
    refused(client.get("/api/payments/owner"), 403)
    refused(pay_answer(client, pmc.owners[0].account), 403)
    refused(other_client.get("/api/payments/owner"), 401)
    refused(pay_answer(other_client, pmc.owners[0].account), 401)


def pay_answer(browser, account, amount="10.00"):
    return browser.post(
        "/api/payments/owner/checkout", json={"account_id": account, "amount": amount}
    )


def test_an_archived_propertys_account_takes_no_payment(
    client, owner_client, conn, staff, owner, pmc
):
    turn_on(client)
    conn.execute(
        "INSERT INTO trust_archivals (pmc_id, property_id, archived) VALUES (%s, %s, true)",
        (pmc.pmc_id, pmc.owners[0].property_id),
    )
    assert ok(owner_client.get("/api/payments/owner"))["accounts"] == []
    refused(pay_answer(owner_client, pmc.owners[0].account), 404)


def test_the_database_takes_an_owners_payment_only_into_an_owners_account(conn, pmc):
    with pytest.raises(psycopg.errors.CheckViolation, match="not an owner's property account"):
        conn.execute(
            "INSERT INTO trust_owner_online_payments (pmc_id, ledger_account_id, amount, method,"
            " partner, partner_ref) VALUES (%s, %s, 10, 'ach', 'fake', 'x')",
            (pmc.pmc_id, pmc.pmc_income),
        )


# --- what the partner reports -----------------------------------------------------------------


def test_paid_is_the_owners_contribution_once_and_the_company_pays_the_fee(
    client, owner_client, conn, staff, owner, pmc
):
    turn_on(client, "tenant")  # tenants pay their own fee; an owner's is still the company's
    fund_company(conn, pmc, "10.00")
    payment = pay(owner_client, pmc.owners[0].account, "500.00")["id"]
    assert bank_says(owner_client, payment, "paid") == {"recorded": True}
    assert bank_says(owner_client, payment, "paid") == {"recorded": True}  # sent again
    assert balance(conn, pmc.owners[0].account) == Decimal("500.00")
    assert contributions(conn, pmc)[-1] == (
        f"owner-payment/{payment}",
        pmc.owners[0].account,
        "500.00",
    )
    # The partner kept 4.00 (0.8%); it came out of the company's 10.00, never the owner's money.
    assert balance(conn, pmc.pmc_income) == Decimal("6.00")
    assert f"owner-partner-fee/{payment}" in keys(conn, pmc)
    assert (
        conn.execute(
            "SELECT count(*) FROM trust_owner_online_payment_events WHERE payment_id = %s",
            (payment,),
        ).fetchone()[0]
        == 1
    )
    view = ok(owner_client.get("/api/payments/owner"))
    assert view["payments"][0]["status"] == "paid"
    assert view["accounts"][0]["balance"] == "500.00"
    # On their statement as a contribution paid online.
    memo = conn.execute(
        "SELECT t.metadata ->> 'memo' FROM trust_owner_contributions AS c"
        " JOIN pgledger_transfers AS t ON t.id = c.transfer_id WHERE c.request_key = %s",
        (f"owner-payment/{payment}",),
    ).fetchone()[0]
    assert memo == "Paid online by bank"
    # A failure after it was paid changes nothing; nothing waits.
    assert bank_says(owner_client, payment, "failed") == {"recorded": True}
    assert ok(owner_client.get("/api/payments/owner"))["payments"][0]["status"] == "paid"
    assert ok(client.get("/api/payments/waiting")) == []


def test_a_failed_or_unfinished_payment_moves_nothing(
    client, owner_client, conn, staff, owner, pmc
):
    turn_on(client)
    failed = pay(owner_client, pmc.owners[0].account)["id"]
    left = pay(owner_client, pmc.owners[0].account)["id"]
    bank_says(owner_client, failed, "failed")
    bank_says(owner_client, failed, "paid")  # out of order: kept as it first came
    bank_says(owner_client, left, "expired")
    statuses = {
        p["id"]: p["status"] for p in ok(owner_client.get("/api/payments/owner"))["payments"]
    }
    assert statuses == {failed: "failed", left: "expired"}
    assert contributions(conn, pmc) == []
    assert balance(conn, pmc.owners[0].account) == Decimal("0.00")


def test_the_company_covers_the_partners_fee_its_account_cant(
    client, owner_client, conn, staff, owner, pmc
):
    turn_on(client)
    payment = pay(owner_client, pmc.owners[0].account, "500.00")["id"]
    bank_says(owner_client, payment, "paid")
    assert balance(conn, pmc.owners[0].account) == Decimal("500.00")  # the owner's, whole
    waiting = ok(client.get("/api/payments/waiting"))
    assert [
        (w["what"], w["company_owes"], w["company_holds"], w["lease_id"], w["owner_id"])
        for w in waiting
    ] == [("partner fee", "4.00", "0.00", None, str(pmc.owners[0].owner_id))]
    assert waiting[0]["cover_needed"] == "4.00"
    cover = {
        "bank_account_id": waiting[0]["bank_account_id"],
        "amount": "4.00",
        "request_id": str(uuid4()),
    }
    refused(owner_client.post("/api/payments/partner-fees/cover", json=cover), 403)
    assert ok(client.post("/api/payments/partner-fees/cover", json=cover)) == {"still_waiting": 0}
    assert ok(client.get("/api/payments/waiting")) == []
    assert balance(conn, pmc.pmc_income) == Decimal("0.00")
    assert balance(conn, pmc.owners[0].account) == Decimal("500.00")


def test_a_return_is_taken_back_out_of_the_owners_account(
    client, owner_client, conn, staff, owner, pmc
):
    turn_on(client)
    fund_company(conn, pmc, "10.00")
    payment = pay(owner_client, pmc.owners[0].account, "500.00")["id"]
    bank_says(owner_client, payment, "returned")  # not paid yet: nothing came back
    assert f"owner-payment-back/{payment}" not in keys(conn, pmc)
    bank_says(owner_client, payment, "paid")
    bank_says(owner_client, payment, "returned")
    bank_says(owner_client, payment, "returned")  # sent again
    assert balance(conn, pmc.owners[0].account) == Decimal("0.00")
    assert f"owner-payment-back/{payment}" in keys(conn, pmc)
    assert ok(owner_client.get("/api/payments/owner"))["payments"][0]["status"] == "returned"
    # The partner's fee stays the company's: the bank kept it.
    assert balance(conn, pmc.pmc_income) == Decimal("6.00")
    assert ok(client.get("/api/payments/waiting")) == []


def test_a_return_the_owners_account_cant_cover_waits_for_staff(
    client, owner_client, conn, staff, owner, pmc
):
    turn_on(client)
    fund_company(conn, pmc, "10.00")
    payment = pay(owner_client, pmc.owners[0].account, "500.00")["id"]
    bank_says(owner_client, payment, "paid")
    draw_owner(
        conn, pmc.pmc_id, pmc.owners[0].account, f"draw/{uuid4()}", "300.00", datetime.now(UTC)
    )
    bank_says(owner_client, payment, "returned")
    waiting = ok(client.get("/api/payments/waiting"))
    assert [(w["what"], w["company_owes"], w["owner_id"]) for w in waiting] == [
        ("return", None, str(pmc.owners[0].owner_id))
    ]
    # Staff try again: still short, so it still waits.
    ok(client.post(f"/api/payments/{payment}/record", json={}), 204)
    assert [w["what"] for w in ok(client.get("/api/payments/waiting"))] == ["return"]
    contribute(conn, pmc.pmc_id, pmc.owners[0].account, f"cheque/{uuid4()}", "300.00")
    ok(client.post(f"/api/payments/{payment}/record", json={}), 204)
    assert ok(client.get("/api/payments/waiting")) == []
    assert balance(conn, pmc.owners[0].account) == Decimal("0.00")


def test_staff_record_only_their_companys_paid_payments(
    client, owner_client, other_client, conn, app_conn, staff, owner, pmc
):
    turn_on(client)
    payment = pay(owner_client, pmc.owners[0].account)["id"]
    refused(
        client.post(f"/api/payments/{payment}/record", json={}),
        409,
        "the payments partner hasn't said this payment was paid",
    )
    refused(owner_client.post(f"/api/payments/{payment}/record", json={}), 403)
    refused(client.post(f"/api/payments/{uuid4()}/record", json={}), 404)
    refused(client.post("/api/payments/not-an-id/record", json={}), 422)


def test_a_refusal_from_the_books_reaches_staff_in_words(
    client, owner_client, conn, staff, owner, pmc, monkeypatch
):
    from pms.api import owner_payments

    turn_on(client)
    payment = pay(owner_client, pmc.owners[0].account)["id"]
    real = owner_payments.record_contribution

    def refuse(conn, row, day):
        conn.execute(
            "SELECT trust_record_owner_contribution(%s, %s, 'k', 0, now())",
            (row.pmc_id, row.account),
        )

    monkeypatch.setattr(owner_payments, "record_contribution", refuse)
    bank_says(owner_client, payment, "paid")
    assert [w["what"] for w in ok(client.get("/api/payments/waiting"))] == ["payment"]
    answer = client.post(f"/api/payments/{payment}/record", json={})
    refused(answer, 409)
    assert "positive amount" in answer.json()["detail"]
    monkeypatch.setattr(owner_payments, "record_contribution", real)
    ok(client.post(f"/api/payments/{payment}/record", json={}), 204)
    assert balance(conn, pmc.owners[0].account) == Decimal("500.00")


def test_a_partner_that_does_not_answer_starts_no_payment(
    client, owner_client, conn, staff, owner, pmc, monkeypatch
):
    turn_on(client)

    def down(self, payment_id, lines, done_url):
        raise payments.PartnerFailed("down")

    monkeypatch.setattr(payments.FakePartner, "checkout", down)
    refused(
        pay_answer(owner_client, pmc.owners[0].account),
        502,
        "We couldn't reach our payments partner. Try again in a few minutes.",
    )
    assert (
        conn.execute(
            "SELECT count(*) FROM trust_owner_online_payments WHERE pmc_id = %s", (pmc.pmc_id,)
        ).fetchone()[0]
        == 0
    )


# --- Stripe's webhook ---------------------------------------------------------------------


@pytest.fixture
def stripe_app(database_url, monkeypatch):
    # The server's own Stripe keys serve only a server holding one company (payments.Resolver);
    # the test database holds many, so it answers as a server with one.
    monkeypatch.setattr(payments, "many_companies", lambda conn: False)
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
    with TestClient(create_app(settings)) as browser, TestClient(create_app(settings)) as owner:
        yield browser, owner, answers


def stripe_report(browser, kind, subject, created=None):
    now = int(time.time())
    body = json.dumps(
        {
            "id": f"evt_{uuid4().hex}",
            "type": kind,
            "created": now if created is None else created,
            "data": {"object": subject},
        }
    ).encode()
    mac = hmac.new(SECRET.encode(), f"{now}.".encode() + body, hashlib.sha256).hexdigest()
    return browser.post(
        "/api/payments/webhook/stripe",
        content=body,
        headers={"stripe-signature": f"t={now},v1={mac}", "content-type": "application/json"},
    )


def test_stripes_signed_report_records_an_owners_payment(stripe_app, conn, app_conn, pmc):
    browser, owner_browser, answers = stripe_app
    join_as_staff(browser, conn, app_conn, pmc)
    turn_on(browser)
    fund_company(conn, pmc, "10.00")
    answers[("POST", "/checkout/sessions")] = {"id": "cs_owner_1", "url": "https://checkout.test/1"}
    join_as_owner(owner_browser, app_conn, pmc)
    started = pay(owner_browser, pmc.owners[0].account, "500.00")
    assert started["url"] == "https://checkout.test/1"
    payment = started["id"]
    answers[("GET", "/payment_intents/pi_o1")] = {
        "latest_charge": {"balance_transaction": {"fee": 400}}
    }
    subject = {"id": "cs_owner_1", "client_reference_id": payment, "payment_intent": "pi_o1"}
    assert ok(stripe_report(browser, "checkout.session.async_payment_succeeded", subject)) == {
        "recorded": True
    }
    assert balance(conn, pmc.owners[0].account) == Decimal("500.00")
    assert balance(conn, pmc.pmc_income) == Decimal("6.00")
    # The fake partner's page is not this server's.
    refused(browser.get(f"/api/payments/fake/{payment}"), 404)


def test_a_payment_whose_day_month_end_closed_is_recorded_in_the_next_open_one(
    stripe_app, conn, app_conn, pmc
):
    # The bank paid yesterday, and month-end already closed yesterday: the contribution goes into
    # the books on the first day left open, as a late correction does.
    browser, owner_browser, answers = stripe_app
    join_as_staff(browser, conn, app_conn, pmc)
    turn_on(browser)
    answers[("POST", "/checkout/sessions")] = {"id": "cs_owner_2", "url": "https://checkout.test/2"}
    join_as_owner(owner_browser, app_conn, pmc)
    payment = pay(owner_browser, pmc.owners[0].account, "300.00")["id"]
    clerk = staff_named(conn, pmc.pmc_id, "Clerk 1")
    import_statement(
        conn, pmc.pmc_id, pmc.operating_bank_id, clerk, TODAY - 9 * DAY, TODAY - DAY, []
    )
    approve_bank(conn, pmc.pmc_id, pmc.operating_bank_id, TODAY - DAY)
    fund_company(conn, pmc, "10.00")
    answers[("GET", "/payment_intents/pi_o2")] = {
        "latest_charge": {"balance_transaction": {"fee": 240}}
    }
    yesterday = int(datetime.combine(TODAY - DAY, datetime.min.time(), UTC).timestamp()) + 43200
    subject = {"id": "cs_owner_2", "client_reference_id": payment, "payment_intent": "pi_o2"}
    paid = stripe_report(browser, "checkout.session.async_payment_succeeded", subject, yesterday)
    assert ok(paid) == {"recorded": True}
    dated = conn.execute(
        "SELECT (t.event_at AT TIME ZONE 'UTC')::date FROM pgledger_transfers AS t"
        " WHERE t.id = (SELECT transfer_id FROM trust_owner_contributions WHERE request_key = %s)"
        " OR t.metadata ->> 'key' = %s",
        (f"owner-payment/{payment}", f"owner-partner-fee/{payment}"),
    ).fetchall()
    assert len(dated) == 2 and {day for (day,) in dated} == {TODAY}
