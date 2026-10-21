"""The general ledger on screen (src/pms/api/general_ledger.py, ADR 0005): each account's prior
balance, entries with a running balance and totals for days, a page of accounts at a time, as
trust_report_general_ledger has them. Always the session's PMC; only staff read it."""

from datetime import UTC, date, datetime

import pytest
from helpers import make_pmc, post
from people import join_as_owner, join_as_staff, join_as_tenant

JAN_5 = datetime(2026, 1, 5, 15, tzinfo=UTC)
FEB_3 = datetime(2026, 2, 3, 15, tzinfo=UTC)


@pytest.fixture
def staff(client, conn, app_conn, pmc):
    return join_as_staff(client, conn, app_conn, pmc)


def ok(answer, status=200):
    assert answer.status_code == status, answer.text
    return answer.json()


def ledger(client, **params):
    return ok(
        client.get(
            "/api/general-ledger", params={"from": "2026-02-01", "to": "2026-02-28", **params}
        )
    )


@pytest.fixture
def books(conn, pmc):
    owner = pmc.owners[0]
    post(
        conn,
        pmc.pmc_id,
        "jan",
        [(pmc.operating_cash, owner.account, "1000.00")],
        JAN_5,
        {"memo": "Rent"},
    )
    post(
        conn, pmc.pmc_id, "feb", [(owner.account, pmc.pmc_income, "80.00")], FEB_3, {"memo": "Fee"}
    )
    return pmc


def test_each_account_has_its_prior_balance_entries_and_totals(client, staff, books):
    page = ledger(client)

    names = [(a["bank"], a["account"]) for a in page["items"]]
    assert names[0] == ("Synthetic operating trust account (operating)", "book cash")
    assert page["total"] == len(names)
    owner = next(a for a in page["items"] if a["account"] == "owner_property: Owner 1 / Property 1")
    assert owner == {
        "bank": "Synthetic operating trust account (operating)",
        "account": "owner_property: Owner 1 / Property 1",
        "prior_balance": "1000.00",
        "entries": [
            {
                "posted_at": "2026-02-03 15:00:00",
                "description": f"pmc_income: {owner_pmc_name(page)} - Fee",
                "debit": "80.00",
                "credit": "0.00",
                "balance": "920.00",
            }
        ],
        "debits": "80.00",
        "credits": "0.00",
        "balance": "920.00",
    }
    # Cash moved only in January: February shows its balance and no entry.
    cash = page["items"][0]
    assert (cash["prior_balance"], cash["entries"], cash["balance"]) == ("1000.00", [], "1000.00")


def owner_pmc_name(page):
    (income,) = [a for a in page["items"] if a["account"].startswith("pmc_income: ")]
    return income["account"].removeprefix("pmc_income: ")


def test_accounts_are_found_by_words_and_come_a_page_at_a_time(client, staff, books):
    found = ledger(client, q="PROPERTY 1")
    assert [a["account"] for a in found["items"]] == ["owner_property: Owner 1 / Property 1"]

    every = ledger(client)["total"]
    first = ledger(client, per_page=2)
    second = ledger(client, per_page=2, page=2)
    assert (first["total"], len(first["items"]), second["page"]) == (every, 2, 2)
    assert first["items"][1]["account"] != second["items"][0]["account"]


def test_it_is_the_sessions_pmc(client, conn, staff, books):
    theirs = make_pmc(conn)
    post(
        conn, theirs.pmc_id, "x", [(theirs.operating_cash, theirs.owners[0].account, "5.00")], FEB_3
    )

    accounts = [a["account"] for a in ledger(client)["items"]]
    assert "owner_property: Owner 1 / Property 1" in accounts
    assert all(b["bank"].startswith("Synthetic") for b in ledger(client)["items"])
    assert sum(len(a["entries"]) for a in ledger(client)["items"]) == 2


def test_a_bank_account_shows_what_the_books_hold(client, staff, books):
    banks = {b["kind"]: b for b in ok(client.get("/api/reconcile"))}
    assert (banks["operating"]["book_cash"], banks["security_deposit"]["book_cash"]) == (
        "1000.00",
        "0.00",
    )


@pytest.mark.parametrize("caller", ["nobody", "owner", "tenant"])
def test_only_staff_read_it(client, conn, app_conn, pmc, caller):
    if caller == "owner":
        join_as_owner(client, app_conn, pmc)
    elif caller == "tenant":
        join_as_tenant(client, conn, app_conn, pmc)
    answer = client.get("/api/general-ledger", params={"from": "2026-02-01", "to": "2026-02-28"})
    assert answer.status_code == (401 if caller == "nobody" else 403)


@pytest.mark.parametrize(
    "params",
    [
        {"from": "2026-02-28", "to": "2026-02-01"},
        {"from": "2026-02-01"},
        {"from": "not a day", "to": "2026-02-28"},
        {"from": "2026-02-01", "to": "2026-02-28", "per_page": 101},
        {"from": "2026-02-01", "to": "2026-02-28", "page": 0},
    ],
)
def test_bad_input_is_refused(client, staff, params):
    answer = client.get("/api/general-ledger", params=params)
    assert answer.status_code == 422, answer.text


def test_a_backwards_period_is_refused_in_words(client, staff):
    answer = client.get(
        "/api/general-ledger", params={"from": date(2026, 2, 2), "to": date(2026, 2, 1)}
    )
    assert (
        answer.json()["detail"] == "a general ledger runs from a day to the same day or a later one"
    )
