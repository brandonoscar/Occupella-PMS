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

from pms import look_ups, payments
from pms.api import Settings, create_app, outbox, payment_keys
from pms.api import online_payments as op

DAY = timedelta(days=1)


def on_this_day():
    """Today, worked out again before each test (tests/conftest.py), so a run that crosses
    midnight UTC compares with the database's today, never the day the module loaded."""
    global TODAY
    TODAY = datetime.now(UTC).date()


on_this_day()
# Made-up keys, never a real account's.
SECRET_KEY = "sk_" + "test_" + "Synthetic000000000000A1b2"
OTHER_KEY = "sk_" + "live_" + "Synthetic000000000000Z9y8"
WEBHOOK = "whsec_" + "Synthetic0000000000000001"
OTHER_WEBHOOK = "whsec_" + "Synthetic0000000000000002"


@pytest.fixture(autouse=True)
def data_key(monkeypatch):
    monkeypatch.setenv("PMS_DATA_KEY", base64.b64encode(os.urandom(32)).decode())


def hooks_of(conn):
    """A Stripe account's webhooks, one to each company's own address, sent every event: what
    keys kept here are checked against (docs/edge-cases.md P-007) unless a test says otherwise."""
    return {
        "data": [
            {
                "url": f"http://testserver/api/payments/webhook/stripe/{pmc_id}",
                "status": "enabled",
                "enabled_events": list(payments.STRIPE_EVENTS),
            }
            for (pmc_id,) in conn.execute("SELECT pmc_id FROM trust_pmcs").fetchall()
        ]
    }


@pytest.fixture(autouse=True)
def stripe(monkeypatch, conn):
    """Stripe's answers, by method and path, and each call made, with the key it was made with.
    Unless a test answers them, Stripe takes the keys and their account has a webhook here."""
    answers, calls = {}, []

    def call(self, method, path, form, key=""):
        calls.append((self._key, method, path))
        answer = answers.get((method, path))
        if answer is None and (method, path) == ("GET", "/account"):
            return {"id": "acct_Synthetic0001", "charges_enabled": True}
        if answer is None and (method, path) == ("GET", "/webhook_endpoints"):
            return hooks_of(conn)
        if isinstance(answer, Exception):
            raise answer
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
        "charge.dispute.closed",
        "charge.dispute.created",
        "charge.dispute.funds_reinstated",
        "charge.failed",
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


# --- what Stripe says of them (docs/edge-cases.md P-006, P-007) ------------------------------


def test_keys_stripe_doesnt_take_are_never_kept_and_say_why(client, conn, pmc, admin, stripe):
    answers, _ = stripe
    answers[("GET", "/account")] = payments.PartnerFailed("HTTP 401", status=401)
    refused(keep(client), 422, payments.KEY_REFUSED)
    answers[("GET", "/account")] = payments.PartnerFailed("HTTP 403", status=403)
    refused(keep(client), 422, payments.KEY_LOCKED)
    # Stripe didn't answer: not kept either, to be tried again.
    answers[("GET", "/account")] = payments.PartnerFailed("timed out")
    refused(keep(client), 502, payments.UNREACHABLE)
    assert ok(client.get("/api/payments/keys"))["using"] == "none"
    assert not conn.execute(
        "SELECT 1 FROM trust_payment_keys WHERE pmc_id = %s", (pmc.pmc_id,)
    ).fetchone()


def test_a_key_of_an_account_with_no_webhook_here_is_never_kept(client, conn, pmc, admin, stripe):
    # A secret key and a webhook secret from two accounts: the key's account sends nothing here.
    answers, _ = stripe
    answers[("GET", "/webhook_endpoints")] = {
        "data": [{"url": "https://elsewhere.example.org/hook", "status": "enabled"}]
    }
    url = f"http://testserver/api/payments/webhook/stripe/{pmc.pmc_id}"
    refused(keep(client), 422, payments.NO_WEBHOOK.format(url=url))
    assert ok(client.get("/api/payments/keys"))["using"] == "none"


def test_company_setup_says_in_words_what_stripe_says_is_wrong_with_the_keys_in_use(
    client, conn, pmc, admin, stripe, monkeypatch
):
    answers, calls = stripe
    hooks = hooks_of(conn)
    for hook in hooks["data"]:
        hook["enabled_events"] = ["checkout.session.completed"]
    answers[("GET", "/webhook_endpoints")] = hooks
    kept = ok(keep(client))
    # Kept, as nothing blocks payments, and the events the webhook isn't sent are named.
    assert kept["using"] == "own"
    assert kept["stripe_says"] == [
        payments.MISSING_EVENTS.format(
            events=", ".join(e for e in payments.STRIPE_EVENTS if e != "checkout.session.completed")
        )
    ]
    # Later the key is rolled in Stripe: Company setup says so once it asks again, not "own" alone.
    answers[("GET", "/account")] = payments.PartnerFailed("HTTP 401", status=401)
    asked = len(calls)  # asked lately: not again yet
    assert ok(client.get("/api/payments/keys"))["stripe_says"] == kept["stripe_says"]
    assert len(calls) == asked
    monkeypatch.setattr(payment_keys, "CHECKED_FOR", -1.0)
    shown = ok(client.get("/api/payments/keys"))
    assert (shown["using"], shown["stripe_says"]) == ("own", [payments.KEY_REFUSED])
    # And an account Stripe stopped taking payments on.
    answers[("GET", "/account")] = {"id": "acct_Synthetic0001", "charges_enabled": False}
    answers[("GET", "/webhook_endpoints")] = hooks_of(conn)
    assert ok(client.get("/api/payments/keys"))["stripe_says"] == [payments.CHARGES_OFF]


def test_staff_who_arent_admins_see_what_stripe_says_too(
    client, other_client, conn, app_conn, pmc, admin, stripe
):
    ok(keep(client))
    join_as_staff(other_client, conn, app_conn, pmc)
    assert ok(other_client.get("/api/payments/keys"))["stripe_says"] == []


# --- reports refused (docs/edge-cases.md P-010, P-011) -------------------------------------


def signed_at(client, pmc_id, payment, secret, when):
    """Stripe's report that the bank paid `payment`, signed with `secret` at `when` (seconds)."""
    subject = {"id": "cs_1", "client_reference_id": payment, "payment_intent": "pi_1"}
    body = json.dumps(
        {
            "id": f"evt_{uuid4().hex}",
            "type": "checkout.session.async_payment_succeeded",
            "created": when,
            "livemode": False,
            "data": {"object": subject},
        }
    ).encode()
    mac = hmac.new(secret.encode(), f"{when}.".encode() + body, hashlib.sha256).hexdigest()
    return client.post(
        f"/api/payments/webhook/stripe/{pmc_id}",
        content=body,
        headers={"stripe-signature": f"t={when},v1={mac}", "content-type": "application/json"},
    )


def refused_on(conn, pmc):
    """The company's day its first refusal was recorded on: what Company setup names."""
    return conn.execute(
        "SELECT min(day) FROM trust_payment_report_refusals WHERE pmc_id = %s", (pmc.pmc_id,)
    ).fetchone()[0]


def refusals(conn, pmc):
    return conn.execute(
        "SELECT reason, drift_seconds FROM trust_payment_report_refusals WHERE pmc_id = %s"
        " ORDER BY reason",
        (pmc.pmc_id,),
    ).fetchall()


def test_reports_signed_with_a_secret_the_company_doesnt_have_are_told_to_company_setup(
    client, other_client, conn, app_conn, pmc, admin, lease, stripe
):
    # P-011: the endpoint was recreated in Stripe, its new secret never entered here.
    ok(keep(client))
    payment = start_payment(client, other_client, conn, app_conn, pmc, lease, stripe)
    new_secret = "whsec_" + "Synthetic0000000000000009"

    refused(report(other_client, pmc.pmc_id, payment, new_secret), 400)
    refused(report(other_client, pmc.pmc_id, payment, new_secret), 400)  # once a day

    assert refusals(conn, pmc) == [("signature", None)]
    said = op.SIGNATURE_REFUSED.format(day=f"{refused_on(conn, pmc):%m/%d/%Y}")
    assert ok(client.get("/api/payments/keys"))["stripe_says"] == [said]
    # A stranger's post naming no payment of the company writes nothing.
    other = report(other_client, pmc.pmc_id, str(uuid4()), new_secret)
    refused(other, 400)
    assert refusals(conn, pmc) == [("signature", None)]
    # The right secret entered, a report lands, and Company setup says nothing more of it.
    assert ok(report(other_client, pmc.pmc_id, payment, WEBHOOK)) == {"recorded": True}
    assert ok(client.get("/api/payments/keys"))["stripe_says"] == []


def test_whose_reports_are_refused_is_read_from_the_payment_or_autopay_they_name(
    conn, app_conn, pmc, lease
):
    # P-011: an unsigned report is read only to say whose reports are being refused.
    tenant = tenant_of(conn, pmc)
    autopay = conn.execute(
        "INSERT INTO trust_autopay (pmc_id, lease_id, tenant_id, rule, amount, partner)"
        " VALUES (%s, %s, %s, 'fixed', 100.00, 'stripe') RETURNING id",
        (pmc.pmc_id, lease, tenant),
    ).fetchone()[0]

    def about(subject):
        return json.dumps({"data": {"object": subject}}).encode()

    saved = about({"id": "seti_1", "metadata": {"autopay_id": str(autopay)}})
    nobodys = about({"id": "seti_2", "metadata": {"autopay_id": str(uuid4())}})
    assert op.named_company(app_conn, saved) == pmc.pmc_id
    assert op.named_company(app_conn, nobodys) is None
    # A payment of none of the companies, a report naming nothing, and bodies that aren't one.
    assert op.named_company(app_conn, about({"client_reference_id": str(uuid4())})) is None
    assert op.named_company(app_conn, about({"id": "cs_1"})) is None
    assert op.named_company(app_conn, about({"metadata": {"autopay_id": "not-an-id"}})) is None
    assert op.named_company(app_conn, b"not json") is None
    assert op.named_company(app_conn, json.dumps({"data": []}).encode()) is None


def test_a_report_to_the_servers_own_webhook_refused_is_kept_for_the_company_it_names(
    database_url, conn, app_conn, pmc, lease, monkeypatch, stripe
):
    # P-011 on a server whose own keys take its one company's payments (PMS_PAYMENTS=stripe).
    monkeypatch.setattr(payments, "many_companies", lambda conn: False)
    monkeypatch.setenv("PMS_STRIPE_SECRET_KEY", "sk_" + "test_" + "Synthetic000000000000Srv1")
    monkeypatch.setenv("PMS_STRIPE_WEBHOOK_SECRET", WEBHOOK)
    settings = Settings(
        database_url=database_url,
        public_url="http://testserver",
        cookie_secure=False,
        payments="stripe",
    )
    with TestClient(create_app(settings)) as staff, TestClient(create_app(settings)) as tenant:
        join_as_staff(staff, conn, app_conn, pmc)
        payment = start_payment(staff, tenant, conn, app_conn, pmc, lease, stripe)
        subject = {"id": "cs_1", "client_reference_id": payment, "payment_intent": "pi_1"}
        body = json.dumps(
            {
                "id": "evt_1",
                "type": "checkout.session.async_payment_succeeded",
                "created": int(time.time()),
                "livemode": False,
                "data": {"object": subject},
            }
        ).encode()
        forged = staff.post(
            "/api/payments/webhook/stripe",
            content=body,
            headers={"stripe-signature": "t=1,v1=00", "content-type": "application/json"},
        )

    refused(forged, 400, op.REFUSED_WORDS["signature"])
    assert refusals(conn, pmc) == [("signature", None)]


def test_reports_refused_because_the_servers_clock_is_off_say_so(
    client, other_client, conn, app_conn, pmc, admin, lease, stripe
):
    # P-010: signed with the company's own secret, ten minutes before the server's clock.
    ok(keep(client))
    payment = start_payment(client, other_client, conn, app_conn, pmc, lease, stripe)

    refused(signed_at(other_client, pmc.pmc_id, payment, WEBHOOK, int(time.time()) - 600), 400)

    ((reason, drift),) = refusals(conn, pmc)
    assert reason == "clock" and 595 <= drift <= 660
    said = ok(client.get("/api/payments/keys"))["stripe_says"]
    assert said == [
        op.CLOCK_REFUSED.format(day=f"{refused_on(conn, pmc):%m/%d/%Y}", minutes=drift // 60)
    ]
    assert not paid(conn, payment)


def test_a_refused_report_answers_in_fixed_words_never_the_errors_own(
    client, other_client, conn, pmc, admin
):
    # Signed with the company's own secret, but not JSON: the reader's own message (a library's)
    # never reaches whoever sent it (CodeQL, information exposure through an exception).
    ok(keep(client))
    when = int(time.time())
    body = b"<not json at all>"
    mac = hmac.new(WEBHOOK.encode(), f"{when}.".encode() + body, hashlib.sha256).hexdigest()
    answer = other_client.post(
        f"/api/payments/webhook/stripe/{pmc.pmc_id}",
        content=body,
        headers={"stripe-signature": f"t={when},v1={mac}", "content-type": "application/json"},
    )
    refused(answer, 400, op.REFUSED_WORDS["signature"])
    late = signed_at(other_client, pmc.pmc_id, str(uuid4()), WEBHOOK, when - 600)
    refused(late, 400, op.REFUSED_WORDS["clock"])


def test_a_report_refused_emails_each_admin_once_a_day_while_the_server_sends_email(
    client, conn, app_conn, pmc, admin
):
    token = outbox.SENDING.set(True)
    try:
        with app_conn.transaction():
            for _ in range(2):
                op.refused_report(app_conn, pmc.pmc_id, payments.NotTheirs("not this company's"))
    finally:
        outbox.SENDING.reset(token)
    alerts = conn.execute(
        "SELECT a.kind, e.subject FROM trust_admin_alerts AS a"
        " JOIN trust_emails AS e ON e.alert_id = a.id WHERE a.pmc_id = %s",
        (pmc.pmc_id,),
    ).fetchall()
    assert alerts == [
        ("payment_reports_refused", f"{pmc_name(conn, pmc)}: payments aren't reaching the books")
    ]


def pmc_name(conn, pmc):
    return conn.execute(
        "SELECT display_name FROM trust_pmcs WHERE pmc_id = %s", (pmc.pmc_id,)
    ).fetchone()[0]


def test_a_payment_reported_while_the_servers_data_key_changed_lands_once_keys_are_entered_again(
    client, other_client, conn, app_conn, pmc, admin, lease, stripe, monkeypatch, database_url
):
    """docs/edge-cases.md P-051, H-055: with PMS_DATA_KEY lost or changed the company's keys don't
    open, its webhook answers 404 (Stripe sends again, then gives up) and Company setup says to
    enter them again; once entered, the nightly look-up asks Stripe of the payment and books it."""
    answers, _ = stripe
    ok(keep(client))
    payment = start_payment(client, other_client, conn, app_conn, pmc, lease, stripe)
    monkeypatch.setenv("PMS_DATA_KEY", base64.b64encode(os.urandom(32)).decode())

    refused(report(other_client, pmc.pmc_id, payment, WEBHOOK), 404)
    assert ok(client.get("/api/payments/keys"))["using"] == "unreadable"

    ok(keep(client))
    ref = conn.execute(
        "SELECT partner_ref FROM trust_online_payments WHERE id = %s", (payment,)
    ).fetchone()[0]
    answers[("GET", f"/checkout/sessions/{ref}")] = {
        "id": ref,
        "client_reference_id": payment,
        "status": "complete",
        "payment_intent": {
            "id": "pi_1",
            "status": "succeeded",
            "metadata": {"payment_id": payment},
            "latest_charge": {"balance_transaction": {"fee": 800, "created": int(time.time())}},
        },
    }
    with psycopg.connect(database_url, autocommit=True) as nightly:
        nightly.execute("SET ROLE trust_app")
        looked = look_ups.look_up_waiting(
            nightly, payments.Resolver(None), [pmc.pmc_id], datetime.now(UTC) + 8 * DAY
        )

    assert (looked.asked, looked.recorded) == (1, 1)
    assert paid(conn, payment)


# --- paying through them -------------------------------------------------------------------


def test_the_companys_payments_go_through_its_own_account_and_its_own_webhook(
    client, other_client, conn, app_conn, pmc, admin, lease, stripe
):
    _, calls = stripe
    ok(keep(client))
    # Stripe was asked of the keys before they were kept (docs/edge-cases.md P-006, P-007).
    assert calls == [(SECRET_KEY, "GET", "/account"), (SECRET_KEY, "GET", "/webhook_endpoints")]

    payment = start_payment(client, other_client, conn, app_conn, pmc, lease, stripe)

    assert calls[2] == (SECRET_KEY, "POST", "/checkout/sessions")
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
    # The account the company's keys act for, noted with the bank account (P-065).
    answers[("GET", "/account")] = {"id": "acct_1Company", "object": "account"}
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
    assert conn.execute(
        "SELECT partner_account, noted FROM trust_autopay_account_homes WHERE autopay_id = %s",
        (autopay,),
    ).fetchone() == ("acct_1Company", "saved")
