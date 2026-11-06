"""Settings, Chart of accounts through the API (src/pms/api/chart.py): every account by section
and code, and the edits staff make (a code, a name, archiving, who keeps a charge type's fees),
each a new row naming who made it, each refused in words when the database refuses it."""

import csv
import io
import json
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from helpers import add_unit, enter_bill, make_pmc, open_lease
from people import join_as_owner, join_as_staff, join_as_tenant

NOW = datetime.now(UTC)
TODAY = NOW.date()
DAY = timedelta(days=1)


@pytest.fixture
def staff(client, conn, app_conn, pmc):
    return join_as_staff(client, conn, app_conn, pmc)


def send(client, path, **body):
    return client.post(path, json=json.loads(json.dumps(body, default=str)))


def ok(answer, status=201):
    assert answer.status_code == status, answer.text
    return None if status == 204 else answer.json()


def refused(answer, status, detail=None):
    assert answer.status_code == status, answer.text
    if detail is not None:
        assert answer.json()["detail"] == detail


def account(client, name, line):
    return ok(send(client, "/api/setup/expense-accounts", name=name, schedule_e_line=line))["id"]


def kind(client, name, payee="owner"):
    return ok(send(client, "/api/setup/charge-types", name=name, payee=payee))["id"]


def chart(client):
    return ok(client.get("/api/setup/chart-of-accounts"), 200)


def row(client, **which):
    (found,) = [a for a in chart(client)["accounts"] if all(a[k] == v for k, v in which.items())]
    return found


def test_staff_read_every_account_by_section_and_code(client, staff):
    plumbing = account(client, "Plumbing repairs", 14)
    admin = kind(client, "Admin fee", "company")

    accounts = chart(client)["accounts"]

    assert [(a["section"], a["code"], a["name"]) for a in accounts] == [
        ("asset", "1000", "Trust bank cash"),
        ("liability", "2000", "Owner funds"),
        ("liability", "2100", "Tenant security deposits"),
        ("liability", "2110", "Tenant pet deposits"),
        ("liability", "2120", "Tenant key deposits"),
        ("liability", "2150", "Owner-held deposits (memo)"),
        ("liability", "2200", "Prepaid rent"),
        ("liability", "2250", "Unapplied cash"),
        ("liability", "2300", "Vendor payables"),
        ("liability", "2400", "Company fees held in trust"),
        ("income", "4000", "Rent"),
        ("income", "4010", "Concessions"),
        ("income", "4100", "Recurring charges"),
        ("income", "4200", "Late fees"),
        ("income", "4300", "NSF fees"),
        ("income", "4400", "Other charges"),
        ("income", "4501", "Admin fee"),
        ("expense", "5000", "Bills not in an expense account"),
        ("expense", "5800", "Leasing fees"),
        ("expense", "6100", "Management fees"),
        ("expense", "6401", "Plumbing repairs"),
        ("expense", "6900", "Security deposit interest"),
    ]
    assert row(client, charge_type_id=admin) | {"charge_type_id": None} == {
        "section": "income",
        "code": "4501",
        "name": "Admin fee",
        "type": "Charge type",
        "line": None,
        "charge_type_id": None,
        "expense_account_id": None,
        "schedule_e_line": None,
        "kept_by": "company",
        "archived": False,
    }
    assert row(client, expense_account_id=plumbing)["schedule_e_line"] == 14
    assert row(client, line="rent")["kept_by"] == "owner"


def test_staff_give_an_account_another_code_and_a_code_taken_is_refused_in_words(
    client, conn, pmc, staff
):
    pets = kind(client, "Pet rent")

    moved = ok(send(client, "/api/setup/codes", line="rent", code="4001"))
    again = ok(send(client, "/api/setup/codes", line="rent", code="4001"))
    taken = send(client, "/api/setup/codes", charge_type_id=pets, code="4001")
    section = send(client, "/api/setup/codes", charge_type_id=pets, code="6001")
    kept = send(client, "/api/setup/codes", line="other_charges", code="4010")

    assert (moved, again) == ({"code": "4001", "changed": True}, {"code": "4001", "changed": False})
    refused(taken, 409, "code 4001 is Rent's already")
    refused(section, 409, "an income account's code is from 4000 to 4999, not 6001")
    # Kept for later in the first pass, 4010 is the concessions line's since the second.
    refused(kept, 409, "code 4010 is Concessions's already")
    assert row(client, line="rent")["code"] == "4001"
    # One row for the change, naming who made it; the same code again wrote nothing.
    # This company's rows only: other tests' companies share the database.
    assert conn.execute(
        "SELECT code, recorded_by FROM trust_account_codes WHERE pmc_id = %s AND line = 'rent'",
        (pmc.pmc_id,),
    ).fetchall() == [("4001", staff.person_id)]


@pytest.mark.parametrize(
    "body",
    [
        {"code": "4001"},  # no account
        {"code": "4001", "line": "rent", "charge_type_id": str(uuid.uuid4())},  # two
        {"code": "401", "line": "rent"},
        {"code": "40011", "line": "rent"},
        {"code": "0400", "line": "rent"},
        {"code": "four", "line": "rent"},
        {"code": "4001", "line": "pizza"},
    ],
)
def test_a_bad_code_change_is_refused(client, staff, body):
    refused(client.post("/api/setup/codes", json=body), 422)


def test_another_companys_account_takes_no_code_from_here(client, conn, staff):
    theirs = make_pmc(conn).pmc_id
    other = conn.execute(
        "INSERT INTO trust_expense_accounts (pmc_id, name, schedule_e_line)"
        " VALUES (%s, 'Repairs', 14) RETURNING id",
        (theirs,),
    ).fetchone()[0]

    refused(send(client, "/api/setup/codes", expense_account_id=other, code="6450"), 404)
    refused(send(client, f"/api/setup/expense-accounts/{other}/name", name="Mine"), 404)
    refused(send(client, f"/api/setup/expense-accounts/{other}/archive", archived=True), 404)


def test_staff_rename_an_expense_account_and_a_charge_type(client, conn, staff):
    ads = account(client, "Ads", 5)
    keys = kind(client, "Keys")
    account(client, "Water", 17)

    renamed = ok(send(client, f"/api/setup/expense-accounts/{ads}/name", name=" Advertising "))
    same = ok(send(client, f"/api/setup/expense-accounts/{ads}/name", name="Advertising"))
    taken = send(client, f"/api/setup/expense-accounts/{ads}/name", name="WATER")
    retyped = ok(send(client, f"/api/setup/charge-types/{keys}/name", name="Key replacement"))
    retyped_again = ok(
        send(client, f"/api/setup/charge-types/{keys}/name", name=" Key replacement ")
    )
    line = send(client, f"/api/setup/charge-types/{keys}/name", name="Other charges")

    assert (renamed, same) == ({"changed": True}, {"changed": False})
    assert (retyped, retyped_again) == ({"changed": True}, {"changed": False})
    refused(taken, 409, "the company has an expense account named WATER already")
    refused(line, 422, "Other charges is a line of the income statement already")
    assert row(client, expense_account_id=ads)["name"] == "Advertising"
    assert row(client, charge_type_id=keys)["name"] == "Key replacement"
    # Pickers read the name today too; the name it had is free for another account.
    assert {"id": ads, "name": "Advertising", "schedule_e_line": 5} in ok(
        client.get("/api/setup"), 200
    )["expense_accounts"]
    assert [t["name"] for t in ok(client.get("/api/setup/charge-types"), 200)] == [
        "Key replacement"
    ]
    account(client, "Ads", 5)
    assert conn.execute(
        "SELECT name, recorded_by FROM trust_expense_account_names WHERE expense_account_id = %s",
        (ads,),
    ).fetchall() == [("Advertising", staff.person_id)]
    assert conn.execute(
        "SELECT name FROM trust_charge_type_names WHERE charge_type_id = %s", (keys,)
    ).fetchall() == [("Key replacement",)]


@pytest.mark.parametrize("name", ["", "   ", "x" * 201])
def test_a_bad_name_is_refused(client, staff, name):
    ads = account(client, "Ads", 5)
    refused(send(client, f"/api/setup/expense-accounts/{ads}/name", name=name), 422)


def test_staff_archive_an_expense_account_only_once_its_bills_are_paid(client, conn, staff, pmc):
    water = account(client, "Water", 17)
    bill = enter_bill(
        conn, pmc.pmc_id, pmc.vendor_id, pmc.owners[0].account, "W-1", TODAY - DAY, TODAY, "40.00"
    )
    ok(send(client, f"/api/payables/bills/{bill}/expense-account", expense_account_id=water))

    held = send(client, f"/api/setup/expense-accounts/{water}/archive", archived=True)
    refused(
        held,
        409,
        "Water has a bill not paid in it; pay or void the bill, or put it in another expense"
        " account, before archiving",
    )
    conn.execute(
        "INSERT INTO trust_bill_declines (pmc_id, bill_id, declined_on, declined_by, reason)"
        " VALUES (%s, %s, %s, 'Owner 1', 'Not ours')",
        (pmc.pmc_id, bill, TODAY),
    )
    archived = ok(send(client, f"/api/setup/expense-accounts/{water}/archive", archived=True))
    later = enter_bill(
        conn, pmc.pmc_id, pmc.vendor_id, pmc.owners[0].account, "W-2", TODAY - DAY, TODAY, "40.00"
    )
    chosen = send(client, f"/api/payables/bills/{later}/expense-account", expense_account_id=water)

    assert archived == {"changed": True}
    refused(chosen, 409, "Water is archived; bring it back or choose another expense account")
    assert row(client, expense_account_id=water)["archived"] is True
    assert water not in {a["id"] for a in ok(client.get("/api/setup"), 200)["expense_accounts"]}
    # Brought back, it takes bills again.
    ok(send(client, f"/api/setup/expense-accounts/{water}/archive", archived=False))
    ok(send(client, f"/api/payables/bills/{later}/expense-account", expense_account_id=water))


def test_staff_archive_a_charge_type_and_it_takes_no_new_fee(client, conn, staff, pmc):
    tenant = conn.execute(
        "SELECT tenant_id FROM trust_ledger_accounts WHERE ledger_account_id = %s",
        (pmc.prepaid_rent,),
    ).fetchone()[0]
    unit = add_unit(conn, pmc.pmc_id, pmc.owners[0].property_id)
    lease = open_lease(conn, pmc.pmc_id, unit, TODAY - 10 * DAY, None, "900.00", [tenant])
    parking = kind(client, "Parking")
    monthly = ok(
        send(
            client,
            f"/api/rent/leases/{lease}/recurring-charges",
            request_id=uuid.uuid4(),
            kind="fee",
            amount="40.00",
            memo="Space 9",
            starts_on=TODAY,
            charge_type_id=parking,
        )
    )["id"]

    running = send(client, f"/api/setup/charge-types/{parking}/archive", archived=True)
    refused(running, 409, "Parking has a monthly charge still running; stop it before archiving")
    ok(send(client, f"/api/rent/recurring-charges/{monthly}/stop", ends_on=TODAY))
    ok(send(client, f"/api/setup/charge-types/{parking}/archive", archived=True))
    fee = send(
        client,
        f"/api/rent/leases/{lease}/charges",
        request_id=uuid.uuid4(),
        kind="fee",
        amount="10.00",
        due_on=TODAY,
        memo="Visitor",
        charge_type_id=parking,
    )

    refused(fee, 409, "Parking is archived; bring it back or choose another type")
    assert parking not in {t["id"] for t in ok(client.get("/api/setup/charge-types"), 200)}
    assert row(client, charge_type_id=parking)["archived"] is True


def test_staff_say_who_keeps_a_charge_types_fees(client, conn, staff):
    admin = kind(client, "Admin fee")

    changed = ok(send(client, f"/api/setup/charge-types/{admin}/payee", payee="company"))
    same = ok(send(client, f"/api/setup/charge-types/{admin}/payee", payee="company"))
    bad = send(client, f"/api/setup/charge-types/{admin}/payee", payee="vendor")

    assert (changed, same) == ({"changed": True}, {"changed": False})
    refused(bad, 422)
    assert row(client, charge_type_id=admin)["kept_by"] == "company"
    assert conn.execute(
        "SELECT payee, recorded_by FROM trust_charge_type_payees WHERE charge_type_id = %s",
        (admin,),
    ).fetchall() == [("company", staff.person_id)]


def test_the_chart_downloads_as_csv_and_a_formula_never_runs(client, staff):
    account(client, "=SUM(A1)", 19)

    answer = client.get("/api/reports/trust_report_chart_of_accounts", params={"format": "csv"})

    assert answer.status_code == 200, answer.text
    rows = list(csv.DictReader(io.StringIO(answer.text)))
    assert list(rows[0]) == [
        "Line",
        "Section",
        "Code",
        "Name",
        "Account type",
        "Schedule E",
        "Kept by",
        "Status",
    ]
    assert rows[0] | {"Line": "1"} == {
        "Line": "1",
        "Section": "Assets",
        "Code": "1000",
        "Name": "Trust bank cash",
        "Account type": "Bank cash",
        "Schedule E": "",
        "Kept by": "",
        "Status": "active",
    }
    (formula,) = [r for r in rows if r["Code"] == "6901"]
    assert not formula["Name"].startswith("=")


@pytest.mark.parametrize("caller", ["nobody", "owner", "tenant"])
def test_only_staff_reach_the_chart(client, conn, app_conn, pmc, caller):
    if caller == "owner":
        join_as_owner(client, app_conn, pmc)
    elif caller == "tenant":
        join_as_tenant(client, conn, app_conn, pmc)
    some = str(uuid.uuid4())

    answers = [
        client.get("/api/setup/chart-of-accounts"),
        send(client, "/api/setup/codes", line="rent", code="4001"),
        send(client, f"/api/setup/expense-accounts/{some}/name", name="Repairs"),
        send(client, f"/api/setup/expense-accounts/{some}/archive", archived=True),
        send(client, f"/api/setup/charge-types/{some}/name", name="Parking"),
        send(client, f"/api/setup/charge-types/{some}/archive", archived=True),
        send(client, f"/api/setup/charge-types/{some}/payee", payee="company"),
    ]

    assert [a.status_code for a in answers] == [401 if caller == "nobody" else 403] * 7


def test_a_custom_role_without_setup_changes_no_account(
    client, other_client, conn, app_conn, staff, pmc
):
    ads = account(client, "Ads", 5)
    clerk = join_as_staff(other_client, conn, app_conn, pmc, name="Clerk 2", roles=[])
    role = ok(client.post("/api/custom-roles", json={"name": "Rent", "areas": ["rent"]}))
    ok(
        client.post(
            f"/api/custom-roles/{role['id']}/staff",
            json={"staff_id": str(clerk.person_id), "granted": True},
        ),
        204,
    )

    answers = [
        send(other_client, "/api/setup/codes", expense_account_id=ads, code="5550"),
        send(other_client, f"/api/setup/expense-accounts/{ads}/name", name="Advertising"),
        send(other_client, f"/api/setup/expense-accounts/{ads}/archive", archived=True),
    ]

    assert [a.status_code for a in answers] == [403] * 3
    # Every staff member still reads the chart.
    assert ok(other_client.get("/api/setup/chart-of-accounts"), 200)["accounts"]
    assert Decimal(row(client, expense_account_id=ads)["code"]) == Decimal("5501")
