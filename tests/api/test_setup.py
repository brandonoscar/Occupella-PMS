"""Staff set up their PMC's books through the API (src/pms/api/setup.py): trust bank accounts,
owners, properties with their owner accounts, units, tenants, vendors, management agreements,
late fee terms and leases already in place."""

import json
from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace

import pytest
from people import join_as_owner, join_as_staff, join_as_tenant

from pms.api.setup import SCHEDULE_E

TODAY = datetime.now(UTC).date()
LATER = TODAY + timedelta(days=30)


@pytest.fixture
def blank(conn):
    """A PMC with nothing set up yet."""
    return SimpleNamespace(
        pmc_id=conn.execute(
            "INSERT INTO trust_pmcs (display_name) VALUES ('Blank PMC') RETURNING pmc_id"
        ).fetchone()[0]
    )


@pytest.fixture
def staff(client, conn, app_conn, blank):
    return join_as_staff(client, conn, app_conn, blank)


def post(client, path, **body):
    # Ids from the database helpers are UUIDs; JSON carries them as text.
    return client.post(f"/api/setup/{path}", json=json.loads(json.dumps(body, default=str)))


def made(answer):
    assert answer.status_code == 201, answer.text
    return answer.json()["id"]


def build(client):
    """A small PMC set up through the API."""
    books = SimpleNamespace()
    books.operating = made(post(client, "bank-accounts", kind="operating", name="Operating trust"))
    books.deposits = made(
        post(client, "bank-accounts", kind="security_deposit", name="Deposit trust")
    )
    books.owner = made(post(client, "owners", name="Owner A"))
    books.property = made(
        post(
            client,
            "properties",
            name="Property A",
            owner_id=books.owner,
            bank_account_id=books.operating,
        )
    )
    books.unit = made(post(client, "units", property_id=books.property, name="Unit 1"))
    books.tenant = made(post(client, "tenants", property_id=books.property, name="Tenant A"))
    # A vendor comes from Vendors (src/pms/api/vendors.py).
    books.vendor = made(client.post("/api/vendors", json={"name": "Vendor A"}))
    (books.account,) = [a["account"] for a in client.get("/api/setup").json()["accounts"]]
    return books


def refused(answer, status, detail=None):
    assert answer.status_code == status, answer.text
    if detail is not None:
        assert answer.json()["detail"] == detail


def test_staff_set_up_a_pmc_from_nothing(client, conn, staff, blank):
    books = build(client)
    agreement = post(
        client,
        "agreements",
        account=books.account,
        starts_on=str(TODAY),
        fee_percent="8.5",
        minimum_fee="50.00",
        reserve="250.00",
        approval_limit="500.00",
    )
    late_fees = post(
        client,
        "late-fees",
        property_id=books.property,
        starts_on=str(TODAY),
        grace_days=5,
        flat_fee="50.00",
        maximum="100.00",
    )
    nsf_fee = post(
        client, "nsf-fees", property_id=books.property, starts_on=str(TODAY), fee="35.00"
    )
    lease = post(
        client,
        "leases",
        unit_id=books.unit,
        starts_on=str(TODAY),
        monthly_rent="1500.00",
        tenant_ids=[books.tenant],
    )

    seen = client.get("/api/setup").json()

    for answer in (agreement, late_fees, nsf_fee, lease):
        made(answer)
    assert [(b["name"], b["kind"]) for b in seen["bank_accounts"]] == [
        ("Deposit trust", "security_deposit"),
        ("Operating trust", "operating"),
    ]
    assert [o["name"] for o in seen["owners"]] == ["Owner A"]
    assert [v["name"] for v in seen["vendors"]] == ["Vendor A"]
    assert [(t["name"], t["property"]) for t in seen["tenants"]] == [("Tenant A", "Property A")]
    (prop,) = seen["properties"]
    assert prop["late_fees"] == {
        "starts_on": str(TODAY),
        "grace_days": 5,
        "flat_fee": "50.00",
        "percent": "0",
        "maximum": "100.00",
    }
    assert prop["nsf_fee"] == {"starts_on": str(TODAY), "fee": "35.00"}
    (unit,) = prop["units"]
    assert unit["name"] == "Unit 1"
    assert [
        (x["starts_on"], x["ends_on"], x["monthly_rent"], x["tenants"]) for x in unit["leases"]
    ] == [(str(TODAY), None, "1500.00", ["Tenant A"])]
    (account,) = seen["accounts"]
    assert (account["owner"], account["property"], account["bank_account"]) == (
        "Owner A",
        "Property A",
        "Operating trust",
    )
    assert account["agreement"] == {
        "starts_on": str(TODAY),
        "fee_percent": "8.5",
        "minimum_fee": "50.00",
        "flat_fee": "0",
        "leasing_fee_percent": "0",
        "reserve": "250.00",
        "approval_limit": "500.00",
    }
    # Each bank account has its cash, the operating one the PMC's fees, and the owner account
    # sits in the operating one.
    assert sorted(
        conn.execute(
            "SELECT b.kind, a.kind FROM trust_ledger_accounts AS a"
            " JOIN trust_bank_accounts AS b ON b.id = a.bank_account_id WHERE a.pmc_id = %s",
            (blank.pmc_id,),
        ).fetchall()
    ) == [
        ("operating", "bank_cash"),
        ("operating", "owner_property"),
        ("operating", "pmc_income"),
        ("security_deposit", "bank_cash"),
    ]


def test_each_write_names_the_staff_member_in_the_audit_log(client, conn, staff):
    owner = made(post(client, "owners", name="Owner A"))

    assert conn.execute(
        "SELECT staff_id FROM trust_audit_log WHERE table_name = 'trust_owners'"
        " AND row_data ->> 'id' = %s",
        (owner,),
    ).fetchone() == (staff.person_id,)


def test_a_property_can_have_another_owner_once(client, staff):
    books = build(client)
    other = made(post(client, "owners", name="Owner B"))
    path = f"properties/{books.property}/owners"

    added = post(client, path, owner_id=other, bank_account_id=books.operating)
    again = post(client, path, owner_id=other, bank_account_id=books.operating)

    assert made(added)
    refused(again, 409, "this owner has an account for this property in that bank account already")
    assert [(a["owner"], a["property"]) for a in client.get("/api/setup").json()["accounts"]] == [
        ("Owner A", "Property A"),
        ("Owner B", "Property A"),
    ]


def test_an_owners_money_is_held_only_in_an_operating_trust_account(client, staff):
    books = build(client)

    answer = post(
        client,
        "properties",
        name="Property B",
        owner_id=books.owner,
        bank_account_id=books.deposits,
    )

    refused(answer, 409)
    assert "belong in the operating trust bank account" in answer.json()["detail"]
    assert [p["name"] for p in client.get("/api/setup").json()["properties"]] == ["Property A"]


def test_terms_start_on_a_day_and_show_once_in_force(client, staff):
    books = build(client)
    earlier = TODAY - timedelta(days=60)
    terms = {"account": books.account, "fee_percent": "8"}
    fees = {"property_id": books.property, "grace_days": 3, "percent": "5"}

    made(post(client, "agreements", starts_on=str(earlier), **terms))
    made(post(client, "agreements", starts_on=str(LATER), **(terms | {"fee_percent": "9"})))
    made(post(client, "late-fees", starts_on=str(earlier), **fees))
    made(post(client, "late-fees", starts_on=str(LATER), **(fees | {"grace_days": 10})))
    nsf = {"property_id": books.property}
    made(post(client, "nsf-fees", starts_on=str(earlier), fee="25.00", **nsf))
    made(post(client, "nsf-fees", starts_on=str(LATER), fee="0", **nsf))
    same_agreement = post(client, "agreements", starts_on=str(LATER), **terms)
    same_fees = post(client, "late-fees", starts_on=str(LATER), **fees)
    same_nsf = post(client, "nsf-fees", starts_on=str(LATER), fee="40.00", **nsf)
    seen = client.get("/api/setup").json()

    refused(same_agreement, 409, "this account has terms starting that day already")
    refused(same_fees, 409, "this property has late fee terms starting that day already")
    refused(same_nsf, 409, "this property has an NSF fee starting that day already")
    assert seen["accounts"][0]["agreement"]["fee_percent"] == "8"
    assert seen["properties"][0]["late_fees"]["grace_days"] == 3
    assert seen["properties"][0]["nsf_fee"] == {"starts_on": str(earlier), "fee": "25.00"}


def test_an_agreement_is_only_for_an_owners_account_of_the_pmc(client, conn, staff, blank):
    build(client)
    cash = conn.execute(
        "SELECT ledger_account_id FROM trust_ledger_accounts WHERE pmc_id = %s"
        " AND kind = 'bank_cash' LIMIT 1",
        (blank.pmc_id,),
    ).fetchone()[0]

    for account in (cash, "not-an-account"):
        refused(
            post(client, "agreements", account=account, starts_on=str(TODAY)),
            404,
            "no such owner account",
        )


def test_a_lease_is_for_tenants_of_the_units_property_and_never_overlaps(client, staff):
    books = build(client)
    elsewhere = made(
        post(
            client,
            "properties",
            name="Property B",
            owner_id=books.owner,
            bank_account_id=books.operating,
        )
    )
    stranger = made(post(client, "tenants", property_id=elsewhere, name="Tenant B"))
    lease = {"unit_id": books.unit, "monthly_rent": "1200.00", "tenant_ids": [books.tenant]}

    ended = post(
        client,
        "leases",
        starts_on=str(TODAY - timedelta(days=400)),
        ends_on=str(TODAY - timedelta(days=1)),
        **lease,
    )
    current = post(client, "leases", starts_on=str(TODAY), **lease)
    overlapping = post(client, "leases", starts_on=str(LATER), **lease)
    wrong_tenant = post(
        client, "leases", starts_on=str(LATER), **(lease | {"tenant_ids": [stranger]})
    )
    backwards = post(client, "leases", starts_on=str(LATER), ends_on=str(TODAY), **lease)
    shown = client.get("/api/setup").json()["properties"]

    assert made(ended) and made(current)
    refused(overlapping, 409)
    assert "would overlap" in overlapping.json()["detail"]
    refused(
        wrong_tenant,
        422,
        "every tenant on a lease must be a tenant of the unit's property",
    )
    refused(backwards, 422, "a lease ends on or after the day it starts")
    # Only leases that haven't ended show.
    unit = next(p for p in shown if p["name"] == "Property A")["units"][0]
    assert [x["starts_on"] for x in unit["leases"]] == [str(TODAY)]


def test_staff_see_only_their_own_pmc(client, conn, staff, pmc):
    conn.execute(
        "INSERT INTO trust_expense_accounts (pmc_id, name, schedule_e_line)"
        " VALUES (%s, 'Repairs', 14)",
        (pmc.pmc_id,),
    )

    seen = client.get("/api/setup").json()

    assert seen == {
        "bank_accounts": [],
        "owners": [],
        "properties": [],
        "accounts": [],
        "tenants": [],
        "vendors": [],
        "expense_accounts": [],
        # Schedule E's expense lines are every PMC's.
        "schedule_e_lines": [{"line": n, "label": label} for n, label in SCHEDULE_E.items()],
    }


# Each case names one record of another PMC (`o`) among this PMC's own (`b`).
OTHER_PMCS = [
    (
        "properties",
        lambda o, b: {
            "name": "P",
            "owner_id": o.owners[0].owner_id,
            "bank_account_id": b.operating,
        },
        "no such owner",
    ),
    (
        "properties",
        lambda o, b: {"name": "P", "owner_id": b.owner, "bank_account_id": o.operating_bank_id},
        "no such bank account",
    ),
    (
        "units",
        lambda o, b: {"property_id": o.owners[0].property_id, "name": "U"},
        "no such property",
    ),
    (
        "tenants",
        lambda o, b: {"property_id": o.owners[0].property_id, "name": "T"},
        "no such property",
    ),
    (
        "late-fees",
        lambda o, b: {
            "property_id": o.owners[0].property_id,
            "starts_on": str(TODAY),
            "grace_days": 1,
        },
        "no such property",
    ),
    (
        "nsf-fees",
        lambda o, b: {"property_id": o.owners[0].property_id, "starts_on": str(TODAY), "fee": "1"},
        "no such property",
    ),
    (
        "agreements",
        lambda o, b: {"account": o.owners[0].account, "starts_on": str(TODAY)},
        "no such owner account",
    ),
    (
        "leases",
        lambda o, b: {
            "unit_id": o.unit,
            "starts_on": str(TODAY),
            "monthly_rent": "1",
            "tenant_ids": [b.tenant],
        },
        "no such unit",
    ),
]


@pytest.mark.parametrize(("path", "body", "detail"), OTHER_PMCS)
def test_another_pmcs_records_are_not_found(client, conn, staff, pmc, path, body, detail):
    """Every id in a request is looked up in the session's PMC only."""
    books = build(client)
    other = SimpleNamespace(**vars(pmc))
    other.unit = conn.execute(
        "INSERT INTO trust_units (pmc_id, property_id, display_name) VALUES (%s, %s, 'U')"
        " RETURNING id",
        (pmc.pmc_id, pmc.owners[0].property_id),
    ).fetchone()[0]

    refused(post(client, path, **body(other, books)), 404, detail)


def test_another_pmcs_property_takes_no_owner(client, staff, pmc):
    books = build(client)

    refused(
        post(
            client,
            f"properties/{pmc.owners[0].property_id}/owners",
            owner_id=books.owner,
            bank_account_id=books.operating,
        ),
        404,
        "no such property",
    )


ENDPOINTS = [
    ("get", "/api/setup"),
    ("post", "/api/setup/bank-accounts"),
    ("post", "/api/setup/owners"),
    ("post", "/api/setup/properties"),
    ("post", "/api/setup/properties/00000000-0000-0000-0000-000000000000/owners"),
    ("post", "/api/setup/units"),
    ("post", "/api/setup/tenants"),
    ("post", "/api/setup/agreements"),
    ("post", "/api/setup/late-fees"),
    ("post", "/api/setup/nsf-fees"),
    ("post", "/api/setup/leases"),
]


@pytest.mark.parametrize("caller", ["nobody", "owner", "tenant"])
@pytest.mark.parametrize(("method", "path"), ENDPOINTS)
def test_only_staff_set_up_the_books(client, conn, app_conn, pmc, caller, method, path):
    if caller == "owner":
        join_as_owner(client, app_conn, pmc)
    elif caller == "tenant":
        join_as_tenant(client, conn, app_conn, pmc)

    answer = client.get(path) if method == "get" else client.post(path, json={"name": "X"})

    assert answer.status_code == (401 if caller == "nobody" else 403)


@pytest.mark.parametrize(
    ("path", "body"),
    [
        ("bank-accounts", {"kind": "checking", "name": "X"}),
        ("bank-accounts", {"kind": "operating", "name": "  "}),
        ("owners", {"name": ""}),
        ("owners", {"name": "N" * 201}),
        ("units", {"property_id": "not-a-uuid", "name": "U"}),
        ("agreements", {"account": "a", "starts_on": "2026-10-12", "fee_percent": "100.01"}),
        ("agreements", {"account": "a", "starts_on": "2026-10-12", "reserve": "-1"}),
        ("agreements", {"account": "a", "starts_on": "2026-10-12", "flat_fee": "1.001"}),
        ("agreements", {"account": "", "starts_on": "2026-10-12"}),
        ("late-fees", {"property_id": str(date.min), "starts_on": "2026-10-12", "grace_days": 366}),
        ("nsf-fees", {"property_id": str(date.min), "starts_on": "2026-10-12", "fee": "-1"}),
        ("nsf-fees", {"property_id": str(date.min), "starts_on": "2026-10-12", "fee": "35.001"}),
        ("nsf-fees", {"property_id": str(date.min), "starts_on": "2026-10-12"}),
        (
            "leases",
            {"unit_id": "x", "starts_on": "2026-10-12", "monthly_rent": "0", "tenant_ids": []},
        ),
        (
            "leases",
            {"unit_id": "x", "starts_on": "not a day", "monthly_rent": "10", "tenant_ids": ["x"]},
        ),
    ],
)
def test_bad_input_is_refused(client, staff, path, body):
    refused(post(client, path, **body), 422)


def test_a_name_is_kept_without_its_spaces(client, staff):
    made(post(client, "owners", name="  Owner A  "))

    assert [o["name"] for o in client.get("/api/setup").json()["owners"]] == ["Owner A"]
