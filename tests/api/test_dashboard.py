"""The Dashboard's figures through the API (src/pms/api/dashboard.py; ADR 0010 phase 3): only
numbers that change, each beside the same day a month before, each counted by the report that
counts it everywhere else, for the session's PMC only."""

from datetime import UTC, date, datetime, time, timedelta

import pytest
from helpers import (
    add_tenant,
    add_unit,
    apply_payment,
    charge,
    end_lease,
    import_statement,
    make_pmc,
    open_lease,
    transfer_batch,
)
from people import join_as_owner, join_as_staff, join_as_tenant

from pms.api.dashboard import month_before

TODAY = datetime.now(UTC).date()
THEN = month_before(TODAY)
DAY = timedelta(days=1)
START = TODAY - timedelta(days=120)


def noon(day: date) -> datetime:
    return datetime.combine(day, time(12), tzinfo=UTC)


def first_of(day: date, months_back: int) -> date:
    for _ in range(months_back):
        day = day.replace(day=1) - DAY
    return day.replace(day=1)


@pytest.fixture
def staff(client, conn, app_conn, pmc):
    return join_as_staff(client, conn, app_conn, pmc)


def unit_of(conn, pmc, name):
    """A unit the company has managed since START."""
    unit = add_unit(conn, pmc.pmc_id, pmc.owners[0].property_id, name)
    conn.execute("UPDATE trust_units SET created_at = %s WHERE id = %s", (noon(START), unit))
    return unit


def lease_of(conn, pmc, name, ends_on=None, tenants=1):
    prop = pmc.owners[0].property_id
    unit = unit_of(conn, pmc, name)
    people = [add_tenant(conn, pmc.pmc_id, prop, f"Tenant {n} of {name}") for n in range(tenants)]
    lease = open_lease(conn, pmc.pmc_id, unit, START, None, "1500.00", people)
    if ends_on is not None:
        end_lease(conn, pmc.pmc_id, lease, ends_on)
    return unit, lease


def pay(conn, pmc, charge_id, amount, at):
    (paid,) = transfer_batch(conn, [(pmc.operating_cash, pmc.owners[0].account, amount)], at)
    apply_payment(conn, pmc.pmc_id, charge_id, paid, amount)


def test_month_before_is_the_same_day_or_the_months_last():
    assert month_before(date(2026, 10, 25)) == date(2026, 9, 25)
    assert month_before(date(2026, 3, 31)) == date(2026, 2, 28)
    assert month_before(date(2026, 1, 15)) == date(2025, 12, 15)


def test_each_figure_now_and_a_month_before(client, conn, staff, pmc):
    # Unit 1, two tenants: last month's rent paid in full that day; this month's 1,000 of 1,500.
    _, owing = lease_of(conn, pmc, "Unit 1", tenants=2)
    pay(
        conn,
        pmc,
        charge(conn, pmc.pmc_id, owing, first_of(THEN, 0), "1500.00"),
        "1500.00",
        noon(first_of(THEN, 0)),
    )
    pay(
        conn,
        pmc,
        charge(conn, pmc.pmc_id, owing, first_of(TODAY, 0), "1500.00"),
        "1000.00",
        datetime.now(UTC),
    )
    # Unit 2 ends in 45 days (within 60 now, not a month before); Unit 3 in 10 (both).
    lease_of(conn, pmc, "Unit 2", ends_on=TODAY + 45 * DAY)
    lease_of(conn, pmc, "Unit 3", ends_on=TODAY + 10 * DAY)
    # Unit 4's lease ended 5 days ago: vacant now, leased a month before. Unit 5 never leased.
    lease_of(conn, pmc, "Unit 4", ends_on=TODAY - 5 * DAY)
    unit_of(conn, pmc, "Unit 5")

    answer = client.get("/api/dashboard")

    assert answer.status_code == 200, answer.text
    figures = answer.json()
    assert (figures["on"], figures["month_ago"]) == (str(TODAY), str(THEN))
    # 3 of 5 units leased now, 4 of 5 then.
    assert figures["occupancy"] == {"percent": "60.0", "then_percent": "80.0"}
    assert figures["rent"] == {
        "collected": "1000.00",
        "due": "1500.00",
        "then_collected": "1500.00",
        "then_due": "1500.00",
    }
    # The delinquency report's total and the two tenants on the lease that owes it.
    assert figures["past_due"] == {
        "total": "500.00",
        "tenants": 2,
        "then_total": "0.00",
        "then_tenants": 0,
    }
    # A month before: Unit 3 and Unit 4 (which has ended since); Unit 2 was 75 days off.
    assert figures["leases_ending"] == {"within_60": 2, "then_within_60": 2}
    # Unit 4 vacant since the day after its lease ended (4 days), Unit 5 since it was added (120
    # days): 62 on average. A month before only Unit 5 was vacant.
    assert figures["vacant"] == {
        "units": 2,
        "average_days": 62,
        "then_units": 1,
        "then_average_days": (THEN - START).days,
    }
    assert figures["next_draw"] == {"on": None, "total": None, "last_total": None}
    assert figures["unreconciled"] == {"accounts": 0, "then_accounts": 0}


def test_an_archived_unit_counts_in_no_occupancy(client, conn, staff, pmc):
    lease_of(conn, pmc, "Unit 1")
    gone = unit_of(conn, pmc, "Unit 2")
    assert client.get("/api/dashboard").json()["occupancy"]["percent"] == "50.0"

    conn.execute(
        "INSERT INTO trust_archivals (pmc_id, unit_id, archived) VALUES (%s, %s, true)",
        (pmc.pmc_id, gone),
    )

    assert client.get("/api/dashboard").json()["occupancy"]["percent"] == "100.0"


def test_the_next_draw_is_the_draw_day_coming_and_what_a_draw_would_pay(client, conn, staff, pmc):
    # The owner's account holds 900 paid in now.
    _, lease = lease_of(conn, pmc, "Unit 1")
    pay(conn, pmc, charge(conn, pmc.pmc_id, lease, TODAY, "900.00"), "900.00", datetime.now(UTC))
    answer = client.post("/api/payouts/draw-day", json={"draw_day": 5})
    assert answer.status_code in (200, 201), answer.text

    draw = client.get("/api/dashboard").json()["next_draw"]

    on = TODAY.replace(day=5)
    if on < TODAY:
        on = (TODAY.replace(day=1) + 32 * DAY).replace(day=5)
    assert draw == {"on": str(on), "total": "900.00", "last_total": None}


def test_an_account_with_a_month_ended_and_not_reconciled(client, conn, staff, pmc):
    # Statements in from two months ago: that month and last month ended, none reconciled.
    import_statement(
        conn,
        pmc.pmc_id,
        pmc.operating_bank_id,
        staff.person_id,
        first_of(TODAY, 2),
        first_of(TODAY, 1) - DAY,
        [],
    )

    figures = client.get("/api/dashboard").json()

    # Imported today: a month before, no statement had come in yet.
    assert figures["unreconciled"] == {"accounts": 1, "then_accounts": 0}


def test_a_quiet_company_shows_zeros_and_no_occupancy(client, staff, pmc):
    figures = client.get("/api/dashboard", params={"on": str(TODAY)}).json()

    assert figures["occupancy"] == {"percent": None, "then_percent": None}
    assert figures["rent"]["collected"] == figures["rent"]["due"] == "0.00"
    assert figures["past_due"]["total"] == "0.00"
    assert figures["vacant"] == {
        "units": 0,
        "average_days": None,
        "then_units": 0,
        "then_average_days": None,
    }


def test_the_dashboard_is_the_sessions_pmc(client, conn, staff, pmc):
    other = make_pmc(conn)
    prop = other.owners[0].property_id
    unit = add_unit(conn, other.pmc_id, prop, "Unit 1")
    tenant = add_tenant(conn, other.pmc_id, prop, "Tenant A")
    theirs = open_lease(conn, other.pmc_id, unit, START, None, "1500.00", [tenant])
    charge(conn, other.pmc_id, theirs, TODAY - DAY, "1500.00")
    add_unit(conn, other.pmc_id, prop, "Unit 2")

    figures = client.get("/api/dashboard").json()

    assert (figures["past_due"]["total"], figures["vacant"]["units"]) == ("0.00", 0)
    assert figures["occupancy"]["percent"] is None


@pytest.mark.parametrize("caller", ["nobody", "owner", "tenant"])
def test_only_staff_see_the_dashboard(client, conn, app_conn, pmc, caller):
    if caller == "owner":
        join_as_owner(client, app_conn, pmc)
    elif caller == "tenant":
        join_as_tenant(client, conn, app_conn, pmc)

    assert client.get("/api/dashboard").status_code == (401 if caller == "nobody" else 403)


@pytest.mark.parametrize("on", ["not a day", "2026-02-30"])
def test_a_day_that_is_not_one_is_refused(client, staff, on):
    assert client.get("/api/dashboard", params={"on": on}).status_code == 422
