"""A bill's life through the API (ADR 0010; src/pms/api/payables.py, src/pms/api/reconcile.py):
what it holds of its owner's balance, paying it in parts by check or ACH with a receipt, voiding
one entered by mistake, asking a short owner for money (once a day, to the address shown first),
the duplicate warning, and the bank statement: a check matched by its number and amount when the
statement comes in, and the less certain lines suggested for staff to confirm."""

import base64
import json
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from helpers import add_agreement, approve, balance, bank_sums, post
from people import join_as_owner, join_as_staff

from pms import email

NOW = datetime.now(UTC)
TODAY = NOW.date()
DAY = timedelta(days=1)
PDF = b"%PDF-1.4 synthetic receipt\n"


@pytest.fixture
def staff(client, conn, app_conn, pmc):
    return join_as_staff(client, conn, app_conn, pmc)


def call(client, path, **body):
    return client.post(f"/api/payables/{path}", json=json.loads(json.dumps(body, default=str)))


def ok(answer, status=201):
    assert answer.status_code == status, answer.text
    return answer.json() if answer.content else None


def refused(answer, status, detail=None):
    assert answer.status_code == status, answer.text
    if detail is not None:
        assert answer.json()["detail"] == detail


def fund(conn, pmc, amount, n=0, limit="1000"):
    add_agreement(conn, pmc.pmc_id, pmc.owners[n].account, TODAY - 60 * DAY, approval_limit=limit)
    post(
        conn,
        pmc.pmc_id,
        f"fund-{uuid.uuid4()}",
        [(pmc.operating_cash, pmc.owners[n].account, Decimal(amount))],
        NOW - 5 * DAY,
    )


def new_bill(client, pmc, amount, reference, due_in=10, n=0):
    return ok(
        call(
            client,
            "bills",
            vendor_id=pmc.vendor_id,
            account=pmc.owners[n].account,
            reference=reference,
            bill_date=TODAY - 2 * DAY,
            due_on=TODAY + due_in * DAY,
            amount=amount,
            memo="Gutter repair",
        )
    )["id"]


def page(client, bill):
    return ok(client.get(f"/api/payables/bills/{bill}"), 200)


def contact_email(conn, pmc, address, n=0):
    conn.execute(
        "INSERT INTO trust_contacts (pmc_id, owner_id, email) VALUES (%s, %s, %s)",
        (pmc.pmc_id, pmc.owners[n].owner_id, address),
    )


# --- holds and payments -----------------------------------------------------------------------


def test_a_bill_is_paid_in_parts_by_check_and_ach_with_a_receipt(client, conn, staff, pmc):
    fund(conn, pmc, "1000.00")
    bill = new_bill(client, pmc, "600.00", "INV-1")
    first = uuid.uuid4()
    receipt = {
        "title": "Stub 1042",
        "content_type": "application/pdf",
        "content_base64": base64.b64encode(PDF).decode(),
    }

    paid = ok(
        call(
            client,
            f"bills/{bill}/pay",
            on=TODAY,
            amount="250.00",
            check_number="1042",
            request_id=first,
            receipt=receipt,
        )
    )
    again = ok(call(client, f"bills/{bill}/pay", on=TODAY, amount="250.00", request_id=first))
    part = page(client, bill)
    too_much = call(client, f"bills/{bill}/pay", on=TODAY, amount="350.01", request_id=uuid.uuid4())
    ok(call(client, f"bills/{bill}/pay", on=TODAY, method="EFT", request_id=uuid.uuid4()))
    done = page(client, bill)

    assert again == paid  # a retry records nothing new
    assert (part["bill"]["status"], part["bill"]["paid"], part["bill"]["left"]) == (
        "partly paid",
        "250.00",
        "350.00",
    )
    assert (part["bill"]["held"], part["bill"]["held_since"]) == ("350.00", str(TODAY))
    assert [f["title"] for f in part["files"]] == ["Stub 1042"]
    refused(too_much, 409, "$350.00 is left to pay of Vendor 1 INV-1; $350.01 is more than that")
    assert done["bill"]["status"] == "paid"
    assert [(p["amount"], p["method"], p["check_number"]) for p in done["payments"]] == [
        ("250.00", "check", "1042"),
        ("350.00", "EFT", None),
    ]
    assert [e["what"] for e in done["history"]] == [
        "Entered",
        "Paid $250.00 by check 1042; $350.00 left to pay",
        "Paid $350.00 by ACH",
    ]
    assert balance(conn, pmc.owners[0].account) == Decimal("400.00")
    assert set(bank_sums(conn, pmc.pmc_id).values()) == {Decimal(0)}
    refused(
        call(client, f"bills/{bill}/pay", on=TODAY, request_id=uuid.uuid4()),
        409,
        "Vendor 1 INV-1 is paid in full already",
    )


def test_the_bills_list_says_what_each_bill_holds_and_what_the_owner_must_send(
    client, conn, staff, pmc
):
    fund(conn, pmc, "500.00")
    first = new_bill(client, pmc, "350.00", "B-1", due_in=5)
    second = new_bill(client, pmc, "300.00", "B-2", due_in=20)

    listed = {b["id"]: b for b in ok(client.get("/api/payables/bills"), 200)["items"]}

    assert (listed[first]["status"], listed[first]["held"], listed[first]["needed"]) == (
        "ready to pay",
        "350.00",
        None,
    )
    assert (
        listed[second]["status"],
        listed[second]["held"],
        listed[second]["short"],
        listed[second]["needed"],
    ) == ("short", "150.00", "150.00", "150.00")


def test_a_bill_dated_later_is_entered_and_holds_nothing_yet(client, conn, staff, pmc):
    fund(conn, pmc, "500.00")
    later = ok(
        call(
            client,
            "bills",
            vendor_id=pmc.vendor_id,
            account=pmc.owners[0].account,
            reference="LATER-1",
            bill_date=TODAY + 3 * DAY,
            due_on=TODAY + 10 * DAY,
            amount="100.00",
            memo="Next week's visit",
        )
    )["id"]

    shown = page(client, later)["bill"]

    assert (shown["status"], shown["held"], shown["short"]) == ("entered", None, None)


# --- voids --------------------------------------------------------------------------------------


def test_staff_void_a_bill_entered_by_mistake_and_its_hold_goes_back(client, conn, staff, pmc):
    fund(conn, pmc, "500.00")
    bill = new_bill(client, pmc, "400.00", "DUP-1")
    before = ok(client.get("/api/payables/owners"), 200)[0]

    ok(call(client, f"bills/{bill}/void", reason="Entered twice"))
    shown = page(client, bill)
    after = ok(client.get("/api/payables/owners"), 200)[0]

    assert (shown["bill"]["status"], shown["bill"]["voided"], shown["bill"]["declined_by"]) == (
        "void",
        True,
        "Staff 1",
    )
    assert shown["history"][-1]["what"] == (
        "Voided by Staff 1: Entered twice. Nothing was paid; the hold went back to Owner 1's"
        " balance."
    )
    assert (before["available"], after["available"]) == ("100.00", "500.00")
    refused(call(client, f"bills/{bill}/void", reason="Again"), 409, "bill DUP-1 is void already")
    refused(
        call(client, f"bills/{bill}/pay", on=TODAY), 409, "Vendor 1 DUP-1 is void: it is never paid"
    )


def test_no_void_once_money_was_taken_for_the_bill(client, conn, staff, pmc):
    fund(conn, pmc, "500.00")
    bill = new_bill(client, pmc, "400.00", "P-1")
    ok(call(client, f"bills/{bill}/pay", on=TODAY, amount="100.00", request_id=uuid.uuid4()))

    refused(
        call(client, f"bills/{bill}/void", reason="Mistake"),
        409,
        "money was taken from the owner's account for bill P-1 already; it can't be voided",
    )


# --- asking the owner ---------------------------------------------------------------------------


def test_a_short_owner_is_asked_once_a_day_at_the_address_shown_first(client, conn, staff, pmc):
    fund(conn, pmc, "100.00")
    contact_email(conn, pmc, "owner.one@example.com")
    bill = new_bill(client, pmc, "400.00", "BIG-1")

    shown = page(client, bill)["ask"]
    asked = ok(call(client, f"bills/{bill}/ask-owner"))
    again = call(client, f"bills/{bill}/ask-owner")
    after = page(client, bill)

    assert shown == {
        "owner": "Owner 1",
        "to": "owner.one@example.com",
        "amount": "300.00",
        "asked_today": False,
    }
    assert asked == {"to": "owner.one@example.com", "amount": "300.00"}
    refused(again, 409, "Owner 1 was asked about this bill today already")
    assert after["ask"]["asked_today"] is True
    ((to, subject, owner, body),) = conn.execute(
        "SELECT to_address, subject, owner_id, body FROM trust_emails"
        " WHERE kind = 'bill_funds_needed' AND bill_id = %s",
        (bill,),
    ).fetchall()
    assert (to, subject, owner) == (
        "owner.one@example.com",
        "Please send $300.00 for Property 1: Vendor 1's bill",
        pmc.owners[0].owner_id,
    )
    assert "Your account for Property 1 holds $100.00, which leaves $300.00" in body
    assert [a["to"] for a in after["asks"]] == ["owner.one@example.com"]
    # The owner sees it on their Email tab, and in the portal what to send.
    emails = ok(client.get(f"/api/emails?about=owner&about_id={pmc.owners[0].owner_id}"), 200)
    assert [e["kind"] for e in emails] == ["bill_funds_needed"]


def test_nobody_is_asked_without_an_address_or_a_shortfall(client, conn, staff, pmc):
    fund(conn, pmc, "100.00")
    short = new_bill(client, pmc, "400.00", "BIG-1", due_in=20)

    refused(
        call(client, f"bills/{short}/ask-owner"),
        409,
        "Owner 1 has no email on file: add one to their contact details first",
    )
    assert page(client, short)["ask"]["to"] is None

    covered = new_bill(client, pmc, "50.00", "SMALL-1", due_in=1)
    refused(
        call(client, f"bills/{covered}/ask-owner"),
        409,
        "Owner 1's balance covers this bill: nothing to ask for",
    )
    assert page(client, covered)["ask"] is None


def test_the_owner_sees_what_their_balance_is_short_of_in_the_portal(
    client, other_client, conn, app_conn, staff, pmc
):
    fund(conn, pmc, "100.00")
    new_bill(client, pmc, "400.00", "BIG-1")
    join_as_owner(other_client, app_conn, pmc)

    (shown,) = ok(other_client.get("/api/owner/bills"), 200)

    assert (shown["reference"], shown["short"], shown["needed"], shown["decision"]) == (
        "BIG-1",
        "300.00",
        "300.00",
        None,
    )


# --- a duplicate --------------------------------------------------------------------------------


def test_record_bill_warns_of_the_same_reference_but_for_case_and_punctuation(
    client, conn, staff, pmc
):
    fund(conn, pmc, "500.00")
    bill = new_bill(client, pmc, "100.00", "INV-100")

    def similar(reference):
        return ok(
            client.get(
                "/api/payables/similar-bills",
                params={"vendor_id": str(pmc.vendor_id), "reference": reference},
            ),
            200,
        )

    assert [(s["id"], s["reference"], s["status"]) for s in similar("inv 100")] == [
        (bill, "INV-100", "ready to pay")
    ]
    assert similar("INV-101") == []
    assert similar("--") == []
    refused(
        client.get("/api/payables/similar-bills", params={"vendor_id": "x", "reference": "a"}), 422
    )


# --- the bank statement -------------------------------------------------------------------------


def csv(*rows):
    return "Date,Description,Amount,Reference\n" + "".join(
        f"{day},{what},{amount},{ref}\n" for day, what, amount, ref in rows
    )


def statement(client, pmc, *rows):
    return client.post(
        "/api/reconcile/statements",
        json={
            "bank_account_id": str(pmc.operating_bank_id),
            "format": "csv",
            "content": csv(*rows),
            "first_day": str(TODAY - 10 * DAY),
            "last_day": str(TODAY),
            "opening_balance": "0.00",
        },
    )


def test_a_check_paid_for_bills_matches_its_line_by_number_and_amount(client, conn, staff, pmc):
    fund(conn, pmc, "1000.00")
    first = new_bill(client, pmc, "200.00", "INV-1")
    second = new_bill(client, pmc, "50.00", "INV-2")
    other = new_bill(client, pmc, "75.00", "INV-3")
    # One check for both of the vendor's bills; another check of the wrong amount.
    run = {
        "on": TODAY,
        "method": "check",
        "bill_ids": [first, second, other],
        "check_numbers": {first: "1042", second: "1042", other: "1043"},
    }
    ok(call(client, "bills/pay", **run))

    imported = ok(
        statement(
            client,
            pmc,
            (TODAY - 5 * DAY, "Deposit", "1000.00", ""),
            (TODAY, "CHECK 1042", "-250.00", ""),
            (TODAY, "Check paid", "-70.00", "001043"),
        )
    )

    assert imported["checks_matched"] == 1
    payments = page(client, first)["payments"]
    assert payments[0]["cleared_on"] == str(TODAY)
    assert page(client, other)["payments"][0]["cleared_on"] is None
    assert page(client, first)["history"][-1]["what"] == (
        "The bank paid $200.00 by check 1042 (bank statement)"
    )


def test_a_line_that_looks_like_a_bill_is_recorded_paid_once_staff_confirm(
    client, conn, staff, pmc
):
    fund(conn, pmc, "1000.00")
    bill = new_bill(client, pmc, "180.00", "WTR-1")
    ok(
        statement(
            client,
            pmc,
            (TODAY - 5 * DAY, "Deposit", "1000.00", ""),
            (TODAY - DAY, "CHECK 2001", "-180.00", ""),
        )
    )
    bank = pmc.operating_bank_id

    (suggested,) = ok(client.get(f"/api/reconcile/{bank}/bill-suggestions"), 200)
    accept = {"line_id": suggested["line"]["id"], "bill_id": bill}
    made = ok(client.post(f"/api/reconcile/{bank}/bill-suggestions", json=accept))
    again = ok(client.post(f"/api/reconcile/{bank}/bill-suggestions", json=accept))
    shown = page(client, bill)

    assert (suggested["kind"], suggested["amount"], suggested["check_number"]) == (
        "record",
        "180.00",
        "2001",
    )
    assert again == made  # a retry records nothing new
    assert shown["bill"]["status"] == "paid"
    assert [
        (p["on"], p["method"], p["check_number"], p["cleared_on"]) for p in shown["payments"]
    ] == [(str(TODAY - DAY), "check", "2001", str(TODAY - DAY))]
    assert ok(client.get(f"/api/reconcile/{bank}/bill-suggestions"), 200) == []


def test_a_payment_recorded_already_is_suggested_for_its_line(client, conn, staff, pmc):
    fund(conn, pmc, "1000.00")
    bill = new_bill(client, pmc, "120.00", "ELC-1")
    ok(call(client, f"bills/{bill}/pay", on=TODAY - 2 * DAY, method="EFT"))
    ok(
        statement(
            client,
            pmc,
            (TODAY - 5 * DAY, "Deposit", "1000.00", ""),
            (TODAY - DAY, "ACH DEMO ELECTRIC", "-120.00", ""),
        )
    )
    bank = pmc.operating_bank_id

    (suggested,) = ok(client.get(f"/api/reconcile/{bank}/bill-suggestions"), 200)
    ok(
        client.post(
            f"/api/reconcile/{bank}/bill-suggestions",
            json={
                "line_id": suggested["line"]["id"],
                "bill_id": bill,
                "transfer_id": suggested["transfer_id"],
            },
        )
    )

    assert suggested["kind"] == "match"
    assert page(client, bill)["payments"][0]["cleared_on"] == str(TODAY - DAY)


def test_a_suggestion_is_refused_in_plain_words(client, conn, staff, pmc):
    fund(conn, pmc, "100.00")
    short = new_bill(client, pmc, "300.00", "BIG-1")
    ready = new_bill(client, pmc, "40.00", "SMALL-1", due_in=1)
    ok(
        statement(
            client,
            pmc,
            (TODAY - 5 * DAY, "Deposit", "100.00", ""),
            (TODAY - DAY, "Withdrawal", "-30.00", ""),
        )
    )
    bank = pmc.operating_bank_id
    (line,) = [
        line
        for line in ok(client.get(f"/api/reconcile/{bank}"), 200)["open_lines"]
        if line["amount"] == "-30.00"
    ]

    def accept(bill, transfer=None):
        return client.post(
            f"/api/reconcile/{bank}/bill-suggestions",
            json={"line_id": line["id"], "bill_id": bill, "transfer_id": transfer},
        )

    refused(
        accept(short),
        409,
        "Owner 1's balance is $240.00 short of Vendor 1 BIG-1: they need to send $240.00 first",
    )
    refused(accept(ready), 409, "the line is $30.00; $40.00 is left to pay of Vendor 1 SMALL-1")
    refused(
        accept(ready, "pgtr_nope"), 404, "no such payment of this bill waiting for its bank line"
    )
    refused(
        client.post(
            f"/api/reconcile/{bank}/bill-suggestions",
            json={"line_id": str(uuid.uuid4()), "bill_id": ready},
        ),
        404,
        "no such line of this trust bank account",
    )
    refused(client.post(f"/api/reconcile/{bank}/bill-suggestions", json={"line_id": "x"}), 422)


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("get", "/api/reconcile/{bank}/bill-suggestions"),
        ("post", "/api/reconcile/{bank}/bill-suggestions"),
    ],
)
@pytest.mark.parametrize("caller", ["nobody", "owner"])
def test_only_staff_see_and_confirm_suggestions(client, app_conn, pmc, method, path, caller):
    if caller == "owner":
        join_as_owner(client, app_conn, pmc)
    url = path.format(bank=pmc.operating_bank_id)

    answer = client.get(url) if method == "get" else client.post(url, json={})

    assert answer.status_code == (401 if caller == "nobody" else 403)


# --- what the books refuse, said in words -------------------------------------------------------


def bill_dated(client, pmc, amount, reference, days_ago=20):
    return ok(
        call(
            client,
            "bills",
            vendor_id=pmc.vendor_id,
            account=pmc.owners[0].account,
            reference=reference,
            bill_date=TODAY - days_ago * DAY,
            due_on=TODAY + DAY,
            amount=amount,
            memo="Gutter repair",
        )
    )["id"]


def close_through(conn, pmc, days_ago):
    approve(
        conn,
        pmc.pmc_id,
        pmc.operating_bank_id,
        datetime.combine(TODAY - 30 * DAY, datetime.min.time(), UTC),
        datetime.combine(TODAY - days_ago * DAY, datetime.min.time(), UTC),
    )


def test_pay_bills_names_the_bill_a_closed_period_refuses(client, conn, staff, pmc):
    fund(conn, pmc, "500.00")
    bill = bill_dated(client, pmc, "45.00", "CLS-2")
    close_through(conn, pmc, 6)

    refused(
        call(client, "bills/pay", on=TODAY - 7 * DAY, method="EFT", bill_ids=[bill]),
        409,
        "Vendor 1 CLS-2: that day is in a period already reconciled and closed: date it in the"
        " next open one",
    )
    assert page(client, bill)["payments"] == []


def test_lines_a_closed_period_covers_are_left_as_they_are(client, conn, staff, pmc):
    # A statement in, then its days closed: a check paid with a line's number isn't matched to it
    # by Auto-match, and Record paid on a line in those days is refused in words.
    fund(conn, pmc, "500.00")
    matched_by_number = bill_dated(client, pmc, "50.00", "CLS-3")
    recorded = bill_dated(client, pmc, "45.00", "CLS-4")
    ok(
        statement(
            client,
            pmc,
            (TODAY - 9 * DAY, "Deposit", "500.00", ""),
            (TODAY - 8 * DAY, "CHECK 3001", "-50.00", ""),
            (TODAY - 8 * DAY, "Withdrawal", "-45.00", ""),
        )
    )
    ok(
        call(
            client,
            "bills/pay",
            on=TODAY - 8 * DAY,
            method="check",
            bill_ids=[matched_by_number],
            check_numbers={matched_by_number: "3001"},
        )
    )
    close_through(conn, pmc, 6)
    bank = pmc.operating_bank_id

    ok(client.post(f"/api/reconcile/{bank}/auto-match", json={}), 200)
    assert page(client, matched_by_number)["payments"][0]["cleared_on"] is None
    (line,) = [
        line
        for line in ok(client.get(f"/api/reconcile/{bank}"), 200)["open_lines"]
        if line["amount"] == "-45.00"
    ]
    refused(
        client.post(
            f"/api/reconcile/{bank}/bill-suggestions",
            json={"line_id": line["id"], "bill_id": recorded},
        ),
        409,
        "that day is in a period already reconciled and closed: date it in the next open one",
    )
    assert page(client, recorded)["payments"] == []


def test_record_paid_needs_bills_and_a_bill_of_the_company(
    client, other_client, conn, app_conn, staff, pmc
):
    fund(conn, pmc, "500.00")
    bill = new_bill(client, pmc, "45.00", "ROLE-1")
    ok(
        statement(
            client,
            pmc,
            (TODAY - 5 * DAY, "Deposit", "500.00", ""),
            (TODAY - DAY, "Withdrawal", "-45.00", ""),
        )
    )
    bank = pmc.operating_bank_id
    (line,) = [
        line
        for line in ok(client.get(f"/api/reconcile/{bank}"), 200)["open_lines"]
        if line["amount"] == "-45.00"
    ]
    accept = {"line_id": line["id"], "bill_id": bill}

    refused(
        client.post(
            f"/api/reconcile/{bank}/bill-suggestions", json={**accept, "bill_id": str(uuid.uuid4())}
        ),
        404,
        "no such bill",
    )
    # A clerk whose roles allow month-end but not bills matches lines, and records no bill paid.
    clerk = join_as_staff(other_client, conn, app_conn, pmc, name="Clerk 1", roles=[])
    role = ok(client.post("/api/custom-roles", json={"name": "Month-end", "areas": ["month-end"]}))
    ok(
        client.post(
            f"/api/custom-roles/{role['id']}/staff",
            json={"staff_id": str(clerk.person_id), "granted": True},
        ),
        204,
    )
    refused(
        other_client.post(f"/api/reconcile/{bank}/bill-suggestions", json=accept),
        403,
        "your roles don't let you change bills",
    )
    assert page(client, bill)["payments"] == []


def test_a_bill_set_aside_before_adr_0010_says_when_its_money_was_held(client, conn, staff, pmc):
    # The old step took the money from the owner's balance before any payment.
    fund(conn, pmc, "500.00")
    bill = new_bill(client, pmc, "90.00", "OLD-1")
    conn.execute("SELECT trust_set_aside_bill(%s, %s, %s)", (pmc.pmc_id, bill, NOW - DAY))
    ok(call(client, f"bills/{bill}/pay", on=TODAY, method="EFT"))

    whats = [event["what"] for event in page(client, bill)["history"]]

    assert "$90.00 taken from Owner 1's balance and held for this bill" in whats
    assert page(client, bill)["bill"]["status"] == "paid"


def test_the_ask_for_money_needs_a_bill_of_the_company(conn, pmc):
    with pytest.raises(LookupError, match="no bill"):
        email.write_bill_funds_needed(
            conn, pmc.pmc_id, uuid.uuid4(), "owner@example.com", Decimal("10.00"), "http://x"
        )
