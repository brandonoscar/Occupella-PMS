"""The switching tool (src/pms/api/switching.py): bringing a PMC's roster in from CSV files,
opening a trust bank account with its old balances, the items carried over, the sign-off, and
what the old system paid each vendor in the cutover's year. Every file here is synthetic."""

import json
import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from helpers import add_agreement, add_tenant, balance, make_pmc, post
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


def contact_of(conn, column, name):
    return conn.execute(
        "SELECT k.email, k.phone, k.alternate_phone, k.address_line_1, k.address_line_2, k.city,"
        f" k.state, k.postal_code, k.recorded_by IS NOT NULL FROM trust_contacts AS k"  # noqa: S608
        f" JOIN trust_{column.removesuffix('_id')}s AS p ON p.id = k.{column}"
        " WHERE p.display_name = %s ORDER BY k.created_at DESC, k.id DESC",
        (name,),
    ).fetchall()


def test_owners_vendors_and_tenants_bring_their_contact_details(client, conn, staff, pmc):
    """q127: the old system's emails, phones and mailing addresses come over with the names,
    each named as the staff member who imported them; a row without any keeps what is on file."""
    owners = ok(
        imported(
            client,
            "owners",
            "Name,Email,Phone,Other phone,Address,Address line 2,City,State,ZIP",
            "Owner A,owner.a@example.com,555-555-0110,,"
            "100 Example Street,Suite 1,Sampletown,ZZ,00000",
            "Owner B,,,,,,,,",
        )
    )
    ok(
        imported(
            client, "vendors", "Name,Email,Phone", "Vendor A,vendor.a@example.com,555-555-0111"
        )
    )
    ok(imported(client, "properties", "Property,Owner", "Property A,Owner A"))
    ok(
        imported(
            client, "tenants", "Property,Tenant,Email", "Property A,Tenant A,tenant.a@example.com"
        )
    )
    again = ok(imported(client, "owners", "Name", "Owner A"))
    same = ok(
        imported(
            client,
            "owners",
            "Name,Email,Phone,Address,Address line 2,City,State,ZIP",
            "Owner A,owner.a@example.com,555-555-0110,"
            "100 Example Street,Suite 1,Sampletown,ZZ,00000",
        )
    )

    assert owners == {"added": 2, "kept": 0} and again == same == {"added": 0, "kept": 1}
    assert contact_of(conn, "owner_id", "Owner A") == [
        (
            "owner.a@example.com",
            "555-555-0110",
            None,
            "100 Example Street",
            "Suite 1",
            "Sampletown",
            "ZZ",
            "00000",
            True,
        )
    ]
    assert contact_of(conn, "owner_id", "Owner B") == []
    assert contact_of(conn, "vendor_id", "Vendor A")[0][:2] == (
        "vendor.a@example.com",
        "555-555-0111",
    )
    assert contact_of(conn, "tenant_id", "Tenant A")[0][0] == "tenant.a@example.com"


@pytest.mark.parametrize(
    ("lines", "why"),
    [
        (["Name,Email", "Owner A,not an email"], "line 2: the email isn't one the PMS can keep"),
        (["Name,Phone", "Owner A,call me"], "line 2: the phone isn't one the PMS can keep"),
        (
            ["Name,Address,City", "Owner A,100 Example Street,Sampletown"],
            "line 2: a mailing address needs its street, city, state and ZIP code",
        ),
    ],
)
def test_bad_contact_details_say_their_line_and_bring_nothing(client, conn, staff, pmc, lines, why):
    refused(imported(client, "owners", *lines), 422, why)

    assert conn.execute(
        "SELECT count(*) FROM trust_owners WHERE pmc_id = %s AND display_name = 'Owner A'",
        (pmc.pmc_id,),
    ).fetchone() == (0,)


def test_a_file_that_is_not_csv_is_refused_in_words_not_a_server_error(client, conn, staff, pmc):
    # A bare carriage return ends a line (old Mac files); the API fuzz test found it answered 500.
    refused(
        client.post("/api/switching/import", json={"kind": "bills", "content": "\r0"}),
        422,
        "line 1: no vendor, owner, property, reference, bill date, due on, amount, memo column",
    )
    # A field longer than the csv module reads.
    refused(
        client.post(
            "/api/switching/import", json={"kind": "owners", "content": "Name\n" + "x" * 140_000}
        ),
        422,
        "line 2: this isn't CSV the PMS can read (field larger than field limit (131072))",
    )
    assert conn.execute(
        "SELECT count(*) FROM trust_owners WHERE pmc_id = %s", (pmc.pmc_id,)
    ).fetchone() == (len(pmc.owners),)


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


# --- unpaid bills carried over ---------------------------------------------------------------

BILLS = "Vendor,Owner,Property,Reference,Bill date,Due on,Amount,Memo,Approved by,Approved on"


def owner_account(conn, pmc, owner):
    return conn.execute(
        "SELECT a.ledger_account_id FROM trust_ledger_accounts AS a"
        " JOIN trust_owners AS o ON o.id = a.owner_id WHERE o.pmc_id = %s AND o.display_name = %s",
        (pmc.pmc_id, owner),
    ).fetchone()[0]


def test_unpaid_bills_come_over_and_hold_back_draws(client, conn, staff, pmc):
    roster(client)
    account = owner_account(conn, pmc, "Owner A")
    add_agreement(conn, pmc.pmc_id, account, date(2025, 1, 1), approval_limit="500")
    post(conn, pmc.pmc_id, "rent-a", [(pmc.operating_cash, account, Decimal("2000.00"))])
    lines = (
        BILLS,
        "Vendor A,Owner A,Property A,INV-1,2025-12-20,2026-01-20,300.00,Gutters,,",
        "Vendor A,Owner A,Property A,INV-2,2025-12-28,2026-01-28,700.00,Roof,Owner A,2025-12-29",
        "Vendor A,Owner A,Property A,INV-3,2025-12-30,2026-02-01,650.00,Boiler,,",
    )

    assert ok(imported(client, "bills", *lines)) == {"added": 3, "kept": 0}
    # Sent again, nothing is added twice.
    assert ok(imported(client, "bills", *lines)) == {"added": 0, "kept": 3}

    bills = {b["reference"]: b for b in ok(client.get("/api/payables/bills"))["items"]}
    assert {
        r: (b["bill_date"], b["due_on"], b["amount"], b["status"]) for r, b in bills.items()
    } == {
        "INV-1": ("2025-12-20", "2026-01-20", "300.00", "ready to pay"),
        "INV-2": ("2025-12-28", "2026-01-28", "700.00", "ready to pay"),
        "INV-3": ("2025-12-30", "2026-02-01", "650.00", "waiting for owner"),
    }
    assert bills["INV-2"]["approved_by"] == "Owner A"
    (owner,) = [o for o in ok(client.get("/api/payables/owners")) if o["account"] == account]
    assert (owner["balance"], owner["unpaid_bills"], owner["available"]) == (
        "2000.00",
        "1650.00",
        "350.00",
    )
    # A draw takes no more than what the bills leave.
    refused(
        client.post(
            "/api/payables/draws/batch",
            json={
                "request_id": str(uuid.uuid4()),
                "on": str(TODAY),
                "method": "check",
                "payouts": [{"account": account, "amount": "350.01"}],
            },
        ),
        409,
        "$1,650.00 of unpaid bills",
    )


def test_an_unpaid_bill_over_the_limit_asks_its_owner_as_a_new_one_does(
    client, other_client, conn, app_conn, staff, pmc
):
    account = pmc.owners[0].account
    names = conn.execute(
        "SELECT o.display_name, p.display_name, v.display_name FROM trust_owners AS o,"
        " trust_properties AS p, trust_vendors AS v WHERE o.id = %s AND p.id = %s AND v.id = %s",
        (pmc.owners[0].owner_id, pmc.owners[0].property_id, pmc.vendor_id),
    ).fetchone()
    add_agreement(conn, pmc.pmc_id, account, date(2025, 1, 1), approval_limit="100")
    join_as_owner(other_client, app_conn, pmc)  # the owner can sign in, so is emailed
    owner, prop, vendor = names

    ok(
        imported(
            client,
            "bills",
            BILLS,
            f"{vendor},{owner},{prop},INV-9,2025-12-20,2026-01-20,250.00,Fence,,",
        )
    )

    assert conn.execute(
        "SELECT e.subject FROM trust_emails AS e JOIN trust_bills AS b ON b.id = e.bill_id"
        " WHERE b.pmc_id = %s AND b.reference = 'INV-9'",
        (pmc.pmc_id,),
    ).fetchall() == [(f"A bill waits for your approval: {vendor}, $250.00",)]


@pytest.mark.parametrize(
    ("line", "why"),
    [
        ("Vendor Z,Owner A,Property A,INV-1,2025-12-20,2026-01-20,10.00,Gutters,,", "no vendor"),
        ("Vendor A,Owner Z,Property A,INV-1,2025-12-20,2026-01-20,10.00,Gutters,,", "no owner"),
        (
            "Vendor A,Owner A,Property Z,INV-1,2025-12-20,2026-01-20,10.00,Gutters,,",
            "no property",
        ),
        ("Vendor A,Owner A,Property A,INV-1,2025-12-20,2026-01-20,0.00,Gutters,,", "more than 0"),
        ("Vendor A,Owner A,Property A,INV-1,2025-12-20,2025-12-19,10.00,Gutters,,", "on or after"),
        ("Vendor A,Owner A,Property A,,2025-12-20,2026-01-20,10.00,Gutters,,", "no reference"),
        ("Vendor A,Owner A,Property A,INV-1,2025-12-20,2026-01-20,10.00,,,", "no memo"),
        (
            "Vendor A,Owner A,Property A,INV-1,2025-12-20,2026-01-20,10.00,Gutters,Owner A,",
            "no approved on",
        ),
        (
            "Vendor A,Owner A,Property A,INV-1,2025-12-20,2026-01-20,10.00,Gutters,,2025-12-21",
            "approved by",
        ),
        (
            "Vendor A,Owner A,Property A,INV-0,2025-12-20,2026-01-20,99.00,Gutters,,",
            "another bill with that reference",
        ),
        ("Vendor A,Owner A,Property A,INV-1,someday,2026-01-20,10.00,Gutters,,", "line 3"),
    ],
)
def test_a_bills_file_that_cant_go_in_says_where_and_why_and_adds_nothing(
    client, conn, staff, pmc, line, why
):
    roster(client)
    ok(
        imported(
            client,
            "bills",
            BILLS,
            "Vendor A,Owner A,Property A,INV-0,2025-12-01,2025-12-31,5.00,Lock,,",
        )
    )

    refused(
        imported(
            client,
            "bills",
            BILLS,
            "Vendor A,Owner B,Property A,INV-5,2025-12-20,2026-01-20,10.00,Paint,,",
            line,
        ),
        422,
        why,
    )
    assert conn.execute(
        "SELECT reference FROM trust_bills WHERE pmc_id = %s", (pmc.pmc_id,)
    ).fetchall() == [("INV-0",)]


def test_a_bill_names_an_owner_with_an_account_for_the_property(client, conn, staff, pmc):
    roster(client)
    ok(imported(client, "owners", "Name", "Owner C"))

    refused(
        imported(
            client,
            "bills",
            BILLS,
            "Vendor A,Owner C,Property A,INV-1,2025-12-20,2026-01-20,10.00,Gutters,,",
        ),
        422,
        "line 2: that owner has no account for that property",
    )


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


def test_an_opening_deposit_is_the_tenants_security_deposit(client, conn, staff, pmc):
    # Tenant 2 holds only a pet deposit account so far (migration 0092): the old system's
    # deposit opens their security deposit beside it.
    tenant = add_tenant(conn, pmc.pmc_id, pmc.owners[0].property_id, "Tenant 2")
    pet = conn.execute(
        "SELECT trust_open_deposit_account(%s, %s, %s, 'pet')",
        (pmc.pmc_id, pmc.deposit_bank_id, tenant),
    ).fetchone()[0]
    deposit = csv("Kind,Property,Tenant,Amount", "deposit,Property 1,Tenant 2,1500.00")

    ok(
        open_balances(
            client, pmc, content=deposit, book="1500.00", bank_account_id=pmc.deposit_bank_id
        ),
        201,
    )

    security = conn.execute(
        "SELECT ledger_account_id FROM trust_ledger_accounts WHERE tenant_id = %s"
        " AND kind = 'tenant_deposit' AND deposit_kind IS NULL",
        (tenant,),
    ).fetchone()[0]
    assert (balance(conn, security), balance(conn, pet)) == (Decimal("1500.00"), 0)


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


# --- vendor payments carried over ----------------------------------------------------------


def vendor_payments(client, *lines, paid_before="2026-03-01"):
    return call(client, "vendor-payments", paid_before=paid_before, content=csv(*lines))


def report(conn, pmc, year):
    return conn.execute(
        "SELECT item, vendor, payments, paid FROM trust_report_vendor_payments(%s, %s)"
        " WHERE item NOT IN ('PMC', 'threshold')",
        (pmc.pmc_id, year),
    ).fetchall()


def of_vendor(rows, name):
    """A vendor's line in the 1099 summary, whichever part it is in."""
    return [row for row in rows if row[1] == name]


def test_what_the_old_system_paid_vendors_comes_over_once(client, conn, staff, pmc):
    ok(imported(client, "vendors", "Name", "Vendor A", "Vendor B"))
    conn.execute(
        "INSERT INTO trust_vendor_profiles (pmc_id, vendor_id, gets_1099)"
        " SELECT pmc_id, id, true FROM trust_vendors WHERE pmc_id = %s",
        (pmc.pmc_id,),
    )

    first = vendor_payments(client, "Vendor,Paid", 'Vendor A,"$2,250.50"', "Vendor B,80.00")
    again = vendor_payments(client, "vendor,paid", "Vendor A,2250.50")

    assert (ok(first, 201), ok(again, 201)) == ({"added": 2, "kept": 0}, {"added": 0, "kept": 1})
    # What the old system paid counts toward the year's 1099 (q060: 2026's threshold, 2000.00).
    assert report(conn, pmc, 2026)[1:] == [
        ("vendor", "Vendor A", 0, Decimal("2250.50")),
        ("carried over", "paid in the old system before 2026-03-01", None, Decimal("2250.50")),
        ("total", None, 0, Decimal("2250.50")),
        ("under the threshold", "Vendor B", 0, Decimal("80.00")),
        ("carried over", "paid in the old system before 2026-03-01", None, Decimal("80.00")),
        ("not marked for a 1099", "0 vendors", 0, Decimal("0.00")),
    ]
    assert conn.execute(
        "SELECT staff_id FROM trust_audit_log"
        " WHERE table_name = 'trust_vendor_payments_carried_over' AND pmc_id = %s",
        (pmc.pmc_id,),
    ).fetchall() == [(staff.person_id,), (staff.person_id,)]


def test_a_january_first_cutover_carries_over_the_year_before(client, conn, staff, pmc):
    ok(imported(client, "vendors", "Name", "Vendor A"))

    ok(vendor_payments(client, "Vendor,Paid", "Vendor A,900.00", paid_before="2027-01-01"), 201)

    assert of_vendor(report(conn, pmc, 2026), "Vendor A") == [
        ("no 1099 setting", "Vendor A", 0, Decimal("900.00"))
    ]


@pytest.mark.parametrize(
    ("lines", "why"),
    [
        (("Vendor,Paid", "Vendor Z,10.00"), "line 2: no vendor 'Vendor Z'"),
        (("Vendor,Paid", "Vendor A,0.00"), "line 2: what was paid is more than 0"),
        (("Vendor,Paid", "Vendor A,-5.00"), "line 2: what was paid is more than 0"),
        (("Vendor,Paid", "Vendor A,1.005"), "not an amount in cents"),
        (("Vendor,Paid", "Vendor A,10.00", "Vendor A,11.00"), "line 3: Vendor A's 2026"),
        (("Vendor",), "line 1: no paid column"),
    ],
)
def test_a_vendor_payments_file_that_cant_go_in_says_why_and_adds_nothing(
    client, conn, staff, pmc, lines, why
):
    ok(imported(client, "vendors", "Name", "Vendor A"))

    refused(vendor_payments(client, *lines), 422, why)
    assert of_vendor(report(conn, pmc, 2026), "Vendor A") == []


def test_another_amount_for_a_year_carried_over_is_refused(client, conn, staff, pmc):
    ok(imported(client, "vendors", "Name", "Vendor A"))
    ok(vendor_payments(client, "Vendor,Paid", "Vendor A,10.00"), 201)

    refused(
        vendor_payments(client, "Vendor,Paid", "Vendor A,10.00", paid_before="2026-04-01"),
        422,
        "line 2: Vendor A's 2026 was carried over already: 10.00 paid before 2026-03-01",
    )


def test_another_pmcs_vendor_is_not_found(client, conn, staff, pmc):
    other = make_pmc(conn)
    conn.execute(
        "INSERT INTO trust_vendors (pmc_id, display_name) VALUES (%s, 'Vendor Elsewhere')",
        (other.pmc_id,),
    )

    refused(
        vendor_payments(client, "Vendor,Paid", "Vendor Elsewhere,10.00"),
        422,
        "line 2: no vendor 'Vendor Elsewhere'",
    )
    assert conn.execute(
        "SELECT count(*) FROM trust_vendor_payments_carried_over WHERE pmc_id IN (%s, %s)",
        (pmc.pmc_id, other.pmc_id),
    ).fetchone() == (0,)


@pytest.mark.parametrize("paid_before", ["0001-01-01", "1900-01-01", "9999-12-31", "someday"])
def test_a_cutover_day_out_of_range_is_refused(client, staff, paid_before):
    refused(vendor_payments(client, "Vendor,Paid", "Vendor 1,10.00", paid_before=paid_before), 422)


ENDPOINTS = [
    ("get", "/api/switching"),
    ("post", "/api/switching/import"),
    ("post", "/api/switching/opening-balances"),
    ("post", "/api/switching/items"),
    ("post", "/api/switching/sign-off"),
    ("post", "/api/switching/vendor-payments"),
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
        ("vendor-payments", {"content": "Vendor,Paid\nVendor 1,1.00\n"}),  # no cutover day
        ("vendor-payments", {"paid_before": "2026-03-01", "content": ""}),
    ],
)
def test_bad_input_is_refused(client, staff, path, body):
    refused(call(client, path, **body), 422)
