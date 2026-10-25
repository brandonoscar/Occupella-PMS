"""Bills as Buildium has them (src/pms/api/payables.py, ADR 0006): a page of bills at a time,
narrowed by vendor, a due day and a property, each overdue or not; a bill's own page with its
expense account lines, its invoice, the email asking its owner and its history; a bill entered
split across expense accounts and with its invoice; and Pay bills, which pays every bill chosen
(what is left of each), all or none, recorded as sent by check or EFT. Always the session's
PMC."""

import base64
import json
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from helpers import add_agreement, balance, bank_sums, make_pmc, post, set_aside_bill
from people import join_as_staff

from pms import accounts, email

NOW = datetime.now(UTC)
TODAY = NOW.date()
DAY = timedelta(days=1)
PDF = b"%PDF-1.4 synthetic invoice\n"


@pytest.fixture
def staff(client, conn, app_conn, pmc):
    return join_as_staff(client, conn, app_conn, pmc)


def call(client, path, **body):
    return client.post(f"/api/payables/{path}", json=json.loads(json.dumps(body, default=str)))


def ok(answer, status=201):
    assert answer.status_code == status, answer.text
    return answer.json()


def refused(answer, status, detail=None):
    assert answer.status_code == status, answer.text
    if detail is not None:
        assert answer.json()["detail"] == detail


def fund(conn, pmc, amount, n=0):
    """Money into owner n's property account, whose bills need no approval up to $10,000."""
    add_agreement(conn, pmc.pmc_id, pmc.owners[n].account, TODAY - 60 * DAY, approval_limit="10000")
    post(
        conn,
        pmc.pmc_id,
        f"fund-{uuid.uuid4()}",
        [(pmc.operating_cash, pmc.owners[n].account, Decimal(amount))],
        NOW - 30 * DAY,
    )


def expense(client, name, line=14):
    return ok(call_setup(client, name, line))["id"]


def call_setup(client, name, line):
    return client.post("/api/setup/expense-accounts", json={"name": name, "schedule_e_line": line})


def new_bill(client, pmc, amount="200.00", reference="INV-1", n=0, **more):
    body = {
        "vendor_id": pmc.vendor_id,
        "account": pmc.owners[n].account,
        "reference": reference,
        "bill_date": TODAY - 2 * DAY,
        "due_on": TODAY + 10 * DAY,
        "amount": amount,
        "memo": "Gutter repair",
    } | more
    return call(client, "bills", **body)


def listed(client, query=""):
    return ok(client.get(f"/api/payables/bills{query}"), 200)


def test_bills_come_a_page_at_a_time_by_due_day_narrowed_by_vendor_day_and_property(
    client, conn, staff, pmc
):
    other_vendor = conn.execute(
        "INSERT INTO trust_vendors (pmc_id, display_name) VALUES (%s, 'Vendor 2') RETURNING id",
        (pmc.pmc_id,),
    ).fetchone()[0]
    ok(new_bill(client, pmc, reference="LATE-1", due_on=TODAY - DAY, bill_date=TODAY - 5 * DAY))
    ok(new_bill(client, pmc, reference="SOON-1", due_on=TODAY + 3 * DAY))
    ok(new_bill(client, pmc, reference="LATER-1", due_on=TODAY + 20 * DAY, n=1))
    ok(new_bill(client, pmc, reference="V2-1", vendor_id=other_vendor, due_on=TODAY + 5 * DAY))

    first = listed(client, "?per_page=2")
    second = listed(client, "?per_page=2&page=2")
    refs = lambda page: [b["reference"] for b in page["items"]]  # noqa: E731

    assert (refs(first), first["total"]) == (["LATE-1", "SOON-1"], 4)
    assert refs(second) == ["V2-1", "LATER-1"]
    late = first["items"][0]
    assert (late["overdue"], first["items"][1]["overdue"]) == (True, False)
    assert (late["vendor"], late["property"], late["property_id"]) == (
        "Vendor 1",
        "Property 1",
        str(pmc.owners[0].property_id),
    )
    assert refs(listed(client, f"?vendor={other_vendor}")) == ["V2-1"]
    assert refs(listed(client, f"?due_by={TODAY + 5 * DAY}")) == ["LATE-1", "SOON-1", "V2-1"]
    assert refs(listed(client, f"?property={pmc.owners[1].property_id}")) == ["LATER-1"]
    assert listed(client, f"?vendor={uuid.uuid4()}") == {
        "items": [],
        "total": 0,
        "page": 1,
        "per_page": 50,
    }


def test_a_bill_is_entered_split_across_expense_accounts_with_its_invoice(client, conn, staff, pmc):
    repairs, supplies = expense(client, "Repairs"), expense(client, "Supplies", 15)
    invoice = {
        "title": "Invoice FIX-1",
        "content_type": "application/pdf",
        "content_base64": base64.b64encode(PDF).decode(),
    }
    lines = [
        {"expense_account_id": repairs, "amount": "200.00"},
        {"expense_account_id": supplies, "amount": "100.00"},
    ]

    made = ok(new_bill(client, pmc, "300.00", "FIX-1", lines=lines, invoice=invoice))["id"]
    page = ok(client.get(f"/api/payables/bills/{made}"), 200)

    assert (page["bill"]["split"], page["bill"]["expense_account"]) == (True, None)
    assert [(x["expense_account"], x["amount"]) for x in page["lines"]] == [
        ("Repairs", "200.00"),
        ("Supplies", "100.00"),
    ]
    (kept,) = page["files"]
    assert (kept["title"], kept["content_type"], kept["size"]) == (
        "Invoice FIX-1",
        "application/pdf",
        len(PDF),
    )
    served = client.get(f"/api/documents/{kept['id']}/file")
    assert (served.status_code, served.content) == (200, PDF)
    assert [e["what"] for e in page["history"]] == ["Entered"]
    assert page["email"] is None


def test_a_bill_is_moved_to_a_split_and_back_to_one_account(client, conn, staff, pmc):
    repairs, water = expense(client, "Repairs"), expense(client, "Water", 17)
    made = ok(new_bill(client, pmc, "80.00", expense_account_id=repairs))["id"]

    def lines():
        page = ok(client.get(f"/api/payables/bills/{made}"), 200)
        return [(x["expense_account"], x["amount"]) for x in page["lines"]]

    whole = lines()
    split = [
        {"expense_account_id": repairs, "amount": "50.00"},
        {"expense_account_id": water, "amount": "30.00"},
    ]
    ok(call(client, f"bills/{made}/expense-account", lines=split))
    shown_split = lines()
    ok(call(client, f"bills/{made}/expense-account", expense_account_id=water))

    assert whole == [("Repairs", "80.00")]
    assert shown_split == [("Repairs", "50.00"), ("Water", "30.00")]
    assert lines() == [("Water", "80.00")]
    # None yet: one line, the whole bill, in no account.
    bare = ok(new_bill(client, pmc, "10.00", "BARE-1"))["id"]
    assert ok(client.get(f"/api/payables/bills/{bare}"), 200)["lines"] == [
        {"expense_account_id": None, "expense_account": None, "amount": "10.00"}
    ]


@pytest.mark.parametrize(
    ("change", "detail"),
    [
        ({"amounts": ["200.00", "50.00"]}, "the lines come to $250.00, not the bill's $300.00"),
        ({"same": True}, "a bill's lines are each in a different expense account"),
        ({"both": True}, "give a bill one expense account or lines, not both"),
    ],
)
def test_lines_that_dont_split_the_bill_are_refused(client, conn, staff, pmc, change, detail):
    repairs, supplies = expense(client, "Repairs"), expense(client, "Supplies", 15)
    amounts = change.get("amounts", ["200.00", "100.00"])
    second = repairs if change.get("same") else supplies
    lines = [
        {"expense_account_id": repairs, "amount": amounts[0]},
        {"expense_account_id": second, "amount": amounts[1]},
    ]
    more = {"expense_account_id": repairs} if change.get("both") else {}

    refused(new_bill(client, pmc, "300.00", lines=lines, **more), 422, detail)
    assert listed(client)["total"] == 0


def test_an_invoice_that_isnt_a_pdf_jpeg_or_png_enters_no_bill(client, conn, staff, pmc):
    invoice = {
        "title": "Invoice",
        "content_type": "application/pdf",
        "content_base64": base64.b64encode(b"not a pdf").decode(),
    }

    refused(new_bill(client, pmc, invoice=invoice), 422, "the file isn't a PDF")
    refused(
        new_bill(client, pmc, invoice=invoice | {"content_base64": "!!!!"}),
        422,
        "the file isn't base64",
    )
    assert listed(client)["total"] == 0
    refused(new_bill(client, pmc, lines=[{"expense_account_id": str(uuid.uuid4())}] * 2), 422)
    unknown = [{"expense_account_id": str(uuid.uuid4()), "amount": "100.00"} for _ in range(2)]
    refused(new_bill(client, pmc, lines=unknown), 404, "no such expense account")


def owner_signs_in(conn, pmc):
    owner = pmc.owners[0].owner_id
    address = f"owner.{owner.hex[:6]}@example.com"
    made = accounts.create_invite(conn, pmc.pmc_id, "owner", owner, address, None)
    accounts.accept_invite(conn, made.token, "a long synthetic passphrase")
    return address


def test_a_bills_page_tells_its_story_email_approval_and_payment(client, conn, staff, pmc):
    client.app.state.email = email.FakeSender()
    to = owner_signs_in(conn, pmc)
    fund(conn, pmc, "1000.00")
    add_agreement(conn, pmc.pmc_id, pmc.owners[0].account, TODAY - 59 * DAY, approval_limit="500")
    made = ok(new_bill(client, pmc, "800.00"))["id"]
    ok(call(client, f"bills/{made}/approval", approved_on=TODAY, approved_by="Owner 1, by phone"))
    ok(call(client, f"bills/{made}/pay", on=TODAY, method="EFT"))

    page = ok(client.get(f"/api/payables/bills/{made}"), 200)

    assert page["bill"]["status"] == "paid"
    assert (page["email"]["to"], page["email"]["status"]) == (to, "sent")
    assert [e["what"] for e in page["history"]] == [
        "Entered",
        f"Email to {to} asking Owner 1 to approve or decline it: sent",
        "Approved by Owner 1, by phone",
        "Paid $800.00 by ACH",
    ]
    assert page["email"]["sent_at"] is not None


def test_another_companys_bill_has_no_page(client, conn, staff):
    theirs = make_pmc(conn)
    bill = conn.execute(
        "INSERT INTO trust_bills (pmc_id, vendor_id, ledger_account_id, reference, bill_date,"
        " due_on, amount, memo) VALUES (%s, %s, %s, 'X-1', %s, %s, 5, 'x') RETURNING id",
        (theirs.pmc_id, theirs.vendor_id, theirs.owners[0].account, TODAY, TODAY),
    ).fetchone()[0]

    refused(client.get(f"/api/payables/bills/{bill}"), 404, "no such bill")
    refused(call(client, "bills/pay", on=TODAY, method="check", bill_ids=[bill]), 404)
    refused(client.get("/api/payables/bills/not-an-id"), 422)


def test_pay_bills_pays_each_bill_once_by_check_or_eft(client, conn, staff, pmc):
    fund(conn, pmc, "1000.00")
    fund(conn, pmc, "500.00", n=1)
    first = ok(new_bill(client, pmc, "200.00", "INV-1"))["id"]
    second = ok(new_bill(client, pmc, "150.00", "INV-2", n=1))["id"]
    # Set aside before ADR 0010: its money left the owner's account then, and it pays as before.
    set_aside_bill(conn, pmc.pmc_id, first, datetime.combine(TODAY, datetime.min.time(), UTC))
    (legacy,) = [b for b in listed(client)["items"] if b["id"] == first]
    run = {"on": TODAY, "method": "EFT", "bill_ids": [first, second]}

    paid = ok(call(client, "bills/pay", **run))
    again = ok(call(client, "bills/pay", **run | {"method": "check"}))

    assert [(p["bill_id"], p["vendor"], p["reference"], p["amount"]) for p in paid] == [
        (first, "Vendor 1", "INV-1", "200.00"),
        (second, "Vendor 1", "INV-2", "150.00"),
    ]
    assert again == paid  # sent again: nothing twice
    assert (legacy["status"], legacy["held"], legacy["held_since"]) == (
        "ready to pay",
        "200.00",
        str(TODAY),
    )
    assert (balance(conn, pmc.owners[0].account), balance(conn, pmc.owners[1].account)) == (
        Decimal("800.00"),
        Decimal("350.00"),
    )
    assert conn.execute(
        "SELECT method FROM trust_payout_methods WHERE transfer_id = ANY(%s)",
        ([p["transfer_id"] for p in paid],),
    ).fetchall() == [("EFT",), ("EFT",)]
    assert {b["status"] for b in listed(client, "?show=paid")["items"]} == {"paid"}
    assert set(bank_sums(conn, pmc.pmc_id).values()) == {Decimal(0)}


def test_pay_bills_pays_none_when_one_cant_be_paid(client, conn, staff, pmc):
    fund(conn, pmc, "250.00")
    first = ok(new_bill(client, pmc, "200.00", "INV-1"))["id"]
    second = ok(new_bill(client, pmc, "100.00", "INV-2"))["id"]

    answer = call(client, "bills/pay", on=TODAY, method="check", bill_ids=[first, second])

    refused(
        answer,
        409,
        "Owner 1's balance is $50.00 short of Vendor 1 INV-2: they need to send $50.00 first",
    )
    assert balance(conn, pmc.owners[0].account) == Decimal("250.00")
    assert [b["status"] for b in listed(client)["items"]] == ["ready to pay", "short"]


def test_pay_bills_waits_for_the_owners_approval_and_refuses_a_declined_bill(
    client, conn, staff, pmc
):
    fund(conn, pmc, "1000.00")
    add_agreement(conn, pmc.pmc_id, pmc.owners[0].account, TODAY - 59 * DAY, approval_limit="100")
    waiting = ok(new_bill(client, pmc, "300.00", "BIG-1"))["id"]
    declined = ok(new_bill(client, pmc, "300.00", "BIG-2"))["id"]
    ok(
        call(
            client,
            f"bills/{declined}/decline",
            declined_on=TODAY,
            declined_by="Owner 1",
            reason="Not ours",
        )
    )

    for bill in (waiting, declined):
        answer = call(client, "bills/pay", on=TODAY, method="check", bill_ids=[bill])
        assert answer.status_code == 409, answer.text
        assert answer.json()["detail"].startswith("Vendor 1 BIG-")
    assert balance(conn, pmc.owners[0].account) == Decimal("1000.00")


def test_pay_bills_takes_the_disburse_role(client, conn, app_conn, pmc):
    join_as_staff(client, conn, app_conn, pmc, roles=["approve"])
    fund(conn, pmc, "1000.00")
    made = ok(new_bill(client, pmc, "200.00"))["id"]

    answer = call(client, "bills/pay", on=TODAY, method="check", bill_ids=[made])

    assert answer.status_code == 403, answer.text
    assert balance(conn, pmc.owners[0].account) == Decimal("1000.00")


@pytest.mark.parametrize(
    "body",
    [
        {"on": str(TODAY), "method": "check", "bill_ids": []},
        {"on": str(TODAY), "method": "wire", "bill_ids": [str(uuid.uuid4())]},
        {"on": str(TODAY + DAY), "method": "check", "bill_ids": [str(uuid.uuid4())]},
    ],
)
def test_pay_bills_refuses_bad_input(client, staff, body):
    refused(client.post("/api/payables/bills/pay", json=body), 422)


def test_pay_bills_pays_each_bill_once_in_a_run(client, conn, staff, pmc):
    made = ok(new_bill(client, pmc))["id"]
    refused(
        call(client, "bills/pay", on=TODAY, method="check", bill_ids=[made, made]),
        422,
        "pay each bill once",
    )


@pytest.mark.parametrize("query", ["?show=late", "?due_by=soon", "?vendor=x", "?per_page=201"])
def test_a_bills_list_with_bad_terms_is_refused(client, staff, query):
    refused(client.get(f"/api/payables/bills{query}"), 422)
