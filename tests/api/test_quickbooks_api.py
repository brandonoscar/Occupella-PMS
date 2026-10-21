"""QuickBooks through the API (src/pms/api/quickbooks.py; the founder's review q117): staff read
each ledger account's name in QuickBooks and set it (setup's), and download the ledger's
transfers over days as journal entries for QuickBooks Desktop (IIF) or Online (CSV)."""

import csv
import io
from datetime import UTC, datetime

import pytest
from helpers import make_pmc, transfer_batch
from people import join_as_owner, join_as_staff, join_as_tenant

ACCOUNTS = "/api/quickbooks/accounts"
JOURNAL = "/api/quickbooks/journal"
JANUARY = {"from": "2026-01-01", "to": "2026-01-31"}


@pytest.fixture
def staff(client, conn, app_conn, pmc):
    return join_as_staff(client, conn, app_conn, pmc)


def ok(answer, status=200):
    assert answer.status_code == status, answer.text
    return answer.json() if answer.content else None


def test_staff_name_accounts_for_quickbooks_and_put_back_the_pms_name(client, conn, staff, pmc):
    before = {a["ledger_account_id"]: a for a in ok(client.get(ACCOUNTS))}
    cash = before[pmc.operating_cash]

    ok(client.post(f"{ACCOUNTS}/{pmc.operating_cash}", json={"name": " Trust checking "}), 204)
    ok(client.post(f"{ACCOUNTS}/{pmc.operating_cash}", json={"name": "Trust checking"}), 204)
    named = {a["ledger_account_id"]: a for a in ok(client.get(ACCOUNTS))}
    ok(client.post(f"{ACCOUNTS}/{pmc.operating_cash}", json={"name": None}), 204)
    back = {a["ledger_account_id"]: a for a in ok(client.get(ACCOUNTS))}
    rows = conn.execute(
        "SELECT name, recorded_by FROM trust_quickbooks_accounts WHERE pmc_id = %s ORDER BY id",
        (pmc.pmc_id,),
    ).fetchall()

    assert cash["kind"] == "bank_cash" and cash["name"] is None
    assert cash["default_name"].startswith("Trust cash:")
    assert named[pmc.operating_cash]["name"] == "Trust checking"
    assert back[pmc.operating_cash]["name"] is None
    # The same name again wrote nothing; each change names who made it.
    assert rows == [("Trust checking", staff.person_id), (None, staff.person_id)]


def test_staff_download_the_journal_for_each_quickbooks(client, conn, staff, pmc):
    transfer_batch(
        conn,
        [(pmc.operating_cash, pmc.owners[0].account, "1500.00")],
        datetime(2026, 1, 3, 12, tzinfo=UTC),
    )
    ok(client.post(f"{ACCOUNTS}/{pmc.operating_cash}", json={"name": "Trust checking"}), 204)

    desktop = client.get(JOURNAL, params={**JANUARY, "format": "iif"})
    online = client.get(JOURNAL, params={**JANUARY, "format": "csv"})

    assert desktop.status_code == 200
    assert desktop.headers["content-type"].startswith("text/plain")
    assert desktop.headers["content-disposition"] == (
        'attachment; filename="quickbooks-journal-2026-01-01-to-2026-01-31.iif"'
    )
    assert "TRNS\t\tGENERAL JOURNAL\t01/03/2026\tTrust checking\t1500.00" in desktop.text
    rows = list(csv.DictReader(io.StringIO(online.text)))
    assert [(r["Account Name"], r["Debits"], r["Credits"]) for r in rows] == [
        ("Trust checking", "1500.00", ""),
        ("Owner funds:Owner 1 - Property 1", "", "1500.00"),
    ]


def test_another_companys_account_is_not_found(client, conn, staff):
    theirs = make_pmc(conn)

    answer = client.post(f"{ACCOUNTS}/{theirs.operating_cash}", json={"name": "Not mine"})

    assert (answer.status_code, answer.json()["detail"]) == (404, "no such ledger account")


@pytest.mark.parametrize(
    ("body", "status"),
    [
        ({"name": "Trust:"}, 422),
        ({"name": "a\tb"}, 422),
        ({"name": ""}, 422),
        ({"name": "x" * 201}, 422),
        ({}, 422),
    ],
)
def test_a_bad_name_is_refused(client, staff, pmc, body, status):
    assert client.post(f"{ACCOUNTS}/{pmc.operating_cash}", json=body).status_code == status


@pytest.mark.parametrize(
    "params",
    [
        {"from": "2026-01-31", "to": "2026-01-01", "format": "iif"},
        {**JANUARY, "format": "qbo"},
        {"from": "soon", "to": "2026-01-31", "format": "csv"},
        JANUARY,
    ],
)
def test_a_bad_journal_is_refused(client, staff, params):
    assert client.get(JOURNAL, params=params).status_code == 422


@pytest.mark.parametrize("caller", ["nobody", "owner", "tenant"])
def test_only_staff_reach_quickbooks(client, conn, app_conn, pmc, caller):
    if caller == "owner":
        join_as_owner(client, app_conn, pmc)
    elif caller == "tenant":
        join_as_tenant(client, conn, app_conn, pmc)
    status = 401 if caller == "nobody" else 403

    assert client.get(ACCOUNTS).status_code == status
    assert client.get(JOURNAL, params={**JANUARY, "format": "csv"}).status_code == status
    assert client.post(f"{ACCOUNTS}/{pmc.operating_cash}", json={"name": "X"}).status_code == status


def test_a_custom_role_without_setup_reads_but_names_nothing(
    client, other_client, conn, app_conn, staff, pmc
):
    clerk = join_as_staff(other_client, conn, app_conn, pmc, name="Clerk 1", roles=[])
    role = ok(client.post("/api/custom-roles", json={"name": "Books", "areas": ["rent"]}), 201)
    ok(
        client.post(
            f"/api/custom-roles/{role['id']}/staff",
            json={"staff_id": str(clerk.person_id), "granted": True},
        ),
        204,
    )

    refused = other_client.post(f"{ACCOUNTS}/{pmc.operating_cash}", json={"name": "Trust"})

    assert refused.status_code == 403
    assert other_client.get(ACCOUNTS).status_code == 200
    assert other_client.get(JOURNAL, params={**JANUARY, "format": "iif"}).status_code == 200
