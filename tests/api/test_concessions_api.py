"""Concessions through the API (src/pms/api/rent.py, migration 20261106000242): staff mark a credit
as a concession on Enter charge or a monthly credit; the lease shows it, and the owner's accrual
income statement counts it on a line of its own, a contra to rent."""

from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

import pytest
from helpers import add_unit, open_lease
from people import join_as_owner, join_as_staff, join_as_tenant

TODAY = datetime.now(UTC).date()
FIRST = TODAY.replace(day=1)


@pytest.fixture
def staff(client, conn, app_conn, pmc):
    return join_as_staff(client, conn, app_conn, pmc)


@pytest.fixture
def lease(conn, pmc):
    tenant = conn.execute(
        "SELECT tenant_id FROM trust_ledger_accounts WHERE ledger_account_id = %s",
        (pmc.prepaid_rent,),
    ).fetchone()[0]
    unit = add_unit(conn, pmc.pmc_id, pmc.owners[0].property_id)
    return open_lease(conn, pmc.pmc_id, unit, FIRST, None, "1000.00", [tenant])


def ok(answer, status=201):
    assert answer.status_code == status, answer.text
    return None if status == 204 else answer.json()


def credit(client, lease, **more):
    body = {"kind": "credit", "amount": "100.00", "due_on": str(FIRST), "memo": "Move-in special"}
    return client.post(f"/api/rent/leases/{lease}/charges", json=body | more)


def monthly(client, lease, **more):
    body = {"kind": "credit", "amount": "50.00", "memo": "Military discount"}
    body["starts_on"] = str(FIRST)
    return client.post(f"/api/rent/leases/{lease}/recurring-charges", json=body | more)


def test_staff_mark_a_credit_once_or_every_month_a_concession_and_the_statement_counts_it(
    client, conn, staff, pmc, lease
):
    once = ok(credit(client, lease, concession=True))
    every = ok(monthly(client, lease, concession=True))
    run = {"due_on": str(FIRST), "late_fees_as_of": str(FIRST)}
    ok(client.post("/api/rent/run", json=run), 200)
    plain = ok(credit(client, lease, amount="10.00", memo="Goodwill"))

    found = ok(client.get(f"/api/leases/{lease}"), 200)
    rows = conn.execute(
        "SELECT item, amount FROM trust_report_income_statement_accrual(%s, %s, %s, %s)"
        " WHERE property IS NOT NULL",
        (pmc.pmc_id, pmc.owners[0].owner_id, FIRST, TODAY),
    ).fetchall()

    assert conn.execute(
        "SELECT id, concession FROM trust_charges WHERE id = ANY(%s) ORDER BY amount",
        ([once["id"], plain["id"]],),
    ).fetchall() == [(UUID(plain["id"]), False), (UUID(once["id"]), True)]
    assert [(r["id"], r["concession"]) for r in found["recurring"]] == [(every["id"], True)]
    by = dict(rows)
    assert by["income: concessions"] == Decimal("-150.00")
    assert by["income: rent"] == Decimal("990.00")


@pytest.mark.parametrize("path", ["charges", "recurring-charges"])
def test_only_a_credit_is_a_concession(client, conn, staff, pmc, lease, path):
    sender = credit if path == "charges" else monthly

    answer = sender(client, lease, kind="fee", concession=True)

    assert answer.status_code == 422, answer.text
    assert answer.json()["detail"] == "only a credit is a concession"


@pytest.mark.parametrize("caller", ["nobody", "owner", "tenant"])
def test_only_staff_give_a_concession(client, conn, app_conn, pmc, lease, caller):
    if caller == "owner":
        join_as_owner(client, app_conn, pmc)
    elif caller == "tenant":
        join_as_tenant(client, conn, app_conn, pmc)

    answers = [credit(client, lease, concession=True), monthly(client, lease, concession=True)]

    assert [a.status_code for a in answers] == [401 if caller == "nobody" else 403] * 2
    assert conn.execute(
        "SELECT count(*) FROM trust_charges WHERE lease_id = %s AND concession", (lease,)
    ).fetchone() == (0,)


def test_a_concession_is_a_yes_or_no(client, conn, staff, pmc, lease):
    answer = credit(client, lease, concession="maybe")

    assert answer.status_code == 422, answer.text
