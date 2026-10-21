"""A report on screen and narrowed (src/pms/api/reports.py, ADR 0006 step 11): any report's
rows as text to preview, and a report narrowed to a property group, one property or an owner's
properties (the general ledger and trial balance too, by the property of each account), in the
preview and every download alike. The PMC still comes from the session."""

import csv
import io
from datetime import UTC, datetime, timedelta

import pytest
from helpers import add_tenant, add_unit, make_pmc, open_lease, transfer_batch
from people import join_as_owner, join_as_staff

from pms.api import reports

TODAY = datetime.now(UTC).date()
ROLL = "/api/reports/trust_report_rent_roll"


@pytest.fixture
def staff(client, conn, app_conn, pmc):
    return join_as_staff(client, conn, app_conn, pmc)


@pytest.fixture
def leased(conn, pmc):
    """Unit A of Property 1 and Unit B of Property 2, each leased since a month ago."""
    for owner, unit, tenant in ((0, "Unit A", "Tenant A"), (1, "Unit B", "Tenant B")):
        prop = pmc.owners[owner].property_id
        open_lease(
            conn,
            pmc.pmc_id,
            add_unit(conn, pmc.pmc_id, prop, unit),
            TODAY - timedelta(days=30),
            None,
            "1000.00",
            [add_tenant(conn, pmc.pmc_id, prop, tenant)],
        )


def ok(answer, status=200):
    assert answer.status_code == status, answer.text
    return answer.json() if "json" in answer.headers.get("content-type", "") else answer.content


def column(found, name):
    at = found["columns"].index(name)
    return [row[at] for row in found["rows"]]


def test_a_report_previews_as_text(client, staff, pmc, leased):
    found = ok(client.get(ROLL, params={"arg": str(TODAY), "format": "json"}))

    assert found["columns"][:4] == ["line", "item", "property", "unit"]
    assert found["total_rows"] == len(found["rows"])
    # Every cell is text, as CSV writes it: money keeps its cents, an empty cell is "".
    assert all(isinstance(cell, str) for row in found["rows"] for cell in row)
    assert {"Property 1", "Property 2"} <= set(column(found, "property"))
    assert "1000.00" in column(found, "monthly_rent")
    # The same rows as the CSV download.
    csv_rows = list(
        csv.reader(io.StringIO(ok(client.get(ROLL, params={"arg": str(TODAY)})).decode()))
    )
    assert csv_rows == [found["columns"], *found["rows"]]


def test_a_preview_sends_at_most_its_first_rows(client, staff, pmc, leased, monkeypatch):
    monkeypatch.setattr(reports, "PREVIEW_ROWS", 2)

    found = ok(client.get(ROLL, params={"arg": str(TODAY), "format": "json"}))

    assert len(found["rows"]) == 2
    assert found["total_rows"] > 2


def test_a_report_narrowed_to_a_property_keeps_its_rows_and_says_so(client, staff, pmc, leased):
    one = str(pmc.owners[0].property_id)
    whole = ok(client.get(ROLL, params={"arg": str(TODAY), "format": "json"}))

    found = ok(client.get(ROLL, params={"arg": str(TODAY), "format": "json", "property": one}))

    assert set(column(found, "property")) == {"", "Property 1"}
    assert "Property 2" not in column(found, "property")
    # The header lines stay; the lines across all properties (the totals) are left out, and a
    # last line says what it was narrowed to.
    first = column(whole, "property").index("Property 1")
    assert found["rows"][:first] == whole["rows"][:first]
    assert column(found, "item")[-1] == (
        "narrowed to Property 1: lines across all properties left out"
    )
    assert len(found["rows"]) < len(whole["rows"])
    assert found["title"].endswith(", Property 1")
    # The download is narrowed the same way.
    narrowed = list(
        csv.reader(
            io.StringIO(ok(client.get(ROLL, params={"arg": str(TODAY), "property": one})).decode())
        )
    )
    assert narrowed == [found["columns"], *found["rows"]]


def test_a_report_narrowed_to_a_group_keeps_its_properties(client, staff, pmc, leased):
    group = ok(
        client.post(
            "/api/property-groups",
            json={"name": "North", "property_ids": [str(pmc.owners[1].property_id)]},
        ),
        201,
    )["id"]

    found = ok(client.get(ROLL, params={"arg": str(TODAY), "format": "json", "group": group}))

    assert set(column(found, "property")) == {"", "Property 2"}
    assert column(found, "item")[-1].startswith("narrowed to North:")


def test_a_report_with_no_property_is_not_narrowed(client, staff, pmc):
    # The balance sheet is by trust bank account: the cash in one is held for every property.
    refused = client.get(
        "/api/reports/trust_report_balance_sheet",
        params={"arg": str(TODAY), "format": "json", "property": str(pmc.owners[0].property_id)},
    )

    assert refused.status_code == 422
    assert refused.json()["detail"] == "this report is not by property; choose All properties"


def test_a_report_narrowed_to_an_owner_keeps_their_properties(client, staff, pmc, leased):
    found = ok(
        client.get(
            ROLL,
            params={"arg": str(TODAY), "format": "json", "owner": str(pmc.owners[1].owner_id)},
        )
    )

    assert set(column(found, "property")) == {"", "Property 2"}
    assert column(found, "item")[-1] == (
        "narrowed to Owner 2's properties: lines across all properties left out"
    )
    # With a group too: the owner's properties in the group, here none of them.
    group = ok(
        client.post(
            "/api/property-groups",
            json={"name": "South", "property_ids": [str(pmc.owners[0].property_id)]},
        ),
        201,
    )["id"]
    both = ok(
        client.get(
            ROLL,
            params={
                "arg": str(TODAY),
                "format": "json",
                "owner": str(pmc.owners[1].owner_id),
                "group": group,
            },
        )
    )
    assert set(column(both, "property")) == {""}
    assert both["title"].endswith(", South, Owner 2's properties")


@pytest.mark.parametrize(
    ("report", "args"),
    [
        ("trust_report_trial_balance", lambda: [str(TODAY)]),
        ("trust_report_general_ledger", lambda: [str(TODAY - timedelta(days=60)), str(TODAY)]),
    ],
)
def test_the_ledger_reports_narrow_to_a_propertys_accounts(
    client, conn, staff, pmc, leased, report, args
):
    """The general ledger and trial balance (migration 0101): an owner's property account and a
    tenant's accounts are their property's; cash, vendors and the company's fees are no one
    property's and are left out with the totals."""
    transfer_batch(
        conn,
        [
            (pmc.operating_cash, pmc.owners[0].account, "100.00"),
            (pmc.operating_cash, pmc.owners[1].account, "50.00"),
            (pmc.operating_cash, pmc.prepaid_rent, "10.00"),
        ],
        datetime.now(UTC) - timedelta(days=1),
    )
    path = f"/api/reports/{report}"
    one = str(pmc.owners[0].property_id)
    whole = ok(client.get(path, params={"arg": args(), "format": "json"}))
    found = ok(client.get(path, params={"arg": args(), "format": "json", "property": one}))

    assert {p for p in column(whole, "property") if p} >= {"Property 1", "Property 2"}
    kept = [
        (item, account, prop)
        for item, account, prop in zip(
            column(found, "item"), column(found, "account"), column(found, "property"), strict=True
        )
    ]
    assert {prop for _, _, prop in kept} == {"", "Property 1"}
    # The heading lines (the company, the day or days) and the property's accounts stay.
    assert [item for item, _, prop in kept if not prop][:2] in (["PMC", "as of"], ["PMC", "period"])
    assert all(
        account.startswith(
            (
                "owner_property: Owner 1 / Property 1",
                "prepaid_rent: Tenant 1",
                "tenant_deposit: Tenant 1",
                "prepaid_rent: Tenant A",
                "tenant_deposit: Tenant A",
            )
        )
        for _, account, prop in kept
        if prop
    )
    assert not any(account == "book cash" for _, account, _ in kept)


def test_another_pmcs_group_or_property_narrows_nothing_of_theirs(client, conn, staff, pmc):
    theirs = make_pmc(conn)

    refused = client.get(
        ROLL,
        params={"arg": str(TODAY), "format": "json", "property": str(theirs.owners[0].property_id)},
    )

    assert refused.status_code == 422
    assert refused.json()["detail"] == "no such property group or property"
    assert (
        client.get(
            ROLL,
            params={"arg": str(TODAY), "group": "00000000-0000-0000-0000-000000000000"},
        ).status_code
        == 422
    )
    their_owner = client.get(
        ROLL, params={"arg": str(TODAY), "owner": str(theirs.owners[0].owner_id)}
    )
    assert (their_owner.status_code, their_owner.json()["detail"]) == (422, "no such owner")


def test_only_staff_preview_a_report(client, other_client, conn, app_conn, pmc):
    assert other_client.get(ROLL, params={"arg": str(TODAY), "format": "json"}).status_code == 401
    join_as_owner(other_client, app_conn, pmc)
    assert other_client.get(ROLL, params={"arg": str(TODAY), "format": "json"}).status_code == 403


@pytest.mark.parametrize(
    "params",
    [
        {"arg": "soon", "format": "json"},
        {"arg": str(TODAY), "format": "html"},
        {"arg": str(TODAY), "format": "json", "group": "north"},
        {"arg": str(TODAY), "format": "json", "owner": "owner 1"},
        {"arg": str(TODAY), "format": "json", "property": "1"},
    ],
)
def test_a_bad_preview_is_refused(client, staff, params):
    assert client.get(ROLL, params=params).status_code == 422
