"""The rent roll through the API (src/pms/api/rentals.py, src/pms/api/rent.py, ADR 0006): each
lease with its days left, rent, the deposits held and its balance, found by where it stands; a
payment recorded with how it came; and an admin's Run rent now (what posts by itself every night,
pms.nightly), recorded with the nightly runs. Always the session's PMC."""

import json
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from helpers import add_late_fee_policy, add_tenant, add_unit, charge, make_pmc, open_lease
from people import join_as_staff

from pms import nightly

TODAY = datetime.now(UTC).date()
DAY = timedelta(days=1)
FIRST = TODAY.replace(day=1)


@pytest.fixture
def staff(client, conn, app_conn, pmc):
    return join_as_staff(client, conn, app_conn, pmc)


def ok(answer, status=200):
    assert answer.status_code == status, answer.text
    return answer.json()


def post(client, path, **body):
    return client.post(path, json=json.loads(json.dumps(body, default=str)))


def lease_at(conn, pmc, unit, starts, ends=None, rent="1200.00", tenant="Tenant A"):
    prop = pmc.owners[0].property_id
    unit_id = add_unit(conn, pmc.pmc_id, prop, unit)
    person = add_tenant(conn, pmc.pmc_id, prop, tenant)
    return open_lease(conn, pmc.pmc_id, unit_id, starts, ends, rent, [person]), person


def test_the_rent_roll_shows_days_left_rent_deposit_and_balance(client, conn, staff, pmc):
    lease, tenant = lease_at(conn, pmc, "Unit 1", TODAY - 30 * DAY, TODAY + 45 * DAY)
    charge(conn, pmc.pmc_id, lease, TODAY - 3 * DAY, "1200.00")
    ok(
        post(
            client,
            "/api/rent/deposits",
            request_id=uuid.uuid4(),
            tenant_id=tenant,
            amount="1200.00",
            received_on=TODAY,
        ),
        201,
    )

    [row] = ok(client.get("/api/leases"))["items"]

    assert (row["status"], row["days_left"], row["monthly_rent"], row["deposit"], row["owed"]) == (
        "current",
        45,
        "1200.00",
        "1200.00",
        "1200.00",
    )


def test_the_rent_roll_shows_leases_to_come_ended_owing_or_all(client, conn, staff, pmc):
    lease_at(conn, pmc, "Now", TODAY - 30 * DAY, tenant="Tenant N")
    lease_at(conn, pmc, "Later", TODAY + 10 * DAY, tenant="Tenant L")
    ended, _ = lease_at(conn, pmc, "Before", TODAY - 90 * DAY, TODAY - DAY, tenant="Tenant B")
    charge(conn, pmc.pmc_id, ended, TODAY - 60 * DAY, "50.00")

    def shown(show):
        return [
            (r["unit"]["name"], r["status"], r["days_left"])
            for r in ok(client.get(f"/api/leases?show={show}"))["items"]
        ]

    assert shown("current") == [("Now", "current", None)]  # no end date, so no days left
    assert shown("future") == [("Later", "future", None)]
    assert shown("past") == [("Before", "ended", None)]
    assert shown("owing") == [("Before", "ended", None)]
    assert len(shown("all")) == 3
    assert client.get("/api/leases?show=gone").status_code == 422


def test_a_payment_records_how_it_came(client, conn, staff, pmc):
    lease, _ = lease_at(conn, pmc, "Unit 1", TODAY - 30 * DAY)
    charge(conn, pmc.pmc_id, lease, TODAY - 3 * DAY, "1200.00")
    for method, reference in (
        ("EFT", None),
        ("check", "Check 7"),
        ("cashier's check", "Cashier's check 8"),
        ("money order", None),
        ("cash", None),
    ):
        ok(
            post(
                client,
                f"/api/rent/leases/{lease}/payments",
                request_id=uuid.uuid4(),
                amount="100.00",
                received_on=TODAY,
                method=method,
                reference=reference,
            ),
            201,
        )

    paid = ok(client.get(f"/api/leases/{lease}"))["payments"]
    ledger = ok(client.get(f"/api/leases/{lease}/ledger"))["lines"]

    assert sorted((p["method"], p["reference"]) for p in paid) == [
        ("EFT", None),
        ("cash", None),
        ("cashier's check", "Cashier's check 8"),
        ("check", "Check 7"),
        ("money order", None),
    ]
    # The ledger's memo is its check or reference, or else how it came.
    assert sorted(line["memo"] for line in ledger if line["kind"] == "payment") == [
        "Cashier's check 8",
        "Check 7",
        "EFT",
        "cash",
        "money order",
    ]
    refused = post(
        client,
        f"/api/rent/leases/{lease}/payments",
        request_id=uuid.uuid4(),
        amount="1.00",
        received_on=TODAY,
        method="bitcoin",
    )
    assert refused.status_code == 422


def test_an_admin_runs_rent_by_hand_and_it_is_listed_with_the_nightly_runs(
    client, conn, app_conn, staff, pmc
):
    lease_at(conn, pmc, "Unit 1", FIRST - 60 * DAY)
    add_late_fee_policy(conn, pmc.pmc_id, pmc.owners[0].property_id, FIRST - 60 * DAY, 5, "40.00")
    earlier = (FIRST - DAY).replace(day=1)
    nightly.run_pmc(app_conn, pmc.pmc_id, FIRST - DAY, "http://localhost:8000")

    by_hand = ok(post(client, "/api/rent/run", due_on=FIRST, late_fees_as_of=TODAY))
    again = ok(post(client, "/api/rent/run", due_on=FIRST, late_fees_as_of=TODAY))
    runs = ok(client.get("/api/rent/runs"))

    assert by_hand["rents"] == 1 and by_hand["recurring"] == 0
    assert again == {
        "rents": 0,
        "recurring": 0,
        "late_fees": 0,
        "emails": 0,
        "authority_late_fees": 0,
    }
    assert [(r["due_on"], r["by"]) for r in runs] == [
        (str(FIRST), "Staff 1"),
        (str(FIRST), "Staff 1"),
        (str(earlier), None),  # the nightly run names nobody
    ]
    assert runs[2]["rents"] == 1


def test_only_an_admin_runs_rent_by_hand(client, conn, app_conn, pmc):
    join_as_staff(client, conn, app_conn, pmc, roles=("approve", "disburse"))
    answer = post(client, "/api/rent/run", due_on=FIRST, late_fees_as_of=TODAY)

    assert answer.status_code == 403
    assert answer.json()["detail"] == "Staff 1 may not run rent by hand (the admin role)"
    assert conn.execute(
        "SELECT count(*) FROM trust_rent_runs WHERE pmc_id = %s", (pmc.pmc_id,)
    ).fetchone() == (0,)


def test_another_companys_runs_are_not_listed(client, conn, app_conn, staff):
    other = make_pmc(conn)
    nightly.run_pmc(app_conn, other.pmc_id, TODAY, "http://localhost:8000")
    assert ok(client.get("/api/rent/runs")) == []


@pytest.mark.parametrize(
    "body",
    [
        {"late_fees_as_of": str(TODAY)},  # no due date
        {"due_on": "the first", "late_fees_as_of": str(TODAY)},
        {"due_on": str(FIRST)},  # no day for late fees
    ],
)
def test_a_run_takes_its_days(client, staff, body):
    assert client.post("/api/rent/run", json=body).status_code == 422
