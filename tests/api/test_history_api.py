"""The old system's past transactions through the API (migration 0107; the founder's review q129):
staff bring tenants', owners' and vendors' history in at Settings, Import, and read each person's
lines from before the switch, newest first; none of it touches the books."""

from datetime import UTC, datetime, timedelta

import pytest
from helpers import make_pmc
from people import join_as_staff, join_as_tenant

TENANTS = """Property,Tenant,Unit,Date,Type,Description,Amount,Reference
Property 1,Tenant 1,Apt 1,2025-03-01,Charge,March rent,1200.00,
Property 1,Tenant 1,Apt 1,2025-03-04,Payment,Check 311,-1200.00,311
Property 1,Former Tenant,Apt 2,2024-11-01,Charge,November rent,950.00,
"""
OWNERS = """Owner,Property,Date,Type,Description,Amount
Owner 1,Property 1,2025-03-31,Owner draw,March draw,-850.00
Owner 1,Property 1,2025-03-05,Rent,March rent collected,1200.00
"""
VENDORS = """Vendor,Date,Type,Description,Amount,Reference
Vendor 1,2025-02-14,Payment,Furnace repair,340.00,CHK-1009
"""


def ok(answer, status=200):
    assert answer.status_code == status, answer.text
    return answer.json() if answer.content else None


@pytest.fixture
def staff(client, conn, app_conn, pmc):
    return join_as_staff(client, conn, app_conn, pmc)


def bring(client, kind, content):
    return client.post("/api/switching/import", json={"kind": kind, "content": content})


def tenant_named(conn, pmc, name):
    return conn.execute(
        "SELECT id FROM trust_tenants WHERE pmc_id = %s AND display_name = %s", (pmc.pmc_id, name)
    ).fetchone()[0]


def test_staff_bring_each_persons_history_in_once_and_read_it_newest_first(
    client, conn, pmc, staff
):
    moved = conn.execute("SELECT count(*) FROM pgledger_transfers").fetchone()[0]
    charged = conn.execute("SELECT count(*) FROM trust_charges").fetchone()[0]

    first = ok(bring(client, "tenant history", TENANTS))
    again = ok(bring(client, "tenant history", TENANTS))
    ok(bring(client, "owner history", OWNERS))
    ok(bring(client, "vendor history", VENDORS))
    tenant = ok(client.get(f"/api/history?tenant_id={tenant_named(conn, pmc, 'Tenant 1')}"))
    former = ok(client.get(f"/api/history?tenant_id={tenant_named(conn, pmc, 'Former Tenant')}"))
    owner = ok(client.get(f"/api/history?owner_id={pmc.owners[0].owner_id}"))
    vendor = ok(client.get(f"/api/history?vendor_id={pmc.vendor_id}"))

    assert (first, again) == ({"added": 3, "kept": 0}, {"added": 0, "kept": 3})
    assert tenant == {
        "items": [
            {
                "posted_on": "2025-03-04",
                "type": "Payment",
                "description": "Check 311",
                "amount": "-1200.00",
                "reference": "311",
                "unit": "Apt 1",
                "property": None,
            },
            {
                "posted_on": "2025-03-01",
                "type": "Charge",
                "description": "March rent",
                "amount": "1200.00",
                "reference": None,
                "unit": "Apt 1",
                "property": None,
            },
        ],
        "total": 2,
        "page": 1,
        "per_page": 50,
    }
    # A tenant who left before the switch is brought in to hold their history.
    assert [line["description"] for line in former["items"]] == ["November rent"]
    assert [(line["posted_on"], line["amount"], line["property"]) for line in owner["items"]] == [
        ("2025-03-31", "-850.00", "Property 1"),
        ("2025-03-05", "1200.00", "Property 1"),
    ]
    assert vendor["items"][0]["reference"] == "CHK-1009"
    # History is never posted.
    assert conn.execute("SELECT count(*) FROM pgledger_transfers").fetchone()[0] == moved
    assert conn.execute("SELECT count(*) FROM trust_charges").fetchone()[0] == charged


def test_history_comes_a_page_at_a_time(client, conn, pmc, staff):
    lines = "\n".join(
        f"Vendor 1,2025-01-{day:02d},Payment,Invoice {day},10.00" for day in range(1, 8)
    )
    ok(bring(client, "vendor history", f"Vendor,Date,Type,Description,Amount\n{lines}\n"))

    second = ok(client.get(f"/api/history?vendor_id={pmc.vendor_id}&page=2&per_page=3"))

    assert [line["description"] for line in second["items"]] == [
        "Invoice 4",
        "Invoice 3",
        "Invoice 2",
    ]
    assert second["total"] == 7


@pytest.mark.parametrize(
    ("kind", "content", "says"),
    [
        ("tenant history", "Property,Tenant,Date,Type,Description\n", "line 1: no amount column"),
        (
            "owner history",
            "Owner,Property,Date,Type,Description,Amount\n"
            "Owner 1,Property 2,2025-01-01,Rent,X,1.00\n",
            "line 2: that owner has no account for that property",
        ),
        (
            "vendor history",
            "Vendor,Date,Type,Description,Amount\nVendor 1,2025-01-01," + "T" * 51 + ",X,1.00\n",
            "line 2: the type is longer than 50 characters",
        ),
        (
            "tenant history",
            "Property,Tenant,Date,Type,Description,Amount\n"
            "Property 9,Tenant 1,2025-01-01,Rent,X,1\n",
            "line 2: no property 'Property 9'; import the properties first",
        ),
    ],
)
def test_a_bad_file_is_refused_whole_saying_which_line_and_why(
    client, conn, pmc, staff, kind, content, says
):
    answer = bring(client, kind, content)

    assert answer.status_code == 422
    assert answer.json()["detail"].startswith(says)
    assert conn.execute(
        "SELECT count(*) FROM trust_history_files WHERE pmc_id = %s", (pmc.pmc_id,)
    ).fetchone() == (0,)


def test_a_day_to_come_is_refused(client, conn, pmc, staff):
    tomorrow = (datetime.now(UTC).date() + timedelta(days=1)).isoformat()

    answer = bring(
        client,
        "vendor history",
        f"Vendor,Date,Type,Description,Amount\nVendor 1,{tomorrow},Payment,Early,5.00\n",
    )

    assert answer.status_code == 422
    assert "line 2 is dated" in answer.json()["detail"]
    assert "which has not come yet" in answer.json()["detail"]


def test_only_staff_read_history(client, other_client, conn, app_conn, pmc):
    join_as_tenant(other_client, conn, app_conn, pmc)

    assert client.get(f"/api/history?vendor_id={pmc.vendor_id}").status_code == 401
    assert other_client.get(f"/api/history?vendor_id={pmc.vendor_id}").status_code == 403


def test_history_names_one_person_of_the_company(client, conn, pmc, staff):
    theirs = make_pmc(conn)

    assert client.get("/api/history").status_code == 422
    assert (
        client.get(
            f"/api/history?vendor_id={pmc.vendor_id}&owner_id={pmc.owners[0].owner_id}"
        ).status_code
        == 422
    )
    assert client.get("/api/history?vendor_id=nope").status_code == 422
    assert client.get(f"/api/history?vendor_id={theirs.vendor_id}").status_code == 404
    assert client.get(f"/api/history?owner_id={theirs.owners[0].owner_id}").status_code == 404
    assert client.get(f"/api/history?tenant_id={theirs.owners[0].owner_id}").status_code == 404
