"""Staff's rent work through the API (src/pms/api/rent.py): the rent run and late fees, fees
and credits once or every month, tenants' payments matched to what a lease owes (the rest held
as prepaid rent), prepaid rent used, payments that bounced, and security deposits."""

import json
import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from helpers import (
    add_late_fee_policy,
    add_nsf_fee_policy,
    add_unit,
    approve,
    balance,
    bank_sums,
    charge,
    draw_owner,
    hold_payments,
    open_account,
    open_lease,
)
from people import join_as_owner, join_as_staff, join_as_tenant

TODAY = datetime.now(UTC).date()
DAY = timedelta(days=1)
START = TODAY - timedelta(days=90)


class Home:
    """Tenant 1 on a lease of Unit 1 at Property 1 since START, at $1500.00 a month."""

    def __init__(self, conn, pmc):
        self.pmc = pmc
        pmc_id = pmc.pmc_id
        self.tenant = conn.execute(
            "SELECT tenant_id FROM trust_ledger_accounts WHERE ledger_account_id = %s",
            (pmc.prepaid_rent,),
        ).fetchone()[0]
        self.unit = add_unit(conn, pmc_id, pmc.owners[0].property_id)
        self.lease = open_lease(conn, pmc_id, self.unit, START, None, "1500.00", [self.tenant])
        self.owner = pmc.owners[0].account

    def charge(self, conn, due_on, amount="1500.00", kind="rent"):
        return charge(conn, self.pmc.pmc_id, self.lease, due_on, amount, kind)


@pytest.fixture
def home(conn, pmc):
    return Home(conn, pmc)


@pytest.fixture
def staff(client, conn, app_conn, pmc):
    return join_as_staff(client, conn, app_conn, pmc)


def call(client, path, **body):
    return client.post(f"/api/rent/{path}", json=json.loads(json.dumps(body, default=str)))


def pay(client, home, amount, received_on=TODAY, request_id=None, **more):
    return call(
        client,
        f"leases/{home.lease}/payments",
        request_id=request_id or uuid.uuid4(),
        amount=amount,
        received_on=received_on,
        **more,
    )


def ok(answer, status=201):
    assert answer.status_code == status, answer.text
    return answer.json()


def refused(answer, status, detail=None):
    assert answer.status_code == status, answer.text
    if detail is not None:
        assert answer.json()["detail"] == detail


def lease_row(client, home):
    return ok(client.get(f"/api/leases/{home.lease}"), 200)


def test_the_rent_run_charges_each_lease_once_a_due_date(client, staff, home):
    first = ok(call(client, "charge-rent", on=TODAY), 200)
    again = ok(call(client, "charge-rent", on=TODAY), 200)
    row = lease_row(client, home)

    assert (first, again) == ({"charged": 1, "recurring": 0}, {"charged": 0, "recurring": 0})
    assert row["owed"] == "1500.00"
    assert [(c["due_on"], c["kind"], c["open"]) for c in row["open_charges"]] == [
        (str(TODAY), "rent", "1500.00")
    ]


def test_the_rent_run_charges_a_lease_starting_later_that_month_its_part_month(
    client, conn, staff, home, pmc
):
    unit = add_unit(conn, pmc.pmc_id, pmc.owners[0].property_id, "Unit 2")
    lease = open_lease(
        conn, pmc.pmc_id, unit, date(2026, 3, 15), date(2026, 4, 30), "1500.00", [home.tenant]
    )

    run = ok(call(client, "charge-rent", on=date(2026, 3, 1)), 200)
    row = ok(client.get(f"/api/leases/{lease}"), 200)

    assert run == {"charged": 1, "recurring": 0}
    assert row["owed"] == "822.58"  # 1500.00 x 17 / 31
    assert [(c["due_on"], c["memo"], c["open"]) for c in row["open_charges"]] == [
        (
            "2026-03-15",
            "Rent due 2026-03-15, 2026-03-15 to 2026-03-31 (17 of 31 days)",
            "822.58",
        )
    ]


def recur(client, home, amount="35.00", memo="Pet rent", starts_on=START, kind="fee"):
    return call(
        client,
        f"leases/{home.lease}/recurring-charges",
        kind=kind,
        amount=amount,
        memo=memo,
        starts_on=starts_on,
    )


def test_the_rent_run_charges_a_lease_starting_later_that_month_its_part_month_of_recurring(
    client, conn, staff, home, pmc
):
    unit = add_unit(conn, pmc.pmc_id, pmc.owners[0].property_id, "Unit 2")
    lease = open_lease(
        conn, pmc.pmc_id, unit, date(2026, 3, 15), date(2026, 4, 30), "1500.00", [home.tenant]
    )
    ok(
        call(
            client,
            f"leases/{lease}/recurring-charges",
            kind="fee",
            amount="35.00",
            memo="Pet rent",
            starts_on=date(2026, 3, 15),
        )
    )

    run = ok(call(client, "charge-rent", on=date(2026, 3, 1)), 200)
    row = ok(client.get(f"/api/leases/{lease}"), 200)

    assert run == {"charged": 1, "recurring": 1}
    assert row["owed"] == "841.77"  # 1500.00 and 35.00, each x 17 / 31
    assert sorted((c["due_on"], c["memo"], c["open"]) for c in row["open_charges"]) == [
        (
            "2026-03-15",
            "Pet rent due 2026-03-15, 2026-03-15 to 2026-03-31 (17 of 31 days)",
            "19.19",
        ),
        (
            "2026-03-15",
            "Rent due 2026-03-15, 2026-03-15 to 2026-03-31 (17 of 31 days)",
            "822.58",
        ),
    ]


def test_the_rent_run_charges_a_recurring_charge_with_the_rent_until_it_stops(client, staff, home):
    pet_rent = ok(recur(client, home))["id"]
    ok(recur(client, home, "100.00", "Concession", kind="credit"))

    first = ok(call(client, "charge-rent", on=TODAY), 200)
    again = ok(call(client, "charge-rent", on=TODAY), 200)
    row = lease_row(client, home)

    assert (first, again) == ({"charged": 1, "recurring": 2}, {"charged": 0, "recurring": 0})
    assert row["owed"] == "1435.00"
    assert [(r["kind"], r["memo"], r["amount"], r["ends_on"]) for r in row["recurring"]] == [
        ("credit", "Concession", "100.00", None),
        ("fee", "Pet rent", "35.00", None),
    ]
    # Charged in one run, so the same day and time: which comes first is the ids' order, and
    # the concession comes off the first (migration 0049), closing the pet rent if it is first.
    assert [(c["kind"], c["memo"], c["open"]) for c in row["open_charges"]] in (
        [("rent", f"Rent due {TODAY}", "1400.00"), ("fee", f"Pet rent due {TODAY}", "35.00")],
        [("rent", f"Rent due {TODAY}", "1435.00")],
    )

    ok(call(client, f"recurring-charges/{pet_rent}/stop", ends_on=TODAY + DAY))
    assert [r["ends_on"] for r in lease_row(client, home)["recurring"]] == [
        None,
        str(TODAY + DAY),
    ]
    assert ok(call(client, "charge-rent", on=TODAY + DAY), 200) == {"charged": 1, "recurring": 1}
    refused(
        call(client, f"recurring-charges/{pet_rent}/stop", ends_on=TODAY),
        409,
        "this recurring charge has stopped already",
    )


def test_a_stopped_recurring_charge_is_no_longer_listed(client, staff, home):
    pet_rent = ok(recur(client, home))["id"]

    ok(call(client, f"recurring-charges/{pet_rent}/stop", ends_on=TODAY))

    assert lease_row(client, home)["recurring"] == []
    assert ok(call(client, "charge-rent", on=TODAY), 200) == {"charged": 1, "recurring": 0}


def test_a_recurring_charge_names_the_staff_member_in_the_audit_log(client, conn, staff, home):
    made = ok(recur(client, home))

    assert conn.execute(
        "SELECT staff_id FROM trust_audit_log WHERE table_name = 'trust_recurring_charges'"
        " AND row_data ->> 'id' = %s",
        (made["id"],),
    ).fetchone() == (staff.person_id,)


def test_a_payment_pays_what_is_owed_oldest_first_and_holds_the_rest_as_prepaid_rent(
    client, conn, staff, home, pmc
):
    october = home.charge(conn, START)
    fee = home.charge(conn, START + 5 * DAY, "50.00", "fee")
    november = home.charge(conn, START + 31 * DAY)

    first = ok(pay(client, home, "2000.00", reference="Check 101"))
    second = ok(pay(client, home, "1200.00"))
    row = lease_row(client, home)

    assert (first["to_owner"], first["to_prepaid"]) == ("2000.00", "0.00")
    assert (second["to_owner"], second["to_prepaid"]) == ("1050.00", "150.00")
    paid = dict(
        conn.execute(
            "SELECT charge_id, sum(amount) FROM trust_charge_payments"
            " WHERE charge_id = ANY(%s) GROUP BY charge_id",
            ([october, fee, november],),
        ).fetchall()
    )
    assert paid == {october: Decimal(1500), fee: Decimal(50), november: Decimal(1500)}
    assert row["owed"] == "0.00" and row["open_charges"] == []
    assert row["tenants"][0]["prepaid"] == "150.00"
    assert balance(conn, home.owner) == Decimal("3050.00")
    assert set(bank_sums(conn, pmc.pmc_id).values()) == {Decimal(0)}
    (latest, earliest) = row["payments"]
    assert (earliest["amount"], earliest["reference"], earliest["bounced"]) == (
        "2000.00",
        "Check 101",
        False,
    )
    # The whole $1,200.00 that came in, not only the $1,050.00 that paid charges.
    assert latest["amount"] == "1200.00"


def test_a_payment_all_of_it_prepaid_rent_is_listed_and_bounces(client, conn, staff, home):
    # Nothing is owed: the whole payment is the tenant's prepaid rent, matched to no charge.
    payment = ok(pay(client, home, "300.00", reference="Money order 7"))
    assert (payment["to_owner"], payment["to_prepaid"]) == ("0.00", "300.00")
    (listed,) = lease_row(client, home)["payments"]
    assert (listed["amount"], listed["reference"], listed["bounced"]) == (
        "300.00",
        "Money order 7",
        False,
    )

    ok(call(client, f"payments/{listed['transfer_id']}/bounce", on=TODAY))

    row = lease_row(client, home)
    (listed,) = row["payments"]
    assert listed["bounced"] is True
    assert row["tenants"][0]["prepaid"] == "0.00"
    assert set(bank_sums(conn, home.pmc.pmc_id).values()) == {Decimal(0)}


def test_a_credit_comes_off_what_is_owed(client, conn, staff, home):
    home.charge(conn, START)
    ok(
        call(
            client,
            f"leases/{home.lease}/charges",
            kind="credit",
            amount="100.00",
            due_on=START,
            memo="Leak repair",
        )
    )

    shown = lease_row(client, home)["open_charges"]
    paid = ok(pay(client, home, "1500.00"))
    row = lease_row(client, home)

    # The credit comes off the oldest open charge (migration 0049), so the rent shows 1400.00
    # open, and once that is paid, nothing.
    assert [(c["kind"], c["open"]) for c in shown] == [("rent", "1400.00")]
    assert (paid["to_owner"], paid["to_prepaid"]) == ("1400.00", "100.00")
    assert (row["owed"], row["open_charges"]) == ("0.00", [])


def test_sending_a_payment_again_records_it_once(client, conn, staff, home):
    home.charge(conn, START)
    request = uuid.uuid4()

    first = ok(pay(client, home, "1600.00", request_id=request))
    again = ok(pay(client, home, "1600.00", request_id=request))
    other = pay(client, home, "1700.00", request_id=request)

    assert again == first
    refused(other, 409, "this request id was used for a different payment")
    assert balance(conn, home.owner) == Decimal("1500.00")
    assert lease_row(client, home)["tenants"][0]["prepaid"] == "100.00"


def test_prepaid_rent_pays_the_next_rent(client, conn, staff, home):
    ok(pay(client, home, "300.00"))
    home.charge(conn, START + 30 * DAY, "250.00")

    used = ok(call(client, f"leases/{home.lease}/use-prepaid", request_id=uuid.uuid4(), on=TODAY))
    nothing = call(client, f"leases/{home.lease}/use-prepaid", request_id=uuid.uuid4(), on=TODAY)
    row = lease_row(client, home)

    assert used["to_owner"] == "250.00"
    refused(nothing, 409, "there is no prepaid rent to use, or nothing is owed")
    assert (row["owed"], row["tenants"][0]["prepaid"]) == ("0.00", "50.00")


def test_a_bounced_payment_is_owed_again_once(client, conn, staff, home, pmc):
    home.charge(conn, START)
    paid = ok(pay(client, home, "1600.00", received_on=TODAY - 2 * DAY))
    payment = paid["transfers"][0]

    early = call(client, f"payments/{payment}/bounce", on=TODAY - 3 * DAY)
    returned = ok(call(client, f"payments/{payment}/bounce", on=TODAY))
    again = call(client, f"payments/{payment}/bounce", on=TODAY - DAY)
    row = lease_row(client, home)

    refused(early, 422, "a payment bounces on or after the day it was received")
    assert len(returned["transfers"]) == 2  # the owner's part and the prepaid part
    refused(again, 409, "this payment bounced already")
    assert row["owed"] == "1500.00" and row["tenants"][0]["prepaid"] == "0.00"
    assert row["payments"][0]["bounced"] is True
    assert returned["nsf_fee"] is None  # the property charges no NSF fee
    assert balance(conn, home.owner) == Decimal(0)
    assert set(bank_sums(conn, pmc.pmc_id).values()) == {Decimal(0)}


def test_a_bounce_charges_the_propertys_nsf_fee_in_force_that_day(client, conn, staff, home, pmc):
    prop = pmc.owners[0].property_id
    add_nsf_fee_policy(conn, pmc.pmc_id, prop, START, "25.00")
    add_nsf_fee_policy(conn, pmc.pmc_id, prop, TODAY - DAY, "35.00")
    home.charge(conn, START)
    payment = ok(pay(client, home, "1500.00", received_on=TODAY - 3 * DAY))["transfers"][0]

    returned = ok(call(client, f"payments/{payment}/bounce", on=TODAY))
    row = lease_row(client, home)

    assert returned["nsf_fee"] == "35.00"
    assert row["owed"] == "1535.00"
    assert [(c["kind"], c["memo"], c["open"]) for c in row["open_charges"]][-1] == (
        "fee",
        f"NSF fee, payment returned {TODAY}",
        "35.00",
    )


def test_a_payment_the_owner_was_paid_out_of_cannot_bounce_into_the_negative(
    client, conn, staff, home
):
    home.charge(conn, START)
    payment = ok(pay(client, home, "1500.00", received_on=TODAY - DAY))["transfers"][0]
    draw_owner(conn, home.pmc.pmc_id, home.owner, "draw-1", None, datetime.now(UTC))

    answer = call(client, f"payments/{payment}/bounce", on=TODAY)

    refused(answer, 409, "not enough money: the owner's account would be $1,500.00 short")
    assert lease_row(client, home)["owed"] == "0.00"


def test_only_a_tenant_payment_bounces(client, staff, home):
    refused(call(client, "payments/not-a-transfer/bounce", on=TODAY), 404, "no such tenant payment")


def test_a_payment_hold_refuses_payments_and_records_nothing(client, conn, staff, home):
    home.charge(conn, START)
    hold_payments(conn, home.pmc.pmc_id, home.lease, START)

    answer = pay(client, home, "1500.00")

    refused(answer, 409)
    assert "payment hold" in answer.json()["detail"]
    assert lease_row(client, home)["on_hold"] is True
    assert balance(conn, home.owner) == Decimal(0)


def test_a_closed_period_refuses_a_payment_dated_in_it(client, conn, staff, home, pmc):
    home.charge(conn, START)
    approve(
        conn,
        pmc.pmc_id,
        pmc.operating_bank_id,
        datetime.combine(START, datetime.min.time(), UTC),
        datetime.combine(TODAY - 10 * DAY, datetime.min.time(), UTC),
    )

    answer = pay(client, home, "1500.00", received_on=TODAY - 20 * DAY)

    refused(answer, 409)
    assert "closed" in answer.json()["detail"]


def test_a_property_with_two_owner_accounts_needs_one_chosen(client, conn, staff, home, pmc):
    second = open_account(
        conn,
        pmc.pmc_id,
        pmc.operating_bank_id,
        "owner_property",
        pmc.owners[1].owner_id,
        pmc.owners[0].property_id,
    )
    home.charge(conn, START)

    unchosen = pay(client, home, "100.00")
    unknown = pay(client, home, "100.00", account=pmc.owners[1].account)
    chosen = ok(pay(client, home, "100.00", account=second))

    refused(unchosen, 422, "the property has more than one owner account; choose one")
    refused(unknown, 404, "no such owner account for this lease's property")
    assert chosen["to_owner"] == "100.00"
    assert balance(conn, second) == Decimal(100)


def test_a_property_without_an_owner_account_takes_no_rent(client, conn, staff, pmc):
    prop = conn.execute(
        "INSERT INTO trust_properties (pmc_id, display_name) VALUES (%s, 'Bare') RETURNING id",
        (pmc.pmc_id,),
    ).fetchone()[0]
    tenant = conn.execute(
        "INSERT INTO trust_tenants (pmc_id, property_id, display_name) VALUES (%s, %s, 'T')"
        " RETURNING id",
        (pmc.pmc_id, prop),
    ).fetchone()[0]
    unit = add_unit(conn, pmc.pmc_id, prop)
    lease = open_lease(conn, pmc.pmc_id, unit, START, None, "100.00", [tenant])

    answer = call(
        client,
        f"leases/{lease}/payments",
        request_id=uuid.uuid4(),
        amount="100.00",
        received_on=TODAY,
    )

    refused(answer, 409, "the lease's property has no owner account to pay rent into")


def test_a_security_deposit_is_held_for_the_tenant_once(client, conn, staff, home, pmc):
    request = uuid.uuid4()
    body = {
        "request_id": request,
        "tenant_id": home.tenant,
        "amount": "1500.00",
        "received_on": TODAY,
        "reference": "Money order 7",
    }

    first = ok(call(client, "deposits", **body))
    again = ok(call(client, "deposits", **body))
    other = call(client, "deposits", **(body | {"amount": "1600.00"}))

    assert again == first
    refused(other, 409, "this request id was used for a different deposit")
    assert balance(conn, pmc.tenant_deposit) == Decimal(1500)
    assert lease_row(client, home)["tenants"][0]["deposit"] == "1500.00"


def test_a_deposit_needs_a_deposit_account_and_one_chosen_among_several(
    client, conn, staff, home, pmc
):
    other = conn.execute(
        "INSERT INTO trust_bank_accounts (pmc_id, kind, display_name)"
        " VALUES (%s, 'security_deposit', 'Second deposit trust') RETURNING id",
        (pmc.pmc_id,),
    ).fetchone()[0]
    open_account(conn, pmc.pmc_id, other, "bank_cash")
    body = {"tenant_id": home.tenant, "amount": "100.00", "received_on": TODAY}

    unchosen = call(client, "deposits", request_id=uuid.uuid4(), **body)
    chosen = ok(call(client, "deposits", request_id=uuid.uuid4(), bank_account_id=other, **body))
    elsewhere = call(
        client, "deposits", request_id=uuid.uuid4(), bank_account_id=pmc.operating_bank_id, **body
    )
    unknown = call(
        client, "deposits", request_id=uuid.uuid4(), **(body | {"tenant_id": uuid.uuid4()})
    )

    refused(unchosen, 422, "the PMC has more than one security deposit account; choose one")
    assert len(chosen["transfers"]) == 1
    refused(elsewhere, 409, "set up a security deposit trust bank account first")
    refused(unknown, 404, "no such tenant")


def test_late_fees_are_assessed_up_to_today(client, conn, staff, home, pmc):
    home.charge(conn, START)
    add_late_fee_policy(conn, pmc.pmc_id, pmc.owners[0].property_id, START, 5, "50.00")

    future = call(client, "late-fees", on=TODAY + DAY)
    assessed = ok(call(client, "late-fees", on=TODAY), 200)

    refused(future, 422, "late fees are assessed as of today or an earlier day")
    assert assessed == {"charged": 1}
    assert lease_row(client, home)["owed"] == "1550.00"


def test_a_charge_names_the_staff_member_in_the_audit_log(client, conn, staff, home):
    made = ok(
        call(
            client,
            f"leases/{home.lease}/charges",
            kind="fee",
            amount="25.00",
            due_on=TODAY,
            memo="Key copy",
        )
    )

    assert conn.execute(
        "SELECT staff_id FROM trust_audit_log WHERE table_name = 'trust_charges'"
        " AND row_data ->> 'id' = %s",
        (made["id"],),
    ).fetchone() == (staff.person_id,)


def test_another_pmcs_lease_is_not_found(client, conn, staff, home, other_pmc_lease):
    for answer in (
        pay(client, other_pmc_lease, "10.00"),
        call(
            client, f"leases/{other_pmc_lease.lease}/use-prepaid", request_id=uuid.uuid4(), on=TODAY
        ),
        call(
            client,
            f"leases/{other_pmc_lease.lease}/charges",
            kind="fee",
            amount="1.00",
            due_on=TODAY,
            memo="x",
        ),
        recur(client, other_pmc_lease),
    ):
        refused(answer, 404, "no such lease")


def test_another_pmcs_recurring_charge_is_not_found(client, conn, staff, other_pmc_lease):
    from helpers import add_recurring_charge

    theirs = add_recurring_charge(
        conn, other_pmc_lease.pmc.pmc_id, other_pmc_lease.lease, "35.00", START
    )

    refused(
        call(client, f"recurring-charges/{theirs}/stop", ends_on=TODAY),
        404,
        "no such recurring charge",
    )
    assert conn.execute(
        "SELECT count(*) FROM trust_recurring_charge_ends WHERE recurring_charge_id = %s",
        (theirs,),
    ).fetchone() == (0,)


@pytest.fixture
def other_pmc_lease(conn):
    from helpers import make_pmc

    return Home(conn, make_pmc(conn))


ENDPOINTS = [
    ("post", "/api/rent/charge-rent"),
    ("post", "/api/rent/late-fees"),
    ("post", f"/api/rent/leases/{uuid.uuid4()}/charges"),
    ("post", f"/api/rent/leases/{uuid.uuid4()}/recurring-charges"),
    ("post", f"/api/rent/recurring-charges/{uuid.uuid4()}/stop"),
    ("post", f"/api/rent/leases/{uuid.uuid4()}/payments"),
    ("post", f"/api/rent/leases/{uuid.uuid4()}/use-prepaid"),
    ("post", "/api/rent/deposits"),
    ("post", "/api/rent/payments/x/bounce"),
]


@pytest.mark.parametrize("caller", ["nobody", "owner", "tenant"])
@pytest.mark.parametrize(("method", "path"), ENDPOINTS)
def test_only_staff_do_rent_work(client, conn, app_conn, pmc, caller, method, path):
    if caller == "owner":
        join_as_owner(client, app_conn, pmc)
    elif caller == "tenant":
        join_as_tenant(client, conn, app_conn, pmc)

    answer = client.get(path) if method == "get" else client.post(path, json={})

    assert answer.status_code == (401 if caller == "nobody" else 403)


@pytest.mark.parametrize(
    ("path", "body"),
    [
        ("payments", {"amount": "10.00", "received_on": str(TODAY)}),  # no request id
        ("payments", {"request_id": "x", "amount": "10.00", "received_on": str(TODAY)}),
        ("payments", {"request_id": str(uuid.uuid4()), "amount": "0", "received_on": str(TODAY)}),
        (
            "payments",
            {"request_id": str(uuid.uuid4()), "amount": "1.001", "received_on": str(TODAY)},
        ),
        (
            "payments",
            {"request_id": str(uuid.uuid4()), "amount": "1000000.01", "received_on": str(TODAY)},
        ),
        ("charges", {"kind": "rent", "amount": "1.00", "due_on": str(TODAY), "memo": "x"}),
        ("charges", {"kind": "fee", "amount": "1.00", "due_on": str(TODAY), "memo": " "}),
        (
            "recurring-charges",
            {"kind": "rent", "amount": "1.00", "starts_on": str(TODAY), "memo": "x"},
        ),
        (
            "recurring-charges",
            {"kind": "fee", "amount": "0", "starts_on": str(TODAY), "memo": "x"},
        ),
        (
            "recurring-charges",
            {"kind": "fee", "amount": "1.00", "starts_on": str(TODAY), "memo": " "},
        ),
        ("recurring-charges", {"kind": "fee", "amount": "1.00", "memo": "x"}),  # no first day
        ("use-prepaid", {"request_id": str(uuid.uuid4())}),
    ],
)
def test_bad_input_is_refused(client, staff, home, path, body):
    refused(client.post(f"/api/rent/leases/{home.lease}/{path}", json=body), 422)


@pytest.mark.parametrize("body", [{}, {"ends_on": "someday"}])
def test_a_stop_needs_its_day(client, staff, home, body):
    pet_rent = ok(recur(client, home))["id"]

    refused(client.post(f"/api/rent/recurring-charges/{pet_rent}/stop", json=body), 422)


@pytest.mark.parametrize("path", ["payments", "use-prepaid"])
def test_money_is_recorded_no_later_than_today(client, staff, home, path):
    tomorrow = TODAY + DAY
    body = {
        "request_id": str(uuid.uuid4()),
        "amount": "10.00",
        "received_on": str(tomorrow),
        "on": str(tomorrow),
    }

    refused(
        client.post(f"/api/rent/leases/{home.lease}/{path}", json=body),
        422,
        "money is recorded on the day it arrived, not a later one",
    )


def test_a_deposit_or_bounce_is_recorded_no_later_than_today(client, staff, home):
    tomorrow = str(TODAY + DAY)
    for answer in (
        call(
            client,
            "deposits",
            request_id=uuid.uuid4(),
            tenant_id=home.tenant,
            amount="1.00",
            received_on=tomorrow,
        ),
        call(client, "payments/x/bounce", on=tomorrow),
    ):
        refused(answer, 422, "money is recorded on the day it arrived, not a later one")


def test_a_payment_smaller_than_what_is_owed_pays_the_oldest_charge_first(
    client, conn, staff, home
):
    older = home.charge(conn, START)
    newer = home.charge(conn, START + 30 * DAY)

    ok(pay(client, home, "400.00"))

    assert dict(
        conn.execute(
            "SELECT charge_id, amount FROM trust_charge_payments WHERE charge_id = ANY(%s)",
            ([older, newer],),
        ).fetchall()
    ) == {older: Decimal(400)}
    assert [c["open"] for c in lease_row(client, home)["open_charges"]] == ["1100.00", "1500.00"]


def test_using_prepaid_rent_again_with_the_same_request_uses_it_once(client, conn, staff, home):
    ok(pay(client, home, "300.00"))
    home.charge(conn, START + 30 * DAY, "250.00")
    request = uuid.uuid4()

    first = ok(call(client, f"leases/{home.lease}/use-prepaid", request_id=request, on=TODAY))
    again = ok(call(client, f"leases/{home.lease}/use-prepaid", request_id=request, on=TODAY))

    home.charge(conn, START + 31 * DAY, "40.00", "fee")
    after_more_owed = ok(
        call(client, f"leases/{home.lease}/use-prepaid", request_id=request, on=TODAY)
    )

    assert again == first == after_more_owed
    assert lease_row(client, home)["tenants"][0]["prepaid"] == "50.00"


def test_a_payment_hold_refuses_prepaid_rent_too(client, conn, staff, home):
    ok(pay(client, home, "300.00"))
    home.charge(conn, START + 30 * DAY, "250.00")
    hold_payments(conn, home.pmc.pmc_id, home.lease, START)

    answer = call(client, f"leases/{home.lease}/use-prepaid", request_id=uuid.uuid4(), on=TODAY)

    refused(answer, 409)
    assert "payment hold" in answer.json()["detail"]


def test_a_deposit_dated_in_a_closed_period_is_refused(client, conn, staff, home, pmc):
    approve(
        conn,
        pmc.pmc_id,
        pmc.deposit_bank_id,
        datetime.combine(START, datetime.min.time(), UTC),
        datetime.combine(TODAY - 10 * DAY, datetime.min.time(), UTC),
    )

    answer = call(
        client,
        "deposits",
        request_id=uuid.uuid4(),
        tenant_id=home.tenant,
        amount="100.00",
        received_on=TODAY - 20 * DAY,
    )

    refused(answer, 409)
    assert "closed" in answer.json()["detail"]


def test_a_bank_account_without_its_cash_account_takes_no_money(client, conn, staff, pmc):
    bank = conn.execute(
        "INSERT INTO trust_bank_accounts (pmc_id, kind, display_name)"
        " VALUES (%s, 'operating', 'Half set up') RETURNING id",
        (pmc.pmc_id,),
    ).fetchone()[0]
    prop = conn.execute(
        "INSERT INTO trust_properties (pmc_id, display_name) VALUES (%s, 'P') RETURNING id",
        (pmc.pmc_id,),
    ).fetchone()[0]
    open_account(conn, pmc.pmc_id, bank, "owner_property", pmc.owners[0].owner_id, prop)
    tenant = conn.execute(
        "INSERT INTO trust_tenants (pmc_id, property_id, display_name) VALUES (%s, %s, 'T')"
        " RETURNING id",
        (pmc.pmc_id, prop),
    ).fetchone()[0]
    lease = open_lease(
        conn, pmc.pmc_id, add_unit(conn, pmc.pmc_id, prop), START, None, "1.00", [tenant]
    )

    answer = call(
        client,
        f"leases/{lease}/payments",
        request_id=uuid.uuid4(),
        amount="1.00",
        received_on=TODAY,
    )

    refused(answer, 409, "the trust bank account has no cash account; set it up again")


def test_open_charges_are_the_pmcs_own(app_conn, conn, home, other_pmc_lease):
    """Narrowed by the PMC first, so another PMC's lease reads as having none."""
    from pms.api import rent

    home.charge(conn, START)
    other_pmc_lease.charge(conn, START)

    assert [row[0] for row in rent.charges_open(app_conn, home.pmc.pmc_id)] == [home.lease]
    assert rent.charges_open(app_conn, home.pmc.pmc_id, other_pmc_lease.lease) == []
    assert len(rent.charges_open(app_conn, home.pmc.pmc_id, home.lease)) == 1


@pytest.mark.parametrize(
    ("open_", "amount", "matched"),
    [
        ([("a", "100"), ("b", "50")], "120", [("a", "100"), ("b", "20")]),
        ([("a", "100"), ("b", "50")], "100", [("a", "100")]),
        ([("a", "100"), ("b", "50")], "500", [("a", "100"), ("b", "50")]),
        ([], "10", []),
    ],
)
def test_allocation_fills_the_oldest_charge_first(open_, amount, matched):
    from pms.api import rent

    charges = [(c, Decimal(v)) for c, v in open_]
    assert rent.allocate(charges, Decimal(amount)) == [(c, Decimal(v)) for c, v in matched]
