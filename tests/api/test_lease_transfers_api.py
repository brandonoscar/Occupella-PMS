"""A lease's tenants moving to another unit through the API (migration 0091; the founder's review,
q010 and q042; POST /api/move-out/<id>/transfer): their deposits stay held for the new lease,
their prepaid rent first pays what the old lease owes, and what it still owes is paid first by the
new lease's payments, into the old owner's account. Every lease here is synthetic."""

import json
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from helpers import (
    add_unit,
    balance,
    bank_sums,
    charge,
    make_pmc,
    open_account,
    open_lease,
    transfer_batch,
)
from people import join_as_owner, join_as_staff, join_as_tenant, tenant_of

TODAY = datetime.now(UTC).date()
STARTED = TODAY - timedelta(days=200)
OWED_ON = TODAY - timedelta(days=20)


def at(day):
    return datetime.combine(day, datetime.min.time(), UTC)


@pytest.fixture
def staff(client, conn, app_conn, pmc):
    return join_as_staff(client, conn, app_conn, pmc, name="Manager 1")


@pytest.fixture
def lease(conn, pmc):
    """Tenant 1's month-to-month lease of Unit 1 at Property 1 (Owner 1's), with a 1200.00
    deposit and 1000.00 of rent unpaid."""
    unit = add_unit(conn, pmc.pmc_id, pmc.owners[0].property_id)
    lease_id = open_lease(conn, pmc.pmc_id, unit, STARTED, None, "1000.00", [tenant_of(conn, pmc)])
    transfer_batch(conn, [(pmc.deposit_cash, pmc.tenant_deposit, "1200.00")], at(STARTED))
    charge(conn, pmc.pmc_id, lease_id, OWED_ON, "1000.00", memo="Rent")
    return lease_id


@pytest.fixture
def there(conn, pmc):
    """Unit 7 at Property 2, Owner 2's."""
    return add_unit(conn, pmc.pmc_id, pmc.owners[1].property_id, "Unit 7")


def ok(answer, status=201):
    assert answer.status_code == status, answer.text
    return answer.json() if answer.content else None


def refused(answer, status, detail=None):
    assert answer.status_code == status, answer.text
    if detail is not None:
        assert detail in answer.json()["detail"], answer.json()


def post(client, path, **body):
    return client.post(path, json=json.loads(json.dumps(body, default=str)))


def move(client, lease, unit, request_id=None, **more):
    body = {
        "request_id": request_id or uuid.uuid4(),
        "unit_id": unit,
        "moved_on": TODAY,
        "monthly_rent": "1100.00",
        "deposit_agreed_on": TODAY,
    } | more
    return post(client, f"/api/move-out/{lease}/transfer", **body)


def pay(client, lease, amount, request_id=None):
    return post(
        client,
        f"/api/rent/leases/{lease}/payments",
        request_id=request_id or uuid.uuid4(),
        amount=amount,
        received_on=TODAY,
    )


def lease_page(client, lease):
    return ok(client.get(f"/api/leases/{lease}"), 200)


def test_staff_move_a_lease_and_its_payments_pay_the_old_owner_first(
    client, conn, staff, pmc, lease, there
):
    moved = ok(move(client, lease, there))
    new = moved["lease_id"]
    ok(
        post(
            client,
            f"/api/rent/leases/{new}/charges",
            kind="fee",
            amount="100.00",
            due_on=TODAY,
            memo="Key",
        )
    )

    before = lease_page(client, new)
    old = lease_page(client, lease)
    request = uuid.uuid4()
    paid = ok(pay(client, new, "1500.00", request))
    again = ok(pay(client, new, "1500.00", request))
    after = lease_page(client, new)

    assert moved == {"lease_id": new, "prepaid_used": "0.00", "prepaid_moved": "0.00"}
    assert before["moved_from"] == {
        "lease_id": str(lease),
        "property": "Property 1",
        "unit": "Unit 1",
        "moved_on": str(TODAY),
    }
    assert (before["carried"], before["owed"], before["moved_to"]) == ("1000.00", "100.00", None)
    assert before["unit"] == "Unit 7" and before["monthly_rent"] == "1100.00"
    assert old["moved_to"]["lease_id"] == new
    assert old["ends_on"] == str(TODAY - timedelta(days=1))
    assert [(p["to_owner"], p["to_prepaid"]) for p in (paid, again)] == [("1100.00", "400.00")] * 2
    assert paid["transfers"] == again["transfers"]
    # March's rent to Owner 1, who was owed it; the key fee to Owner 2.
    assert (balance(conn, pmc.owners[0].account), balance(conn, pmc.owners[1].account)) == (
        Decimal("1000.00"),
        Decimal("100.00"),
    )
    assert (after["carried"], after["owed"]) == ("0.00", "0.00")
    assert [p["amount"] for p in after["payments"]] == ["1500.00"]
    assert set(bank_sums(conn, pmc.pmc_id).values()) == {0}


def test_a_payment_on_the_new_lease_that_bounces_comes_back_from_both_owners(
    client, conn, staff, pmc, lease, there
):
    new = ok(move(client, lease, there))["lease_id"]
    ok(pay(client, new, "600.00"))
    payment = lease_page(client, new)["payments"][0]["transfer_id"]

    ok(post(client, f"/api/rent/payments/{payment}/bounce", on=TODAY))

    assert balance(conn, pmc.owners[0].account) == 0
    assert lease_page(client, new)["carried"] == "1000.00"


def test_a_hold_on_the_old_lease_stops_its_part_of_a_payment_unless_accepted_past_it(
    client, conn, staff, pmc, lease, there
):
    """An eviction filed over the old lease after the move: a payment on the new lease can't pay
    what the old lease owes unless staff accept it past that hold."""
    new = ok(move(client, lease, there))["lease_id"]
    ok(post(client, f"/api/rent/leases/{lease}/holds", starts_on=TODAY, reason="Eviction filed"))

    refused(pay(client, new, "200.00"), 409)
    accepted = ok(
        post(
            client,
            f"/api/rent/leases/{new}/payments",
            request_id=uuid.uuid4(),
            amount="200.00",
            received_on=TODAY,
            past_hold=True,
        )
    )

    assert accepted["to_owner"] == "200.00"
    assert balance(conn, pmc.owners[0].account) == Decimal("200.00")
    assert conn.execute(
        "SELECT count(*) FROM trust_payment_hold_allowances AS a"
        " JOIN trust_payment_holds AS h ON h.id = a.hold_id WHERE h.lease_id = %s",
        (lease,),
    ).fetchone() == (1,)


def test_a_payment_short_of_what_the_old_lease_owes_pays_it_alone(
    client, conn, staff, pmc, lease, there
):
    new = ok(move(client, lease, there))["lease_id"]
    ok(
        post(
            client,
            f"/api/rent/leases/{new}/charges",
            kind="fee",
            amount="100.00",
            due_on=TODAY,
            memo="Key",
        )
    )

    paid = ok(pay(client, new, "250.00"))
    assert (paid["to_owner"], paid["to_prepaid"]) == ("250.00", "0.00")
    assert (balance(conn, pmc.owners[0].account), balance(conn, pmc.owners[1].account)) == (
        Decimal("250.00"),
        Decimal("0"),
    )
    page = lease_page(client, new)
    assert (page["carried"], page["owed"]) == ("750.00", "100.00")


def test_prepaid_rent_pays_what_the_old_lease_owes_and_stays_in_its_bank(
    client, conn, staff, pmc, lease, there
):
    transfer_batch(conn, [(pmc.operating_cash, pmc.prepaid_rent, "1500.00")], at(STARTED))

    moved = ok(move(client, lease, there))

    assert (moved["prepaid_used"], moved["prepaid_moved"]) == ("1000.00", "0.00")
    assert balance(conn, pmc.owners[0].account) == Decimal("1000.00")
    assert balance(conn, pmc.prepaid_rent) == Decimal("500.00")
    assert lease_page(client, moved["lease_id"])["carried"] == "0.00"


def test_prepaid_rent_left_moves_to_the_new_owners_bank_with_its_cash(
    client, conn, staff, pmc, lease
):
    transfer_batch(conn, [(pmc.operating_cash, pmc.prepaid_rent, "1500.00")], at(STARTED))
    bank = conn.execute(
        "INSERT INTO trust_bank_accounts (pmc_id, kind, display_name)"
        " VALUES (%s, 'operating', 'Second operating trust account') RETURNING id",
        (pmc.pmc_id,),
    ).fetchone()[0]
    cash = open_account(conn, pmc.pmc_id, bank, "bank_cash")
    prop = conn.execute(
        "INSERT INTO trust_properties (pmc_id, display_name) VALUES (%s, 'Property 3')"
        " RETURNING id",
        (pmc.pmc_id,),
    ).fetchone()[0]
    open_account(conn, pmc.pmc_id, bank, "owner_property", pmc.owners[1].owner_id, prop)
    far = add_unit(conn, pmc.pmc_id, prop, "Unit 9")
    request = uuid.uuid4()

    moved = ok(move(client, lease, far, request))
    again = ok(move(client, lease, far, request))
    there_now = conn.execute(
        "SELECT ledger_account_id FROM trust_ledger_accounts"
        " WHERE bank_account_id = %s AND kind = 'prepaid_rent'",
        (bank,),
    ).fetchone()[0]

    assert moved == again
    assert (moved["prepaid_used"], moved["prepaid_moved"]) == ("1000.00", "500.00")
    assert (balance(conn, pmc.prepaid_rent), balance(conn, there_now)) == (0, Decimal("500.00"))
    assert balance(conn, cash) == Decimal("-500.00")  # its cash moved with it
    assert set(bank_sums(conn, pmc.pmc_id).values()) == {0}


def test_move_outs_list_the_new_lease_with_what_it_carries_and_not_the_old(
    client, conn, staff, pmc, lease, there
):
    new = ok(move(client, lease, there))["lease_id"]

    listed = {m["lease_id"]: m for m in ok(client.get("/api/move-out"), 200)}

    assert str(lease) not in listed
    row = listed[new]
    assert (row["owed"], row["carried"], row["held"]) == ("0.00", "1000.00", "1200.00")
    assert [(c["memo"], c["open"], c["home"]) for c in row["open_charges"]] == [
        ("Rent", "1000.00", "Property 1, Unit 1")
    ]


def test_the_new_lease_keeps_the_old_one_s_due_day_unless_staff_pick_another(
    client, conn, staff, pmc
):
    """A lease due on the 5th moves today and its rent stays due on the 5th; moved again, staff
    pick the 10th; moved once more, the first."""
    first = open_lease(
        conn,
        pmc.pmc_id,
        add_unit(conn, pmc.pmc_id, pmc.owners[0].property_id, "Unit 2"),
        STARTED,
        None,
        "900.00",
        [tenant_of(conn, pmc)],
    )
    conn.execute(
        "INSERT INTO trust_rent_due_days (pmc_id, lease_id, starts_on, due_day)"
        " VALUES (%s, %s, %s, 5)",
        (pmc.pmc_id, first, STARTED.replace(day=1)),
    )
    later, last = TODAY + timedelta(days=40), TODAY + timedelta(days=80)

    seven, request = unit(conn, pmc, "Unit 7"), uuid.uuid4()
    kept = ok(move(client, first, seven, request))["lease_id"]
    assert ok(move(client, first, seven, request))["lease_id"] == kept  # sent again: the same
    picked = ok(move(client, kept, unit(conn, pmc, "Unit 8"), moved_on=later, rent_due_day=10))[
        "lease_id"
    ]
    on_first = ok(move(client, picked, unit(conn, pmc, "Unit 9"), moved_on=last, rent_due_day=1))[
        "lease_id"
    ]

    def due_days(lease):
        return [(d["starts_on"], d["due_day"]) for d in lease_page(client, lease)["due_days"]]

    assert due_days(kept) == [(str(TODAY.replace(day=1)), 5)]
    assert due_days(picked) == [(str(later.replace(day=1)), 10)]
    assert due_days(on_first) == []


def unit(conn, pmc, name):
    return add_unit(conn, pmc.pmc_id, pmc.owners[1].property_id, name)


def test_a_move_says_why_it_is_refused(client, conn, staff, pmc, lease, there):
    own = conn.execute("SELECT unit_id FROM trust_leases WHERE id = %s", (lease,)).fetchone()[0]
    other = make_pmc(conn)
    theirs = add_unit(conn, other.pmc_id, other.owners[0].property_id)

    refused(move(client, lease, own), 422, "at its own unit it is renewed")
    refused(move(client, lease, theirs), 422, "no unit")
    refused(move(client, lease, there, deposit_agreed_on=None), 409, "agreed in writing")
    refused(
        move(client, lease, there, deposit_agreed_on=TODAY + timedelta(days=1)),
        422,
        "agreed on a day that has come",
    )
    refused(move(client, lease, there, ends_on=TODAY - timedelta(days=1)), 422, "ends on or after")
    refused(move(client, lease, there, moved_on=STARTED), 409, "not in effect")
    refused(move(client, uuid.uuid4(), there), 404, "no such lease")
    ok(move(client, lease, there))
    refused(
        move(client, lease, add_unit(conn, pmc.pmc_id, pmc.owners[1].property_id, "U8")),
        409,
        "moved already",
    )


def test_a_unit_with_a_lease_then_takes_no_move(client, conn, staff, pmc, lease, there):
    other_tenant = conn.execute(
        "INSERT INTO trust_tenants (pmc_id, property_id, display_name) VALUES (%s, %s, 'T9')"
        " RETURNING id",
        (pmc.pmc_id, pmc.owners[1].property_id),
    ).fetchone()[0]
    open_lease(conn, pmc.pmc_id, there, STARTED, None, "1100.00", [other_tenant])

    refused(move(client, lease, there), 409)


def test_the_old_lease_names_its_owners_account_when_it_has_two(
    client, conn, staff, pmc, lease, there
):
    second = open_account(
        conn,
        pmc.pmc_id,
        pmc.operating_bank_id,
        "owner_property",
        pmc.owners[1].owner_id,
        pmc.owners[0].property_id,
    )

    refused(move(client, lease, there), 422, "choose one")
    refused(move(client, lease, there, owner_account="nope"), 404, "no such owner account")
    assert ok(move(client, lease, there, owner_account=second))["prepaid_used"] == "0.00"


@pytest.mark.parametrize(
    "body",
    [
        {"monthly_rent": "0"},
        {"monthly_rent": "abc"},
        {"rent_due_day": 29},
        {"rent_due_day": 0},
        {"moved_on": "not a day"},
        {"unit_id": "not an id"},
        {"request_id": "not an id"},
    ],
)
def test_bad_input_is_refused(client, staff, lease, there, body):
    refused(move(client, lease, there, **body), 422)


def test_bad_ids_are_refused(client, staff):
    refused(move(client, "not-a-lease", uuid.uuid4()), 422)


@pytest.mark.parametrize("caller", ["nobody", "owner", "tenant"])
def test_only_staff_move_a_lease(client, conn, app_conn, pmc, lease, there, caller):
    if caller == "owner":
        join_as_owner(client, app_conn, pmc)
    elif caller == "tenant":
        join_as_tenant(client, conn, app_conn, pmc)

    refused(move(client, lease, there), 401 if caller == "nobody" else 403)
