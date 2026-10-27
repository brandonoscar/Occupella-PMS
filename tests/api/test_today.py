"""Staff's day at a glance through the API (src/pms/api/today.py): late rent, bills due within a
week, months not reconciled, applications waiting for a decision, offers waiting for signatures
and journal entries waiting to be posted. Each part is the work page's own answer, picked for
the day."""

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from helpers import (
    add_tenant,
    add_unit,
    apply_payment,
    approve,
    charge,
    decline_bill,
    decline_entry,
    enter_bill,
    import_statement,
    make_pmc,
    offer_lease,
    open_application_link,
    open_lease,
    post_entry,
    prepare_bank,
    propose_entry,
    record_decision,
    send_offer,
    sign_for,
    sign_offer,
    staff_named,
    submit_application,
    transfer_batch,
)
from people import join_as_owner, join_as_staff, join_as_tenant

from pms.api.today import months_ended

TODAY = datetime.now(UTC).date()
DAY = timedelta(days=1)
START = TODAY - timedelta(days=90)


def first_of(day, months_back):
    """The first day of the month months_back before day's."""
    month = day.replace(day=1)
    for _ in range(months_back):
        month = (month - DAY).replace(day=1)
    return month


def at(day):
    return datetime(day.year, day.month, day.day, tzinfo=UTC)


@pytest.fixture
def staff(client, conn, app_conn, pmc):
    return join_as_staff(client, conn, app_conn, pmc)


def lease_of(conn, pmc, name):
    prop = pmc.owners[0].property_id
    unit = add_unit(conn, pmc.pmc_id, prop, name)
    tenant = add_tenant(conn, pmc.pmc_id, prop, f"Tenant of {name}")
    return unit, open_lease(conn, pmc.pmc_id, unit, START, None, "1500.00", [tenant])


def test_the_day_shows_what_needs_someone(client, conn, staff, pmc):
    # Month-end: the operating account was reconciled through the month before last.
    approve(conn, pmc.pmc_id, pmc.operating_bank_id, at(first_of(TODAY, 2)), at(first_of(TODAY, 1)))
    # Rent: Unit 1 owes rent from ten days ago (and some due tomorrow, not late yet); Unit 2
    # paid its rent.
    unit, late = lease_of(conn, pmc, "Unit 1")
    charge(conn, pmc.pmc_id, late, TODAY - 10 * DAY, "1500.00")
    charge(conn, pmc.pmc_id, late, TODAY - 9 * DAY, "35.00", "fee")  # a fee, not rent
    charge(conn, pmc.pmc_id, late, TODAY + DAY, "1500.00")
    _, paid = lease_of(conn, pmc, "Unit 2")
    rent = charge(conn, pmc.pmc_id, paid, TODAY - 10 * DAY, "1500.00")
    (payment,) = transfer_batch(conn, [(pmc.operating_cash, pmc.owners[0].account, "1500.00")])
    apply_payment(conn, pmc.pmc_id, rent, payment, Decimal("1500.00"))
    # Bills: due in three days, past due, due in a month, and one the owner declined.
    for reference, due in (("SOON", 3), ("PAST", -5), ("LATER", 30), ("DECLINED", 2)):
        bill = enter_bill(
            conn,
            pmc.pmc_id,
            pmc.vendor_id,
            pmc.owners[0].account,
            reference,
            TODAY - 20 * DAY,
            TODAY + due * DAY,
            "120.00",
        )
    decline_bill(conn, pmc.pmc_id, bill, TODAY, "Owner 1")
    # Leasing: an application waiting and one decided; a renewal sent and not signed, one
    # signed by everyone, and one not sent.
    link = open_application_link(conn, pmc.pmc_id, unit, staff.person_id)
    waiting = submit_application(conn, pmc.pmc_id, link)
    decided = submit_application(conn, pmc.pmc_id, link)
    record_decision(conn, pmc.pmc_id, decided, staff.person_id, "declined")
    offers = [
        offer_lease(conn, pmc.pmc_id, unit, staff.person_id, START + n * DAY, renews_lease_id=late)
        for n in (100, 200, 300)
    ]
    for offer in offers[:2]:
        send_offer(conn, pmc.pmc_id, offer, staff.person_id)
    sign_offer(conn, pmc.pmc_id, offers[1], 1)

    answer = client.get("/api/today")
    day = answer.json()

    assert answer.status_code == 200, answer.text
    assert day["on"] == str(TODAY)
    assert [
        (x["unit"], x["tenants"], x["rent_past_due"], x["oldest_due_on"]) for x in day["late_rent"]
    ] == [("Unit 1", ["Tenant of Unit 1"], "1500.00", str(TODAY - 10 * DAY))]
    assert sorted(b["reference"] for b in day["bills_due"]) == ["PAST", "SOON"]
    (operating,) = day["unreconciled"]
    assert (
        operating["closed_through"],
        operating["months"],
        operating["waiting_for_approval"],
    ) == (
        str(first_of(TODAY, 1) - DAY),
        [first_of(TODAY, 1).strftime("%Y-%m")],
        None,
    )
    assert [a["id"] for a in day["applications"]] == [str(waiting)]
    assert [o["id"] for o in day["unsigned_offers"]] == [str(offers[0])]


def test_a_month_end_prepared_waits_for_approval(client, conn, staff, pmc):
    # Last month's statement is in and its month-end prepared; nobody has approved it yet.
    last_month, last_day = first_of(TODAY, 1), first_of(TODAY, 0) - DAY
    import_statement(
        conn, pmc.pmc_id, pmc.operating_bank_id, staff.person_id, last_month, last_day, []
    )
    prepare_bank(conn, pmc.pmc_id, pmc.operating_bank_id, last_day, staff.person_id)

    (operating,) = client.get("/api/today").json()["unreconciled"]

    assert (
        operating["bank_account_id"],
        operating["closed_through"],
        operating["months"],
        operating["waiting_for_approval"],
    ) == (str(pmc.operating_bank_id), None, [last_month.strftime("%Y-%m")], str(last_day))


def test_journal_entries_wait_until_posted_or_declined(client, conn, staff, pmc):
    bookkeeper = staff_named(conn, pmc.pmc_id, "Bookkeeper 1")
    move = [(pmc.operating_cash, pmc.owners[0].account, "25.00")]
    waiting, posted, declined = (
        propose_entry(conn, pmc.pmc_id, bookkeeper, TODAY, memo, move)
        for memo in ("Waiting", "Posted", "Declined")
    )
    post_entry(conn, pmc.pmc_id, posted, staff.person_id)
    decline_entry(conn, pmc.pmc_id, declined, staff.person_id)

    (entry,) = client.get("/api/today").json()["journal_waiting"]

    assert (entry["id"], entry["memo"], entry["proposed_by"], entry["lines"]) == (
        str(waiting),
        "Waiting",
        "Bookkeeper 1",
        [
            {
                "from_account": pmc.operating_cash,
                "to_account": pmc.owners[0].account,
                "amount": "25.00",
            }
        ],
    )


def test_a_quiet_day_shows_nothing(client, conn, staff, pmc):
    # Each trust bank account has its broker on file.
    for bank in (pmc.operating_bank_id, pmc.deposit_bank_id):
        sign_for(conn, pmc.pmc_id, bank, staff.person_id, "broker", on=date(2026, 1, 1))
    answer = client.get("/api/today", params={"on": "2026-10-13"})

    assert answer.status_code == 200
    assert answer.json() == {
        "on": "2026-10-13",
        "late_rent": [],
        "bills_due": [],
        "unreconciled": [],
        "applications": [],
        "unsigned_offers": [],
        "journal_waiting": [],
        "vendor_insurance": [],
        "company_fees": [],
        "signers": [],
        "renters_insurance": [],
        "deposits_to_receive": [],
        "deposits_to_return": [],
        "owner_draws": None,
    }


def test_the_day_is_the_sessions_pmc(client, conn, staff, pmc):
    other = make_pmc(conn)
    _, theirs = lease_of(conn, other, "Unit 1")
    charge(conn, other.pmc_id, theirs, TODAY - 10 * DAY, "1500.00")

    assert client.get("/api/today").json()["late_rent"] == []


@pytest.mark.parametrize("caller", ["nobody", "owner", "tenant"])
def test_only_staff_see_the_day(client, conn, app_conn, pmc, caller):
    if caller == "owner":
        join_as_owner(client, app_conn, pmc)
    elif caller == "tenant":
        join_as_tenant(client, conn, app_conn, pmc)

    assert client.get("/api/today").status_code == (401 if caller == "nobody" else 403)


@pytest.mark.parametrize("on", ["not a day", "2026-02-30"])
def test_a_day_that_is_not_one_is_refused(client, staff, on):
    assert client.get("/api/today", params={"on": on}).status_code == 422


def test_months_ended_run_from_the_first_open_month_to_the_last_one_ended():
    assert months_ended(date(2026, 9, 1), date(2026, 10, 13)) == ["2026-09"]
    assert months_ended(date(2026, 9, 15), date(2026, 10, 1)) == ["2026-09"]
    assert months_ended(date(2025, 11, 1), date(2026, 2, 28)) == ["2025-11", "2025-12", "2026-01"]
    assert months_ended(date(2026, 10, 1), date(2026, 10, 31)) == []
