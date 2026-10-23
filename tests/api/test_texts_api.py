"""Texts from the API (src/pms/api/texts.py, migration 20261022000161): staff record a person's
word and text them from their Texts tab, the company's settings for the texts the PMS writes by
itself, a tenant's own word in the portal, Run rent now texting reminders, and Twilio's signed
replies. Every number is a fictional 555-01xx one, and nothing reaches Twilio."""

import base64
import hashlib
import hmac
import urllib.parse
from datetime import UTC, date, datetime, timedelta

import pytest
from helpers import add_unit, fresh_phone, open_lease
from people import join_as_owner, join_as_staff, join_as_tenant, tenant_of

from pms import texts

PUBLIC_URL = "http://testserver"


def typed(phone: str) -> str:
    return f"({phone[2:5]}) {phone[5:8]}-{phone[8:]}"


class Kept:
    name = "fake"

    def __init__(self):
        self.sent: list[tuple[str, str]] = []

    def send(self, to, body):
        self.sent.append((to, body))
        return f"fake-{len(self.sent)}"


@pytest.fixture
def staff(client, conn, app_conn, pmc):
    return join_as_staff(client, conn, app_conn, pmc, name="Writer 1")


@pytest.fixture
def kept(client):
    sender = Kept()
    client.app.state.texts = sender
    return sender


def ok(answer, status=200):
    assert answer.status_code == status, answer.text
    return answer.json() if answer.content else None


def word(client, about, about_id, phone, consented=True, how="in writing"):
    return client.post(
        "/api/texts/consent",
        json={
            "about": about,
            "about_id": str(about_id),
            "phone": phone,
            "consented": consented,
            "how": how,
        },
    )


def write(client, about, about_id, text="The water is off from 9 to 11 today."):
    return client.post("/api/texts", json={"about": about, "about_id": str(about_id), "text": text})


def listed(client, about, about_id):
    return ok(client.get("/api/texts", params={"about": about, "about_id": str(about_id)}))


# --- staff ----------------------------------------------------------------------------------------


def test_staff_record_a_tenants_word_and_text_them_and_it_goes(client, conn, staff, pmc, kept):
    tenant = tenant_of(conn, pmc)
    number = fresh_phone()
    ok(client.post(f"/api/tenants/{tenant}/contact", json={"phone": typed(number)}), 204)
    start = listed(client, "tenant", tenant)
    assert (start["sending"], start["consent"], start["phone_on_file"], start["texts"]) == (
        True,
        None,
        number,
        [],
    )
    refused = write(client, "tenant", tenant)
    assert refused.status_code == 409 and "hasn't agreed" in refused.json()["detail"]

    after = ok(word(client, "tenant", tenant, typed(number)), 201)
    assert {k: after["consent"][k] for k in ("phone", "consented", "how", "by")} == {
        "phone": number,
        "consented": True,
        "how": "in writing",
        "by": "Writer 1",
    }
    sent = ok(write(client, "tenant", tenant), 201)
    assert sent["to"] == number
    company = conn.execute(
        "SELECT display_name FROM trust_pmcs WHERE pmc_id = %s", (pmc.pmc_id,)
    ).fetchone()[0]
    body = f"{company}: The water is off from 9 to 11 today. - Writer 1. Reply STOP to stop."
    assert [b for to, b in kept.sent if to == number] == [body]  # went after the request
    (line,) = listed(client, "tenant", tenant)["texts"]
    assert (line["kind"], line["to"], line["body"], line["by"], line["status"]) == (
        "message",
        number,
        body,
        "Writer 1",
        "sent",
    )


def test_a_person_who_said_no_is_not_texted_and_the_same_word_again_writes_nothing(
    client, conn, staff, pmc, kept
):
    vendor = pmc.vendor_id
    number = fresh_phone()
    ok(word(client, "vendor", vendor, number, consented=True, how="verbally"), 201)
    ok(word(client, "vendor", vendor, number, consented=True, how="verbally"), 201)
    ok(word(client, "vendor", vendor, number, consented=False, how="verbally"), 201)
    assert (
        conn.execute(
            "SELECT count(*) FROM trust_text_consents WHERE vendor_id = %s", (vendor,)
        ).fetchone()[0]
        == 2
    )
    assert write(client, "vendor", vendor).status_code == 409
    owner = pmc.owners[0].owner_id
    ok(word(client, "owner", owner, number), 201)
    ok(write(client, "owner", owner), 201)


def test_a_text_too_long_with_the_companys_name_is_refused(client, conn, staff, pmc, kept):
    tenant = tenant_of(conn, pmc)
    ok(word(client, "tenant", tenant, fresh_phone()), 201)
    ok(write(client, "tenant", tenant, "x" * 300), 201)
    conn.execute(
        "UPDATE trust_pmcs SET display_name = %s WHERE pmc_id = %s", ("P" * 200, pmc.pmc_id)
    )
    refused = write(client, "tenant", tenant, "x" * 300)
    assert refused.status_code == 422 and "too long to text" in refused.json()["detail"]


def test_without_a_text_sender_staff_record_words_but_write_no_text(client, conn, staff, pmc):
    tenant = tenant_of(conn, pmc)
    ok(word(client, "tenant", tenant, fresh_phone()), 201)
    assert listed(client, "tenant", tenant)["sending"] is False
    refused = write(client, "tenant", tenant)
    assert refused.status_code == 409 and refused.json()["detail"] == "this server sends no texts"


def test_another_companys_person_is_not_found(client, conn, app_conn, staff, kept):
    from helpers import make_pmc

    other = make_pmc(conn)
    stranger = tenant_of(conn, other)
    assert word(client, "tenant", stranger, fresh_phone()).status_code == 404
    assert write(client, "tenant", stranger).status_code == 404
    assert (
        client.get("/api/texts", params={"about": "tenant", "about_id": str(stranger)}).status_code
        == 404
    )


@pytest.mark.parametrize("caller", ["nobody", "owner", "tenant"])
def test_only_staff_read_or_write_texts(client, conn, app_conn, pmc, caller):
    if caller == "owner":
        join_as_owner(client, app_conn, pmc)
    elif caller == "tenant":
        join_as_tenant(client, conn, app_conn, pmc)
    status = 401 if caller == "nobody" else 403
    tenant = tenant_of(conn, pmc)
    assert (
        client.get("/api/texts", params={"about": "tenant", "about_id": str(tenant)}).status_code
        == status
    )
    assert word(client, "tenant", tenant, fresh_phone()).status_code == status
    assert write(client, "tenant", tenant).status_code == status
    assert client.get("/api/texts/settings").status_code == status
    assert client.post("/api/texts/settings", json={"late_notices": True}).status_code == status


def test_a_custom_role_without_email_may_not_text(client, other_client, conn, app_conn, staff, pmc):
    clerk = join_as_staff(other_client, conn, app_conn, pmc, name="Clerk 1", roles=[])
    role = ok(client.post("/api/custom-roles", json={"name": "Books", "areas": ["rent"]}), 201)
    ok(
        client.post(
            f"/api/custom-roles/{role['id']}/staff",
            json={"staff_id": str(clerk.person_id), "granted": True},
        ),
        204,
    )
    tenant = tenant_of(conn, pmc)
    refused = word(other_client, "tenant", tenant, fresh_phone())
    assert refused.status_code == 403 and "email to tenants" in refused.json()["detail"]
    assert other_client.get("/api/texts/settings").status_code == 200  # reads stay open


NOWHERE = "00000000-0000-0000-0000-000000000000"


@pytest.mark.parametrize(
    "body",
    [
        {"about": "tenant", "about_id": NOWHERE, "text": " "},
        {"about": "tenant", "about_id": NOWHERE, "text": "x" * 301},
        {"about": "tenant", "about_id": NOWHERE, "text": "a\u0000b"},
        {"about": "staff", "about_id": NOWHERE, "text": "x"},
        {"about": "tenant", "about_id": "not-an-id", "text": "x"},
    ],
)
def test_bad_text_is_refused(client, staff, body):
    assert client.post("/api/texts", json=body).status_code == 422


def test_a_bad_word_is_refused(client, conn, staff, pmc):
    tenant = tenant_of(conn, pmc)
    for phone in ("555-0123", "(102) 555-0123", "+44 20 5550 1234"):
        refused = word(client, "tenant", tenant, phone)
        assert refused.status_code == 422 and "US mobile number" in refused.json()["detail"]
    assert word(client, "tenant", tenant, fresh_phone(), how="portal").status_code == 422
    assert word(client, "tenant", tenant, fresh_phone(), how="stop reply").status_code == 422
    assert (
        client.get("/api/texts", params={"about": "bill", "about_id": NOWHERE}).status_code == 422
    )


# --- what the PMS texts by itself -----------------------------------------------------------------


def test_staff_set_which_texts_the_pms_writes_and_run_rent_now_writes_them(
    client, conn, staff, pmc, kept
):
    assert ok(client.get("/api/texts/settings")) == {
        "rent_reminder_days": None,
        "late_notices": False,
        "sending": True,
    }
    wanted = {"rent_reminder_days": 15, "late_notices": True}
    assert ok(client.post("/api/texts/settings", json=wanted)) == {**wanted, "sending": True}
    ok(client.post("/api/texts/settings", json=wanted))
    assert (
        conn.execute(
            "SELECT count(*) FROM trust_text_settings WHERE pmc_id = %s", (pmc.pmc_id,)
        ).fetchone()[0]
        == 1
    )
    for bad in ({"rent_reminder_days": 0}, {"rent_reminder_days": 16}, {"late_notices": "x"}):
        assert client.post("/api/texts/settings", json=bad).status_code == 422

    # A lease due on the first of next month, its tenant agreed: Run rent now texts the
    # reminder, in the afternoon (UTC), and only once.
    tenant = tenant_of(conn, pmc)
    today = datetime.now(UTC).date()
    first_next = (today.replace(day=1) + timedelta(days=32)).replace(day=1)
    unit = add_unit(conn, pmc.pmc_id, pmc.owners[0].property_id)
    open_lease(conn, pmc.pmc_id, unit, today.replace(day=1), None, "1200.00", [tenant])
    ok(word(client, "tenant", tenant, fresh_phone()), 201)
    run = {"due_on": str(today.replace(day=1)), "late_fees_as_of": str(today)}
    done = ok(client.post("/api/rent/run", json=run))
    days_left = (first_next - today).days
    assert done["texts"] == (1 if days_left <= 15 else 0)
    again = ok(client.post("/api/rent/run", json=run))
    assert again["texts"] == 0
    reminders = conn.execute(
        "SELECT period, not_before FROM trust_texts WHERE pmc_id = %s AND kind = 'rent_reminder'",
        (pmc.pmc_id,),
    ).fetchall()
    assert [period for period, _ in reminders] == ([first_next] if days_left <= 15 else [])
    for _, not_before in reminders:
        assert 16 <= not_before.astimezone(UTC).hour <= 23


# --- a tenant's own word --------------------------------------------------------------------------


def test_a_tenant_says_yes_or_no_to_texts_in_the_portal(client, conn, app_conn, pmc):
    join_as_tenant(client, conn, app_conn, pmc)
    tenant = tenant_of(conn, pmc)
    number = fresh_phone()
    mine = ok(client.get("/api/tenant/texts"))
    assert mine == {"sending": False, "consent": None, "phone_on_file": None}
    said = ok(
        client.post("/api/tenant/texts", json={"phone": typed(number), "consented": True}), 201
    )
    assert {k: said["consent"][k] for k in ("phone", "consented", "how", "by")} == {
        "phone": number,
        "consented": True,
        "how": "portal",
        "by": None,
    }
    ok(client.post("/api/tenant/texts", json={"phone": number, "consented": True}), 201)
    ok(client.post("/api/tenant/texts", json={"phone": number, "consented": False}), 201)
    rows = conn.execute(
        "SELECT consented, how, by_person, recorded_by FROM trust_text_consents"
        " WHERE tenant_id = %s ORDER BY created_at",
        (tenant,),
    ).fetchall()
    assert rows == [(True, "portal", True, None), (False, "portal", True, None)]
    refused = client.post("/api/tenant/texts", json={"phone": "12345", "consented": True})
    assert refused.status_code == 422
    assert client.post("/api/tenant/texts", json={"consented": True}).status_code == 422


@pytest.mark.parametrize("caller", ["nobody", "owner", "staff"])
def test_only_a_tenant_keeps_their_own_word(client, conn, app_conn, pmc, caller):
    if caller == "owner":
        join_as_owner(client, app_conn, pmc)
    elif caller == "staff":
        join_as_staff(client, conn, app_conn, pmc)
    status = 401 if caller == "nobody" else 403
    assert client.get("/api/tenant/texts").status_code == status
    answer = client.post("/api/tenant/texts", json={"phone": fresh_phone(), "consented": True})
    assert answer.status_code == status


# --- Twilio's replies -----------------------------------------------------------------------------

TOKEN = "synthetic-auth-token"


@pytest.fixture
def twilio(client):
    client.app.state.texts = texts.TwilioSender("AC0000", TOKEN, "+12025550100")
    return client


def signed(form, url=f"{PUBLIC_URL}/api/twilio/webhook", token=TOKEN):
    data = url + "".join(name + form[name] for name in sorted(form))
    return base64.b64encode(hmac.new(token.encode(), data.encode(), hashlib.sha1).digest()).decode()


def post_reply(client, form, signature=None):
    return client.post(
        "/api/twilio/webhook",
        content=urllib.parse.urlencode(form),
        headers={
            "content-type": "application/x-www-form-urlencoded",
            "x-twilio-signature": signed(form) if signature is None else signature,
        },
    )


def test_a_stop_reply_from_twilio_stops_texts_and_start_starts_them(twilio, conn, pmc):
    tenant = tenant_of(conn, pmc)
    number = fresh_phone()
    texts.record_consent(conn, pmc.pmc_id, "tenant", tenant, number, True, "in writing")
    form = {"MessageSid": "SM0001", "From": number, "To": "+12025550100", "Body": "STOP"}
    answer = post_reply(twilio, form)
    assert answer.status_code == 200, answer.text
    assert answer.headers["content-type"].startswith("text/xml")
    assert answer.text == '<?xml version="1.0" encoding="UTF-8"?><Response/>'
    assert texts.consent_of(conn, pmc.pmc_id, "tenant", tenant).how == "stop reply"
    assert post_reply(twilio, {**form, "Body": "Start"}).status_code == 200
    assert texts.consent_of(conn, pmc.pmc_id, "tenant", tenant).consented
    # Anything else is not kept.
    assert post_reply(twilio, {**form, "Body": "Is the pool open?"}).status_code == 200
    assert texts.consent_of(conn, pmc.pmc_id, "tenant", tenant).how == "start reply"


def test_a_reply_not_signed_by_twilio_is_refused(twilio, conn, pmc):
    tenant = tenant_of(conn, pmc)
    number = fresh_phone()
    texts.record_consent(conn, pmc.pmc_id, "tenant", tenant, number, True, "in writing")
    form = {"From": number, "Body": "STOP"}
    for signature in ("", signed(form, token="another-token"), signed({**form, "Body": "x"})):
        assert post_reply(twilio, form, signature).status_code == 403
    assert texts.consent_of(conn, pmc.pmc_id, "tenant", tenant).consented
    # JSON to it is still refused unsigned; a form elsewhere is still not taken.
    assert twilio.post("/api/twilio/webhook", json=form).status_code == 403
    elsewhere = twilio.post(
        "/api/texts/settings",
        content="late_notices=true",
        headers={"content-type": "application/x-www-form-urlencoded"},
    )
    assert elsewhere.status_code == 415


def test_without_twilio_the_webhook_is_not_found(client):
    form = {"From": fresh_phone(), "Body": "STOP"}
    assert post_reply(client, form).status_code == 404
    client.app.state.texts = Kept()
    assert post_reply(client, form).status_code == 404


def test_a_reply_from_a_number_that_isnt_one_changes_nothing(twilio):
    answer = post_reply(twilio, {"From": "+442071234567", "Body": "STOP"})
    assert answer.status_code == 200


def test_the_nightly_run_texts_only_while_the_server_sends_texts(conn, pmc):
    from pms import nightly

    tenant = tenant_of(conn, pmc)
    unit = add_unit(conn, pmc.pmc_id, pmc.owners[0].property_id)
    open_lease(conn, pmc.pmc_id, unit, date(2026, 1, 1), None, "1500.00", [tenant])
    texts.record_consent(conn, pmc.pmc_id, "tenant", tenant, fresh_phone(), True, "in writing")
    conn.execute(
        "INSERT INTO trust_text_settings (pmc_id, rent_reminder_days, late_notices)"
        " VALUES (%s, 5, false)",
        (pmc.pmc_id,),
    )
    quiet = nightly.run_pmc(conn, pmc.pmc_id, date(2026, 2, 27), PUBLIC_URL)
    assert quiet.texts == 0
    texted = nightly.run_pmc(conn, pmc.pmc_id, date(2026, 2, 27), PUBLIC_URL, texting=True)
    assert texted.texts == 1


def test_the_api_hands_the_pmss_own_texts_over_in_a_thread_while_it_sends_texts(
    monkeypatch, database_url
):
    import threading

    from fastapi.testclient import TestClient

    from pms.api import Settings, create_app

    def running():
        return [t for t in threading.enumerate() if t.name == "pms-texts"]

    with TestClient(create_app(Settings(database_url=database_url, text_sender="fake"))):
        assert len(running()) == 1
    assert running() == []
    with TestClient(create_app(Settings(database_url=database_url))):
        assert running() == []

    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.setenv("PMS_TEXT_SENDER", "fake")
    assert Settings.from_env().text_sender == "fake"
    monkeypatch.setenv("PMS_TEXT_SENDER", "twilio")
    with pytest.raises(ValueError, match="PMS_TEXT_SENDER=twilio needs"):
        create_app(Settings.from_env())
