"""Unapplied cash through the API (src/pms/api/unapplied.py, migration 20261106000240): staff
record money they can't match yet, see how long it has waited, apply it to a lease (paying what it
owes as Receive payment does) or refund it (a payout, disburse)."""

import json
import threading
import uuid
from datetime import UTC, datetime, time, timedelta
from decimal import Decimal
from types import SimpleNamespace

import psycopg
import pytest
from helpers import (
    add_tenant,
    add_unit,
    approve,
    bank_sums,
    make_pmc,
    open_account,
    open_lease,
)
from people import join_as_owner, join_as_staff, join_as_tenant

from pms import email
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


def test_the_same_request_again_is_the_receipt_recorded_and_never_another(client, conn, staff, pmc):
    request_id = uuid.uuid4()
    made = receive(client, request_id=request_id)
    asked = {
        "request_id": request_id,
        "amount": "500.00",
        "received_on": TODAY - 3 * DAY,
        "payer": "J. Sample",
        "method": "check",
    }

    # Sent again described otherwise (another check number, how it came, who it seems to be
    # from), it is the receipt recorded, as it was recorded.
    described = ok(
        send(client, "/api/unapplied", **asked | {"payer": "J. Sample Jr.", "reference": "4411"})
    )
    more = send(client, "/api/unapplied", **asked | {"amount": "499.00"})
    later = send(client, "/api/unapplied", **asked | {"received_on": TODAY - 2 * DAY})

    assert described == made
    for answer in (more, later):
        refused(answer, 409, "this request id was used for something else")
    (listed,) = ok(client.get("/api/unapplied"), 200)
    assert (listed["payer"], listed["reference"], listed["amount"]) == (
        "J. Sample",
        "4410",
        "500.00",
    )
    assert balance(conn, unapplied_account(conn, pmc)) == Decimal("500.00")


def test_a_company_with_no_operating_trust_bank_account_holds_none(client, conn, app_conn):
    pmc_id = conn.execute(
        "INSERT INTO trust_pmcs (display_name) VALUES ('Synthetic new company') RETURNING pmc_id"
    ).fetchone()[0]
    conn.execute(
        "INSERT INTO trust_company_profiles (pmc_id, time_zone) VALUES (%s, 'UTC')", (pmc_id,)
    )
    join_as_staff(client, conn, app_conn, SimpleNamespace(pmc_id=pmc_id))

    answer = send(
        client,
        "/api/unapplied",
        request_id=uuid.uuid4(),
        amount="5.00",
        received_on=TODAY,
        payer="X",
        method="cash",
    )

    refused(answer, 409, "the company has no operating trust bank account yet")
    assert conn.execute(
        "SELECT count(*) FROM trust_unapplied_receipts WHERE pmc_id = %s", (pmc_id,)
    ).fetchone() == (0,)


def test_unapplied_cash_dated_in_a_month_already_closed_is_refused_in_words(
    client, conn, staff, pmc, lease
):
    def at(day):
        return datetime.combine(day, time.min, UTC)

    approve(conn, pmc.pmc_id, pmc.operating_bank_id, at(START), at(TODAY - 5 * DAY))
    closed = "that day is in a period already reconciled and closed: date it in the next open one"

    late = send(
        client,
        "/api/unapplied",
        request_id=uuid.uuid4(),
        amount="500.00",
        received_on=TODAY - 10 * DAY,
        payer="J. Sample",
        method="check",
    )
    made = receive(client, on=TODAY - 3 * DAY)
    back_dated = send(
        client,
        f"/api/unapplied/{made['id']}/apply",
        request_id=uuid.uuid4(),
        lease_id=lease,
        on=TODAY - 10 * DAY,
    )

    refused(late, 409, closed)
    refused(back_dated, 409, closed)
    assert [r["id"] for r in ok(client.get("/api/unapplied"), 200)] == [made["id"]]
    assert ok(client.get("/api/unapplied"), 200)[0]["left"] == "500.00"


def test_applied_to_a_lease_whose_owners_bank_elsewhere_it_is_refused(client, conn, staff, pmc):
    # A second operating trust bank account, holding another owner's property and its lease.
    bank = ok(
        send(client, "/api/setup/bank-accounts", kind="operating", name="Second operating account")
    )["id"]
    owner = ok(send(client, "/api/setup/owners", name="Owner 3"))["id"]
    home = conn.execute(
        "INSERT INTO trust_properties (pmc_id, display_name) VALUES (%s, 'Property 3')"
        " RETURNING id",
        (pmc.pmc_id,),
    ).fetchone()[0]
    open_account(conn, pmc.pmc_id, bank, "owner_property", owner, home)
    tenant = add_tenant(conn, pmc.pmc_id, home, "Tenant 3")
    lease = open_lease(
        conn, pmc.pmc_id, add_unit(conn, pmc.pmc_id, home), START, None, "900.00", [tenant]
    )
    made = receive(client, bank_account_id=pmc.operating_bank_id)

    def apply():
        path = f"/api/unapplied/{made['id']}/apply"
        return send(client, path, request_id=uuid.uuid4(), lease_id=lease)

    # Owing nothing, all of it would be prepaid rent, held in the other account.
    nothing_owed = apply()
    ok(send(client, "/api/rent/run", due_on=START, late_fees_as_of=START), 200)
    owed = apply()

    refused(
        nothing_owed,
        409,
        "this lease's tenants hold prepaid rent in another trust bank account than the one this"
        " unapplied cash is in",
    )
    refused(
        owed,
        409,
        "this lease's owners keep their money in another trust bank account than the one this"
        " unapplied cash is in",
    )
    assert ok(client.get("/api/unapplied"), 200)[0]["left"] == "500.00"
    assert set(bank_sums(conn, pmc.pmc_id).values()) == {Decimal(0)}


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


def test_applied_its_tenants_get_a_receipt_as_for_any_payment(client, conn, staff, pmc, lease):
    tenant = conn.execute(
        "SELECT tenant_id FROM trust_lease_tenants WHERE lease_id = %s", (lease,)
    ).fetchone()[0]
    ok(client.post(f"/api/tenants/{tenant}/contact", json={"email": "t@example.com"}), 204)
    client.app.state.email = email.FakeSender()
    made = receive(client, amount="300.00")

    paid = ok(
        send(client, f"/api/unapplied/{made['id']}/apply", request_id=uuid.uuid4(), lease_id=lease)
    )
    receipt = client.get(f"/api/rent/payments/{paid['transfers'][0]}/receipt")

    assert (receipt.status_code, receipt.content[:5]) == (200, b"%PDF-")
    assert conn.execute(
        "SELECT kind, transfer_id FROM trust_emails WHERE pmc_id = %s", (pmc.pmc_id,)
    ).fetchall() == [("payment_receipt", paid["transfers"][0])]


def test_applied_to_a_property_with_two_owner_accounts_it_needs_one_chosen(
    client, conn, staff, pmc, lease
):
    second = open_account(
        conn,
        pmc.pmc_id,
        pmc.operating_bank_id,
        "owner_property",
        pmc.owners[1].owner_id,
        pmc.owners[0].property_id,
    )
    ok(send(client, "/api/rent/run", due_on=START, late_fees_as_of=START), 200)
    made = receive(client, amount="100.00")

    def apply(**more):
        path = f"/api/unapplied/{made['id']}/apply"
        return send(client, path, request_id=uuid.uuid4(), lease_id=lease, **more)

    unchosen = apply()
    chosen = ok(apply(account=second))

    refused(unchosen, 422, "the property has more than one owner account; choose one")
    assert chosen["to_owner"] == "100.00"
    assert balance(conn, second) == Decimal(100)


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


def waiting_on_an_advisory_lock(conn):
    for _ in range(1000):
        if conn.execute(
            "SELECT count(*) FROM pg_locks WHERE locktype = 'advisory' AND NOT granted"
        ).fetchone()[0]:
            return True
        threading.Event().wait(0.01)
    return False


@pytest.mark.timing
@pytest.mark.parametrize("step", ["apply", "refund"])
def test_applying_and_refunding_one_receipt_wait_for_each_other_first(
    client, conn, database_url, staff, pmc, lease, step
):
    """An application posts its payment, locking the unapplied cash account, and then locks the
    receipt; a refund locks the receipt and then the account. Both at once deadlocked, one a
    server error (the review of 11/07). Each now takes the receipt's own lock before anything
    else, so the second waits there, having posted nothing."""
    made = receive(client, amount="500.00")
    held = balance(conn, unapplied_account(conn, pmc))
    holder = psycopg.connect(database_url)  # as a request on the receipt would hold it
    holder.execute(
        "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
        (f"unapplied receipt {made['id']}",),
    )
    body = (
        {"lease_id": str(lease)} if step == "apply" else {"paid_to": "J. Sample", "method": "check"}
    )
    answer: dict = {}
    thread = threading.Thread(
        target=lambda: answer.update(
            it=send(client, f"/api/unapplied/{made['id']}/{step}", request_id=uuid.uuid4(), **body)
        )
    )
    thread.start()
    try:
        assert waiting_on_an_advisory_lock(conn)
        assert balance(conn, unapplied_account(conn, pmc)) == held  # nothing moved yet
    finally:
        holder.rollback()
        holder.close()
        thread.join(timeout=30)
    ok(answer["it"])
    assert balance(conn, unapplied_account(conn, pmc)) == Decimal("0.00")


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
