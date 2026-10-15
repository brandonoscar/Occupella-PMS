"""Journal entries (src/pms/api/journal.py): one staff member proposes an entry, another who
holds the approve role posts it; nothing moves until then, and every trust rule holds."""

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from helpers import approve, balance, make_pmc, transfer_batch
from people import join_as_owner, join_as_staff

FEB_10 = "2026-02-10"
MEMO = "Rent deposited to the wrong property"


@pytest.fixture
def pmc(conn):
    pmc = make_pmc(conn)
    transfer_batch(
        conn,
        [(pmc.operating_cash, pmc.owners[0].account, "1000.00")],
        datetime(2026, 1, 2, tzinfo=UTC),
    )
    return pmc


@pytest.fixture
def bookkeeper(client, conn, app_conn, pmc):
    """Signed in on `client`, holding no role."""
    return join_as_staff(client, conn, app_conn, pmc, name="Bookkeeper 1", roles=[])


@pytest.fixture
def manager(other_client, conn, app_conn, pmc, bookkeeper):
    """Signed in on `other_client`, holding every role."""
    return join_as_staff(other_client, conn, app_conn, pmc, name="Manager 1")


def ok(answer, status=200):
    assert answer.status_code == status, answer.text
    return answer.json() if answer.content else None


def refused(answer, status, detail=None):
    assert answer.status_code == status, answer.text
    if detail is not None:
        assert detail in str(answer.json()["detail"]), answer.text


def lines(pmc, amount="25.00"):
    return [
        {
            "from_account": pmc.owners[0].account,
            "to_account": pmc.owners[1].account,
            "amount": amount,
        }
    ]


def propose(client, pmc, amount="25.00", event_on=FEB_10, memo=MEMO):
    return ok(
        client.post(
            "/api/journal", json={"event_on": event_on, "memo": memo, "lines": lines(pmc, amount)}
        ),
        201,
    )["id"]


def entries(client):
    return {e["id"]: e for e in ok(client.get("/api/journal"))["entries"]}


def test_a_bookkeeper_proposes_and_a_manager_posts(
    client, other_client, conn, pmc, bookkeeper, manager
):
    entry = propose(client, pmc)

    waiting = entries(other_client)[entry]
    assert {k: waiting[k] for k in ("event_on", "memo", "proposed_by", "status")} == {
        "event_on": FEB_10,
        "memo": MEMO,
        "proposed_by": "Bookkeeper 1",
        "status": "waiting",
    }
    assert waiting["lines"] == lines(pmc)
    assert entries(client)[entry]["proposed_by_me"] is True
    assert waiting["proposed_by_me"] is False
    assert balance(conn, pmc.owners[1].account) == 0

    assert ok(other_client.post(f"/api/journal/{entry}/post", json={})) == {"transfers": 1}
    assert ok(other_client.post(f"/api/journal/{entry}/post", json={})) == {
        "transfers": 1
    }  # a retry

    posted = entries(client)[entry]
    assert (posted["status"], posted["decided_by"]) == ("posted", "Manager 1")
    assert balance(conn, pmc.owners[0].account) == Decimal("975.00")
    assert balance(conn, pmc.owners[1].account) == Decimal("25.00")
    assert conn.execute(
        "SELECT staff_id FROM trust_audit_log WHERE table_name = 'trust_journal_entry_decisions'"
        " AND row_data ->> 'entry_id' = %s",
        (entry,),
    ).fetchone() == (manager.person_id,)


def test_the_page_lists_the_pmcs_accounts_in_words(client, pmc, bookkeeper, manager):
    journal = ok(client.get("/api/journal"))

    assert journal["may_post"] is False
    labels = {a["id"]: a["label"] for a in journal["accounts"]}
    assert set(labels) == set(pmc.accounts())
    operating = "Synthetic operating trust account"
    deposits = "Synthetic security_deposit trust account"
    assert labels[pmc.operating_cash] == f"{operating}: cash"
    assert labels[pmc.owners[0].account] == f"{operating}: Owner 1 / Property 1"
    assert labels[pmc.prepaid_rent] == f"{operating}: prepaid rent: Tenant 1"
    assert labels[pmc.vendor_payable] == f"{operating}: vendor: Vendor 1"
    assert labels[pmc.pmc_income] == f"{operating}: company fees"
    assert labels[pmc.tenant_deposit] == f"{deposits}: deposit: Tenant 1"
    # Each bank's cash first, then what it holds.
    assert [a["id"] for a in journal["accounts"]][0] == pmc.operating_cash


def test_whoever_proposed_it_does_not_post_it(client, other_client, conn, pmc, bookkeeper, manager):
    entry = propose(other_client, pmc)

    refused(other_client.post(f"/api/journal/{entry}/post", json={}), 403, "does not post it")
    refused(
        client.post(f"/api/journal/{entry}/post", json={}),
        403,
        "Bookkeeper 1 may not post a journal entry",
    )
    assert balance(conn, pmc.owners[1].account) == 0
    assert entries(client)[entry]["status"] == "waiting"


def test_a_declined_entry_is_never_posted(client, other_client, conn, pmc, bookkeeper, manager):
    entry = propose(client, pmc)

    ok(other_client.post(f"/api/journal/{entry}/decline", json={"reason": " Entered twice "}), 204)

    refused(
        other_client.post(f"/api/journal/{entry}/post", json={}),
        409,
        "was declined; it is never posted",
    )
    refused(
        client.post(f"/api/journal/{entry}/decline", json={"reason": "Again"}),
        409,
        "decided already",
    )
    declined = entries(client)[entry]
    assert (declined["status"], declined["decided_by"], declined["reason"]) == (
        "declined",
        "Manager 1",
        "Entered twice",
    )
    assert balance(conn, pmc.owners[1].account) == 0


def test_a_post_the_ledger_refuses_says_why_and_moves_nothing(
    client, other_client, conn, pmc, bookkeeper, manager
):
    too_much = propose(client, pmc, "1000.01")
    refused(
        other_client.post(f"/api/journal/{too_much}/post", json={}),
        409,
        "not enough money: the owner's account would be $0.01 short",
    )

    approve(
        conn,
        pmc.pmc_id,
        pmc.operating_bank_id,
        datetime(2026, 1, 1, tzinfo=UTC),
        datetime(2026, 2, 1, tzinfo=UTC),
    )
    closed = propose(client, pmc, event_on="2026-01-31")
    refused(
        other_client.post(f"/api/journal/{closed}/post", json={}),
        409,
        "that day is in a period already reconciled and closed: date it in the next open one",
    )

    across = ok(
        client.post(
            "/api/journal",
            json={
                "event_on": FEB_10,
                "memo": "Deposit held in the operating account",
                "lines": [
                    {
                        "from_account": pmc.owners[0].account,
                        "to_account": pmc.tenant_deposit,
                        "amount": "300.00",
                    }
                ],
            },
        ),
        201,
    )["id"]
    refused(
        other_client.post(f"/api/journal/{across}/post", json={}),
        409,
        "money moves to another trust bank account only with its cash: this would leave one off"
        " by $300.00",
    )

    assert balance(conn, pmc.owners[0].account) == Decimal("1000.00")
    assert {entries(client)[e]["status"] for e in (too_much, closed, across)} == {"waiting"}


def test_only_the_pmcs_staff_use_the_journal(client, other_client, conn, app_conn, pmc, bookkeeper):
    entry = propose(client, pmc)

    for answer in (
        other_client.get("/api/journal"),
        other_client.post(
            "/api/journal", json={"event_on": FEB_10, "memo": MEMO, "lines": lines(pmc)}
        ),
        other_client.post(f"/api/journal/{entry}/post", json={}),
        other_client.post(f"/api/journal/{entry}/decline", json={"reason": "No"}),
    ):
        refused(answer, 401)

    join_as_owner(other_client, app_conn, pmc)
    refused(other_client.get("/api/journal"), 403, "only the PMC's staff")
    refused(other_client.post(f"/api/journal/{entry}/post", json={}), 403, "only the PMC's staff")

    # Another PMC's manager sees none of it and finds no such entry or account.
    other = make_pmc(conn)
    other_client.post("/api/sign-out")
    join_as_staff(other_client, conn, app_conn, other, name="Manager 2")
    assert entries(other_client) == {}
    refused(other_client.post(f"/api/journal/{entry}/post", json={}), 404, "no such journal entry")
    refused(
        other_client.post(f"/api/journal/{entry}/decline", json={"reason": "No"}),
        404,
        "no such journal entry",
    )
    refused(
        other_client.post(
            "/api/journal", json={"event_on": FEB_10, "memo": MEMO, "lines": lines(pmc)}
        ),
        404,
        "no such ledger account",
    )
    assert entries(client)[entry]["status"] == "waiting"


@pytest.mark.parametrize(
    "change",
    [
        {"memo": "   "},
        {"memo": "x" * 501},
        {"event_on": "not a day"},
        {"lines": []},
        {"lines": "many"},
    ],
)
def test_an_entry_needs_a_day_a_memo_and_one_to_twenty_lines(client, pmc, bookkeeper, change):
    body = {"event_on": FEB_10, "memo": MEMO, "lines": lines(pmc)} | change
    refused(client.post("/api/journal", json=body), 422)
    assert entries(client) == {}


@pytest.mark.parametrize("amount", ["0", "-1.00", "1.001", "10000000.01", "ten"])
def test_a_line_moves_whole_cents_between_two_accounts(client, pmc, bookkeeper, amount):
    refused(
        client.post(
            "/api/journal", json={"event_on": FEB_10, "memo": MEMO, "lines": lines(pmc, amount)}
        ),
        422,
    )
    same = [{"from_account": pmc.pmc_income, "to_account": pmc.pmc_income, "amount": "1.00"}]
    refused(
        client.post("/api/journal", json={"event_on": FEB_10, "memo": MEMO, "lines": same}),
        422,
        "from one account to another",
    )
    many = lines(pmc) * 21
    refused(
        client.post("/api/journal", json={"event_on": FEB_10, "memo": MEMO, "lines": many}), 422
    )
    assert entries(client) == {}


def test_a_decline_needs_a_reason_and_a_real_entry(client, pmc, bookkeeper):
    entry = propose(client, pmc)
    for reason in ("", "  ", "x" * 1001):
        refused(client.post(f"/api/journal/{entry}/decline", json={"reason": reason}), 422)
    refused(client.post(f"/api/journal/{entry}/decline", json={}), 422)
    refused(client.post("/api/journal/not-an-id/post", json={}), 422)
    refused(
        client.post("/api/journal/3f1c2b9a-0000-4000-8000-000000000001/post", json={}),
        404,
        "no such journal entry",
    )
    assert entries(client)[entry]["status"] == "waiting"


def test_the_portal_reads_no_float(client, pmc, bookkeeper):
    propose(client, pmc, "0.10")
    (entry,) = entries(client).values()
    assert entry["lines"][0]["amount"] == "0.10"
    assert date.fromisoformat(entry["event_on"]) == date(2026, 2, 10)
