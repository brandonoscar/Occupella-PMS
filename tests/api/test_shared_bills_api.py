"""Bills shared across properties and the company's markup, through the API
(src/pms/api/payables.py; the founder's review, q062 and q065; migration 0083).

Staff enter one vendor invoice shared across properties: a bill for each owner's property
account's share, under the invoice's reference with /1, /2..., each following every bill rule on
its own. Staff add the company's markup to a bill, at the percent the owner's agreement sets; it
is taken with the bill's money when the bill is first paid, and the owner sees it before then.
Always the session's PMC."""

import base64
import json
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from helpers import add_agreement, balance, bank_sums, make_pmc, post
from people import join_as_owner, join_as_staff, join_as_tenant

from pms import accounts, email

NOW = datetime.now(UTC)
TODAY = NOW.date()
DAY = timedelta(days=1)
PDF = b"%PDF-1.4 synthetic invoice\n"


@pytest.fixture
def staff(client, conn, app_conn, pmc):
    return join_as_staff(client, conn, app_conn, pmc)


def call(client, path, **body):
    return client.post(f"/api/payables/{path}", json=json.loads(json.dumps(body, default=str)))


def ok(answer, status=201):
    assert answer.status_code == status, answer.text
    return None if status == 204 else answer.json()


def refused(answer, status, detail=None):
    assert answer.status_code == status, answer.text
    if detail is not None:
        assert answer.json()["detail"] == detail


def terms(conn, pmc, n=0, markup="10", limit="10000"):
    add_agreement(
        conn,
        pmc.pmc_id,
        pmc.owners[n].account,
        TODAY - 60 * DAY,
        approval_limit=limit,
        bill_markup_percent=markup,
    )


def fund(conn, pmc, amount, n=0):
    post(
        conn,
        pmc.pmc_id,
        f"fund-{uuid.uuid4()}",
        [(pmc.operating_cash, pmc.owners[n].account, Decimal(amount))],
        NOW - 30 * DAY,
    )


def shared(client, pmc, parts=(("400.00", 0), ("500.00", 1)), reference="ROOF-1", **more):
    body = {
        "vendor_id": pmc.vendor_id,
        "reference": reference,
        "bill_date": TODAY - 2 * DAY,
        "due_on": TODAY + 10 * DAY,
        "memo": "Roof repair, both buildings",
        "shares": [{"account": pmc.owners[n].account, "amount": amount} for amount, n in parts],
    } | more
    return call(client, "shared-bills", **body)


def bill(client, pmc, amount="250.00", reference="FIX-1", n=0, **more):
    body = {
        "vendor_id": pmc.vendor_id,
        "account": pmc.owners[n].account,
        "reference": reference,
        "bill_date": TODAY - 2 * DAY,
        "due_on": TODAY + 10 * DAY,
        "amount": amount,
        "memo": "Water heater",
    } | more
    return call(client, "bills", **body)


def page(client, bill_id):
    return ok(client.get(f"/api/payables/bills/{bill_id}"), 200)


def test_staff_share_one_invoice_across_two_properties(client, conn, staff, pmc):
    for n in (0, 1):
        terms(conn, pmc, n)
        fund(conn, pmc, "1000.00", n)
    repairs = ok(
        client.post("/api/setup/expense-accounts", json={"name": "Repairs", "schedule_e_line": 14})
    )["id"]
    invoice = {
        "title": "Roof invoice",
        "content_type": "application/pdf",
        "content_base64": base64.b64encode(PDF).decode(),
    }

    made = ok(shared(client, pmc, expense_account_id=repairs, markup=True, invoice=invoice))

    first, second = made["bills"]
    one, two = page(client, first), page(client, second)
    assert (one["bill"]["reference"], one["bill"]["amount"], one["bill"]["markup"]) == (
        "ROOF-1/1",
        "400.00",
        "40.00",
    )
    assert (two["bill"]["reference"], two["bill"]["amount"], two["bill"]["markup"]) == (
        "ROOF-1/2",
        "500.00",
        "50.00",
    )
    assert one["shared"] == two["shared"]
    assert (one["shared"]["id"], one["shared"]["reference"], one["shared"]["amount"]) == (
        made["id"],
        "ROOF-1",
        "900.00",
    )
    assert [
        (p["part"], p["property"], p["amount"], p["status"]) for p in one["shared"]["parts"]
    ] == [
        (1, "Property 1", "400.00", "ready to pay"),
        (2, "Property 2", "500.00", "ready to pay"),
    ]
    assert [line["expense_account"] for line in one["lines"] + two["lines"]] == ["Repairs"] * 2
    # The invoice is kept with the first share.
    assert ([f["title"] for f in one["files"]], two["files"]) == (["Roof invoice"], [])

    # Pay bills pays both shares at once, each from its own owner's money with its markup.
    ok(call(client, "bills/pay", on=TODAY, method="check", bill_ids=[first, second]))
    assert (balance(conn, pmc.owners[0].account), balance(conn, pmc.owners[1].account)) == (
        Decimal("560.00"),
        Decimal("450.00"),
    )
    assert balance(conn, pmc.pmc_income) == Decimal("90.00")
    assert set(bank_sums(conn, pmc.pmc_id).values()) == {Decimal(0)}
    assert [p["status"] for p in page(client, first)["shared"]["parts"]] == ["paid", "paid"]


def test_each_share_waits_for_its_own_owners_approval_and_email(client, conn, staff, pmc):
    client.app.state.email = email.FakeSender()
    owner = pmc.owners[1].owner_id
    address = f"owner.{owner.hex[:6]}@example.com"
    made = accounts.create_invite(conn, pmc.pmc_id, "owner", owner, address, None)
    accounts.accept_invite(conn, made.token, "a long synthetic passphrase")
    terms(conn, pmc, 0, markup="0", limit="1000")
    terms(conn, pmc, 1, markup="0", limit="300")

    first, second = ok(shared(client, pmc))["bills"]

    assert (page(client, first)["bill"]["status"], page(client, second)["bill"]["status"]) == (
        "short",  # its owner's account holds nothing yet
        "waiting for owner",
    )
    assert page(client, first)["email"] is None
    assert page(client, second)["email"]["to"] == address


def test_a_bill_takes_the_markup_its_owners_agreement_sets(client, conn, staff, pmc):
    terms(conn, pmc)
    fund(conn, pmc, "1000.00")

    made = ok(bill(client, pmc, markup=True))["id"]

    marked = page(client, made)["bill"]
    assert (marked["markup"], marked["markup_percent"]) == ("25.00", "10")
    # Draws hold the bill back with its markup until it is paid.
    [money] = [
        m
        for m in ok(client.get("/api/payables/owners"), 200)
        if m["account"] == pmc.owners[0].account
    ]
    assert (money["unpaid_bills"], money["available"]) == ("275.00", "725.00")
    ok(call(client, f"bills/{made}/pay", on=TODAY))
    assert balance(conn, pmc.owners[0].account) == Decimal("725.00")
    assert [e["what"] for e in page(client, made)["history"]][-2:] == [
        "Markup of $25.00 (10%) taken into the company's fees",
        "Paid $250.00 by check",
    ]
    # The owner's statement shows it on a line of its own.
    statement = ok(
        client.get(
            f"/api/owners/{pmc.owners[0].owner_id}/statement",
            params={"start": str(TODAY), "end": str(TODAY + DAY)},
        ),
        200,
    )
    assert any("Markup of 10% on bill FIX-1" in str(row) for row in statement["rows"])
    # A bill without it takes none.
    plain = ok(bill(client, pmc, reference="FIX-2"))["id"]
    assert page(client, plain)["bill"]["markup"] is None


def test_staff_set_the_markup_in_the_owners_terms(client, conn, staff, pmc):
    account = pmc.owners[0].account
    ok(
        client.post(
            "/api/setup/agreements",
            json={"account": account, "starts_on": str(TODAY - DAY), "bill_markup_percent": "12.5"},
        )
    )
    owner = ok(client.get(f"/api/owners/{pmc.owners[0].owner_id}"), 200)
    assert owner["agreements"][0]["bill_markup_percent"] == "12.5"
    for bad in ("101", "-1", "ten"):
        refused(
            client.post(
                "/api/setup/agreements",
                json={"account": account, "starts_on": str(TODAY), "bill_markup_percent": bad},
            ),
            422,
        )


def test_the_owner_sees_the_markup_on_a_bill_waiting_for_them(
    client, other_client, conn, app_conn, staff, pmc
):
    terms(conn, pmc, limit="100")
    made = ok(bill(client, pmc, markup=True))["id"]
    join_as_owner(other_client, app_conn, pmc)
    [waiting] = ok(other_client.get("/api/owner/bills"), 200)
    assert (waiting["id"], waiting["markup"], waiting["markup_percent"]) == (made, "25.00", "10")


def test_the_approval_email_names_the_markup(client, conn, staff, pmc):
    owner = pmc.owners[0].owner_id
    address = f"owner.{owner.hex[:6]}@example.com"
    invite = accounts.create_invite(conn, pmc.pmc_id, "owner", owner, address, None)
    accounts.accept_invite(conn, invite.token, "a long synthetic passphrase")
    terms(conn, pmc, limit="100")
    ok(bill(client, pmc, markup=True))
    (body,) = conn.execute(
        "SELECT body FROM trust_emails WHERE pmc_id = %s AND kind = 'bill_approval'",
        (pmc.pmc_id,),
    ).fetchone()
    assert "Our markup of 10% on it, $25.00, as your management agreement sets" in body


@pytest.mark.parametrize(
    ("change", "status", "detail"),
    [
        ({"reference": "USED-1"}, 409, "this vendor has a bill with that reference already"),
        ({"due_on": str(TODAY - 3 * DAY)}, 422, "a bill is due on or after its date"),
        ({"shares": "one"}, 422, None),
        ({"shares": "same account"}, 422, "each share is a different owner's property account"),
        ({"shares": "zero"}, 422, None),
        ({"vendor_id": str(uuid.uuid4())}, 404, "no such vendor"),
        ({"expense_account_id": str(uuid.uuid4())}, 404, "no such expense account"),
        ({"reference": " "}, 422, None),
    ],
)
def test_bad_shared_bills_are_refused_and_enter_nothing(
    client, conn, staff, pmc, change, status, detail
):
    terms(conn, pmc, 0)
    terms(conn, pmc, 1, markup="0")
    ok(bill(client, pmc, reference="USED-1"))
    before = conn.execute(
        "SELECT count(*) FROM trust_bills WHERE pmc_id = %s", (pmc.pmc_id,)
    ).fetchone()
    shares = {
        "one": [{"account": pmc.owners[0].account, "amount": "900.00"}],
        "same account": [{"account": pmc.owners[0].account, "amount": "400.00"}] * 2,
        "zero": [
            {"account": pmc.owners[0].account, "amount": "0"},
            {"account": pmc.owners[1].account, "amount": "900.00"},
        ],
    }
    if "shares" in change:
        change = {"shares": shares[change["shares"]]}
    refused(shared(client, pmc, **change), status, detail)
    assert (
        conn.execute("SELECT count(*) FROM trust_bills WHERE pmc_id = %s", (pmc.pmc_id,)).fetchone()
        == before
    )
    assert conn.execute(
        "SELECT count(*) FROM trust_shared_bills WHERE pmc_id = %s", (pmc.pmc_id,)
    ).fetchone() == (0,)


def test_each_share_takes_the_markup_its_owners_agreement_sets_or_none(client, conn, staff, pmc):
    terms(conn, pmc, 0, markup="10")
    terms(conn, pmc, 1, markup="0")
    first, second = ok(shared(client, pmc, markup=True))["bills"]
    assert (page(client, first)["bill"]["markup"], page(client, second)["bill"]["markup"]) == (
        "40.00",
        None,
    )


def test_a_markup_no_share_can_take_is_refused_and_enters_nothing(client, conn, staff, pmc):
    terms(conn, pmc, 0, markup="0")
    refused(
        shared(client, pmc, markup=True),
        409,
        "no owner's agreement here sets a markup on bills dated then",
    )
    assert conn.execute(
        "SELECT count(*) FROM trust_bills WHERE pmc_id = %s", (pmc.pmc_id,)
    ).fetchone() == (0,)


def test_another_companys_account_is_no_share(client, conn, staff, pmc):
    theirs = make_pmc(conn)
    body = {
        "vendor_id": pmc.vendor_id,
        "reference": "ROOF-2",
        "bill_date": TODAY,
        "due_on": TODAY,
        "memo": "Roof",
        "shares": [
            {"account": pmc.owners[0].account, "amount": "1.00"},
            {"account": theirs.owners[0].account, "amount": "1.00"},
        ],
    }
    refused(call(client, "shared-bills", **body), 404, "no such owner account")


def test_a_markup_where_the_agreement_sets_none_is_refused_in_words(client, conn, staff, pmc):
    terms(conn, pmc, markup="0")
    refused(
        bill(client, pmc, markup=True),
        409,
        "the owner's agreement sets no markup on bills dated then",
    )
    assert conn.execute(
        "SELECT count(*) FROM trust_bills WHERE pmc_id = %s", (pmc.pmc_id,)
    ).fetchone() == (0,)


@pytest.mark.parametrize("caller", ["nobody", "owner", "tenant"])
def test_only_staff_share_a_bill(client, conn, app_conn, pmc, caller):
    if caller == "owner":
        join_as_owner(client, app_conn, pmc)
    elif caller == "tenant":
        join_as_tenant(client, conn, app_conn, pmc)
    refused(shared(client, pmc), 401 if caller == "nobody" else 403)


def test_a_custom_role_without_bills_shares_none(client, other_client, conn, app_conn, staff, pmc):
    clerk = join_as_staff(other_client, conn, app_conn, pmc, name="Clerk 2", roles=[])
    role = ok(client.post("/api/custom-roles", json={"name": "Rent", "areas": ["rent"]}))
    ok(
        client.post(
            f"/api/custom-roles/{role['id']}/staff",
            json={"staff_id": str(clerk.person_id), "granted": True},
        ),
        204,
    )
    refused(shared(other_client, pmc), 403)
