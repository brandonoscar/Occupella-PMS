"""Rent first through the API (migration 20261018000093; docs/law.md): a payment staff record,
and prepaid rent used, pay a lease's rent and monthly charges before its other charges, even an
older fee; a moved tenant's payment pays the rent of every lease they moved through before any of
their fees. The database's side is in tests/test_rent_first.py. Every lease here is synthetic."""

import json
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from helpers import add_unit, balance, charge, open_lease, transfer_batch
from people import join_as_staff, tenant_of

TODAY = datetime.now(UTC).date()
STARTED = TODAY - timedelta(days=200)
FEE_DAY = TODAY - timedelta(days=60)
RENT_DAY = TODAY - timedelta(days=20)


@pytest.fixture
def staff(client, conn, app_conn, pmc):
    return join_as_staff(client, conn, app_conn, pmc)


@pytest.fixture
def lease(conn, pmc):
    """Tenant 1's lease of Unit 1 at Property 1, owing an older fee and a newer rent."""
    unit = add_unit(conn, pmc.pmc_id, pmc.owners[0].property_id)
    return open_lease(conn, pmc.pmc_id, unit, STARTED, None, "1000.00", [tenant_of(conn, pmc)])


def post(client, path, **body):
    return client.post(path, json=json.loads(json.dumps(body, default=str)))


def ok(answer):
    assert answer.status_code == 201, answer.text
    return answer.json()


def paid(conn, charge_id):
    return conn.execute(
        "SELECT coalesce(sum(amount), 0) FROM trust_charge_payments WHERE charge_id = %s",
        (charge_id,),
    ).fetchone()[0]


def test_a_payment_pays_the_rent_before_an_older_fee(client, conn, staff, pmc, lease):
    fee = charge(conn, pmc.pmc_id, lease, FEE_DAY, "100.00", "fee", "Lost key")
    rent = charge(conn, pmc.pmc_id, lease, RENT_DAY, "1000.00", "rent", "Rent")

    first = ok(
        post(
            client,
            f"/api/rent/leases/{lease}/payments",
            request_id=uuid.uuid4(),
            amount="1000.00",
            received_on=TODAY,
        )
    )
    after_rent = (paid(conn, rent), paid(conn, fee))
    ok(
        post(
            client,
            f"/api/rent/leases/{lease}/payments",
            request_id=uuid.uuid4(),
            amount="60.00",
            received_on=TODAY,
        )
    )

    assert first["to_owner"] == "1000.00"
    assert after_rent == (Decimal("1000.00"), Decimal("0.00"))
    assert (paid(conn, rent), paid(conn, fee)) == (Decimal("1000.00"), Decimal("60.00"))


def test_prepaid_rent_pays_the_rent_before_an_older_fee(client, conn, staff, pmc, lease):
    fee = charge(conn, pmc.pmc_id, lease, FEE_DAY, "100.00", "fee", "Lost key")
    rent = charge(conn, pmc.pmc_id, lease, RENT_DAY, "1000.00", "rent", "Rent")
    transfer_batch(
        conn,
        [(pmc.operating_cash, pmc.prepaid_rent, "500.00")],
        datetime.combine(STARTED, datetime.min.time(), UTC),
    )

    used = ok(
        post(client, f"/api/rent/leases/{lease}/use-prepaid", request_id=uuid.uuid4(), on=TODAY)
    )

    assert used["to_owner"] == "500.00"
    assert (paid(conn, rent), paid(conn, fee)) == (Decimal("500.00"), Decimal("0.00"))


def test_a_moved_tenant_pays_the_rent_of_every_lease_before_any_fee(
    client, conn, app_conn, staff, pmc, lease
):
    old_fee = charge(conn, pmc.pmc_id, lease, FEE_DAY, "100.00", "fee", "Lost key")
    old_rent = charge(conn, pmc.pmc_id, lease, RENT_DAY, "500.00", "rent", "Rent")
    there = add_unit(conn, pmc.pmc_id, pmc.owners[1].property_id, "Unit 7")
    moved = app_conn.execute(
        "SELECT trust_transfer_lease(%s, %s, %s, %s, NULL, 1000.00, NULL, NULL)",
        (pmc.pmc_id, lease, there, TODAY - timedelta(days=10)),
    ).fetchone()[0]
    new_rent = charge(conn, pmc.pmc_id, moved, TODAY - timedelta(days=5), "1000.00", "rent")
    before = (balance(conn, pmc.owners[0].account), balance(conn, pmc.owners[1].account))

    answer = ok(
        post(
            client,
            f"/api/rent/leases/{moved}/payments",
            request_id=uuid.uuid4(),
            amount="1550.00",
            received_on=TODAY,
        )
    )

    # Both rents first (the old one first), then 50.00 of the old lease's fee.
    assert answer["to_owner"] == "1550.00"
    assert (paid(conn, old_rent), paid(conn, new_rent), paid(conn, old_fee)) == (
        Decimal("500.00"),
        Decimal("1000.00"),
        Decimal("50.00"),
    )
    assert (
        balance(conn, pmc.owners[0].account) - before[0],
        balance(conn, pmc.owners[1].account) - before[1],
    ) == (Decimal("550.00"), Decimal("1000.00"))


def test_move_outs_list_what_a_deposit_would_pay_rent_first(client, conn, staff, pmc):
    """An ended lease's open charges on Move-outs, in the order its deposit pays them, each less
    the credits that came off it."""
    unit = add_unit(conn, pmc.pmc_id, pmc.owners[0].property_id)
    ended = TODAY - timedelta(days=3)
    lease = open_lease(conn, pmc.pmc_id, unit, STARTED, ended, "1000.00", [tenant_of(conn, pmc)])
    transfer_batch(
        conn,
        [(pmc.deposit_cash, pmc.tenant_deposit, "1500.00")],
        datetime.combine(STARTED, datetime.min.time(), UTC),
    )
    fee = charge(conn, pmc.pmc_id, lease, FEE_DAY, "100.00", "fee", "Lost key")
    rent = charge(conn, pmc.pmc_id, lease, RENT_DAY, "1000.00", "rent", "Rent")
    charge(conn, pmc.pmc_id, lease, RENT_DAY, "150.00", "credit", "Courtesy")

    rows = {r["lease_id"]: r for r in client.get("/api/move-out").json()}

    assert [(c["id"], c["open"]) for c in rows[str(lease)]["open_charges"]] == [
        (str(rent), "850.00"),
        (str(fee), "100.00"),
    ]
    assert rows[str(lease)]["owed"] == "950.00"


def test_the_lease_page_lists_the_rent_before_a_fee_due_the_same_day_whatever_their_ids(
    client, conn, staff, pmc, lease
):
    with conn.transaction():  # the same time, the fee with the smaller id
        conn.execute(
            "INSERT INTO trust_charges (id, pmc_id, lease_id, due_on, kind, amount, memo)"
            " VALUES ('00000000-0000-4000-8000-000000000002', %s, %s, %s, 'fee', 20.00,"
            " 'Lost key')",
            (pmc.pmc_id, lease, RENT_DAY),
        )
        conn.execute(
            "INSERT INTO trust_charges (id, pmc_id, lease_id, due_on, kind, amount, memo)"
            " VALUES ('ffffffff-0000-4000-8000-000000000002', %s, %s, %s, 'rent', 1000.00,"
            " 'Rent')",
            (pmc.pmc_id, lease, RENT_DAY),
        )

    row = client.get(f"/api/leases/{lease}").json()

    assert [c["memo"] for c in row["open_charges"]] == ["Rent", "Lost key"]
