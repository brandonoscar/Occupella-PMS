"""Owners through the API (src/pms/api/directory.py, src/pms/api/payables.py, ADR 0006): each
owner once with what their accounts hold, keep back and may pay out; an owner's page with their
agreements' terms, portal login and statement; Pay owners, a draw from each account chosen, all
or none, with how it was sent; and the management fees page, which says what Collect fees would
take without taking it. Always the session's PMC."""

import json
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from helpers import add_agreement, balance, contribute, make_pmc
from people import PASSWORD, invite_token, join_as_owner, join_as_staff, join_as_tenant

TODAY = datetime.now(UTC).date()
DAY = timedelta(days=1)


@pytest.fixture
def staff(client, conn, app_conn, pmc):
    return join_as_staff(client, conn, app_conn, pmc)


def ok(answer, status=200):
    assert answer.status_code == status, answer.text
    return answer.json()


def post(client, path, **body):
    return client.post(path, json=json.loads(json.dumps(body, default=str)))


def fund(conn, pmc, n, amount, reserve="0"):
    """Owner n's property account holds `amount`, keeping back `reserve`."""
    account = pmc.owners[n].account
    add_agreement(conn, pmc.pmc_id, account, TODAY - 30 * DAY, reserve=reserve, minimum_fee="10.00")
    contribute(conn, pmc.pmc_id, account, f"fund/{uuid.uuid4()}", amount)
    return account


def names(conn, pmc, n):
    """Owner n's name and their property's."""
    return conn.execute(
        "SELECT o.display_name, p.display_name FROM trust_owners AS o, trust_properties AS p"
        " WHERE o.id = %s AND p.id = %s",
        (pmc.owners[n].owner_id, pmc.owners[n].property_id),
    ).fetchone()


def test_each_owner_once_with_what_they_hold_keep_back_and_may_pay_out(client, conn, staff, pmc):
    fund(conn, pmc, 0, "500.00", reserve="100.00")
    fund(conn, pmc, 1, "50.00", reserve="80.00")

    rows = {r["name"]: r for r in ok(client.get("/api/owners"))["items"]}

    assert [
        (r["held"], r["reserve"], r["available"])
        for r in (rows[names(conn, pmc, n)[0]] for n in (0, 1))
    ] == [("500.00", "100.00", "400.00"), ("50.00", "80.00", "0.00")]


def test_an_owners_page_shows_their_terms_portal_and_statement(
    client, other_client, conn, app_conn, staff, pmc
):
    account = fund(conn, pmc, 0, "500.00", reserve="100.00")
    owner = pmc.owners[0].owner_id

    page = ok(client.get(f"/api/owners/{owner}"))
    assert (page["portal"], page["login_email"]) == ("no login", None)
    [terms] = page["agreements"]
    assert terms == {
        "account": account,
        "starts_on": str(TODAY - 30 * DAY),
        "fee_percent": "0",
        "minimum_fee": "10.00",
        "flat_fee": "0.00",
        "leasing_fee_percent": "0",
        "reserve": "100.00",
        "approval_limit": "0.00",
    }

    token = invite_token(app_conn, pmc.pmc_id, "owner", owner, "owner.a@example.com")
    assert ok(client.get(f"/api/owners/{owner}"))["portal"] == "invited"
    ok(other_client.post("/api/invites/accept", json={"token": token, "password": PASSWORD}))
    assert ok(client.get(f"/api/owners/{owner}"))["portal"] == "active"

    statement = ok(client.get(f"/api/owners/{owner}/statement?start={TODAY - 7 * DAY}&end={TODAY}"))
    assert statement["title"] == f"Owner statement, {TODAY - 7 * DAY} to {TODAY}"
    assert any("500.00" in json.dumps(row) for row in statement["rows"])


def test_staff_download_an_owners_statement(client, conn, staff, pmc):
    fund(conn, pmc, 0, "500.00")
    owner = pmc.owners[0].owner_id
    path = f"/api/owners/{owner}/statement/download?start={TODAY - DAY}&end={TODAY}"

    pdf = client.get(path)
    csv = client.get(path + "&format=csv")

    assert pdf.status_code == 200 and pdf.content.startswith(b"%PDF")
    assert "500.00" in csv.text
    assert client.get(path + "&format=doc").status_code == 422


def test_another_companys_owner_and_bad_days_are_refused(client, conn, staff):
    theirs = make_pmc(conn).owners[0].owner_id
    for path in ("statement", "statement/download"):
        answer = client.get(f"/api/owners/{theirs}/{path}?start={TODAY}&end={TODAY}")
        assert answer.status_code == 404, path
    not_a_day = client.get(f"/api/owners/{theirs}/statement?start={TODAY}&end=x")
    assert not_a_day.status_code == 422


def test_pay_owners_pays_each_account_and_records_how(client, conn, staff, pmc):
    first = fund(conn, pmc, 0, "500.00", reserve="100.00")
    second = fund(conn, pmc, 1, "300.00")
    run = {
        "request_id": uuid.uuid4(),
        "on": TODAY,
        "method": "EFT",
        "payouts": [
            {"account": first, "amount": "400.00"},
            {"account": second, "amount": "120.00"},
        ],
    }

    paid = ok(post(client, "/api/payables/draws/batch", **run), 201)
    again = ok(post(client, "/api/payables/draws/batch", **(run | {"method": "check"})), 201)

    assert [(p["owner"], p["property"], p["amount"]) for p in paid] == [
        (*names(conn, pmc, 0), "400.00"),
        (*names(conn, pmc, 1), "120.00"),
    ]
    assert [p["transfer_id"] for p in again] == [p["transfer_id"] for p in paid]
    assert (balance(conn, first), balance(conn, second)) == (100, 180)
    assert conn.execute(
        "SELECT method, recorded_by FROM trust_payout_methods WHERE pmc_id = %s",
        (pmc.pmc_id,),
    ).fetchall() == [("EFT", staff.person_id), ("EFT", staff.person_id)]


def test_pay_owners_pays_none_when_one_cant_be_paid(client, conn, staff, pmc):
    first = fund(conn, pmc, 0, "500.00")
    second = fund(conn, pmc, 1, "50.00", reserve="40.00")

    answer = post(
        client,
        "/api/payables/draws/batch",
        request_id=uuid.uuid4(),
        on=TODAY,
        method="check",
        payouts=[{"account": first, "amount": "500.00"}, {"account": second, "amount": "20.00"}],
    )

    assert answer.status_code == 409
    owner, prop = names(conn, pmc, 1)
    assert answer.json()["detail"] == (
        f"{owner}, {prop}: only $10.00 is available to draw"
        " (the balance less the $40.00 reserve); $20.00 is more than that"
    )
    assert (balance(conn, first), balance(conn, second)) == (500, 50)


@pytest.mark.parametrize(
    ("change", "status"),
    [
        ({"payouts": []}, 422),
        ({"method": "wire"}, 422),
        ({"on": str(TODAY + DAY)}, 422),
        ({"payouts": [{"account": "nobody", "amount": "1.00"}]}, 404),
        (
            {"payouts": [{"account": "a", "amount": "1.00"}, {"account": "a", "amount": "2.00"}]},
            422,
        ),
        ({"payouts": [{"account": "a", "amount": "0"}]}, 422),
    ],
)
def test_pay_owners_takes_a_day_a_method_and_each_account_once(client, staff, change, status):
    body = {"request_id": str(uuid.uuid4()), "on": str(TODAY), "method": "check"} | change
    assert client.post("/api/payables/draws/batch", json=body).status_code == status


def test_another_companys_account_is_not_paid(client, conn, staff):
    other = make_pmc(conn)
    theirs = fund(conn, other, 0, "100.00")
    answer = post(
        client,
        "/api/payables/draws/batch",
        request_id=uuid.uuid4(),
        on=TODAY,
        method="check",
        payouts=[{"account": theirs, "amount": "10.00"}],
    )
    assert answer.status_code == 404
    assert balance(conn, theirs) == 100


def test_pay_owners_lists_a_groups_or_a_propertys_accounts(client, conn, staff, pmc):
    fund(conn, pmc, 0, "10.00")
    fund(conn, pmc, 1, "20.00")
    prop = pmc.owners[1].property_id

    narrowed = ok(client.get(f"/api/payables/owners?property={prop}"))

    assert [a["property"] for a in narrowed] == [names(conn, pmc, 1)[1]]
    assert len(ok(client.get("/api/payables/owners"))) == 2


def test_the_fees_page_says_what_collect_fees_takes_and_moves_nothing(client, conn, staff, pmc):
    account = fund(conn, pmc, 0, "500.00")
    empty = pmc.owners[1].account
    add_agreement(conn, pmc.pmc_id, empty, TODAY - 30 * DAY, minimum_fee="10.00")
    period = f"period_start={TODAY - 10 * DAY}&period_end={TODAY - 3 * DAY}"

    due = ok(client.get(f"/api/payables/management-fees?{period}"))

    assert [(d["account"], d["taken"], d["fee"]) for d in due] == [
        (account, False, "10.00"),
        (empty, False, None),
    ]
    assert due[1]["refused"] == "not enough money: the owner's account would be $10.00 short"
    assert balance(conn, account) == 500
    assert conn.execute(
        "SELECT count(*) FROM trust_management_fees WHERE pmc_id = %s", (pmc.pmc_id,)
    ).fetchone() == (0,)

    taken = ok(
        post(
            client,
            "/api/payables/management-fees",
            period_start=TODAY - 10 * DAY,
            period_end=TODAY - 3 * DAY,
            on=TODAY,
        )
    )
    after = ok(client.get(f"/api/payables/management-fees?{period}"))

    assert [t["fee"] for t in taken] == ["10.00", None]
    assert [(d["taken"], d["fee"]) for d in after] == [(True, "10.00"), (False, None)]
    assert (
        client.get(
            f"/api/payables/management-fees?period_start={TODAY}&period_end={TODAY}"
        ).status_code
        == 422
    )


@pytest.mark.parametrize("caller", ["nobody", "owner", "tenant"])
@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("post", "/api/payables/draws/batch"),
        ("get", f"/api/payables/management-fees?period_start={TODAY}&period_end={TODAY + DAY}"),
        ("get", f"/api/owners/{uuid.uuid4()}/statement?start={TODAY}&end={TODAY}"),
        ("get", f"/api/owners/{uuid.uuid4()}/statement/download?start={TODAY}&end={TODAY}"),
    ],
)
def test_only_staff_pay_owners_and_read_fees_and_statements(
    client, conn, app_conn, pmc, caller, method, path
):
    if caller == "owner":
        join_as_owner(client, app_conn, pmc)
    elif caller == "tenant":
        join_as_tenant(client, conn, app_conn, pmc)
    answer = client.get(path) if method == "get" else client.post(path, json={})
    assert answer.status_code == (401 if caller == "nobody" else 403)
