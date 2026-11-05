"""Webhooks (ADR 0011, migration 20261105000500, src/pms/webhooks.py): each event goes to each
standing webhook of its company, signed with the webhook's secret, retried after a wait until it
is delivered or tried MAX_ATTEMPTS times; an address that leads inside the server's network is
never sent to. Every name and address here is synthetic (example.com is reserved for examples;
the addresses used as public are documentation ranges)."""

import base64
import hashlib
import hmac
import ipaddress
import json
import socket
import uuid
from datetime import UTC, datetime, timedelta

import psycopg
import pytest
from helpers import acting_as, add_work_order, admin_of, events_seen, make_pmc

from pms import sealed, webhooks

KEY = base64.b64encode(b"k" * 32).decode()
NOW = 1_800_000_000
SECRET = "whsec_test1234"  # noqa: S105 - a synthetic secret, for this test's webhooks


@pytest.fixture
def key(monkeypatch):
    monkeypatch.setenv("PMS_DATA_KEY", KEY)
    return sealed.key_from()


@pytest.fixture
def pmc(conn):
    return make_pmc(conn)


def webhook(conn, pmc, key, url="https://hooks.example.com/occupella", secret=SECRET):
    made = uuid.uuid4()
    with acting_as(conn, admin_of(conn, pmc.pmc_id)):
        conn.execute(
            "INSERT INTO trust_event_subscriptions (id, pmc_id, url, secret_sealed, secret_last4)"
            " VALUES (%s, %s, %s, %s, %s)",
            (made, pmc.pmc_id, url, sealed.seal(secret, str(made), key), secret[-4:]),
        )
    return made


def opened(conn, pmc, summary="Leak"):
    """A work order opened, committed, and its event past every older writer (events_seen)."""
    order = add_work_order(
        conn, pmc.pmc_id, pmc.owners[0].property_id, None, summary, datetime.now(UTC)
    )
    events_seen(conn, pmc.pmc_id)
    return order


class Receiver:
    """Answers each request with the next status it is given, keeping what it was sent."""

    def __init__(self, *statuses):
        self.statuses = list(statuses)
        self.got = []

    def __call__(self, url, body, headers):
        self.got.append((url, body, headers))
        status = self.statuses.pop(0) if self.statuses else 200
        if isinstance(status, Exception):
            raise status
        return status


def deliveries(conn, sub):
    return conn.execute(
        "SELECT event_id, status, error FROM trust_event_deliveries WHERE subscription_id = %s"
        " ORDER BY id",
        (sub,),
    ).fetchall()


def events_of(conn, pmc):
    return conn.execute(
        "SELECT id, kind, subject FROM trust_events WHERE pmc_id = %s ORDER BY id", (pmc.pmc_id,)
    ).fetchall()


def test_each_event_goes_signed_with_ids_only_and_once(conn, app_conn, pmc, key):
    sub = webhook(conn, pmc, key)
    order = opened(conn, pmc, "Leak under the sink")
    ((event, kind, subject),) = events_of(conn, pmc)
    receiver = Receiver(200)

    assert webhooks.deliver_pending(app_conn, key, receiver, clock=lambda: NOW) == 1
    ((url, body, headers),) = receiver.got
    assert url == "https://hooks.example.com/occupella"
    sent = json.loads(body)
    assert sent == {
        "id": event,
        "kind": "work_order.opened",
        "at": sent["at"],
        "company": str(pmc.pmc_id),
        "subject": {"work_order_id": str(order), "property_id": str(pmc.owners[0].property_id)},
    }
    assert "Leak" not in body.decode()  # ids only: never the words of what happened
    assert headers["Occupella-Event-Id"] == str(event)
    mac = hmac.new(b"whsec_test1234", f"{NOW}.".encode() + body, hashlib.sha256).hexdigest()
    assert headers["Occupella-Signature"] == f"t={NOW},v1={mac}"
    assert deliveries(conn, sub) == [(event, 200, None)]
    # Delivered once: nothing more goes.
    assert webhooks.deliver_pending(app_conn, key, Receiver()) == 0


def tried(conn, pmc, sub, event, times, last_ago):
    """`times` failed attempts at the event, the last `last_ago` before now."""
    for n in range(times):
        conn.execute(
            "INSERT INTO trust_event_deliveries"
            " (pmc_id, subscription_id, event_id, attempted_at, status, error)"
            " VALUES (%s, %s, %s, now() - %s - %s * interval '1 day', 503, 'busy')",
            (pmc.pmc_id, sub, event, last_ago, times - 1 - n),
        )


def test_a_failed_delivery_is_tried_again_after_its_wait(conn, app_conn, pmc, key):
    sub = webhook(conn, pmc, key)
    opened(conn, pmc)
    ((event, _, _),) = events_of(conn, pmc)

    webhooks.deliver_pending(app_conn, key, Receiver(500))
    # Not again before its wait.
    assert webhooks.deliver_pending(app_conn, key, Receiver()) == 0
    assert deliveries(conn, sub) == [(event, 500, "the receiver answered 500")]


@pytest.mark.parametrize("times", range(1, webhooks.MAX_ATTEMPTS))
def test_each_wait_is_longer_and_the_next_attempt_goes_once_it_passed(
    conn, app_conn, pmc, key, times
):
    sub = webhook(conn, pmc, key)
    other = webhook(conn, pmc, key, url="https://hooks.example.com/second")
    opened(conn, pmc)
    ((event, _, _),) = events_of(conn, pmc)
    wait = webhooks.BACKOFF[times - 1]
    # Tried `times` times on each: the first's last attempt within the wait, the second's past it.
    tried(conn, pmc, sub, event, times, wait - timedelta(seconds=30))
    tried(conn, pmc, other, event, times, wait + timedelta(seconds=30))
    receiver = Receiver()
    assert webhooks.deliver_pending(app_conn, key, receiver) == 1
    assert [url for url, _, _ in receiver.got] == ["https://hooks.example.com/second"]


def test_an_event_tried_every_time_is_given_up_on(conn, app_conn, pmc, key):
    sub = webhook(conn, pmc, key)
    opened(conn, pmc)
    ((event, _, _),) = events_of(conn, pmc)
    tried(conn, pmc, sub, event, webhooks.MAX_ATTEMPTS, timedelta(days=2))
    assert webhooks.deliver_pending(app_conn, key, Receiver()) == 0


def test_a_connection_that_fails_is_a_failed_attempt(conn, app_conn, pmc, key):
    sub = webhook(conn, pmc, key)
    opened(conn, pmc)
    webhooks.deliver_pending(app_conn, key, Receiver(OSError("connection refused")))
    assert deliveries(conn, sub)[0][1:] == (None, "OSError: connection refused")


def test_a_webhook_gets_its_own_companys_events_from_when_it_was_added_while_it_stands(
    conn, app_conn, pmc, key
):
    opened(conn, pmc, "Before")  # before the webhook: not sent
    other = make_pmc(conn)
    sub = webhook(conn, pmc, key)
    theirs = webhook(conn, other, key, url="https://hooks.example.com/other")
    opened(conn, pmc, "Ours")
    opened(conn, other, "Theirs")
    receiver = Receiver()
    webhooks.deliver_pending(app_conn, key, receiver)

    by_url = {}
    for url, body, _ in receiver.got:
        by_url.setdefault(url, []).append(json.loads(body)["company"])
    assert by_url == {
        "https://hooks.example.com/occupella": [str(pmc.pmc_id)],
        "https://hooks.example.com/other": [str(other.pmc_id)],
    }

    with acting_as(conn, admin_of(conn, pmc.pmc_id)):
        conn.execute(
            "INSERT INTO trust_event_subscription_revocations (subscription_id, pmc_id)"
            " VALUES (%s, %s)",
            (sub, pmc.pmc_id),
        )
    opened(conn, pmc, "After")
    later = Receiver()
    webhooks.deliver_pending(app_conn, key, later)
    assert later.got == []
    assert len(deliveries(conn, theirs)) == 1


def test_an_address_that_leads_inside_is_never_sent_to(conn, app_conn, pmc, key):
    sub = webhook(conn, pmc, key, url="https://inside.example.com/hook")
    opened(conn, pmc)

    def to_metadata(host, port, **kind):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("169.254.169.254", port))]

    def send(url, body, headers):
        return webhooks.post(url, body, headers, resolve=to_metadata)

    webhooks.deliver_pending(app_conn, key, send)
    ((_, status, error),) = deliveries(conn, sub)
    assert (status, error) == (
        None,
        "the webhook's host leads to an internal address: nothing was sent",
    )


def test_a_secret_that_wont_open_is_a_failed_attempt(conn, app_conn, pmc, key):
    sub = webhook(conn, pmc, key)
    opened(conn, pmc)
    other_key = base64.b64decode(base64.b64encode(b"z" * 32))
    webhooks.deliver_pending(app_conn, other_key, Receiver())
    assert deliveries(conn, sub)[0][2] == "a sealed value that doesn't open under this server's key"


@pytest.mark.parametrize(
    ("url", "says"),
    [
        ("http://hooks.example.com/x", "starts with https://"),
        ("https://user:pw@example.com/x", "no user name or password"),
        ("https://localhost/x", "never the server's own network"),
        ("https://metadata.google.internal/x", "never the server's own network"),
        ("https://printer.local/x", "never the server's own network"),
        ("https://intranet/x", "never the server's own network"),
        ("https://10.0.0.5/x", "never the server's own network"),
        ("https://127.0.0.1:8123/x", "never the server's own network"),
        ("https://169.254.169.254/latest/meta-data", "never the server's own network"),
        ("https://[::1]/x", "never the server's own network"),
        ("https://[::ffff:192.168.1.1]/x", "never the server's own network"),
        ("https://100.64.0.1/x", "never the server's own network"),
        ("https://hooks.example.com:99999/x", "a port from 1 to 65535"),
        ("https://hooks.example.com/x#frag", "no #fragment"),
        ("https:///x", "names a host"),
    ],
)
def test_an_address_is_refused_saying_why(url, says):
    with pytest.raises(webhooks.Refused, match=says):
        webhooks.parts_of(url)


def test_a_public_address_and_its_parts():
    assert webhooks.parts_of("https://hooks.example.com/occupella?team=1") == (
        "hooks.example.com",
        443,
        "/occupella?team=1",
    )
    assert webhooks.parts_of("https://[2606:2800:21f:cb07:6820:80da:af6b:8b2c]:8443/") == (
        "2606:2800:21f:cb07:6820:80da:af6b:8b2c",
        8443,
        "/",
    )


@pytest.mark.parametrize(
    ("found", "public"),
    [
        (["93.184.215.14"], True),
        (["93.184.215.14", "10.1.2.3"], False),  # any one inside is enough to refuse
        (["::ffff:127.0.0.1"], False),
        (["fd00::1"], False),
        (["fe80::1%eth0"], False),
        (["224.0.0.1"], False),
        (["0.0.0.0"], False),
    ],
)
def test_every_address_a_host_resolves_to_must_be_public(found, public):
    def resolve(host, port, **kind):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (a, port)) for a in found]

    if public:
        assert webhooks.addresses_of("hooks.example.com", 443, resolve) == found
    else:
        with pytest.raises(webhooks.Refused, match="internal address"):
            webhooks.addresses_of("hooks.example.com", 443, resolve)


def test_a_host_that_doesnt_resolve_is_refused():
    def resolve(host, port, **kind):
        raise socket.gaierror("Name or service not known")

    with pytest.raises(webhooks.Refused, match="doesn't resolve"):
        webhooks.addresses_of("nowhere.example.com", 443, resolve)


def test_the_database_takes_a_webhook_from_an_admin_only(conn, pmc, key):
    from helpers import add_staff

    clerk = add_staff(conn, pmc.pmc_id, "Clerk 1")
    with pytest.raises(psycopg.errors.InsufficientPrivilege), acting_as(conn, clerk):
        conn.execute(
            "INSERT INTO trust_event_subscriptions (id, pmc_id, url, secret_sealed, secret_last4)"
            " VALUES (%s, %s, 'https://hooks.example.com/x', '\\x00', 'abcd')",
            (uuid.uuid4(), pmc.pmc_id),
        )


def test_public_rejects_every_inside_range():
    for inside in ("10.0.0.1", "172.16.0.1", "192.168.0.1", "127.0.0.1", "169.254.1.1", "::1"):
        assert not webhooks.public(ipaddress.ip_address(inside))
    assert webhooks.public(ipaddress.ip_address("93.184.215.14"))
