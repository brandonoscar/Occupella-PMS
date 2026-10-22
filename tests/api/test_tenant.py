"""The tenant portal's endpoints (src/pms/api/tenant.py): a tenant sees their own leases, what
they owe and what comes due, each lease's ledger and the statement of a deposit returned at
move-out, and files maintenance requests, never anyone else's."""

import csv
import io
import uuid
from datetime import UTC, date, datetime, timedelta

import pytest
from helpers import (
    add_recurring_charge,
    add_tenant,
    add_unit,
    add_work_order,
    apply_payment,
    charge,
    make_pmc,
    open_lease,
    return_deposit,
    step_work_order,
    stop_recurring_charge,
    transfer_batch,
)
from people import join_as, join_as_owner, join_as_staff, join_as_tenant, tenant_of

from pms.api import tenant as tenant_api

JAN_1, FEB_1 = date(2026, 1, 1), date(2026, 2, 1)
JAN_3 = datetime(2026, 1, 3, 12, tzinfo=UTC)


class Home:
    """Tenant 1 and Tenant 2 rent Unit 1 of Property 1 from January 2026 at 1500 a month; Tenant
    1 also rented Unit 0 through 2025. Tenant 3 rents Unit 2. January's rent is paid, February's
    is not."""

    def __init__(self, conn, pmc):
        pmc_id, prop = pmc.pmc_id, pmc.owners[0].property_id
        self.pmc = pmc
        self.tenant = tenant_of(conn, pmc)
        self.co_tenant = add_tenant(conn, pmc_id, prop, "Tenant 2")
        self.neighbour = add_tenant(conn, pmc_id, prop, "Tenant 3")
        self.unit = add_unit(conn, pmc_id, prop, "Unit 1")
        self.old_unit = add_unit(conn, pmc_id, prop, "Unit 0")
        self.next_door = add_unit(conn, pmc_id, prop, "Unit 2")
        self.lease = open_lease(
            conn, pmc_id, self.unit, JAN_1, None, "1500.00", [self.tenant, self.co_tenant]
        )
        self.old_lease = open_lease(
            conn,
            pmc_id,
            self.old_unit,
            date(2025, 1, 1),
            date(2025, 12, 31),
            "1400.00",
            [self.tenant],
        )
        self.neighbours_lease = open_lease(
            conn, pmc_id, self.next_door, JAN_1, None, "1100.00", [self.neighbour]
        )
        january = charge(conn, pmc_id, self.lease, JAN_1, "1500.00")
        charge(conn, pmc_id, self.lease, FEB_1, "1500.00")
        charge(conn, pmc_id, self.neighbours_lease, JAN_1, "1100.00")
        (paid,) = transfer_batch(
            conn, [(pmc.operating_cash, pmc.owners[0].account, "1500.00")], JAN_3
        )
        apply_payment(conn, pmc_id, january, paid, "1500.00")


@pytest.fixture
def home(conn, pmc):
    return Home(conn, pmc)


def new_request(home, lease=None, **changes):
    return {
        "lease_id": str(lease or home.lease),
        "summary": "Kitchen tap drips",
        "entry_allowed": True,
        **changes,
    }


def test_a_tenant_sees_their_leases_with_what_they_owe(client, conn, app_conn, home):
    join_as_tenant(client, conn, app_conn, home.pmc)

    answer = client.get("/api/tenant/leases")

    assert answer.status_code == 200
    assert answer.json() == [
        {
            "lease_id": str(home.lease),
            "property": "Property 1",
            "unit": "Unit 1",
            "starts_on": "2026-01-01",
            "ends_on": None,
            "monthly_rent": "1500.00",
            "rent_due_day": 1,
            "rent_changes": [],
            "tenants": ["Tenant 1", "Tenant 2"],
            "balance_due": "1500.00",
            "in_effect": True,
            "each_month": [
                {
                    "what": "Rent",
                    "kind": "rent",
                    "amount": "1500.00",
                    "starts_on": "2026-01-01",
                    "ends_on": None,
                }
            ],
            "coming_due": [],
            "deposit_returned": False,
            "renters_insurance": {
                "required": False,
                "minimum_liability": None,
                "carrier": None,
                "policy_number": None,
                "expires_on": None,
                "liability_limit": None,
                "standing": "none on file",
                "below_minimum": False,
            },
            # Only payments staff record are listed, with receipts; this one was posted directly.
            "payments": [],
            # No lease signed through the PMS and no lease documents kept.
            "files": [],
        },
        {
            "lease_id": str(home.old_lease),
            "property": "Property 1",
            "unit": "Unit 0",
            "starts_on": "2025-01-01",
            "ends_on": "2025-12-31",
            "monthly_rent": "1400.00",
            "rent_due_day": 1,
            "rent_changes": [],
            "tenants": ["Tenant 1"],
            "balance_due": "0.00",
            "in_effect": False,
            "each_month": [],  # it has ended
            "coming_due": [],
            "deposit_returned": False,
            "renters_insurance": None,  # not in effect
            "payments": [],
            "files": [],
        },
    ]


def test_a_tenant_sees_what_each_month_charges_and_what_comes_due(client, conn, app_conn, home):
    pmc_id, today = home.pmc.pmc_id, datetime.now(UTC).date()
    add_recurring_charge(conn, pmc_id, home.lease, "35.00", FEB_1, "Pet rent")
    parking = add_recurring_charge(conn, pmc_id, home.lease, "25.00", FEB_1, "Parking", "credit")
    stop_recurring_charge(conn, pmc_id, parking, today)  # no longer charged from today
    storage = add_recurring_charge(conn, pmc_id, home.lease, "40.00", FEB_1, "Storage")
    stop_recurring_charge(conn, pmc_id, storage, today + timedelta(days=60))
    charge(conn, pmc_id, home.lease, today + timedelta(days=10), "50.00", "fee", "Key replacement")
    join_as_tenant(client, conn, app_conn, home.pmc)

    (lease,) = [x for x in client.get("/api/tenant/leases").json() if x["in_effect"]]

    assert [(m["what"], m["kind"], m["amount"], m["ends_on"]) for m in lease["each_month"]] == [
        ("Rent", "rent", "1500.00", None),
        ("Pet rent", "fee", "35.00", None),
        ("Storage", "fee", "40.00", str(today + timedelta(days=60))),
    ]
    assert lease["coming_due"] == [
        {
            "due_on": str(today + timedelta(days=10)),
            "kind": "fee",
            "memo": "Key replacement",
            "open": "50.00",
        }
    ]
    assert lease["balance_due"] == "1500.00"  # February's rent; the fee is not due yet


def test_each_month_shows_the_rent_of_the_next_due_date_and_each_change_to_come(
    client, conn, app_conn, home, monkeypatch
):
    """The rent a tenant pays next is the rent in force on the lease's next due date (H3: the
    card said today's rent while a raise took effect on the next one)."""
    pmc_id = home.pmc.pmc_id
    for effective, rent, notice in (
        (date(2026, 11, 1), "1575.00", date(2026, 9, 20)),
        (date(2027, 1, 1), "1600.00", date(2026, 10, 1)),
    ):
        conn.execute(
            "INSERT INTO trust_rent_changes (pmc_id, lease_id, effective_on, monthly_rent,"
            " notice_given_on, served_by) VALUES (%s, %s, %s, %s, %s, 'mail')",
            (pmc_id, home.lease, effective, rent, notice),
        )
    join_as_tenant(client, conn, app_conn, home.pmc)

    def rents_on(day):
        monkeypatch.setattr(tenant_api, "today", lambda: day)
        (lease,) = [x for x in client.get("/api/tenant/leases").json() if x["in_effect"]]
        return lease["monthly_rent"], [
            (m["amount"], m["starts_on"], m["ends_on"])
            for m in lease["each_month"]
            if m["kind"] == "rent"
        ]

    # October's rent is the old one; November's, due next, is the new one, then January's.
    assert rents_on(date(2026, 10, 22)) == (
        "1500.00",
        [("1575.00", "2026-11-01", "2027-01-01"), ("1600.00", "2027-01-01", None)],
    )
    # On a due date, that day's rent is the one to pay.
    assert rents_on(date(2026, 10, 1)) == (
        "1500.00",
        [
            ("1500.00", "2026-01-01", "2026-11-01"),
            ("1575.00", "2026-11-01", "2027-01-01"),
            ("1600.00", "2027-01-01", None),
        ],
    )
    assert rents_on(date(2026, 11, 1))[1][0] == ("1575.00", "2026-11-01", "2027-01-01")
    # Rent due on the 15th from October: October's, still to come on the 10th, is the old rent.
    conn.execute(
        "INSERT INTO trust_rent_due_days (pmc_id, lease_id, starts_on, due_day)"
        " VALUES (%s, %s, '2026-10-01', 15)",
        (pmc_id, home.lease),
    )
    assert rents_on(date(2026, 10, 10))[1][0] == ("1500.00", "2026-01-01", "2026-11-01")
    assert rents_on(date(2026, 10, 16))[1][0] == ("1575.00", "2026-11-01", "2027-01-01")
    # After the last change, one rent.
    assert rents_on(date(2027, 2, 3)) == ("1600.00", [("1600.00", "2027-01-01", None)])


def test_a_lease_to_come_shows_the_rent_of_its_first_due_date(client, conn, app_conn, home):
    pmc_id = home.pmc.pmc_id
    unit = add_unit(conn, pmc_id, home.pmc.owners[0].property_id, "Unit 9")
    starts = datetime.now(UTC).date().replace(day=1) + timedelta(days=95)
    to_come = open_lease(conn, pmc_id, unit, starts, None, "1800.00", [home.tenant])
    join_as_tenant(client, conn, app_conn, home.pmc)

    (lease,) = [x for x in client.get("/api/tenant/leases").json() if x["lease_id"] == str(to_come)]

    assert lease["in_effect"] is False
    assert lease["each_month"] == [
        {
            "what": "Rent",
            "kind": "rent",
            "amount": "1800.00",
            "starts_on": str(starts),
            "ends_on": None,
        }
    ]


@pytest.fixture
def moved_out(client, conn, app_conn):
    """A tenant whose lease ended in June, with their 1500.00 deposit returned in July: 1000.00
    kept for June's rent, 500.00 back."""
    pmc = make_pmc(conn)
    tenant = tenant_of(conn, pmc)
    unit = add_unit(conn, pmc.pmc_id, pmc.owners[0].property_id)
    lease = open_lease(conn, pmc.pmc_id, unit, JAN_1, date(2026, 6, 30), "1000.00", [tenant])
    transfer_batch(conn, [(pmc.deposit_cash, pmc.tenant_deposit, "1500.00")], JAN_3)
    charge(conn, pmc.pmc_id, lease, date(2026, 6, 1), "1000.00", "rent", "June rent")
    return_deposit(conn, pmc.pmc_id, lease, "move-out-1", datetime(2026, 7, 5, 12, tzinfo=UTC))
    join_as_tenant(client, conn, app_conn, pmc)
    return pmc, lease


@pytest.mark.parametrize(("fmt", "magic"), [("pdf", b"%PDF-"), ("csv", b"line,")])
def test_a_tenant_downloads_the_statement_of_their_returned_deposit(client, moved_out, fmt, magic):
    _, lease = moved_out

    (seen,) = client.get("/api/tenant/leases").json()
    answer = client.get(
        f"/api/tenant/leases/{lease}/deposit-return/download", params={"format": fmt}
    )

    assert seen["deposit_returned"] is True
    assert answer.status_code == 200
    assert answer.content.startswith(magic)
    assert answer.headers["content-disposition"] == f'attachment; filename="deposit-return.{fmt}"'


def test_the_deposit_statement_is_the_deposit_return_report(client, conn, moved_out):
    pmc, lease = moved_out
    direct = conn.execute(
        "SELECT item, detail, amount FROM trust_report_deposit_return(%s, %s)",
        (pmc.pmc_id, lease),
    ).fetchall()

    answer = client.get(
        f"/api/tenant/leases/{lease}/deposit-return/download", params={"format": "csv"}
    )

    header, *rows = csv.reader(io.StringIO(answer.content.decode(), newline=""))
    assert header == ["line", "item", "detail", "amount"]
    assert [tuple(row[1:]) for row in rows] == [
        (item, detail or "", "" if amount is None else str(amount))
        for item, detail, amount in direct
    ]


def test_a_deposit_not_yet_returned_has_no_statement(client, conn, app_conn, home):
    join_as_tenant(client, conn, app_conn, home.pmc)

    answer = client.get(f"/api/tenant/leases/{home.old_lease}/deposit-return/download")

    assert answer.status_code == 404
    assert answer.json()["detail"] == "this lease's deposit has not been returned"


def test_the_ledger_is_the_tenant_ledger_report(client, conn, app_conn, home):
    join_as_tenant(client, conn, app_conn, home.pmc)
    direct = conn.execute(
        "SELECT * FROM trust_report_tenant_ledger(%s, %s, %s)",
        (home.pmc.pmc_id, home.lease, date(2026, 1, 31)),
    ).fetchall()

    answer = client.get(f"/api/tenant/leases/{home.lease}/ledger", params={"as_of": "2026-01-31"})

    assert answer.status_code == 200
    ledger = answer.json()
    assert ledger["title"] == "Tenant ledger through 2026-01-31"
    assert ledger["columns"] == ["line", "on_date", "item", "detail", "charged", "paid", "balance"]
    assert len(ledger["rows"]) == len(direct)
    assert ledger["rows"][-1][2:] == ["balance", "end of day UTC", "1500.00", "1500.00", "0.00"]


def test_the_ledger_runs_through_today_by_default(client, conn, app_conn, home):
    join_as_tenant(client, conn, app_conn, home.pmc)

    ledger = client.get(f"/api/tenant/leases/{home.lease}/ledger").json()

    assert ledger["title"] == f"Tenant ledger through {datetime.now(UTC).date().isoformat()}"
    assert ledger["rows"][-1][-1] == "1500.00"


@pytest.mark.parametrize(("fmt", "magic"), [("pdf", b"%PDF-"), ("csv", b"line,"), ("xlsx", b"PK")])
def test_the_tenant_downloads_their_ledger(client, conn, app_conn, home, fmt, magic):
    join_as_tenant(client, conn, app_conn, home.pmc)

    answer = client.get(
        f"/api/tenant/leases/{home.lease}/ledger/download",
        params={"as_of": "2026-02-28", "format": fmt},
    )

    assert answer.status_code == 200
    assert answer.content.startswith(magic)
    assert answer.headers["content-disposition"] == (
        f'attachment; filename="tenant-ledger-2026-02-28.{fmt}"'
    )


@pytest.mark.parametrize("whose", ["a neighbour's", "another PMC's", "no one's"])
def test_a_lease_that_isnt_theirs_reads_as_not_found(client, conn, app_conn, home, whose):
    join_as_tenant(client, conn, app_conn, home.pmc)
    if whose == "a neighbour's":
        lease = home.neighbours_lease
    elif whose == "another PMC's":
        lease = Home(conn, make_pmc(conn)).lease
    else:
        lease = uuid.uuid4()

    answers = [
        client.get(f"/api/tenant/leases/{lease}/ledger"),
        client.get(f"/api/tenant/leases/{lease}/ledger/download"),
        client.get(f"/api/tenant/leases/{lease}/deposit-return/download"),
        client.post("/api/tenant/requests", json=new_request(home, lease)),
    ]

    assert [a.status_code for a in answers] == [404] * 4
    assert {a.json()["detail"] for a in answers} == {"no such lease of yours"}


@pytest.mark.parametrize("caller", ["nobody", "staff", "owner"])
def test_only_a_tenant_reaches_the_tenant_endpoints(client, conn, app_conn, home, caller):
    if caller == "staff":
        join_as_staff(client, conn, app_conn, home.pmc)
    elif caller == "owner":
        join_as_owner(client, app_conn, home.pmc)
    expected = 401 if caller == "nobody" else 403

    answers = [
        client.get("/api/tenant/leases"),
        client.get(f"/api/tenant/leases/{home.lease}/ledger"),
        client.get(f"/api/tenant/leases/{home.lease}/ledger/download"),
        client.get(f"/api/tenant/leases/{home.lease}/deposit-return/download"),
        client.get("/api/tenant/requests"),
        client.post("/api/tenant/requests", json=new_request(home)),
    ]

    assert [a.status_code for a in answers] == [expected] * 6


@pytest.mark.parametrize(
    ("path", "params"),
    [
        ("/api/tenant/leases/{lease}/ledger", {"as_of": "not a day"}),
        ("/api/tenant/leases/not-a-lease/ledger", {}),
        ("/api/tenant/leases/{lease}/ledger/download", {"format": "docx"}),
        ("/api/tenant/leases/{lease}/ledger/download", {"as_of": "2026-02-30"}),
        ("/api/tenant/leases/{lease}/deposit-return/download", {"format": "docx"}),
        ("/api/tenant/leases/not-a-lease/deposit-return/download", {}),
    ],
)
def test_a_bad_ledger_request_is_refused(client, conn, app_conn, home, path, params):
    join_as_tenant(client, conn, app_conn, home.pmc)

    answer = client.get(path.format(lease=home.lease), params=params)

    assert answer.status_code == 422


def test_a_tenant_files_a_request_and_their_co_tenant_sees_it(
    client, other_client, conn, app_conn, home
):
    join_as_tenant(client, conn, app_conn, home.pmc)
    join_as(other_client, app_conn, home.pmc.pmc_id, "tenant", home.co_tenant)

    filed = client.post("/api/tenant/requests", json=new_request(home, summary="  Tap drips  "))

    assert filed.status_code == 201, filed.text
    request = filed.json()
    assert set(request) == {
        "id",
        "lease_id",
        "property",
        "unit",
        "filed_by",
        "summary",
        "entry_allowed",
        "emergency",
        "submitted_at",
        "status",
        "status_at",
        "visit_on",
        "visit_window",
        "photos",
    }
    assert (
        request["filed_by"],
        request["summary"],
        request["status"],
        request["emergency"],
        request["photos"],
    ) == ("Tenant 1", "Tap drips", "received", False, 0)
    assert other_client.get("/api/tenant/requests").json() == [request]


def test_the_tenant_follows_the_work_order_but_never_sees_its_vendor(client, conn, app_conn, home):
    join_as_tenant(client, conn, app_conn, home.pmc)
    request = client.post("/api/tenant/requests", json=new_request(home)).json()
    pmc_id = home.pmc.pmc_id
    now = datetime.now(UTC)
    order = add_work_order(conn, pmc_id, home.pmc.owners[0].property_id, home.unit, "Tap", now)
    conn.execute(
        "INSERT INTO trust_maintenance_request_work_orders (pmc_id, request_id, work_order_id)"
        " VALUES (%s, %s, %s)",
        (pmc_id, request["id"], order),
    )

    statuses = [client.get("/api/tenant/requests").json()[0]["status"]]
    step_work_order(conn, pmc_id, order, "assigned", now, home.pmc.vendor_id)
    statuses.append(client.get("/api/tenant/requests").json()[0]["status"])
    step_work_order(conn, pmc_id, order, "completed", now)
    seen = client.get("/api/tenant/requests").json()[0]

    assert statuses + [seen["status"]] == ["opened", "assigned", "completed"]
    assert "vendor" not in seen and "work_order_id" not in seen


def test_a_neighbours_requests_are_never_shown(client, other_client, conn, app_conn, home):
    join_as(other_client, app_conn, home.pmc.pmc_id, "tenant", home.neighbour)
    other_client.post("/api/tenant/requests", json=new_request(home, home.neighbours_lease))
    join_as_tenant(client, conn, app_conn, home.pmc)

    assert client.get("/api/tenant/requests").json() == []


def test_a_request_is_filed_only_under_a_lease_in_effect(client, conn, app_conn, home):
    join_as_tenant(client, conn, app_conn, home.pmc)

    answer = client.post("/api/tenant/requests", json=new_request(home, home.old_lease))

    assert answer.status_code == 409
    assert answer.json()["detail"] == "requests are filed under a lease in effect today"


def test_ten_requests_a_day_per_lease(client, conn, app_conn, home):
    join_as_tenant(client, conn, app_conn, home.pmc)
    # Yesterday's don't count.
    conn.execute(
        "INSERT INTO trust_maintenance_requests"
        " (pmc_id, lease_id, tenant_id, summary, entry_allowed, submitted_at)"
        " VALUES (%s, %s, %s, 'Old', false, %s)",
        (
            home.pmc.pmc_id,
            home.lease,
            home.tenant,
            datetime.now(UTC) - timedelta(days=1, minutes=1),
        ),
    )

    answers = [client.post("/api/tenant/requests", json=new_request(home)) for _ in range(11)]

    assert [a.status_code for a in answers] == [201] * 10 + [429]


@pytest.mark.parametrize(
    "changes",
    [
        {"summary": ""},
        {"summary": "   \n "},
        {"summary": "x" * 2001},
        {"entry_allowed": "maybe"},
        {"lease_id": "not-a-lease"},
    ],
)
def test_a_bad_request_is_refused(client, conn, app_conn, home, changes):
    join_as_tenant(client, conn, app_conn, home.pmc)

    answer = client.post("/api/tenant/requests", json=new_request(home, **changes))

    assert answer.status_code == 422
    filed = conn.execute(
        "SELECT count(*) FROM trust_maintenance_requests WHERE pmc_id = %s", (home.pmc.pmc_id,)
    ).fetchone()[0]
    assert filed == 0
