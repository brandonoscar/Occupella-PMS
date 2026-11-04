"""How to pay us (migration 20261104000400; the founder's review of 11/03, item 4b): staff write
what their company tells tenants and owners about paying it, and the tenant's Pay online and the
owner's Add money carry it, with why they can't pay online ("off", or "paused" on a payment hold),
in place of a line the server wrote. Each company reads only its own."""

from datetime import UTC, datetime, timedelta

import pytest
from helpers import add_unit, make_pmc, open_lease
from people import join_as_owner, join_as_staff, join_as_tenant, tenant_of

PATH = "/api/company/how-to-pay"
WORDS = "Pay by check to Example Property Co.\nDrop it at 100 Example Street, Suite 2."


def ok(answer, status=200):
    assert answer.status_code == status, answer.text
    return answer.json() if answer.content else None


def rows(conn, pmc_id):
    return conn.execute(
        "SELECT body, recorded_by FROM trust_payment_instructions WHERE pmc_id = %s"
        " ORDER BY created_at",
        (pmc_id,),
    ).fetchall()


@pytest.fixture
def lease(conn, pmc):
    today = datetime.now(UTC).date()
    unit = add_unit(conn, pmc.pmc_id, pmc.owners[0].property_id)
    return open_lease(
        conn, pmc.pmc_id, unit, today - timedelta(days=30), None, "1200.00", [tenant_of(conn, pmc)]
    )


def test_staff_write_it_and_the_tenant_and_owner_read_it_where_they_would_pay(
    client, other_client, conn, app_conn, pmc, lease
):
    staff = join_as_staff(client, conn, app_conn, pmc)
    assert ok(client.get(PATH)) == {"body": None}

    saved = ok(client.post(PATH, json={"body": f"  {WORDS}  "}))
    again = ok(client.post(PATH, json={"body": WORDS}))

    assert saved == again == {"body": WORDS}
    assert rows(conn, pmc.pmc_id) == [(WORDS, staff.person_id)]  # the same again writes nothing
    join_as_tenant(other_client, conn, app_conn, pmc)
    paying = ok(other_client.get(f"/api/payments/leases/{lease}"))
    autopay = ok(other_client.get(f"/api/payments/leases/{lease}/autopay"))
    assert (paying["available"], paying["why_not"], paying["how_to_pay"]) == (False, "off", WORDS)
    assert (autopay["available"], autopay["why_not"]) == (False, "off")
    join_as_owner(other_client, app_conn, pmc)
    adding = ok(other_client.get("/api/payments/owner"))
    assert (adding["available"], adding["why_not"], adding["how_to_pay"]) == (False, "off", WORDS)


def test_clearing_it_leaves_nothing_to_show(client, conn, app_conn, pmc):
    join_as_staff(client, conn, app_conn, pmc)
    ok(client.post(PATH, json={"body": WORDS}))

    cleared = ok(client.post(PATH, json={"body": "   "}))
    none = ok(client.post(PATH, json={}))

    assert cleared == none == {"body": None}
    assert [body for body, _ in rows(conn, pmc.pmc_id)] == [WORDS, None]


def test_each_company_reads_only_its_own(client, other_client, conn, app_conn, pmc, lease):
    elsewhere = make_pmc(conn)
    join_as_staff(other_client, conn, app_conn, elsewhere)
    ok(other_client.post(PATH, json={"body": "Wire it to the other company."}))
    join_as_staff(client, conn, app_conn, pmc)

    assert ok(client.get(PATH)) == {"body": None}
    join_as_tenant(other_client, conn, app_conn, pmc)
    assert ok(other_client.get(f"/api/payments/leases/{lease}"))["how_to_pay"] is None


@pytest.mark.parametrize(
    "body",
    [
        {"body": "x" * 2001},
        {"body": "a\x00b"},
        {"body": WORDS, "online": True},
        {"body": 5},
    ],
)
def test_what_isnt_how_to_pay_is_refused(client, conn, app_conn, pmc, body):
    join_as_staff(client, conn, app_conn, pmc)

    answer = client.post(PATH, json=body)

    assert answer.status_code == 422
    assert rows(conn, pmc.pmc_id) == []


@pytest.mark.parametrize("caller", ["nobody", "owner", "tenant"])
def test_only_staff_read_or_write_it(client, conn, app_conn, pmc, lease, caller):
    if caller == "owner":
        join_as_owner(client, app_conn, pmc)
    elif caller == "tenant":
        join_as_tenant(client, conn, app_conn, pmc)

    answers = [client.get(PATH), client.post(PATH, json={"body": WORDS})]

    assert [a.status_code for a in answers] == [401 if caller == "nobody" else 403] * 2
    assert rows(conn, pmc.pmc_id) == []
