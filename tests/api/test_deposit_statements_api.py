"""A deposit's return deadline and its statement (migration 0120; the founder's review q040 and
q039; src/pms/api/moveout.py): the day each lease's deposit is due back by the strictest deadline
found, receipts kept for the fees a deposit keeps, and each tenant's statement as a letter to their
forwarding address carrying the receipts and move-out photos, downloaded by staff or the tenant
and emailed. Every name and address is synthetic."""

import base64
import io
import uuid
from datetime import UTC, date, datetime, timedelta

import pytest
from fpdf import FPDF
from helpers import (
    add_document,
    add_tenant,
    add_unit,
    make_pmc,
    open_lease,
    png,
    transfer_batch,
)
from people import join_as_owner, join_as_staff, join_as_tenant, tenant_of
from pypdf import PdfReader

from pms import email
from pms.api import moveout


def today():
    """The day the API takes as today (UTC), read when a test needs it."""
    return datetime.now(UTC).date()


def invoice_pdf(words="Synthetic invoice CC-1"):
    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Helvetica", size=12)
    pdf.cell(0, 10, words)
    return bytes(pdf.output())


def encoded(content):
    return base64.b64encode(content).decode()


def text(data):
    return "\n".join(page.extract_text() for page in PdfReader(io.BytesIO(data)).pages)


def ok(answer, status=200):
    assert answer.status_code == status, answer.text
    return answer.json() if answer.content else None


def refused(answer, status, detail=None):
    assert answer.status_code == status, answer.text
    if detail is not None:
        assert detail in str(answer.json()["detail"]), answer.text


@pytest.fixture
def staff(client, conn, app_conn, pmc):
    return join_as_staff(client, conn, app_conn, pmc, name="Manager 1")


@pytest.fixture
def lease(conn, pmc):
    """Tenant 1's month-to-month lease of Unit 1 at Property 1, with a 1200.00 deposit."""
    started = today() - timedelta(days=200)
    unit = add_unit(conn, pmc.pmc_id, pmc.owners[0].property_id)
    lease_id = open_lease(conn, pmc.pmc_id, unit, started, None, "1000.00", [tenant_of(conn, pmc)])
    transfer_batch(
        conn,
        [(pmc.deposit_cash, pmc.tenant_deposit, "1200.00")],
        datetime.combine(started, datetime.min.time(), UTC),
    )
    return lease_id


def end(client, lease, on):
    ok(client.post(f"/api/move-out/{lease}/end", json={"ends_on": str(on)}), 204)


def charge(client, lease, on, memo="Carpet cleaning", amount="350.00"):
    body = {"amount": amount, "due_on": str(on), "memo": memo}
    return ok(client.post(f"/api/move-out/{lease}/deductions", json=body), 201)["id"]


def receipt(client, lease, fee, content=None, title="CleanCo invoice", content_type=None):
    body = {
        "title": title,
        "content_type": content_type or "application/pdf",
        "content_base64": encoded(content if content is not None else invoice_pdf()),
    }
    return client.post(f"/api/move-out/{lease}/fees/{fee}/receipts", json=body)


def give_back(client, lease):
    body = {"request_id": str(uuid.uuid4()), "on": str(today())}
    return ok(client.post(f"/api/move-out/{lease}/return", json=body), 201)


def shown(client, lease):
    return {m["lease_id"]: m for m in ok(client.get("/api/move-out"))}[str(lease)]


def test_return_by_is_the_strictest_deadline_found_from_the_last_day():
    last = date(2026, 10, 1)
    assert moveout.return_by(None, moveout.Decimal("0")) is None
    assert moveout.return_by(last, moveout.Decimal("0")) == date(2026, 10, 11)
    assert moveout.return_by(last, moveout.Decimal("0.01")) == date(2026, 10, 15)
    assert [moveout.standing_of(date(2026, 10, 15), date(2026, 10, d)) for d in (12, 14)] == [
        "due in 3 days",
        "due in 1 day",
    ]
    assert moveout.standing_of(date(2026, 10, 15), date(2026, 10, 15)) == "due today"
    assert [moveout.standing_of(date(2026, 10, 15), date(2026, 10, d)) for d in (16, 20)] == [
        "1 day late",
        "5 days late",
    ]


def test_a_lease_shows_when_its_deposit_is_due_back_and_its_fees_with_their_receipts(
    client, conn, staff, pmc, lease
):
    assert shown(client, lease)["return_by"] is None  # no last day yet

    ended = today() - timedelta(days=3)
    end(client, lease, ended)
    # Owing nothing: 10 days (Montana's, when nothing is kept).
    assert shown(client, lease)["return_by"] == str(ended + timedelta(days=10))

    fee = charge(client, lease, ended)
    other_fee = charge(client, lease, ended, memo="Broken blind", amount="40.00")
    made = ok(receipt(client, lease, fee), 201)
    ok(receipt(client, lease, fee, png(), "Before and after", "image/png"), 201)

    listed = shown(client, lease)
    # Owing something: 14 days (Hawaii, New York).
    assert listed["return_by"] == str(ended + timedelta(days=14))
    assert [
        (f["memo"], f["amount"], [r["title"] for r in f["receipts"]]) for f in listed["fees"]
    ] == [
        ("Carpet cleaning", "350.00", ["CleanCo invoice", "Before and after"]),
        ("Broken blind", "40.00", []),
    ]
    assert listed["fees"][0]["receipts"][0]["id"] == made["id"]
    assert listed["fees"][1]["id"] == other_fee
    # The receipt is one of the lease's documents, on its Files tab.
    assert conn.execute(
        "SELECT kind, lease_id FROM trust_documents WHERE id = %s", (made["id"],)
    ).fetchone() == ("lease_document", lease)

    give_back(client, lease)
    assert shown(client, lease)["return_by"] is None


def test_a_receipt_is_for_a_fee_of_the_lease(client, conn, staff, pmc, lease):
    ended = today() - timedelta(days=3)
    end(client, lease, ended)
    fee = charge(client, lease, ended)
    unit = add_unit(conn, pmc.pmc_id, pmc.owners[0].property_id, "Unit 2")
    tenant = add_tenant(conn, pmc.pmc_id, pmc.owners[0].property_id, "Tenant 2")
    elsewhere = open_lease(conn, pmc.pmc_id, unit, ended, None, "900.00", [tenant])
    rent = conn.execute(
        "INSERT INTO trust_charges (pmc_id, lease_id, due_on, kind, amount)"
        " VALUES (%s, %s, %s, 'rent', 100) RETURNING id",
        (pmc.pmc_id, lease, ended),
    ).fetchone()[0]

    refused(receipt(client, elsewhere, fee), 404, "no such fee on this lease")
    refused(receipt(client, lease, rent), 404, "no such fee on this lease")
    refused(receipt(client, lease, uuid.uuid4()), 404, "no such fee on this lease")
    refused(receipt(client, uuid.uuid4(), fee), 404, "no such lease")
    refused(receipt(client, lease, fee, b"not a pdf"), 422, "isn't a PDF")
    bad = {"title": "x", "content_type": "application/pdf", "content_base64": "@@@@"}
    refused(client.post(f"/api/move-out/{lease}/fees/{fee}/receipts", json=bad), 422)
    refused(receipt(client, lease, fee, title=" "), 422)


def test_the_statement_is_a_letter_to_the_forwarding_address_with_its_receipts_and_photos(
    client, conn, staff, pmc, lease
):
    ended = today() - timedelta(days=3)
    end(client, lease, ended)
    fee = charge(client, lease, ended)
    ok(receipt(client, lease, fee), 201)
    add_document(conn, pmc.pmc_id, "move_out_photo", lease, png(), "image/png", "Living room")
    tenant = tenant_of(conn, pmc)
    forwarding = {
        "email": "tenant1@example.com",
        "address_line_1": "100 Example Way",
        "address_line_2": "Apt 4",
        "city": "Sampletown",
        "state": "CA",
        "postal_code": "90000",
    }
    ok(client.post(f"/api/tenants/{tenant}/contact", json=forwarding), 204)
    path = f"/api/move-out/{lease}/statement"

    refused(client.get(path, params={"tenant_id": str(tenant)}), 409, "hasn't been returned")
    give_back(client, lease)
    answer = client.get(path, params={"tenant_id": str(tenant)})

    assert answer.status_code == 200
    assert answer.headers["content-type"] == "application/pdf"
    read = text(answer.content)
    for words in (
        "Security deposit statement",
        "Tenant 1",
        "100 Example Way",
        "Apt 4",
        "Sampletown, CA 90000",
        "Deposit held: Tenant 1",
        "Carpet cleaning",
        "CleanCo invoice",
        "Attached after this letter: 1 receipt file(s) and 1 move-out photo(s).",
        "Synthetic invoice CC-1",  # the receipt's own page
        "Living room",  # the photo's page
    ):
        assert words in read, words
    # The letter, the invoice's page and the photo's page.
    assert len(PdfReader(io.BytesIO(answer.content)).pages) == 3
    refused(client.get(path, params={"tenant_id": str(uuid.uuid4())}), 404, "no such tenant")
    refused(client.get(path, params={"tenant_id": "not an id"}), 422)


class Keeping:
    """Keeps what it is handed, as a server would send it."""

    name = "fake"

    def __init__(self):
        self.sent = []

    def send(self, message):
        self.sent.append(message)


def test_staff_email_each_tenant_their_statement_with_its_pdf(client, conn, staff, pmc, lease):
    ended = today() - timedelta(days=3)
    end(client, lease, ended)
    tenant = tenant_of(conn, pmc)
    path = f"/api/move-out/{lease}/statement/send"

    refused(client.post(path, json={}), 409, "hasn't been returned")
    give_back(client, lease)
    # No email on file and no login: nothing is written to them.
    assert ok(client.post(path, json={}), 201) == {"emailed": [], "without_email": ["Tenant 1"]}
    ok(client.post(f"/api/tenants/{tenant}/contact", json={"email": "tenant1@example.com"}), 204)
    assert ok(client.post(path, json={}), 201) == {
        "emailed": [{"name": "Tenant 1", "to": "tenant1@example.com"}],
        "without_email": [],
    }
    ok(client.post(path, json={}), 201)  # again: written again
    written = conn.execute(
        "SELECT tenant_id, lease_id, subject, body LIKE '%%tenant portal%%' FROM trust_emails"
        " WHERE pmc_id = %s AND kind = 'deposit_statement'",
        (pmc.pmc_id,),
    ).fetchall()
    subject = "Your security deposit statement: Property 1, Unit 1"
    assert written == [(tenant, lease, subject, False)] * 2

    keeping = Keeping()
    email.deliver_pending(conn, keeping)
    mine = [m for m in keeping.sent if m.to == "tenant1@example.com"]
    assert len(mine) == 2
    (attached,) = mine[0].attachments
    assert attached.filename == "security-deposit-statement.pdf"
    assert "Security deposit statement" in text(attached.data)


def test_a_statement_that_cannot_be_made_is_a_failed_delivery(conn, pmc, lease):
    """An email of a lease whose deposit isn't returned (written by hand) can't carry it."""
    conn.execute(
        "INSERT INTO trust_emails (pmc_id, kind, tenant_id, lease_id, to_address, subject, body)"
        " VALUES (%s, 'deposit_statement', %s, %s, 'tenant1@example.com', 'Statement F1', 'B')",
        (pmc.pmc_id, tenant_of(conn, pmc), lease),
    )
    email.deliver_pending(conn, Keeping())
    assert conn.execute(
        "SELECT d.error FROM trust_email_deliveries AS d JOIN trust_emails AS e"
        " ON e.id = d.email_id WHERE e.subject = 'Statement F1'"
    ).fetchone() == ("its attachment could not be made: the deposit hasn't been returned yet",)


def test_the_tenant_downloads_their_own_statement(
    client, other_client, conn, app_conn, staff, pmc, lease
):
    ended = today() - timedelta(days=3)
    end(client, lease, ended)
    unit = add_unit(conn, pmc.pmc_id, pmc.owners[0].property_id, "Unit 2")
    stranger = add_tenant(conn, pmc.pmc_id, pmc.owners[0].property_id, "Tenant 2")
    theirs = open_lease(conn, pmc.pmc_id, unit, ended, None, "900.00", [stranger])
    join_as_tenant(other_client, conn, app_conn, pmc)
    mine = f"/api/tenant/leases/{lease}/deposit-statement"

    refused(other_client.get(mine), 409, "hasn't been returned")
    give_back(client, lease)
    # A tenant who signs in is told it is in the portal too.
    ok(client.post(f"/api/move-out/{lease}/statement/send", json={}), 201)
    assert conn.execute(
        "SELECT body LIKE '%%in the tenant portal too%%' FROM trust_emails"
        " WHERE lease_id = %s AND kind = 'deposit_statement'",
        (lease,),
    ).fetchone() == (True,)
    answer = other_client.get(mine)

    assert answer.status_code == 200
    assert answer.headers["content-type"] == "application/pdf"
    assert "Tenant 1" in text(answer.content)
    refused(other_client.get(f"/api/tenant/leases/{theirs}/deposit-statement"), 404)


def test_the_dashboard_lists_deposits_due_back(client, conn, staff, pmc, lease):
    ended = today() - timedelta(days=3)
    end(client, lease, ended)
    charge(client, lease, ended)
    # A lease that has not ended, and one whose tenants hold no deposit, aren't listed.
    unit = add_unit(conn, pmc.pmc_id, pmc.owners[0].property_id, "Unit 2")
    tenant = add_tenant(conn, pmc.pmc_id, pmc.owners[0].property_id, "Tenant 2")
    open_lease(conn, pmc.pmc_id, unit, ended - timedelta(days=90), ended, "900.00", [tenant])

    def due(on):
        return ok(client.get("/api/today", params={"on": str(on)}))["deposits_to_return"]

    assert due(ended) == []  # its last day: not past yet
    assert due(ended + timedelta(days=1)) == [
        {
            "lease_id": str(lease),
            "property": "Property 1",
            "unit": "Unit 1",
            "tenants": ["Tenant 1"],
            "held": "1200.00",
            "return_by": str(ended + timedelta(days=14)),
            "standing": "due in 13 days",
        }
    ]
    assert due(ended + timedelta(days=16))[0]["standing"] == "2 days late"
    give_back(client, lease)
    assert due(ended + timedelta(days=16)) == []


@pytest.mark.parametrize("caller", ["nobody", "owner", "tenant"])
def test_only_staff_keep_receipts_and_send_statements(client, conn, app_conn, pmc, lease, caller):
    if caller == "owner":
        join_as_owner(client, app_conn, pmc)
    elif caller == "tenant":
        join_as_tenant(client, conn, app_conn, pmc)
    status = 401 if caller == "nobody" else 403

    assert receipt(client, lease, uuid.uuid4()).status_code == status
    params = {"tenant_id": str(uuid.uuid4())}
    assert client.get(f"/api/move-out/{lease}/statement", params=params).status_code == status
    assert client.post(f"/api/move-out/{lease}/statement/send", json={}).status_code == status


def test_another_companys_lease_reads_as_not_found(client, conn, staff, pmc):
    other = make_pmc(conn)
    unit = add_unit(conn, other.pmc_id, other.owners[0].property_id)
    theirs = open_lease(conn, other.pmc_id, unit, today(), None, "900.00", [tenant_of(conn, other)])
    params = {"tenant_id": str(tenant_of(conn, other))}

    refused(receipt(client, theirs, uuid.uuid4()), 404, "no such lease")
    refused(client.get(f"/api/move-out/{theirs}/statement", params=params), 404)
    refused(client.post(f"/api/move-out/{theirs}/statement/send", json={}), 404)
