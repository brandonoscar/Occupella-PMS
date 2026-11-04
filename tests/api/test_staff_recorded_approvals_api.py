"""An owner's approval that staff record names who (migration 20261104000200; the founder's
review of 11/03, item 2): the staff member comes from the session, the owner is the bill's own,
picked by id, and staff say how the owner approved and add a note. No free-text name."""

import json
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import psycopg
import pytest
from helpers import add_agreement, post
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
    return join_as_staff(client, conn, app_conn, pmc, name="Manager 1")


@pytest.fixture
def bill(client, conn, staff, pmc):
    """Owner 1's 800.00 bill over their 500.00 limit; their account holds 1,000.00."""
    post(
        conn,
        pmc.pmc_id,
        f"fund-{uuid.uuid4()}",
        [(pmc.operating_cash, pmc.owners[0].account, Decimal("1000.00"))],
        datetime.now(UTC) - 30 * DAY,
    )
    add_agreement(conn, pmc.pmc_id, pmc.owners[0].account, TODAY - 60 * DAY, approval_limit="500")
    made = call(
        client,
        "bills",
        vendor_id=pmc.vendor_id,
        account=pmc.owners[0].account,
        reference="ROOF-2",
        bill_date=TODAY - 2 * DAY,
        due_on=TODAY + 10 * DAY,
        amount="800.00",
        memo="Roof",
    )
    assert made.status_code == 201, made.text
    return made.json()["id"]


def call(client, path, **body):
    return client.post(f"/api/payables/{path}", json=json.loads(json.dumps(body, default=str)))


def approve(client, bill, **change):
    return call(client, f"bills/{bill}/approval", **(APPROVAL | change))


NOTE = "Called on the 3rd; go ahead."
APPROVAL = {"method": "phone", "note": NOTE}


def test_staff_record_the_owners_approval_by_id_how_and_why(client, conn, staff, pmc, bill):
    owner = pmc.owners[0].owner_id
    answer = approve(client, bill, approved_on=TODAY, owner_id=owner)

    assert answer.status_code == 201, answer.text
    assert conn.execute(
        "SELECT a.approved_by, a.owner_id, a.method, a.note, s.display_name"
        " FROM trust_bill_approvals AS a JOIN trust_staff AS s ON s.id = a.recorded_by"
        " WHERE a.bill_id = %s",
        (bill,),
    ).fetchone() == (
        "Owner 1, by phone",
        owner,
        "phone",
        "Called on the 3rd; go ahead.",
        "Manager 1",
    )
    history = client.get(f"/api/payables/bills/{bill}").json()["history"]
    assert any(
        e["what"]
        == "Approved by Owner 1, by phone. Recorded by Manager 1: Called on the 3rd; go ahead."
        for e in history
    ), history


def test_only_the_bills_owner_can_be_named(client, conn, staff, pmc, bill):
    for owner in (pmc.owners[1].owner_id, uuid.uuid4()):
        answer = approve(client, bill, approved_on=TODAY, owner_id=owner)
        assert (answer.status_code, answer.json()["detail"]) == (
            422,
            "Pick the bill's owner: only they can approve it.",
        )
    assert conn.execute(
        "SELECT count(*) FROM trust_bill_approvals WHERE bill_id = %s", (bill,)
    ).fetchone() == (0,)


@pytest.mark.parametrize(
    "change",
    [
        {"note": "   "},
        {"note": None},
        {"method": "text message"},
        {"method": None},
        {"approved_by": "Demo Owner, by phone"},
        {"owner_id": None},
        {"approved_on": "soon"},
    ],
)
def test_a_free_text_name_or_a_missing_how_or_note_is_refused(
    client, conn, staff, pmc, bill, change
):
    body = {"approved_on": TODAY, "owner_id": pmc.owners[0].owner_id} | change
    answer = approve(client, bill, **body)
    assert answer.status_code == 422, answer.text


def test_only_staff_record_an_owners_approval(client, other_client, conn, app_conn, pmc, bill):
    join_as_owner(other_client, app_conn, pmc)
    owner_try = approve(other_client, bill, approved_on=TODAY, owner_id=pmc.owners[0].owner_id)
    client.cookies.clear()
    join_as_tenant(client, conn, app_conn, pmc)
    tenant_try = approve(client, bill, approved_on=TODAY, owner_id=pmc.owners[0].owner_id)
    assert {owner_try.status_code, tenant_try.status_code} <= {401, 403}


def test_the_database_holds_a_staff_member_to_naming_the_owner_how_and_why(conn, pmc, staff, bill):
    conn.execute("SELECT set_config('trust.staff_id', %s, false)", (str(staff.person_id),))
    with pytest.raises(psycopg.errors.CheckViolation):
        conn.execute(
            "INSERT INTO trust_bill_approvals (pmc_id, bill_id, approved_on, approved_by)"
            " VALUES (%s, %s, %s, 'Someone, by phone')",
            (pmc.pmc_id, bill, TODAY),
        )
