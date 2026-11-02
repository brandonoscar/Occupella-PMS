"""Paying owners and vendors through the API (ADR 0010 phase 1b; src/pms/api/payouts.py and Pay
owners and Pay bills in src/pms/api/payables.py): the draw day and the draws prepared on it, how
each owner is paid, payees' bank accounts sealed with the server's key, what a bank asks in an ACH
file, and the files of payouts recorded together: an ACH file and checks to print, each taken
only by staff who may pay out money and logged."""

import base64
import io
import json
import os
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from helpers import add_agreement, post
from people import join_as_owner, join_as_staff
from pypdf import PdfReader

NOW = datetime.now(UTC)
TODAY = NOW.date()
DAY = timedelta(days=1)
ACCOUNT = "123456789"


def routing(prefix8: str) -> str:
    """A synthetic routing number: the prefix 00 (never a bank's) with a check digit that holds."""
    d = [int(c) for c in prefix8]
    for c in range(10):
        n = [*d, c]
        if (3 * (n[0] + n[3] + n[6]) + 7 * (n[1] + n[4] + n[7]) + n[2] + n[5] + n[8]) % 10 == 0:
            return prefix8 + str(c)
    raise AssertionError


BANK = routing("00123456")
PAYEE_BANK = routing("00987654")


@pytest.fixture(autouse=True)
def key(monkeypatch):
    monkeypatch.setenv("PMS_DATA_KEY", base64.b64encode(os.urandom(32)).decode())


@pytest.fixture
def staff(client, conn, app_conn, pmc):
    return join_as_staff(client, conn, app_conn, pmc)


def send(client, path, **body):
    return client.post(f"/api/{path}", json=json.loads(json.dumps(body, default=str)))


def ok(answer, status=200):
    assert answer.status_code == status, answer.text
    return (
        answer.json()
        if answer.headers.get("content-type", "").startswith("application/json")
        else answer
    )


def refused(answer, status, detail=None):
    assert answer.status_code == status, answer.text
    if detail is not None:
        assert answer.json()["detail"] == detail


def fund(conn, pmc, amount, n=0):
    add_agreement(conn, pmc.pmc_id, pmc.owners[n].account, TODAY - 60 * DAY, approval_limit="5000")
    post(
        conn,
        pmc.pmc_id,
        f"fund-{uuid.uuid4()}",
        [(pmc.operating_cash, pmc.owners[n].account, Decimal(amount))],
        NOW - 5 * DAY,
    )


def ach_details(client, pmc, first_check=None):
    return ok(
        send(
            client,
            f"payouts/bank-accounts/{pmc.operating_bank_id}",
            bank_routing_number=BANK,
            bank_name="Synthetic Bank",
            origin_id="1000000000",
            origin_name="Golden PMC",
            company_id="1000000000",
            company_name="Golden PMC",
            first_check_number=first_check,
        )
    )


def owner_bank(client, pmc, n=0, number=ACCOUNT):
    return ok(
        send(
            client,
            f"payouts/owners/{pmc.owners[n].owner_id}/bank-account",
            routing_number=PAYEE_BANK,
            account_number=number,
            account_type="checking",
            holder_name=f"Owner {n + 1}",
        ),
        201,
    )


def pay_owners(client, pmc, method, output="file", n=0, amount="100.00", **more):
    return send(
        client,
        "payables/draws/batch",
        request_id=more.pop("request_id", uuid.uuid4()),
        on=TODAY,
        method=method,
        payouts=[{"account": pmc.owners[n].account, "amount": amount}],
        output=output,
        **more,
    )


# --- the draw day -----------------------------------------------------------------------------


def test_the_draw_day_is_set_and_saving_it_again_writes_nothing(client, conn, staff, pmc):
    assert ok(client.get("/api/payouts/draw-day"))["draw_day"] is None
    set_ = ok(send(client, "payouts/draw-day", draw_day=15))
    assert (set_["draw_day"], set_["changed_by"]) == (15, "Staff 1")
    ok(send(client, "payouts/draw-day", draw_day=15))
    assert ok(send(client, "payouts/draw-day", draw_day=None))["draw_day"] is None
    rows = conn.execute(
        "SELECT count(*) FROM trust_owner_draw_settings WHERE pmc_id = %s", (pmc.pmc_id,)
    ).fetchone()[0]
    assert rows == 2
    refused(send(client, "payouts/draw-day", draw_day=29), 422)


def test_the_draws_prepared_on_the_draw_day_wait_on_the_dashboard_until_paid_once(
    client, conn, staff, pmc
):
    fund(conn, pmc, "700.00")
    ok(send(client, "payouts/draw-day", draw_day=1))
    conn.execute("SELECT trust_prepare_owner_draws(%s, %s)", (pmc.pmc_id, TODAY))

    waiting = ok(client.get("/api/payouts/draw-runs/waiting"))
    assert [(line["owner"], line["amount"], line["method"]) for line in waiting["lines"]] == [
        ("Owner 1", "700.00", "check")
    ]
    assert waiting["lines"][0]["available_now"] == "700.00"
    assert ok(client.get("/api/today"))["owner_draws"]["id"] == waiting["id"]

    request = uuid.uuid4()
    ok(
        pay_owners(
            client,
            pmc,
            "check",
            "record",
            amount="700.00",
            request_id=request,
            draw_run_id=waiting["id"],
        ),
        201,
    )
    assert ok(client.get("/api/payouts/draw-runs/waiting")) is None
    assert ok(client.get("/api/today"))["owner_draws"] is None
    # Sent again, the same request pays nothing twice; another request can't pay them again.
    ok(
        pay_owners(
            client,
            pmc,
            "check",
            "record",
            amount="700.00",
            request_id=request,
            draw_run_id=waiting["id"],
        ),
        201,
    )
    refused(
        pay_owners(client, pmc, "check", "record", draw_run_id=waiting["id"]),
        409,
        "these prepared draws were paid already",
    )


# --- payees -----------------------------------------------------------------------------------


def test_an_owners_bank_account_is_sealed_and_only_its_last_four_come_back(
    client, conn, staff, pmc
):
    owner = pmc.owners[0].owner_id
    assert ok(client.get(f"/api/payouts/owners/{owner}")) == {"method": "check", "bank": None}
    ok(send(client, f"payouts/owners/{owner}/method", method="EFT"))

    shown = owner_bank(client, pmc)

    assert shown["method"] == "EFT"
    assert {k: shown["bank"][k] for k in ("routing_number", "last4", "holder_name")} == {
        "routing_number": PAYEE_BANK,
        "last4": "6789",
        "holder_name": "Owner 1",
    }
    assert ACCOUNT not in json.dumps(shown)
    stored = conn.execute(
        "SELECT account_sealed, account_last4 FROM trust_payee_bank_accounts WHERE owner_id = %s",
        (owner,),
    ).fetchone()
    assert ACCOUNT.encode() not in bytes(stored[0]) and stored[1] == "6789"


def test_pay_owners_lists_how_each_owner_is_paid_and_whether_an_ach_file_can_pay_them(
    client, staff, pmc
):
    def listed():
        return {
            row["owner_id"]: (row["payout_method"], row["bank_on_file"])
            for row in ok(client.get("/api/payables/owners"))
        }

    first, second = (str(o.owner_id) for o in pmc.owners)
    assert listed() == {first: ("check", False), second: ("check", False)}
    ok(send(client, f"payouts/owners/{first}/method", method="EFT"))
    assert listed()[first] == ("EFT", False)
    owner_bank(client, pmc)
    assert listed() == {first: ("EFT", True), second: ("check", False)}


def test_a_vendors_bank_account_is_kept_as_an_owners(client, conn, staff, pmc):
    shown = ok(
        send(
            client,
            f"payouts/vendors/{pmc.vendor_id}/bank-account",
            routing_number=PAYEE_BANK,
            account_number="5550001",
            account_type="savings",
            holder_name="Vendor 1 LLC",
        ),
        201,
    )
    assert (shown["bank"]["last4"], shown["bank"]["account_type"]) == ("0001", "savings")
    assert ok(client.get(f"/api/payouts/vendors/{pmc.vendor_id}"))["bank"]["last4"] == "0001"


@pytest.mark.parametrize(
    ("body", "status", "detail"),
    [
        (
            {"routing_number": BANK[:8] + str((int(BANK[8]) + 1) % 10)},
            422,
            "that routing number's last digit doesn't check: look at it again",
        ),
        ({"routing_number": "12345"}, 422, None),
        ({"account_number": "12-34"}, 422, None),
        ({"account_type": "brokerage"}, 422, None),
        ({"holder_name": " "}, 422, None),
    ],
)
def test_a_bank_account_that_cant_be_paid_is_refused(client, staff, pmc, body, status, detail):
    full = {
        "routing_number": PAYEE_BANK,
        "account_number": ACCOUNT,
        "account_type": "checking",
        "holder_name": "Owner 1",
    } | body
    refused(
        send(client, f"payouts/owners/{pmc.owners[0].owner_id}/bank-account", **full),
        status,
        detail,
    )


def test_a_server_without_a_key_keeps_no_account_numbers(client, staff, pmc, monkeypatch):
    monkeypatch.delenv("PMS_DATA_KEY")
    refused(
        send(
            client,
            f"payouts/owners/{pmc.owners[0].owner_id}/bank-account",
            routing_number=PAYEE_BANK,
            account_number=ACCOUNT,
            account_type="checking",
            holder_name="Owner 1",
        ),
        409,
        "this server keeps no bank account numbers: it has no PMS_DATA_KEY; ask whoever runs it"
        " to set one",
    )


def test_an_unknown_payee_or_bank_account_is_not_found(client, staff):
    refused(client.get(f"/api/payouts/owners/{uuid.uuid4()}"), 404)
    refused(client.get(f"/api/payouts/vendors/{uuid.uuid4()}"), 404)
    refused(client.get(f"/api/payouts/bank-accounts/{uuid.uuid4()}"), 404)


# --- what the bank asks --------------------------------------------------------------------


def test_a_banks_ach_details_come_whole_and_the_next_check_follows_the_first(client, staff, pmc):
    bank = pmc.operating_bank_id
    assert ok(client.get(f"/api/payouts/bank-accounts/{bank}"))["ach_ready"] is False
    refused(send(client, f"payouts/bank-accounts/{bank}", bank_routing_number=BANK), 422)

    settings = ach_details(client, pmc, first_check=1001)

    assert (settings["ach_ready"], settings["next_check_number"]) == (True, 1001)
    assert settings["changed_by"] == "Staff 1"


# --- payout files ---------------------------------------------------------------------------


def test_an_ach_run_needs_the_banks_details_and_every_payees_account(client, conn, staff, pmc):
    fund(conn, pmc, "500.00")
    first = pay_owners(client, pmc, "EFT")
    refused(first, 409)
    assert first.json()["detail"].endswith(
        "has no ACH details from its bank yet: add them on its Payouts tab first"
    )
    ach_details(client, pmc)
    answer = pay_owners(client, pmc, "EFT")
    refused(answer, 409, "no bank account on file for Owner 1: add it or pay by check")
    # Nothing was paid.
    assert (
        conn.execute(
            "SELECT count(*) FROM trust_owner_draws WHERE pmc_id = %s", (pmc.pmc_id,)
        ).fetchone()[0]
        == 0
    )


def test_owners_paid_by_ach_get_one_file_their_bank_takes(client, conn, staff, pmc):
    fund(conn, pmc, "500.00")
    ach_details(client, pmc)
    owner_bank(client, pmc)

    paid = ok(pay_owners(client, pmc, "EFT", amount="125.50"), 201)

    run = paid[0]["run_id"]
    listed = ok(client.get("/api/payouts/runs?kind=owners"))
    assert [(r["id"], r["method"], r["payouts"], r["total"]) for r in listed] == [
        (run, "EFT", 1, "125.50")
    ]
    # A trust bank account's Payouts tab lists its own runs only.
    of = "/api/payouts/runs?bank_account_id="
    assert [r["id"] for r in ok(client.get(f"{of}{pmc.operating_bank_id}"))] == [run]
    assert ok(client.get(f"{of}{pmc.deposit_bank_id}")) == []
    refused(client.get(f"{of}not-an-id"), 422)
    answer = client.get(f"/api/payouts/runs/{run}/ach")
    assert answer.status_code == 200, answer.text
    lines = answer.content.decode("ascii").splitlines()
    assert len(lines) % 10 == 0 and all(len(line) == 94 for line in lines)
    entry = next(line for line in lines if line.startswith("6"))
    assert entry[1:3] == "22" and entry[3:12] == PAYEE_BANK
    assert entry[12:29].strip() == ACCOUNT and entry[29:39] == "0000012550"
    assert lines[1][50:53] == "PPD"
    assert conn.execute(
        "SELECT file, recorded_by IS NOT NULL FROM trust_payout_file_downloads WHERE run_id = %s",
        (run,),
    ).fetchall() == [("ach", True)]
    # An ACH run has no checks to print.
    refused(
        client.get(f"/api/payouts/runs/{run}/checks"),
        409,
        "this run was paid by ACH: take its ACH file",
    )


def test_an_ach_file_taken_again_is_the_one_made_when_the_run_was_paid(client, conn, staff, pmc):
    fund(conn, pmc, "500.00")
    ach_details(client, pmc)
    owner_bank(client, pmc)
    run = ok(pay_owners(client, pmc, "EFT", amount="125.50"), 201)[0]["run_id"]
    first = client.get(f"/api/payouts/runs/{run}/ach").content
    # The bank's details and the owner's account change after it was paid.
    ok(
        send(
            client,
            f"payouts/bank-accounts/{pmc.operating_bank_id}",
            bank_routing_number=routing("00999999"),
            bank_name="Another Bank",
            origin_id="2000000000",
            origin_name="Other PMC",
            company_id="2000000000",
            company_name="Other PMC",
        )
    )
    owner_bank(client, pmc, number="99999999")
    again = client.get(f"/api/payouts/runs/{run}/ach").content
    # Byte for byte: the bank details, the account and the time are the run's.
    assert again == first
    assert first.decode("ascii").splitlines()[0][63:86].strip() == "GOLDEN PMC"


def test_owners_paid_by_check_get_their_checks_numbered_on(client, conn, staff, pmc):
    fund(conn, pmc, "500.00", n=0)
    fund(conn, pmc, "300.00", n=1)
    ach_details(client, pmc, first_check=2001)
    answer = send(
        client,
        "payables/draws/batch",
        request_id=uuid.uuid4(),
        on=TODAY,
        method="check",
        output="file",
        payouts=[
            {"account": pmc.owners[0].account, "amount": "200.00"},
            {"account": pmc.owners[1].account, "amount": "50.00"},
        ],
    )
    paid = ok(answer, 201)

    numbers = conn.execute(
        "SELECT check_number FROM trust_payout_methods WHERE transfer_id = ANY(%s)"
        " ORDER BY check_number",
        ([p["transfer_id"] for p in paid],),
    ).fetchall()
    assert numbers == [("2001",), ("2002",)]
    pdf = client.get(f"/api/payouts/runs/{paid[0]['run_id']}/checks")
    assert pdf.status_code == 200 and pdf.content.startswith(b"%PDF")
    assert (
        ok(client.get(f"/api/payouts/bank-accounts/{pmc.operating_bank_id}"))["next_check_number"]
        == 2003
    )


def test_vendors_paid_by_ach_from_pay_bills_get_a_company_batch(client, conn, staff, pmc):
    fund(conn, pmc, "500.00")
    ach_details(client, pmc)
    ok(
        send(
            client,
            f"payouts/vendors/{pmc.vendor_id}/bank-account",
            routing_number=PAYEE_BANK,
            account_number="5550001",
            account_type="checking",
            holder_name="Vendor 1 LLC",
        ),
        201,
    )
    bill = ok(
        send(
            client,
            "payables/bills",
            vendor_id=pmc.vendor_id,
            account=pmc.owners[0].account,
            reference="ACH-1",
            bill_date=TODAY - DAY,
            due_on=TODAY + 10 * DAY,
            amount="80.00",
            memo="Filter",
        ),
        201,
    )["id"]

    paid = ok(
        send(
            client,
            "payables/bills/pay",
            on=TODAY,
            method="EFT",
            bill_ids=[bill],
            output="file",
            request_id=uuid.uuid4(),
        ),
        201,
    )

    lines = client.get(f"/api/payouts/runs/{paid[0]['run_id']}/ach").content.decode().splitlines()
    assert lines[1][50:63] == "CCDVENDOR PAY"
    entry = next(line for line in lines if line.startswith("6"))
    assert entry[39:54].strip() == "ACH-1" and entry[54:76].strip() == "VENDOR 1 LLC"
    refused(
        send(client, "payables/bills/pay", on=TODAY, method="EFT", bill_ids=[bill], output="file"),
        422,
        "a payout file takes the page's request id",
    )


def test_only_staff_who_may_pay_out_take_a_payout_file(
    client, other_client, conn, app_conn, staff, pmc
):
    fund(conn, pmc, "500.00")
    ach_details(client, pmc)
    owner_bank(client, pmc)
    run = ok(pay_owners(client, pmc, "EFT"), 201)[0]["run_id"]
    join_as_staff(other_client, conn, app_conn, pmc, name="Clerk 1", roles=[])

    refused(
        other_client.get(f"/api/payouts/runs/{run}/ach"),
        403,
        "only staff who may pay out money (the disburse role) take a payout file",
    )
    assert (
        conn.execute(
            "SELECT count(*) FROM trust_payout_file_downloads WHERE run_id = %s", (run,)
        ).fetchone()[0]
        == 0
    )
    refused(other_client.get(f"/api/payouts/runs/{uuid.uuid4()}/ach"), 404)


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", "/api/payouts/draw-day"),
        ("POST", "/api/payouts/draw-day"),
        ("GET", "/api/payouts/draw-runs/waiting"),
        ("GET", "/api/payouts/runs"),
        ("GET", f"/api/payouts/runs/{uuid.uuid4()}/ach"),
        ("GET", f"/api/payouts/runs/{uuid.uuid4()}/checks"),
        ("GET", f"/api/payouts/owners/{uuid.uuid4()}"),
        ("POST", f"/api/payouts/owners/{uuid.uuid4()}/method"),
        ("POST", f"/api/payouts/owners/{uuid.uuid4()}/bank-account"),
        ("GET", f"/api/payouts/vendors/{uuid.uuid4()}"),
        ("POST", f"/api/payouts/vendors/{uuid.uuid4()}/bank-account"),
        ("GET", f"/api/payouts/bank-accounts/{uuid.uuid4()}"),
        ("POST", f"/api/payouts/bank-accounts/{uuid.uuid4()}"),
    ],
)
def test_only_staff_reach_payouts(client, app_conn, pmc, method, path):
    assert client.request(method, path, json={}).status_code == 401
    join_as_owner(client, app_conn, pmc)
    assert client.request(method, path, json={}).status_code == 403


# --- what a file needs at the moment it is made ----------------------------------------------


def test_a_server_without_its_key_records_no_ach_payout(client, conn, staff, pmc, monkeypatch):
    fund(conn, pmc, "500.00")
    ach_details(client, pmc)
    owner_bank(client, pmc)
    monkeypatch.delenv("PMS_DATA_KEY")

    answer = pay_owners(client, pmc, "EFT")

    refused(answer, 409)
    assert answer.json()["detail"].endswith(", so it makes no ACH file")
    assert (
        conn.execute(
            "SELECT count(*) FROM trust_owner_draws WHERE pmc_id = %s", (pmc.pmc_id,)
        ).fetchone()[0]
        == 0
    )


def test_an_ach_file_opens_its_numbers_only_under_the_key_they_were_sealed_with(
    client, conn, staff, pmc, monkeypatch
):
    fund(conn, pmc, "500.00")
    ach_details(client, pmc)
    owner_bank(client, pmc)
    run = ok(pay_owners(client, pmc, "EFT"), 201)[0]["run_id"]

    monkeypatch.delenv("PMS_DATA_KEY")
    gone = client.get(f"/api/payouts/runs/{run}/ach")
    refused(gone, 409)
    assert gone.json()["detail"].endswith(", so it makes no ACH file")
    monkeypatch.setenv("PMS_DATA_KEY", base64.b64encode(os.urandom(32)).decode())
    refused(
        client.get(f"/api/payouts/runs/{run}/ach"),
        409,
        "Owner 1's account number doesn't open under this server's key",
    )


def test_a_check_is_addressed_to_the_payees_mailing_address(client, conn, staff, pmc):
    fund(conn, pmc, "500.00")
    ach_details(client, pmc, first_check=101)
    owner = pmc.owners[0].owner_id
    address = {
        "address_line_1": "1 Example Road",
        "address_line_2": "Suite 2",
        "city": "Sampletown",
    }
    saved = send(client, f"owners/{owner}/contact", **address, state="ZZ", postal_code="00000")
    assert saved.status_code == 204, saved.text
    run = ok(pay_owners(client, pmc, "check"), 201)[0]["run_id"]

    pdf = client.get(f"/api/payouts/runs/{run}/checks")

    assert pdf.status_code == 200, pdf.text
    text = "".join(page.extract_text() for page in PdfReader(io.BytesIO(pdf.content)).pages)
    assert "1 Example Road" in text and "Suite 2" in text and "Sampletown, ZZ 00000" in text


def test_saving_the_same_way_paid_or_bank_details_again_writes_nothing(client, conn, staff, pmc):
    owner = pmc.owners[0].owner_id
    ok(send(client, f"payouts/owners/{owner}/method", method="EFT"))
    ok(send(client, f"payouts/owners/{owner}/method", method="EFT"))
    ach_details(client, pmc)
    ach_details(client, pmc)

    counts = conn.execute(
        "SELECT (SELECT count(*) FROM trust_owner_payout_methods WHERE pmc_id = %(p)s),"
        " (SELECT count(*) FROM trust_bank_payout_settings WHERE pmc_id = %(p)s)",
        {"p": pmc.pmc_id},
    ).fetchone()
    assert counts == (1, 1)
