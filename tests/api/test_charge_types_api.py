"""Charge types through the API (the founder's review q133, migration 0100): staff add the
company's own kinds of fee in Settings (src/pms/api/setup.py), give a fee one on Enter charge or
a monthly charge (src/pms/api/rent.py), and the owner's income statement counts each type on a
line of its own."""

import csv
import io
import json
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from helpers import add_unit, make_pmc, open_lease
from people import join_as_owner, join_as_staff, join_as_tenant

TODAY = datetime.now(UTC).date()
# The first of last month: a whole month of rent and pet rent.
START = (TODAY.replace(day=1) - timedelta(days=1)).replace(day=1)


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
    return open_lease(conn, pmc.pmc_id, unit, START, None, "1500.00", [tenant])


def send(client, path, **body):
    return client.post(path, json=json.loads(json.dumps(body, default=str)))


def ok(answer, status=201):
    assert answer.status_code == status, answer.text
    return answer.json()


def add_type(client, name):
    return ok(send(client, "/api/setup/charge-types", name=name))["id"]


def test_staff_add_charge_types_and_list_them_by_name(client, conn, staff):
    pets = add_type(client, "Pet rent")
    fee = add_type(client, "  Application fee ")

    assert ok(client.get("/api/setup/charge-types"), 200) == [
        {"id": fee, "name": "Application fee"},
        {"id": pets, "name": "Pet rent"},
    ]
    (by,) = conn.execute(
        "SELECT recorded_by FROM trust_charge_types WHERE id = %s", (pets,)
    ).fetchone()
    assert by == staff.person_id


def test_a_fee_of_a_type_counts_on_its_own_line_of_the_income_statement(
    client, conn, staff, pmc, lease
):
    application = add_type(client, "Application fee")
    pets = add_type(client, "Pet rent")
    ok(
        send(
            client,
            f"/api/rent/leases/{lease}/charges",
            kind="fee",
            amount="50.00",
            due_on=START,
            memo="Application",
            charge_type_id=application,
        )
    )
    ok(
        send(
            client,
            f"/api/rent/leases/{lease}/recurring-charges",
            kind="fee",
            amount="25.00",
            memo="Dog",
            starts_on=START,
            charge_type_id=pets,
        )
    )
    ok(
        send(
            client,
            f"/api/rent/leases/{lease}/charges",
            kind="fee",
            amount="10.00",
            due_on=START,
            memo="Key copy",
        )
    )
    ok(send(client, "/api/rent/run", due_on=START, late_fees_as_of=START), 200)
    ok(
        send(
            client,
            f"/api/rent/leases/{lease}/payments",
            request_id=uuid.uuid4(),
            amount="1585.00",
            received_on=TODAY,
        )
    )
    row = ok(client.get(f"/api/leases/{lease}"), 200)
    year = TODAY.year
    report = client.get(
        "/api/reports/trust_report_income_statement",
        params={"arg": [str(pmc.owners[0].owner_id), f"{year}-01-01", f"{year}-12-31"]},
    )
    assert report.status_code == 200, report.text
    lines = {
        r["item"]: r["amount"]
        for r in csv.DictReader(io.StringIO(report.text))
        if r["property"] == "Property 1"
    }

    assert [(r["memo"], r["charge_type"]) for r in row["recurring"]] == [("Dog", "Pet rent")]
    assert {k: v for k, v in lines.items() if k.startswith("income:")} == {
        "income: rent": "1500.00",
        "income: recurring charges": "0.00",
        "income: late fees": "0.00",
        "income: NSF fees": "0.00",
        "income: other charges": "10.00",
        "income: Application fee": "50.00",
        "income: Pet rent": "25.00",
    }
    assert lines["total income"] == "1585.00"


def test_the_same_name_again_or_an_income_line_s_is_refused(client, staff):
    add_type(client, "Parking")

    again = send(client, "/api/setup/charge-types", name="PARKING")
    line = send(client, "/api/setup/charge-types", name="Late fees")

    assert (again.status_code, again.json()["detail"]) == (
        409,
        "the company has a charge type of that name already",
    )
    assert (line.status_code, line.json()["detail"]) == (
        422,
        "Late fees is a line of the income statement already",
    )


@pytest.mark.parametrize("name", ["", "   ", "x" * 101])
def test_a_bad_name_is_refused(client, staff, name):
    assert send(client, "/api/setup/charge-types", name=name).status_code == 422


def test_a_type_is_a_fees_and_the_companys_own(client, conn, staff, lease):
    mine = add_type(client, "Cleaning")
    theirs = conn.execute(
        "INSERT INTO trust_charge_types (pmc_id, name) VALUES (%s, 'Cleaning') RETURNING id",
        (make_pmc(conn).pmc_id,),
    ).fetchone()[0]
    charge = {"amount": "20.00", "due_on": TODAY, "memo": "Move-out clean"}

    credit = send(
        client, f"/api/rent/leases/{lease}/charges", kind="credit", charge_type_id=mine, **charge
    )
    monthly_credit = send(
        client,
        f"/api/rent/leases/{lease}/recurring-charges",
        kind="credit",
        amount="5.00",
        memo="Discount",
        starts_on=TODAY,
        charge_type_id=mine,
    )
    other = send(
        client, f"/api/rent/leases/{lease}/charges", kind="fee", charge_type_id=theirs, **charge
    )
    unknown = send(
        client,
        f"/api/rent/leases/{lease}/recurring-charges",
        kind="fee",
        amount="5.00",
        memo="Parking",
        starts_on=TODAY,
        charge_type_id=uuid.uuid4(),
    )

    assert [a.status_code for a in (credit, monthly_credit, other, unknown)] == [422, 422, 404, 404]
    assert credit.json()["detail"] == "only a fee has a charge type"
    assert ok(client.get("/api/setup/charge-types"), 200) == [{"id": mine, "name": "Cleaning"}]
    (count,) = conn.execute(
        "SELECT count(*) FROM trust_charges WHERE lease_id = %s", (lease,)
    ).fetchone()
    assert count == 0


@pytest.mark.parametrize("caller", ["nobody", "owner", "tenant"])
def test_only_staff_reach_charge_types(client, conn, app_conn, pmc, caller):
    if caller == "owner":
        join_as_owner(client, app_conn, pmc)
    elif caller == "tenant":
        join_as_tenant(client, conn, app_conn, pmc)

    answers = [
        client.get("/api/setup/charge-types"),
        send(client, "/api/setup/charge-types", name="Parking"),
    ]

    assert [a.status_code for a in answers] == [401 if caller == "nobody" else 403] * 2
