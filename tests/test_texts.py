"""The texts module (src/pms/texts.py, migration 20261023000204): numbers as they are typed, the
afternoon window the PMS's own texts wait for, the senders (Twilio's request and signature, NOT
VERIFIED against Twilio itself), writing a text only under a yes, delivery once with each attempt
recorded, STOP and START replies, and the rent reminders and late notices a company texts. Every
number is a fictional 555-01xx one."""

import io
import json
import re
import urllib.error
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from helpers import (
    add_late_fee_policy,
    add_staff,
    add_unit,
    charge,
    fresh_phone,
    make_pmc,
    open_lease,
)

from pms import texts

ONE = "+12025550123"
NOON = datetime(2026, 3, 2, 17, 0, tzinfo=UTC)


@pytest.fixture
def pmc(conn):
    return make_pmc(conn)


def tenant_of(conn, pmc):
    return conn.execute(
        "SELECT tenant_id FROM trust_ledger_accounts WHERE ledger_account_id = %s",
        (pmc.prepaid_rent,),
    ).fetchone()[0]


class Kept:
    name = "fake"

    def __init__(self, refuse=None):
        self.sent: list[tuple[str, str]] = []
        self.refuse = refuse

    def send(self, to, body):
        if self.refuse:
            raise self.refuse
        self.sent.append((to, body))
        return f"fake-{len(self.sent)}"


def deliveries(conn, text_id):
    return conn.execute(
        "SELECT sender, provider_id, error FROM trust_text_deliveries WHERE text_id = %s"
        " ORDER BY id",
        (text_id,),
    ).fetchall()


# --- numbers and the window --------------------------------------------------------------------


@pytest.mark.parametrize(
    ("typed", "phone"),
    [
        ("(202) 555-0123", ONE),
        ("202.555.0123", ONE),
        ("1 202 555 0123", ONE),
        ("+1 (202) 555-0123", ONE),
        ("555-0123", None),
        ("(102) 555-0123", None),
        ("(202) 155-0123", None),
        ("+44 20 5550 1234", None),
        ("", None),
    ],
)
def test_a_us_mobile_number_is_read_however_it_was_typed(typed, phone):
    assert texts.us_mobile(typed) == phone
    if phone:
        assert texts.shown(phone) == "(202) 555-0123"


def utc(month, day, hour, minute=0):
    return datetime(2026, month, day, hour, minute, tzinfo=UTC)


@pytest.mark.parametrize(
    ("now", "zones", "at"),
    [
        # Winter, the continental US: daytime everywhere from 9 am Pacific to 8 pm Eastern.
        (utc(3, 2, 6), texts.CONTINENTAL, utc(3, 2, 17)),
        (utc(3, 2, 17), texts.CONTINENTAL, utc(3, 2, 17)),
        (utc(3, 3, 0, 59), texts.CONTINENTAL, utc(3, 3, 0, 59)),
        (utc(3, 3, 1), texts.CONTINENTAL, utc(3, 3, 17)),
        # Summer: 9 am Pacific is 16:00 UTC, 8 pm Eastern midnight.
        (utc(7, 1, 12), texts.CONTINENTAL, utc(7, 1, 16)),
        # One zone: a home in California waits for 9 am there; one in Hawaii for 9 am Honolulu.
        (utc(3, 2, 6), texts.ZONES["CA"], utc(3, 2, 17)),
        (utc(3, 2, 6), texts.ZONES["HI"], utc(3, 2, 19)),
        (utc(3, 2, 15), texts.ZONES["NY"], utc(3, 2, 15)),
        # A state in two zones waits for the later one's morning (Texas: Central and Mountain).
        (utc(3, 2, 15, 30), texts.ZONES["TX"], utc(3, 2, 16)),
    ],
)
def test_the_pmss_own_texts_wait_for_daytime_where_the_home_is(now, zones, at):
    assert texts.next_in_window(now, zones) == at


def test_every_state_has_its_zones_and_each_is_a_real_one():
    from pathlib import Path
    from zoneinfo import ZoneInfo

    # Every state code an address may have (migration 0109).
    migration = Path(__file__).parent.parent / "db/migrations/20261021000109_property_addresses.sql"
    listed = migration.read_text().split("state text NOT NULL CHECK (state IN (")[1].split("))")[0]
    assert set(texts.ZONES) == set(re.findall(r"'([A-Z]{2})'", listed))
    for zones in texts.ZONES.values():
        for zone in zones:
            ZoneInfo(zone)


def test_a_homes_zones_come_from_its_address(conn, pmc):
    prop = pmc.owners[0].property_id
    assert texts.zones_of(conn, prop) == texts.CONTINENTAL  # no address on file
    assert texts.zones_of(conn, None) == texts.CONTINENTAL
    conn.execute(
        "INSERT INTO trust_property_addresses (pmc_id, property_id, line_1, city, state, zip)"
        " VALUES (%s, %s, '100 Synthetic Way', 'Demo City', 'TX', '00001')",
        (pmc.pmc_id, prop),
    )
    assert texts.zones_of(conn, prop) == ("America/Chicago", "America/Denver")


# --- the senders --------------------------------------------------------------------------------


def test_the_sender_is_the_one_the_server_names():
    assert texts.sender_named("", {}) is None
    assert texts.sender_named("fake", {}).name == "fake"
    env = {
        "PMS_TWILIO_ACCOUNT_SID": "AC0000",
        "PMS_TWILIO_AUTH_TOKEN": "synthetic-token",
        "PMS_TWILIO_FROM": "+12025550100",
    }
    twilio = texts.sender_named("twilio", env)
    assert isinstance(twilio, texts.TwilioSender)
    assert "synthetic-token" not in repr(twilio)  # the token never prints
    with pytest.raises(ValueError, match="PMS_TWILIO_AUTH_TOKEN"):
        texts.sender_named("twilio", {k: v for k, v in env.items() if "TOKEN" not in k})
    with pytest.raises(ValueError, match="'fake', 'twilio' or unset"):
        texts.sender_named("pigeon", {})


def test_twilio_is_asked_to_send_from_the_number_or_the_messaging_service(monkeypatch):
    asked = []

    def answer(request, timeout):
        asked.append(request)
        return io.BytesIO(json.dumps({"sid": "SM0001"}).encode())

    monkeypatch.setattr(texts.urllib.request, "urlopen", answer)
    sent = texts.TwilioSender("AC0000", "synthetic-token", "+12025550100").send(ONE, "Hello.")
    assert sent == "SM0001"
    (request,) = asked
    assert request.full_url == "https://api.twilio.com/2010-04-01/Accounts/AC0000/Messages.json"
    assert request.get_method() == "POST"
    assert request.get_header("Authorization") == "Basic QUMwMDAwOnN5bnRoZXRpYy10b2tlbg=="
    assert texts.urllib.parse.parse_qs(request.data.decode()) == {
        "To": [ONE],
        "Body": ["Hello."],
        "From": ["+12025550100"],
    }
    texts.TwilioSender("AC0000", "synthetic-token", "MG0001").send(ONE, "Hello.")
    assert "MessagingServiceSid=MG0001" in asked[1].data.decode()


def test_twilio_refusing_a_text_says_why(monkeypatch):
    def refuse(request, timeout):
        body = io.BytesIO(json.dumps({"code": 21610, "message": "unsubscribed"}).encode())
        raise urllib.error.HTTPError(request.full_url, 400, "Bad Request", {}, body)  # type: ignore[arg-type]

    monkeypatch.setattr(texts.urllib.request, "urlopen", refuse)
    with pytest.raises(texts.Refused, match="Twilio said 400: unsubscribed"):
        texts.TwilioSender("AC0000", "synthetic-token", "+12025550100").send(ONE, "Hello.")


def test_twilios_signature_is_checked_as_its_documentation_shows():
    # Twilio's own example (its security documentation): this token, URL and form sign to this.
    twilio = texts.TwilioSender("AC0000", "12345", "+12025550100")
    url = "https://mycompany.com/myapp.php?foo=1&bar=2"
    form = {
        "CallSid": "CA1234567890ABCDE",
        "Caller": "+12349013030",
        "Digits": "1234",
        "From": "+12349013030",
        "To": "+18005551212",
    }
    assert twilio.signed(url, form, "0/KCTR6DLpKmkAf8muzZqo1nDgQ=")
    assert not twilio.signed(url, {**form, "Digits": "1235"}, "0/KCTR6DLpKmkAf8muzZqo1nDgQ=")
    assert not twilio.signed(url + "&baz=3", form, "0/KCTR6DLpKmkAf8muzZqo1nDgQ=")
    assert not twilio.signed(url, form, "")


# --- writing and delivering ---------------------------------------------------------------------
#
# The test database is shared, so a delivery hands over other tests' texts too: each test texts
# numbers of its own and looks only at its own texts.


@pytest.fixture
def number():
    return fresh_phone()


def mine(kept, phone):
    return [body for to, body in kept.sent if to == phone]


def test_a_text_is_written_only_under_a_yes_and_goes_once(conn, pmc, number):
    tenant = tenant_of(conn, pmc)
    staff = add_staff(conn, pmc.pmc_id)
    write = texts.write
    assert write(conn, pmc.pmc_id, "message", "tenant", tenant, "Hi.", NOON, sent_by=staff) is None
    assert texts.record_consent(conn, pmc.pmc_id, "tenant", tenant, number, True, "in writing")
    assert not texts.record_consent(conn, pmc.pmc_id, "tenant", tenant, number, True, "verbally")
    made = write(conn, pmc.pmc_id, "message", "tenant", tenant, "Hi.", NOON, sent_by=staff)
    assert made is not None

    kept = Kept()
    texts.deliver_pending(conn, kept, NOON - timedelta(minutes=1))
    assert (mine(kept, number), deliveries(conn, made)) == ([], [])  # not its time yet
    texts.deliver_pending(conn, kept, NOON)
    assert mine(kept, number) == ["Hi."]
    ((sender, provider_id, error),) = deliveries(conn, made)
    assert (sender, provider_id.startswith("fake-"), error) == ("fake", True, None)
    texts.deliver_pending(conn, kept, NOON)
    assert mine(kept, number) == ["Hi."]  # once


def test_a_refused_text_is_tried_again_at_most_five_times(conn, pmc, number):
    tenant = tenant_of(conn, pmc)
    staff = add_staff(conn, pmc.pmc_id)
    texts.record_consent(conn, pmc.pmc_id, "tenant", tenant, number, True, "in writing")
    made = texts.write(conn, pmc.pmc_id, "message", "tenant", tenant, "Hi.", NOON, sent_by=staff)
    refusing = Kept(texts.Refused("Twilio said 400: invalid"))
    for _ in range(7):
        texts.deliver_pending(conn, refusing, NOON)
    assert [error for _, _, error in deliveries(conn, made)] == ["Twilio said 400: invalid"] * 5
    other = texts.write(
        conn, pmc.pmc_id, "message", "tenant", tenant, "Again.", NOON, sent_by=staff
    )
    texts.deliver_pending(conn, Kept(OSError("network unreachable")), NOON)
    assert deliveries(conn, other) == [("fake", None, "OSError: network unreachable")]
    texts.deliver_pending(conn, Kept(), NOON)
    assert [error for _, _, error in deliveries(conn, other)] == [
        "OSError: network unreachable",
        None,
    ]


def test_a_text_whose_person_said_stop_since_or_that_waited_too_long_is_given_up(conn, pmc, number):
    tenant = tenant_of(conn, pmc)
    staff = add_staff(conn, pmc.pmc_id)
    texts.record_consent(conn, pmc.pmc_id, "tenant", tenant, number, True, "in writing")
    sent = texts.write(conn, pmc.pmc_id, "message", "tenant", tenant, "One.", NOON, sent_by=staff)
    late = texts.write(
        conn, pmc.pmc_id, "message", "tenant", tenant, "Two.", NOON - timedelta(days=2),
        sent_by=staff,
    )  # fmt: skip
    kept = Kept()
    texts.deliver_pending(conn, kept, NOON)
    assert mine(kept, number) == ["One."]
    assert deliveries(conn, late) == [("fake", None, texts.LATE)]
    assert deliveries(conn, sent)[0][2] is None
    again = texts.write(
        conn, pmc.pmc_id, "message", "tenant", tenant, "Three.", NOON, sent_by=staff
    )
    assert texts.reply(conn, number, " stop ") == 1
    texts.deliver_pending(conn, kept, NOON)
    assert deliveries(conn, again) == [("fake", None, texts.STOPPED)]
    texts.deliver_pending(conn, kept, NOON)
    assert mine(kept, number) == ["One."]  # given up for good
    assert len(deliveries(conn, again)) == 1


def test_stop_says_no_everywhere_the_number_was_texted_and_start_takes_back_only_a_stop(conn):
    one_number, two_number = fresh_phone(), fresh_phone()
    first, second = make_pmc(conn), make_pmc(conn)
    one, two = tenant_of(conn, first), tenant_of(conn, second)
    texts.record_consent(conn, first.pmc_id, "tenant", one, one_number, True, "in writing")
    texts.record_consent(conn, second.pmc_id, "tenant", two, one_number, True, "in writing")
    texts.record_consent(
        conn, first.pmc_id, "vendor", first.vendor_id, one_number, False, "verbally"
    )
    texts.record_consent(
        conn, second.pmc_id, "vendor", second.vendor_id, two_number, True, "verbally"
    )

    assert texts.reply(conn, one_number, "When is rent due?") == 0
    assert texts.reply(conn, one_number, "Stop") == 2
    assert texts.reply(conn, one_number, "STOP") == 0  # stopped already
    for pmc, tenant in ((first, one), (second, two)):
        word = texts.consent_of(conn, pmc.pmc_id, "tenant", tenant)
        assert (word.phone, word.consented, word.how, word.by) == (
            one_number,
            False,
            "stop reply",
            None,
        )
    assert texts.consent_of(conn, second.pmc_id, "vendor", second.vendor_id).consented

    assert texts.reply(conn, one_number, "start") == 2  # not the vendor who said no to staff
    assert texts.consent_of(conn, first.pmc_id, "tenant", one).how == "start reply"
    assert not texts.consent_of(conn, first.pmc_id, "vendor", first.vendor_id).consented
    assert texts.reply(conn, two_number, "UNSUBSCRIBE") == 1


# --- what a company texts by itself ---------------------------------------------------------------


def settings(conn, pmc, days=None, late=False):
    conn.execute(
        "INSERT INTO trust_text_settings (pmc_id, rent_reminder_days, late_notices)"
        " VALUES (%s, %s, %s)",
        (pmc.pmc_id, days, late),
    )


def test_rent_reminders_go_to_each_tenant_who_agreed_once_in_the_daytime(conn, pmc):
    tenant = tenant_of(conn, pmc)
    unit = add_unit(conn, pmc.pmc_id, pmc.owners[0].property_id)
    lease = open_lease(conn, pmc.pmc_id, unit, date(2026, 1, 1), None, "1500.00", [tenant])
    morning = datetime(2026, 2, 27, 6, 0, tzinfo=UTC)
    assert texts.write_scheduled(conn, pmc.pmc_id, date(2026, 2, 27), morning) == 0  # off
    settings(conn, pmc, days=3)
    assert texts.write_scheduled(conn, pmc.pmc_id, date(2026, 2, 27), morning) == 0  # no word
    number = fresh_phone()
    texts.record_consent(conn, pmc.pmc_id, "tenant", tenant, number, True, "in writing")
    assert texts.write_scheduled(conn, pmc.pmc_id, date(2026, 2, 27), morning) == 1
    assert texts.write_scheduled(conn, pmc.pmc_id, date(2026, 2, 28), morning) == 0  # once
    (row,) = conn.execute(
        "SELECT kind, to_phone, body, lease_id, period, not_before FROM trust_texts"
        " WHERE pmc_id = %s",
        (pmc.pmc_id,),
    ).fetchall()
    property_name = conn.execute(
        "SELECT display_name FROM trust_properties WHERE id = %s", (pmc.owners[0].property_id,)
    ).fetchone()[0]
    company = conn.execute(
        "SELECT display_name FROM trust_pmcs WHERE pmc_id = %s", (pmc.pmc_id,)
    ).fetchone()[0]
    assert row == (
        "rent_reminder",
        number,
        f"{company}: rent for {property_name}, Unit 1 is due 03/01/2026, $1,500.00."
        " Reply STOP to stop.",
        lease,
        date(2026, 3, 1),
        # No address on file: 9 am Pacific, when it is daytime across the continental US.
        datetime(2026, 2, 27, 17, 0, tzinfo=UTC),
    )


def test_a_late_notice_is_texted_when_a_late_fee_is_charged(conn, pmc):
    tenant = tenant_of(conn, pmc)
    unit = add_unit(conn, pmc.pmc_id, pmc.owners[0].property_id)
    lease = open_lease(conn, pmc.pmc_id, unit, date(2026, 1, 1), None, "1500.00", [tenant])
    charge(conn, pmc.pmc_id, lease, date(2026, 2, 1), "1500.00")
    add_late_fee_policy(conn, pmc.pmc_id, pmc.owners[0].property_id, date(2026, 1, 1), 5, "50.00")
    texts.record_consent(conn, pmc.pmc_id, "tenant", tenant, fresh_phone(), True, "in writing")
    settings(conn, pmc, late=True)
    with conn.transaction():
        conn.execute("SELECT trust_assess_late_fees(%s, %s)", (pmc.pmc_id, date(2026, 2, 10)))
        assert texts.write_scheduled(conn, pmc.pmc_id, date(2026, 2, 10), NOON) == 1
    (body, fee) = conn.execute(
        "SELECT t.body, c.amount FROM trust_texts AS t"
        " JOIN trust_charges AS c ON c.id = t.charge_id"
        " WHERE t.pmc_id = %s AND t.kind = 'late_notice'",
        (pmc.pmc_id,),
    ).fetchone()
    assert fee == Decimal("50.00")
    assert "a late fee of $50.00 was charged" in body and "Owed now: $1,550.00" in body
