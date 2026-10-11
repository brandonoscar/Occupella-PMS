"""The tenant portal's endpoints (src/pms/api/tenant.py): a tenant sees their own leases, what
they owe and each lease's ledger, and files maintenance requests, never anyone else's."""

import uuid
from datetime import UTC, date, datetime, timedelta

import pytest
from helpers import (
    add_tenant,
    add_unit,
    add_work_order,
    apply_payment,
    charge,
    make_pmc,
    open_lease,
    step_work_order,
    transfer_batch,
)
from people import join_as, join_as_owner, join_as_staff, join_as_tenant, tenant_of

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
            "tenants": ["Tenant 1", "Tenant 2"],
            "balance_due": "1500.00",
            "in_effect": True,
        },
        {
            "lease_id": str(home.old_lease),
            "property": "Property 1",
            "unit": "Unit 0",
            "starts_on": "2025-01-01",
            "ends_on": "2025-12-31",
            "monthly_rent": "1400.00",
            "tenants": ["Tenant 1"],
            "balance_due": "0.00",
            "in_effect": False,
        },
    ]


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
        client.post("/api/tenant/requests", json=new_request(home, lease)),
    ]

    assert [a.status_code for a in answers] == [404] * 3
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
        client.get("/api/tenant/requests"),
        client.post("/api/tenant/requests", json=new_request(home)),
    ]

    assert [a.status_code for a in answers] == [expected] * 5


@pytest.mark.parametrize(
    ("path", "params"),
    [
        ("/api/tenant/leases/{lease}/ledger", {"as_of": "not a day"}),
        ("/api/tenant/leases/not-a-lease/ledger", {}),
        ("/api/tenant/leases/{lease}/ledger/download", {"format": "docx"}),
        ("/api/tenant/leases/{lease}/ledger/download", {"as_of": "2026-02-30"}),
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
        "submitted_at",
        "status",
        "status_at",
    }
    assert (request["filed_by"], request["summary"], request["status"]) == (
        "Tenant 1",
        "Tap drips",
        "received",
    )
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
