"""What a tenant's lease says is coming and is held (src/pms/api/tenant.py, the founder's review
of 11/03, items 10 and 11). The next charge: the first due day after today whose month's rent is
not charged yet, with the rent in force then (a rent change to come included) and each monthly fee
or credit, for the days the lease is in effect, as the rent run will charge them. The security
deposit held for the lease's tenants, or nothing when none is held. Every name here is
synthetic."""

import uuid
from datetime import UTC, date, datetime, timedelta

import pytest
from helpers import add_recurring_charge, add_unit, charge, open_lease, stop_recurring_charge
from people import join_as_staff, join_as_tenant, tenant_of

TODAY = datetime.now(UTC).date()
START = TODAY - timedelta(days=60)


def first_of(months_ahead: int, day: date = TODAY) -> date:
    month = day.month - 1 + months_ahead
    return date(day.year + month // 12, month % 12 + 1, 1)


def ok(answer, status=200):
    assert answer.status_code == status, answer.text
    return answer.json()


@pytest.fixture
def lease(conn, pmc):
    unit = add_unit(conn, pmc.pmc_id, pmc.owners[0].property_id, name="Unit 4")
    return open_lease(conn, pmc.pmc_id, unit, START, None, "2250.00", [tenant_of(conn, pmc)])


def next_charge(client, lease):
    [mine] = [x for x in ok(client.get("/api/tenant/leases")) if x["lease_id"] == str(lease)]
    return mine["next_charge"]


def test_the_next_charge_is_next_months_rent_with_its_monthly_fees_and_credits(
    client, conn, app_conn, pmc, lease
):
    add_recurring_charge(conn, pmc.pmc_id, lease, "25.00", START, memo="Pet rent")
    add_recurring_charge(
        conn, pmc.pmc_id, lease, "10.00", START, memo="Autopay credit", kind="credit"
    )
    parking = add_recurring_charge(conn, pmc.pmc_id, lease, "40.00", START, memo="Parking")
    stop_recurring_charge(conn, pmc.pmc_id, parking, first_of(1))  # not charged from next month
    join_as_tenant(client, conn, app_conn, pmc)

    assert next_charge(client, lease) == {
        "due_on": str(first_of(1)),
        "total": "2265.00",
        "lines": [
            {"what": "Rent", "kind": "rent", "amount": "2250.00"},
            {"what": "Autopay credit", "kind": "credit", "amount": "10.00"},
            {"what": "Pet rent", "kind": "fee", "amount": "25.00"},
        ],
    }


def test_a_rent_change_to_come_is_the_rent_of_the_next_charge(client, conn, app_conn, pmc, lease):
    join_as_staff(client, conn, app_conn, pmc)
    changed = client.post(
        f"/api/rent/leases/{lease}/rent-changes",
        json={
            "effective_on": str(first_of(1)),
            "monthly_rent": "2300.00",
            "notice_given_on": str(first_of(0) - timedelta(days=1)),
            "served_by": "in person",
        },
    )
    assert changed.status_code == 201, changed.text
    join_as_tenant(client, conn, app_conn, pmc)

    said = next_charge(client, lease)

    assert (said["due_on"], said["total"]) == (str(first_of(1)), "2300.00")


def test_a_month_charged_already_is_not_the_next_charge(client, conn, app_conn, pmc, lease):
    charge(conn, pmc.pmc_id, lease, first_of(1), "2250.00", memo="Rent")  # charged ahead
    join_as_tenant(client, conn, app_conn, pmc)

    assert next_charge(client, lease)["due_on"] == str(first_of(2))


def test_a_lease_ending_mid_month_is_charged_its_days(client, conn, app_conn, pmc):
    unit = add_unit(conn, pmc.pmc_id, pmc.owners[0].property_id, name="Unit 5")
    ends = first_of(1) + timedelta(days=14)  # the 15th of next month
    ending = open_lease(conn, pmc.pmc_id, unit, START, ends, "3000.00", [tenant_of(conn, pmc)])
    days = (first_of(2) - first_of(1)).days
    join_as_tenant(client, conn, app_conn, pmc)

    said = next_charge(client, ending)

    assert said["due_on"] == str(first_of(1))
    assert said["total"] == f"{round(3000 * 15 / days, 2):.2f}"


def test_a_lease_ending_before_its_next_due_day_has_no_next_charge(client, conn, app_conn, pmc):
    unit = add_unit(conn, pmc.pmc_id, pmc.owners[0].property_id, name="Unit 6")
    ending = open_lease(
        conn,
        pmc.pmc_id,
        unit,
        START,
        first_of(1) - timedelta(days=1),
        "900.00",
        [tenant_of(conn, pmc)],
    )
    join_as_tenant(client, conn, app_conn, pmc)

    assert next_charge(client, ending) is None


# --- the security deposit held (item 11) --------------------------------------------------------


def deposit(client, tenant, amount, kind="security"):
    answer = client.post(
        "/api/rent/deposits",
        json={
            "request_id": str(uuid.uuid4()),
            "tenant_id": str(tenant),
            "amount": amount,
            "received_on": str(TODAY),
            "kind": kind,
        },
    )
    assert answer.status_code == 201, answer.text


def held(client, lease):
    [mine] = [x for x in ok(client.get("/api/tenant/leases")) if x["lease_id"] == str(lease)]
    return mine["deposit_held"]


def test_the_security_deposit_held_for_the_lease_shows_and_a_key_deposit_is_its_own(
    client, conn, app_conn, pmc, lease
):
    join_as_staff(client, conn, app_conn, pmc)
    deposit(client, tenant_of(conn, pmc), "2250.00")
    deposit(client, tenant_of(conn, pmc), "50.00", kind="key")
    join_as_tenant(client, conn, app_conn, pmc)

    assert held(client, lease) == "2250.00"


def test_no_deposit_held_shows_nothing(client, conn, app_conn, pmc, lease):
    join_as_tenant(client, conn, app_conn, pmc)

    assert held(client, lease) is None
