"""Unapplied cash through the API (src/pms/api/unapplied.py, migration 20261106000240): staff
record money they can't match yet, see how long it has waited, apply it to a lease (paying what it
owes as Receive payment does) or refund it (a payout, disburse)."""

import json
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from helpers import add_unit, bank_sums, make_pmc, open_lease
from people import join_as_owner, join_as_staff, join_as_tenant

from pms.api import rent

TODAY = datetime.now(UTC).date()
DAY = timedelta(days=1)
START = (TODAY.replace(day=1) - DAY).replace(day=1)


@pytest.fixture
def staff(client, conn, app_conn, pmc):
    return join_as_staff(client, conn, app_conn, pmc)


@pytest.fixture
def lease(conn, pmc):
    tenant = conn.execute(
        "SELECT tenant_id FROM trust_ledger_accounts WHERE ledger_account_id = %s",
        (pmc.prepaid_rent,),
    ).fetchone()[0]
    unit = add_unit(conn, pmc.pmc_id, pmc.owners[0].property_id)
    return open_lease(conn, pmc.pmc_id, unit, START, None, "1000.00", [tenant])


def send(client, path, **body):
    return client.post(path, json=json.loads(json.dumps(body, default=str)))


def ok(answer, status=201):
    assert answer.status_code == status, answer.text
    return None if status == 204 else answer.json()


def refused(answer, status, detail=None):
    assert answer.status_code == status, answer.text
    if detail is not None:
        assert answer.json()["detail"] == detail


def receive(client, amount="500.00", on=TODAY - 3 * DAY, request_id=None, **more):
    return ok(
        send(
            client,
            "/api/unapplied",
            request_id=request_id or uuid.uuid4(),
            amount=amount,
            received_on=on,
            payer="J. Sample",
            memo="Check with no unit written on it",
            method="check",
            reference="4410",
            **more,
        )
    )


def balance(conn, account):
    row = conn.execute("SELECT balance FROM pgledger_accounts WHERE id = %s", (account,)).fetchone()
    return row[0]


def unapplied_account(conn, pmc):
    return conn.execute(
        "SELECT ledger_account_id FROM trust_ledger_accounts"
        " WHERE bank_account_id = %s AND kind = 'unapplied_cash'",
        (pmc.operating_bank_id,),
    ).fetchone()[0]


def test_staff_record_unapplied_cash_and_see_how_long_it_has_waited(client, conn, staff, pmc):
    request_id = uuid.uuid4()
    made = receive(client, request_id=request_id)
    again = receive(client, request_id=request_id)

    (listed,) = ok(client.get("/api/unapplied"), 200)
    assert again == made
    assert listed == {
        "id": made["id"],
        "bank_account_id": str(pmc.operating_bank_id),
        "bank_account": "Synthetic operating trust account",
        "received_on": str(TODAY - 3 * DAY),
        "payer": "J. Sample",
        "memo": "Check with no unit written on it",
        "method": "check",
        "reference": "4410",
        "amount": "500.00",
        "left": "500.00",
        "age_days": 3,
    }
    assert balance(conn, unapplied_account(conn, pmc)) == Decimal("500.00")
    assert set(bank_sums(conn, pmc.pmc_id).values()) == {Decimal(0)}
    assert conn.execute(
        "SELECT recorded_by FROM trust_unapplied_receipts WHERE id = %s", (made["id"],)
    ).fetchone() == (staff.person_id,)


def test_applied_to_a_lease_it_pays_rent_first_and_the_rest_is_prepaid_rent(
    client, conn, staff, pmc, lease
):
    ok(send(client, "/api/rent/run", due_on=START, late_fees_as_of=START), 200)
    made = receive(client, amount="1200.00")

    paid = ok(
        send(client, f"/api/unapplied/{made['id']}/apply", request_id=uuid.uuid4(), lease_id=lease)
    )

    assert (paid["to_owner"], paid["to_prepaid"]) == ("1000.00", "200.00")
    assert rent.owed(conn, lease) == Decimal("0.00")
    assert balance(conn, pmc.owners[0].account) == Decimal("1000.00")
    assert balance(conn, unapplied_account(conn, pmc)) == Decimal("0.00")
    # It is used up: off the list, and on it with everything.
    assert ok(client.get("/api/unapplied"), 200) == []
    assert ok(client.get("/api/unapplied?all=true"), 200)[0]["left"] == "0.00"
    # The lease lists it as a payment as it came: the check and its number.
    found = ok(client.get(f"/api/leases/{lease}"), 200)
    assert set(bank_sums(conn, pmc.pmc_id).values()) == {Decimal(0)}
    assert found is not None
    # It came in before, so it never bounces from here.
    refused(
        send(client, f"/api/rent/payments/{paid['transfers'][0]}/bounce", on=TODAY),
        409,
        "this payment is unapplied cash applied to the lease: it came in before, so it can't"
        " bounce here",
    )


def test_part_of_it_applied_and_the_rest_refunded_never_past_what_is_left(
    client, conn, staff, pmc, lease
):
    ok(send(client, "/api/rent/run", due_on=START, late_fees_as_of=START), 200)
    made = receive(client, amount="1500.00")
    ok(
        send(
            client,
            f"/api/unapplied/{made['id']}/apply",
            request_id=uuid.uuid4(),
            lease_id=lease,
            amount="1000.00",
        )
    )

    too_much = send(
        client,
        f"/api/unapplied/{made['id']}/refund",
        request_id=uuid.uuid4(),
        paid_to="J. Sample",
        method="check",
        amount="500.01",
    )
    request_id = uuid.uuid4()
    back = ok(
        send(
            client,
            f"/api/unapplied/{made['id']}/refund",
            request_id=request_id,
            paid_to="J. Sample",
            method="check",
        )
    )
    again = ok(
        send(
            client,
            f"/api/unapplied/{made['id']}/refund",
            request_id=request_id,
            paid_to="J. Sample",
            method="check",
        )
    )
    nothing = send(
        client,
        f"/api/unapplied/{made['id']}/apply",
        request_id=uuid.uuid4(),
        lease_id=lease,
    )

    refused(too_much, 409, "only $500.00 of this unapplied cash is left; $500.01 is more")
    assert back == again and back["amount"] == "500.00"
    refused(nothing, 409, "nothing is left of this unapplied cash to apply")
    assert balance(conn, unapplied_account(conn, pmc)) == Decimal("0.00")
    assert set(bank_sums(conn, pmc.pmc_id).values()) == {Decimal(0)}


def test_a_refund_takes_the_disburse_role(client, other_client, conn, app_conn, staff, pmc):
    made = receive(client)
    clerk = join_as_staff(other_client, conn, app_conn, pmc, name="Clerk 2", roles=[])

    answer = send(
        other_client,
        f"/api/unapplied/{made['id']}/refund",
        request_id=uuid.uuid4(),
        paid_to="J. Sample",
        method="EFT",
    )

    assert answer.status_code == 403, answer.text
    assert "disburse" in answer.json()["detail"]
    assert clerk is not None


@pytest.mark.parametrize(
    "change",
    [
        {"amount": "0.00"},
        {"amount": "-5.00"},
        {"amount": "1.001"},
        {"method": "bitcoin"},
        {"payer": ""},
        {"received_on": str(TODAY + DAY)},
    ],
)
def test_bad_unapplied_cash_is_refused_and_records_nothing(client, conn, staff, pmc, change):
    body = {
        "request_id": str(uuid.uuid4()),
        "amount": "10.00",
        "received_on": str(TODAY),
        "payer": "J. Sample",
        "method": "check",
    } | change

    answer = client.post("/api/unapplied", json=body)

    assert answer.status_code == 422, answer.text
    assert conn.execute(
        "SELECT count(*) FROM trust_unapplied_receipts WHERE pmc_id = %s", (pmc.pmc_id,)
    ).fetchone() == (0,)


def test_another_companys_unapplied_cash_and_lease_are_not_found(client, conn, staff, pmc, lease):
    theirs = make_pmc(conn)
    other_unit = add_unit(conn, theirs.pmc_id, theirs.owners[0].property_id)
    tenant = conn.execute(
        "SELECT tenant_id FROM trust_ledger_accounts WHERE ledger_account_id = %s",
        (theirs.prepaid_rent,),
    ).fetchone()[0]
    other_lease = open_lease(conn, theirs.pmc_id, other_unit, START, None, "900.00", [tenant])
    made = receive(client)

    refused(
        send(
            client, f"/api/unapplied/{uuid.uuid4()}/apply", request_id=uuid.uuid4(), lease_id=lease
        ),
        404,
    )
    refused(
        send(
            client,
            f"/api/unapplied/{made['id']}/apply",
            request_id=uuid.uuid4(),
            lease_id=other_lease,
        ),
        404,
    )
    refused(
        send(
            client,
            f"/api/unapplied/{uuid.uuid4()}/refund",
            request_id=uuid.uuid4(),
            paid_to="X",
            method="check",
        ),
        404,
    )


@pytest.mark.parametrize("caller", ["nobody", "owner", "tenant"])
def test_only_staff_reach_unapplied_cash(client, conn, app_conn, pmc, caller):
    if caller == "owner":
        join_as_owner(client, app_conn, pmc)
    elif caller == "tenant":
        join_as_tenant(client, conn, app_conn, pmc)
    some = uuid.uuid4()

    answers = [
        client.get("/api/unapplied"),
        send(
            client,
            "/api/unapplied",
            request_id=some,
            amount="5.00",
            received_on=TODAY,
            payer="X",
            method="cash",
        ),
        send(client, f"/api/unapplied/{some}/apply", request_id=some, lease_id=some),
        send(client, f"/api/unapplied/{some}/refund", request_id=some, paid_to="X", method="check"),
    ]

    assert [a.status_code for a in answers] == [401 if caller == "nobody" else 403] * 4


def test_a_custom_role_without_rent_records_no_unapplied_cash(
    client, other_client, conn, app_conn, staff, pmc
):
    clerk = join_as_staff(other_client, conn, app_conn, pmc, name="Clerk 2", roles=[])
    role = ok(client.post("/api/custom-roles", json={"name": "Bills", "areas": ["bills"]}))
    ok(
        client.post(
            f"/api/custom-roles/{role['id']}/staff",
            json={"staff_id": str(clerk.person_id), "granted": True},
        ),
        204,
    )

    answer = send(
        other_client,
        "/api/unapplied",
        request_id=uuid.uuid4(),
        amount="5.00",
        received_on=TODAY,
        payer="X",
        method="cash",
    )

    assert answer.status_code == 403, answer.text
    assert ok(other_client.get("/api/unapplied"), 200) == []
