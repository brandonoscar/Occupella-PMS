"""The PDF an automatic email carries (src/pms/attachments.py, migration 0111; the founder's review
q029, q045 and q055): a payment receipt's receipt and an owner statement's whole month, made when
the email is handed to the sender; no other email carries one, and one whose file can't be made
is a failed delivery that says why. Every name and address is synthetic."""

import io
import smtplib
import uuid
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from helpers import add_staff, add_unit, contribute, make_pmc, open_lease, transfer_batch
from pypdf import PdfReader

from pms import email
from pms.api import rent

SEPTEMBER = date(2026, 9, 1)


class Keeping:
    """Keeps what it is handed, as a server would send it."""

    name = "fake"

    def __init__(self):
        self.sent: list[email.Message] = []

    def send(self, message):
        self.sent.append(message)


@pytest.fixture
def pmc(conn):
    return make_pmc(conn)


def tenant_of(conn, pmc):
    return conn.execute(
        "SELECT tenant_id FROM trust_ledger_accounts WHERE ledger_account_id = %s",
        (pmc.prepaid_rent,),
    ).fetchone()[0]


def write(conn, pmc, kind, subject, **about):
    columns = ["pmc_id", "kind", "to_address", "subject", "body", *about]
    conn.execute(
        f"INSERT INTO trust_emails ({', '.join(columns)})"
        f" VALUES ({', '.join(['%s'] * len(columns))})",
        (pmc.pmc_id, kind, "someone@example.com", subject, "Body", *about.values()),
    )


def mine(keeping, tag):
    """What this test wrote (deliver_pending sends every PMC's emails)."""
    return {m.subject.split(" ", 1)[1]: m for m in keeping.sent if m.subject.startswith(tag)}


def text(data):
    return " ".join(page.extract_text() for page in PdfReader(io.BytesIO(data)).pages)


@pytest.fixture
def tag():
    return f"[{uuid.uuid4().hex[:8]}]"


def test_a_receipt_and_a_statement_carry_their_pdf_and_nothing_else_does(conn, app_conn, pmc, tag):
    unit = add_unit(conn, pmc.pmc_id, pmc.owners[0].property_id, name="Unit 4")
    tenant = tenant_of(conn, pmc)
    lease = open_lease(conn, pmc.pmc_id, unit, SEPTEMBER, None, "900.00", [tenant])
    with conn.transaction():
        payment = rent.record_payment(
            conn, pmc.pmc_id, lease, uuid.uuid4(), Decimal("125.00"), date(2026, 10, 3)
        ).transfers[0]
    owner = pmc.owners[0]
    contribute(conn, pmc.pmc_id, owner.account, "c-1", "250.00", datetime(2026, 9, 10, tzinfo=UTC))
    staff = add_staff(conn, pmc.pmc_id)
    write(
        conn,
        pmc,
        "payment_receipt",
        f"{tag} receipt",
        tenant_id=tenant,
        lease_id=lease,
        transfer_id=payment,
    )
    write(
        conn, pmc, "owner_statement", f"{tag} statement", owner_id=owner.owner_id, period=SEPTEMBER
    )
    write(conn, pmc, "message", f"{tag} message", tenant_id=tenant, sent_by=staff)

    keeping = Keeping()
    email.deliver_pending(app_conn, keeping)
    sent = mine(keeping, tag)

    assert [a.filename for a in sent["receipt"].attachments] == ["receipt-2026-10-03.pdf"]
    assert [a.filename for a in sent["statement"].attachments] == [
        "owner-statement-2026-09-01-2026-09-30.pdf"
    ]
    assert sent["message"].attachments == ()
    receipt = text(sent["receipt"].attachments[0].data)
    assert "Payment receipt" in receipt
    assert "$125.00" in receipt
    statement = text(sent["statement"].attachments[0].data)
    assert "Owner statement, 2026-09-01 to 2026-09-30" in statement
    assert "250.00" in statement
    assert {a.content_type for m in sent.values() for a in m.attachments} == {"application/pdf"}


def test_a_receipt_whose_file_cant_be_made_is_a_failed_delivery_saying_why(
    conn, app_conn, pmc, tag
):
    unit = add_unit(conn, pmc.pmc_id, pmc.owners[0].property_id, name="Unit 4")
    tenant = tenant_of(conn, pmc)
    lease = open_lease(conn, pmc.pmc_id, unit, SEPTEMBER, None, "900.00", [tenant])
    # A transfer that is no tenant payment: there is no receipt to make.
    (other,) = transfer_batch(conn, [(pmc.operating_cash, pmc.owners[0].account, "5.00")])
    write(
        conn,
        pmc,
        "payment_receipt",
        f"{tag} receipt",
        tenant_id=tenant,
        lease_id=lease,
        transfer_id=other,
    )

    keeping = Keeping()
    # Tried at each delivery until it is given up on, after five failures.
    for _ in range(email.MAX_ATTEMPTS + 1):
        email.deliver_pending(app_conn, keeping)

    assert mine(keeping, tag) == {}
    assert conn.execute(
        "SELECT d.error FROM trust_email_deliveries AS d JOIN trust_emails AS e"
        " ON e.id = d.email_id WHERE e.pmc_id = %s",
        (pmc.pmc_id,),
    ).fetchall() == [("its attachment could not be made: no such tenant payment",)] * (
        email.MAX_ATTEMPTS
    )


def test_smtp_sends_the_attachment_beside_the_text_and_html(monkeypatch):
    seen = []

    class Server:
        def __init__(self, host, port, timeout):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def starttls(self, context):
            pass

        def send_message(self, message):
            seen.append(message)

    monkeypatch.setattr(smtplib, "SMTP", Server)
    sender = email.SmtpSender("mail.example.com", 2525, "", "", "pms@example.com")
    sender.send(
        email.Message(
            "tenant@example.com",
            "Your receipt",
            "Attached.",
            html="<p>Attached.</p>",
            attachments=(email.Attachment("receipt-2026-10-03.pdf", b"%PDF-1.4 synthetic"),),
        )
    )

    (message,) = seen
    parts = [(p.get_content_type(), p.get_filename()) for p in message.walk()]
    assert ("text/plain", None) in parts
    assert ("text/html", None) in parts
    assert ("application/pdf", "receipt-2026-10-03.pdf") in parts
    (pdf,) = [p for p in message.walk() if p.get_content_type() == "application/pdf"]
    assert pdf.get_payload(decode=True) == b"%PDF-1.4 synthetic"
