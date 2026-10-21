"""Payment files (migration 0118, src/pms/api/payment_files.py; the founder's review q035): a
bank's or lockbox's CSV file of the tenants' payments it received, brought in once, each line
recorded as Receive payment records one, to the lease it names by its unit or its payer. A line
that names no single lease, looks recorded already, or that the books refuse waits for staff, who
record it to a lease they pick or set it aside with why."""

import json
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from helpers import (
    add_tenant,
    add_unit,
    balance,
    bank_sums,
    charge,
    hold_payments,
    make_pmc,
    open_lease,
)
from people import join_as_owner, join_as_staff, join_as_tenant

from pms import email
from pms.api import rent

TODAY = datetime.now(UTC).date()
DAY = timedelta(days=1)
START = TODAY - 90 * DAY
HEADER = "Date,Amount,Property,Unit,Payer,Reference,Method"
US = f"{TODAY:%m/%d/%Y}"


@pytest.fixture
def staff(client, conn, app_conn, pmc):
    return join_as_staff(client, conn, app_conn, pmc)


def ok(answer, status=200):
    assert answer.status_code == status, answer.text
    return answer.json() if answer.content else None


def refused(answer, status, says=None):
    assert answer.status_code == status, answer.text
    if says is not None:
        assert answer.json()["detail"] == says


def post(client, path, **body):
    return client.post(path, json=json.loads(json.dumps(body, default=str)))


def a_file(*rows, header=HEADER):
    return "\n".join([header, *rows]) + "\n"


def bring(client, content, name="lockbox.csv"):
    return post(client, "/api/rent/payment-files", file_name=name, content=content)


class Home:
    """A tenant of their own on a lease of a unit at one of the PMC's properties since START,
    owing this month's $1200.00 rent."""

    def __init__(self, conn, pmc, owner, unit, tenant):
        prop = pmc.owners[owner].property_id
        self.pmc_id = pmc.pmc_id
        self.account = pmc.owners[owner].account
        self.tenant = add_tenant(conn, pmc.pmc_id, prop, tenant)
        unit_id = add_unit(conn, pmc.pmc_id, prop, unit)
        self.id = open_lease(conn, pmc.pmc_id, unit_id, START, None, "1200.00", [self.tenant])
        charge(conn, pmc.pmc_id, self.id, TODAY.replace(day=1), Decimal("1200.00"))

    def owed(self, conn):
        return rent.owed(conn, self.id)


def rows_of(conn, pmc):
    return conn.execute(
        "SELECT (SELECT count(*) FROM trust_payment_files WHERE pmc_id = %(p)s),"
        " (SELECT count(*) FROM trust_payment_file_lines WHERE pmc_id = %(p)s),"
        " (SELECT count(*) FROM trust_payment_file_postings WHERE pmc_id = %(p)s),"
        " (SELECT count(*) FROM trust_payment_file_skips WHERE pmc_id = %(p)s)",
        {"p": pmc.pmc_id},
    ).fetchone()


def payments_to(conn, lease):
    return conn.execute(
        "SELECT metadata ->> 'amount', metadata ->> 'reference', metadata ->> 'method'"
        " FROM pgledger_transfers WHERE metadata ->> 'kind' = 'tenant payment'"
        " AND metadata ->> 'lease_id' = %s ORDER BY created_at, id",
        (str(lease),),
    ).fetchall()


def line(answer, line_no):
    return next(item for item in answer["items"] if item["line_no"] == line_no)


@pytest.fixture
def homes(conn, pmc):
    return (
        Home(conn, pmc, 0, "1A", "Reese Sample"),
        Home(conn, pmc, 1, "2B", "Rowan Sample"),
        Home(conn, pmc, 0, "Unit 7", "Quinn Sample"),
        Home(conn, pmc, 1, "Unit 7", "Avery Sample"),
    )


def test_a_file_records_each_line_to_the_lease_it_names_and_the_rest_wait(
    client, conn, staff, pmc, homes
):
    first, second, seven_one, _ = homes
    content = a_file(
        f"{TODAY},1200.00,Property 1,1a,,1043,check",  # by unit, any case
        f'{US},"$1,000.00",,,rowan sample,,ACH',  # by payer, a US date
        f"{TODAY},50.00,,Unit 7,,,",  # two units named Unit 7
        f"{TODAY},75.00,Property 1,Unit 7,,,money order",  # named by its property too
        f"{TODAY},20.00,Property 1,9Z,,,",  # no such unit
        f"{TODAY},30.00,,,Nobody Sample,,",  # no such tenant
    )

    seen = ok(post(client, "/api/rent/payment-files/preview", file_name="x.csv", content=content))

    assert rows_of(conn, pmc) == (0, 0, 0, 0)  # a preview writes nothing
    assert [(s["line_no"], s["status"], s["lease_id"]) for s in seen["lines"]] == [
        (2, "to record", str(first.id)),
        (3, "to record", str(second.id)),
        (4, "waiting", None),
        (5, "to record", str(seven_one.id)),
        (6, "waiting", None),
        (7, "waiting", None),
    ]
    assert (seen["to_record"], seen["to_record_total"], seen["waiting"], seen["total"]) == (
        3,
        "2275.00",
        3,
        "2375.00",
    )
    assert seen["came_in"] is None

    came = ok(bring(client, content), 201)

    assert (came["lines"], came["recorded"], came["recorded_total"], came["waiting"]) == (
        6,
        3,
        "2275.00",
        3,
    )
    assert (came["file_name"], came["brought_by"], came["set_aside"]) == (
        "lockbox.csv",
        "Staff 1",
        0,
    )
    assert line(came, 2)["status"] == "recorded"
    assert line(came, 2)["lease"] == "Reese Sample, Property 1 1A"
    assert line(came, 2)["by"] == "Staff 1"
    assert line(came, 4)["note"] == (
        f"2 leases in effect on {US} at unit Unit 7; name the property too"
    )
    assert line(came, 6)["note"] == f"no lease in effect on {US} at Property 1 9Z"
    assert line(came, 7)["note"] == f"no lease in effect on {US} at a tenant named Nobody Sample"
    assert first.owed(conn) == 0
    assert second.owed(conn) == Decimal("200.00")
    assert seven_one.owed(conn) == Decimal("1125.00")
    assert payments_to(conn, first.id) == [("1200.00", "1043", "check")]
    assert payments_to(conn, second.id) == [("1000.00", None, "EFT")]
    assert balance(conn, first.account) == Decimal("1275.00")
    assert set(bank_sums(conn, pmc.pmc_id).values()) == {Decimal(0)}
    assert rows_of(conn, pmc) == (1, 6, 3, 0)

    assert ok(client.get(f"/api/rent/payment-files/{came['id']}")) == came
    listed = ok(client.get("/api/rent/payment-files"))
    assert listed["total"] == 1
    assert listed["items"][0] == {k: v for k, v in came.items() if k != "items"}


def test_a_file_comes_in_once(client, conn, staff, pmc, homes):
    content = a_file(f"{TODAY},100.00,Property 1,1A,,,")
    ok(bring(client, content), 201)

    refused(
        bring(client, content, "again.csv"), 409, f"this file came in on {US}; it comes in once"
    )
    seen = ok(post(client, "/api/rent/payment-files/preview", file_name="x.csv", content=content))
    assert seen["came_in"] is not None
    assert rows_of(conn, pmc) == (1, 1, 1, 0)
    assert payments_to(conn, homes[0].id) == [("100.00", None, None)]


def test_a_payment_that_looks_recorded_already_waits(client, conn, staff, pmc, homes):
    first = homes[0]
    with conn.transaction():
        rent.record_payment(conn, pmc.pmc_id, first.id, uuid.uuid4(), Decimal("300.00"), TODAY)
    came = ok(
        bring(
            client,
            a_file(
                f"{TODAY},300.00,Property 1,1A,,,",  # staff typed this one in already
                f"{TODAY},400.00,Property 1,1A,,,",
                f"{TODAY},400.00,Property 1,1A,,,",  # the same line twice
            ),
        ),
        201,
    )

    said = f"a payment of $%s on {US} is recorded to this lease already"
    assert [(s["status"], s["lease_id"], s["note"]) for s in came["items"]] == [
        ("waiting", str(first.id), said % "300.00"),
        ("recorded", str(first.id), None),
        ("waiting", str(first.id), said % "400.00"),
    ]
    assert first.owed(conn) == Decimal("500.00")


def test_a_lease_on_a_payment_hold_waits_until_staff_accept_it_past_the_hold(
    client, conn, staff, pmc, homes
):
    first = homes[0]
    hold_payments(conn, pmc.pmc_id, first.id, TODAY - 5 * DAY)
    came = ok(bring(client, a_file(f"{TODAY},1200.00,Property 1,1A,,,")), 201)
    waiting = came["items"][0]
    assert (waiting["status"], waiting["lease_id"]) == ("waiting", str(first.id))
    hold = "the lease is on a payment hold: accept the payment past the hold, or set it aside"
    assert waiting["note"] == hold
    assert first.owed(conn) == Decimal("1200.00")

    to = f"/api/rent/payment-files/lines/{waiting['id']}/record"
    refused(post(client, to, lease_id=first.id), 409, hold)
    done = ok(post(client, to, lease_id=first.id, past_hold=True), 201)

    assert (done["status"], done["by"], done["note"]) == ("recorded", "Staff 1", None)
    assert first.owed(conn) == 0
    assert conn.execute(
        "SELECT allowed_by FROM trust_payment_hold_allowances WHERE pmc_id = %s", (pmc.pmc_id,)
    ).fetchall() == [("Staff 1",)]


def test_staff_record_a_waiting_line_to_the_lease_they_pick_once(client, conn, staff, pmc, homes):
    _, second, *_ = homes
    came = ok(bring(client, a_file(f"{TODAY},250.00,,,R. Sample,77,cash")), 201)
    waiting = came["items"][0]
    to = f"/api/rent/payment-files/lines/{waiting['id']}/record"

    done = ok(post(client, to, lease_id=second.id), 201)
    again = ok(post(client, to, lease_id=second.id), 201)  # a retry records nothing new

    assert done == again
    assert (done["status"], done["lease"], done["by"]) == (
        "recorded",
        "Rowan Sample, Property 2 2B",
        "Staff 1",
    )
    assert payments_to(conn, second.id) == [("250.00", "77", "cash")]
    assert second.owed(conn) == Decimal("950.00")
    refused(
        post(client, to, lease_id=homes[0].id),
        409,
        "this line is recorded already, to Rowan Sample, Property 2 2B",
    )
    aside = f"/api/rent/payment-files/lines/{waiting['id']}/set-aside"
    refused(post(client, aside, reason="Not ours"), 409)
    detail = ok(client.get(f"/api/rent/payment-files/{came['id']}"))
    assert (detail["recorded"], detail["waiting"], detail["recorded_total"]) == (1, 0, "250.00")
    assert set(bank_sums(conn, pmc.pmc_id).values()) == {Decimal(0)}


def test_staff_set_a_line_aside_with_why(client, conn, staff, pmc, homes):
    came = ok(bring(client, a_file(f"{TODAY},20.00,,,Nobody Sample,,")), 201)
    waiting = came["items"][0]
    aside = f"/api/rent/payment-files/lines/{waiting['id']}/set-aside"

    done = ok(post(client, aside, reason="A refund from the utility, not rent"), 201)

    assert (done["status"], done["note"], done["by"]) == (
        "set aside",
        "A refund from the utility, not rent",
        "Staff 1",
    )
    refused(post(client, aside, reason="Again"), 409, "this line is set aside")
    to = f"/api/rent/payment-files/lines/{waiting['id']}/record"
    refused(post(client, to, lease_id=homes[0].id), 409, "this line is set aside")
    detail = ok(client.get(f"/api/rent/payment-files/{came['id']}"))
    assert (detail["recorded"], detail["waiting"], detail["set_aside"]) == (0, 0, 1)
    assert rows_of(conn, pmc) == (1, 1, 0, 1)


def test_a_line_whose_owner_account_staff_must_choose_waits_and_takes_the_account(
    client, conn, staff, pmc, homes
):
    first = homes[0]
    second_account = rent_account_at(conn, pmc, 0)
    came = ok(bring(client, a_file(f"{TODAY},100.00,Property 1,1A,,,")), 201)
    waiting = came["items"][0]
    assert waiting["status"] == "waiting"
    assert "account" in waiting["note"]

    to = f"/api/rent/payment-files/lines/{waiting['id']}/record"
    done = ok(post(client, to, lease_id=first.id, account=second_account), 201)

    assert done["status"] == "recorded"
    assert balance(conn, second_account) == Decimal("100.00")


def rent_account_at(conn, pmc, owner):
    """A second owner's account at the owner's property, so staff choose which one is paid."""
    from helpers import open_account

    other = conn.execute(
        "INSERT INTO trust_owners (pmc_id, display_name) VALUES (%s, 'Owner 9') RETURNING id",
        (pmc.pmc_id,),
    ).fetchone()[0]
    bank = conn.execute(
        "SELECT bank_account_id FROM trust_ledger_accounts WHERE ledger_account_id = %s",
        (pmc.owners[owner].account,),
    ).fetchone()[0]
    return open_account(
        conn, pmc.pmc_id, bank, "owner_property", other, pmc.owners[owner].property_id
    )


def test_receipts_go_to_the_tenants_while_the_company_asks_for_them(
    client, conn, staff, pmc, homes
):
    first = homes[0]
    ok(client.post(f"/api/tenants/{first.tenant}/contact", json={"email": "r@example.com"}), 204)
    client.app.state.email = email.FakeSender()
    ok(client.post("/api/emails/settings", json={**EMAILS, "payment_receipts": True}))

    ok(bring(client, a_file(f"{TODAY},1200.00,Property 1,1A,,,")), 201)

    assert conn.execute(
        "SELECT kind, lease_id FROM trust_emails WHERE pmc_id = %s", (pmc.pmc_id,)
    ).fetchall() == [("payment_receipt", first.id)]


EMAILS = {
    "rent_reminder_days": None,
    "late_notices": False,
    "owner_statement_day": None,
    "work_order_notices": False,
    "adverse_action_notices": False,
    "payment_receipts": False,
}


def test_another_pmcs_file_line_or_lease_reads_as_not_found(client, conn, staff, pmc, homes):
    other = make_pmc(conn)
    theirs = Home(conn, other, 0, "1A", "Sky Sample")
    came = ok(bring(client, a_file(f"{TODAY},20.00,,,Nobody Sample,,")), 201)
    waiting = came["items"][0]["id"]

    refused(
        post(client, f"/api/rent/payment-files/lines/{waiting}/record", lease_id=theirs.id),
        404,
        "no such lease",
    )
    refused(client.get(f"/api/rent/payment-files/{uuid.uuid4()}"), 404, "no such payment file")
    for path, body in (("record", {"lease_id": homes[0].id}), ("set-aside", {"reason": "x"})):
        refused(
            post(client, f"/api/rent/payment-files/lines/{uuid.uuid4()}/{path}", **body),
            404,
            "no such payment file line",
        )
    assert theirs.owed(conn) == Decimal("1200.00")


@pytest.mark.parametrize(
    ("content", "says"),
    [
        (a_file(f"{TODAY},100.00", header="Date,Amount"), "line 1: no unit or payer column:"),
        (a_file(f"{TODAY},1A", header="Date,Unit"), "line 1: no amount column"),
        (a_file(f"{TODAY},-100.00,,1A,,,"), "line 2: a payment file lists money received"),
        (a_file(f"{TODAY},0.00,,1A,,,"), "line 2: a payment file lists money received"),
        (a_file(f"{TODAY},12.345,,1A,,,"), "line 2: '12.345' is not an amount in cents"),
        (a_file(f"{TODAY + DAY},100.00,,1A,,,"), "line 2: "),
        (a_file("10/32/2026,100.00,,1A,,,"), "line 2: '10/32/2026' is not a date"),
        (a_file(f"{TODAY},100.00,,1A,,,barter"), "line 2: 'barter' is not a way money comes"),
        (a_file(",100.00,,1A,,,"), "line 2: no date"),
        ("\n\n", "the file is empty"),
        ("\r0", "line 1: no date, amount column"),  # a bare carriage return ends a line
        ("Date,Amount,Unit\n" + "x" * 140_000, "line 2: this isn't CSV the PMS can read"),
    ],
)
def test_a_file_that_is_not_payments_received_is_refused_whole(
    client, conn, staff, pmc, homes, content, says
):
    for path in ("/api/rent/payment-files/preview", "/api/rent/payment-files"):
        answer = post(client, path, file_name="bad.csv", content=content)
        assert answer.status_code == 422, answer.text
        assert answer.json()["detail"].startswith(says)
    assert rows_of(conn, pmc) == (0, 0, 0, 0)


@pytest.mark.parametrize(
    "body",
    [
        {"file_name": "x.csv"},
        {"content": "Date,Amount,Unit\n"},
        {"file_name": " ", "content": "Date,Amount,Unit\n"},
        {"file_name": "x" * 201, "content": "Date,Amount,Unit\n"},
    ],
)
def test_a_request_without_a_file_or_its_name_is_refused(client, conn, staff, pmc, body):
    refused(client.post("/api/rent/payment-files", json=body), 422)
    assert rows_of(conn, pmc) == (0, 0, 0, 0)


def test_a_waiting_line_takes_a_lease_and_a_reason(client, conn, staff, pmc, homes):
    came = ok(bring(client, a_file(f"{TODAY},20.00,,,Nobody Sample,,")), 201)
    waiting = came["items"][0]["id"]
    for path, body in (
        ("record", {}),
        ("record", {"lease_id": "not a lease"}),
        ("set-aside", {}),
        ("set-aside", {"reason": " "}),
        ("set-aside", {"reason": "x" * 501}),
    ):
        refused(client.post(f"/api/rent/payment-files/lines/{waiting}/{path}", json=body), 422)
    assert rows_of(conn, pmc) == (1, 1, 0, 0)


@pytest.mark.parametrize("caller", ["nobody", "owner", "tenant"])
def test_only_staff_bring_files_in_and_deal_with_their_lines(
    client, conn, app_conn, pmc, homes, caller
):
    if caller == "owner":
        join_as_owner(client, app_conn, pmc)
    elif caller == "tenant":
        join_as_tenant(client, conn, app_conn, pmc)
    status = 401 if caller == "nobody" else 403
    content = a_file(f"{TODAY},100.00,Property 1,1A,,,")
    line_id = uuid.uuid4()
    for answer in (
        post(client, "/api/rent/payment-files/preview", file_name="x.csv", content=content),
        bring(client, content),
        client.get("/api/rent/payment-files"),
        client.get(f"/api/rent/payment-files/{uuid.uuid4()}"),
        post(client, f"/api/rent/payment-files/lines/{line_id}/record", lease_id=homes[0].id),
        post(client, f"/api/rent/payment-files/lines/{line_id}/set-aside", reason="x"),
    ):
        refused(answer, status)
    assert rows_of(conn, pmc) == (0, 0, 0, 0)


def test_files_list_newest_first_a_page_at_a_time(client, conn, staff, pmc, homes):
    for n in range(3):
        ok(bring(client, a_file(f"{TODAY},{n + 1}.00,Property 1,1A,,,"), f"day{n}.csv"), 201)

    page = ok(client.get("/api/rent/payment-files", params={"per_page": 2}))
    rest = ok(client.get("/api/rent/payment-files", params={"per_page": 2, "page": 2}))

    assert page["total"] == 3
    assert [f["file_name"] for f in page["items"] + rest["items"]] == [
        "day2.csv",
        "day1.csv",
        "day0.csv",
    ]
    refused(client.get("/api/rent/payment-files", params={"per_page": 0}), 422)
