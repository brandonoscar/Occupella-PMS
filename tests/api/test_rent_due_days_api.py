"""A lease's rent due day through the API (src/pms/api/rent.py; the founder's review, q013): staff
pick the day of the month a lease's rent is due, from the first of a month, when they add a lease
in place or later from its page; the lease's page and the tenant's portal show it."""

import json
from datetime import UTC, date, datetime

import pytest
from helpers import add_unit, make_pmc, open_lease
from people import join_as_owner, join_as_staff, join_as_tenant

from pms.api import rent, tenant

TODAY = datetime.now(UTC).date()


def months_from(day, n):
    """The first of the month n months from day's month."""
    year, month = divmod(day.year * 12 + day.month - 1 + n, 12)
    return date(year, month + 1, 1)


NEXT_MONTH, IN_TWO, IN_THREE = (months_from(TODAY, n) for n in (1, 2, 3))


@pytest.fixture
def staff(client, conn, app_conn, pmc):
    return join_as_staff(client, conn, app_conn, pmc)


def tenant_of(conn, pmc):
    return conn.execute(
        "SELECT tenant_id FROM trust_ledger_accounts WHERE ledger_account_id = %s",
        (pmc.prepaid_rent,),
    ).fetchone()[0]


@pytest.fixture
def lease(conn, pmc):
    unit = add_unit(conn, pmc.pmc_id, pmc.owners[0].property_id)
    return open_lease(
        conn, pmc.pmc_id, unit, months_from(TODAY, -3), None, "1500.00", [tenant_of(conn, pmc)]
    )


def ok(answer, status=200):
    assert answer.status_code == status, answer.text
    return None if status == 204 else answer.json()


def refused(answer, status, detail=None):
    assert answer.status_code == status, answer.text
    if detail is not None:
        assert answer.json()["detail"] == detail


def change(client, lease, **body):
    body = {"starts_on": NEXT_MONTH, "due_day": 10} | body
    return client.post(
        f"/api/rent/leases/{lease}/due-day", json=json.loads(json.dumps(body, default=str))
    )


def test_staff_move_a_leases_rent_due_day_from_next_month(
    client, other_client, conn, app_conn, staff, pmc, lease, monkeypatch
):
    made = ok(change(client, lease), 201)

    assert made == {"id": made["id"], "starts_on": str(NEXT_MONTH), "due_day": 10}
    shown = ok(client.get(f"/api/leases/{lease}"))
    assert (shown["rent_due_day"], shown["due_days"]) == (1, [made])
    join_as_tenant(other_client, conn, app_conn, pmc)
    (mine,) = ok(other_client.get("/api/tenant/leases"))
    assert mine["rent_due_day"] == 1
    # From its month on, the lease's page and the portal show the new day.
    with monkeypatch.context() as later:
        later.setattr(rent, "today", lambda: NEXT_MONTH)
        later.setattr(tenant, "today", lambda: NEXT_MONTH)
        assert ok(client.get(f"/api/leases/{lease}"))["rent_due_day"] == 10
        (mine,) = ok(other_client.get("/api/tenant/leases"))
        assert mine["rent_due_day"] == 10
    assert conn.execute(
        "SELECT recorded_by FROM trust_rent_due_days WHERE lease_id = %s", (lease,)
    ).fetchone() == (staff.person_id,)


def test_the_rent_run_charges_on_the_new_day(client, conn, staff, pmc, lease):
    ok(change(client, lease, due_day=12), 201)
    tenth, twelfth = NEXT_MONTH.replace(day=10), NEXT_MONTH.replace(day=12)
    for day in (NEXT_MONTH, tenth, twelfth):
        conn.execute("SELECT trust_run_rent(%s, %s, %s)", (pmc.pmc_id, NEXT_MONTH, day))

    assert conn.execute(
        "SELECT due_on FROM trust_charges WHERE lease_id = %s AND kind = 'rent'", (lease,)
    ).fetchall() == [(twelfth,)]


def test_a_month_set_or_charged_already_and_a_month_outside_the_lease_are_refused(
    client, conn, staff, pmc, lease
):
    ok(change(client, lease), 201)
    refused(
        change(client, lease, due_day=15),
        409,
        "this lease's due day changes from that month already",
    )
    conn.execute(
        "INSERT INTO trust_charges (pmc_id, lease_id, due_on, kind, amount)"
        " VALUES (%s, %s, %s, 'rent', 1500.00)",
        (pmc.pmc_id, lease, IN_THREE),
    )
    refused(
        change(client, lease, starts_on=IN_TWO),
        409,
        f"rent due {IN_THREE} is charged already; a new due day starts after its month",
    )
    unit = add_unit(conn, pmc.pmc_id, pmc.owners[0].property_id, "Unit 2")
    starts = months_from(TODAY, -3)
    ending = open_lease(conn, pmc.pmc_id, unit, starts, TODAY, "900.00", [tenant_of(conn, pmc)])
    refused(
        change(client, ending),
        409,
        f"a rent due day starts in a month of the lease, from {starts} to {TODAY}",
    )


@pytest.mark.parametrize("caller", ["nobody", "owner", "tenant"])
def test_only_staff_move_a_due_day(client, conn, app_conn, pmc, lease, caller):
    if caller == "owner":
        join_as_owner(client, app_conn, pmc)
    elif caller == "tenant":
        join_as_tenant(client, conn, app_conn, pmc)
    refused(change(client, lease), 401 if caller == "nobody" else 403)


def test_another_companys_staff_find_no_such_lease(other_client, conn, app_conn, lease):
    join_as_staff(other_client, conn, app_conn, make_pmc(conn), name="Staff 2")
    refused(change(other_client, lease), 404)


def test_a_custom_role_without_rent_may_not_move_a_due_day(
    client, other_client, conn, app_conn, staff, pmc, lease
):
    clerk = join_as_staff(other_client, conn, app_conn, pmc, name="Clerk 2", roles=[])
    role = ok(client.post("/api/custom-roles", json={"name": "Books", "areas": ["bills"]}), 201)
    ok(
        client.post(
            f"/api/custom-roles/{role['id']}/staff",
            json={"staff_id": str(clerk.person_id), "granted": True},
        ),
        204,
    )
    refused(change(other_client, lease), 403)


@pytest.mark.parametrize(
    ("body", "detail"),
    [
        ({"starts_on": "2026-11-02"}, "a due day starts on the first of a month"),
        ({"starts_on": "soon"}, None),
        ({"due_day": 0}, None),
        ({"due_day": 29}, None),
        ({"due_day": "the tenth"}, None),
        ({"due_day": None}, None),
    ],
)
def test_bad_input_is_refused(client, conn, staff, lease, body, detail):
    refused(change(client, lease, **body), 422, detail)
    assert conn.execute(
        "SELECT count(*) FROM trust_rent_due_days WHERE lease_id = %s", (lease,)
    ).fetchone() == (0,)


def add_lease(client, conn, pmc, unit_name="Unit 3", **body):
    unit = add_unit(conn, pmc.pmc_id, pmc.owners[0].property_id, unit_name)
    body = {
        "unit_id": unit,
        "starts_on": TODAY,
        "monthly_rent": "1200.00",
        "tenant_ids": [tenant_of(conn, pmc)],
    } | body
    return client.post("/api/setup/leases", json=json.loads(json.dumps(body, default=str)))


def test_a_lease_added_in_place_picks_its_due_day_from_its_first_month(client, conn, staff, pmc):
    added = ok(add_lease(client, conn, pmc, rent_due_day=15), 201)

    assert conn.execute(
        "SELECT starts_on, due_day, recorded_by FROM trust_rent_due_days WHERE lease_id = %s",
        (added["id"],),
    ).fetchall() == [(TODAY.replace(day=1), 15, staff.person_id)]
    # Due on the first, it records nothing.
    plain = ok(add_lease(client, conn, pmc, "Unit 4"), 201)
    assert conn.execute(
        "SELECT count(*) FROM trust_rent_due_days WHERE lease_id = %s", (plain["id"],)
    ).fetchone() == (0,)


@pytest.mark.parametrize("day", [0, 29, "15th"])
def test_a_lease_added_with_a_due_day_off_the_calendar_is_refused(client, conn, staff, pmc, day):
    leases = "SELECT count(*) FROM trust_leases WHERE pmc_id = %s"
    before = conn.execute(leases, (pmc.pmc_id,)).fetchone()
    refused(add_lease(client, conn, pmc, rent_due_day=day), 422)
    assert conn.execute(leases, (pmc.pmc_id,)).fetchone() == before
