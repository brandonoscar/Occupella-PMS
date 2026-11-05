"""What a company still needs before it goes live (GET /api/go-live, src/pms/api/go_live.py): each
step is worked out from the company's own records, so a step done anywhere shows as done; the
company is ready once every required step is. Staff read it; owners, tenants and strangers don't.
"""

import json
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from helpers import add_unit, make_pmc, open_lease
from people import join_as_owner, join_as_staff, join_as_tenant, tenant_of

from pms.api import Settings, create_app

TODAY = datetime.now(UTC).date()
KEYS = [
    "company",
    "bank_accounts",
    "brokers",
    "properties",
    "leases",
    "team",
    "email",
    "portals",
    "online_rent",
]


def ok(answer, status=200):
    assert answer.status_code == status, answer.text
    return answer.json() if answer.content else None


def done(answer) -> dict[str, bool]:
    return {step["key"]: step["done"] for step in answer["steps"]}


@pytest.fixture
def emailing(database_url):
    """A server that sends email (the fake sender keeps it in the outbox)."""
    settings = Settings(
        database_url=database_url,
        public_url="http://testserver",
        cookie_secure=False,
        email_sender="fake",
    )
    with (
        TestClient(create_app(settings)) as staff,
        TestClient(create_app(settings)) as second,
        TestClient(create_app(settings)) as owner,
    ):
        yield staff, second, owner


def test_a_new_company_sees_what_it_still_needs(client, conn, app_conn, pmc):
    join_as_staff(client, conn, app_conn, pmc)
    shown = ok(client.get("/api/go-live"))

    assert [step["key"] for step in shown["steps"]] == KEYS
    assert shown["ready"] is False
    # A synthetic company has its two trust bank accounts and its owners' properties already.
    assert done(shown) == {
        "company": False,
        "bank_accounts": True,
        "brokers": False,
        "properties": True,
        "leases": False,
        "team": False,
        "email": False,
        "portals": False,
        "online_rent": False,
    }
    required = {step["key"] for step in shown["steps"] if step["required"]}
    assert required == set(KEYS) - {"portals", "online_rent"}
    assert all(
        step["label"] and step["detail"] and step["href"].startswith("/") for step in shown["steps"]
    )


def test_each_step_is_done_by_the_companys_own_records(emailing, conn, app_conn, pmc):
    staff, second, owner = emailing
    admin = join_as_staff(staff, conn, app_conn, pmc, name="Admin 1")
    # The company's address, keeping its day UTC's as the tests count days (a profile's time
    # zone left out would be America/New_York's).
    conn.execute(
        "INSERT INTO trust_company_profiles"
        " (pmc_id, address_line_1, city, state, postal_code, time_zone)"
        " VALUES (%s, '100 Example Street', 'Demo City', 'CA', '00001', 'UTC')",
        (pmc.pmc_id,),
    )
    for bank in conn.execute(
        "SELECT id FROM trust_bank_accounts WHERE pmc_id = %s", (pmc.pmc_id,)
    ).fetchall():
        body = {
            "bank_account_id": bank[0],
            "staff_id": admin.person_id,
            "role": "broker",
            "granted": True,
            "license_number": "LIC-0001",
            "authorized_on": TODAY,
        }
        ok(staff.post("/api/signers", json=json.loads(json.dumps(body, default=str))), 204)
    unit = add_unit(conn, pmc.pmc_id, pmc.owners[0].property_id)
    open_lease(conn, pmc.pmc_id, unit, TODAY, None, "1200.00", [tenant_of(conn, pmc)])
    join_as_staff(second, conn, app_conn, pmc, name="Approver 1")
    shown = ok(staff.get("/api/go-live"))
    assert [s["key"] for s in shown["steps"] if not s["done"]] == ["portals", "online_rent"]
    assert shown["ready"] is True  # the optional steps can wait

    join_as_owner(owner, app_conn, pmc)
    conn.execute(
        "INSERT INTO trust_payment_settings (pmc_id, online, ach_fee_payer)"
        " VALUES (%s, true, 'company')",
        (pmc.pmc_id,),
    )
    shown = ok(staff.get("/api/go-live"))
    assert done(shown) == dict.fromkeys(KEYS, True)
    assert shown["ready"] is True


def test_another_companys_records_count_for_nothing(client, conn, app_conn, pmc):
    theirs = make_pmc(conn)
    conn.execute(
        "INSERT INTO trust_company_profiles (pmc_id, address_line_1, city, state, postal_code)"
        " VALUES (%s, '9 Example Road', 'Elsewhere', 'ZZ', '00000')",
        (theirs.pmc_id,),
    )
    unit = add_unit(conn, theirs.pmc_id, theirs.owners[0].property_id)
    open_lease(conn, theirs.pmc_id, unit, TODAY, None, "900.00", [tenant_of(conn, theirs)])
    join_as_staff(client, conn, app_conn, pmc)

    shown = done(ok(client.get("/api/go-live")))
    assert shown["company"] is False and shown["leases"] is False


def test_only_staff_read_it(client, other_client, conn, app_conn, pmc):
    assert client.get("/api/go-live").status_code == 401
    join_as_tenant(client, conn, app_conn, pmc)
    assert client.get("/api/go-live").status_code == 403
    join_as_owner(other_client, app_conn, pmc)
    assert other_client.get("/api/go-live").status_code == 403


def test_it_only_reads(client, conn, app_conn, pmc):
    join_as_staff(client, conn, app_conn, pmc)
    assert client.post("/api/go-live", json={"ready": True}).status_code == 405
