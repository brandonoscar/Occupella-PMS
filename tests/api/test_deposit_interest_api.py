"""Interest on a lease's security deposits through the API (src/pms/api/deposit_interest.py,
migration 0122; the founder's review q041): a property's rate from a day, set in its setup; what
the lease's deposits earned since the last payment; and paying it as a rent credit or added to the
deposits, once per request id, refused in words. Every name is synthetic."""

import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from helpers import (
    add_tenant,
    add_unit,
    balance,
    make_pmc,
    open_lease,
    transfer_batch,
)
from people import join_as_owner, join_as_staff, join_as_tenant, tenant_of

TODAY = datetime.now(UTC).date()
START = TODAY - timedelta(days=365)


def noon(day: date) -> datetime:
    return datetime(day.year, day.month, day.day, 12, tzinfo=UTC)


@pytest.fixture
def staff(client, conn, app_conn, pmc):
    return join_as_staff(client, conn, app_conn, pmc, name="Manager 1")


@pytest.fixture
def lease(conn, pmc):
    """Tenant 1's month-to-month lease of Unit 1 at Property 1 from a year ago, with 1000.00 of
    deposit since its first day."""
    unit = add_unit(conn, pmc.pmc_id, pmc.owners[0].property_id)
    made = open_lease(conn, pmc.pmc_id, unit, START, None, "1000.00", [tenant_of(conn, pmc)])
    transfer_batch(conn, [(pmc.deposit_cash, pmc.tenant_deposit, Decimal(1000))], noon(START))
    return made


def ok(answer, status=200):
    assert answer.status_code == status, answer.text
    return answer.json() if answer.content else None


def refused(answer, status, detail=None):
    assert answer.status_code == status, answer.text
    if detail is not None:
        assert detail in str(answer.json()["detail"]), answer.text


def set_rate(client, pmc, percent="1.5", starts_on=START):
    return client.post(
        "/api/setup/deposit-interest",
        json={
            "property_id": str(pmc.owners[0].property_id),
            "starts_on": str(starts_on),
            "annual_percent": percent,
        },
    )


def pay(client, lease, through=TODAY, paid_as="rent_credit", request_id=None, **more):
    body = {
        "request_id": str(request_id or uuid.uuid4()),
        "through": str(through),
        "paid_as": paid_as,
        "on": str(TODAY),
    } | more
    return client.post(f"/api/rent/leases/{lease}/deposit-interest", json=body)


def expected(amount, days, percent):
    """Simple interest on a constant deposit, a day at a time over the days in each day's year,
    to the cent."""
    total = Decimal(0)
    for n in range(days):
        day = START + timedelta(days=n)
        in_year = (date(day.year + 1, 1, 1) - date(day.year, 1, 1)).days
        total += Decimal(amount) * Decimal(percent) / 100 / in_year
    return str(total.quantize(Decimal("0.01")))


def test_a_propertys_rate_shows_on_its_page_and_on_the_lease(client, staff, pmc, lease):
    ok(set_rate(client, pmc), 201)
    shown = ok(client.get(f"/api/properties/{pmc.owners[0].property_id}"))
    assert shown["deposit_interest"] == {"starts_on": str(START), "annual_percent": "1.5"}

    due = ok(client.get(f"/api/rent/leases/{lease}/deposit-interest"))

    days = (TODAY - START).days + 1
    assert {k: due[k] for k in ("annual_percent", "rate_from", "from_on", "through")} == {
        "annual_percent": "1.5",
        "rate_from": str(START),
        "from_on": str(START),
        "through": str(TODAY),
    }
    assert due["deposits"] == [
        {"tenant": "Tenant 1", "held": "1000.00", "interest": expected(1000, days, "1.5")}
    ]
    assert due["due"] == expected(1000, days, "1.5")
    assert (due["in_effect"], due["returned"], due["paid"]) == (True, False, [])
    assert due["owner_accounts"] == [{"id": pmc.owners[0].account, "owner": "Owner 1"}]
    # Through an earlier day, less.
    week = ok(
        client.get(
            f"/api/rent/leases/{lease}/deposit-interest",
            params={"through": str(START + timedelta(days=6))},
        )
    )
    assert week["due"] == expected(1000, 7, "1.5")


def test_paid_as_a_rent_credit_once_then_from_the_next_day(client, conn, staff, pmc, lease):
    ok(set_rate(client, pmc), 201)
    through = TODAY - timedelta(days=1)
    request = uuid.uuid4()

    paid = ok(pay(client, lease, through, request_id=request), 201)

    days = (through - START).days + 1
    assert paid == {
        "from_on": str(START),
        "through_on": str(through),
        "amount": expected(1000, days, "1.5"),
        "paid_as": "rent_credit",
    }
    assert ok(pay(client, lease, through, request_id=request), 201) == paid  # sent again
    ledger = ok(client.get(f"/api/leases/{lease}/ledger"))
    assert any(
        line["memo"] == f"Security deposit interest, {START:%m/%d/%Y} to {through:%m/%d/%Y}"
        for line in ledger["lines"]
    ), ledger
    after = ok(client.get(f"/api/rent/leases/{lease}/deposit-interest"))
    assert after["from_on"] == str(TODAY)
    assert after["paid"] == [
        {
            "from_on": str(START),
            "through_on": str(through),
            "amount": paid["amount"],
            "paid_as": "rent_credit",
            "paid_on": str(TODAY),
        }
    ]
    refused(
        pay(client, lease, through),
        409,
        f"its interest is paid through {through:%m/%d/%Y}: pay from {TODAY:%m/%d/%Y} on",
    )


def test_added_to_the_deposit_from_the_owners_account(client, conn, staff, pmc, lease):
    ok(set_rate(client, pmc), 201)
    refused(pay(client, lease, paid_as="deposit"), 409, "not enough money")
    transfer_batch(conn, [(pmc.operating_cash, pmc.owners[0].account, Decimal(100))], noon(START))

    paid = ok(pay(client, lease, paid_as="deposit"), 201)

    assert balance(conn, pmc.tenant_deposit) == Decimal(1000) + Decimal(paid["amount"])
    assert balance(conn, pmc.owners[0].account) == Decimal(100) - Decimal(paid["amount"])


def test_what_is_refused_in_words(client, conn, staff, pmc, lease):
    refused(pay(client, lease), 409, f"no interest is due on the deposits from {START:%m/%d/%Y}")
    ok(set_rate(client, pmc), 201)
    refused(set_rate(client, pmc, "2"), 409, "changes on that day already")
    refused(set_rate(client, pmc, "26", TODAY), 422)
    refused(set_rate(client, pmc, "1.00001", TODAY), 422)
    tomorrow = TODAY + timedelta(days=1)
    refused(pay(client, lease, on=str(tomorrow)), 422, "not later")
    refused(pay(client, lease, through=tomorrow), 422, "days gone by")
    refused(pay(client, lease, paid_as="cash"), 422)
    refused(pay(client, uuid.uuid4()), 404, "no such lease")
    refused(client.get(f"/api/rent/leases/{uuid.uuid4()}/deposit-interest"), 404)
    other = make_pmc(conn)
    refused(
        client.post(
            "/api/setup/deposit-interest",
            json={
                "property_id": str(other.owners[0].property_id),
                "starts_on": str(START),
                "annual_percent": "1",
            },
        ),
        404,
    )
    # Two owner accounts on the property and no shares: one is named.
    second = conn.execute(
        "SELECT trust_open_ledger_account(%s, %s, 'owner_property', %s, %s, NULL)",
        (pmc.pmc_id, pmc.operating_bank_id, pmc.owners[1].owner_id, pmc.owners[0].property_id),
    ).fetchone()[0]
    refused(pay(client, lease, paid_as="deposit"), 422, "name the owner's account")
    transfer_batch(conn, [(pmc.operating_cash, second, Decimal(100))], noon(START))
    ok(pay(client, lease, paid_as="deposit", owner_account=second), 201)


def test_an_ended_lease_takes_it_with_its_deposit(client, conn, staff, pmc):
    prop = pmc.owners[0].property_id
    tenant = add_tenant(conn, pmc.pmc_id, prop, "Tenant 2")
    unit = add_unit(conn, pmc.pmc_id, prop, "Unit 2")
    ended = open_lease(conn, pmc.pmc_id, unit, START, TODAY - timedelta(days=3), "900", [tenant])
    deposit = conn.execute(
        "SELECT trust_open_ledger_account(%s, %s, 'tenant_deposit', NULL, NULL, %s)",
        (pmc.pmc_id, pmc.deposit_bank_id, tenant),
    ).fetchone()[0]
    transfer_batch(conn, [(pmc.deposit_cash, deposit, Decimal(900))], noon(START))
    ok(set_rate(client, pmc), 201)

    due = ok(client.get(f"/api/rent/leases/{ended}/deposit-interest"))
    assert due["in_effect"] is False
    refused(
        pay(client, ended),
        409,
        f"the lease is not in effect on {TODAY:%m/%d/%Y}: add its interest to the deposit",
    )


@pytest.mark.parametrize("caller", ["nobody", "owner", "tenant"])
def test_only_staff_see_or_pay_it(client, conn, app_conn, pmc, lease, caller):
    if caller == "owner":
        join_as_owner(client, app_conn, pmc)
    elif caller == "tenant":
        join_as_tenant(client, conn, app_conn, pmc)
    status = 401 if caller == "nobody" else 403
    assert client.get(f"/api/rent/leases/{lease}/deposit-interest").status_code == status
    assert pay(client, lease).status_code == status
    assert set_rate(client, pmc).status_code == status


def test_staff_without_disburse_may_credit_it_but_not_add_it(client, conn, app_conn, pmc, lease):
    join_as_staff(client, conn, app_conn, pmc, name="Clerk 1", roles=())
    ok(set_rate(client, pmc), 201)
    transfer_batch(conn, [(pmc.operating_cash, pmc.owners[0].account, Decimal(100))], noon(START))
    refused(pay(client, lease, paid_as="deposit"), 403, "may not pay out money")
    ok(pay(client, lease, TODAY - timedelta(days=1)), 201)
