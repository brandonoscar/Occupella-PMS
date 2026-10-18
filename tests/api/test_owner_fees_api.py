"""Owners' fees through the API (src/pms/api/payables.py; the founder's review, q049 to q051):
staff turn taking the management fees each month on or off from a day of the month, and take
other fees from an owner's account (an inspection, a lease-up, another), once per request."""

import json
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from helpers import balance, contribute, make_pmc
from people import join_as_owner, join_as_staff, join_as_tenant

NOW = datetime.now(UTC)
TODAY = NOW.date()


@pytest.fixture
def staff(client, conn, app_conn, pmc):
    return join_as_staff(client, conn, app_conn, pmc)


def ok(answer, status=200):
    assert answer.status_code == status, answer.text
    return None if status == 204 else answer.json()


def refused(answer, status, detail=None):
    assert answer.status_code == status, answer.text
    if detail is not None:
        assert answer.json()["detail"] == detail


def automatic(client, **body):
    return client.post(
        "/api/payables/management-fees/automatic",
        json={"automatic": True, "day_of_month": 5} | body,
    )


def fee(client, pmc, request_id=None, **body):
    body = {
        "request_id": request_id or uuid.uuid4(),
        "account": pmc.owners[0].account,
        "kind": "inspection",
        "amount": "75.00",
        "memo": "Move-in inspection, Unit 1",
        "on": TODAY,
    } | body
    return client.post("/api/payables/owner-fees", json=json.loads(json.dumps(body, default=str)))


def settings_rows(conn, pmc):
    return conn.execute(
        "SELECT count(*) FROM trust_management_fee_settings WHERE pmc_id = %s", (pmc.pmc_id,)
    ).fetchone()[0]


def test_staff_turn_on_taking_the_management_fees_each_month(client, conn, staff, pmc):
    assert ok(client.get("/api/payables/management-fees/automatic")) == {
        "automatic": False,
        "day_of_month": 1,
        "changed_at": None,
        "changed_by": None,
    }
    turned = ok(automatic(client))
    assert (turned["automatic"], turned["day_of_month"], turned["changed_by"]) == (
        True,
        5,
        "Staff 1",
    )
    # Saving what holds already writes nothing; a change writes a row.
    ok(automatic(client))
    assert settings_rows(conn, pmc) == 1
    off = ok(automatic(client, automatic=False))
    assert (off["automatic"], off["day_of_month"]) == (False, 5)
    assert settings_rows(conn, pmc) == 2
    assert ok(client.get("/api/payables/management-fees/automatic")) == off


@pytest.mark.parametrize(
    "body",
    [{"day_of_month": 0}, {"day_of_month": 29}, {"day_of_month": "fifth"}, {"automatic": None}],
)
def test_bad_settings_are_refused(client, conn, staff, pmc, body):
    refused(automatic(client, **body), 422)
    assert settings_rows(conn, pmc) == 0


@pytest.mark.parametrize("caller", ["nobody", "owner", "tenant"])
def test_only_staff_see_and_change_the_settings(client, conn, app_conn, pmc, caller):
    if caller == "owner":
        join_as_owner(client, app_conn, pmc)
    elif caller == "tenant":
        join_as_tenant(client, conn, app_conn, pmc)
    status = 401 if caller == "nobody" else 403
    refused(client.get("/api/payables/management-fees/automatic"), status)
    refused(automatic(client), status)
    refused(fee(client, pmc), status)


def test_a_custom_role_without_owners_money_changes_neither(
    client, other_client, conn, app_conn, staff, pmc
):
    clerk = join_as_staff(other_client, conn, app_conn, pmc, name="Clerk 2", roles=[])
    role = ok(client.post("/api/custom-roles", json={"name": "Books", "areas": ["bills"]}), 201)
    ok(
        client.post(
            f"/api/custom-roles/{role['id']}/staff",
            json={"staff_id": str(clerk.person_id), "granted": True},
        ),
        204,
    )
    refused(automatic(other_client), 403)
    refused(fee(other_client, pmc), 403)
    # Reading stays open.
    ok(other_client.get("/api/payables/management-fees/automatic"))


def test_staff_take_a_fee_from_an_owner_once_per_request(client, conn, staff, pmc):
    contribute(conn, pmc.pmc_id, pmc.owners[0].account, "in-1", "500.00", NOW - timedelta(days=1))
    request = uuid.uuid4()
    first = ok(fee(client, pmc, request), 201)
    again = ok(fee(client, pmc, request), 201)
    assert again == first
    refused(
        fee(client, pmc, request, amount="80.00"),
        409,
        "this request already took a fee of $75.00: start a new one",
    )
    assert balance(conn, pmc.owners[0].account) == Decimal("425.00")
    assert balance(conn, pmc.pmc_income) == Decimal("75.00")
    # The owner's statement says what it was for, and the audit log who took it.
    rows = ok(
        client.get(
            f"/api/owners/{pmc.owners[0].owner_id}/statement",
            params={"start": str(TODAY), "end": str(TODAY + timedelta(days=1))},
        )
    )
    assert any("Inspection fee: Move-in inspection, Unit 1" in str(row) for row in rows["rows"])
    assert conn.execute(
        "SELECT recorded_by FROM trust_owner_fees WHERE transfer_id = %s",
        (first["transfer_id"],),
    ).fetchone() == (staff.person_id,)


def test_a_fee_more_than_the_owner_holds_is_refused_in_words(client, conn, staff, pmc):
    contribute(conn, pmc.pmc_id, pmc.owners[0].account, "in-2", "50.00", NOW - timedelta(days=1))
    refused(
        fee(client, pmc),
        409,
        "not enough money: the owner's account would be $25.00 short",
    )
    assert balance(conn, pmc.owners[0].account) == Decimal("50.00")


def test_staff_without_disburse_take_no_fee(client, other_client, conn, app_conn, staff, pmc):
    contribute(conn, pmc.pmc_id, pmc.owners[0].account, "in-3", "500.00", NOW - timedelta(days=1))
    join_as_staff(other_client, conn, app_conn, pmc, name="Clerk 3", roles=[])
    answer = fee(other_client, pmc)
    refused(answer, 403)
    assert "disburse" in answer.json()["detail"]


def test_another_companys_account_is_not_found(client, conn, staff, pmc):
    other = make_pmc(conn)
    refused(fee(client, pmc, account=other.owners[0].account), 404, "no such owner account")


@pytest.mark.parametrize(
    "body",
    [
        {"kind": "markup"},
        {"amount": "0"},
        {"amount": "1.001"},
        {"memo": "  "},
        {"memo": "x" * 201},
        {"on": str(TODAY + timedelta(days=1))},
        {"request_id": "again"},
    ],
)
def test_bad_fees_are_refused(client, conn, staff, pmc, body):
    contribute(conn, pmc.pmc_id, pmc.owners[0].account, "in-4", "500.00", NOW - timedelta(days=1))
    refused(fee(client, pmc, **body), 422)
    assert conn.execute(
        "SELECT count(*) FROM trust_owner_fees WHERE pmc_id = %s", (pmc.pmc_id,)
    ).fetchone() == (0,)
