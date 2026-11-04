"""Where a line of an owner's statement or a tenant's ledger came from (ledger_sources.py,
the founder's review of 11/03, item 8): each line of the statement carries its ledger transfer and
each line of the ledger its charge, payment or returned payment, and opening one says what it was
(a tenant's payment and its receipt, a bill and its receipts, how a management fee was worked out,
how a payout left), for the session's own owner or lease only. Every name here is synthetic."""

import json
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from helpers import (
    add_agreement,
    add_document,
    add_tenant,
    add_unit,
    apply_payment,
    charge,
    co_own,
    contribute,
    draw_owner,
    enter_bill,
    open_lease,
    post,
    post_leasing_fee,
    post_management_fee,
    set_aside_bill,
    set_ownership,
    transfer_batch,
)
from people import join_as_owner, join_as_staff, join_as_tenant, tenant_of

from pms.api.ledger_sources import came

TODAY = datetime.now(UTC).date()
DAY = timedelta(days=1)
START = TODAY - timedelta(days=60)
PERIOD = {"start": str(TODAY), "end": str(TODAY)}


def routing(prefix8: str) -> str:
    """Nine digits with a good ABA check digit; a 00 prefix is never issued (rule 2)."""
    weights = (3, 7, 1, 3, 7, 1, 3, 7)
    total = sum(int(d) * w for d, w in zip(prefix8, weights, strict=True))
    return prefix8 + str((10 - total % 10) % 10)


def us(day):
    return f"{day:%m/%d/%Y}"


def ok(answer, status=200):
    assert answer.status_code == status, answer.text
    return answer.json()


@pytest.fixture
def lease(conn, pmc):
    unit = add_unit(conn, pmc.pmc_id, pmc.owners[0].property_id, name="Unit 4")
    made = open_lease(conn, pmc.pmc_id, unit, START, None, "1500.00", [tenant_of(conn, pmc)])
    charge(conn, pmc.pmc_id, made, START, "1500.00", memo="Rent")
    charge(conn, pmc.pmc_id, made, START + 5 * DAY, "50.00", kind="fee", memo="Late fee")
    return made


def pay(client, lease, amount, **more):
    answer = client.post(
        f"/api/rent/leases/{lease}/payments",
        json=json.loads(
            json.dumps(
                {"request_id": uuid.uuid4(), "amount": amount, "received_on": TODAY, **more},
                default=str,
            )
        ),
    )
    assert answer.status_code == 201, answer.text
    return answer.json()["transfers"][0]


@pytest.fixture
def books(client, conn, app_conn, pmc, lease):
    """Owner 1's month: a tenant's payment by check, money they added, a bill set aside with its
    receipt, a management fee and a payout by check."""
    account = pmc.owners[0].account
    add_agreement(
        conn,
        pmc.pmc_id,
        account,
        START,
        fee_percent="10",
        minimum_fee="50",
        flat_fee="25",
        approval_limit="500",
    )
    join_as_staff(client, conn, app_conn, pmc)
    paid = pay(client, lease, "1550.00", method="check", reference="No. 311")
    now = datetime.now(UTC)
    added = contribute(conn, pmc.pmc_id, account, "added-1", "300.00", now)
    bill = enter_bill(
        conn, pmc.pmc_id, pmc.vendor_id, account, "PLB-77", TODAY, TODAY, "200.00", memo="Sink"
    )
    receipt = add_document(conn, pmc.pmc_id, "receipt", bill, title="Plumber's invoice")
    set_aside = set_aside_bill(conn, pmc.pmc_id, bill, now)
    post_management_fee(conn, pmc.pmc_id, account, START, TODAY + DAY, now)
    draw_owner(conn, pmc.pmc_id, account, "draw-1", "100.00", now)
    drawn = conn.execute(
        "SELECT transfer_id FROM trust_owner_draws WHERE pmc_id = %s AND request_key = 'draw-1'",
        (pmc.pmc_id,),
    ).fetchone()[0]
    conn.execute(
        "INSERT INTO trust_payout_methods (pmc_id, transfer_id, method, check_number)"
        " VALUES (%s, %s, 'check', '1042')",
        (pmc.pmc_id, drawn),
    )
    return {
        "paid": paid,
        "added": added,
        "set aside": set_aside,
        "receipt": receipt,
        "drawn": drawn,
    }


def statement_sources(client):
    statement = ok(client.get("/api/owner/statement", params=PERIOD))
    item = statement["columns"].index("item")
    assert len(statement["sources"]) == len(statement["rows"])
    for row, source in zip(statement["rows"], statement["sources"], strict=True):
        assert (source is not None) == (row[item] == "posting")
    return [s for s in statement["sources"] if s]


def by_label(source):
    return {r["label"]: r["value"] for r in source["rows"]}


def test_each_line_of_the_owners_statement_opens_where_it_came_from(
    client, conn, app_conn, pmc, books
):
    join_as_owner(client, app_conn, pmc)

    opened = {
        transfer: ok(client.get(f"/api/owner/statement/sources/{transfer}"))
        for transfer in statement_sources(client)
    }

    assert sorted(s["kind"] for s in opened.values()) == [
        "bill",
        "contribution",
        "management fee",
        "owner draw",
        "payment",
    ]
    payment = opened[books["paid"]]
    assert payment["title"] == "Tenant payment"
    assert by_label(payment)["Amount"] == "$1,550.00"
    assert by_label(payment)["How"] == "Check No. 311"
    assert payment["links"] == [
        {"label": "Receipt (PDF)", "url": f"/api/owner/statement/sources/{books['paid']}/receipt"}
    ]
    receipt = client.get(payment["links"][0]["url"])
    assert receipt.status_code == 200 and receipt.content.startswith(b"%PDF-")

    bill = opened[books["set aside"]]
    assert bill["title"] == "Bill PLB-77"
    assert by_label(bill) == {
        "Vendor": "Vendor 1",
        "Bill #": "PLB-77",
        "For": "Sink",
        "Bill date": us(TODAY),
        "Due": us(TODAY),
        "Amount": "$200.00",
    }
    assert bill["links"] == [
        {"label": "Plumber's invoice", "url": f"/api/owner/documents/{books['receipt']}/file"}
    ]
    assert client.get(bill["links"][0]["url"]).status_code == 200

    fee = next(s for s in opened.values() if s["kind"] == "management fee")
    assert [(r["label"], r["value"]) for r in fee["rows"]] == [
        ("Period", f"{us(START)} to {us(TODAY)}"),
        ("Rent and fees collected", "$1,500.00"),
        ("10% of that", "$150.00"),
        ("Minimum fee", "$50.00"),
        ("Flat fee", "$25.00"),
        ("Fee", "$175.00"),
    ]
    draw = opened[books["drawn"]]
    assert (draw["title"], by_label(draw)) == (
        "Paid to you",
        {"Date": us(TODAY), "Amount": "$100.00", "How": "Check No. 1042"},
    )
    assert by_label(opened[books["added"]]) == {"Date": us(TODAY), "Amount": "$300.00"}


def test_a_returned_payment_and_an_eft_payout_say_so(client, conn, app_conn, pmc, books):
    # Enough in the owner's account that the bounce takes back what the payment brought.
    contribute(conn, pmc.pmc_id, pmc.owners[0].account, "added-2", "1000.00", datetime.now(UTC))
    bounced = client.post(f"/api/rent/payments/{books['paid']}/bounce", json={"on": str(TODAY)})
    assert bounced.status_code == 201, bounced.text
    conn.execute(
        "INSERT INTO trust_payee_bank_accounts (pmc_id, owner_id, routing_number, account_type,"
        " account_sealed, account_last4, holder_name)"
        " VALUES (%s, %s, %s, 'checking', %s, '6789', 'Owner 1')",
        (pmc.pmc_id, pmc.owners[0].owner_id, routing("00123456"), bytes(32)),  # never read
    )
    draw_owner(conn, pmc.pmc_id, pmc.owners[0].account, "draw-2", "10.00", datetime.now(UTC))
    eft = conn.execute(
        "SELECT transfer_id FROM trust_owner_draws WHERE pmc_id = %s AND request_key = 'draw-2'",
        (pmc.pmc_id,),
    ).fetchone()[0]
    conn.execute(
        "INSERT INTO trust_payout_methods (pmc_id, transfer_id, method) VALUES (%s, %s, 'EFT')",
        (pmc.pmc_id, eft),
    )
    join_as_owner(client, app_conn, pmc)

    opened = [
        ok(client.get(f"/api/owner/statement/sources/{t}")) for t in statement_sources(client)
    ]

    returned = [s for s in opened if s["kind"] == "returned payment"]
    assert [by_label(s)["What happened"] for s in returned] == [
        "A tenant's payment came back unpaid."
    ]
    assert by_label(next(s for s in opened if by_label(s).get("How") == "EFT")) == {
        "Date": us(TODAY),
        "Amount": "$10.00",
        "How": "EFT",
        "To": "Bank account ending 6789",
    }


def test_fees_a_markup_prepaid_rent_used_and_anything_else_say_what_they_were(
    client, conn, app_conn, pmc, lease
):
    account = pmc.owners[0].account
    add_agreement(
        conn,
        pmc.pmc_id,
        account,
        START,
        leasing_fee_percent="50",
        approval_limit="500",
        bill_markup_percent="10",
    )
    join_as_staff(client, conn, app_conn, pmc)
    pay(client, lease, "1600.00")  # 50.00 more than the lease owes: held as prepaid rent
    charge(conn, pmc.pmc_id, lease, TODAY, "40.00", kind="fee", memo="Parking")
    used = client.post(
        f"/api/rent/leases/{lease}/use-prepaid",
        json={"request_id": str(uuid.uuid4()), "on": str(TODAY)},
    )
    assert used.status_code == 201, used.text
    now = datetime.now(UTC)
    post_leasing_fee(conn, pmc.pmc_id, lease, account, now)
    taken = client.post(
        "/api/payables/owner-fees",
        json={
            "request_id": str(uuid.uuid4()),
            "account": account,
            "kind": "inspection",
            "amount": "75.00",
            "memo": "Move-in inspection",
            "on": str(TODAY),
        },
    )
    assert taken.status_code == 201, taken.text
    bill = enter_bill(
        conn, pmc.pmc_id, pmc.vendor_id, account, "GT-9", TODAY, TODAY, "100.00", memo="Gutters"
    )
    conn.execute(
        "INSERT INTO trust_bill_markups (bill_id, pmc_id) VALUES (%s, %s)", (bill, pmc.pmc_id)
    )
    set_aside_bill(conn, pmc.pmc_id, bill, now)
    post(
        conn,
        pmc.pmc_id,
        "brought-1",
        [(pmc.operating_cash, account, "12.00")],
        now,
        {"memo": "Brought over"},
    )
    join_as_owner(client, app_conn, pmc)

    opened = {
        s["title"]: s
        for s in (
            ok(client.get(f"/api/owner/statement/sources/{t}")) for t in statement_sources(client)
        )
    }

    assert by_label(opened["Prepaid rent used"]) == {
        "Date": us(TODAY),
        "Amount": "$40.00",
        "Paid toward": f"Parking, due {us(TODAY)}: $40.00",
    }
    # Unit 4 is Property 1's only unit, so the lease's home reads as the property.
    assert by_label(opened["Leasing fee"]) == {
        "Date": us(TODAY),
        "For the lease at": "Property 1",
        "Fee": "$750.00",
    }
    assert by_label(opened["Inspection fee"]) == {
        "Date": us(TODAY),
        "For": "Move-in inspection",
        "Amount": "$75.00",
    }
    assert by_label(opened["Markup on bill GT-9"])["Markup"] == "$10.00"
    assert by_label(opened["Bill GT-9"])["Markup"] == "$10.00"
    assert opened["Brought over"]["kind"] == "other"
    assert by_label(opened["Brought over"]) == {"Date": us(TODAY), "Amount": "$12.00"}


def test_a_fee_split_by_ownership_says_it_is_the_owners_share(client, conn, app_conn, pmc, lease):
    first, second = pmc.owners[0].account, co_own(conn, pmc)
    set_ownership(conn, pmc.pmc_id, pmc.owners[0].property_id, START, [(first, 60), (second, 40)])
    for account in (first, second):
        add_agreement(conn, pmc.pmc_id, account, START, "8", "50.00", "10.01")
    join_as_staff(client, conn, app_conn, pmc)
    pay(client, lease, "1550.00")
    post_management_fee(conn, pmc.pmc_id, first, START, TODAY + DAY, datetime.now(UTC))
    join_as_owner(client, app_conn, pmc)

    [fee] = [
        source
        for source in (
            ok(client.get(f"/api/owner/statement/sources/{t}")) for t in statement_sources(client)
        )
        if source["kind"] == "management fee"
    ]

    shown = by_label(fee)
    assert shown["Split by ownership"] == "Your share of the property's fee"
    # The property's fee is 8% of the 1,500.00 rent collected plus 10.01: 130.01; this owner's
    # 60% of it is 78.006, to the cent.
    assert shown["Fee"] == "$78.01"


def test_another_owners_line_reads_as_not_found(client, other_client, conn, app_conn, pmc, books):
    join_as_owner(other_client, app_conn, pmc, n=1)

    answers = [
        other_client.get(f"/api/owner/statement/sources/{books['paid']}"),
        other_client.get(f"/api/owner/statement/sources/{books['paid']}/receipt"),
        other_client.get(f"/api/owner/statement/sources/{books['drawn']}"),
    ]

    assert [a.status_code for a in answers] == [404] * 3
    assert {a.json()["detail"] for a in answers} == {"no such line of your statement"}
    assert all(
        s is None for s in ok(other_client.get("/api/owner/statement", params=PERIOD))["sources"]
    )


def test_a_line_that_is_no_tenant_payment_has_no_receipt(client, app_conn, pmc, books):
    join_as_owner(client, app_conn, pmc)

    answer = client.get(f"/api/owner/statement/sources/{books['drawn']}/receipt")

    assert answer.status_code == 404


@pytest.mark.parametrize(
    ("transfer", "status"), [("x" * 65, 422), ("pglt_nothing", 404), ("", 404)]
)
def test_a_line_the_statement_never_had_is_refused(client, app_conn, pmc, transfer, status):
    join_as_owner(client, app_conn, pmc)

    answer = client.get(f"/api/owner/statement/sources/{transfer}")

    assert answer.status_code == status


@pytest.mark.parametrize("caller", ["nobody", "staff", "tenant"])
def test_only_the_owner_opens_their_statements_lines(client, conn, app_conn, pmc, books, caller):
    client.cookies.clear()
    if caller == "staff":
        join_as_staff(client, conn, app_conn, pmc)
    elif caller == "tenant":
        join_as_tenant(client, conn, app_conn, pmc)

    answers = [
        client.get(f"/api/owner/statement/sources/{books['paid']}"),
        client.get(f"/api/owner/statement/sources/{books['paid']}/receipt"),
    ]

    assert [a.status_code for a in answers] == [401 if caller == "nobody" else 403] * 2


# --- the tenant's ledger -----------------------------------------------------------------------


def ledger_sources(client, lease):
    ledger = ok(client.get(f"/api/tenant/leases/{lease}/ledger"))
    item = ledger["columns"].index("item")
    assert len(ledger["sources"]) == len(ledger["rows"])
    for row, source in zip(ledger["rows"], ledger["sources"], strict=True):
        assert (source is None) == (row[item] in ("lease", "balance"))
    return [s for s in ledger["sources"] if s]


def test_each_line_of_the_tenants_ledger_opens_where_it_came_from(
    client, conn, app_conn, pmc, lease
):
    join_as_staff(client, conn, app_conn, pmc)
    paid = pay(client, lease, "1000.00", method="money order")
    bounced = client.post(f"/api/rent/payments/{paid}/bounce", json={"on": str(TODAY)})
    assert bounced.status_code == 201, bounced.text
    join_as_tenant(client, conn, app_conn, pmc)

    sources = ledger_sources(client, lease)
    opened = [
        ok(client.get(f"/api/tenant/leases/{lease}/ledger/sources/{s['kind']}/{s['id']}"))
        for s in sources
    ]

    assert [s["kind"] for s in sources] == ["charge", "charge", "payment", "returned"]
    rent, late, payment, returned = opened
    assert (rent["title"], by_label(rent)) == (
        "Rent",
        {"What": "Rent", "Due": us(START), "Amount": "$1,500.00", "Still owed": "$1,500.00"},
    )
    assert by_label(late)["What"] == "Late fee"
    assert by_label(payment)["How"] == "Money order"
    assert payment["links"] == [
        {"label": "Receipt (PDF)", "url": f"/api/tenant/payments/{paid}/receipt"}
    ]
    assert client.get(payment["links"][0]["url"]).status_code == 200
    assert by_label(returned) == {
        "Date": us(TODAY),
        "Amount": "$1,000.00",
        "What happened": "The payment came back unpaid.",
    }


def test_prepaid_rent_used_says_what_it_paid(client, conn, app_conn, pmc, lease):
    join_as_staff(client, conn, app_conn, pmc)
    pay(client, lease, "1600.00")  # 50.00 more than the lease owes: held as prepaid rent
    charge(conn, pmc.pmc_id, lease, TODAY, "40.00", kind="fee", memo="Parking")
    used = client.post(
        f"/api/rent/leases/{lease}/use-prepaid",
        json={"request_id": str(uuid.uuid4()), "on": str(TODAY)},
    )
    assert used.status_code == 201, used.text
    join_as_tenant(client, conn, app_conn, pmc)

    opened = [
        ok(client.get(f"/api/tenant/leases/{lease}/ledger/sources/payment/{s['id']}"))
        for s in ledger_sources(client, lease)
        if s["kind"] == "payment"
    ]

    assert [s["title"] for s in opened] == ["Payment", "Prepaid rent used"]
    assert by_label(opened[1]) == {
        "Date": us(TODAY),
        "Amount": "$40.00",
        "Paid toward": f"Parking, due {us(TODAY)}: $40.00",
    }


def test_a_line_of_another_lease_reads_as_not_found(client, conn, app_conn, pmc, lease):
    join_as_staff(client, conn, app_conn, pmc)
    paid = pay(client, lease, "100.00")
    unit = add_unit(conn, pmc.pmc_id, pmc.owners[0].property_id, name="Unit 6")
    others = open_lease(conn, pmc.pmc_id, unit, START, None, "900.00", [tenant_of(conn, pmc)])
    rent = conn.execute(
        "SELECT id FROM trust_charges WHERE lease_id = %s AND kind = 'rent'", (lease,)
    ).fetchone()[0]
    join_as_tenant(client, conn, app_conn, pmc)

    answers = [
        client.get(f"/api/tenant/leases/{others}/ledger/sources/charge/{rent}"),
        client.get(f"/api/tenant/leases/{others}/ledger/sources/payment/{paid}"),
        client.get(f"/api/tenant/leases/{others}/ledger/sources/returned/{paid}"),
        client.get(f"/api/tenant/leases/{lease}/ledger/sources/charge/not-a-charge"),
    ]

    assert [a.status_code for a in answers] == [404] * 4
    assert {a.json()["detail"] for a in answers} == {"no such line of your ledger"}


def test_a_lease_theyre_not_on_reads_as_not_found(client, other_client, conn, app_conn, pmc, lease):
    elsewhere = add_unit(conn, pmc.pmc_id, pmc.owners[1].property_id, name="Unit 9")
    someone = add_tenant(conn, pmc.pmc_id, pmc.owners[1].property_id, "Tenant 9")
    theirs = open_lease(conn, pmc.pmc_id, elsewhere, START, None, "800.00", [someone])
    rent = charge(conn, pmc.pmc_id, theirs, START, "800.00", memo="Rent")
    join_as_tenant(client, conn, app_conn, pmc)

    answer = client.get(f"/api/tenant/leases/{theirs}/ledger/sources/charge/{rent}")

    assert answer.status_code == 404


@pytest.mark.parametrize(
    ("kind", "source", "status"),
    [("bounce", "x", 422), ("charge", "x" * 65, 422), ("payment", "pglt_nothing", 404)],
)
def test_a_line_the_ledger_never_had_is_refused(
    client, conn, app_conn, pmc, lease, kind, source, status
):
    join_as_tenant(client, conn, app_conn, pmc)

    answer = client.get(f"/api/tenant/leases/{lease}/ledger/sources/{kind}/{source}")

    assert answer.status_code == status


@pytest.mark.parametrize("caller", ["nobody", "staff", "owner"])
def test_only_the_tenant_opens_their_ledgers_lines(client, conn, app_conn, pmc, lease, caller):
    rent = conn.execute(
        "SELECT id FROM trust_charges WHERE lease_id = %s AND kind = 'rent'", (lease,)
    ).fetchone()[0]
    if caller == "staff":
        join_as_staff(client, conn, app_conn, pmc)
    elif caller == "owner":
        join_as_owner(client, app_conn, pmc)

    answer = client.get(f"/api/tenant/leases/{lease}/ledger/sources/charge/{rent}")

    assert answer.status_code == (401 if caller == "nobody" else 403)


def test_each_payment_reads_as_what_paid_and_how_with_its_receipt(
    client, conn, app_conn, pmc, lease
):
    join_as_staff(client, conn, app_conn, pmc)
    checked = pay(client, lease, "1600.00", method="check", reference="No. 2101")
    charge(conn, pmc.pmc_id, lease, TODAY, "40.00", kind="fee", memo="Parking")
    used = client.post(
        f"/api/rent/leases/{lease}/use-prepaid",
        json={"request_id": str(uuid.uuid4()), "on": str(TODAY)},
    )
    assert used.status_code == 201, used.text
    join_as_tenant(client, conn, app_conn, pmc)

    ledger = ok(client.get(f"/api/tenant/leases/{lease}/ledger"))

    item = ledger["columns"].index("item")
    assert len(ledger["payments"]) == len(ledger["rows"])
    words = [
        (row[item], said)
        for row, said in zip(ledger["rows"], ledger["payments"], strict=True)
        if row[item] == "payment" or said is not None
    ]
    assert words == [
        (
            "payment",
            {
                "what": "Rent payment",
                "how": "Check No. 2101",
                "receipt": f"/api/tenant/payments/{checked}/receipt",
            },
        ),
        ("payment", {"what": "Prepaid rent used", "how": None, "receipt": None}),
    ]


def test_a_payment_nobody_said_how_came_reads_as_a_rent_payment_alone(
    client, conn, app_conn, pmc, lease
):
    join_as_staff(client, conn, app_conn, pmc)
    pay(client, lease, "100.00")
    join_as_tenant(client, conn, app_conn, pmc)

    ledger = ok(client.get(f"/api/tenant/leases/{lease}/ledger"))

    said = [p for p in ledger["payments"] if p is not None]
    assert [(p["what"], p["how"]) for p in said] == [("Rent payment", None)]


@pytest.mark.parametrize(
    ("method", "reference", "said"),
    [
        ("check", "2101", "Check 2101"),
        ("check", "Check 2101", "Check 2101"),  # the method once, when the reference says it
        ("online", None, "ACH"),
        (None, "No. 8", "No. 8"),
        (None, None, None),
    ],
)
def test_how_a_payment_came_reads_as_a_person_says_it(method, reference, said):
    assert came(method, reference) == said


def test_a_payment_with_no_kind_keeps_its_memo(client, conn, app_conn, pmc, lease):
    rent = conn.execute(
        "SELECT id FROM trust_charges WHERE lease_id = %s AND kind = 'rent'", (lease,)
    ).fetchone()[0]
    [moved] = transfer_batch(conn, [(pmc.operating_cash, pmc.owners[0].account, "500.00")])
    apply_payment(conn, pmc.pmc_id, rent, moved, "500.00")
    join_as_tenant(client, conn, app_conn, pmc)

    ledger = ok(client.get(f"/api/tenant/leases/{lease}/ledger"))

    assert [p for p in ledger["payments"] if p] == [
        {"what": "Payment", "how": None, "receipt": None}
    ]


def test_what_a_payment_held_as_prepaid_rent_has_a_line_of_its_own(
    client, conn, app_conn, pmc, lease
):
    join_as_staff(client, conn, app_conn, pmc)
    first = pay(client, lease, "1600.00", method="check", reference="2101")  # 50.00 more than owed
    second = pay(client, lease, "75.00", method="cash")  # nothing owed: all held
    bounced = client.post(f"/api/rent/payments/{second}/bounce", json={"on": str(TODAY)})
    assert bounced.status_code == 201, bounced.text
    join_as_tenant(client, conn, app_conn, pmc)

    ledger = ok(client.get(f"/api/tenant/leases/{lease}/ledger"))

    held = sorted(ledger["held"], key=lambda h: h["amount"])
    assert [(h["on"], h["what"], h["how"], h["amount"], h["returned"]) for h in held] == [
        (str(TODAY), "Rent payment", "Check 2101", "50.00", False),
        (str(TODAY), "Rent payment", "Cash", "75.00", True),
    ]
    for line in held:
        receipt = client.get(line["receipt"])
        assert receipt.status_code == 200 and receipt.content.startswith(b"%PDF-")
    # The download is the report as it was: nothing held reads as paid.
    balance = ledger["rows"][-1]
    assert balance[ledger["columns"].index("paid")] == "1550.00"
    assert first


def test_another_leases_prepaid_rent_is_not_on_this_ledger(client, conn, app_conn, pmc, lease):
    join_as_staff(client, conn, app_conn, pmc)
    unit = add_unit(conn, pmc.pmc_id, pmc.owners[0].property_id, name="Unit 7")
    other = open_lease(conn, pmc.pmc_id, unit, START, None, "900.00", [tenant_of(conn, pmc)])
    pay(client, other, "40.00")
    join_as_tenant(client, conn, app_conn, pmc)

    ledger = ok(client.get(f"/api/tenant/leases/{lease}/ledger"))

    assert ledger["held"] == []
