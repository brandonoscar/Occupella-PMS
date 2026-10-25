"""The owner portal's endpoints (src/pms/api/owner.py): an owner sees their own properties,
statement and year-end package, and nobody else's, and approves or declines their own bills
over the approval limit."""

import uuid
from datetime import UTC, date, datetime
from decimal import Decimal

import psycopg
import pytest
from helpers import add_agreement, enter_bill, make_pmc, set_aside_bill, transfer_batch
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
        client.get("/api/owner/bills"),
        client.post(f"/api/owner/bills/{uuid.uuid4()}/approval", json={}),
        client.post(f"/api/owner/bills/{uuid.uuid4()}/decline", json={"reason": "No"}),
    ]

    assert [a.status_code for a in answers] == [expected] * 8


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


# --- bills over the approval limit ---------------------------------------------------------

JAN_1, JAN_10 = date(2026, 1, 1), date(2026, 1, 10)


def bills_of(conn, pmc):
    """Owner 1's 600.00 roof bill over their 500.00 limit and an 80.00 one under it; owner 2's
    700.00 bill. Owner 1's account holds 1000.00."""
    for n in (0, 1):
        add_agreement(conn, pmc.pmc_id, pmc.owners[n].account, JAN_1, approval_limit="500")
    rent(conn, pmc, 0, "1000.00")

    def bill(n, reference, amount):
        return enter_bill(
            conn,
            pmc.pmc_id,
            pmc.vendor_id,
            pmc.owners[n].account,
            reference,
            JAN_10,
            JAN_10,
            amount,
        )

    return bill(0, "ROOF-1", "600.00"), bill(0, "LIGHT-1", "80.00"), bill(1, "OTHER-1", "700.00")


def test_an_owner_sees_the_bills_waiting_for_them_and_approves_one(client, conn, app_conn, pmc):
    roof, _, _ = bills_of(conn, pmc)
    join_as_owner(client, app_conn, pmc)

    waiting = client.get("/api/owner/bills")
    approved = client.post(f"/api/owner/bills/{roof}/approval", json={})
    after = client.get("/api/owner/bills").json()

    assert waiting.status_code == 200
    assert waiting.json() == [
        {
            "id": str(roof),
            "vendor": "Vendor 1",
            "reference": "ROOF-1",
            "property": "Property 1",
            "bill_date": "2026-01-10",
            "due_on": "2026-01-10",
            "amount": "600.00",
            "memo": "Repair",
            "approval_limit": "500.00",
            "markup": None,
            "markup_percent": None,
            "decision": None,
            "decided_on": None,
            "decided_by": None,
            "reason": None,
            "short": None,
            "needed": None,
        }
    ]
    assert approved.status_code == 201
    assert (after[0]["decision"], after[0]["decided_by"]) == (
        "approved",
        "Owner 1, in the owner portal",
    )
    assert conn.execute(
        "SELECT owner_id FROM trust_bill_approvals WHERE bill_id = %s", (roof,)
    ).fetchone() == (pmc.owners[0].owner_id,)
    set_aside_bill(conn, pmc.pmc_id, roof, datetime(2026, 1, 11, tzinfo=UTC))  # staff may now


def test_an_owner_declines_a_bill_and_it_is_never_paid(client, conn, app_conn, pmc):
    roof, _, _ = bills_of(conn, pmc)
    join_as_owner(client, app_conn, pmc)

    declined = client.post(
        f"/api/owner/bills/{roof}/decline", json={"reason": "  Get a second quote  "}
    )

    assert declined.status_code == 201
    shown = client.get("/api/owner/bills").json()[0]
    assert (shown["decision"], shown["reason"]) == ("declined", "Get a second quote")
    with pytest.raises(psycopg.errors.CheckViolation, match="declined"):
        set_aside_bill(conn, pmc.pmc_id, roof, datetime(2026, 1, 11, tzinfo=UTC))


def test_an_owner_decides_a_bill_once(client, conn, app_conn, pmc):
    roof, _, _ = bills_of(conn, pmc)
    join_as_owner(client, app_conn, pmc)
    client.post(f"/api/owner/bills/{roof}/approval", json={})

    again = client.post(f"/api/owner/bills/{roof}/approval", json={})
    declined = client.post(f"/api/owner/bills/{roof}/decline", json={"reason": "Changed my mind"})

    assert [again.status_code, declined.status_code] == [409, 409]
    assert again.json()["detail"] == "this bill has its decision already"


def test_a_bill_whose_money_was_taken_is_not_declined_in_the_portal(client, conn, app_conn, pmc):
    _, light, _ = bills_of(conn, pmc)
    set_aside_bill(conn, pmc.pmc_id, light, datetime(2026, 1, 11, tzinfo=UTC))
    join_as_owner(client, app_conn, pmc)

    answer = client.post(f"/api/owner/bills/{light}/decline", json={"reason": "Too much"})

    assert answer.status_code == 409
    assert answer.json()["detail"] == "money was taken from your account to pay this bill already"


def test_another_owners_bill_reads_as_not_found(client, conn, app_conn, pmc):
    _, _, other = bills_of(conn, pmc)
    join_as_owner(client, app_conn, pmc)

    answers = [
        client.post(f"/api/owner/bills/{other}/approval", json={}),
        client.post(f"/api/owner/bills/{other}/decline", json={"reason": "No"}),
        client.post(f"/api/owner/bills/{uuid.uuid4()}/approval", json={}),
    ]

    assert [a.status_code for a in answers] == [404, 404, 404]
    assert "OTHER-1" not in {b["reference"] for b in client.get("/api/owner/bills").json()}
    assert conn.execute(
        "SELECT (SELECT count(*) FROM trust_bill_approvals WHERE pmc_id = %(pmc)s),"
        " (SELECT count(*) FROM trust_bill_declines WHERE pmc_id = %(pmc)s)",
        {"pmc": pmc.pmc_id},
    ).fetchone() == (0, 0)


@pytest.mark.parametrize(
    ("path", "body"),
    [
        ("decline", {"reason": ""}),
        ("decline", {"reason": "   "}),
        ("decline", {"reason": "x" * 1001}),
        ("decline", {}),
        ("approval", None),
    ],
)
def test_a_decision_needs_a_bill_and_a_decline_a_reason(client, conn, app_conn, pmc, path, body):
    roof, _, _ = bills_of(conn, pmc)
    join_as_owner(client, app_conn, pmc)

    target = "not-a-bill" if body is None else str(roof)
    answer = client.post(f"/api/owner/bills/{target}/{path}", json=body or {})

    assert answer.status_code == 422
