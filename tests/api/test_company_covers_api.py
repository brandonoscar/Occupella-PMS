"""The company covers an owner's shortfall when a payment bounces, through the API (migration
20261115000100, docs/edge-cases.md M-060, GitHub issue #232): a tenant's payment that comes back
after the owner's money went out is refused saying how much the owner's account is short, staff
have the company cover it (`cover_shortfall`) and the bounce posts, the owner's page and portal
show what the owner owes back, and staff take the owner's repayment
(POST /api/payables/advance-repayments). The same for a housing authority's payment, and for a
payment online whose return waits on Banking."""

import json
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from helpers import (
    add_authority,
    add_unit,
    assist,
    balance,
    bank_sums,
    charge,
    contribute,
    draw_owner,
    open_lease,
)
from people import join_as_owner, join_as_staff, join_as_tenant, tenant_of

DAY = timedelta(days=1)


def on_this_day() -> None:
    """Today's dates (UTC), worked out again before each test (tests/conftest.py)."""
    global TODAY, START
    TODAY = datetime.now(UTC).date()
    START = TODAY - 40 * DAY


on_this_day()
SHORT = (
    "not enough money: the owner's account would be $110.00 short. The company can cover it from"
    " its own money, and the owner owes it back"
)


def body(**values):
    return json.loads(json.dumps(values, default=str))


def ok(answer, status=201):
    assert answer.status_code == status, answer.text
    return None if status == 204 else answer.json()


def refused(answer, status, detail=None):
    assert answer.status_code == status, answer.text
    if detail is not None:
        assert answer.json()["detail"] == detail, answer.text


class Short:
    """A lease of Owner 1's property paid $2,250.00, then $110.00 of the owner's money paid out:
    the payment coming back would leave the account $110.00 short."""

    def __init__(self, client, conn, pmc):
        self.pmc = pmc
        self.owner = pmc.owners[0].account
        unit = add_unit(conn, pmc.pmc_id, pmc.owners[0].property_id)
        self.lease = open_lease(
            conn, pmc.pmc_id, unit, START, None, "2250.00", [tenant_of(conn, pmc)]
        )
        charge(conn, pmc.pmc_id, self.lease, START, "2250.00")
        paid = client.post(
            f"/api/rent/leases/{self.lease}/payments",
            json=body(
                request_id=uuid.uuid4(), amount="2250.00", received_on=TODAY - DAY, method="check"
            ),
        )
        self.payment = ok(paid)["transfers"][0]
        draw_owner(conn, pmc.pmc_id, self.owner, "draw-1", "110.00", datetime.now(UTC))

    def bounce(self, client, **more):
        return client.post(f"/api/rent/payments/{self.payment}/bounce", json=body(on=TODAY, **more))


@pytest.fixture
def staff(client, conn, app_conn, pmc):
    return join_as_staff(client, conn, app_conn, pmc)


@pytest.fixture
def short(client, conn, staff, pmc):
    return Short(client, conn, pmc)


def advances(conn, pmc):
    return conn.execute(
        "SELECT amount, recorded_by FROM trust_company_advances WHERE pmc_id = %s", (pmc.pmc_id,)
    ).fetchall()


def lease_payment(client, short):
    lease = ok(client.get(f"/api/leases/{short.lease}"), 200)
    return next(p for p in lease["payments"] if p["transfer_id"] == short.payment)


def owner_account(client, pmc):
    owner = ok(client.get(f"/api/owners/{pmc.owners[0].owner_id}"), 200)
    return next(a for a in owner["accounts"] if a["account"] == pmc.owners[0].account)


def test_a_bounce_the_owner_cannot_take_says_how_much_is_short(client, conn, short, pmc):
    assert lease_payment(client, short)["short_by"] == "110.00"

    refused(short.bounce(client), 409, SHORT)

    assert balance(conn, short.owner) == Decimal("2140.00")
    assert advances(conn, pmc) == []
    assert lease_payment(client, short)["bounced"] is False


def test_the_company_covers_the_shortfall_and_the_payment_bounces(client, conn, staff, short, pmc):
    request = uuid.uuid4()

    done = ok(short.bounce(client, cover_shortfall=True, request_id=request))

    assert done["covered"] == "110.00"
    assert balance(conn, short.owner) == Decimal("0.00")
    assert advances(conn, pmc) == [(Decimal("110.00"), staff.person_id)]
    assert set(bank_sums(conn, pmc.pmc_id).values()) == {Decimal(0)}
    paid = lease_payment(client, short)
    assert (paid["bounced"], paid["short_by"]) == (True, None)
    # The owner's page says what they owe the company back.
    assert owner_account(client, pmc)["owed_to_company"] == "110.00"
    # The same request again records nothing new and answers the same.
    assert ok(short.bounce(client, cover_shortfall=True, request_id=request)) == done
    assert len(advances(conn, pmc)) == 1


def test_a_payment_the_owner_can_take_bounces_with_no_cover(client, conn, staff, pmc):
    whole = Short.__new__(Short)
    whole.pmc, whole.owner = pmc, pmc.owners[0].account
    unit = add_unit(conn, pmc.pmc_id, pmc.owners[0].property_id)
    whole.lease = open_lease(conn, pmc.pmc_id, unit, START, None, "900.00", [tenant_of(conn, pmc)])
    charge(conn, pmc.pmc_id, whole.lease, START, "900.00")
    whole.payment = ok(
        client.post(
            f"/api/rent/leases/{whole.lease}/payments",
            json=body(request_id=uuid.uuid4(), amount="900.00", received_on=TODAY),
        )
    )["transfers"][0]

    done = ok(whole.bounce(client, cover_shortfall=True, request_id=uuid.uuid4()))

    assert done["covered"] is None
    assert advances(conn, pmc) == []


@pytest.mark.parametrize(
    "more",
    [
        {"cover_shortfall": True},  # a cover needs the page's request id
        {"cover_shortfall": True, "request_id": "not-a-uuid"},
        {"cover_shortfall": "maybe", "request_id": str(uuid.uuid4())},
    ],
)
def test_a_cover_asked_badly_is_refused_and_records_nothing(client, conn, short, pmc, more):
    refused(short.bounce(client, **more), 422)
    assert advances(conn, pmc) == []
    assert balance(conn, short.owner) == Decimal("2140.00")


@pytest.mark.parametrize("caller", ["nobody", "owner", "tenant"])
def test_only_staff_cover_a_shortfall(client, other_client, conn, app_conn, short, pmc, caller):
    if caller == "owner":
        join_as_owner(other_client, app_conn, pmc)
    elif caller == "tenant":
        join_as_tenant(other_client, conn, app_conn, pmc)
    status = 401 if caller == "nobody" else 403
    refused(short.bounce(other_client, cover_shortfall=True, request_id=uuid.uuid4()), status)
    assert advances(conn, pmc) == []


def test_a_custom_role_without_rent_work_covers_nothing(
    client, other_client, conn, app_conn, short, pmc
):
    clerk = join_as_staff(other_client, conn, app_conn, pmc, name="Clerk 2", roles=[])
    role = ok(client.post("/api/custom-roles", json={"name": "Books", "areas": ["bills"]}))
    ok(
        client.post(
            f"/api/custom-roles/{role['id']}/staff",
            json={"staff_id": str(clerk.person_id), "granted": True},
        ),
        204,
    )
    refused(short.bounce(other_client, cover_shortfall=True, request_id=uuid.uuid4()), 403)
    assert advances(conn, pmc) == []


def repay(client, pmc, request_id=None, **more):
    return client.post(
        "/api/payables/advance-repayments",
        json=body(
            **(
                {
                    "request_id": request_id or uuid.uuid4(),
                    "account": pmc.owners[0].account,
                    "amount": "60.00",
                    "on": TODAY,
                }
                | more
            )
        ),
    )


def test_staff_take_the_owners_repayment_once_and_never_more_than_is_owed(client, conn, short, pmc):
    ok(short.bounce(client, cover_shortfall=True, request_id=uuid.uuid4()))
    contribute(conn, pmc.pmc_id, short.owner, "in-1", "500.00", datetime.now(UTC))
    request = uuid.uuid4()

    first = ok(repay(client, pmc, request))
    assert ok(repay(client, pmc, request)) == first
    refused(
        repay(client, pmc, amount="50.01"),
        409,
        "only $50.00 is owed back to the company; $50.01 is more than that",
    )
    ok(repay(client, pmc, amount="50.00"))
    refused(
        repay(client, pmc, amount="1.00"),
        409,
        "nothing is owed back to the company from this account",
    )

    assert owner_account(client, pmc)["owed_to_company"] == "0.00"
    assert balance(conn, short.owner) == Decimal("390.00")
    assert balance(conn, pmc.pmc_income) == Decimal("110.00")


@pytest.mark.parametrize(
    "more",
    [{"amount": "-5.00"}, {"amount": "0"}, {"amount": "1.005"}, {"account": None}, {"on": "soon"}],
)
def test_a_repayment_asked_badly_is_refused(client, conn, short, pmc, more):
    refused(repay(client, pmc, **more), 422)


def test_a_repayment_of_another_companys_account_is_not_found(client, staff, pmc):
    refused(repay(client, pmc, account="pgla_not_here"), 404)


def test_repaying_takes_the_disburse_role(client, other_client, conn, app_conn, short, pmc):
    ok(short.bounce(client, cover_shortfall=True, request_id=uuid.uuid4()))
    contribute(conn, pmc.pmc_id, short.owner, "in-1", "500.00", datetime.now(UTC))
    join_as_staff(other_client, conn, app_conn, pmc, name="Clerk 3", roles=[])

    refused(repay(other_client, pmc), 403)
    refused(repay(join_as_owner_client(other_client, app_conn, pmc), pmc), 403)
    assert owner_account(client, pmc)["owed_to_company"] == "110.00"


def join_as_owner_client(other_client, app_conn, pmc):
    other_client.cookies.clear()
    join_as_owner(other_client, app_conn, pmc)
    return other_client


def test_the_owner_sees_what_they_owe_the_company_in_the_portal(
    client, other_client, conn, app_conn, short, pmc
):
    ok(short.bounce(client, cover_shortfall=True, request_id=uuid.uuid4()))
    join_as_owner(other_client, app_conn, pmc)

    [mine] = [
        p
        for p in ok(other_client.get("/api/owner/properties"), 200)
        if p["account_id"] == short.owner
    ]

    assert (mine["balance"], mine["owed_to_company"]) == ("0.00", "110.00")


# --- a housing authority's payment ---------------------------------------------------------------


def test_the_company_covers_an_authority_payment_that_came_back(client, conn, staff, pmc):
    owner = pmc.owners[0].account
    unit = add_unit(conn, pmc.pmc_id, pmc.owners[0].property_id)
    first = TODAY.replace(day=1)
    lease = open_lease(conn, pmc.pmc_id, unit, first, None, "1000.00", [tenant_of(conn, pmc)])
    authority = add_authority(conn, pmc.pmc_id)
    assist(conn, pmc.pmc_id, lease, authority, first, "800.00")
    ok(client.post("/api/rent/run", json=body(due_on=first, late_fees_as_of=first)), 200)
    paid = ok(
        client.post(
            "/api/assistance/payments",
            json=body(
                request_id=uuid.uuid4(),
                housing_authority_id=authority,
                received_on=first,
                lines=[{"lease_id": lease, "amount": "800.00"}],
            ),
        )
    )
    transfer = paid["transfers"][0]
    draw_owner(conn, pmc.pmc_id, owner, "draw-1", "800.00", datetime.now(UTC))
    path = f"/api/assistance/payments/{transfer}/bounce"
    page = ok(client.get(f"/api/assistance/authorities/{authority}"), 200)
    assert page["payments"][0]["short_by"] == "800.00"

    refused(
        client.post(path, json=body(on=TODAY)),
        409,
        "not enough money: the owner's account would be $800.00 short. The company can cover it"
        " from its own money, and the owner owes it back",
    )
    refused(client.post(path, json=body(on=TODAY, cover_shortfall=True)), 422)
    request = uuid.uuid4()
    done = ok(client.post(path, json=body(on=TODAY, cover_shortfall=True, request_id=request)))

    assert (done["amount"], done["covered"]) == ("800.00", "800.00")
    assert balance(conn, owner) == Decimal("0.00")
    assert (
        ok(client.post(path, json=body(on=TODAY, cover_shortfall=True, request_id=request))) == done
    )
    assert len(advances(conn, pmc)) == 1
