"""A company's own Stripe keys through the API (src/pms/api/payment_keys.py, migration
20261104000800; the founder's review of 11/03, item 4, and his call of 11/04): an admin keeps them
sealed, only the last four and the mode ever show; the company's payments then go through its own
Stripe account and its own webhook, which records only its payments and still takes reports signed
with keys it used before; taken away, the server's partner holds again. Every key here is made up,
and spelled in pieces so no secret scanner mistakes it for one."""

import base64
import hashlib
import hmac
import json
import os
import time
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import psycopg
import pytest
from fastapi.testclient import TestClient
from helpers import add_unit, charge, make_pmc, open_lease
from people import join_as_owner, join_as_staff, join_as_tenant, tenant_of

from pms import payments
from pms.api import Settings, create_app

TODAY = datetime.now(UTC).date()
DAY = timedelta(days=1)
# Made-up keys, never a real account's.
SECRET_KEY = "sk_" + "test_" + "Synthetic000000000000A1b2"
OTHER_KEY = "sk_" + "live_" + "Synthetic000000000000Z9y8"
WEBHOOK = "whsec_" + "Synthetic0000000000000001"
OTHER_WEBHOOK = "whsec_" + "Synthetic0000000000000002"


@pytest.fixture(autouse=True)
def data_key(monkeypatch):
    monkeypatch.setenv("PMS_DATA_KEY", base64.b64encode(os.urandom(32)).decode())


@pytest.fixture
def stripe(monkeypatch):
    """Stripe's answers, by method and path, and each call made, with the key it was made with."""
    answers, calls = {}, []

    def call(self, method, path, form, key=""):
        calls.append((self._key, method, path))
        return answers[(method, path)]

    monkeypatch.setattr(payments.StripePartner, "_call", call)
    return answers, calls


@pytest.fixture
def admin(client, conn, app_conn, pmc):
    return join_as_staff(client, conn, app_conn, pmc)


@pytest.fixture
def lease(conn, pmc):
    unit = add_unit(conn, pmc.pmc_id, pmc.owners[0].property_id)
    made = open_lease(
        conn, pmc.pmc_id, unit, TODAY - 60 * DAY, None, "1200.00", [tenant_of(conn, pmc)]
    )
    charge(conn, pmc.pmc_id, made, TODAY - 10 * DAY, "1200.00")
    return made


def ok(answer, status=200):
    assert answer.status_code == status, answer.text
    return answer.json() if answer.content else None


def refused(answer, status, detail=None):
    assert answer.status_code == status, answer.text
    if detail is not None:
        assert answer.json()["detail"] == detail


def keep(client, secret_key=SECRET_KEY, webhook_secret=WEBHOOK):
    return client.post(
        "/api/payments/keys", json={"secret_key": secret_key, "webhook_secret": webhook_secret}
    )


def signed(client, pmc_id, kind, subject, secret):
    """Stripe's signed report of `kind` about `subject`, to the company's own webhook."""
    now = int(time.time())
    body = json.dumps(
        {
            "id": f"evt_{uuid4().hex}",
            "type": kind,
            "created": now,
            # Each webhook secret here goes with its key: OTHER_KEY's is live, SECRET_KEY's test.
            "livemode": secret == OTHER_WEBHOOK,
            "data": {"object": subject},
        }
    ).encode()
    mac = hmac.new(secret.encode(), f"{now}.".encode() + body, hashlib.sha256).hexdigest()
    return client.post(
        f"/api/payments/webhook/stripe/{pmc_id}",
        content=body,
        headers={"stripe-signature": f"t={now},v1={mac}", "content-type": "application/json"},
    )


def report(client, pmc_id, payment, secret, kind="checkout.session.async_payment_succeeded"):
    """Stripe's signed report that the bank paid `payment`, to the company's own webhook."""
    subject = {"id": "cs_1", "client_reference_id": payment, "payment_intent": "pi_1"}
    return signed(client, pmc_id, kind, subject, secret)


def start_payment(client, tenant_client, conn, app_conn, pmc, lease, stripe):
    answers, _ = stripe
    ok(
        client.post("/api/payments/settings", json={"online": True, "ach_fee_payer": "company"}),
        204,
    )
    # Stripe's ids are its own and never repeat.
    session = f"cs_{uuid4().hex}"
    answers[("POST", "/checkout/sessions")] = {
        "id": session,
        "url": f"https://checkout.test/{session}",
    }
    answers[("GET", "/payment_intents/pi_1")] = {
        "latest_charge": {"balance_transaction": {"fee": 800}}
    }
    join_as_tenant(tenant_client, conn, app_conn, pmc)
    return ok(
        tenant_client.post(f"/api/payments/leases/{lease}/checkout", json={"amount": "1200.00"}),
        201,
    )["id"]


def paid(conn, payment):
    return conn.execute(
        "SELECT count(*) FROM trust_online_payment_events WHERE payment_id = %s AND kind = 'paid'",
        (payment,),
    ).fetchone() == (1,)


# --- keeping them --------------------------------------------------------------------------


def test_an_admin_keeps_the_companys_keys_sealed_and_only_the_last_four_show(
    client, conn, pmc, admin
):
    before = ok(client.get("/api/payments/keys"))
    assert (before["using"], before["ends_in"], before["can_keep"], before["may_change"]) == (
        "none",
        None,
        True,
        True,
    )
    assert before["webhook_url"] == f"http://testserver/api/payments/webhook/stripe/{pmc.pmc_id}"
    # docs/edge-cases.md P-012: an endpoint not sent these never reports a bank payment paid.
    assert before["webhook_events"] == [
        "charge.dispute.created",
        "checkout.session.async_payment_failed",
        "checkout.session.async_payment_succeeded",
        "checkout.session.completed",
        "checkout.session.expired",
        "payment_intent.payment_failed",
        "payment_intent.succeeded",
        "setup_intent.succeeded",
    ]

    kept = keep(client)

    shown = ok(kept)
    assert (shown["using"], shown["mode"], shown["ends_in"], shown["changed_by"]) == (
        "own",
        "test",
        "A1b2",
        "Staff 1",
    )
    assert SECRET_KEY not in kept.text and WEBHOOK not in kept.text
    assert ok(client.get("/api/payments/settings"))["partner"] == "stripe"
    [row] = conn.execute(
        "SELECT secret_key_sealed, webhook_secret_sealed, secret_key_last4, mode, recorded_by"
        " FROM trust_payment_keys WHERE pmc_id = %s",
        (pmc.pmc_id,),
    ).fetchall()
    assert SECRET_KEY.encode() not in bytes(row[0]) and WEBHOOK.encode() not in bytes(row[1])
    assert row[2:] == ("A1b2", "test", admin.person_id)
    audited = conn.execute(
        "SELECT row_data::text FROM trust_audit_log WHERE table_name = 'trust_payment_keys'"
    ).fetchall()
    assert audited and all(SECRET_KEY not in text and WEBHOOK not in text for (text,) in audited)


def test_a_live_key_says_live(client, admin):
    assert ok(keep(client, OTHER_KEY))["mode"] == "live"


# docs/edge-cases.md P-048: a server at https on a host other than the machine itself is live
# (deployment.is_live): a test payment there would be booked as real money.


def test_a_live_server_takes_a_companys_live_keys_and_refuses_its_test_keys(
    client, conn, pmc, admin
):
    client.app.state.payments.live = True

    refused(
        keep(client),
        422,
        "Those are test keys, and this server takes real payments. Enter your live keys"
        " (they start with sk_live_).",
    )
    assert conn.execute(
        "SELECT count(*) FROM trust_payment_keys WHERE pmc_id = %s", (pmc.pmc_id,)
    ).fetchone() == (0,)
    shown = ok(keep(client, OTHER_KEY, OTHER_WEBHOOK))
    assert (shown["using"], shown["mode"]) == ("own", "live")


def test_test_keys_kept_before_the_server_went_live_take_no_payment_and_no_report(
    client, pmc, admin, lease
):
    ok(keep(client))
    client.app.state.payments.live = True

    shown = ok(client.get("/api/payments/keys"))
    assert (shown["using"], shown["mode"], shown["ends_in"]) == ("test_refused", "test", "A1b2")
    assert ok(client.get("/api/payments/settings"))["partner"] is None
    # Its own webhook has no keys left to take a report with.
    refused(report(client, pmc.pmc_id, str(uuid4()), WEBHOOK), 404)


@pytest.mark.parametrize(
    ("secret_key", "webhook_secret", "said"),
    [
        ("pk_" + "test_" + "Synthetic000000000000A1b2", WEBHOOK, "It starts with sk_live_"),
        ("sk_" + "test_" + "short", WEBHOOK, "It starts with sk_live_"),
        ("", WEBHOOK, "It starts with sk_live_"),
        (SECRET_KEY, "not a secret", "It starts with whsec_"),
        (SECRET_KEY + "x" * 400, WEBHOOK, "longer than any Stripe key"),
    ],
)
def test_what_isnt_a_stripe_key_is_refused_without_saying_it_back(
    client, conn, pmc, admin, secret_key, webhook_secret, said
):
    answer = keep(client, secret_key, webhook_secret)

    assert answer.status_code == 422
    assert said in answer.json()["detail"]
    assert (secret_key or "-") not in answer.text and webhook_secret not in answer.text
    assert conn.execute(
        "SELECT count(*) FROM trust_payment_keys WHERE pmc_id = %s", (pmc.pmc_id,)
    ).fetchone() == (0,)


def test_a_server_without_a_data_key_keeps_none(client, admin, monkeypatch):
    monkeypatch.delenv("PMS_DATA_KEY")

    assert ok(client.get("/api/payments/keys"))["can_keep"] is False
    refused(
        keep(client), 409, "This server can't keep keys. Ask whoever runs it to set PMS_DATA_KEY."
    )


def test_only_an_admin_changes_them(client, other_client, conn, app_conn, pmc, admin):
    ok(keep(client))
    clerk = join_as_staff(other_client, conn, app_conn, pmc, "Clerk 1", ("approve", "disburse"))

    assert ok(other_client.get("/api/payments/keys"))["may_change"] is False
    refused(keep(other_client, OTHER_KEY), 403, "Only an admin can change the payments keys.")
    refused(other_client.post("/api/payments/keys/remove", json={}), 403)
    # Past the API too: the database holds the keys to an admin.
    with pytest.raises(psycopg.errors.InsufficientPrivilege), app_conn.transaction():
        app_conn.execute("SELECT set_config('trust.staff_id', %s, true)", (str(clerk.person_id),))
        app_conn.execute("INSERT INTO trust_payment_keys (pmc_id) VALUES (%s)", (pmc.pmc_id,))
    assert ok(client.get("/api/payments/keys"))["using"] == "own"


@pytest.mark.parametrize("caller", ["nobody", "owner", "tenant"])
def test_only_staff_see_them(client, conn, app_conn, pmc, lease, caller):
    if caller == "owner":
        join_as_owner(client, app_conn, pmc)
    elif caller == "tenant":
        join_as_tenant(client, conn, app_conn, pmc)
    status = 401 if caller == "nobody" else 403

    assert [
        client.get("/api/payments/keys").status_code,
        keep(client).status_code,
        client.post("/api/payments/keys/remove", json={}).status_code,
    ] == [status] * 3


def test_keys_or_none_the_database_keeps_nothing_in_between(conn, app_conn, pmc, admin):
    with pytest.raises(psycopg.errors.CheckViolation), app_conn.transaction():
        app_conn.execute("SELECT set_config('trust.staff_id', %s, true)", (str(admin.person_id),))
        app_conn.execute(
            "INSERT INTO trust_payment_keys (pmc_id, secret_key_last4) VALUES (%s, 'A1b2')",
            (pmc.pmc_id,),
        )


# --- paying through them -------------------------------------------------------------------


def test_the_companys_payments_go_through_its_own_account_and_its_own_webhook(
    client, other_client, conn, app_conn, pmc, admin, lease, stripe
):
    _, calls = stripe
    ok(keep(client))

    payment = start_payment(client, other_client, conn, app_conn, pmc, lease, stripe)

    assert calls[0] == (SECRET_KEY, "POST", "/checkout/sessions")
    # The server has no partner of its own: its webhook isn't there.
    refused(other_client.post("/api/payments/webhook/stripe", json={}), 404)
    refused(report(other_client, pmc.pmc_id, payment, OTHER_WEBHOOK), 400)
    assert ok(report(other_client, pmc.pmc_id, payment, WEBHOOK)) == {"recorded": True}
    assert paid(conn, payment)


def test_a_companys_webhook_records_none_of_another_companys_payments(
    client, other_client, conn, app_conn, pmc, admin, lease, stripe
):
    ok(keep(client))
    payment = start_payment(client, other_client, conn, app_conn, pmc, lease, stripe)
    other = make_pmc(conn)
    with TestClient(
        create_app(
            Settings(
                database_url=client.app.state.settings.database_url,
                public_url="http://testserver",
                cookie_secure=False,
            )
        )
    ) as theirs:
        join_as_staff(theirs, conn, app_conn, other)
        ok(keep(theirs, OTHER_KEY, OTHER_WEBHOOK))

        answer = report(theirs, other.pmc_id, payment, OTHER_WEBHOOK)

    assert ok(answer) == {"recorded": False}
    assert not paid(conn, payment)
    refused(report(client, uuid4(), payment, WEBHOOK), 404)


def test_a_payment_started_under_keys_since_changed_or_taken_away_still_reports_in(
    client, other_client, conn, app_conn, pmc, admin, lease, stripe
):
    ok(keep(client))
    payment = start_payment(client, other_client, conn, app_conn, pmc, lease, stripe)
    ok(keep(client, OTHER_KEY, OTHER_WEBHOOK))
    ok(client.post("/api/payments/keys/remove", json={}), 204)

    assert ok(report(other_client, pmc.pmc_id, payment, WEBHOOK)) == {"recorded": True}
    assert paid(conn, payment)


def test_taking_the_keys_away_goes_back_to_the_servers_partner(database_url, conn, app_conn, pmc):
    settings = Settings(
        database_url=database_url,
        public_url="http://testserver",
        cookie_secure=False,
        payments="fake",
    )
    with TestClient(create_app(settings)) as browser:
        join_as_staff(browser, conn, app_conn, pmc)
        assert ok(browser.get("/api/payments/keys"))["using"] == "server"
        ok(keep(browser))
        assert ok(browser.get("/api/payments/settings"))["partner"] == "stripe"

        ok(browser.post("/api/payments/keys/remove", json={}), 204)
        ok(browser.post("/api/payments/keys/remove", json={}), 204)  # again: nothing new

        shown = ok(browser.get("/api/payments/keys"))
        assert (shown["using"], shown["server_partner"], shown["ends_in"]) == (
            "server",
            "fake",
            None,
        )
        assert ok(browser.get("/api/payments/settings"))["partner"] == "fake"
    assert conn.execute(
        "SELECT count(*), count(*) FILTER (WHERE secret_key_sealed IS NULL)"
        " FROM trust_payment_keys WHERE pmc_id = %s",
        (pmc.pmc_id,),
    ).fetchone() == (2, 1)


def test_keys_that_dont_open_here_take_no_payment_and_never_fall_back(
    database_url, conn, app_conn, pmc, lease, monkeypatch
):
    settings = Settings(
        database_url=database_url,
        public_url="http://testserver",
        cookie_secure=False,
        payments="fake",
    )
    with TestClient(create_app(settings)) as browser, TestClient(create_app(settings)) as tenant:
        join_as_staff(browser, conn, app_conn, pmc)
        ok(keep(browser))
        ok(
            browser.post(
                "/api/payments/settings", json={"online": True, "ach_fee_payer": "company"}
            ),
            204,
        )
        # Another server's key: the company's keys don't open here.
        monkeypatch.setenv("PMS_DATA_KEY", base64.b64encode(os.urandom(32)).decode())

        assert ok(browser.get("/api/payments/keys"))["using"] == "unreadable"
        join_as_tenant(tenant, conn, app_conn, pmc)
        assert ok(tenant.get(f"/api/payments/leases/{lease}"))["why_not"] == "off"


def test_stripe_not_answering_through_the_companys_webhook_asks_for_the_report_again(
    client, other_client, conn, app_conn, pmc, admin, lease, stripe
):
    answers, _ = stripe
    ok(keep(client))
    payment = start_payment(client, other_client, conn, app_conn, pmc, lease, stripe)
    # Stripe can't say its fee yet: answered "send it again", nothing recorded.
    answers[("GET", "/payment_intents/pi_1")] = {"latest_charge": None}

    refused(
        report(other_client, pmc.pmc_id, payment, WEBHOOK),
        502,
        "the payments partner didn't answer: send it again",
    )
    assert not paid(conn, payment)


def test_a_bank_account_saved_for_autopay_reports_through_its_own_companys_webhook(
    client, other_client, conn, app_conn, pmc, admin, lease, stripe
):
    answers, _ = stripe
    ok(keep(client))
    ok(
        client.post("/api/payments/settings", json={"online": True, "ach_fee_payer": "company"}),
        204,
    )
    join_as_tenant(other_client, conn, app_conn, pmc)
    answers[("POST", "/customers")] = {"id": "cus_1"}
    answers[("POST", "/checkout/sessions")] = {"id": "cs_setup_1", "url": "https://checkout.test/s"}
    autopay = ok(
        other_client.post(
            f"/api/payments/leases/{lease}/autopay", json={"rule": "balance", "amount": "1500.00"}
        ),
        201,
    )["id"]
    answers[("GET", "/setup_intents/seti_1")] = {
        "status": "succeeded",
        "customer": "cus_1",
        "mandate": "mandate_1",
        "metadata": {"autopay_id": autopay},
        "payment_method": {
            "id": "pm_1",
            "us_bank_account": {"bank_name": "STRIPE TEST BANK", "last4": "6789"},
        },
    }
    subject = {"id": "cs_setup_1", "mode": "setup", "setup_intent": "seti_1"}
    other = make_pmc(conn)
    with TestClient(
        create_app(
            Settings(
                database_url=client.app.state.settings.database_url,
                public_url="http://testserver",
                cookie_secure=False,
            )
        )
    ) as theirs:
        join_as_staff(theirs, conn, app_conn, other)
        ok(keep(theirs, OTHER_KEY, OTHER_WEBHOOK))
        # Another company's webhook records none of this company's autopays.
        assert ok(
            signed(theirs, other.pmc_id, "checkout.session.completed", subject, OTHER_WEBHOOK)
        ) == {"recorded": False}

    assert ok(signed(other_client, pmc.pmc_id, "checkout.session.completed", subject, WEBHOOK)) == {
        "recorded": True
    }
    assert conn.execute(
        "SELECT partner_customer, label FROM trust_autopay_accounts WHERE autopay_id = %s",
        (autopay,),
    ).fetchone() == ("cus_1", "STRIPE TEST BANK ••••6789")
