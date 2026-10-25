"""A lease's renewal plan through the API (src/pms/api/renewals.py; ADR 0010 phase 3, part L): the
Renew lease panel's starting terms, who signs, and the written notice the property's state asks
before the new rent (src/pms/rent_notice.py), for the session's PMC only. Every address here is
synthetic."""

from datetime import UTC, date, datetime, timedelta

import pytest
from helpers import add_tenant, add_unit, make_pmc, open_lease
from people import join_as_owner, join_as_staff, join_as_tenant

TODAY = datetime.now(UTC).date()
DAY = timedelta(days=1)


@pytest.fixture
def staff(client, conn, app_conn, pmc):
    return join_as_staff(client, conn, app_conn, pmc)


def first_of_next_month(day: date) -> date:
    return (day.replace(day=1) + 32 * DAY).replace(day=1)


def first_of_last_months(months: int) -> date:
    day = TODAY.replace(day=1)
    for _ in range(months):
        day = (day - DAY).replace(day=1)
    return day


def in_state(conn, pmc, state):
    conn.execute(
        "INSERT INTO trust_property_addresses (pmc_id, property_id, line_1, city, state, zip)"
        " VALUES (%s, %s, '100 Synthetic Way', 'Demo City', %s, '00001')",
        (pmc.pmc_id, pmc.owners[0].property_id, state),
    )


def lease_of(conn, pmc, starts_on, ends_on, rent="1000.00", names=("Tenant A",)):
    prop = pmc.owners[0].property_id
    unit = add_unit(conn, pmc.pmc_id, prop, "Unit 1")
    people = [add_tenant(conn, pmc.pmc_id, prop, name) for name in names]
    return open_lease(conn, pmc.pmc_id, unit, starts_on, ends_on, rent, people), people


def plan(client, lease, **asked):
    answer = client.get(f"/api/leasing/renewals/{lease}/plan", params=asked)
    assert answer.status_code == 200, answer.text
    return answer.json()


def test_a_plan_starts_the_day_after_the_lease_ends_at_its_rent(client, conn, staff, pmc):
    lease, (a, b) = lease_of(
        conn, pmc, TODAY - 300 * DAY, TODAY + 20 * DAY, names=("Tenant B", "Tenant A")
    )
    conn.execute(
        "INSERT INTO trust_contacts (pmc_id, tenant_id, email) VALUES (%s, %s, %s)",
        (pmc.pmc_id, a, "tenant.b@example.com"),
    )

    found = plan(client, lease)

    assert found["lease_id"] == str(lease)
    assert (found["unit"], found["monthly_rent"], found["new_rent"]) == (
        "Unit 1",
        "1000.00",
        "1000.00",
    )
    assert found["new_starts_on"] == str(TODAY + 21 * DAY)
    assert found["ends_on"] == str(TODAY + 20 * DAY)
    # By name; a tenant with no email on file is listed without one, so the panel can say so.
    assert found["signers"] == [
        {"tenant_id": str(b), "name": "Tenant A", "email": None},
        {"tenant_id": str(a), "name": "Tenant B", "email": "tenant.b@example.com"},
    ]
    # No address on file: no state, so no notice.
    assert found["notice"] is None


def test_a_month_to_month_lease_renews_from_the_first_of_next_month(client, conn, staff, pmc):
    lease, _ = lease_of(conn, pmc, TODAY - 100 * DAY, None)

    assert plan(client, lease)["new_starts_on"] == str(first_of_next_month(TODAY))


def test_california_warns_when_an_increase_over_ten_percent_starts_inside_90_days(
    client, conn, staff, pmc
):
    in_state(conn, pmc, "CA")
    lease, _ = lease_of(conn, pmc, TODAY - 300 * DAY, TODAY + 20 * DAY)

    notice = plan(client, lease, monthly_rent="1150.00")["notice"]

    starts = TODAY + 21 * DAY
    earliest = TODAY + 90 * DAY
    assert notice == {
        "state": "CA",
        "days": 90,
        "law": "California Civil Code §827",
        "increase_percent": "15.0",
        "earliest": str(earliest),
        "warning": (
            "California asks 90 days' written notice before a rent increase of 15.0% (California"
            f" Civil Code §827). This new rent starts in {(starts - TODAY).days} days. To give that"
            f" notice, start it on or after {earliest:%m/%d/%Y}."
        ),
    }
    # Started late enough, the notice stands and nothing is said.
    later = plan(client, lease, monthly_rent="1150.00", starts_on=str(earliest))["notice"]
    assert (later["days"], later["warning"]) == (90, None)


def test_california_asks_30_days_up_to_ten_percent(client, conn, staff, pmc):
    in_state(conn, pmc, "CA")
    lease, _ = lease_of(conn, pmc, TODAY - 300 * DAY, TODAY + 20 * DAY)

    notice = plan(client, lease, monthly_rent="1100.00")["notice"]

    assert (notice["days"], notice["increase_percent"]) == (30, "10.0")
    assert notice["warning"].startswith("California asks 30 days' written notice")
    assert plan(client, lease, monthly_rent="1000.00")["notice"] is None


def test_an_earlier_increase_in_the_year_counts_toward_californias_ten_percent(
    client, conn, staff, pmc
):
    in_state(conn, pmc, "CA")
    lease, _ = lease_of(conn, pmc, TODAY - 400 * DAY, None)
    raised = first_of_last_months(2)
    conn.execute(
        "INSERT INTO trust_rent_changes (pmc_id, lease_id, effective_on, monthly_rent,"
        " notice_given_on, served_by) VALUES (%s, %s, %s, 1060, %s, 'mail')",
        (pmc.pmc_id, lease, raised, raised - 40 * DAY),
    )

    found = plan(client, lease, monthly_rent="1120.00")

    # 1,060 to 1,120 is 5.7%; from the 1,000 charged earlier in the 12 months it is 12%.
    assert found["monthly_rent"] == "1060.00"
    assert (found["notice"]["days"], found["notice"]["increase_percent"]) == (90, "12.0")


def test_new_york_asks_more_notice_of_a_tenant_there_two_years(client, conn, staff, pmc):
    in_state(conn, pmc, "NY")
    long_ago, _ = lease_of(conn, pmc, TODAY - 800 * DAY, None)

    assert plan(client, long_ago, monthly_rent="1060.00")["notice"]["days"] == 90
    # Under 5%: New York asks nothing.
    assert plan(client, long_ago, monthly_rent="1040.00")["notice"] is None


def test_new_york_asks_30_days_in_the_first_year(client, conn, staff, pmc):
    in_state(conn, pmc, "NY")
    lately, _ = lease_of(conn, pmc, TODAY - 100 * DAY, None)

    assert plan(client, lately, monthly_rent="1060.00")["notice"]["days"] == 30


def test_a_state_without_a_recorded_rule_gets_no_notice(client, conn, staff, pmc):
    in_state(conn, pmc, "TX")
    lease, _ = lease_of(conn, pmc, TODAY - 300 * DAY, TODAY + 20 * DAY)

    assert plan(client, lease, monthly_rent="2000.00")["notice"] is None


def test_another_companys_lease_is_not_found(client, conn, staff, pmc):
    other = make_pmc(conn)
    prop = other.owners[0].property_id
    unit = add_unit(conn, other.pmc_id, prop, "Unit 1")
    tenant = add_tenant(conn, other.pmc_id, prop, "Tenant A")
    theirs = open_lease(conn, other.pmc_id, unit, TODAY - 300 * DAY, None, "1000.00", [tenant])

    assert client.get(f"/api/leasing/renewals/{theirs}/plan").status_code == 404


@pytest.mark.parametrize(
    "asked",
    [
        {"monthly_rent": "abc"},
        {"monthly_rent": "0"},
        {"monthly_rent": "10.001"},
        {"starts_on": "2026-02-30"},
    ],
)
def test_bad_terms_are_refused(client, conn, staff, pmc, asked):
    lease, _ = lease_of(conn, pmc, TODAY - 300 * DAY, None)

    assert client.get(f"/api/leasing/renewals/{lease}/plan", params=asked).status_code == 422


@pytest.mark.parametrize("caller", ["nobody", "owner", "tenant"])
def test_only_staff_read_a_renewal_plan(client, conn, app_conn, pmc, caller):
    lease, _ = lease_of(conn, pmc, TODAY - 300 * DAY, None)
    if caller == "owner":
        join_as_owner(client, app_conn, pmc)
    elif caller == "tenant":
        join_as_tenant(client, conn, app_conn, pmc)

    answer = client.get(f"/api/leasing/renewals/{lease}/plan")

    assert answer.status_code == (401 if caller == "nobody" else 403)
