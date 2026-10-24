"""A trust bank account's register and breakdown through the API (src/pms/api/banking.py,
ADR 0006): every move of its cash with what it was, who paid or was paid, its memo, its
property and the balance after it, a page at a time; and what it holds, by kind and by owner.
Rent receipts, bounces and deposits carry a memo naming the tenants and the home. Always the
session's PMC."""

import json
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from helpers import add_agreement, add_tenant, add_unit, charge, make_pmc, open_lease
from people import join_as_owner, join_as_staff, join_as_tenant

TODAY = datetime.now(UTC).date()
DAY = timedelta(days=1)


@pytest.fixture
def staff(client, conn, app_conn, pmc):
    return join_as_staff(client, conn, app_conn, pmc)


def ok(answer, status=200):
    assert answer.status_code == status, answer.text
    return answer.json()


def post(client, path, **body):
    return client.post(path, json=json.loads(json.dumps(body, default=str)))


def rented(conn, pmc):
    prop = pmc.owners[0].property_id
    unit = add_unit(conn, pmc.pmc_id, prop, "Unit 1")
    tenant = add_tenant(conn, pmc.pmc_id, prop, "Tenant A")
    lease = open_lease(conn, pmc.pmc_id, unit, TODAY - 40 * DAY, None, "1000.00", [tenant])
    charge(conn, pmc.pmc_id, lease, TODAY - 10 * DAY, "1000.00")
    return lease, tenant


def names(conn, pmc):
    return conn.execute(
        "SELECT o.display_name, p.display_name FROM trust_owners AS o, trust_properties AS p"
        " WHERE o.id = %s AND p.id = %s",
        (pmc.owners[0].owner_id, pmc.owners[0].property_id),
    ).fetchone()


def money_moves(client, conn, pmc):
    """A rent payment, a bounced one, a deposit, an owner contribution and a payout."""
    lease, tenant = rented(conn, pmc)
    pay = {"amount": "1000.00", "received_on": TODAY - 5 * DAY, "reference": "Check 7"}
    ok(post(client, f"/api/rent/leases/{lease}/payments", request_id=uuid.uuid4(), **pay), 201)
    bounced = ok(
        post(
            client,
            f"/api/rent/leases/{lease}/payments",
            request_id=uuid.uuid4(),
            amount="50.00",
            received_on=TODAY - 4 * DAY,
        ),
        201,
    )["transfers"][0]
    ok(post(client, f"/api/rent/payments/{bounced}/bounce", on=TODAY - 3 * DAY), 201)
    ok(
        post(
            client,
            "/api/rent/deposits",
            request_id=uuid.uuid4(),
            tenant_id=tenant,
            amount="900.00",
            received_on=TODAY - 2 * DAY,
        ),
        201,
    )
    account = pmc.owners[0].account
    ok(
        post(
            client,
            "/api/payables/contributions",
            request_id=uuid.uuid4(),
            account=account,
            amount="25.00",
            on=TODAY - DAY,
            memo="Roof fund",
        ),
        201,
    )
    ok(
        post(
            client,
            "/api/payables/draws/batch",
            request_id=uuid.uuid4(),
            on=TODAY,
            method="check",
            payouts=[{"account": account, "amount": "300.00"}],
        ),
        201,
    )


def test_the_register_lists_each_move_of_cash_with_what_it_was_and_the_balance_after(
    client, conn, staff, pmc
):
    money_moves(client, conn, pmc)
    owner, prop = names(conn, pmc)

    register = ok(client.get(f"/api/bank-accounts/{pmc.operating_bank_id}/register"))
    lines = [
        (
            r["type"],
            r["party"],
            r["memo"],
            r["property"],
            r["deposit"],
            r["withdrawal"],
            r["balance"],
        )
        for r in register["items"]
    ]

    assert lines == [
        ("Owner draw", owner, "Owner draw", prop, None, "300.00", "725.00"),
        ("Owner contribution", owner, "Roof fund", prop, "25.00", None, "1025.00"),
        (
            "Returned payment",
            "Tenant A",
            f"Returned payment – Tenant A, {prop} Unit 1",
            prop,
            None,
            "50.00",
            "1000.00",
        ),
        (
            "Rent payment",
            "Tenant A",
            f"Rent – Tenant A, {prop} Unit 1",
            prop,
            "50.00",
            None,
            "1050.00",
        ),
        (
            "Rent payment",
            "Tenant A",
            f"Rent – Tenant A, {prop} Unit 1",
            prop,
            "1000.00",
            None,
            "1000.00",
        ),
    ]
    assert register["total"] == 5
    # The security deposit went into the deposit account, with its own memo.
    [deposit] = ok(client.get(f"/api/bank-accounts/{pmc.deposit_bank_id}/register"))["items"]
    assert (deposit["type"], deposit["memo"], deposit["deposit"], deposit["balance"]) == (
        "Security deposit",
        f"Security deposit – Tenant A, {prop}",
        "900.00",
        "900.00",
    )


def test_the_register_takes_days_and_pages(client, conn, staff, pmc):
    money_moves(client, conn, pmc)
    bank = pmc.operating_bank_id

    window = ok(
        client.get(
            f"/api/bank-accounts/{bank}/register?start={TODAY - 4 * DAY}&end={TODAY - 3 * DAY}"
        )
    )
    second = ok(client.get(f"/api/bank-accounts/{bank}/register?per_page=2&page=2"))

    assert [r["type"] for r in window["items"]] == ["Returned payment", "Rent payment"]
    # The balance after each counts everything before it, even outside the days shown.
    assert [r["balance"] for r in window["items"]] == ["1000.00", "1050.00"]
    assert [r["type"] for r in second["items"]] == ["Returned payment", "Rent payment"]
    assert second["total"] == 5
    backwards = client.get(f"/api/bank-accounts/{bank}/register?start={TODAY}&end={TODAY - DAY}")
    assert backwards.status_code == 422


def test_the_breakdown_is_what_the_account_holds_by_kind_and_by_owner(client, conn, staff, pmc):
    add_agreement(conn, pmc.pmc_id, pmc.owners[0].account, TODAY - 60 * DAY, reserve="100.00")
    money_moves(client, conn, pmc)

    found = ok(client.get(f"/api/bank-accounts/{pmc.operating_bank_id}/breakdown"))

    assert found["book_cash"] == "725.00"
    assert [(h["label"], h["amount"]) for h in found["held"]] == [
        ("Owners' money", "725.00"),
        ("Prepaid rent", "0.00"),
        ("Held to pay vendors", "0.00"),
        ("The company's fees", "0.00"),
    ]
    assert sum(Decimal(h["amount"]) for h in found["held"]) == Decimal(found["book_cash"])
    assert [(o["balance"], o["reserve"], o["available"]) for o in found["owners"]] == [
        ("725.00", "100.00", "625.00"),
        ("0.00", "0.00", "0.00"),
    ]
    deposits = ok(client.get(f"/api/bank-accounts/{pmc.deposit_bank_id}/breakdown"))
    assert (deposits["book_cash"], deposits["owners"]) == ("900.00", [])
    assert [(h["label"], h["amount"]) for h in deposits["held"]] == [
        ("Tenants' security deposits", "900.00")
    ]


def test_another_companys_account_is_not_found(client, conn, staff):
    theirs = make_pmc(conn).operating_bank_id
    for path in ("register", "breakdown"):
        answer = client.get(f"/api/bank-accounts/{theirs}/{path}")
        assert answer.status_code == 404, path
        assert answer.json()["detail"] == "no such trust bank account"
    assert client.get("/api/bank-accounts/not-an-id/register").status_code == 422


@pytest.mark.parametrize("caller", ["nobody", "owner", "tenant"])
@pytest.mark.parametrize("path", ["register", "breakdown"])
def test_only_staff_read_a_trust_bank_account(client, conn, app_conn, pmc, caller, path):
    if caller == "owner":
        join_as_owner(client, app_conn, pmc)
    elif caller == "tenant":
        join_as_tenant(client, conn, app_conn, pmc)
    answer = client.get(f"/api/bank-accounts/{pmc.operating_bank_id}/{path}")
    assert answer.status_code == (401 if caller == "nobody" else 403)
