"""A unit's details through the API (src/pms/api/unit_details.py, migration 0124): staff keep its
bedrooms, bathrooms, square feet and market rent on its page, and its property's page, the rent
roll, Listings and the public vacancy page show them; the switching tool brings them over from a
units file. Every name and figure is synthetic."""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from helpers import add_tenant, add_unit, make_pmc, open_lease
from people import join_as_owner, join_as_staff, join_as_tenant

TODAY = datetime.now(UTC).date()
NONE = {"bedrooms": None, "bathrooms": None, "square_feet": None, "market_rent": None}
TWO_BED = {"bedrooms": 2, "bathrooms": "1.5", "square_feet": 850, "market_rent": "1250.00"}


@pytest.fixture
def staff(client, conn, app_conn, pmc):
    return join_as_staff(client, conn, app_conn, pmc, name="Manager 1")


@pytest.fixture
def unit(conn, pmc):
    return add_unit(conn, pmc.pmc_id, pmc.owners[0].property_id, "Unit 1")


def ok(answer, status=200):
    assert answer.status_code == status, answer.text
    return answer.json() if answer.content else None


def refused(answer, status, detail=None):
    assert answer.status_code == status, answer.text
    if detail is not None:
        assert detail in str(answer.json()["detail"]), answer.text


def keep(client, unit, **details):
    return client.post(f"/api/units/{unit}/details", json=details)


def test_staff_keep_a_units_details_and_every_page_shows_them(
    client, other_client, conn, staff, pmc, unit
):
    prop = pmc.owners[0].property_id
    leased = add_unit(conn, pmc.pmc_id, prop, "Unit 2")
    tenant = add_tenant(conn, pmc.pmc_id, prop, "Tenant 1")
    open_lease(conn, pmc.pmc_id, leased, TODAY - timedelta(days=30), None, "1400.00", [tenant])

    assert ok(keep(client, unit, bedrooms=2, bathrooms="1.5", square_feet=850, market_rent="1250"))
    assert ok(keep(client, unit, **TWO_BED)) == {"changed": False}  # the same again
    assert ok(keep(client, leased, bedrooms=3, bathrooms=2, market_rent="1500.00"))["changed"]
    # Kept to the cent whatever was typed, so reports print 1250.00; naming who recorded it.
    assert conn.execute(
        "SELECT count(*), min(market_rent::text), min(recorded_by::text)"
        " FROM trust_unit_details WHERE unit_id = %s",
        (unit,),
    ).fetchone() == (1, "1250.00", str(staff.person_id))

    assert ok(client.get(f"/api/units/{unit}"))["details"] == TWO_BED
    page = {u["name"]: u["details"] for u in ok(client.get(f"/api/properties/{prop}"))["units"]}
    assert page == {
        "Unit 1": TWO_BED,
        "Unit 2": {"bedrooms": 3, "bathrooms": "2", "square_feet": None, "market_rent": "1500.00"},
    }
    roll = ok(client.get("/api/leases", params={"show": "current"}))["items"]
    assert [(r["unit"]["name"], r["details"]["market_rent"]) for r in roll] == [
        ("Unit 2", "1500.00")
    ]

    link = ok(
        client.post("/api/leasing/links", json={"unit_id": str(unit), "monthly_rent": "1225.00"}),
        201,
    )["id"]
    ok(client.post(f"/api/leasing/links/{link}/listing", json={}), 201)
    vacancies = ok(client.get("/api/leasing/vacancies"))["items"]
    assert [(v["unit"], v["details"]) for v in vacancies] == [("Unit 1", TWO_BED)]
    listing = ok(other_client.get(f"/api/listings/{pmc.pmc_id}"))["listings"][0]
    assert (listing["bedrooms"], listing["bathrooms"], listing["square_feet"]) == (2, "1.5", 850)
    assert "market_rent" not in listing  # what staff would ask stays with staff

    assert ok(keep(client, unit)) == {"changed": True}  # nothing at all clears them
    assert ok(client.get(f"/api/units/{unit}"))["details"] == NONE


@pytest.mark.parametrize(
    ("details", "why"),
    [
        ({"bedrooms": 21}, "less than or equal to 20"),
        ({"bedrooms": -1}, "greater than or equal to 0"),
        ({"bathrooms": "1.25"}, "bathrooms count in halves"),
        ({"bathrooms": "20.5"}, "less than or equal to 20"),
        ({"square_feet": 0}, "greater than or equal to 1"),
        ({"market_rent": "-1"}, "greater than or equal to 0"),
        ({"market_rent": "1200.005"}, "decimal places"),
        ({"bedrooms": "two"}, "valid integer"),
    ],
)
def test_details_out_of_range_are_refused_in_words(client, conn, staff, pmc, unit, details, why):
    refused(keep(client, unit, **details), 422, why)
    assert conn.execute(
        "SELECT count(*) FROM trust_unit_details WHERE unit_id = %s", (unit,)
    ).fetchone() == (0,)


def test_only_a_unit_of_the_company(client, conn, staff, pmc):
    other = make_pmc(conn)
    theirs = add_unit(conn, other.pmc_id, other.owners[0].property_id, "Theirs")
    refused(keep(client, theirs, **TWO_BED), 404, "no such unit")
    refused(keep(client, uuid.uuid4(), **TWO_BED), 404, "no such unit")


@pytest.mark.parametrize("caller", ["nobody", "owner", "tenant"])
def test_only_staff_keep_them(client, conn, app_conn, pmc, unit, caller):
    if caller == "owner":
        join_as_owner(client, app_conn, pmc)
    elif caller == "tenant":
        join_as_tenant(client, conn, app_conn, pmc)
    assert keep(client, unit, **TWO_BED).status_code == (401 if caller == "nobody" else 403)


def test_staff_whose_roles_leave_out_setup_read_them_but_keep_none(
    client, other_client, conn, app_conn, staff, pmc, unit
):
    clerk = join_as_staff(other_client, conn, app_conn, pmc, name="Clerk 1", roles=[])
    role = ok(client.post("/api/custom-roles", json={"name": "Bills", "areas": ["bills"]}), 201)
    ok(
        client.post(
            f"/api/custom-roles/{role['id']}/staff",
            json={"staff_id": str(clerk.person_id), "granted": True},
        ),
        204,
    )
    refused(keep(other_client, unit, **TWO_BED), 403, "your roles don't let you change")
    assert ok(other_client.get(f"/api/units/{unit}"))["details"] == NONE


def imported(client, *lines):
    return client.post(
        "/api/switching/import", json={"kind": "units", "content": "\n".join(lines) + "\n"}
    )


def test_a_units_file_brings_their_details(client, conn, staff, pmc):
    prop = conn.execute(
        "SELECT display_name FROM trust_properties WHERE id = %s", (pmc.owners[0].property_id,)
    ).fetchone()[0]
    header = "Property,Unit,Bedrooms,Bathrooms,Square feet,Market rent"
    ok(imported(client, header, f'{prop},Unit 7,2,1.5,850,"$1,250.00"', f"{prop},Unit 8,,,,"))
    details = {
        name: ok(client.get(f"/api/units/{unit}"))["details"]
        for unit, name in conn.execute(
            "SELECT id, display_name FROM trust_units WHERE pmc_id = %s"
            " AND display_name IN ('Unit 7', 'Unit 8')",
            (pmc.pmc_id,),
        ).fetchall()
    }
    assert details == {"Unit 7": TWO_BED, "Unit 8": NONE}
    # Again, the same details write nothing.
    ok(imported(client, header, f"{prop},Unit 7,2,1.5,850,1250.00"))
    assert conn.execute(
        "SELECT count(*) FROM trust_unit_details WHERE pmc_id = %s", (pmc.pmc_id,)
    ).fetchone() == (1,)
    refused(
        imported(client, header, f"{prop},Unit 9,2,1.25,850,1250.00"),
        422,
        "line 2: the bathrooms isn't one the PMS can keep",
    )
