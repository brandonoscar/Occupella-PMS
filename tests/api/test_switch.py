"""The switching tool (src/pms/api/switching.py): bringing a PMC's roster in from CSV files,
opening a trust bank account with its old balances, the items carried over and the sign-off.
Every file here is synthetic."""

import json
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from helpers import make_pmc
from people import join_as_owner, join_as_staff, join_as_tenant

from pms.api import switching

TODAY = datetime.now(UTC).date()
CUTOVER = "2026-01-01"


@pytest.fixture
def staff(client, conn, app_conn, pmc):
    return join_as_staff(client, conn, app_conn, pmc, name="Switcher 1")


def call(client, path, **body):
    return client.post(f"/api/switching/{path}", json=json.loads(json.dumps(body, default=str)))


def ok(answer, status=200):
    assert answer.status_code == status, answer.text
    return answer.json() if answer.content else None


def refused(answer, status, detail=None):
    assert answer.status_code == status, answer.text
    if detail is not None:
        assert detail in answer.json()["detail"]


def csv(*lines):
    return "\n".join(lines) + "\n"


def imported(client, kind, *lines):
    return call(client, "import", kind=kind, content=csv(*lines))


def roster(client):
    ok(imported(client, "owners", "Name", "Owner A", "Owner B"))
    ok(imported(client, "vendors", "Name", "Vendor A"))
    ok(imported(client, "properties", "Property,Owner", "Property A,Owner A", "Property A,Owner B"))
    ok(imported(client, "units", "Property,Unit", "Property A,Unit 1", "Property A,Unit 2"))


def cutover(client):
    return {item["name"]: item for item in ok(client.get("/api/switching"))}


# --- the roster ------------------------------------------------------------------------------


def test_the_roster_comes_in_and_a_file_sent_again_adds_nothing(client, conn, staff, pmc):
    assert ok(imported(client, "owners", "Name", "Owner A", "Owner B")) == {"added": 2, "kept": 0}
    assert ok(imported(client, "owners", "name", "Owner A")) == {"added": 0, "kept": 1}
    ok(imported(client, "vendors", "Name", "Vendor A"))
    assert ok(
        imported(client, "properties", "Property,Owner", "Property A,Owner A", "Property A,Owner B")
    ) == {"added": 2, "kept": 0}
    assert ok(imported(client, "properties", "property,owner", "Property A,Owner A")) == {
        "added": 0,
        "kept": 1,
    }
    ok(imported(client, "units", "Property,Unit", "Property A,Unit 1", "Property A,Unit 2"))
    assert ok(imported(client, "tenants", "Property,Tenant", "Property A,Tenant A")) == {
        "added": 1,
        "kept": 0,
    }
    leases = imported(
        client,
        "leases",
        "Property,Unit,Tenants,Starts On,Ends On,Monthly Rent",
        "Property A,Unit 1,Tenant A; Tenant B,2025-06-01,,1500.00",
        'Property A,Unit 2,Tenant C,06/01/2025,2026-05-31,"$1,250.00"',
    )
    assert ok(leases) == {"added": 4, "kept": 1}  # two leases, two new tenants; Tenant A kept
    again = imported(
        client,
        "leases",
        "property,unit,tenants,starts_on,monthly_rent",
        "Property A,Unit 1,Tenant A,2025-06-01,1500.00",
    )
    assert ok(again) == {"added": 0, "kept": 2}
    charges = imported(
        client,
        "charges",
        "Property,Unit,Due On,Amount,Kind,Memo",
        "Property A,Unit 1,2025-12-01,1500.00,rent,Rent",
        "Property A,Unit 1,2025-12-06,50.00,fee,Late fee",
    )
    assert ok(charges) == {"added": 2, "kept": 0}
    assert ok(
        imported(
            client,
            "charges",
            "Property,Unit,Due On,Amount,Kind,Memo",
            "Property A,Unit 1,2025-12-01,1500.00,rent,Rent",
        )
    ) == {"added": 0, "kept": 1}

    (owed,) = conn.execute(
        "SELECT sum(c.amount) FROM trust_charges AS c JOIN trust_leases AS l ON l.id = c.lease_id"
        " JOIN trust_units AS u ON u.id = l.unit_id WHERE u.display_name = 'Unit 1'"
        " AND c.pmc_id = %s",
        (pmc.pmc_id,),
    ).fetchone()
    assert str(owed) == "1550.00"
    accounts = conn.execute(
        "SELECT count(*) FROM trust_ledger_accounts AS t JOIN trust_properties AS p"
        " ON p.id = t.property_id WHERE p.display_name = 'Property A' AND t.pmc_id = %s",
        (pmc.pmc_id,),
    ).fetchone()
    assert accounts == (2,)


@pytest.mark.parametrize(
    "kind, lines, why",
    [
        ("owners", [" "], ""),
        ("owners", ["Owner"], "line 1: no name column"),
        ("owners", ["Name", "x" * 201], "line 2: the name is longer than 200 characters"),
        ("owners", ["Name,Note", " ,x"], "line 2: no name"),
        ("properties", ["Property,Owner", "Property Z,Owner Z"], "no owner 'Owner Z'"),
        ("units", ["Property,Unit", "Property Z,Unit 1"], "no property 'Property Z'"),
        (
            "leases",
            [
                "Property,Unit,Tenants,Starts On,Monthly Rent",
                "Property A,Unit 9,Tenant A,2025-06-01,1500.00",
            ],
            "no unit 'Unit 9'",
        ),
        (
            "leases",
            [
                "Property,Unit,Tenants,Starts On,Ends On,Monthly Rent",
                "Property A,Unit 1,Tenant A,2025-06-01,2025-05-01,1500.00",
            ],
            "ends on or after",
        ),
        (
            "leases",
            [
                "Property,Unit,Tenants,Starts On,Monthly Rent",
                "Property A,Unit 1,Tenant A,2025-06-01,0.00",
            ],
            "monthly rent is more than 0",
        ),
        (
            "leases",
            [
                "Property,Unit,Tenants,Starts On,Monthly Rent",
                "Property A,Unit 1,Tenant A,June,1500.00",
            ],
            "line 2: 'June' is not a date",
        ),
        (
            "leases",
            [
                "Property,Unit,Tenants,Starts On,Monthly Rent",
                "Property A,Unit 1,Tenant A,2025-06-01,lots",
            ],
            "'lots' is not an amount",
        ),
        (
            "charges",
            ["Property,Unit,Due On,Amount", "Property A,Unit 1,2025-12-01,10.00"],
            "no lease of that unit had started by 2025-12-01",
        ),
        (
            "charges",
            ["Property,Unit,Due On,Amount,Kind", "Property A,Unit 1,2025-12-01,10.00,toll"],
            "rent or a fee",
        ),
    ],
)
def test_a_file_that_cant_go_in_says_where_and_why_and_adds_nothing(
    client, conn, staff, pmc, kind, lines, why
):
    roster(client)
    answer = imported(client, kind, *lines)

    refused(answer, 422, why or "the file is empty")
    assert conn.execute(
        "SELECT count(*) FROM trust_owners WHERE pmc_id = %s", (pmc.pmc_id,)
    ).fetchone() == (4,)  # Owner 1 and 2 of the fixture, A and B of the roster


def test_a_lease_over_another_is_refused_with_its_line(client, staff, pmc):
    roster(client)
    ok(
        imported(
            client,
            "leases",
            "Property,Unit,Tenants,Starts On,Monthly Rent",
            "Property A,Unit 1,Tenant A,2025-06-01,1500.00",
        )
    )
    refused(
        imported(
            client,
            "leases",
            "Property,Unit,Tenants,Starts On,Monthly Rent",
            "Property A,Unit 1,Tenant B,2025-09-01,1600.00",
        ),
        422,
        "line 2:",
    )


def test_a_rent_charged_twice_is_refused_with_its_line(client, staff, pmc):
    roster(client)
    ok(
        imported(
            client,
            "leases",
            "Property,Unit,Tenants,Starts On,Monthly Rent",
            "Property A,Unit 1,Tenant A,2025-06-01,1500.00",
        )
    )
    ok(
        imported(
            client, "charges", "Property,Unit,Due On,Amount", "Property A,Unit 1,2025-12-01,1500.00"
        )
    )
    refused(
        imported(
            client, "charges", "Property,Unit,Due On,Amount", "Property A,Unit 1,2025-12-01,1400.00"
        ),
        422,
        "line 2:",
    )


def test_properties_name_their_trust_bank_account_when_there_are_several(client, conn, staff, pmc):
    ok(imported(client, "owners", "Name", "Owner A"))
    conn.execute(
        "INSERT INTO trust_bank_accounts (pmc_id, kind, display_name)"
        " VALUES (%s, 'operating', 'Second operating account')",
        (pmc.pmc_id,),
    )
    refused(
        imported(client, "properties", "Property,Owner", "Property A,Owner A"),
        422,
        "name one of the PMC's operating trust bank accounts in a 'bank account' column",
    )
    ok(
        imported(
            client,
            "properties",
            "Property,Owner,Bank Account",
            "Property A,Owner A,Second operating account",
        )
    )


def test_a_file_has_at_most_so_many_rows(client, staff, monkeypatch):
    monkeypatch.setattr(switching, "MAX_ROWS", 1)
    refused(imported(client, "owners", "Name", "Owner A", "Owner B"), 422, "at most 1 rows")


# --- opening balances, items and the sign-off --------------------------------------------------


BALANCES = csv(
    "Kind,Owner,Property,Tenant,Vendor,Amount",
    "owner,Owner 1,Property 1,,,600.00",
    "prepaid rent,,Property 1,Tenant 1,,100.00",
    "vendor,,,,Vendor 1,50.00",
    "fees,,,,,25.00",
)


def open_balances(client, pmc, content=BALANCES, book="775.00", **more):
    body = {
        "bank_account_id": pmc.operating_bank_id,
        "opened_on": CUTOVER,
        "book_cash": book,
        "content": content,
    } | more
    return call(client, "opening-balances", **body)


def test_a_trust_bank_account_opens_with_its_old_balances_and_signs_off(client, staff, pmc):
    assert cutover(client)["Synthetic operating trust account"]["can_open"] is True

    assert ok(open_balances(client, pmc), 201) == {"accounts": 4}
    assert ok(open_balances(client, pmc), 201) == {"accounts": 4}  # the same again: a retry
    items = call(
        client,
        "items",
        bank_account_id=pmc.operating_bank_id,
        content=csv(
            "Date,Amount,Description,Reference",
            "2025-12-30,(200.00),Check to Vendor 1,1041",
            "12/31/2025,75.00,Deposit,",
        ),
    )
    assert ok(items, 201) == {"added": 2, "kept": 0}
    shown = cutover(client)["Synthetic operating trust account"]
    ok(call(client, "sign-off", bank_account_id=pmc.operating_bank_id), 201)
    signed = cutover(client)["Synthetic operating trust account"]

    assert (shown["can_open"], shown["opened_on"], shown["signed_off"]) == (False, CUTOVER, False)
    rows = {r["item"]: (r["detail"], r["amount"]) for r in shown["tie_out"]}
    assert rows["old system's book cash"] == (None, "775.00")
    assert rows["entered by"][0].startswith("Switcher 1 at ")
    assert rows["bank balance to expect at the cutover"][1] == "900.00"
    assert rows["signed off by"] == ("not signed off yet", None)
    assert signed["signed_off"] is True
    assert signed["tie_out"][-1]["detail"].startswith("Switcher 1 at ")
    refused(
        call(client, "sign-off", bank_account_id=pmc.operating_bank_id), 409, "signed off already"
    )
    refused(
        call(
            client,
            "items",
            bank_account_id=pmc.operating_bank_id,
            content=csv("Date,Amount,Description", "2025-12-30,-5.00,Late"),
        ),
        409,
        "line 2: the cutover of trust bank account",
    )


def test_deposits_open_in_the_security_deposit_account_only(client, staff, pmc):
    deposit = csv("Kind,Property,Tenant,Amount", "deposit,Property 1,Tenant 1,1500.00")
    refused(open_balances(client, pmc, content=deposit, book="1500.00"), 422, "line 2:")
    ok(
        open_balances(
            client, pmc, content=deposit, book="1500.00", bank_account_id=pmc.deposit_bank_id
        ),
        201,
    )


@pytest.mark.parametrize(
    "content, book, status, why",
    [
        (BALANCES, "776.00", 409, "add up to 775.00, not the book cash of 776.00"),
        (csv("Kind,Amount", "rent,5.00"), "5.00", 422, "the kind is one of"),
        (csv("Kind,Amount", "fees,5.00", "fees,1.00"), "6.00", 422, "line 3: that account has"),
        (
            csv("Kind,Owner,Property,Amount", "owner,Owner 2,Property 1,5.00"),
            "5.00",
            422,
            "has no account for that property in this trust bank account",
        ),
        (csv("Kind,Amount", "fees,-5.00"), "1.00", 422, "it must be positive"),
        (csv("Kind,Vendor,Amount", "vendor,Vendor Z,5.00"), "5.00", 422, "no vendor 'Vendor Z'"),
        (
            csv("Kind,Property,Tenant,Amount", "prepaid rent,Property 1,Tenant Z,5.00"),
            "5.00",
            422,
            "no tenant 'Tenant Z' at that property",
        ),
    ],
)
def test_opening_balances_that_dont_tie_out_are_refused(
    client, staff, pmc, content, book, status, why
):
    refused(open_balances(client, pmc, content=content, book=book), status, why)
    assert cutover(client)["Synthetic operating trust account"]["can_open"] is True


def test_fees_open_only_in_an_operating_account_and_never_in_the_future(client, staff, pmc):
    refused(
        open_balances(
            client,
            pmc,
            content=csv("Kind,Amount", "fees,5.00"),
            book="5.00",
            bank_account_id=pmc.deposit_bank_id,
        ),
        422,
        "held in an operating",
    )
    refused(
        open_balances(client, pmc, opened_on=TODAY + timedelta(days=1)), 422, "has not come yet"
    )


def test_a_trust_bank_account_with_postings_cant_open(client, conn, staff, pmc):
    from helpers import post

    post(conn, pmc.pmc_id, "rent-1", [(pmc.operating_cash, pmc.owners[0].account, "5.00")])
    assert cutover(client)["Synthetic operating trust account"]["can_open"] is False
    refused(open_balances(client, pmc), 409, "opening balances come first")


def test_items_come_after_the_opening_and_by_the_cutover(client, staff, pmc):
    path = "items"
    body = {"bank_account_id": pmc.operating_bank_id}
    refused(
        call(
            client, path, content=csv("Date,Amount,Description", "2025-12-30,-5.00,Check"), **body
        ),
        409,
        "has no opening balances to carry over to",
    )
    ok(open_balances(client, pmc), 201)
    refused(
        call(
            client, path, content=csv("Date,Amount,Description", "2026-01-02,-5.00,Check"), **body
        ),
        409,
        "issued by the cutover",
    )
    refused(
        call(client, path, content=csv("Date,Amount,Description", "2025-12-30,0.00,Check"), **body),
        422,
        "line 2: an item moved some money",
    )


def test_another_pmcs_trust_bank_account_is_not_found(client, conn, staff, pmc):
    theirs = make_pmc(conn).operating_bank_id
    for answer in (
        open_balances(client, pmc, bank_account_id=theirs),
        call(client, "items", bank_account_id=theirs, content=csv("Date,Amount,Description")),
        call(client, "sign-off", bank_account_id=theirs),
    ):
        refused(answer, 404, "no such trust bank account")


ENDPOINTS = [
    ("get", "/api/switching"),
    ("post", "/api/switching/import"),
    ("post", "/api/switching/opening-balances"),
    ("post", "/api/switching/items"),
    ("post", "/api/switching/sign-off"),
]


@pytest.mark.parametrize("caller", ["nobody", "owner", "tenant"])
@pytest.mark.parametrize(("method", "path"), ENDPOINTS)
def test_only_staff_switch(client, conn, app_conn, pmc, caller, method, path):
    if caller == "owner":
        join_as_owner(client, app_conn, pmc)
    elif caller == "tenant":
        join_as_tenant(client, conn, app_conn, pmc)

    answer = client.get(path) if method == "get" else client.post(path, json={})

    assert answer.status_code == (401 if caller == "nobody" else 403)


@pytest.mark.parametrize(
    ("path", "body"),
    [
        ("import", {"kind": "staff", "content": "Name\nA\n"}),
        ("import", {"kind": "owners", "content": ""}),
        (
            "opening-balances",
            {
                "bank_account_id": str(uuid.uuid4()),
                "opened_on": CUTOVER,
                "book_cash": "0",
                "content": "x",
            },
        ),
        (
            "opening-balances",
            {
                "bank_account_id": str(uuid.uuid4()),
                "opened_on": CUTOVER,
                "book_cash": "1.001",
                "content": "x",
            },
        ),
        ("items", {"bank_account_id": "x", "content": "x"}),
        ("sign-off", {}),
    ],
)
def test_bad_input_is_refused(client, staff, path, body):
    refused(call(client, path, **body), 422)
