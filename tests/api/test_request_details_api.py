"""A maintenance request's details (migration 20261104000600, the founder's review of 11/03, item
12): what kind of repair it is and where in the unit; a tenant on the lease takes it back while it
is still received, and adds notes while it is open. Staff see all of it; the AI role never reads
the notes. Every name here is synthetic."""

import threading
import time
from datetime import UTC, datetime

import psycopg
import pytest
from helpers import add_tenant, add_work_order, link_work_order, step_work_order
from people import join_as, join_as_owner, join_as_staff, join_as_tenant
from test_tenant import Home, new_request


@pytest.fixture
def home(conn, pmc):
    return Home(conn, pmc)


def ok(answer, status=200):
    assert answer.status_code == status, answer.text
    return answer.json()


def file(client, home, **more):
    return ok(client.post("/api/tenant/requests", json=new_request(home, **more)), 201)


def staff_sees(client, request_id):
    listed = ok(client.get("/api/maintenance/requests", params={"show": "all"}))
    return next(r for r in listed if r["id"] == request_id)


def test_a_request_says_what_kind_of_repair_and_where(client, other_client, conn, app_conn, home):
    join_as_tenant(other_client, conn, app_conn, home.pmc)

    filed = file(other_client, home, category="plumbing", location="  Kitchen sink  ")

    assert (filed["category"], filed["location"]) == ("plumbing", "Kitchen sink")
    join_as_staff(client, conn, app_conn, home.pmc)
    staff = staff_sees(client, filed["id"])
    assert (staff["category"], staff["location"], staff["taken_back"]) == (
        "plumbing",
        "Kitchen sink",
        False,
    )


def test_a_request_filed_without_them_says_neither(client, conn, app_conn, home):
    join_as_tenant(client, conn, app_conn, home.pmc)

    filed = file(client, home)

    assert (filed["category"], filed["location"], filed["notes"]) == (None, None, [])


@pytest.mark.parametrize(
    "changes",
    [
        {"category": "roof"},
        {"location": "Kitchen\nsink"},
        {"location": "x" * 201},
        {"location": "   "},
    ],
)
def test_what_isnt_a_category_or_a_place_is_refused(client, conn, app_conn, home, changes):
    join_as_tenant(client, conn, app_conn, home.pmc)

    answer = client.post("/api/tenant/requests", json=new_request(home, **changes))

    assert answer.status_code == 422
    assert conn.execute(
        "SELECT count(*) FROM trust_maintenance_requests WHERE lease_id = %s", (home.lease,)
    ).fetchone() == (0,)


# --- taking a request back ------------------------------------------------------------------


def test_a_tenant_takes_back_a_request_still_received_and_staff_open_no_work_order(
    client, other_client, conn, app_conn, home
):
    join_as_tenant(other_client, conn, app_conn, home.pmc)
    filed = file(other_client, home, category="appliance")

    cancelled = ok(other_client.post(f"/api/tenant/requests/{filed['id']}/cancel", json={}))
    again = ok(other_client.post(f"/api/tenant/requests/{filed['id']}/cancel", json={}))

    assert (cancelled["status"], cancelled["taken_back"]) == ("cancelled", True)
    assert again == cancelled  # sent again, nothing new
    assert conn.execute(
        "SELECT count(*) FROM trust_maintenance_request_cancellations WHERE request_id = %s",
        (filed["id"],),
    ).fetchone() == (1,)
    join_as_staff(client, conn, app_conn, home.pmc)
    assert staff_sees(client, filed["id"])["status"] == "cancelled"
    opened = client.post(f"/api/maintenance/requests/{filed['id']}/work-order", json={})
    assert (opened.status_code, opened.json()["detail"]) == (
        409,
        "The tenant cancelled this request.",
    )
    assert conn.execute(
        "SELECT count(*) FROM trust_maintenance_request_work_orders WHERE request_id = %s",
        (filed["id"],),
    ).fetchone() == (0,)


def test_a_request_with_a_work_order_cant_be_taken_back(client, other_client, conn, app_conn, home):
    join_as_tenant(other_client, conn, app_conn, home.pmc)
    filed = file(other_client, home)
    join_as_staff(client, conn, app_conn, home.pmc)
    ok(client.post(f"/api/maintenance/requests/{filed['id']}/work-order", json={}), 201)

    answer = other_client.post(f"/api/tenant/requests/{filed['id']}/cancel", json={})

    assert answer.status_code == 409
    assert "can't be cancelled" in answer.json()["detail"]


def test_the_database_keeps_a_cancellation_and_a_work_order_apart(conn, app_conn, home):
    """Even past the API: no cancellation after a work order, no work order after one."""
    filed = app_conn.execute(
        "INSERT INTO trust_maintenance_requests (pmc_id, lease_id, tenant_id, summary,"
        " entry_allowed) VALUES (%s, %s, %s, 'Door sticks', true) RETURNING id",
        (home.pmc.pmc_id, home.lease, home.tenant),
    ).fetchone()[0]
    app_conn.execute(
        "INSERT INTO trust_maintenance_request_cancellations"
        " (pmc_id, request_id, lease_id, tenant_id) VALUES (%s, %s, %s, %s)",
        (home.pmc.pmc_id, filed, home.lease, home.tenant),
    )
    order = app_conn.execute(
        "INSERT INTO trust_work_orders (pmc_id, property_id, unit_id, summary, opened_at)"
        " VALUES (%s, %s, %s, 'Door sticks', now()) RETURNING id",
        (home.pmc.pmc_id, home.pmc.owners[0].property_id, home.unit),
    ).fetchone()[0]

    with pytest.raises(psycopg.errors.CheckViolation, match="cancelled by its tenant"):
        app_conn.execute(
            "INSERT INTO trust_maintenance_request_work_orders (pmc_id, request_id,"
            " work_order_id) VALUES (%s, %s, %s)",
            (home.pmc.pmc_id, filed, order),
        )
    with pytest.raises(psycopg.errors.CheckViolation, match="was cancelled"):
        app_conn.execute(
            "INSERT INTO trust_maintenance_request_notes (pmc_id, request_id, lease_id,"
            " tenant_id, body) VALUES (%s, %s, %s, %s, 'Still sticks')",
            (home.pmc.pmc_id, filed, home.lease, home.tenant),
        )
    with pytest.raises(psycopg.errors.CheckViolation, match="not of lease"):
        app_conn.execute(
            "INSERT INTO trust_maintenance_request_cancellations"
            " (pmc_id, request_id, lease_id, tenant_id) VALUES (%s, %s, %s, %s)",
            (home.pmc.pmc_id, filed, home.old_lease, home.tenant),
        )


def test_the_database_takes_nothing_from_a_tenant_who_left_or_on_a_closed_request(
    conn, app_conn, home
):
    """Even past the API: a tenant who left the lease cancels nothing and adds no note, a request
    with a work order isn't cancelled, and a closed one takes no note."""
    pmc = home.pmc.pmc_id
    filed = app_conn.execute(
        "INSERT INTO trust_maintenance_requests (pmc_id, lease_id, tenant_id, summary,"
        " entry_allowed) VALUES (%s, %s, %s, 'Window sticks', true) RETURNING id",
        (pmc, home.lease, home.tenant),
    ).fetchone()[0]
    note = (
        "INSERT INTO trust_maintenance_request_notes (pmc_id, request_id, lease_id, tenant_id,"
        " body) VALUES (%s, %s, %s, %s, 'Still sticks')"
    )
    order = add_work_order(
        app_conn, pmc, home.pmc.owners[0].property_id, home.unit, "Window", datetime.now(UTC)
    )
    link_work_order(app_conn, pmc, filed, order)

    with pytest.raises(psycopg.errors.CheckViolation, match="can no longer be cancelled"):
        app_conn.execute(
            "INSERT INTO trust_maintenance_request_cancellations"
            " (pmc_id, request_id, lease_id, tenant_id) VALUES (%s, %s, %s, %s)",
            (pmc, filed, home.lease, home.tenant),
        )
    step_work_order(app_conn, pmc, order, "cancelled", datetime.now(UTC))
    with pytest.raises(psycopg.errors.CheckViolation, match="is closed"):
        app_conn.execute(note, (pmc, filed, home.lease, home.tenant))
    conn.execute(
        "SELECT trust_remove_lease_tenant(%s, %s, %s, current_date - 1, NULL)",
        (pmc, home.lease, home.tenant),
    )
    with pytest.raises(psycopg.errors.CheckViolation, match="has left lease"):
        app_conn.execute(note, (pmc, filed, home.lease, home.tenant))


# --- notes ----------------------------------------------------------------------------------


def test_a_tenant_adds_notes_to_an_open_request_and_staff_read_them(
    client, other_client, conn, app_conn, home
):
    join_as_tenant(other_client, conn, app_conn, home.pmc)
    filed = file(other_client, home, category="plumbing")

    noted = ok(
        other_client.post(
            f"/api/tenant/requests/{filed['id']}/notes", json={"body": "  It drips faster now.  "}
        ),
        201,
    )

    assert [(n["by"], n["body"]) for n in noted["notes"]] == [("Tenant 1", "It drips faster now.")]
    join_as_staff(client, conn, app_conn, home.pmc)
    ok(client.post(f"/api/maintenance/requests/{filed['id']}/work-order", json={}), 201)
    still_open = other_client.post(
        f"/api/tenant/requests/{filed['id']}/notes", json={"body": "The plumber can come Monday."}
    )
    assert still_open.status_code == 201, still_open.text
    assert [n["body"] for n in staff_sees(client, filed["id"])["notes"]] == [
        "It drips faster now.",
        "The plumber can come Monday.",
    ]


def test_a_closed_or_cancelled_request_takes_no_note(client, other_client, conn, app_conn, home):
    join_as_tenant(other_client, conn, app_conn, home.pmc)
    done = file(other_client, home)
    taken_back = file(other_client, home, summary="Light flickers")
    ok(other_client.post(f"/api/tenant/requests/{taken_back['id']}/cancel", json={}))
    join_as_staff(client, conn, app_conn, home.pmc)
    order = ok(client.post(f"/api/maintenance/requests/{done['id']}/work-order", json={}), 201)
    ok(
        client.post(
            f"/api/maintenance/work-orders/{order['work_order_id']}/steps",
            json={"step": "completed"},
        ),
        201,
    )

    answers = [
        other_client.post(f"/api/tenant/requests/{r['id']}/notes", json={"body": "Thanks"})
        for r in (done, taken_back)
    ]

    assert [a.status_code for a in answers] == [409, 409]
    assert conn.execute(
        "SELECT count(*) FROM trust_maintenance_request_notes WHERE lease_id = %s", (home.lease,)
    ).fetchone() == (0,)


def test_notes_come_twenty_a_day_at_most(client, conn, app_conn, home):
    join_as_tenant(client, conn, app_conn, home.pmc)
    filed = file(client, home)
    for n in range(20):
        ok(
            client.post(f"/api/tenant/requests/{filed['id']}/notes", json={"body": f"Note {n}"}),
            201,
        )

    answer = client.post(f"/api/tenant/requests/{filed['id']}/notes", json={"body": "One more"})

    assert answer.status_code == 429


@pytest.mark.parametrize("body", [{"body": "   "}, {"body": "x" * 2001}, {}])
def test_an_empty_or_long_note_is_refused(client, conn, app_conn, home, body):
    join_as_tenant(client, conn, app_conn, home.pmc)
    filed = file(client, home)

    assert client.post(f"/api/tenant/requests/{filed['id']}/notes", json=body).status_code == 422


# --- who may ---------------------------------------------------------------------------------


def test_a_request_of_a_lease_theyre_not_on_reads_as_not_found(
    client, other_client, conn, app_conn, home
):
    join_as_tenant(other_client, conn, app_conn, home.pmc)
    filed = file(other_client, home)
    neighbour = add_tenant(conn, home.pmc.pmc_id, home.pmc.owners[0].property_id, "Tenant 8")
    join_as(client, app_conn, home.pmc.pmc_id, "tenant", neighbour)

    answers = [
        client.post(f"/api/tenant/requests/{filed['id']}/cancel", json={}),
        client.post(f"/api/tenant/requests/{filed['id']}/notes", json={"body": "Hi"}),
    ]

    assert [a.status_code for a in answers] == [404, 404]


def test_a_tenant_who_left_the_lease_takes_nothing_back(client, conn, app_conn, home):
    join_as_tenant(client, conn, app_conn, home.pmc)
    filed = file(client, home)
    # Tenant 1 leaves the lease Tenant 2 stays on, from yesterday.
    conn.execute(
        "SELECT trust_remove_lease_tenant(%s, %s, %s, current_date - 1, NULL)",
        (home.pmc.pmc_id, home.lease, home.tenant),
    )

    answers = [
        client.post(f"/api/tenant/requests/{filed['id']}/cancel", json={}),
        client.post(f"/api/tenant/requests/{filed['id']}/notes", json={"body": "Hi"}),
    ]

    assert [a.status_code for a in answers] == [409, 409]


@pytest.mark.parametrize("caller", ["nobody", "staff", "owner"])
def test_only_a_tenant_takes_back_or_adds_to_a_request(
    client, other_client, conn, app_conn, home, caller
):
    join_as_tenant(other_client, conn, app_conn, home.pmc)
    filed = file(other_client, home)
    if caller == "staff":
        join_as_staff(client, conn, app_conn, home.pmc)
    elif caller == "owner":
        join_as_owner(client, app_conn, home.pmc)

    answers = [
        client.post(f"/api/tenant/requests/{filed['id']}/cancel", json={}),
        client.post(f"/api/tenant/requests/{filed['id']}/notes", json={"body": "Hi"}),
    ]

    assert [a.status_code for a in answers] == [401 if caller == "nobody" else 403] * 2


# --- at the same moment ----------------------------------------------------------------------


def waits(conn, insert):
    """Until the API's insert waits for the other transaction's lock on the request."""
    for _ in range(1000):
        if conn.execute(
            "SELECT 1 FROM pg_stat_activity WHERE wait_event_type = 'Lock' AND query LIKE %s",
            (f"INSERT INTO {insert}%",),
        ).fetchone():
            return
        time.sleep(0.01)
    raise AssertionError(f"the API never waited to insert into {insert}")


def cancel_uncommitted(database_url, home, filed):
    """A connection holding the tenant's cancellation of the request, not committed yet."""
    first = psycopg.connect(database_url)
    first.execute(
        "INSERT INTO trust_maintenance_request_cancellations"
        " (pmc_id, request_id, lease_id, tenant_id) VALUES (%s, %s, %s, %s)",
        (home.pmc.pmc_id, filed["id"], home.lease, home.tenant),
    )
    return first


def at_once(conn, first, insert, call):
    answer: dict = {}
    thread = threading.Thread(target=lambda: answer.update(it=call()))
    thread.start()
    waits(conn, insert)
    first.commit()
    first.close()
    thread.join(timeout=30)
    return answer["it"]


@pytest.mark.timing
def test_a_cancel_as_staff_open_its_work_order_is_refused(
    client, other_client, conn, app_conn, database_url, home
):
    join_as_tenant(other_client, conn, app_conn, home.pmc)
    filed = file(other_client, home)
    first = psycopg.connect(database_url)
    order = add_work_order(
        first,
        home.pmc.pmc_id,
        home.pmc.owners[0].property_id,
        home.unit,
        "Sink",
        datetime.now(UTC),
    )
    link_work_order(first, home.pmc.pmc_id, filed["id"], order)  # not committed yet

    answer = at_once(
        conn,
        first,
        "trust_maintenance_request_cancellations",
        lambda: other_client.post(f"/api/tenant/requests/{filed['id']}/cancel", json={}),
    )

    assert (answer.status_code, answer.json()["detail"]) == (
        409,
        "It's being worked on, so it can't be cancelled. Send a note instead.",
    )
    assert conn.execute(
        "SELECT count(*) FROM trust_maintenance_request_cancellations WHERE request_id = %s",
        (filed["id"],),
    ).fetchone() == (0,)


@pytest.mark.timing
def test_a_note_as_the_request_is_cancelled_is_refused(
    other_client, conn, app_conn, database_url, home
):
    join_as_tenant(other_client, conn, app_conn, home.pmc)
    filed = file(other_client, home)
    first = cancel_uncommitted(database_url, home, filed)

    answer = at_once(
        conn,
        first,
        "trust_maintenance_request_notes",
        lambda: other_client.post(
            f"/api/tenant/requests/{filed['id']}/notes", json={"body": "Still dripping"}
        ),
    )

    assert (answer.status_code, answer.json()["detail"]) == (
        409,
        "This request is closed. Send a new one if you still need help.",
    )


@pytest.mark.timing
def test_a_work_order_as_the_tenant_cancels_is_refused(
    client, other_client, conn, app_conn, database_url, home
):
    join_as_tenant(other_client, conn, app_conn, home.pmc)
    filed = file(other_client, home)
    join_as_staff(client, conn, app_conn, home.pmc)
    first = cancel_uncommitted(database_url, home, filed)

    answer = at_once(
        conn,
        first,
        "trust_maintenance_request_work_orders",
        lambda: client.post(f"/api/maintenance/requests/{filed['id']}/work-order", json={}),
    )

    assert (answer.status_code, answer.json()["detail"]) == (
        409,
        "The tenant cancelled this request.",
    )
    assert conn.execute(
        "SELECT count(*) FROM trust_maintenance_request_work_orders WHERE request_id = %s",
        (filed["id"],),
    ).fetchone() == (0,)
