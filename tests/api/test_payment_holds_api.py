"""Payment holds from the lease page (src/pms/api/rent.py; the holds of migration 0015): staff
put a lease on hold when an eviction is filed, release it from a day, and accept one payment past
it, the PMC's choice, naming who did. Every name here is synthetic."""

import json
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from helpers import add_unit, balance, charge, make_pmc, open_lease
from people import join_as_owner, join_as_staff, join_as_tenant, tenant_of

TODAY = datetime.now(UTC).date()
DAY = timedelta(days=1)
START = TODAY - timedelta(days=60)


@pytest.fixture
def staff(client, conn, app_conn, pmc):
    return join_as_staff(client, conn, app_conn, pmc, name="Clerk 1")


@pytest.fixture
def lease(conn, pmc):
    unit = add_unit(conn, pmc.pmc_id, pmc.owners[0].property_id)
    made = open_lease(conn, pmc.pmc_id, unit, START, None, "1500.00", [tenant_of(conn, pmc)])
    charge(conn, pmc.pmc_id, made, START, "1500.00")
    return made


def ok(answer, status=200):
    assert answer.status_code == status, answer.text
    return answer.json()


def post(client, path, **body):
    return client.post(path, json=json.loads(json.dumps(body, default=str)))


def place(client, lease, starts_on=START + DAY, reason="Eviction filed"):
    return post(client, f"/api/rent/leases/{lease}/holds", starts_on=starts_on, reason=reason)


def pay(client, lease, amount="500.00", **more):
    return post(
        client,
        f"/api/rent/leases/{lease}/payments",
        request_id=uuid.uuid4(),
        amount=amount,
        received_on=TODAY,
        **more,
    )


def holds(client, lease):
    return ok(client.get(f"/api/leases/{lease}"))["holds"]


def test_staff_put_a_lease_on_hold_and_release_it(client, conn, staff, pmc, lease):
    held = ok(place(client, lease), 201)
    assert (held["starts_on"], held["reason"], held["ends_on"], held["in_force"]) == (
        str(START + DAY),
        "Eviction filed",
        None,
        True,
    )
    shown = ok(client.get(f"/api/leases/{lease}"))
    assert shown["on_hold"] is True and shown["holds"] == [held]
    refused = pay(client, lease)
    assert refused.status_code == 409 and "payment hold" in refused.json()["detail"]
    # One hold at a time.
    again = place(client, lease)
    assert again.status_code == 409 and "release it first" in again.json()["detail"]

    released = ok(post(client, f"/api/rent/holds/{held['id']}/release", ends_on=TODAY), 201)
    assert (released["ends_on"], released["released_by"], released["in_force"]) == (
        str(TODAY),
        "Clerk 1",
        False,
    )
    assert ok(client.get(f"/api/leases/{lease}"))["on_hold"] is False
    ok(pay(client, lease), 201)
    # Each write names the staff member in the audit log.
    assert conn.execute(
        "SELECT table_name, staff_id FROM trust_audit_log WHERE pmc_id = %s"
        " AND table_name LIKE 'trust_payment_hold%%' ORDER BY id",
        (pmc.pmc_id,),
    ).fetchall() == [
        ("trust_payment_holds", staff.person_id),
        ("trust_payment_hold_releases", staff.person_id),
    ]
    # A new hold once the last one is released.
    ok(place(client, lease, starts_on=TODAY), 201)
    assert [h["in_force"] for h in holds(client, lease)] == [True, False]


def test_staff_accept_one_payment_past_the_hold(client, conn, staff, pmc, lease):
    held = ok(place(client, lease), 201)
    paid = ok(pay(client, lease, past_hold=True), 201)

    assert paid["to_owner"] == "500.00"
    assert balance(conn, pmc.owners[0].account) == Decimal("500.00")
    assert conn.execute(
        "SELECT hold_id, transfer_id, allowed_by FROM trust_payment_hold_allowances"
        " WHERE pmc_id = %s",
        (pmc.pmc_id,),
    ).fetchall() == [(uuid.UUID(held["id"]), paid["transfers"][0], "Clerk 1")]
    assert holds(client, lease)[0]["allowed"] == 1
    # The next one is refused again unless accepted too.
    assert pay(client, lease).status_code == 409


def test_a_payment_with_no_hold_needs_no_acceptance(client, conn, staff, pmc, lease):
    ok(pay(client, lease, past_hold=True), 201)
    assert conn.execute(
        "SELECT count(*) FROM trust_payment_hold_allowances WHERE pmc_id = %s", (pmc.pmc_id,)
    ).fetchone() == (0,)


def test_a_release_ends_after_the_hold_starts_and_comes_once(client, staff, pmc, lease):
    held = ok(place(client, lease, starts_on=TODAY), 201)
    path = f"/api/rent/holds/{held['id']}/release"
    early = post(client, path, ends_on=TODAY)
    assert early.status_code == 422 and "after the day it started" in early.json()["detail"]
    ok(post(client, path, ends_on=TODAY + DAY), 201)
    assert post(client, path, ends_on=TODAY + 2 * DAY).status_code == 409
    # Released from tomorrow: still in force today.
    assert holds(client, lease)[0]["in_force"] is True


def test_another_companys_lease_or_hold_is_not_found(client, conn, app_conn, staff, pmc):
    other = make_pmc(conn)
    unit = add_unit(conn, other.pmc_id, other.owners[0].property_id)
    theirs = open_lease(conn, other.pmc_id, unit, START, None, "900.00", [tenant_of(conn, other)])
    assert place(client, theirs).status_code == 404
    hold = conn.execute(
        "INSERT INTO trust_payment_holds (pmc_id, lease_id, starts_on, reason)"
        " VALUES (%s, %s, %s, 'Eviction filed') RETURNING id",
        (other.pmc_id, theirs, START),
    ).fetchone()[0]
    assert post(client, f"/api/rent/holds/{hold}/release", ends_on=TODAY).status_code == 404
    assert post(client, f"/api/rent/holds/{uuid.uuid4()}/release", ends_on=TODAY).status_code == 404


@pytest.mark.parametrize(
    "body",
    [
        {"starts_on": str(TODAY + DAY), "reason": "Eviction filed"},
        {"starts_on": str(TODAY), "reason": " "},
        {"starts_on": str(TODAY), "reason": "x" * 501},
        {"starts_on": "soon", "reason": "Eviction filed"},
        {"reason": "Eviction filed"},
    ],
)
def test_bad_holds_are_refused(client, staff, lease, body):
    assert client.post(f"/api/rent/leases/{lease}/holds", json=body).status_code == 422


@pytest.mark.parametrize("caller", ["nobody", "owner", "tenant"])
def test_only_staff_place_or_release_holds(client, conn, app_conn, pmc, lease, caller):
    if caller == "owner":
        join_as_owner(client, app_conn, pmc)
    elif caller == "tenant":
        join_as_tenant(client, conn, app_conn, pmc)
    status = 401 if caller == "nobody" else 403
    assert place(client, lease).status_code == status
    hold = conn.execute(
        "INSERT INTO trust_payment_holds (pmc_id, lease_id, starts_on, reason)"
        " VALUES (%s, %s, %s, 'Eviction filed') RETURNING id",
        (pmc.pmc_id, lease, START),
    ).fetchone()[0]
    assert post(client, f"/api/rent/holds/{hold}/release", ends_on=TODAY).status_code == status
