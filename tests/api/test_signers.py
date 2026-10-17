"""Who signs each trust bank account through the API (src/pms/api/signers.py): an admin names
its broker, the broker authorizes signers, and once the broker is on file only its signers pay
money out of it."""

import json
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from helpers import balance, make_pmc, post
from people import join_as_owner, join_as_staff

NOW = datetime.now(UTC)
TODAY = NOW.date()
DAY = timedelta(days=1)
OPERATING = "Synthetic operating trust account"
DEPOSITS = "Synthetic security_deposit trust account"


@pytest.fixture
def admin(client, conn, app_conn, pmc):
    return join_as_staff(client, conn, app_conn, pmc, name="Admin 1")


def ok(answer, status=200):
    assert answer.status_code == status, answer.text
    return answer.json() if answer.content else None


def refused(answer, status, detail=None):
    assert answer.status_code == status, answer.text
    if detail is not None:
        assert answer.json()["detail"] == detail


def change(client, pmc, staff_id, **body):
    body = {
        "bank_account_id": pmc.operating_bank_id,
        "staff_id": staff_id,
        "role": "signer",
        "granted": True,
        "authorized_on": TODAY,
    } | body
    return client.post("/api/signers", json=json.loads(json.dumps(body, default=str)))


def listed(client, on=None):
    found = ok(client.get("/api/signers", params={"on": str(on)} if on else {}))
    return {
        a["bank_account"]: [(s["name"], s["role"], s["standing"]) for s in a["signers"]]
        for a in found
    }


def draw(client, pmc, amount="10.00"):
    return client.post(
        "/api/payables/draws/batch",
        json={
            "request_id": str(uuid.uuid4()),
            "on": str(TODAY),
            "method": "check",
            "payouts": [{"account": pmc.owners[0].account, "amount": amount}],
        },
    )


def test_an_admin_names_the_broker_who_authorizes_signers(
    client, other_client, conn, app_conn, admin, pmc
):
    post(conn, pmc.pmc_id, "rent-1", [(pmc.operating_cash, pmc.owners[0].account, Decimal("500"))])
    clerk = join_as_staff(other_client, conn, app_conn, pmc, name="Clerk 1", roles=["disburse"])
    assert listed(client) == {OPERATING: [], DEPOSITS: []}
    # No broker on file: the roles alone apply.
    ok(draw(other_client, pmc), 201)

    ok(change(client, pmc, admin.person_id, role="broker", license_number=" LIC-0001 "), 204)
    assert listed(client)[OPERATING] == [("Admin 1", "broker", "licensed")]
    refused(
        draw(other_client, pmc),
        403,
        f"Clerk 1 is not an authorized signer of {OPERATING}",
    )
    # The broker authorizes the clerk, bonded; the clerk then pays out.
    ok(
        change(
            client,
            pmc,
            clerk.person_id,
            bond_amount="25000.00",
            bond_expires_on=str(TODAY + 10 * DAY),
        ),
        204,
    )
    assert listed(client)[OPERATING] == [
        ("Admin 1", "broker", "licensed"),
        ("Clerk 1", "signer", "bond runs out in 10 days"),
    ]
    ok(draw(other_client, pmc), 201)
    today = ok(client.get("/api/today"))["signers"]
    assert [(n["bank_account"], n["signer"], n["standing"]) for n in today] == [
        (OPERATING, "Clerk 1", "bond runs out in 10 days"),
        (DEPOSITS, None, "no broker on file"),
    ]
    # Taken away, they pay out no more.
    ok(change(client, pmc, clerk.person_id, granted=False), 204)
    refused(draw(other_client, pmc), 403)
    assert balance(conn, pmc.owners[0].account) == Decimal("480.00")


def test_a_new_broker_replaces_the_one_on_file(client, other_client, conn, app_conn, admin, pmc):
    second = join_as_staff(other_client, conn, app_conn, pmc, name="Broker 2", roles=[])
    ok(change(client, pmc, admin.person_id, role="broker", license_number="LIC-0001"), 204)

    ok(change(client, pmc, second.person_id, role="broker", license_number="LIC-0002"), 204)

    assert listed(client)[OPERATING] == [("Broker 2", "broker", "licensed")]


def test_only_an_admin_names_a_broker_and_only_the_broker_adds_signers(
    client, other_client, conn, app_conn, admin, pmc
):
    clerk = join_as_staff(other_client, conn, app_conn, pmc, name="Clerk 1", roles=["disburse"])
    refused(
        change(other_client, pmc, clerk.person_id, role="broker", license_number="LIC-0002"),
        403,
        "Clerk 1 may not name a trust bank account's broker (the admin role)",
    )
    ok(change(client, pmc, clerk.person_id, role="broker", license_number="LIC-0002"), 204)
    # The admin is not the broker now: only the clerk adds signers.
    refused(
        change(client, pmc, admin.person_id, license_number="LIC-0001"),
        403,
        "only this trust bank account's broker authorizes its signers",
    )
    ok(change(other_client, pmc, admin.person_id, license_number="LIC-0001"), 204)
    # Nor may the broker, not being an admin, take their own place away.
    refused(
        change(other_client, pmc, clerk.person_id, role="broker", granted=False),
        403,
        "Clerk 1 may not name a trust bank account's broker (the admin role)",
    )


def test_signers_need_their_broker_and_take_away_only_what_is_held(client, conn, admin, pmc):
    refused(
        change(client, pmc, admin.person_id, granted=False),
        409,
        "Admin 1 is not the signer of this trust bank account; nothing to take away",
    )
    ok(change(client, pmc, admin.person_id, role="broker", license_number="LIC-0001"), 204)
    refused(
        change(client, pmc, admin.person_id, license_number="LIC-0001"),
        409,
        "the broker signs this trust bank account already",
    )


def test_only_staff_see_or_change_signers(client, other_client, app_conn, conn, pmc):
    refused(client.get("/api/signers"), 401)
    refused(change(client, pmc, uuid.uuid4(), license_number="L"), 401)
    join_as_owner(client, app_conn, pmc)
    refused(client.get("/api/signers"), 403)
    refused(change(client, pmc, uuid.uuid4(), license_number="L"), 403)
    # Another PMC's staff see their own accounts only, and none of these.
    other = make_pmc(conn)
    staff = join_as_staff(other_client, conn, app_conn, other, name="Staff 2")
    refused(
        change(other_client, pmc, staff.person_id, license_number="L"),
        404,
        "no such trust bank account",
    )
    refused(
        change(other_client, other, uuid.uuid4(), license_number="L"),
        404,
        "no such staff member in this PMC",
    )


@pytest.mark.parametrize(
    ("body", "why"),
    [
        ({}, "say how they may sign: a license number, or a bond"),
        ({"bond_amount": "100.00"}, "a bond has an amount and a day it runs out"),
        ({"bond_expires_on": "2026-12-31"}, "a bond has an amount and a day it runs out"),
        ({"granted": False, "license_number": "LIC-1"}, "taking it away says nothing more"),
        ({"role": "broker", "bond_amount": "1.00", "bond_expires_on": "2026-12-31"}, None),
        ({"role": "owner", "license_number": "LIC-1"}, None),
        ({"license_number": "L" * 51}, None),
        ({"license_number": "LIC-1", "bond_amount": "0", "bond_expires_on": "2026-12-31"}, None),
        ({"license_number": "LIC-1", "authorized_on": "soon"}, None),
    ],
)
def test_bad_input_is_refused(client, conn, admin, pmc, body, why):
    refused(change(client, pmc, admin.person_id, **body), 422, why)
    refused(client.get("/api/signers", params={"on": "soon"}), 422)
    assert conn.execute(
        "SELECT count(*) FROM trust_account_signers WHERE pmc_id = %s", (pmc.pmc_id,)
    ).fetchone() == (0,)
