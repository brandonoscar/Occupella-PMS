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
import os
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


def test_an_address_over_500_characters_is_refused():
    with pytest.raises(webhooks.Refused, match="at most 500 characters"):
        webhooks.parts_of("https://hooks.example.com/" + "a" * 500)


def test_a_host_that_resolves_to_nothing_is_refused():
    with pytest.raises(webhooks.Refused, match="doesn't resolve"):
        webhooks.addresses_of("hooks.example.com", 443, lambda host, port, **kind: [])


# --- the post itself: HTTPS to the address checked, the certificate checked against the name ---


def certificate_for(directory, name):
    """A throwaway certificate authority and a certificate it signs for `name`: the CA's file and
    the server's certificate and key files."""
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID

    def made(subject, issuer, key, signer, extensions):
        now = datetime.now(UTC)
        builder = (
            x509.CertificateBuilder()
            .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, subject)]))
            .issuer_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, issuer)]))
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(minutes=5))
            .not_valid_after(now + timedelta(hours=1))
        )
        for extension, critical in extensions:
            builder = builder.add_extension(extension, critical=critical)
        return builder.sign(signer, hashes.SHA256())

    ca_key = ec.generate_private_key(ec.SECP256R1())
    ca = made(
        "Synthetic test CA",
        "Synthetic test CA",
        ca_key,
        ca_key,
        [(x509.BasicConstraints(ca=True, path_length=None), True)],
    )
    server_key = ec.generate_private_key(ec.SECP256R1())
    server = made(
        name,
        "Synthetic test CA",
        server_key,
        ca_key,
        [(x509.SubjectAlternativeName([x509.DNSName(name)]), False)],
    )
    files = {}
    for label, data in (
        ("ca.pem", ca.public_bytes(serialization.Encoding.PEM)),
        ("server.pem", server.public_bytes(serialization.Encoding.PEM)),
        (
            "server.key",
            server_key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            ),
        ),
    ):
        path = directory / label
        path.write_bytes(data)
        files[label] = str(path)
    return files


@pytest.fixture
def receiver(tmp_path, monkeypatch):
    """An HTTPS receiver on 127.0.0.1 whose certificate names hooks.example.com, signed by a CA
    the post is made to trust; the loopback address passed as public for this test alone."""
    import http.server
    import ssl
    import threading

    files = certificate_for(tmp_path, "hooks.example.com")
    got = []

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            length = int(self.headers["Content-Length"])
            got.append((self.path, self.headers["Host"], self.rfile.read(length)))
            self.send_response(202)
            self.end_headers()

        def log_message(self, *args):
            pass

    server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    serving = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
    serving.minimum_version = ssl.TLSVersion.TLSv1_2  # never TLS 1.0 or 1.1, even in a test
    serving.load_cert_chain(files["server.pem"], files["server.key"])
    server.socket = serving.wrap_socket(server.socket, server_side=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    trusting = ssl.create_default_context(cafile=files["ca.pem"])
    trusting.minimum_version = ssl.TLSVersion.TLSv1_2
    monkeypatch.setattr(webhooks.ssl, "create_default_context", lambda: trusting)
    monkeypatch.setattr(webhooks, "public", lambda address: True)
    port = server.server_address[1]

    def resolve(host, port_asked, **kind):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", port))]

    try:
        yield port, resolve, got
    finally:
        server.shutdown()
        server.server_close()


def test_a_post_goes_over_https_to_the_address_checked_by_its_name(receiver):
    port, resolve, got = receiver
    status = webhooks.post(
        f"https://hooks.example.com:{port}/occupella?team=1",
        b'{"id":1}',
        {"Content-Type": "application/json"},
        resolve,
    )
    assert status == 202
    assert got == [("/occupella?team=1", f"hooks.example.com:{port}", b'{"id":1}')]


def test_a_delivery_leaves_no_descriptor_open_however_it_ends(receiver, monkeypatch):
    """The connection keeps a descriptor of its own for the deadline (_Pinned): a delivery that
    went through, and one refused before anything was sent, each close the one it made."""
    import ssl

    made, closed = [], []
    real_dup, real_close = os.dup, os.close

    def dup(fd):
        made.append(real_dup(fd))
        return made[-1]

    def close(fd):
        closed.append(fd)
        real_close(fd)

    monkeypatch.setattr(webhooks.os, "dup", dup)
    monkeypatch.setattr(webhooks.os, "close", close)
    port, resolve, _ = receiver
    for _ in range(3):
        assert webhooks.post(f"https://hooks.example.com:{port}/x", b"{}", {}, resolve) == 202
        with pytest.raises(ssl.SSLCertVerificationError):
            webhooks.post(f"https://other.example.com:{port}/x", b"{}", {}, resolve)
    assert len(made) == 6
    assert closed == made


def test_a_post_to_a_name_its_certificate_doesnt_carry_is_refused(receiver):
    """The connection goes to the address checked, but the certificate is checked against the
    host's name: one for another name fails before anything is sent."""
    import ssl

    port, resolve, got = receiver
    with pytest.raises(ssl.SSLCertVerificationError):
        webhooks.post(f"https://other.example.com:{port}/x", b"{}", {}, resolve)
    assert got == []


@pytest.fixture
def slow_receiver(tmp_path, monkeypatch):
    """An HTTPS receiver that takes the request, then answers its status line one byte every
    0.3 s (`trickle`), never (`silent`), or at once saying Connection: close and then its body
    one byte every 0.3 s for 12 s (`close-trickle`): what the courier's deadline is for."""
    import ssl
    import threading
    import time

    files = certificate_for(tmp_path, "hooks.example.com")
    serving = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
    serving.minimum_version = ssl.TLSVersion.TLSv1_2
    serving.load_cert_chain(files["server.pem"], files["server.key"])
    listening = socket.create_server(("127.0.0.1", 0))
    mode = {"answer": "trickle"}
    stop = threading.Event()

    def serve():
        listening.settimeout(0.2)
        while not stop.is_set():
            try:
                raw, _ = listening.accept()
            except TimeoutError:
                continue
            try:
                with serving.wrap_socket(raw, server_side=True) as tls:
                    tls.settimeout(5)
                    tls.recv(65536)  # the request
                    if mode["answer"] == "trickle":
                        for byte in b"HTTP/1.1 202 Accepted\r\n\r\n":
                            if stop.is_set():
                                break
                            tls.sendall(bytes([byte]))
                            time.sleep(0.3)
                    elif mode["answer"] == "close-trickle":
                        tls.sendall(b"HTTP/1.1 200 OK\r\nConnection: close\r\n\r\n")
                        for _ in range(40):
                            if stop.is_set():
                                break
                            tls.sendall(b"x")
                            time.sleep(0.3)
                    else:
                        stop.wait(15)
            except OSError:
                pass

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    trusting = ssl.create_default_context(cafile=files["ca.pem"])
    trusting.minimum_version = ssl.TLSVersion.TLSv1_2
    monkeypatch.setattr(webhooks.ssl, "create_default_context", lambda: trusting)
    monkeypatch.setattr(webhooks, "public", lambda address: True)
    monkeypatch.setattr(webhooks, "DEADLINE", 1.0)
    port = listening.getsockname()[1]

    def resolve(host, port_asked, **kind):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", port))]

    try:
        yield port, resolve, mode
    finally:
        stop.set()
        thread.join(5)
        listening.close()


@pytest.mark.timing
@pytest.mark.parametrize("answer", ["trickle", "silent", "close-trickle"])
def test_a_receiver_that_answers_too_slowly_is_given_up_at_the_deadline(slow_receiver, answer):
    # The AI pen test of 11/06: TIMEOUT bounds each read, not the delivery, so a receiver that
    # answers a byte every 9 s held the one courier thread, and every company's webhooks behind
    # it, for as long as it liked. A delivery now has DEADLINE in all. The OWASP review of 11/06:
    # an answer saying Connection: close made http.client close the connection before its body
    # was read, which let go of what the deadline shuts down, so a body trickled slowly enough
    # held the thread as before (close-trickle).
    import time

    port, resolve, mode = slow_receiver
    mode["answer"] = answer
    started = time.monotonic()
    with pytest.raises(TimeoutError, match="didn't answer within 1 s"):
        webhooks.post(f"https://hooks.example.com:{port}/x", b"{}", {}, resolve)
    assert time.monotonic() - started < webhooks.TIMEOUT  # cut at the deadline, not a read's


def test_giving_up_before_the_connection_is_made_or_after_it_finished_does_nothing_more():
    # The deadline may pass while the connection is being made, or just as the delivery finishes:
    # the first connects nothing more, the second shuts down nothing (its descriptor is closed).
    listening = socket.create_server(("127.0.0.1", 0))
    try:
        port = listening.getsockname()[1]
        early = webhooks._Pinned("hooks.example.com", "127.0.0.1", port, None)
        early.give_up()
        with pytest.raises(TimeoutError, match="given up before the connection was made"):
            early.connect()
        early.finish()
        early.give_up()  # finished: nothing to shut down
        unconnected = socket.socket()
        late = webhooks._Pinned("hooks.example.com", "127.0.0.1", port, None)
        late._fd = unconnected.fileno()
        late.give_up()  # shutting down a socket that never connected raises, and is let be
        assert late.gave_up
        assert unconnected.fileno() != -1  # still open: give_up never closes what it shuts
        unconnected.close()
    finally:
        listening.close()


# --- what a delivery records ----------------------------------------------------------------


def test_an_event_another_process_is_sending_is_left_to_it(conn, app_conn, pmc, key, database_url):
    sub = webhook(conn, pmc, key)
    opened(conn, pmc)
    ((event, _, _),) = events_of(conn, pmc)
    with psycopg.connect(database_url, autocommit=True) as other:
        other.execute(
            "SELECT pg_advisory_lock(hashtextextended(%s, 0))",
            (f"trust_event_delivery:{sub}:{event}",),
        )
        sent = Receiver()
        assert webhooks.deliver_pending(app_conn, key, sent) == 0
    assert sent.got == []
    assert deliveries(conn, sub) == []


def test_a_status_no_receiver_answers_is_a_failed_attempt(conn, app_conn, pmc, key):
    ours = "https://hooks.example.com/answers-42"
    sub = webhook(conn, pmc, key, url=ours)
    opened(conn, pmc)
    webhooks.deliver_pending(app_conn, key, lambda url, body, headers: 42 if url == ours else 200)
    assert deliveries(conn, sub)[0][1:] == (None, "the receiver answered 42")


def test_a_sender_that_fails_strangely_is_recorded_and_logged(conn, app_conn, pmc, key, caplog):
    ours = "https://hooks.example.com/fails-oddly"
    sub = webhook(conn, pmc, key, url=ours)
    opened(conn, pmc)

    def send(url, body, headers):
        if url == ours:
            raise ValueError("odd")
        return 200

    webhooks.deliver_pending(app_conn, key, send)
    assert deliveries(conn, sub)[0][1:] == (None, "ValueError: odd")
    assert any("could not be sent" in r.getMessage() for r in caplog.records)


# --- the courier and the command -------------------------------------------------------------


def test_the_courier_sends_nothing_without_a_data_key(monkeypatch, database_url):
    monkeypatch.delenv("PMS_DATA_KEY", raising=False)
    assert webhooks.Courier(database_url, "trust_app").once() == 0


def test_the_courier_delivers_what_waits_as_the_app(conn, pmc, key, database_url, monkeypatch):
    webhook(conn, pmc, key)
    opened(conn, pmc)
    sent = Receiver()
    monkeypatch.setattr(webhooks, "post", sent)
    assert webhooks.Courier(database_url, "trust_app").once() >= 1
    assert any(json.loads(body)["company"] == str(pmc.pmc_id) for _, body, _ in sent.got)


def test_the_courier_looks_again_after_a_look_that_failed(database_url, monkeypatch, caplog):
    import threading

    looked = threading.Event()
    tries = []

    def once(self):
        tries.append(1)
        if len(tries) == 1:
            raise RuntimeError("the database was away")
        looked.set()
        return 0

    monkeypatch.setattr(webhooks.Courier, "once", once)
    courier = webhooks.Courier(database_url, "trust_app", interval=0.01)
    courier.start()
    try:
        assert looked.wait(5)
    finally:
        courier.stop()
    assert any("delivering what waits failed" in r.getMessage() for r in caplog.records)


def test_the_command_delivers_what_waits(conn, pmc, key, database_url, monkeypatch, capsys):
    webhook(conn, pmc, key)
    opened(conn, pmc)
    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.setattr(webhooks, "post", Receiver())
    assert webhooks.main(["deliver"]) == 0
    assert capsys.readouterr().out.startswith("delivered ")


def test_the_command_says_so_without_a_data_key(monkeypatch, capsys):
    monkeypatch.delenv("PMS_DATA_KEY", raising=False)
    assert webhooks.main(["deliver"]) == 1
    assert capsys.readouterr().err.strip()
