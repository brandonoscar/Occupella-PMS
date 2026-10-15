"""Budgets (src/pms/api/budgets.py): staff set a line's budget for each month of a year, on an
owner's property account; a change is a new row and a month left as it was writes nothing.
The budget against the income statement downloads like every report."""

import pytest
from helpers import make_pmc
from people import join_as_owner, join_as_staff

YEAR = 2026
EVEN = ["100.00"] * 12


@pytest.fixture
def staff(client, conn, app_conn, pmc):
    return join_as_staff(client, conn, app_conn, pmc)


@pytest.fixture
def repairs(conn, pmc):
    return conn.execute(
        "INSERT INTO trust_expense_accounts (pmc_id, name, schedule_e_line)"
        " VALUES (%s, 'Repairs', 14) RETURNING id",
        (pmc.pmc_id,),
    ).fetchone()[0]


def ok(answer, status=200):
    assert answer.status_code == status, answer.text
    return answer.json() if answer.content else None


def refused(answer, status, detail=None):
    assert answer.status_code == status, answer.text
    if detail is not None:
        assert detail in str(answer.json()["detail"]), answer.text


def budget(client, account, year=YEAR):
    return ok(client.get("/api/budgets", params={"account": account, "year": year}))


def line_of(found, label):
    (line,) = [x for x in found["lines"] if x["label"] == label]
    return line


def test_staff_set_a_lines_budget_month_by_month(client, conn, pmc, staff, repairs):
    account = pmc.owners[0].account
    months = [f"{n}00.00" for n in range(1, 13)]
    assert ok(
        client.post(
            "/api/budgets",
            json={"account": account, "year": YEAR, "line": "rent", "months": months},
        )
    ) == {"changed": 12}
    ok(
        client.post(
            "/api/budgets",
            json={
                "account": account,
                "year": YEAR,
                "line": "expense account",
                "expense_account_id": str(repairs),
                "months": EVEN,
            },
        )
    )

    found = budget(client, account)
    assert (found["owner"], found["property"], found["year"]) == ("Owner 1", "Property 1", YEAR)
    assert [x["label"] for x in found["lines"]] == [
        "income: rent",
        "income: recurring charges",
        "income: late fees",
        "income: NSF fees",
        "income: other charges",
        "expense: management fees",
        "expense: leasing fees",
        "expense: Repairs",
        "expense: bills not in an expense account",
    ]
    assert line_of(found, "income: rent")["months"] == months
    assert line_of(found, "income: rent")["year"] == "7800.00"
    assert line_of(found, "expense: Repairs")["year"] == "1200.00"
    assert line_of(found, "income: late fees")["months"] == ["0.00"] * 12
    # Another year, or another account, has none of it.
    assert line_of(budget(client, account, YEAR + 1), "income: rent")["year"] == "0.00"
    assert line_of(budget(client, pmc.owners[1].account), "income: rent")["year"] == "0.00"


def test_a_change_writes_only_the_months_it_changes(client, conn, pmc, staff):
    account = pmc.owners[0].account
    body = {"account": account, "year": YEAR, "line": "management fees", "months": EVEN}
    ok(client.post("/api/budgets", json=body))
    assert ok(client.post("/api/budgets", json=body)) == {"changed": 0}
    changed = ["100.00"] * 11 + ["150.00"]
    assert ok(client.post("/api/budgets", json=body | {"months": changed})) == {"changed": 1}

    assert line_of(budget(client, account), "expense: management fees")["months"][-1] == "150.00"
    assert conn.execute(
        "SELECT count(*) FROM trust_budgets WHERE ledger_account_id = %s", (account,)
    ).fetchone() == (13,)
    # The audit log names who changed it.
    assert conn.execute(
        "SELECT count(*) FROM trust_audit_log WHERE table_name = 'trust_budgets' AND staff_id = %s",
        (staff.person_id,),
    ).fetchone() == (13,)


def test_the_budget_against_actual_downloads_for_an_owner(client, pmc, staff):
    account = pmc.owners[0].account
    ok(
        client.post(
            "/api/budgets", json={"account": account, "year": YEAR, "line": "rent", "months": EVEN}
        )
    )
    owner = budget(client, account)["owner_id"]

    answer = client.get(
        "/api/reports/trust_report_budget_vs_actual",
        params=[
            ("arg", owner),
            ("arg", f"{YEAR}-01-01"),
            ("arg", f"{YEAR}-12-31"),
            ("format", "csv"),
        ],
    )

    assert answer.status_code == 200, answer.text
    assert "Property 1,income: rent,1200.00,0.00,-1200.00" in answer.text


def test_only_the_pmcs_staff_set_or_read_budgets(client, other_client, conn, app_conn, pmc, staff):
    account = pmc.owners[0].account
    body = {"account": account, "year": YEAR, "line": "rent", "months": EVEN}
    for answer in (
        other_client.get("/api/budgets", params={"account": account, "year": YEAR}),
        other_client.post("/api/budgets", json=body),
    ):
        refused(answer, 401)
    join_as_owner(other_client, app_conn, pmc)
    refused(other_client.post("/api/budgets", json=body), 403, "only the PMC's staff")

    other = make_pmc(conn)
    other_client.post("/api/sign-out")
    join_as_staff(other_client, conn, app_conn, other, name="Staff 2")
    refused(other_client.post("/api/budgets", json=body), 404, "no such owner's property account")
    refused(
        other_client.get("/api/budgets", params={"account": account, "year": YEAR}),
        404,
        "no such owner's property account",
    )
    assert conn.execute(
        "SELECT count(*) FROM trust_budgets WHERE ledger_account_id = %s", (account,)
    ).fetchone() == (0,)


@pytest.mark.parametrize(
    "change",
    [
        {"months": ["100.00"] * 11},
        {"months": ["100.00"] * 13},
        {"months": ["-1.00"] * 12},
        {"months": ["1.001"] * 12},
        {"months": ["100000000.01"] * 12},
        {"year": 1899},
        {"line": "parking"},
        {"line": "expense account"},
        {"expense_account_id": "3f1c2b9a-0000-4000-8000-000000000001"},
    ],
)
def test_a_budget_is_twelve_months_of_whole_cents_on_a_known_line(client, conn, pmc, staff, change):
    body = {"account": pmc.owners[0].account, "year": YEAR, "line": "rent", "months": EVEN}
    refused(client.post("/api/budgets", json=body | change), 422)
    assert conn.execute(
        "SELECT count(*) FROM trust_budgets WHERE pmc_id = %s", (pmc.pmc_id,)
    ).fetchone() == (0,)


def test_a_budget_names_an_owners_account_and_an_expense_account_of_the_company(
    client, conn, pmc, staff
):
    refused(
        client.post(
            "/api/budgets",
            json={"account": pmc.operating_cash, "year": YEAR, "line": "rent", "months": EVEN},
        ),
        404,
        "no such owner's property account",
    )
    theirs = make_pmc(conn)
    elsewhere = conn.execute(
        "INSERT INTO trust_expense_accounts (pmc_id, name, schedule_e_line)"
        " VALUES (%s, 'Repairs', 14) RETURNING id",
        (theirs.pmc_id,),
    ).fetchone()[0]
    refused(
        client.post(
            "/api/budgets",
            json={
                "account": pmc.owners[0].account,
                "year": YEAR,
                "line": "expense account",
                "expense_account_id": str(elsewhere),
                "months": EVEN,
            },
        ),
        404,
        "no such expense account",
    )
    refused(
        client.get("/api/budgets", params={"account": pmc.owners[0].account, "year": "soon"}), 422
    )
