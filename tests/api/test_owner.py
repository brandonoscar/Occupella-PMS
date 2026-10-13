"""The owner portal's endpoints (src/pms/api/owner.py): an owner sees their own properties,
statement and year-end package, and nobody else's."""

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from helpers import make_pmc, transfer_batch
from people import join_as_owner, join_as_staff, join_as_tenant

JAN_3 = datetime(2026, 1, 3, 12, tzinfo=UTC)
FEB_1 = datetime(2026, 2, 1, 0, tzinfo=UTC)


def rent(conn, pmc, n, amount, when=JAN_3):
    transfer_batch(conn, [(pmc.operating_cash, pmc.owners[n].account, amount)], when)


def test_an_owner_sees_each_of_their_properties_and_what_it_holds(client, conn, app_conn, pmc):
    rent(conn, pmc, 0, "1500.00")
    rent(conn, pmc, 1, "999.00")  # owner 2's: never shown to owner 1
    join_as_owner(client, app_conn, pmc)

    answer = client.get("/api/owner/properties")

    assert answer.status_code == 200
    assert answer.json() == [
        {
            "account_id": pmc.owners[0].account,
            "property_id": str(pmc.owners[0].property_id),
            "name": "Property 1",
            "bank": "Synthetic operating trust account",
            "balance": "1500.00",
        }
    ]


def test_the_statement_is_the_owners_report_over_whole_days(client, conn, app_conn, pmc):
    rent(conn, pmc, 0, "1500.00")
    rent(conn, pmc, 0, "10.00", FEB_1)  # February 1: outside a statement ending January 31
    join_as_owner(client, app_conn, pmc)
    direct = conn.execute(
        "SELECT * FROM trust_report_owner_statement(%s, %s, %s, %s)",
        (pmc.pmc_id, pmc.owners[0].owner_id, datetime(2026, 1, 1, tzinfo=UTC), FEB_1),
    ).fetchall()

    answer = client.get("/api/owner/statement", params={"start": "2026-01-01", "end": "2026-01-31"})

    assert answer.status_code == 200
    statement = answer.json()
    assert statement["title"] == "Owner statement, 2026-01-01 to 2026-01-31"
    assert statement["columns"][:4] == ["line", "property", "posted_on", "item"]
    assert len(statement["rows"]) == len(direct)
    closing = [row for row in statement["rows"] if row[3] == "all properties: closing balance"]
    assert closing[0][-1] == "1500.00" and Decimal(closing[0][-1]) == direct[-1][-1]
    assert statement["rows"][0][3:5] == ["PMC", direct[0][4]]


@pytest.mark.parametrize(("fmt", "magic"), [("pdf", b"%PDF-"), ("csv", b"line,"), ("xlsx", b"PK")])
def test_the_owner_downloads_their_statement(client, conn, app_conn, pmc, fmt, magic):
    rent(conn, pmc, 0, "1500.00")
    join_as_owner(client, app_conn, pmc)

    answer = client.get(
        "/api/owner/statement/download",
        params={"start": "2026-01-01", "end": "2026-01-31", "format": fmt},
    )

    assert answer.status_code == 200
    assert answer.content.startswith(magic)
    assert answer.headers["content-disposition"] == (
        f'attachment; filename="owner-statement-2026-01-01-2026-01-31.{fmt}"'
    )


def test_the_year_end_package_is_the_owners_report_for_the_year(client, conn, app_conn, pmc):
    rent(conn, pmc, 0, "1500.00")
    rent(conn, pmc, 0, "10.00", datetime(2027, 1, 1, tzinfo=UTC))  # the next year
    rent(conn, pmc, 1, "999.00")  # owner 2's: never shown to owner 1
    join_as_owner(client, app_conn, pmc)
    direct = conn.execute(
        "SELECT * FROM trust_report_owner_year_end(%s, %s, 2026)",
        (pmc.pmc_id, pmc.owners[0].owner_id),
    ).fetchall()

    answer = client.get("/api/owner/year-end", params={"year": 2026})

    assert answer.status_code == 200
    package = answer.json()
    assert package["title"] == "Year-end package, 2026"
    assert package["columns"] == ["line", "property", "item", "amount"]
    assert len(package["rows"]) == len(direct)
    closing = [row for row in package["rows"] if row[2] == "all properties: closing balance"]
    assert closing == [["21", None, "all properties: closing balance", "1500.00"]]
    flat = [cell for row in package["rows"] for cell in row]
    assert "999.00" not in flat and "Owner 1" in flat


@pytest.mark.parametrize(("fmt", "magic"), [("pdf", b"%PDF-"), ("csv", b"line,"), ("xlsx", b"PK")])
def test_the_owner_downloads_their_year_end_package(client, conn, app_conn, pmc, fmt, magic):
    rent(conn, pmc, 0, "1500.00")
    join_as_owner(client, app_conn, pmc)

    answer = client.get("/api/owner/year-end/download", params={"year": 2026, "format": fmt})

    assert answer.status_code == 200
    assert answer.content.startswith(magic)
    assert answer.headers["content-disposition"] == (f'attachment; filename="year-end-2026.{fmt}"')


@pytest.mark.parametrize(
    "params", [{}, {"year": "x"}, {"year": 1899}, {"year": 9999}, {"year": 2026, "format": "docx"}]
)
def test_a_bad_year_is_refused(client, app_conn, pmc, params):
    join_as_owner(client, app_conn, pmc)

    for path in ("/api/owner/year-end", "/api/owner/year-end/download"):
        if "format" in params and path == "/api/owner/year-end":
            continue
        assert client.get(path, params=params).status_code == 422, path


def test_another_owners_statement_is_never_reachable(client, other_client, conn, app_conn, pmc):
    rent(conn, pmc, 1, "777.00")
    join_as_owner(client, app_conn, pmc, n=0)
    other = make_pmc(conn)
    rent(conn, other, 0, "888.00")

    statement = client.get(
        "/api/owner/statement",
        params={
            "start": "2026-01-01",
            "end": "2026-01-31",
            "owner_id": str(pmc.owners[1].owner_id),
        },
    ).json()

    flat = [cell for row in statement["rows"] for cell in row]
    assert "777.00" not in flat and "888.00" not in flat
    assert "Owner 1" in flat


@pytest.mark.parametrize("caller", ["nobody", "staff", "tenant"])
def test_only_an_owner_reaches_the_owner_endpoints(client, conn, app_conn, pmc, caller):
    if caller == "staff":
        join_as_staff(client, conn, app_conn, pmc)
    elif caller == "tenant":
        join_as_tenant(client, conn, app_conn, pmc)
    expected = 401 if caller == "nobody" else 403
    period = {"start": "2026-01-01", "end": "2026-01-31"}

    answers = [
        client.get("/api/owner/properties"),
        client.get("/api/owner/statement", params=period),
        client.get("/api/owner/statement/download", params=period),
        client.get("/api/owner/year-end", params={"year": 2026}),
        client.get("/api/owner/year-end/download", params={"year": 2026}),
    ]

    assert [a.status_code for a in answers] == [expected] * 5


@pytest.mark.parametrize(
    ("params", "detail"),
    [
        ({"start": "2026-02-01", "end": "2026-01-01"}, "from a day to the same day or a later one"),
        ({"start": "2000-01-01", "end": "2026-01-01"}, "at most 3660 days"),
        ({"start": "not a day", "end": "2026-01-01"}, None),
        ({"start": "2026-01-01"}, None),
        ({"start": "2026-01-01", "end": "2026-01-31", "format": "docx"}, None),
    ],
)
def test_a_bad_statement_period_is_refused(client, app_conn, pmc, params, detail):
    join_as_owner(client, app_conn, pmc)

    for path in ("/api/owner/statement", "/api/owner/statement/download"):
        if "format" in params and path == "/api/owner/statement":
            continue
        answer = client.get(path, params=params)
        assert answer.status_code == 422, path
        if detail:
            assert detail in answer.json()["detail"]
