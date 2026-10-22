"""Bank deposits through the API (src/pms/api/bank_deposits.py): what came into a trust bank
account and is in no deposit yet, each payment once; a deposit of what staff take to the bank,
all or none, with its slip; and month-end, where the deposit's one bank line matches all of it.
Every name and number here is synthetic."""

import io
import json
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from helpers import add_unit, charge, make_pmc, open_lease
from people import join_as_owner, join_as_staff, join_as_tenant, tenant_of
from pypdf import PdfReader

TODAY = datetime.now(UTC).date()
DAY = timedelta(days=1)
START = TODAY - timedelta(days=60)


@pytest.fixture
def staff(client, conn, app_conn, pmc):
    return join_as_staff(client, conn, app_conn, pmc)


@pytest.fixture
def lease(conn, pmc):
    unit = add_unit(conn, pmc.pmc_id, pmc.owners[0].property_id, name="Unit 4")
    made = open_lease(conn, pmc.pmc_id, unit, START, None, "1500.00", [tenant_of(conn, pmc)])
    charge(conn, pmc.pmc_id, made, START, "1500.00", memo="Rent")
    return made


def ok(answer, status=200):
    assert answer.status_code == status, answer.text
    return answer.json()


def post(client, path, **body):
    return client.post(path, json=json.loads(json.dumps(body, default=str)))


def pay(client, lease, amount, reference, on=TODAY):
    return ok(
        post(
            client,
            f"/api/rent/leases/{lease}/payments",
            request_id=uuid.uuid4(),
            amount=amount,
            received_on=on,
            method="check",
            reference=reference,
        ),
        201,
    )["transfers"]


def undeposited(client, pmc):
    return ok(client.get(f"/api/bank-accounts/{pmc.operating_bank_id}/undeposited"))


def deposit(client, pmc, transfer_ids, on=TODAY):
    return post(
        client,
        f"/api/bank-accounts/{pmc.operating_bank_id}/deposits",
        deposited_on=on,
        transfer_ids=transfer_ids,
    )


def test_staff_take_payments_to_the_bank_together_and_print_the_slip(
    client, conn, staff, pmc, lease
):
    first = pay(client, lease, "1600.00", "Check 101", TODAY - DAY)  # $100 held as prepaid
    second = pay(client, lease, "200.00", "Check 102")

    waiting = undeposited(client, pmc)
    assert [
        (w["transfer_ids"], w["type"], w["party"], w["memo"], w["amount"]) for w in waiting
    ] == [
        (first, "Rent payment", "Tenant 1", "Check 101", "1600.00"),
        (second, "Rent payment", "Tenant 1", "Check 102", "200.00"),
    ]

    made = ok(deposit(client, pmc, first + second), 201)
    assert (made["deposited_on"], made["total"], made["items"], made["recorded_by"]) == (
        str(TODAY),
        "1800.00",
        3,
        "Staff 1",
    )
    assert made["matched"] is False
    assert undeposited(client, pmc) == []
    assert ok(client.get(f"/api/bank-accounts/{pmc.operating_bank_id}/deposits")) == [made]

    slip = client.get(f"/api/bank-accounts/deposits/{made['id']}/slip")
    assert slip.status_code == 200 and slip.headers["content-type"] == "application/pdf"
    text = " ".join(p.extract_text() for p in PdfReader(io.BytesIO(slip.content)).pages)
    for part in (
        "Deposit slip",
        "Check 101",
        "Check 102",
        "1600.00",
        "200.00",
        "1800.00",
        "3 items",
    ):
        assert part in text, part


def test_a_deposit_is_all_or_none(client, conn, staff, pmc, lease):
    paid = pay(client, lease, "500.00", "Check 7")
    held = ok(
        post(
            client,
            "/api/rent/deposits",
            request_id=uuid.uuid4(),
            tenant_id=tenant_of(conn, pmc),
            amount="900.00",
            received_on=TODAY,
        ),
        201,
    )["transfers"]
    # A security deposit sits in the other trust bank account: not this one's to deposit.
    refused = deposit(client, pmc, paid + held)
    assert refused.status_code == 422 and "brought no money" in refused.json()["detail"]
    assert ok(client.get(f"/api/bank-accounts/{pmc.operating_bank_id}/deposits")) == []
    assert [w["transfer_ids"] for w in undeposited(client, pmc)] == [paid]
    # Its own account takes it.
    ok(
        post(
            client,
            f"/api/bank-accounts/{pmc.deposit_bank_id}/deposits",
            deposited_on=TODAY,
            transfer_ids=held,
        ),
        201,
    )
    ok(deposit(client, pmc, paid), 201)
    again = deposit(client, pmc, paid)
    assert again.status_code == 409 and "another deposit" in again.json()["detail"]


def test_a_deposit_day_is_not_before_the_money_came_or_after_today(client, staff, pmc, lease):
    paid = pay(client, lease, "500.00", "Check 7")
    early = deposit(client, pmc, paid, on=TODAY - DAY)
    assert early.status_code == 422 and "came in after the deposit" in early.json()["detail"]
    assert deposit(client, pmc, paid, on=TODAY + DAY).status_code == 422
    twice = deposit(client, pmc, paid + paid)
    assert twice.status_code == 422 and "once" in twice.json()["detail"]
    unknown = deposit(client, pmc, ["pglt_not_a_transfer"])
    assert unknown.status_code == 422 and "brought no money" in unknown.json()["detail"]


def test_month_end_matches_a_deposits_one_bank_line_to_all_of_it(client, conn, staff, pmc, lease):
    first = pay(client, lease, "1600.00", "Check 101", TODAY - DAY)
    second = pay(client, lease, "200.00", "Check 102")
    ok(deposit(client, pmc, first + second), 201)
    lone = pay(client, lease, "75.00", "Check 103")
    ok(
        post(
            client,
            "/api/reconcile/statements",
            bank_account_id=pmc.operating_bank_id,
            format="csv",
            content="Date,Description,Amount,Reference\n"
            f"{TODAY},Deposit,1800.00,D1\n{TODAY},Deposit,75.00,D2\n",
            first_day=TODAY - 2 * DAY,
            last_day=TODAY,
        ),
        201,
    )
    sheet = ok(client.get(f"/api/reconcile/{pmc.operating_bank_id}"))
    assert {t["id"]: t["deposited_on"] for t in sheet["open_transfers"]} == {
        **dict.fromkeys(first + second, str(TODAY)),
        **dict.fromkeys(lone, None),
    }
    # Before matching, the deposit's line has the deposit to match (not a line the books lack),
    # and each payment in it waits for that line: none is said to be missing.
    assert sheet["unmatched"] == {
        "lines": 2,
        "total": "1875.00",
        "sure": 2,
        "likely": 2,
        "likely_total": "1875.00",
        "alone": 0,
        "alone_total": "0.00",
    }
    assert all(r["pending"] for r in sheet["rows"] if r["line"] > 9)

    counted = ok(post(client, f"/api/reconcile/{pmc.operating_bank_id}/auto-match"))
    assert counted == {"matched": 2}
    matches = ok(client.get(f"/api/reconcile/{pmc.operating_bank_id}"))["matches"]
    assert sorted(sorted(t["id"] for t in m["transfers"]) for m in matches) == sorted(
        [sorted(first + second), lone]
    )
    (shown,) = ok(client.get(f"/api/bank-accounts/{pmc.operating_bank_id}/deposits"))
    assert shown["matched"] is True


def test_another_companys_account_or_deposit_is_not_found(client, conn, staff, pmc, lease):
    other = make_pmc(conn)
    for path in ("undeposited", "deposits"):
        assert client.get(f"/api/bank-accounts/{other.operating_bank_id}/{path}").status_code == 404
    refused = post(
        client,
        f"/api/bank-accounts/{other.operating_bank_id}/deposits",
        deposited_on=TODAY,
        transfer_ids=["x"],
    )
    assert refused.status_code == 404
    theirs = conn.execute(
        "INSERT INTO trust_bank_deposits (pmc_id, bank_account_id, deposited_on)"
        " VALUES (%s, %s, %s) RETURNING id",
        (other.pmc_id, other.operating_bank_id, TODAY),
    ).fetchone()[0]
    assert client.get(f"/api/bank-accounts/deposits/{theirs}/slip").status_code == 404


@pytest.mark.parametrize("caller", ["nobody", "owner", "tenant"])
def test_only_staff_deposit(client, conn, app_conn, pmc, caller):
    if caller == "owner":
        join_as_owner(client, app_conn, pmc)
    elif caller == "tenant":
        join_as_tenant(client, conn, app_conn, pmc)
    status = 401 if caller == "nobody" else 403
    bank = pmc.operating_bank_id
    assert client.get(f"/api/bank-accounts/{bank}/undeposited").status_code == status
    assert client.get(f"/api/bank-accounts/{bank}/deposits").status_code == status
    assert (
        post(client, f"/api/bank-accounts/{bank}/deposits", deposited_on=TODAY, transfer_ids=["x"])
    ).status_code == status
    assert client.get(f"/api/bank-accounts/deposits/{uuid.uuid4()}/slip").status_code == status


@pytest.mark.parametrize(
    "body",
    [
        {"deposited_on": "today", "transfer_ids": ["x"]},
        {"deposited_on": "2026-01-01", "transfer_ids": []},
        {"deposited_on": "2026-01-01"},
        {"deposited_on": "2026-01-01", "transfer_ids": [""]},
    ],
)
def test_bad_deposits_are_refused(client, staff, pmc, body):
    answer = client.post(f"/api/bank-accounts/{pmc.operating_bank_id}/deposits", json=body)
    assert answer.status_code == 422
