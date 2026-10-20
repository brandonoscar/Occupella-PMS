"""Calendar feeds through the API (src/pms/api/calendar.py, migration 0098): a staff member or a
tenant makes their own link, a calendar app fetches iCalendar text from it with no login, and the
link is taken back by a newer one, by its person, or when their login is disabled."""

import logging
import threading
import time as clock
import uuid
from datetime import UTC, date, datetime, time, timedelta

import icalendar
import psycopg
import pytest
from helpers import (
    add_tenant,
    add_unit,
    add_work_order,
    decline_bill,
    enter_bill,
    file_request,
    link_work_order,
    make_pmc,
    open_lease,
    post,
    step_work_order,
)
from people import join_as, join_as_owner, join_as_staff, join_as_tenant

from pms.api import calendar

TODAY = datetime.now(UTC).date()
DAY = timedelta(days=1)


def first_of_month(day: date, months: int) -> date:
    month = day.month - 1 + months
    return date(day.year + month // 12, month % 12 + 1, 1)


class World:
    """Property 1's Unit 1 leased to Tenant A from 10 days on, with a work order (assigned,
    a visit in 5 days, due in 7) for the tenant's request, a showing in 3 days, a bill due in 9,
    a declined bill, a rent change, renters and vendor insurance running out and fees held; and
    Property 2's lease ending in 20 days."""

    def __init__(self, conn, pmc):
        self.pmc = pmc
        pmc_id = pmc.pmc_id
        first, second = pmc.owners[0], pmc.owners[1]
        self.unit = add_unit(conn, pmc_id, first.property_id, "Unit 1")
        self.tenant = add_tenant(conn, pmc_id, first.property_id, "Tenant A")
        self.starts, self.ends = TODAY + 10 * DAY, TODAY + 200 * DAY
        self.lease = open_lease(
            conn, pmc_id, self.unit, self.starts, self.ends, "1500", [self.tenant]
        )
        other_unit = add_unit(conn, pmc_id, second.property_id, "Unit 9")
        other_tenant = add_tenant(conn, pmc_id, second.property_id, "Tenant B")
        self.other_lease = open_lease(
            conn, pmc_id, other_unit, TODAY - 300 * DAY, TODAY + 20 * DAY, "900", [other_tenant]
        )
        self.order = add_work_order(
            conn,
            pmc_id,
            first.property_id,
            self.unit,
            "Leak under the sink",
            datetime.now(UTC) - 2 * DAY,
        )
        step_work_order(
            conn, pmc_id, self.order, "assigned", datetime.now(UTC) - DAY, vendor_id=pmc.vendor_id
        )
        conn.execute(
            "INSERT INTO trust_work_order_steps (pmc_id, work_order_id, step, taken_at,"
            " scheduled_for, visit_window) VALUES (%s, %s, 'scheduled', %s, %s, '8-10 am')",
            (pmc_id, self.order, datetime.now(UTC), TODAY + 5 * DAY),
        )
        conn.execute(
            "INSERT INTO trust_work_order_details (pmc_id, work_order_id, priority, due_on)"
            " VALUES (%s, %s, 'normal', %s)",
            (pmc_id, self.order, TODAY + 7 * DAY),
        )
        request = file_request(conn, pmc_id, self.lease, self.tenant, "The sink leaks")
        link_work_order(conn, pmc_id, request, self.order)
        lead = conn.execute(
            "INSERT INTO trust_leads (pmc_id, display_name, email, property_id, unit_id)"
            " VALUES (%s, 'Lead L', 'lead@example.com', %s, %s) RETURNING id",
            (pmc_id, first.property_id, self.unit),
        ).fetchone()[0]
        self.showing_at = datetime.combine(TODAY + 3 * DAY, time(15, 0), UTC)
        conn.execute(
            "INSERT INTO trust_showings (pmc_id, lead_id, unit_id, starts_at)"
            " VALUES (%s, %s, %s, %s)",
            (pmc_id, lead, self.unit, self.showing_at),
        )
        self.bill = enter_bill(
            conn,
            pmc_id,
            pmc.vendor_id,
            first.account,
            "INV-1",
            TODAY,
            TODAY + 9 * DAY,
            "123.45",
            memo="Faucet",
        )
        declined = enter_bill(
            conn, pmc_id, pmc.vendor_id, first.account, "INV-2", TODAY, TODAY + 9 * DAY, "50.00"
        )
        decline_bill(conn, pmc_id, declined, TODAY, "Owner O", "Not ours")
        self.raise_on = first_of_month(self.starts, 3)
        conn.execute(
            "INSERT INTO trust_rent_changes (pmc_id, lease_id, effective_on, monthly_rent,"
            " notice_given_on, served_by, lease_terms) VALUES (%s, %s, %s, 1600, %s, 'mail',"
            " 'Section 4 allows a change on 30 days notice')",
            (pmc_id, self.lease, self.raise_on, TODAY),
        )
        conn.execute(
            "INSERT INTO trust_renters_insurance (pmc_id, lease_id, carrier, policy_number,"
            " starts_on, expires_on) VALUES (%s, %s, 'Carrier R', 'R-1', %s, %s)",
            (pmc_id, self.lease, self.starts, TODAY + 100 * DAY),
        )
        conn.execute(
            "INSERT INTO trust_vendor_insurance (pmc_id, vendor_id, coverage, carrier,"
            " policy_number, starts_on, expires_on) VALUES (%s, %s, 'general liability',"
            " 'Carrier V', 'V-1', %s, %s)",
            (pmc_id, pmc.vendor_id, TODAY - 300 * DAY, TODAY + 50 * DAY),
        )
        when = datetime.now(UTC) - 4 * DAY
        owner = first.account
        post(conn, pmc_id, f"rent-{uuid.uuid4()}", [(pmc.operating_cash, owner, "25.00")], when)
        post(conn, pmc_id, f"fee-{uuid.uuid4()}", [(owner, pmc.pmc_income, "25.00")], when)
        self.fees_by = (when + 25 * DAY).date()


@pytest.fixture
def world(conn, pmc):
    return World(conn, pmc)


@pytest.fixture
def staff(client, conn, app_conn, pmc):
    return join_as_staff(client, conn, app_conn, pmc)


def fetch(browser, url):
    """What a calendar app gets from a link: the parsed calendar."""
    answer = browser.get(url.removeprefix("http://testserver"))
    assert answer.status_code == 200, answer.text
    assert answer.headers["content-type"] == "text/calendar; charset=utf-8"
    return icalendar.Calendar.from_ical(answer.content)


def entries(cal):
    """Each event as (summary, start), plus its description when it has one."""
    found = {}
    for event in cal.walk("VEVENT"):
        start = event.decoded("dtstart")
        found[str(event["summary"])] = (start, str(event.get("description", "")) or None)
    return found


def test_a_staff_members_feed_shows_what_is_coming_at_the_company(
    client, other_client, world, staff
):
    made = client.post("/api/calendar/mine", json={})
    cal = fetch(other_client, made.json()["url"])
    seen = entries(cal)

    assert made.status_code == 201
    assert made.json()["url"].startswith("http://testserver/api/calendar/")
    assert seen["Move-in: Property 1, Unit 1"] == (world.starts, None)
    assert seen["Lease ends: Property 1, Unit 1"] == (world.ends, None)
    assert seen["Lease ends: Property 2, Unit 9"][0] == TODAY + 20 * DAY
    assert seen["Visit: Leak under the sink"] == (
        TODAY + 5 * DAY,
        "Property 1, Unit 1, Vendor 1. Window: 8-10 am",
    )
    assert seen["Work order due: Leak under the sink"] == (TODAY + 7 * DAY, "Property 1, Unit 1")
    assert seen["Showing: Property 1, Unit 1"] == (world.showing_at, None)
    assert seen["Bill due: Vendor 1 $123.45"] == (TODAY + 9 * DAY, "Property 1. Faucet")
    assert seen["Rent changes: Property 1, Unit 1 to $1,600.00"][0] == world.raise_on
    assert seen["Renters insurance ends: Property 1, Unit 1"][0] == TODAY + 100 * DAY
    assert seen["Insurance ends: Vendor 1 (general liability)"][0] == TODAY + 50 * DAY
    fees = [s for s in seen if s.startswith("Take the company's fees out of")]
    assert len(fees) == 1 and seen[fees[0]][0] == world.fees_by
    assert "$25.00 held since" in seen[fees[0]][1]
    # A declined bill is due nowhere, and no event carries a tenant's name or contact details.
    assert not any("$50.00" in s for s in seen)
    text = other_client.get(made.json()["url"].removeprefix("http://testserver")).text
    assert "Tenant A" not in text and "Lead L" not in text and "lead@example.com" not in text


def test_each_event_keeps_its_id_and_a_whole_day_lasts_a_day(client, other_client, world, staff):
    url = client.post("/api/calendar/mine", json={}).json()["url"]

    first, again = fetch(other_client, url), fetch(other_client, url)
    uids = [str(e["uid"]) for e in first.walk("VEVENT")]

    assert uids == [str(e["uid"]) for e in again.walk("VEVENT")]
    assert len(set(uids)) == len(uids) and all(u.endswith("@occupella-pms") for u in uids)
    for event in first.walk("VEVENT"):
        start, end = event.decoded("dtstart"), event.decoded("dtend")
        if isinstance(start, datetime):
            assert end - start == timedelta(minutes=30)
        else:
            assert end - start == DAY
    days = [e.decoded("dtstart") for e in first.walk("VEVENT")]
    as_days = [d.date() if isinstance(d, datetime) else d for d in days]
    assert as_days == sorted(as_days)


def test_a_feed_of_one_property_group_shows_its_properties_and_the_companys_fees(
    client, other_client, conn, world, staff, pmc
):
    group = conn.execute(
        "INSERT INTO trust_property_groups (pmc_id, display_name) VALUES (%s, 'North')"
        " RETURNING id",
        (pmc.pmc_id,),
    ).fetchone()[0]
    conn.execute(
        "INSERT INTO trust_property_group_members (pmc_id, group_id, property_id, member)"
        " VALUES (%s, %s, %s, true)",
        (pmc.pmc_id, group, pmc.owners[0].property_id),
    )

    made = client.post("/api/calendar/mine", json={"group_id": str(group)})
    seen = entries(fetch(other_client, made.json()["url"]))
    mine = client.get("/api/calendar/mine").json()

    assert "Move-in: Property 1, Unit 1" in seen
    assert "Lease ends: Property 2, Unit 9" not in seen
    assert any(s.startswith("Take the company's fees out of") for s in seen)
    assert mine["group_id"] == str(group) and mine["made_at"]


def test_a_tenants_feed_shows_their_rent_due_days_their_visits_and_their_leases_end(
    other_client, conn, app_conn, world, pmc
):
    join_as(other_client, app_conn, pmc.pmc_id, "tenant", world.tenant)

    made = other_client.post("/api/tenant/calendar", json={})
    cal = fetch(other_client, made.json()["url"])
    seen = entries(cal)
    dues = sorted(
        e.decoded("dtstart") for e in cal.walk("VEVENT") if str(e["summary"]).startswith("Rent due")
    )

    assert made.status_code == 201
    assert seen["Your lease ends: Property 1, Unit 1"][0] == world.ends
    assert seen["Repair visit: Leak under the sink"] == (
        TODAY + 5 * DAY,
        "Property 1, Unit 1. Window: 8-10 am",
    )
    # The first part month is due on the lease's first day, then the first of each month.
    assert dues[0] == world.starts and all(d.day == 1 for d in dues[1:])
    assert dues[-1] <= world.ends and len(dues) >= 6
    # Nothing of the company's or of other homes.
    assert not any(s.startswith(("Bill due", "Showing", "Take the company")) for s in seen)
    assert not any("Property 2" in s for s in seen)


def test_a_new_link_takes_back_the_old_and_a_link_taken_back_stops(
    client, other_client, world, staff
):
    first = client.post("/api/calendar/mine", json={}).json()["url"]
    second = client.post("/api/calendar/mine", json={}).json()["url"]
    old = other_client.get(first.removeprefix("http://testserver"))
    still = other_client.get(second.removeprefix("http://testserver"))
    taken = client.post("/api/calendar/mine/revoke", json={})
    gone = other_client.get(second.removeprefix("http://testserver"))
    again = client.post("/api/calendar/mine/revoke", json={})

    assert (old.status_code, old.json()["detail"]) == (404, "no such calendar")
    assert still.status_code == 200
    assert taken.status_code == 204
    assert gone.status_code == 404
    assert (again.status_code, again.json()["detail"]) == (
        409,
        "you have no calendar link to take back",
    )
    assert client.get("/api/calendar/mine").json() == {"made_at": None, "group_id": None}


@pytest.mark.timing
def test_two_links_made_at_once_leave_one_standing(client, conn, database_url, staff, pmc):
    with psycopg.connect(database_url) as first:
        calendar.revoke(first, pmc.pmc_id, "staff_id", staff.person_id)  # holds the lock
        first.execute(
            "INSERT INTO trust_calendar_feeds (pmc_id, staff_id, token_sha256) VALUES (%s, %s, %s)",
            (pmc.pmc_id, staff.person_id, b"x" * 32),
        )
        answer: dict = {}
        thread = threading.Thread(
            target=lambda: answer.update(it=client.post("/api/calendar/mine", json={}))
        )
        thread.start()
        for _ in range(1000):  # until the API's link waits for this transaction
            if conn.execute(
                "SELECT 1 FROM pg_stat_activity WHERE wait_event = 'advisory'"
            ).fetchone():
                break
            clock.sleep(0.01)
        else:
            raise AssertionError("the API never waited for the other link")
        first.commit()
    thread.join(timeout=30)
    standing = conn.execute(
        "SELECT f.token_sha256 FROM trust_calendar_feeds AS f WHERE f.staff_id = %s"
        " AND NOT EXISTS (SELECT 1 FROM trust_calendar_feed_revocations AS r"
        " WHERE r.feed_id = f.id)",
        (staff.person_id,),
    ).fetchall()

    assert answer["it"].status_code == 201
    assert len(standing) == 1 and bytes(standing[0][0]) != b"x" * 32


def test_a_feed_stops_once_its_persons_login_is_disabled(
    client, other_client, conn, app_conn, world, staff, pmc
):
    tenant = join_as(other_client, app_conn, pmc.pmc_id, "tenant", world.tenant)
    url = other_client.post("/api/tenant/calendar", json={}).json()["url"]
    before = other_client.get(url.removeprefix("http://testserver"))

    client.post(f"/api/logins/{tenant.login_id}/disable", json={})
    after = client.get(url.removeprefix("http://testserver"))

    assert before.status_code == 200
    assert after.status_code == 404


def test_a_token_that_is_not_ours_is_not_found(other_client):
    answer = other_client.get("/api/calendar/a-made-up-token-of-enough-length.ics")

    assert (answer.status_code, answer.json()["detail"]) == (404, "no such calendar")


def test_a_group_of_another_company_makes_no_feed(client, conn, world, staff):
    elsewhere = make_pmc(conn)
    theirs = conn.execute(
        "INSERT INTO trust_property_groups (pmc_id, display_name) VALUES (%s, 'Theirs')"
        " RETURNING id",
        (elsewhere.pmc_id,),
    ).fetchone()[0]

    answer = client.post("/api/calendar/mine", json={"group_id": str(theirs)})
    bad = client.post("/api/calendar/mine", json={"group_id": "not-a-group"})

    assert (answer.status_code, answer.json()["detail"]) == (404, "no such property group")
    assert bad.status_code == 422
    assert client.get("/api/calendar/mine").json()["made_at"] is None


@pytest.mark.parametrize("caller", ["nobody", "owner", "tenant"])
def test_only_staff_reach_their_own_calendar_link(client, conn, app_conn, pmc, caller):
    if caller == "owner":
        join_as_owner(client, app_conn, pmc)
    elif caller == "tenant":
        join_as_tenant(client, conn, app_conn, pmc)

    answers = [
        client.get("/api/calendar/mine"),
        client.post("/api/calendar/mine", json={}),
        client.post("/api/calendar/mine/revoke", json={}),
    ]

    assert [a.status_code for a in answers] == [401 if caller == "nobody" else 403] * 3


@pytest.mark.parametrize("caller", ["nobody", "owner", "staff"])
def test_only_a_tenant_reaches_their_own_calendar_link(client, conn, app_conn, pmc, caller):
    if caller == "owner":
        join_as_owner(client, app_conn, pmc)
    elif caller == "staff":
        join_as_staff(client, conn, app_conn, pmc)

    answers = [
        client.get("/api/tenant/calendar"),
        client.post("/api/tenant/calendar", json={}),
        client.post("/api/tenant/calendar/revoke", json={}),
    ]

    assert [a.status_code for a in answers] == [401 if caller == "nobody" else 403] * 3


def test_a_staff_member_whose_roles_allow_another_area_still_makes_their_own_link(
    client, other_client, conn, app_conn, pmc
):
    join_as_staff(client, conn, app_conn, pmc, name="Admin 1")
    clerk = join_as_staff(other_client, conn, app_conn, pmc, name="Clerk 1", roles=[])
    role = client.post("/api/custom-roles", json={"name": "Bills only", "areas": ["bills"]})
    client.post(
        f"/api/custom-roles/{role.json()['id']}/staff",
        json={"staff_id": str(clerk.person_id), "granted": True},
    )

    made = other_client.post("/api/calendar/mine", json={})
    taken = other_client.post("/api/calendar/mine/revoke", json={})

    assert other_client.get("/api/me").json()["areas"] == ["bills"]
    assert (made.status_code, taken.status_code) == (201, 204)


def test_the_access_log_never_prints_a_feed_token():
    record = logging.LogRecord(
        "uvicorn.access",
        logging.INFO,
        "",
        0,
        '%s - "%s %s HTTP/%s" %d',
        ("192.0.2.10:5000", "GET", "/api/calendar/secret-token_123.ics", "1.1", 200),
        None,
    )
    logger = logging.getLogger("test-calendar-access")

    calendar.hide_feed_tokens(logger)
    calendar.hide_feed_tokens(logger)

    assert [type(f) for f in logger.filters] == [calendar.HideFeedTokens]
    assert logger.filters[0].filter(record)
    assert record.getMessage() == '192.0.2.10:5000 - "GET /api/calendar/….ics HTTP/1.1" 200'
    other = logging.LogRecord("x", logging.INFO, "", 0, "plain %s", ("text",), None)
    assert logger.filters[0].filter(other) and other.getMessage() == "plain text"
