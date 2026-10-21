"""Utility billing through the API (src/pms/api/utilities.py, migration 20261020000096, the
founder's review q021): staff set a property's plan for a utility, its units' square feet and
which utilities each lease is billed for, preview a bill's shares and bill it back, each billed
lease's share a fee charged to it. The database's side is in tests/test_utility_billing.py.
Every name here is synthetic."""

import json
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from helpers import add_tenant, add_unit, enter_bill, make_pmc, open_lease
from people import join_as_owner, join_as_staff

TODAY = datetime.now(UTC).date()
FIRST = TODAY.replace(day=1)
LAST_START = (FIRST - timedelta(days=1)).replace(day=1)
LAST_END = FIRST - timedelta(days=1)
STARTED = (LAST_START - timedelta(days=1)).replace(day=1)
DAYS = (LAST_END - LAST_START).days + 1


@pytest.fixture
def staff(client, conn, app_conn, pmc):
    return join_as_staff(client, conn, app_conn, pmc)


@pytest.fixture
def prop(pmc):
    return pmc.owners[0].property_id


@pytest.fixture
def homes(conn, pmc, prop):
    """Three units of Property 1: two leased since before last month, one vacant."""
    units = [add_unit(conn, pmc.pmc_id, prop, f"Unit {n}") for n in (1, 2, 3)]
    leases = [
        open_lease(
            conn,
            pmc.pmc_id,
            unit,
            STARTED,
            None,
            "1000.00",
            [add_tenant(conn, pmc.pmc_id, prop, f"Tenant {n}")],
        )
        for n, unit in enumerate(units[:2], 1)
    ]
    return units, leases


def post(client, path, **body):
    return client.post(path, json=json.loads(json.dumps(body, default=str)))


def ok(answer, status=201):
    assert answer.status_code == status, answer.text
    return answer.json()


def refused(answer, status, said=None):
    assert answer.status_code == status, answer.text
    if said is not None:
        assert said in str(answer.json()["detail"]), answer.text


def plan(client, prop, method="units", utility="water", starts_on=STARTED, owner_percent="0"):
    return post(
        client,
        f"/api/utilities/properties/{prop}/plans",
        utility=utility,
        starts_on=starts_on,
        method=method,
        owner_percent=owner_percent,
    )


def billed(client, lease, utility="water", value=True, starts_on=STARTED):
    return post(
        client,
        f"/api/utilities/leases/{lease}/billed",
        utility=utility,
        billed=value,
        starts_on=starts_on,
    )


def asked(prop, amount="90.00", utility="water", **more):
    return {
        "property_id": prop,
        "utility": utility,
        "period_start": LAST_START,
        "period_end": LAST_END,
        "amount": amount,
        **more,
    }


def charges(conn, lease):
    return conn.execute(
        "SELECT kind, amount, memo FROM trust_charges WHERE lease_id = %s ORDER BY id", (lease,)
    ).fetchall()


# --- setting it up -----------------------------------------------------------------------------


def test_staff_set_up_a_propertys_utility_billing_and_its_page_shows_it(client, staff, prop, homes):
    units, leases = homes
    page = ok(client.get(f"/api/utilities/properties/{prop}"), 200)
    assert page["property"] == "Property 1"
    assert (page["plans"], page["bills"]) == ([], [])
    assert [(u["unit"], u["square_feet"]) for u in page["units"]] == [
        ("Unit 1", None),
        ("Unit 2", None),
        ("Unit 3", None),
    ]
    assert [(lease["home"], lease["billed"]) for lease in page["leases"]] == [
        ("Tenant 1, Property 1 Unit 1", []),
        ("Tenant 2, Property 1 Unit 2", []),
    ]

    made = ok(plan(client, prop, "square_feet", owner_percent="12.5"))["id"]
    ok(post(client, f"/api/utilities/units/{units[0]}/area", square_feet=750))
    ok(billed(client, leases[0]))
    ok(billed(client, leases[0], "trash"))

    page = ok(client.get(f"/api/utilities/properties/{prop}"), 200)
    assert page["plans"] == [
        {
            "id": made,
            "utility": "water",
            "starts_on": STARTED.isoformat(),
            "method": "square_feet",
            "owner_percent": "12.5",
            "recorded_by": "Staff 1",
        }
    ]
    assert page["units"][0]["square_feet"] == 750
    assert page["leases"][0]["billed"] == ["water", "trash"]


@pytest.mark.parametrize(
    ("path", "body", "status", "said"),
    [
        ("plans", {"utility": "steam", "starts_on": "2026-01-01", "method": "units"}, 422, None),
        ("plans", {"utility": "water", "starts_on": "2026-01-01", "method": "guess"}, 422, None),
        (
            "plans",
            {
                "utility": "water",
                "starts_on": "2026-01-01",
                "method": "units",
                "owner_percent": "101",
            },
            422,
            None,
        ),
        ("area", {"square_feet": 0}, 422, None),
        ("billed", {"utility": "water", "billed": True}, 422, None),
    ],
)
def test_setting_it_up_takes_good_input(client, staff, prop, homes, path, body, status, said):
    units, leases = homes
    url = {
        "plans": f"/api/utilities/properties/{prop}/plans",
        "area": f"/api/utilities/units/{units[0]}/area",
        "billed": f"/api/utilities/leases/{leases[0]}/billed",
    }[path]
    refused(post(client, url, **body), status, said)


def test_a_plan_or_a_lease_change_is_once_a_day_and_of_the_company(
    client, conn, staff, prop, homes
):
    units, leases = homes
    ok(plan(client, prop))
    refused(plan(client, prop, "occupants"), 409, "a water plan from that day already")
    ok(plan(client, prop, "occupants", utility="gas"))
    ok(billed(client, leases[0]))
    refused(billed(client, leases[0], value=False), 409, "water billing changes on that day")

    other = make_pmc(conn)
    elsewhere = add_unit(conn, other.pmc_id, other.owners[0].property_id)
    refused(plan(client, other.owners[0].property_id), 404, "no such property")
    refused(post(client, f"/api/utilities/units/{elsewhere}/area", square_feet=500), 404)
    refused(billed(client, uuid.uuid4()), 404)
    refused(client.get(f"/api/utilities/properties/{uuid.uuid4()}"), 404)


# --- billing it back ---------------------------------------------------------------------------


def test_a_preview_shows_the_shares_and_charges_nothing(client, conn, staff, prop, homes):
    _, leases = homes
    ok(plan(client, prop, owner_percent="10"))
    for lease in leases:
        ok(billed(client, lease))

    shown = ok(post(client, "/api/utilities/preview", **asked(prop)), 200)

    # Three units, last month: the owner's 10% off 90.00 leaves 81.00, a third each 27.00; the
    # vacant unit's 27.00 and the 9.00 stay with the owner.
    assert (shown["method"], shown["owner_percent"]) == ("units", "10")
    assert [(s["home"], s["weight"], s["amount"]) for s in shown["shares"]] == [
        ("Tenant 1, Property 1 Unit 1", str(DAYS), "27.00"),
        ("Tenant 2, Property 1 Unit 2", str(DAYS), "27.00"),
    ]
    assert (shown["charged"], shown["owner_keeps"]) == ("54.00", "36.00")
    assert charges(conn, leases[0]) == []


def test_staff_bill_it_back_and_each_billed_lease_is_charged_its_share(
    client, conn, staff, prop, homes
):
    _, leases = homes
    ok(plan(client, prop))
    ok(billed(client, leases[0]))  # the second lease isn't billed for water

    made = ok(post(client, "/api/utilities/bills", **asked(prop), due_on=FIRST, memo="Meter 7"))

    assert [(s["billed"], s["amount"]) for s in made["shares"]] == [
        (True, "30.00"),
        (False, "0.00"),
    ]
    assert (made["charged"], made["owner_keeps"]) == ("30.00", "60.00")
    memo = f"Water {LAST_START} to {LAST_END}, by unit: Meter 7"
    assert charges(conn, leases[0]) == [("fee", Decimal("30.00"), memo)]
    assert charges(conn, leases[1]) == []
    (bill,) = ok(client.get(f"/api/utilities/properties/{prop}"), 200)["bills"]
    assert (bill["id"], bill["amount"], bill["charged"], bill["memo"]) == (
        made["id"],
        "90.00",
        "30.00",
        "Meter 7",
    )
    # The same period again is refused: no day is billed twice.
    refused(
        post(client, "/api/utilities/bills", **asked(prop), due_on=FIRST),
        409,
        "already covers part of",
    )


def test_a_submetered_bill_reads_each_lease_of_the_property(client, conn, staff, prop, homes):
    _, leases = homes
    ok(plan(client, prop, "submeter"))
    for lease in leases:
        ok(billed(client, lease))
    readings = [{"lease_id": leases[0], "usage": "30"}, {"lease_id": leases[1], "usage": "45.5"}]

    made = ok(
        post(
            client,
            "/api/utilities/bills",
            **asked(prop, "100.00"),
            master_usage="100",
            usage=readings,
            due_on=FIRST,
        )
    )

    assert [(s["usage"], s["amount"]) for s in made["shares"]] == [
        ("30", "30.00"),
        ("45.5", "45.50"),
    ]
    refused(
        post(
            client,
            "/api/utilities/preview",
            **asked(prop, "100.00"),
            master_usage="100",
            usage=[{"lease_id": uuid.uuid4(), "usage": "1"}],
        ),
        422,
        "isn't the property's in the bill's period",
    )
    refused(
        post(
            client,
            "/api/utilities/preview",
            **asked(prop, "100.00"),
            master_usage="100",
            usage=readings + [readings[0]],
        ),
        422,
        "each lease has one reading",
    )
    refused(
        post(
            client,
            "/api/utilities/preview",
            **asked(prop, "100.00"),
            master_usage="70",
            usage=readings,
        ),
        409,
        "more than the master meter",
    )


def test_a_bill_names_a_vendors_bill_of_the_property_once(client, conn, staff, pmc, prop, homes):
    ok(plan(client, prop))
    ours = enter_bill(
        conn, pmc.pmc_id, pmc.vendor_id, pmc.owners[0].account, "W-1", LAST_END, FIRST, "90.00"
    )
    theirs = enter_bill(
        conn, pmc.pmc_id, pmc.vendor_id, pmc.owners[1].account, "W-2", LAST_END, FIRST, "90.00"
    )
    refused(
        post(client, "/api/utilities/bills", **asked(prop), due_on=FIRST, bill_id=theirs),
        422,
        "isn't one of this property's",
    )
    ok(post(client, "/api/utilities/bills", **asked(prop), due_on=FIRST, bill_id=ours))
    later = asked(prop, utility="trash")
    ok(plan(client, prop, utility="trash"))
    refused(
        post(client, "/api/utilities/bills", **later, due_on=FIRST, bill_id=ours),
        409,
        "billed back already",
    )


@pytest.mark.parametrize(
    ("change", "status", "said"),
    [
        ({"utility": "gas"}, 409, "bills no gas back"),
        ({"period_end": "2000-01-01"}, 422, "ends on or after its first day"),
        ({"amount": "0"}, 422, None),
        ({"master_usage": "10"}, 409, "master meter"),
        ({"property_id": "unknown"}, 404, "no such property"),
    ],
)
def test_a_bill_is_one_the_property_bills_back(client, staff, prop, homes, change, status, said):
    ok(plan(client, prop))
    body = asked(prop) | change
    if body["property_id"] == "unknown":
        body["property_id"] = uuid.uuid4()
    refused(post(client, "/api/utilities/preview", **body), status, said)


# --- who may ------------------------------------------------------------------------------------


def test_utility_billing_takes_staff(client, other_client, app_conn, pmc, prop, homes):
    assert client.get(f"/api/utilities/properties/{prop}").status_code == 401
    assert plan(client, prop).status_code == 401
    join_as_owner(other_client, app_conn, pmc)
    assert other_client.get(f"/api/utilities/properties/{prop}").status_code == 403
    assert post(other_client, "/api/utilities/preview", **asked(prop)).status_code == 403
