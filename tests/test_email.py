"""Email to an owner when a bill waits for them (src/pms/email.py, migration 20261013000042,
docs/adr/0004-email.md).

The email is written in the transaction that enters the bill, once per bill, only when the bill
is over the owner's limit and the owner has an active portal login. Delivery hands each email
not yet delivered to the sender and records each attempt; a delivered email is never sent again,
and one that keeps failing is given up after MAX_ATTEMPTS.
"""

import smtplib
import threading
from datetime import date
from decimal import Decimal

import psycopg
import pytest
from helpers import add_agreement, enter_bill, make_pmc

from pms import accounts, email

JAN_1, JAN_10, JAN_20 = date(2026, 1, 1), date(2026, 1, 10), date(2026, 1, 20)
URL = "https://pms.example"


@pytest.fixture
def pmc(conn):
    made = make_pmc(conn)
    add_agreement(conn, made.pmc_id, made.owners[0].account, JAN_1, approval_limit="500")
    return made


def join(conn, pmc, n=0, password=True):
    """Owner n's portal login; with a password set unless `password` is False."""
    owner = pmc.owners[n].owner_id
    made = accounts.create_invite(
        conn, pmc.pmc_id, "owner", owner, f"owner{n}.{owner.hex[:6]}@example.com", None
    )
    if password:
        accounts.accept_invite(conn, made.token, "a long synthetic passphrase")
    return made


def bill(conn, pmc, amount="600.00", reference="ROOF-1", n=0):
    return enter_bill(
        conn,
        pmc.pmc_id,
        pmc.vendor_id,
        pmc.owners[n].account,
        reference,
        JAN_10,
        JAN_20,
        amount,
    )


def emails(conn, pmc):
    return conn.execute(
        "SELECT kind, bill_id, to_address, subject, body FROM trust_emails WHERE pmc_id = %s",
        (pmc.pmc_id,),
    ).fetchall()


def deliveries(conn, pmc):
    return conn.execute(
        "SELECT sender, error FROM trust_email_deliveries WHERE pmc_id = %s ORDER BY attempted_at",
        (pmc.pmc_id,),
    ).fetchall()


def pmc_name(conn, pmc):
    return conn.execute(
        "SELECT display_name FROM trust_pmcs WHERE pmc_id = %s", (pmc.pmc_id,)
    ).fetchone()[0]


# deliver_pending delivers every PMC's emails, as a server does; each test reads only its own
# PMC's deliveries.


class Failing:
    name = "smtp"

    def __init__(self, error=None):
        self.error = error or smtplib.SMTPServerDisconnected("Connection unexpectedly closed")
        self.tried = 0

    def send(self, message):
        self.tried += 1
        raise self.error


# --- writing the email ---------------------------------------------------------------------


def test_a_bill_over_the_limit_writes_its_owner_an_email(conn, app_conn, pmc):
    join(conn, pmc)
    roof = bill(conn, pmc)

    assert email.write_bill_approval(app_conn, pmc.pmc_id, roof, URL + "/") == "written"

    ((kind, about, to, subject, body),) = emails(conn, pmc)
    assert (kind, about, to) == (
        "bill_approval",
        roof,
        f"owner0.{pmc.owners[0].owner_id.hex[:6]}@example.com",
    )
    assert subject == "A bill waits for your approval: Vendor 1, $600.00"
    assert body == (
        "Hello Owner 1,\n\n"
        "Vendor 1 billed $600.00 for Property 1: Repair. Reference ROOF-1, dated 2026-01-10, "
        "due 2026-01-20.\n\n"
        "It is over your approval limit of $500.00, so it waits for you. Sign in to approve or "
        "decline it:\nhttps://pms.example/\n\n"
        f"{pmc_name(conn, pmc)}\n"
    )


def test_a_bill_within_the_limit_writes_none(conn, pmc):
    join(conn, pmc)

    assert email.write_bill_approval(conn, pmc.pmc_id, bill(conn, pmc, "500.00"), URL) == (
        "within the limit"
    )
    assert emails(conn, pmc) == []


@pytest.mark.parametrize("login", ["none", "invited", "disabled"])
def test_an_owner_who_cannot_sign_in_gets_none(conn, pmc, login):
    if login == "invited":
        join(conn, pmc, password=False)
    elif login == "disabled":
        made = join(conn, pmc)
        accounts.disable_login(conn, pmc.pmc_id, made.login_id)

    assert email.write_bill_approval(conn, pmc.pmc_id, bill(conn, pmc), URL) == "no active login"
    assert emails(conn, pmc) == []


def test_a_bill_is_emailed_about_once(conn, pmc):
    join(conn, pmc)
    roof = bill(conn, pmc)
    email.write_bill_approval(conn, pmc.pmc_id, roof, URL)

    assert email.write_bill_approval(conn, pmc.pmc_id, roof, URL) == "written already"
    assert len(emails(conn, pmc)) == 1
    with pytest.raises(psycopg.errors.UniqueViolation):
        conn.execute(
            "INSERT INTO trust_emails (pmc_id, kind, bill_id, to_address, subject, body)"
            " VALUES (%s, 'bill_approval', %s, 'x@example.com', 's', 'b')",
            (pmc.pmc_id, roof),
        )


def test_another_pmcs_bill_is_not_found(conn, pmc):
    other = make_pmc(conn)

    with pytest.raises(LookupError):
        email.write_bill_approval(conn, other.pmc_id, bill(conn, pmc), URL)


@pytest.mark.parametrize(
    "change",
    [
        {"to_address": ""},
        {"to_address": "o" * 309 + "@example.com"},  # 321 characters
        {"subject": ""},
        {"subject": "s" * 201},
        {"body": ""},
        {"body": "b" * 10_001},
        {"kind": "newsletter"},
        {"kind": "newsletter", "bill_id": None},
        {"bill_id": None},
    ],
)
def test_an_email_says_to_whom_what_and_about_what(conn, pmc, change):
    given = {
        "kind": "bill_approval",
        "bill_id": bill(conn, pmc),
        "to_address": "o@example.com",
        "subject": "s",
        "body": "b",
    } | change

    with pytest.raises(psycopg.errors.CheckViolation):
        conn.execute(
            "INSERT INTO trust_emails (pmc_id, kind, bill_id, to_address, subject, body)"
            " VALUES (%(pmc)s, %(kind)s, %(bill_id)s, %(to_address)s, %(subject)s, %(body)s)",
            given | {"pmc": pmc.pmc_id},
        )


def test_the_longest_email_the_rules_allow_is_kept(conn, pmc):
    conn.execute(
        "INSERT INTO trust_emails (pmc_id, kind, bill_id, to_address, subject, body)"
        " VALUES (%s, 'bill_approval', %s, %s, %s, %s)",
        (pmc.pmc_id, bill(conn, pmc), "o" * 308 + "@example.com", "s" * 200, "b" * 10_000),
    )

    assert len(emails(conn, pmc)) == 1


def test_an_email_is_about_a_bill_of_its_own_pmc(conn, pmc):
    other = make_pmc(conn)

    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        conn.execute(
            "INSERT INTO trust_emails (pmc_id, kind, bill_id, to_address, subject, body)"
            " VALUES (%s, 'bill_approval', %s, 'o@example.com', 's', 'b')",
            (other.pmc_id, bill(conn, pmc)),
        )


@pytest.mark.parametrize(
    ("sender", "error"),
    [("pigeon", None), ("smtp", ""), ("smtp", "e" * 1001)],
)
def test_a_delivery_says_which_sender_and_why_it_failed(conn, pmc, sender, error):
    join(conn, pmc)
    email.write_bill_approval(conn, pmc.pmc_id, bill(conn, pmc), URL)
    (email_id,) = conn.execute(
        "SELECT id FROM trust_emails WHERE pmc_id = %s", (pmc.pmc_id,)
    ).fetchone()
    record = (
        "INSERT INTO trust_email_deliveries (email_id, pmc_id, sender, error)"
        " VALUES (%s, %s, %s, %s)"
    )
    conn.execute(record, (email_id, pmc.pmc_id, "smtp", "e" * 1000))  # the longest error kept

    with pytest.raises(psycopg.errors.CheckViolation):
        conn.execute(record, (email_id, pmc.pmc_id, sender, error))


def test_a_delivery_is_of_an_email_of_its_own_pmc(conn, pmc):
    join(conn, pmc)
    email.write_bill_approval(conn, pmc.pmc_id, bill(conn, pmc), URL)
    (email_id,) = conn.execute(
        "SELECT id FROM trust_emails WHERE pmc_id = %s", (pmc.pmc_id,)
    ).fetchone()
    other = make_pmc(conn)

    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        conn.execute(
            "INSERT INTO trust_email_deliveries (email_id, pmc_id, sender) VALUES (%s, %s, 'fake')",
            (email_id, other.pmc_id),
        )


# --- delivering it -------------------------------------------------------------------------


def test_the_fake_sender_delivers_once(conn, app_conn, pmc):
    join(conn, pmc)
    email.write_bill_approval(conn, pmc.pmc_id, bill(conn, pmc), URL)

    first = email.deliver_pending(app_conn, email.FakeSender())
    email.deliver_pending(app_conn, email.FakeSender())

    assert first.sent >= 1 and first.failed == 0
    assert deliveries(conn, pmc) == [("fake", None)]


def test_a_failed_delivery_is_recorded_and_tried_again_up_to_the_limit(conn, app_conn, pmc):
    join(conn, pmc)
    email.write_bill_approval(conn, pmc.pmc_id, bill(conn, pmc), URL)
    failing = Failing()

    for _ in range(email.MAX_ATTEMPTS + 2):
        email.deliver_pending(app_conn, failing)
    email.deliver_pending(app_conn, email.FakeSender())  # given up on: not tried again

    disconnected = ("smtp", "SMTPServerDisconnected: Connection unexpectedly closed")
    assert deliveries(conn, pmc) == [disconnected] * email.MAX_ATTEMPTS


def test_a_failure_then_a_delivery(conn, app_conn, pmc):
    join(conn, pmc)
    email.write_bill_approval(conn, pmc.pmc_id, bill(conn, pmc), URL)

    assert email.deliver_pending(app_conn, Failing(OSError("network is down"))).failed >= 1
    assert email.deliver_pending(app_conn, email.FakeSender()).sent >= 1
    assert deliveries(conn, pmc) == [("smtp", "OSError: network is down"), ("fake", None)]


def test_an_email_is_delivered_once_even_if_recorded_twice(conn, pmc):
    join(conn, pmc)
    email.write_bill_approval(conn, pmc.pmc_id, bill(conn, pmc), URL)
    (email_id,) = conn.execute(
        "SELECT id FROM trust_emails WHERE pmc_id = %s", (pmc.pmc_id,)
    ).fetchone()
    record = "INSERT INTO trust_email_deliveries (email_id, pmc_id, sender) VALUES (%s, %s, 'fake')"
    conn.execute(record, (email_id, pmc.pmc_id))

    with pytest.raises(psycopg.errors.UniqueViolation):
        conn.execute(record, (email_id, pmc.pmc_id))


@pytest.mark.timing
def test_two_deliveries_at_once_send_each_email_once(conn, database_url, pmc):
    invite = join(conn, pmc)
    to = conn.execute(
        "SELECT email FROM trust_logins WHERE id = %s", (invite.login_id,)
    ).fetchone()[0]
    for n in range(3):
        email.write_bill_approval(conn, pmc.pmc_id, bill(conn, pmc, reference=f"ROOF-{n}"), URL)
    sent: list[str] = []
    gate = threading.Barrier(2)

    class Slow:
        name = "fake"

        def send(self, message):
            if message.to == to:
                sent.append(message.subject)

    def run():
        with psycopg.connect(database_url, autocommit=True) as worker:
            worker.execute("SET ROLE trust_app")
            gate.wait(timeout=10)
            email.deliver_pending(worker, Slow())

    threads = [threading.Thread(target=run) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    assert len(sent) == 3
    assert len(deliveries(conn, pmc)) == 3


def test_deliveries_and_emails_are_kept_for_good(conn, pmc):
    join(conn, pmc)
    email.write_bill_approval(conn, pmc.pmc_id, bill(conn, pmc), URL)
    email.deliver_pending(conn, email.FakeSender())

    for statement in (
        "UPDATE trust_emails SET subject = 'x'",
        "DELETE FROM trust_email_deliveries",
    ):
        with pytest.raises(psycopg.errors.RestrictViolation, match="append-only"):
            conn.execute(statement)


def test_the_ai_reads_no_email(ai_conn):
    for table in ("trust_emails", "trust_email_deliveries"):
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            ai_conn.execute(f"SELECT 1 FROM {table}")


# --- the senders ---------------------------------------------------------------------------


def test_the_server_picks_the_sender():
    assert email.sender_named("", {}) is None
    assert isinstance(email.sender_named("fake", {}), email.FakeSender)
    smtp = email.sender_named(
        "smtp",
        {
            "PMS_SMTP_HOST": "mail.example.com",
            "PMS_EMAIL_FROM": "pms@example.com",
            "PMS_SMTP_USERNAME": "pms",
            "PMS_SMTP_PASSWORD": "synthetic",
        },
    )
    assert smtp == email.SmtpSender("mail.example.com", 587, "pms", "synthetic", "pms@example.com")
    with pytest.raises(ValueError, match="needs PMS_SMTP_HOST and PMS_EMAIL_FROM"):
        email.sender_named("smtp", {})
    with pytest.raises(ValueError, match="not 'postcard'"):
        email.sender_named("postcard", {})


def test_smtp_sends_over_starttls_and_signs_in(monkeypatch):
    seen: list[tuple] = []

    class Server:
        def __init__(self, host, port, timeout):
            seen.append(("connect", host, port))

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def starttls(self, context):
            seen.append(("starttls",))

        def login(self, username, password):
            seen.append(("login", username))

        def send_message(self, message):
            seen.append(("send", message["From"], message["To"], message["Subject"]))

    monkeypatch.setattr(smtplib, "SMTP", Server)
    email.SmtpSender("mail.example.com", 2525, "pms", "synthetic", "pms@example.com").send(
        email.Message("owner@example.com", "Hello", "Body")
    )

    assert seen == [
        ("connect", "mail.example.com", 2525),
        ("starttls",),
        ("login", "pms"),
        ("send", "pms@example.com", "owner@example.com", "Hello"),
    ]


def test_the_command_delivers_what_waits(conn, database_url, pmc, monkeypatch, capsys):
    join(conn, pmc)
    email.write_bill_approval(conn, pmc.pmc_id, bill(conn, pmc), URL)
    monkeypatch.setenv("DATABASE_URL", database_url)

    monkeypatch.delenv("PMS_EMAIL_SENDER", raising=False)
    assert email.main(["deliver"]) == 0
    assert deliveries(conn, pmc) == []
    monkeypatch.setenv("PMS_EMAIL_SENDER", "fake")
    assert email.main(["deliver"]) == 0
    assert email.main(["send"]) == 2

    assert deliveries(conn, pmc) == [("fake", None)]
    assert capsys.readouterr().out.startswith("sent ")


def test_dollars_are_written_with_cents_and_commas():
    assert email.dollars(Decimal("1234.5")) == "$1,234.50"
    assert email.dollars(Decimal("0")) == "$0.00"
