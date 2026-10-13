"""Staff's bills and owners' money through the API (src/pms/api/payables.py): vendor bills,
the owner's approval over the limit, setting aside and paying them, recurring bills, management
and leasing fees, owner draws and contributions."""

import json
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from helpers import (
    add_agreement,
    add_recurring_charge,
    add_unit,
    add_work_order,
    apply_payment,
    approve,
    balance,
    bank_sums,
    charge,
    charge_recurring_due,
    enter_bill,
    open_lease,
    post,
    step_work_order,
)
from people import join_as_owner, join_as_staff, join_as_tenant

from pms import accounts, email

NOW = datetime.now(UTC)
TODAY = NOW.date()
DAY = timedelta(days=1)


@pytest.fixture
def staff(client, conn, app_conn, pmc):
    return join_as_staff(client, conn, app_conn, pmc)


def call(client, path, **body):
    return client.post(f"/api/payables/{path}", json=json.loads(json.dumps(body, default=str)))


def ok(answer, status=201):
    assert answer.status_code == status, answer.text
    return answer.json()


def refused(answer, status, detail=None):
    assert answer.status_code == status, answer.text
    if detail is not None:
        assert answer.json()["detail"] == detail


def fund(conn, pmc, amount, n=0, key=None):
    """Money into owner n's property account, as rent from the operating account's cash."""
    post(
        conn,
        pmc.pmc_id,
        key or f"fund-{uuid.uuid4()}",
        [(pmc.operating_cash, pmc.owners[n].account, Decimal(amount))],
        NOW - 30 * DAY,
    )


def new_bill(client, pmc, amount="200.00", reference="INV-1", **more):
    return call(
        client,
        "bills",
        **(
            {
                "vendor_id": pmc.vendor_id,
                "account": pmc.owners[0].account,
                "reference": reference,
                "bill_date": TODAY - 2 * DAY,
                "due_on": TODAY + 10 * DAY,
                "amount": amount,
                "memo": "Gutter repair",
            }
            | more
        ),
    )


def statuses(client, show="open"):
    return {
        b["reference"]: b["status"] for b in ok(client.get(f"/api/payables/bills?show={show}"), 200)
    }


def test_a_bill_is_entered_set_aside_and_paid_once_each(client, conn, staff, pmc):
    fund(conn, pmc, "1000.00")
    add_agreement(conn, pmc.pmc_id, pmc.owners[0].account, TODAY - 60 * DAY, approval_limit="500")
    bill = ok(new_bill(client, pmc))["id"]

    entered = statuses(client)
    aside = ok(call(client, f"bills/{bill}/set-aside", on=TODAY))
    again = ok(call(client, f"bills/{bill}/set-aside", on=TODAY))
    held = balance(conn, pmc.owners[0].account)
    paid = ok(call(client, f"bills/{bill}/pay", on=TODAY))
    (listed,) = ok(client.get("/api/payables/bills?show=all"), 200)

    assert entered == {"INV-1": "to set aside"}
    assert again == aside
    assert held == Decimal("800.00")
    assert paid["transfer_id"] != aside["transfer_id"]
    assert statuses(client) == {}
    assert (listed["status"], listed["amount"], listed["owner"], listed["vendor"]) == (
        "paid",
        "200.00",
        "Owner 1",
        "Vendor 1",
    )
    assert set(bank_sums(conn, pmc.pmc_id).values()) == {Decimal(0)}


def test_a_new_vendors_first_bill_opens_its_account(client, conn, staff, pmc):
    fund(conn, pmc, "1000.00")
    add_agreement(conn, pmc.pmc_id, pmc.owners[0].account, TODAY - 60 * DAY, approval_limit="500")
    (vendor,) = conn.execute(
        "INSERT INTO trust_vendors (pmc_id, display_name) VALUES (%s, 'Vendor 2') RETURNING id",
        (pmc.pmc_id,),
    ).fetchone()
    bill = ok(new_bill(client, pmc, vendor_id=vendor))["id"]

    ok(call(client, f"bills/{bill}/set-aside", on=TODAY))
    ok(call(client, f"bills/{bill}/pay", on=TODAY))

    (account,) = conn.execute(
        "SELECT ledger_account_id FROM trust_ledger_accounts"
        " WHERE vendor_id = %s AND kind = 'vendor_payable'",
        (vendor,),
    ).fetchone()
    assert balance(conn, account) == Decimal(0)
    assert balance(conn, pmc.owners[0].account) == Decimal("800.00")


def test_a_bill_is_not_paid_in_a_closed_period(client, conn, staff, pmc):
    fund(conn, pmc, "1000.00")
    add_agreement(conn, pmc.pmc_id, pmc.owners[0].account, TODAY - 60 * DAY, approval_limit="500")
    bill = ok(new_bill(client, pmc, bill_date=TODAY - 25 * DAY))["id"]
    ok(call(client, f"bills/{bill}/set-aside", on=TODAY - 20 * DAY))
    approve(
        conn,
        pmc.pmc_id,
        pmc.operating_bank_id,
        NOW - 40 * DAY,
        datetime.combine(TODAY - 10 * DAY, datetime.min.time(), UTC),
        statement_balance="1000.00",
    )

    answer = call(client, f"bills/{bill}/pay", on=TODAY - 15 * DAY)

    refused(answer, 409)
    assert "closed" in answer.json()["detail"]


def test_a_bill_over_the_limit_needs_the_owners_approval_once(client, conn, staff, pmc):
    fund(conn, pmc, "1000.00")
    add_agreement(conn, pmc.pmc_id, pmc.owners[0].account, TODAY - 60 * DAY, approval_limit="500")
    bill = ok(new_bill(client, pmc, "800.00"))["id"]

    waiting = statuses(client)
    early = call(client, f"bills/{bill}/set-aside", on=TODAY)
    ok(call(client, f"bills/{bill}/approval", approved_on=TODAY, approved_by="Owner 1, by phone"))
    twice = call(client, f"bills/{bill}/approval", approved_on=TODAY, approved_by="Owner 1")
    approved = statuses(client)
    ok(call(client, f"bills/{bill}/set-aside", on=TODAY))

    assert waiting == {"INV-1": "needs approval"}
    refused(early, 409)
    assert "approval limit" in early.json()["detail"]
    refused(twice, 409, "this bill is approved already")
    assert approved == {"INV-1": "to set aside"}
    assert statuses(client) == {"INV-1": "set aside"}


def test_staff_record_an_owners_decline_and_the_bill_is_never_paid(client, conn, staff, pmc):
    fund(conn, pmc, "1000.00")
    add_agreement(conn, pmc.pmc_id, pmc.owners[0].account, TODAY - 60 * DAY, approval_limit="500")
    bill = ok(new_bill(client, pmc, "800.00"))["id"]
    before = ok(client.get("/api/payables/owners"), 200)[0]

    ok(
        call(
            client,
            f"bills/{bill}/decline",
            declined_on=TODAY,
            declined_by="Owner 1, by email",
            reason="Get a second quote",
        )
    )
    shown = next(b for b in ok(client.get("/api/payables/bills?show=all"), 200) if b["id"] == bill)
    after = ok(client.get("/api/payables/owners"), 200)[0]
    aside = call(client, f"bills/{bill}/set-aside", on=TODAY)
    approval = call(client, f"bills/{bill}/approval", approved_on=TODAY, approved_by="Owner 1")
    again = call(
        client, f"bills/{bill}/decline", declined_on=TODAY, declined_by="Owner 1", reason="No"
    )

    assert (shown["status"], shown["declined_by"], shown["decline_reason"]) == (
        "declined",
        "Owner 1, by email",
        "Get a second quote",
    )
    assert statuses(client) == {}  # nothing left to pay
    assert (before["unpaid_bills"], after["unpaid_bills"]) == ("800.00", "0.00")
    refused(aside, 409)
    assert "declined by Owner 1, by email" in aside.json()["detail"]
    refused(approval, 409, "the owner declined this bill")
    refused(again, 409, "the owner declined this bill")


def test_a_bill_set_aside_is_not_declined_by_staff_either(client, conn, staff, pmc):
    fund(conn, pmc, "1000.00")
    bill = ok(new_bill(client, pmc, "80.00"))["id"]
    add_agreement(conn, pmc.pmc_id, pmc.owners[0].account, TODAY - 60 * DAY, approval_limit="500")
    ok(call(client, f"bills/{bill}/set-aside", on=TODAY))

    answer = call(
        client, f"bills/{bill}/decline", declined_on=TODAY, declined_by="Owner 1", reason="No"
    )

    refused(answer, 409)
    assert "set aside already" in answer.json()["detail"]


@pytest.mark.parametrize(
    "body",
    [
        {"declined_by": "Owner 1", "reason": "No"},
        {"declined_on": str(TODAY), "declined_by": "", "reason": "No"},
        {"declined_on": str(TODAY), "declined_by": "Owner 1", "reason": " "},
        {"declined_on": str(TODAY), "declined_by": "Owner 1", "reason": "x" * 1001},
    ],
)
def test_a_decline_says_when_who_and_why(client, conn, staff, pmc, body):
    bill = ok(new_bill(client, pmc, "800.00"))["id"]

    refused(client.post(f"/api/payables/bills/{bill}/decline", json=body), 422)


def test_a_bill_is_paid_only_once_set_aside_and_never_into_the_negative(client, conn, staff, pmc):
    add_agreement(conn, pmc.pmc_id, pmc.owners[0].account, TODAY - 60 * DAY, approval_limit="500")
    bill = ok(new_bill(client, pmc))["id"]

    unpaid = call(client, f"bills/{bill}/pay", on=TODAY)
    no_money = call(client, f"bills/{bill}/set-aside", on=TODAY)

    refused(unpaid, 409)
    assert "not set aside yet" in unpaid.json()["detail"]
    refused(no_money, 409)
    assert "below zero" in no_money.json()["detail"]


def test_a_bill_is_entered_once_per_vendor_reference_with_sound_terms(client, conn, staff, pmc):
    ok(new_bill(client, pmc))
    other_property = add_work_order(
        conn, pmc.pmc_id, pmc.owners[1].property_id, None, "Roof", NOW - DAY
    )
    cancelled = add_work_order(
        conn, pmc.pmc_id, pmc.owners[0].property_id, None, "Fence", NOW - DAY
    )
    step_work_order(conn, pmc.pmc_id, cancelled, "cancelled", NOW - DAY)

    refused(new_bill(client, pmc), 409, "this vendor has a bill with that reference already")
    refused(
        new_bill(client, pmc, reference="INV-2", due_on=TODAY - 5 * DAY),
        422,
        "a bill is due on or after its date",
    )
    wrong = new_bill(client, pmc, reference="INV-3", work_order_id=other_property)
    refused(wrong, 409)
    assert "is not for the property" in wrong.json()["detail"]
    gone = new_bill(client, pmc, reference="INV-4", work_order_id=cancelled)
    refused(gone, 409)
    assert "was cancelled" in gone.json()["detail"]


def test_recurring_bills_are_entered_each_month_once_and_stop(client, conn, staff, pmc):
    start = TODAY.replace(day=1) - timedelta(days=60)
    body = {
        "vendor_id": pmc.vendor_id,
        "account": pmc.owners[0].account,
        "reference_prefix": "LAWN",
        "amount": "75.00",
        "memo": "Lawn care",
        "day_of_month": 15,
        "starts_on": start,
    }

    made = ok(call(client, "recurring", **body))["id"]
    same = call(client, "recurring", **body)
    first = ok(call(client, "recurring/enter", on=TODAY), 200)
    again = ok(call(client, "recurring/enter", on=TODAY), 200)
    ok(call(client, f"recurring/{made}/end", ends_on=TODAY))
    ended_twice = call(client, f"recurring/{made}/end", ends_on=TODAY)
    (listed,) = ok(client.get("/api/payables/recurring"), 200)

    refused(same, 409, "this vendor has a recurring bill with that reference already")
    assert first["entered"] >= 2 and again == {"entered": 0}
    refused(ended_twice, 409, "this recurring bill has ended already")
    assert (listed["reference_prefix"], listed["day_of_month"], listed["ends_on"]) == (
        "LAWN",
        15,
        str(TODAY),
    )
    assert all(ref.startswith("LAWN-") for ref in statuses(client))
    refused(
        call(client, f"recurring/{uuid.uuid4()}/end", ends_on=TODAY), 404, "no such recurring bill"
    )


def test_owners_money_shows_what_a_draw_may_take(client, conn, staff, pmc):
    fund(conn, pmc, "1000.00")
    add_agreement(conn, pmc.pmc_id, pmc.owners[0].account, TODAY - 60 * DAY, reserve="200")
    enter_bill(
        conn,
        pmc.pmc_id,
        pmc.vendor_id,
        pmc.owners[0].account,
        "B-1",
        TODAY - DAY,
        TODAY + DAY,
        "300.00",
    )

    first, second = ok(client.get("/api/payables/owners"), 200)

    assert (first["owner"], first["balance"], first["reserve"], first["unpaid_bills"]) == (
        "Owner 1",
        "1000.00",
        "200.00",
        "300.00",
    )
    assert (first["available"], first["has_agreement"]) == ("500.00", True)
    assert (second["available"], second["has_agreement"]) == ("0.00", False)


def test_a_draw_pays_out_what_is_available_once(client, conn, staff, pmc):
    fund(conn, pmc, "1000.00")
    add_agreement(conn, pmc.pmc_id, pmc.owners[0].account, TODAY - 60 * DAY, reserve="200")
    request = uuid.uuid4()
    account = pmc.owners[0].account

    drawn = ok(call(client, "draws", request_id=request, account=account, on=TODAY))
    again = ok(call(client, "draws", request_id=request, account=account, on=TODAY))
    more = call(client, "draws", request_id=uuid.uuid4(), account=account, amount="0.01", on=TODAY)
    elsewhere = call(client, "draws", request_id=request, account=pmc.owners[1].account, on=TODAY)

    assert drawn == again == {"amount": "800.00"}
    refused(more, 409)
    assert "available to draw" in more.json()["detail"]
    refused(elsewhere, 409)
    assert balance(conn, account) == Decimal("200.00")


def test_a_contribution_is_recorded_once(client, conn, staff, pmc):
    request = uuid.uuid4()
    account = pmc.owners[0].account

    first = ok(
        call(
            client,
            "contributions",
            request_id=request,
            account=account,
            amount="500.00",
            on=TODAY,
            memo="For the roof",
        )
    )
    again = ok(
        call(
            client, "contributions", request_id=request, account=account, amount="500.00", on=TODAY
        )
    )
    other = call(
        client, "contributions", request_id=request, account=account, amount="600.00", on=TODAY
    )

    assert again == first
    refused(other, 409)
    assert balance(conn, account) == Decimal("500.00")


class Rented:
    """Owner 1's property with a lease whose $1500.00 rent was paid in the last 30 days."""

    def __init__(self, conn, pmc):
        tenant = conn.execute(
            "SELECT tenant_id FROM trust_ledger_accounts WHERE ledger_account_id = %s",
            (pmc.prepaid_rent,),
        ).fetchone()[0]
        unit = add_unit(conn, pmc.pmc_id, pmc.owners[0].property_id)
        self.lease = open_lease(conn, pmc.pmc_id, unit, TODAY - 90 * DAY, None, "1500.00", [tenant])
        rent = charge(conn, pmc.pmc_id, self.lease, TODAY - 20 * DAY, "1500.00")
        (paid,) = post(
            conn,
            pmc.pmc_id,
            f"rent-{uuid.uuid4()}",
            [(pmc.operating_cash, pmc.owners[0].account, Decimal("1500.00"))],
            NOW - 10 * DAY,
        )
        apply_payment(conn, pmc.pmc_id, rent, paid, "1500.00")


def test_management_fees_are_taken_once_a_period_for_each_agreement(client, conn, staff, pmc):
    Rented(conn, pmc)
    add_agreement(conn, pmc.pmc_id, pmc.owners[0].account, TODAY - 60 * DAY, fee_percent="10")
    period = {"period_start": TODAY - 30 * DAY, "period_end": TODAY, "on": TODAY}

    first = ok(call(client, "management-fees", **period), 200)
    again = ok(call(client, "management-fees", **period), 200)
    overlapping = ok(
        call(client, "management-fees", **(period | {"period_start": TODAY - 15 * DAY})), 200
    )
    one = ok(call(client, "management-fees", account=pmc.owners[0].account, **period), 200)

    assert [(f["owner"], f["fee"], f["refused"]) for f in first] == [("Owner 1", "150.00", None)]
    assert again == first == one
    assert overlapping[0]["fee"] is None and "overlaps" in overlapping[0]["refused"]
    assert balance(conn, pmc.pmc_income) == Decimal("150.00")


def test_the_fee_counts_a_recurring_fee_collected_with_the_rent(client, conn, staff, pmc):
    rented = Rented(conn, pmc)
    add_agreement(conn, pmc.pmc_id, pmc.owners[0].account, TODAY - 60 * DAY, fee_percent="10")
    parking = add_recurring_charge(
        conn, pmc.pmc_id, rented.lease, "40.00", TODAY - 20 * DAY, "Parking"
    )
    charge_recurring_due(conn, pmc.pmc_id, TODAY - 20 * DAY)
    (charged,) = conn.execute(
        "SELECT id FROM trust_charges WHERE recurring_charge_id = %s", (parking,)
    ).fetchone()
    (paid,) = post(
        conn,
        pmc.pmc_id,
        f"parking-{uuid.uuid4()}",
        [(pmc.operating_cash, pmc.owners[0].account, Decimal("40.00"))],
        NOW - 10 * DAY,
    )
    apply_payment(conn, pmc.pmc_id, charged, paid, "40.00")

    fees = ok(
        call(client, "management-fees", period_start=TODAY - 30 * DAY, period_end=TODAY, on=TODAY),
        200,
    )

    # 10% of the 1500.00 rent and the 40.00 parking.
    assert [(f["owner"], f["fee"]) for f in fees] == [("Owner 1", "154.00")]


def test_a_fee_refused_for_one_owner_leaves_the_others_taken(client, conn, staff, pmc):
    Rented(conn, pmc)
    add_agreement(conn, pmc.pmc_id, pmc.owners[0].account, TODAY - 60 * DAY, fee_percent="10")
    add_agreement(conn, pmc.pmc_id, pmc.owners[1].account, TODAY - 60 * DAY, minimum_fee="50")

    fees = ok(
        call(client, "management-fees", period_start=TODAY - 30 * DAY, period_end=TODAY, on=TODAY),
        200,
    )

    assert [(f["owner"], f["fee"]) for f in fees] == [("Owner 1", "150.00"), ("Owner 2", None)]
    assert "below zero" in fees[1]["refused"]
    assert balance(conn, pmc.pmc_income) == Decimal("150.00")


def test_a_leasing_fee_is_taken_once_from_an_owner_of_the_property(client, conn, staff, pmc):
    rented = Rented(conn, pmc)
    add_agreement(
        conn, pmc.pmc_id, pmc.owners[0].account, TODAY - 120 * DAY, leasing_fee_percent="50"
    )
    body = {"lease_id": rented.lease, "on": TODAY}

    taken = ok(call(client, "leasing-fees", account=pmc.owners[0].account, **body))
    again = ok(call(client, "leasing-fees", account=pmc.owners[0].account, **body))
    wrong = call(client, "leasing-fees", account=pmc.owners[1].account, **body)

    assert taken == again == {"amount": "750.00"}
    refused(wrong, 422)
    assert "not an owner's account for the lease's property" in wrong.json()["detail"]


def test_a_leasing_fee_needs_an_agreement_and_the_money(client, conn, staff, pmc):
    rented = Rented(conn, pmc)
    unagreed = call(
        client, "leasing-fees", lease_id=rented.lease, account=pmc.owners[0].account, on=TODAY
    )
    add_agreement(
        conn, pmc.pmc_id, pmc.owners[0].account, TODAY - 120 * DAY, leasing_fee_percent="100"
    )
    drawn = {"request_id": str(uuid.uuid4()), "amount": "1000.00", "on": TODAY}
    ok(call(client, "draws", account=pmc.owners[0].account, **drawn))
    too_much = call(
        client, "leasing-fees", lease_id=rented.lease, account=pmc.owners[0].account, on=TODAY
    )

    refused(unagreed, 422)
    assert "no management agreement" in unagreed.json()["detail"]
    refused(too_much, 409)


def test_another_pmcs_records_are_not_found(client, conn, staff, pmc):
    from helpers import make_pmc

    other = make_pmc(conn)
    theirs = enter_bill(
        conn, other.pmc_id, other.vendor_id, other.owners[0].account, "X-1", TODAY, TODAY, "10.00"
    )
    cases = [
        (new_bill(client, pmc, vendor_id=other.vendor_id), "no such vendor"),
        (new_bill(client, pmc, account=other.owners[0].account), "no such owner account"),
        (call(client, f"bills/{theirs}/set-aside", on=TODAY), "no such bill"),
        (call(client, f"bills/{theirs}/pay", on=TODAY), "no such bill"),
        (
            call(
                client,
                f"bills/{theirs}/decline",
                declined_on=TODAY,
                declined_by="Owner",
                reason="No",
            ),
            "no such bill",
        ),
        (
            call(client, f"bills/{theirs}/approval", approved_on=TODAY, approved_by="X"),
            "no such bill",
        ),
        (
            call(
                client, "draws", request_id=uuid.uuid4(), account=other.owners[0].account, on=TODAY
            ),
            "no such owner account",
        ),
        (
            call(
                client,
                "contributions",
                request_id=uuid.uuid4(),
                account=other.owners[0].account,
                amount="1.00",
                on=TODAY,
            ),
            "no such owner account",
        ),
        (
            call(
                client,
                "management-fees",
                account=other.owners[0].account,
                period_start=TODAY - DAY,
                period_end=TODAY,
                on=TODAY,
            ),
            "no such owner account",
        ),
        (
            call(
                client,
                "recurring",
                vendor_id=pmc.vendor_id,
                account=other.owners[0].account,
                reference_prefix="P",
                amount="1.00",
                memo="m",
                day_of_month=1,
                starts_on=TODAY,
            ),
            "no such owner account",
        ),
    ]
    for answer, detail in cases:
        refused(answer, 404, detail)


# --- the email to the owner (ADR 0004) -------------------------------------------------------


def owner_signs_in(conn, pmc, n=0):
    """Owner n's portal login, password set: the address their bill emails go to."""
    owner = pmc.owners[n].owner_id
    made = accounts.create_invite(
        conn, pmc.pmc_id, "owner", owner, f"owner{n}.{owner.hex[:6]}@example.com", None
    )
    accounts.accept_invite(conn, made.token, "a long synthetic passphrase")
    return f"owner{n}.{owner.hex[:6]}@example.com"


def bill_email(client, bill):
    return next(
        b["owner_email"]
        for b in ok(client.get("/api/payables/bills?show=all"), 200)
        if b["id"] == bill
    )


def test_a_bill_over_the_limit_emails_its_owner_after_it_commits(client, conn, staff, pmc):
    client.app.state.email = email.FakeSender()
    to = owner_signs_in(conn, pmc)
    add_agreement(conn, pmc.pmc_id, pmc.owners[0].account, TODAY - 60 * DAY, approval_limit="500")

    bill = ok(new_bill(client, pmc, "800.00"))["id"]

    assert bill_email(client, bill) == "sent"
    (sent,) = [e for e in ok(client.get("/api/payables/emails"), 200) if e["to"] == to]
    assert (sent["subject"], sent["status"], sent["error"]) == (
        "A bill waits for your approval: Vendor 1, $800.00",
        "sent",
        None,
    )
    assert conn.execute(
        "SELECT d.sender, d.error FROM trust_email_deliveries AS d"
        " JOIN trust_emails AS e ON e.id = d.email_id WHERE e.bill_id = %s",
        (bill,),
    ).fetchall() == [("fake", None)]


def test_with_no_sender_the_email_waits_in_the_outbox(client, conn, staff, pmc):
    owner_signs_in(conn, pmc)
    add_agreement(conn, pmc.pmc_id, pmc.owners[0].account, TODAY - 60 * DAY, approval_limit="500")

    bill = ok(new_bill(client, pmc, "800.00"))["id"]

    assert bill_email(client, bill) == "waiting"


def test_a_bill_refused_writes_no_email(client, conn, staff, pmc):
    client.app.state.email = email.FakeSender()
    owner_signs_in(conn, pmc)
    add_agreement(conn, pmc.pmc_id, pmc.owners[0].account, TODAY - 60 * DAY, approval_limit="500")
    ok(new_bill(client, pmc, "800.00"))

    again = new_bill(client, pmc, "900.00")  # the same reference: refused, rolled back

    refused(again, 409)
    assert conn.execute(
        "SELECT count(*) FROM trust_emails WHERE pmc_id = %s", (pmc.pmc_id,)
    ).fetchone() == (1,)


@pytest.mark.parametrize("why", ["within the limit", "no login"])
def test_no_email_when_the_bill_needs_none_or_the_owner_cannot_sign_in(
    client, conn, staff, pmc, why
):
    client.app.state.email = email.FakeSender()
    if why == "within the limit":
        owner_signs_in(conn, pmc)
    add_agreement(conn, pmc.pmc_id, pmc.owners[0].account, TODAY - 60 * DAY, approval_limit="500")

    bill = ok(new_bill(client, pmc, "300.00" if why == "within the limit" else "800.00"))["id"]

    assert bill_email(client, bill) is None


def test_recurring_bills_over_the_limit_email_their_owner(client, conn, staff, pmc):
    client.app.state.email = email.FakeSender()
    to = owner_signs_in(conn, pmc)
    add_agreement(conn, pmc.pmc_id, pmc.owners[0].account, TODAY - 400 * DAY, approval_limit="500")
    ok(
        call(
            client,
            "recurring",
            vendor_id=pmc.vendor_id,
            account=pmc.owners[0].account,
            reference_prefix="HOA",
            amount="600.00",
            memo="Association dues",
            day_of_month=1,
            starts_on=TODAY - 40 * DAY,
        )
    )

    entered = ok(call(client, "recurring/enter", on=TODAY), 200)["entered"]

    emailed = [e for e in ok(client.get("/api/payables/emails"), 200) if e["to"] == to]
    assert entered >= 1 and len(emailed) == entered
    assert {e["status"] for e in emailed} == {"sent"}


def test_an_email_given_up_on_shows_as_failed_with_why(client, conn, staff, pmc):
    owner_signs_in(conn, pmc)
    add_agreement(conn, pmc.pmc_id, pmc.owners[0].account, TODAY - 60 * DAY, approval_limit="500")
    bill = ok(new_bill(client, pmc, "800.00"))["id"]
    (email_id,) = conn.execute("SELECT id FROM trust_emails WHERE bill_id = %s", (bill,)).fetchone()
    for n in range(email.MAX_ATTEMPTS):
        conn.execute(
            "INSERT INTO trust_email_deliveries (email_id, pmc_id, sender, error, attempted_at)"
            " VALUES (%s, %s, 'smtp', 'SMTPRecipientsRefused: no such mailbox',"
            " now() + %s * interval '1 second')",
            (email_id, pmc.pmc_id, n),
        )

    (shown,) = [e for e in ok(client.get("/api/payables/emails"), 200) if e["id"] == str(email_id)]
    assert bill_email(client, bill) == "failed"
    assert (shown["status"], shown["error"]) == ("failed", "SMTPRecipientsRefused: no such mailbox")


ENDPOINTS = [
    ("get", "/api/payables/bills"),
    ("post", "/api/payables/bills"),
    ("post", f"/api/payables/bills/{uuid.uuid4()}/approval"),
    ("post", f"/api/payables/bills/{uuid.uuid4()}/decline"),
    ("post", f"/api/payables/bills/{uuid.uuid4()}/set-aside"),
    ("post", f"/api/payables/bills/{uuid.uuid4()}/pay"),
    ("get", "/api/payables/recurring"),
    ("post", "/api/payables/recurring"),
    ("post", f"/api/payables/recurring/{uuid.uuid4()}/end"),
    ("post", "/api/payables/recurring/enter"),
    ("get", "/api/payables/owners"),
    ("get", "/api/payables/emails"),
    ("post", "/api/payables/management-fees"),
    ("post", "/api/payables/leasing-fees"),
    ("post", "/api/payables/draws"),
    ("post", "/api/payables/contributions"),
]


@pytest.mark.parametrize("caller", ["nobody", "owner", "tenant"])
@pytest.mark.parametrize(("method", "path"), ENDPOINTS)
def test_only_staff_handle_bills_and_owners_money(
    client, conn, app_conn, pmc, caller, method, path
):
    if caller == "owner":
        join_as_owner(client, app_conn, pmc)
    elif caller == "tenant":
        join_as_tenant(client, conn, app_conn, pmc)

    answer = client.get(path) if method == "get" else client.post(path, json={})

    assert answer.status_code == (401 if caller == "nobody" else 403)


@pytest.mark.parametrize(
    ("path", "body"),
    [
        ("bills", {"amount": "10.00"}),
        ("draws", {"request_id": "x", "account": "a", "on": "2026-10-01"}),
        (
            "draws",
            {"request_id": str(uuid.uuid4()), "account": "a", "amount": "0", "on": "2026-10-01"},
        ),
        (
            "contributions",
            {
                "request_id": str(uuid.uuid4()),
                "account": "a",
                "amount": "1.001",
                "on": "2026-10-01",
            },
        ),
        (
            "recurring",
            {
                "vendor_id": str(uuid.uuid4()),
                "account": "a",
                "reference_prefix": "P",
                "amount": "1.00",
                "memo": "m",
                "day_of_month": 29,
                "starts_on": "2026-10-01",
            },
        ),
        (
            "management-fees",
            {"period_start": "2026-10-01", "period_end": "2026-10-01", "on": "2026-10-01"},
        ),
        ("bills/x/set-aside", {"on": "2026-10-01"}),
    ],
)
def test_bad_input_is_refused(client, staff, path, body):
    refused(client.post(f"/api/payables/{path}", json=body), 422)


@pytest.mark.parametrize(
    "path",
    [
        "bills/{bill}/set-aside",
        "bills/{bill}/pay",
        "management-fees",
        "leasing-fees",
        "draws",
        "contributions",
    ],
)
def test_money_is_recorded_no_later_than_today(client, conn, staff, pmc, path):
    bill = enter_bill(
        conn, pmc.pmc_id, pmc.vendor_id, pmc.owners[0].account, "T-1", TODAY, TODAY, "1.00"
    )
    tomorrow = str(TODAY + DAY)
    body = {
        "on": tomorrow,
        "request_id": str(uuid.uuid4()),
        "account": pmc.owners[0].account,
        "amount": "1.00",
        "period_start": str(TODAY - DAY),
        "period_end": tomorrow,
        "lease_id": str(uuid.uuid4()),
    }

    refused(
        client.post(f"/api/payables/{path.format(bill=bill)}", json=body),
        422,
        "money is recorded on the day it moved, not a later one",
    )
