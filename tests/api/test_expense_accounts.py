"""Expense accounts through the API: staff add them in setup (src/pms/api/setup.py), put bills
and recurring bills in them (src/pms/api/payables.py), and an owner reads and downloads the
year's income statement in the portal (src/pms/api/owner.py)."""

import json
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from helpers import make_pmc, post
from people import join_as_owner, join_as_staff, join_as_tenant

from pms import nightly
from pms.api.setup import SCHEDULE_E

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
    return answer.json()


def refused(answer, status, detail=None):
    assert answer.status_code == status, answer.text
    if detail is not None:
        assert answer.json()["detail"] == detail


def account(client, name="Repairs", line=14):
    return ok(send(client, "/api/setup/expense-accounts", name=name, schedule_e_line=line))["id"]


def fund(conn, pmc, amount, n=0):
    post(
        conn,
        pmc.pmc_id,
        f"fund-{uuid.uuid4()}",
        [(pmc.operating_cash, pmc.owners[n].account, Decimal(amount))],
        NOW - 30 * DAY,
    )


def bill(client, pmc, reference="INV-1", amount="20.00", **more):
    return ok(
        send(
            client,
            "/api/payables/bills",
            **(
                {
                    "vendor_id": pmc.vendor_id,
                    "account": pmc.owners[0].account,
                    "reference": reference,
                    "bill_date": TODAY - 2 * DAY,
                    "due_on": TODAY,
                    "amount": amount,
                    "memo": "Faucet",
                }
                | more
            ),
        )
    )["id"]


def in_account(client, reference):
    found = ok(client.get("/api/payables/bills?show=all"), 200)["items"]
    return next(b["expense_account"] for b in found if b["reference"] == reference)


def test_staff_add_expense_accounts_on_schedule_e_lines(client, staff):
    account(client, "Water", 17)
    account(client, "Landscaping", 7)

    books = ok(client.get("/api/setup"), 200)

    assert [(a["name"], a["schedule_e_line"]) for a in books["expense_accounts"]] == [
        ("Landscaping", 7),
        ("Water", 17),
    ]
    assert [line["line"] for line in books["schedule_e_lines"]] == list(range(5, 20))
    refused(
        send(client, "/api/setup/expense-accounts", name="Water", schedule_e_line=14),
        409,
        "the company has an expense account of that name already",
    )


@pytest.mark.parametrize(
    "body",
    [
        {"name": "Odd", "schedule_e_line": 3},
        {"name": "Odd", "schedule_e_line": 20},
        {"name": " ", "schedule_e_line": 14},
        {"name": "Odd"},
        {"name": "Odd", "schedule_e_line": "repairs"},
    ],
)
def test_an_expense_account_needs_a_name_and_an_expense_line(client, staff, body):
    refused(send(client, "/api/setup/expense-accounts", **body), 422)


@pytest.mark.parametrize("caller", ["nobody", "owner", "tenant"])
def test_only_staff_add_expense_accounts_or_put_bills_in_them(client, conn, app_conn, pmc, caller):
    if caller == "owner":
        join_as_owner(client, app_conn, pmc)
    elif caller == "tenant":
        join_as_tenant(client, conn, app_conn, pmc)
    status = 401 if caller == "nobody" else 403
    some = str(uuid.uuid4())

    refused(send(client, "/api/setup/expense-accounts", name="X", schedule_e_line=14), status)
    refused(
        send(client, f"/api/payables/bills/{some}/expense-account", expense_account_id=some),
        status,
    )
    refused(
        send(client, f"/api/payables/recurring/{some}/expense-account", expense_account_id=some),
        status,
    )


def test_a_bill_is_entered_in_an_account_and_moved_to_another(client, staff, pmc):
    repairs, water = account(client, "Repairs", 14), account(client, "Water", 17)
    entered = bill(client, pmc, expense_account_id=repairs)
    bill(client, pmc, "INV-2")
    assert (in_account(client, "INV-1"), in_account(client, "INV-2")) == ("Repairs", None)

    ok(send(client, f"/api/payables/bills/{entered}/expense-account", expense_account_id=water))
    ok(send(client, f"/api/payables/bills/{entered}/expense-account", expense_account_id=water))

    assert in_account(client, "INV-1") == "Water"


def test_only_the_pmcs_own_bills_and_expense_accounts(client, conn, staff, pmc):
    theirs = make_pmc(conn)
    their_account = conn.execute(
        "INSERT INTO trust_expense_accounts (pmc_id, name, schedule_e_line)"
        " VALUES (%s, 'Repairs', 14) RETURNING id",
        (theirs.pmc_id,),
    ).fetchone()[0]
    their_bill = conn.execute(
        "INSERT INTO trust_bills (pmc_id, vendor_id, ledger_account_id, reference, bill_date,"
        " due_on, amount, memo) VALUES (%s, %s, %s, 'X-1', %s, %s, 5, 'x') RETURNING id",
        (theirs.pmc_id, theirs.vendor_id, theirs.owners[0].account, TODAY, TODAY),
    ).fetchone()[0]
    mine = account(client)
    entered = bill(client, pmc)

    refused(
        send(
            client,
            "/api/payables/bills",
            vendor_id=pmc.vendor_id,
            account=pmc.owners[0].account,
            reference="INV-9",
            bill_date=TODAY,
            due_on=TODAY,
            amount="5.00",
            memo="x",
            expense_account_id=their_account,
        ),
        404,
        "no such expense account",
    )
    refused(
        send(
            client,
            f"/api/payables/bills/{entered}/expense-account",
            expense_account_id=their_account,
        ),
        404,
        "no such expense account",
    )
    refused(
        send(client, f"/api/payables/bills/{their_bill}/expense-account", expense_account_id=mine),
        404,
    )
    refused(
        send(client, f"/api/payables/bills/{entered}/expense-account", expense_account_id="x"),
        422,
    )


def test_a_recurring_bills_account_goes_on_the_months_it_enters(client, app_conn, staff, pmc):
    lawn = account(client, "Landscaping", 7)
    gardens = account(client, "Gardens", 7)
    recurring = ok(
        send(
            client,
            "/api/payables/recurring",
            vendor_id=pmc.vendor_id,
            account=pmc.owners[0].account,
            reference_prefix="LAWN",
            amount="30.00",
            memo="Lawn service",
            day_of_month=1,
            starts_on=TODAY.replace(day=1),
            expense_account_id=lawn,
        )
    )["id"]
    listed = ok(client.get("/api/payables/recurring"), 200)
    assert [(r["reference_prefix"], r["expense_account"]) for r in listed] == [
        ("LAWN", "Landscaping")
    ]

    nightly.run_pmc(app_conn, pmc.pmc_id, TODAY, "http://pms.test")
    ok(
        send(
            client,
            f"/api/payables/recurring/{recurring}/expense-account",
            expense_account_id=gardens,
        )
    )

    assert in_account(client, f"LAWN-{TODAY:%Y-%m}") == "Landscaping"  # entered before
    assert ok(client.get("/api/payables/recurring"), 200)[0]["expense_account"] == "Gardens"
    refused(
        send(
            client,
            f"/api/payables/recurring/{uuid.uuid4()}/expense-account",
            expense_account_id=gardens,
        ),
        404,
        "no such recurring bill",
    )


def test_every_schedule_e_line_is_labelled_as_the_income_statement_labels_it(
    client, conn, staff, pmc
):
    """Setup's labels (SCHEDULE_E) and the report's are the same words."""
    fund(conn, pmc, "1000.00")
    for line in SCHEDULE_E:
        entered = bill(
            client, pmc, f"L-{line}", expense_account_id=account(client, f"A{line}", line)
        )
        ok(
            send(
                client,
                f"/api/payables/bills/{entered}/approval",
                approved_on=TODAY,
                approved_by="Owner 1, by phone",
            )
        )
        ok(
            send(
                client, f"/api/payables/bills/{entered}/pay", on=TODAY, request_id=str(uuid.uuid4())
            )
        )

    rows = conn.execute(
        "SELECT item FROM trust_report_income_statement(%s, %s, %s, %s)"
        " WHERE property IS NULL AND item LIKE 'all properties: Schedule E line %%'",
        (pmc.pmc_id, pmc.owners[0].owner_id, TODAY, TODAY),
    ).fetchall()

    assert [r[0] for r in rows] == [
        "all properties: Schedule E line 3: Rents received",
        *(f"all properties: Schedule E line {n}: {label}" for n, label in SCHEDULE_E.items()),
    ]


def test_an_owner_reads_and_downloads_their_years_income_statement(
    client, other_client, conn, app_conn, pmc
):
    owner = join_as_owner(client, app_conn, pmc)
    join_as_staff(other_client, conn, app_conn, pmc)
    fund(conn, pmc, "500.00")
    repairs = account(other_client)
    entered = bill(other_client, pmc, amount="120.00", expense_account_id=repairs)
    ok(
        send(
            other_client,
            f"/api/payables/bills/{entered}/approval",
            approved_on=TODAY,
            approved_by="Owner 1, by phone",
        )
    )
    ok(
        send(
            other_client,
            f"/api/payables/bills/{entered}/pay",
            on=TODAY,
            request_id=str(uuid.uuid4()),
        )
    )
    fund(conn, pmc, "75.00", n=1)  # another owner's money is not theirs

    found = ok(client.get(f"/api/owner/income-statement?year={TODAY.year}"), 200)

    rows = {(r[1], r[2]): r[4] for r in found["rows"]}
    assert found["columns"] == ["line", "property", "item", "schedule_e", "amount"]
    assert rows[("Owner 1", "owner")] is None
    assert rows[("Property 1", "expense: Repairs")] == "120.00"
    assert rows[("Property 1", "net income")] == "-120.00"
    assert not any(p == "Property 2" for p, _ in rows)
    assert found["title"] == f"Income statement, {TODAY.year}"
    pdf = client.get(f"/api/owner/income-statement/download?year={TODAY.year}")
    assert pdf.status_code == 200 and pdf.content.startswith(b"%PDF")
    csv = client.get(f"/api/owner/income-statement/download?year={TODAY.year}&format=csv")
    assert "expense: Repairs" in csv.text
    assert owner.email


@pytest.mark.parametrize("year", ["1899", "9999", "this"])
def test_an_income_statement_is_for_a_year(client, app_conn, pmc, year):
    join_as_owner(client, app_conn, pmc)
    refused(client.get(f"/api/owner/income-statement?year={year}"), 422)
    refused(client.get(f"/api/owner/income-statement/download?year={year}"), 422)


@pytest.mark.parametrize("caller", ["nobody", "staff", "tenant"])
def test_only_an_owner_reads_their_income_statement(client, conn, app_conn, pmc, caller):
    if caller == "staff":
        join_as_staff(client, conn, app_conn, pmc)
    elif caller == "tenant":
        join_as_tenant(client, conn, app_conn, pmc)
    status = 401 if caller == "nobody" else 403
    refused(client.get("/api/owner/income-statement?year=2026"), status)
    refused(client.get("/api/owner/income-statement/download?year=2026"), status)


def test_staff_download_any_owners_income_statement(client, staff, pmc):
    # The owners page links each owner's statement by the owner's id.
    owners = ok(client.get("/api/payables/owners"), 200)
    assert [o["owner_id"] for o in owners] == [str(o.owner_id) for o in pmc.owners]
    answer = client.get(
        "/api/reports/trust_report_income_statement"
        f"?arg={pmc.owners[1].owner_id}&arg={TODAY.replace(month=1, day=1)}&arg={TODAY}"
        "&format=csv"
    )
    assert answer.status_code == 200, answer.text
    assert "Owner 2" in answer.text and "net income" in answer.text
