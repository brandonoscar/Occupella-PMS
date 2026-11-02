"""A lease's page through the API (src/pms/api/rentals.py, ADR 0006): its tenants with their contact
details and portal logins, whether it is in effect, and its ledger with a running balance, the
lease ledger report's own lines, each charge saying what it is and each payment its reference
and whether it bounced.
Always the session's PMC."""

import json
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from helpers import add_nsf_fee_policy, add_tenant, add_unit, charge, make_pmc, open_lease
from people import PASSWORD, invite_token, join_as_owner, join_as_staff, join_as_tenant

TODAY = datetime.now(UTC).date()
DAY = timedelta(days=1)
START = TODAY - 60 * DAY


@pytest.fixture
def staff(client, conn, app_conn, pmc):
    return join_as_staff(client, conn, app_conn, pmc)


def ok(answer, status=200):
    assert answer.status_code == status, answer.text
    return answer.json() if answer.content else None


def post(client, path, **body):
    return client.post(path, json=json.loads(json.dumps(body, default=str)))


def lease_of(conn, pmc, tenants=("Tenant A",), starts=START, ends=None):
    prop = pmc.owners[0].property_id
    unit = add_unit(conn, pmc.pmc_id, prop, "Unit 1")
    people = [add_tenant(conn, pmc.pmc_id, prop, name) for name in tenants]
    return open_lease(conn, pmc.pmc_id, unit, starts, ends, "1500.00", people), people


def test_a_lease_shows_its_tenants_contact_and_who_can_sign_in(
    client, other_client, conn, app_conn, staff, pmc
):
    lease, (active, invited, none) = lease_of(conn, pmc, ("Tenant A", "Tenant B", "Tenant C"))
    ok(
        post(client, f"/api/tenants/{active}/contact", email="a@example.com", phone="555-555-0101"),
        204,
    )
    token = invite_token(app_conn, pmc.pmc_id, "tenant", active, "a.login@example.com")
    ok(other_client.post("/api/invites/accept", json={"token": token, "password": PASSWORD}))
    invite_token(app_conn, pmc.pmc_id, "tenant", invited, "b.login@example.com")

    found = ok(client.get(f"/api/leases/{lease}"))

    assert found["status"] == "current"
    assert [
        (p["name"], p["email"], p["phone"], p["portal"], p["login_email"]) for p in found["people"]
    ] == [
        ("Tenant A", "a@example.com", "555-555-0101", "active", "a.login@example.com"),
        ("Tenant B", None, None, "invited", "b.login@example.com"),
        ("Tenant C", None, None, "no login", None),
    ]
    assert [p["id"] for p in found["people"]] == [str(active), str(invited), str(none)]


def test_a_lease_to_come_or_ended_says_so(client, conn, staff, pmc):
    coming, _ = lease_of(conn, pmc, starts=TODAY + 5 * DAY)
    assert ok(client.get(f"/api/leases/{coming}"))["status"] == "future"
    prop = pmc.owners[0].property_id
    unit = add_unit(conn, pmc.pmc_id, prop, "Unit 2")
    tenant = add_tenant(conn, pmc.pmc_id, prop, "Tenant Z")
    ended = open_lease(conn, pmc.pmc_id, unit, START, TODAY - DAY, "900.00", [tenant])
    assert ok(client.get(f"/api/leases/{ended}"))["status"] == "ended"


def test_the_ledger_runs_its_balance_and_marks_the_payment_that_bounced(client, conn, staff, pmc):
    add_nsf_fee_policy(conn, pmc.pmc_id, pmc.owners[0].property_id, START, "35.00")
    lease, _ = lease_of(conn, pmc)
    charge(conn, pmc.pmc_id, lease, START, "1500.00", memo="Rent")
    charge(conn, pmc.pmc_id, lease, START + 5 * DAY, "50.00", kind="fee", memo="Lost key")
    charge(conn, pmc.pmc_id, lease, START + 6 * DAY, "20.00", kind="credit", memo="Repair credit")
    ok(
        post(
            client,
            f"/api/rent/leases/{lease}/payments",
            request_id=uuid.uuid4(),
            amount="1000.00",
            received_on=TODAY - 10 * DAY,
            reference="Check 101",
        ),
        201,
    )
    second = ok(
        post(
            client,
            f"/api/rent/leases/{lease}/payments",
            request_id=uuid.uuid4(),
            amount="200.00",
            received_on=TODAY - 5 * DAY,
        ),
        201,
    )["transfers"][0]
    ok(post(client, f"/api/rent/payments/{second}/bounce", on=TODAY - 2 * DAY), 201)

    ledger = ok(client.get(f"/api/leases/{lease}/ledger"))

    lines = [
        (line["kind"], line["charged"], line["paid"], line["balance"], line["bounced"])
        for line in ledger["lines"]
    ]
    assert lines == [
        ("rent", "1500.00", None, "1500.00", False),
        ("fee", "50.00", None, "1550.00", False),
        ("credit", "-20.00", None, "1530.00", False),
        ("payment", None, "1000.00", "530.00", False),
        ("payment", None, "200.00", "330.00", True),
        # On the day it came back, the NSF fee is charged before the bounce is listed.
        ("NSF fee", "35.00", None, "365.00", False),
        ("bounced payment", None, "-200.00", "565.00", False),
    ]
    assert [line["memo"] for line in ledger["lines"]][:3] == ["Rent", "Lost key", "Repair credit"]
    # A payment's memo is its check or reference.
    assert [line["memo"] for line in ledger["lines"] if line["kind"] == "payment"] == [
        "Check 101",
        None,
    ]
    assert ledger["balance"] == "565.00"
    assert ok(client.get(f"/api/leases/{lease}"))["owed"] == "565.00"
    # The report's own lines, day for day; the report calls every kind of fee a fee.
    report = conn.execute(
        "SELECT on_date, item, charged, paid, balance FROM trust_report_tenant_ledger(%s, %s, %s)"
        " ORDER BY line",
        (pmc.pmc_id, lease, TODAY),
    ).fetchall()[1:-1]
    fee = {"late fee", "NSF fee", "recurring", "fee"}
    assert [
        (line["on"], "fee" if line["kind"] in fee else line["kind"], line["balance"])
        for line in ledger["lines"]
    ] == [(str(on), item, f"{balance:.2f}") for on, item, _, _, balance in report]


def test_a_lease_with_nothing_on_it_has_an_empty_ledger(client, conn, staff, pmc):
    lease, _ = lease_of(conn, pmc)
    assert ok(client.get(f"/api/leases/{lease}/ledger")) == {
        "start": None,
        "end": str(TODAY),
        "opening": "0.00",
        "lines": [],
        "balance": "0.00",
    }


def ledger_with_a_bounce(client, conn, pmc):
    """Rent, a fee and a credit; a payment by check, and another that bounced. Their ids."""
    lease, _ = lease_of(conn, pmc)
    rent = charge(conn, pmc.pmc_id, lease, START, "1500.00", memo="Rent")
    fee = charge(conn, pmc.pmc_id, lease, START + 5 * DAY, "50.00", kind="fee", memo="=Lost key")
    credit = charge(conn, pmc.pmc_id, lease, START + 6 * DAY, "20.00", kind="credit", memo="Credit")
    paid = [
        ok(
            post(
                client,
                f"/api/rent/leases/{lease}/payments",
                request_id=uuid.uuid4(),
                amount=amount,
                received_on=on,
                reference=reference,
            ),
            201,
        )["transfers"][0]
        for amount, on, reference in (
            ("1000.00", TODAY - 10 * DAY, "Check 101"),
            ("200.00", TODAY - 5 * DAY, None),
        )
    ]
    ok(post(client, f"/api/rent/payments/{paid[1]}/bounce", on=TODAY - 2 * DAY), 201)
    return lease, (rent, fee, credit), paid


def test_each_ledger_line_names_the_charge_or_payment_it_opens(client, conn, staff, pmc):
    lease, (rent, fee, credit), (first, second) = ledger_with_a_bounce(client, conn, pmc)

    ledger = ok(client.get(f"/api/leases/{lease}/ledger"))

    assert [(line["kind"], line["charge_id"], line["transfer_id"]) for line in ledger["lines"]] == [
        ("rent", str(rent), None),
        ("fee", str(fee), None),
        ("credit", str(credit), None),
        ("payment", None, first),
        ("payment", None, second),
        # The bounce opens the payment that bounced.
        ("bounced payment", None, second),
    ]


def test_a_payment_with_prepaid_rent_opens_as_the_lease_lists_it(client, conn, staff, pmc):
    lease, _ = lease_of(conn, pmc)
    charge(conn, pmc.pmc_id, lease, START, "100.00", memo="Rent")
    ok(
        post(
            client,
            f"/api/rent/leases/{lease}/payments",
            request_id=uuid.uuid4(),
            amount="250.00",
            received_on=TODAY - DAY,
        ),
        201,
    )

    [paid] = [line for line in ok(client.get(f"/api/leases/{lease}/ledger"))["lines"][1:]]
    [listed] = ok(client.get(f"/api/leases/{lease}"))["payments"]

    # 100.00 paid the rent and 150.00 is prepaid rent, two transfers: the line opens the payment.
    assert (paid["paid"], listed["amount"]) == ("100.00", "250.00")
    assert paid["transfer_id"] == listed["transfer_id"]


def test_a_ledger_over_days_brings_its_balance_forward(client, conn, staff, pmc):
    lease, _, _ = ledger_with_a_bounce(client, conn, pmc)

    ledger = ok(
        client.get(
            f"/api/leases/{lease}/ledger",
            params={"start": str(START + 6 * DAY), "end": str(TODAY - 6 * DAY)},
        )
    )

    # Rent and the fee came before: 1,550.00 forward; the bounced payment came after.
    assert (ledger["start"], ledger["end"], ledger["opening"]) == (
        str(START + 6 * DAY),
        str(TODAY - 6 * DAY),
        "1550.00",
    )
    assert [(line["kind"], line["balance"]) for line in ledger["lines"]] == [
        ("credit", "1530.00"),
        ("payment", "530.00"),
    ]
    assert ledger["balance"] == "530.00"


def test_a_ledger_downloads_as_csv_with_its_balance_forward(client, conn, staff, pmc):
    lease, _, _ = ledger_with_a_bounce(client, conn, pmc)

    answer = client.get(
        f"/api/leases/{lease}/ledger",
        params={"start": str(START + 5 * DAY), "end": str(TODAY - 6 * DAY), "format": "csv"},
    )

    assert answer.status_code == 200, answer.text
    assert answer.headers["content-type"].startswith("text/csv")
    assert "lease-ledger-" in answer.headers["content-disposition"]
    assert answer.text.splitlines() == [
        "date,type,memo,charges,payments,balance",
        f"{START + 5 * DAY},Balance forward,,,,1500.00",
        # A memo a spreadsheet would run as a formula is text; a credit stays a number.
        f"{START + 5 * DAY},Fee,'=Lost key,50.00,,1550.00",
        f"{START + 6 * DAY},Credit,Credit,-20.00,,1530.00",
        f"{TODAY - 10 * DAY},Payment,Check 101,,1000.00,530.00",
    ]


@pytest.mark.parametrize(
    "asked",
    [
        {"start": str(TODAY), "end": str(TODAY - DAY)},
        {"start": "2026-02-30"},
        {"format": "xlsx"},
    ],
)
def test_a_ledger_refuses_days_backwards_or_a_format_it_has_not(client, conn, staff, pmc, asked):
    lease, _ = lease_of(conn, pmc)
    assert client.get(f"/api/leases/{lease}/ledger", params=asked).status_code == 422


def test_another_companys_lease_is_not_found(client, conn, staff):
    other = make_pmc(conn)
    theirs, _ = lease_of(conn, other)
    for path in (f"/api/leases/{theirs}", f"/api/leases/{theirs}/ledger"):
        answer = client.get(path)
        assert answer.status_code == 404, path
        assert answer.json()["detail"] == "no such lease"


@pytest.mark.parametrize("caller", ["nobody", "owner", "tenant"])
def test_only_staff_read_a_leases_ledger(client, conn, app_conn, pmc, caller):
    lease, _ = lease_of(conn, pmc)
    if caller == "owner":
        join_as_owner(client, app_conn, pmc)
    elif caller == "tenant":
        join_as_tenant(client, conn, app_conn, pmc)
    assert client.get(f"/api/leases/{lease}/ledger").status_code == (
        401 if caller == "nobody" else 403
    )


def test_a_ledger_is_of_a_lease(client, staff):
    assert client.get("/api/leases/not-an-id/ledger").status_code == 422


def test_lease_rows_name_each_tenant_with_their_own_page(client, conn, staff, pmc):
    lease, (b, a) = lease_of(conn, pmc, ("Tenant B", "Tenant A"))
    people = [{"id": str(a), "name": "Tenant A"}, {"id": str(b), "name": "Tenant B"}]
    unit = conn.execute("SELECT unit_id FROM trust_leases WHERE id = %s", (lease,)).fetchone()[0]

    [row] = ok(client.get("/api/leases"))["items"]
    assert (row["tenants"], row["people"]) == (["Tenant A", "Tenant B"], people)
    assert ok(client.get(f"/api/units/{unit}"))["leases"][0]["people"] == people
    found = ok(client.get(f"/api/properties/{pmc.owners[0].property_id}"))
    assert found["units"][0]["lease"]["people"] == people


def test_a_tenants_page_says_whether_they_can_sign_in(client, conn, app_conn, staff, pmc):
    _, (tenant,) = lease_of(conn, pmc)
    page = ok(client.get(f"/api/tenants/{tenant}"))
    assert (page["portal"], page["login_email"]) == ("no login", None)
    invite_token(app_conn, pmc.pmc_id, "tenant", tenant, "tenant.a@example.com")
    page = ok(client.get(f"/api/tenants/{tenant}"))
    assert (page["portal"], page["login_email"]) == ("invited", "tenant.a@example.com")
    listed = ok(client.get("/api/tenants"))["items"]
    assert [(t["name"], t["portal"]) for t in listed if t["name"] == "Tenant A"] == [
        ("Tenant A", "invited")
    ]
