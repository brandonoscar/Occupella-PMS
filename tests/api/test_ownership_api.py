"""A property owned in shares through the API (migration 20261019000094, the founder's review
q044): staff set each owner's percent from a day on the property's page; then a tenant's payment,
prepaid rent used, a deposit kept at move-out and a bill split by ownership go to the owners by
their shares, each to the cent. The database's side is in tests/test_ownership_shares.py. Every
name here is synthetic."""

import json
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from helpers import add_unit, balance, charge, co_own, open_lease, transfer_batch
from people import join_as_owner, join_as_staff, tenant_of

TODAY = datetime.now(UTC).date()
STARTED = TODAY - timedelta(days=200)


@pytest.fixture
def staff(client, conn, app_conn, pmc):
    return join_as_staff(client, conn, app_conn, pmc)


@pytest.fixture
def owners(conn, pmc):
    """Property 1's owners' accounts: Owner 1's, and Owner 2's as a co-owner."""
    return pmc.owners[0].account, co_own(conn, pmc)


@pytest.fixture
def lease(conn, pmc):
    unit = add_unit(conn, pmc.pmc_id, pmc.owners[0].property_id)
    return open_lease(conn, pmc.pmc_id, unit, STARTED, None, "1000.00", [tenant_of(conn, pmc)])


def post(client, path, **body):
    return client.post(path, json=json.loads(json.dumps(body, default=str)))


def ok(answer, status=201):
    assert answer.status_code == status, answer.text
    return answer.json()


def own(client, pmc, owners, first="60", second="40", starts_on=STARTED):
    return post(
        client,
        f"/api/setup/properties/{pmc.owners[0].property_id}/ownership",
        starts_on=starts_on,
        shares=[
            {"account": owners[0], "percent": first},
            {"account": owners[1], "percent": second},
        ],
    )


def pay(client, lease, amount, request_id=None, **more):
    return post(
        client,
        f"/api/rent/leases/{lease}/payments",
        request_id=request_id or uuid.uuid4(),
        amount=amount,
        received_on=TODAY,
        **more,
    )


# --- the shares --------------------------------------------------------------------------------


def test_staff_set_a_propertys_ownership_and_its_pages_show_it(client, staff, pmc, owners, lease):
    made = ok(own(client, pmc, owners, "62.5", "37.5"))

    page = ok(client.get(f"/api/properties/{pmc.owners[0].property_id}"), 200)
    assert [(o["account"], o["percent"]) for o in page["owners"]] == [
        (owners[0], "62.5"),
        (owners[1], "37.5"),
    ]
    (shares,) = page["ownership"]
    assert (shares["id"], shares["starts_on"], shares["recorded_by"]) == (
        made["id"],
        STARTED.isoformat(),
        "Staff 1",
    )
    assert [(s["owner"], s["percent"]) for s in shares["shares"]] == [
        ("Owner 1", "62.5"),
        ("Owner 2", "37.5"),
    ]
    accounts = {
        a["account"]: a["ownership_percent"] for a in ok(client.get("/api/setup"), 200)["accounts"]
    }
    assert (accounts[owners[0]], accounts[owners[1]], accounts[pmc.owners[1].account]) == (
        "62.5",
        "37.5",
        None,
    )
    lease_page = ok(client.get(f"/api/leases/{lease}"), 200)
    assert [(o["owner"], o["percent"]) for o in lease_page["owners"]] == [
        ("Owner 1", "62.5"),
        ("Owner 2", "37.5"),
    ]


def test_new_shares_from_a_later_day_take_over_from_it(client, staff, pmc, owners, lease):
    ok(own(client, pmc, owners, "60", "40", STARTED))
    ok(own(client, pmc, owners, "50", "50", TODAY + timedelta(days=30)))

    page = ok(client.get(f"/api/properties/{pmc.owners[0].property_id}"), 200)
    assert [s["starts_on"] for s in page["ownership"]] == [
        (TODAY + timedelta(days=30)).isoformat(),
        STARTED.isoformat(),
    ]
    assert [o["percent"] for o in page["owners"]] == ["60", "40"]  # today's


def test_setting_ownership_takes_staff(client, other_client, conn, app_conn, pmc, owners):
    assert own(client, pmc, owners).status_code == 401
    join_as_owner(other_client, app_conn, pmc)
    assert own(other_client, pmc, owners).status_code == 403


@pytest.mark.parametrize(
    ("first", "second", "said"),
    [
        ("60", "30", "the shares come to 90%, not 100%"),
        ("0", "100", None),
        ("60.00001", "39.99999", None),
    ],
)
def test_shares_come_to_100(client, staff, pmc, owners, first, second, said):
    answer = own(client, pmc, owners, first, second)
    assert answer.status_code == 422
    if said:
        assert answer.json()["detail"] == said


def test_each_share_is_an_owner_account_at_the_property_once(client, staff, pmc, owners):
    prop = pmc.owners[0].property_id
    twice = post(
        client,
        f"/api/setup/properties/{prop}/ownership",
        starts_on=STARTED,
        shares=[{"account": owners[0], "percent": "50"}, {"account": owners[0], "percent": "50"}],
    )
    assert twice.status_code == 422
    assert twice.json()["detail"] == "each owner's account is in the shares once"
    elsewhere = post(
        client,
        f"/api/setup/properties/{prop}/ownership",
        starts_on=STARTED,
        shares=[
            {"account": owners[0], "percent": "50"},
            {"account": pmc.owners[1].account, "percent": "50"},
        ],
    )
    assert elsewhere.status_code == 422
    assert "is not an owner's account at the property" in elsewhere.json()["detail"]
    unknown = post(
        client,
        f"/api/setup/properties/{uuid.uuid4()}/ownership",
        starts_on=STARTED,
        shares=[{"account": owners[0], "percent": "100"}],
    )
    assert unknown.status_code == 404


# --- what splits -------------------------------------------------------------------------------


def test_a_payment_pays_the_owners_by_their_shares(client, conn, staff, pmc, owners, lease):
    rent = charge(conn, pmc.pmc_id, lease, TODAY - timedelta(days=5), "1000.00", "rent", "Rent")
    ok(own(client, pmc, owners, "60", "40"))
    request = uuid.uuid4()

    done = ok(pay(client, lease, "1100.00", request))

    assert (done["to_owner"], done["to_prepaid"]) == ("1000.00", "100.00")
    assert (balance(conn, owners[0]), balance(conn, owners[1])) == (
        Decimal("600.00"),
        Decimal("400.00"),
    )
    assert conn.execute(
        "SELECT sum(amount) FROM trust_charge_payments WHERE charge_id = %s", (rent,)
    ).fetchone()[0] == Decimal("1000.00")
    # Sent again: the same payment, nothing new.
    again = ok(pay(client, lease, "1100.00", request))
    assert again == done
    assert balance(conn, owners[0]) == Decimal("600.00")
    # With shares in force staff name no account.
    named = pay(client, lease, "10.00", account=owners[0])
    assert named.status_code == 422
    assert named.json()["detail"] == (
        "the property's owners own it in shares: its payments split between them"
    )


def test_a_bounced_split_payment_takes_back_each_owners_part(
    client, conn, staff, pmc, owners, lease
):
    charge(conn, pmc.pmc_id, lease, TODAY - timedelta(days=5), "500.00", "rent", "Rent")
    ok(own(client, pmc, owners, "50", "50"))
    done = ok(pay(client, lease, "500.00"))

    ok(post(client, f"/api/rent/payments/{done['transfers'][0]}/bounce", on=TODAY))

    assert (balance(conn, owners[0]), balance(conn, owners[1])) == (Decimal(0), Decimal(0))
    assert ok(client.get(f"/api/leases/{lease}"), 200)["owed"] == "500.00"


def test_prepaid_rent_used_pays_the_owners_by_their_shares(client, conn, staff, pmc, owners, lease):
    transfer_batch(conn, [(pmc.operating_cash, pmc.prepaid_rent, "300.00")])
    charge(conn, pmc.pmc_id, lease, TODAY - timedelta(days=5), "250.00", "rent", "Rent")
    ok(own(client, pmc, owners, "60", "40"))

    done = ok(
        post(client, f"/api/rent/leases/{lease}/use-prepaid", request_id=uuid.uuid4(), on=TODAY)
    )

    assert done["to_owner"] == "250.00"
    assert (
        balance(conn, owners[0]),
        balance(conn, owners[1]),
        balance(conn, pmc.prepaid_rent),
    ) == (
        Decimal("150.00"),
        Decimal("100.00"),
        Decimal("50.00"),
    )


def test_without_shares_staff_choose_one_of_several_owners(client, conn, staff, pmc, owners, lease):
    charge(conn, pmc.pmc_id, lease, TODAY - timedelta(days=5), "400.00", "rent", "Rent")
    unchosen = pay(client, lease, "400.00")
    assert unchosen.status_code == 422
    assert unchosen.json()["detail"] == "the property has more than one owner account; choose one"
    assert [o["percent"] for o in ok(client.get(f"/api/leases/{lease}"), 200)["owners"]] == [
        None,
        None,
    ]

    ok(pay(client, lease, "400.00", account=owners[1]))
    assert (balance(conn, owners[0]), balance(conn, owners[1])) == (Decimal(0), Decimal("400.00"))


def test_a_deposit_kept_at_move_out_goes_to_the_owners_by_their_shares(
    client, conn, staff, pmc, owners
):
    unit = add_unit(conn, pmc.pmc_id, pmc.owners[0].property_id)
    ended = open_lease(
        conn,
        pmc.pmc_id,
        unit,
        STARTED,
        TODAY - timedelta(days=10),
        "1000.00",
        [tenant_of(conn, pmc)],
    )
    transfer_batch(conn, [(pmc.deposit_cash, pmc.tenant_deposit, "1000.00")])
    charge(conn, pmc.pmc_id, ended, TODAY - timedelta(days=40), "800.00", "rent", "Rent")
    ok(own(client, pmc, owners, "75", "25"))
    listed = next(m for m in ok(client.get("/api/move-out"), 200) if m["lease_id"] == str(ended))
    assert [o["percent"] for o in listed["owner_accounts"]] == ["75", "25"]

    done = ok(post(client, f"/api/move-out/{ended}/return", request_id=uuid.uuid4(), on=TODAY))

    assert done["returned"] == "200.00"
    assert (balance(conn, owners[0]), balance(conn, owners[1])) == (
        Decimal("600.00"),
        Decimal("200.00"),
    )


def test_a_bill_split_by_ownership_is_a_bill_for_each_owners_share(
    client, conn, staff, pmc, owners
):
    ok(own(client, pmc, owners, "60", "40"))

    made = ok(
        post(
            client,
            "/api/payables/shared-bills",
            vendor_id=pmc.vendor_id,
            reference="ROOF-9",
            bill_date=TODAY,
            due_on=TODAY + timedelta(days=30),
            memo="Roof repair",
            by_ownership={"account": owners[1], "amount": "1000.01"},
        )
    )

    bills = conn.execute(
        "SELECT ledger_account_id, reference, amount FROM trust_bills WHERE id = ANY(%s)"
        " ORDER BY reference",
        (made["bills"],),
    ).fetchall()
    first, second = sorted(owners)
    assert bills == [
        (first, "ROOF-9/1", Decimal("600.01") if first == owners[0] else Decimal("400.00")),
        (second, "ROOF-9/2", Decimal("400.00") if second == owners[1] else Decimal("600.01")),
    ]


def test_a_bill_split_by_ownership_needs_shares_between_owners(client, staff, pmc, owners):
    body = {
        "vendor_id": pmc.vendor_id,
        "reference": "ROOF-10",
        "bill_date": TODAY,
        "due_on": TODAY,
        "memo": "Roof repair",
    }
    split = {"account": owners[0], "amount": "100.00"}
    none_yet = post(client, "/api/payables/shared-bills", **body, by_ownership=split)
    assert none_yet.status_code == 422
    assert none_yet.json()["detail"] == (
        "the property has no shares between two owners or more on the bill's date"
    )
    both = post(
        client,
        "/api/payables/shared-bills",
        **body,
        by_ownership=split,
        shares=[
            {"account": owners[0], "amount": "50.00"},
            {"account": owners[1], "amount": "50.00"},
        ],
    )
    assert both.status_code == 422
    assert both.json()["detail"] == "give a shared bill its shares, or split it by ownership"
    neither = post(client, "/api/payables/shared-bills", **body)
    assert neither.status_code == 422
    unknown = post(
        client,
        "/api/payables/shared-bills",
        **body,
        by_ownership={"account": "pgla_none", "amount": "100.00"},
    )
    assert unknown.status_code == 404
