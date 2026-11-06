"""A lease's renewal plan through the API (src/pms/api/renewals.py; ADR 0010 phase 3, part L): the
Renew lease panel's starting terms, who signs, and the written notice the property's state asks
before the new rent (src/pms/rent_notice.py), for the session's PMC only. Every address here is
synthetic."""

from datetime import UTC, date, datetime, timedelta

import pytest
from helpers import add_tenant, add_unit, make_pmc, open_lease
from people import join_as_owner, join_as_staff, join_as_tenant


def on_this_day() -> None:
    """Today's dates (UTC), worked out again before each test (tests/conftest.py,
    pytest_runtest_setup), so a run past midnight compares them with the database's."""
    global TODAY
    TODAY = datetime.now(UTC).date()


on_this_day()
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


def years_on(day: date, years: int) -> date:
    """The same day `years` later (February 29 to February 28)."""
    try:
        return day.replace(year=day.year + years)
    except ValueError:
        return day.replace(year=day.year + years, day=28)


def test_new_york_counts_a_twelve_month_lease_as_a_year(client, conn, staff, pmc):
    """docs/edge-cases.md D-067: a 12-month lease is 365 days when no February 29 falls in it,
    which a count of 1461 days every four years read as no year at all (30 days asked);
    RPL 226-c asks 60 for a lease term of at least a year."""
    in_state(conn, pmc, "NY")
    starts = first_of_last_months(6)
    year_lease, _ = lease_of(conn, pmc, starts, years_on(starts, 1) - DAY)

    assert plan(client, year_lease, monthly_rent="1060.00")["notice"]["days"] == 60


def test_new_york_counts_a_tenant_there_exactly_two_years_as_two(client, conn, staff, pmc):
    """docs/edge-cases.md D-068: two years are 730 days without a February 29, which the same
    count read as one (60 days asked instead of 90)."""
    in_state(conn, pmc, "NY")
    renews = first_of_next_month(TODAY)
    two_years, _ = lease_of(conn, pmc, years_on(renews, -2), None)

    assert plan(client, two_years, monthly_rent="1060.00")["notice"]["days"] == 90
    # A day short of two years is one.
    one_and_more, _ = lease_of(conn, pmc, years_on(renews, -2) + DAY, None)
    assert plan(client, one_and_more, monthly_rent="1060.00")["notice"]["days"] == 60


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


# --- a rent change's notice (docs/edge-cases.md D-065, D-066) ----------------------------------


def change_notice(client, lease, **asked):
    return client.get(f"/api/leasing/renewals/{lease}/rent-change-notice", params=asked)


def test_a_rent_change_on_the_rent_page_is_warned_of_a_states_notice_from_the_day_given(
    client, conn, staff, pmc
):
    # A month-to-month lease in California raised 5% from March 1 with notice given February 1:
    # the database's full month is only 28 days in February, short of the state's 30 (D-065), and
    # the rent page ran no state check at all (D-066).
    in_state(conn, pmc, "CA")
    lease, _ = lease_of(conn, pmc, date(2025, 1, 1), None)

    answer = change_notice(
        client,
        lease,
        effective_on="2026-03-01",
        monthly_rent="1050.00",
        notice_given_on="2026-02-01",
    )

    assert answer.status_code == 200, answer.text
    assert answer.json() == {
        "state": "CA",
        "days": 30,
        "law": "California Civil Code §827",
        "increase_percent": "5.0",
        "earliest": "2026-03-03",
        "warning": (
            "California asks 30 days' written notice before a rent increase of 5.0% (California"
            " Civil Code §827). This new rent starts in 28 days. To give that notice, start it on"
            " or after 03/03/2026."
        ),
    }
    # Given in January, it is notice enough.
    enough = change_notice(
        client,
        lease,
        effective_on="2026-03-01",
        monthly_rent="1050.00",
        notice_given_on="2026-01-30",
    ).json()
    assert enough["warning"] is None


def test_a_rent_change_with_no_states_rule_or_no_increase_is_warned_of_nothing(
    client, conn, staff, pmc
):
    lease, _ = lease_of(conn, pmc, date(2025, 1, 1), None)
    asked = {"effective_on": "2026-03-01", "notice_given_on": "2026-02-01"}

    # No address on file: no state.
    none = change_notice(client, lease, monthly_rent="1500.00", **asked)
    in_state(conn, pmc, "NV")
    lower = change_notice(client, lease, monthly_rent="900.00", **asked)
    nevada = change_notice(client, lease, monthly_rent="1100.00", **asked)

    assert (none.status_code, none.json()) == (200, None)
    assert lower.json() is None
    assert nevada.json()["days"] == 60


def test_only_staff_read_a_rent_changes_notice_and_only_their_own_leases(
    client, other_client, conn, app_conn, pmc
):
    lease, _ = lease_of(conn, pmc, date(2025, 1, 1), None)
    asked = {
        "effective_on": "2026-03-01",
        "monthly_rent": "1100.00",
        "notice_given_on": "2026-02-01",
    }

    assert change_notice(client, lease, **asked).status_code == 401
    join_as_tenant(other_client, conn, app_conn, pmc)
    assert change_notice(other_client, lease, **asked).status_code == 403
    join_as_staff(client, conn, app_conn, pmc)
    theirs = make_pmc(conn)
    elsewhere, _ = lease_of(conn, theirs, date(2025, 1, 1), None)
    assert change_notice(client, elsewhere, **asked).status_code == 404


def test_a_rent_changes_notice_asks_for_what_it_needs(client, conn, staff, pmc):
    lease, _ = lease_of(conn, pmc, date(2025, 1, 1), None)

    assert change_notice(client, lease, effective_on="2026-03-01").status_code == 422
    assert (
        change_notice(
            client, lease, effective_on="soon", monthly_rent="1.00", notice_given_on="2026-02-01"
        ).status_code
        == 422
    )
    assert (
        change_notice(
            client,
            lease,
            effective_on="2026-03-01",
            monthly_rent="-5",
            notice_given_on="2026-02-01",
        ).status_code
        == 422
    )
