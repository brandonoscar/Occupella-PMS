"""Move-in and move-out inspections (src/pms/api/inspections.py): staff record a lease's
inspection room by room and see its inspections; the report lays the latest two side by side."""

import uuid
from datetime import date

import pytest
from helpers import add_unit, make_pmc, open_lease
from people import join_as_staff, join_as_tenant, tenant_of


@pytest.fixture
def staff(client, conn, app_conn, pmc):
    return join_as_staff(client, conn, app_conn, pmc)


@pytest.fixture
def lease(conn, pmc):
    unit = add_unit(conn, pmc.pmc_id, pmc.owners[0].property_id, "Unit 1")
    return open_lease(
        conn,
        pmc.pmc_id,
        unit,
        date(2026, 1, 1),
        date(2026, 6, 30),
        "1500.00",
        [tenant_of(conn, pmc)],
    )


def ok(answer, status=200):
    assert answer.status_code == status, answer.text
    return answer.json() if answer.content else None


def refused(answer, status, detail=None):
    assert answer.status_code == status, answer.text
    if detail is not None:
        assert detail in str(answer.json()["detail"]), answer.text


def inspection(lease, kind="move-in", on="2026-01-01", **change):
    return {
        "lease_id": str(lease),
        "kind": kind,
        "inspected_on": on,
        "items": [
            {"room": " Kitchen ", "condition": "good"},
            {"room": "Bathroom", "condition": "fair", "note": " Chipped tile "},
        ],
    } | change


def test_staff_record_inspections_and_download_them_side_by_side(client, conn, pmc, staff, lease):
    ok(client.post("/api/inspections", json=inspection(lease, notes="Two keys")), 201)
    ok(
        client.post(
            "/api/inspections",
            json=inspection(
                lease,
                "move-out",
                "2026-06-30",
                items=[{"room": "kitchen", "condition": "damaged", "note": "Burn"}],
            ),
        ),
        201,
    )

    found = ok(client.get("/api/inspections", params={"lease_id": str(lease)}))
    assert [(i["kind"], i["inspected_on"], i["recorded_by"]) for i in found] == [
        ("move-out", "2026-06-30", "Staff 1"),
        ("move-in", "2026-01-01", "Staff 1"),
    ]
    assert found[1]["notes"] == "Two keys"
    assert found[1]["items"] == [
        {"room": "Kitchen", "condition": "good", "note": None},
        {"room": "Bathroom", "condition": "fair", "note": "Chipped tile"},
    ]

    answer = client.get(
        "/api/reports/trust_report_inspection", params={"arg": str(lease), "format": "csv"}
    )
    assert answer.status_code == 200, answer.text
    assert "room,Kitchen,good,damaged,true,move-out: Burn" in answer.text
    assert "room,Bathroom,fair,,,move-in: Chipped tile" in answer.text


def test_only_the_pmcs_staff_inspect_its_leases(
    client, other_client, conn, app_conn, pmc, staff, lease
):
    refused(other_client.get("/api/inspections", params={"lease_id": str(lease)}), 401)
    refused(other_client.post("/api/inspections", json=inspection(lease)), 401)
    join_as_tenant(other_client, conn, app_conn, pmc)
    refused(
        other_client.post("/api/inspections", json=inspection(lease)), 403, "only the PMC's staff"
    )

    other = make_pmc(conn)
    other_client.post("/api/sign-out", json={})
    join_as_staff(other_client, conn, app_conn, other, name="Staff 2")
    refused(other_client.post("/api/inspections", json=inspection(lease)), 404, "no such lease")
    refused(
        other_client.get("/api/inspections", params={"lease_id": str(lease)}), 404, "no such lease"
    )
    refused(
        client.get("/api/inspections", params={"lease_id": str(uuid.uuid4())}), 404, "no such lease"
    )


@pytest.mark.parametrize(
    "change",
    [
        {"kind": "mid-lease"},
        {"inspected_on": "soon"},
        {"items": []},
        {"items": [{"room": " ", "condition": "good"}]},
        {"items": [{"room": "Kitchen", "condition": "sparkling"}]},
        {
            "items": [
                {"room": "Kitchen", "condition": "good"},
                {"room": "KITCHEN", "condition": "fair"},
            ]
        },
        {"items": [{"room": f"Room {n}", "condition": "good"} for n in range(101)]},
        {"notes": "x" * 2001},
    ],
)
def test_an_inspection_names_each_room_once_in_a_known_condition(
    client, conn, pmc, staff, lease, change
):
    refused(client.post("/api/inspections", json=inspection(lease, **change)), 422)
    assert conn.execute(
        "SELECT count(*) FROM trust_inspections WHERE pmc_id = %s", (pmc.pmc_id,)
    ).fetchone() == (0,)
