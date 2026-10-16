"""A lease's rent changed from the first of a month through the API (src/pms/api/rent.py): after
written notice of a full month, for a lease with an end date only as its terms allow; the rent
page and the tenant's portal show the rent in force and the change to come."""

import json
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from helpers import add_unit, make_pmc, open_lease
from people import join_as_owner, join_as_staff, join_as_tenant

from pms.api import rent

TODAY = datetime.now(UTC).date()
FIRST = TODAY.replace(day=1)


def months_from(day, n):
    """The first of the month n months from day's month."""
    year, month = divmod(day.year * 12 + day.month - 1 + n, 12)
    return date(year, month + 1, 1)


NEXT_MONTH, IN_TWO = months_from(TODAY, 1), months_from(TODAY, 2)


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
    return open_lease(conn, pmc.pmc_id, unit, months_from(TODAY, -3), None, "1500.00", [tenant])


def ok(answer, status=200):
    assert answer.status_code == status, answer.text
    return answer.json()


def refused(answer, status, detail=None):
    assert answer.status_code == status, answer.text
    if detail is not None:
        assert answer.json()["detail"] == detail


def change(client, lease, **body):
    body = {
        "effective_on": IN_TWO,
        "monthly_rent": "1575.00",
        "notice_given_on": TODAY,
        "served_by": "mail",
    } | body
    return client.post(
        f"/api/rent/leases/{lease}/rent-changes", json=json.loads(json.dumps(body, default=str))
    )


def the_lease(client, lease):
    return ok(client.get(f"/api/leases/{lease}"))


def test_staff_change_a_month_to_month_rent_after_a_full_months_notice(
    client, other_client, conn, app_conn, staff, pmc, lease, monkeypatch
):
    made = ok(change(client, lease), 201)

    shown = the_lease(client, lease)
    assert shown["monthly_rent"] == "1500.00"
    assert shown["rent_changes"] == [
        {
            "id": made["id"],
            "effective_on": str(IN_TWO),
            "monthly_rent": "1575.00",
            "notice_given_on": str(TODAY),
            "served_by": "mail",
            "lease_terms": None,
        }
    ]
    # From its month on, the lease's page shows it in force.
    with monkeypatch.context() as later:
        later.setattr(rent, "today", lambda: IN_TWO)
        assert the_lease(client, lease)["monthly_rent"] == "1575.00"
    # The tenant sees the change to come in their portal.
    join_as_tenant(other_client, conn, app_conn, pmc)
    (mine,) = ok(other_client.get("/api/tenant/leases"))
    assert (mine["monthly_rent"], mine["rent_changes"]) == (
        "1500.00",
        [{"effective_on": str(IN_TWO), "monthly_rent": "1575.00", "notice_given_on": str(TODAY)}],
    )
    # The audit log names who recorded it.
    assert conn.execute(
        "SELECT recorded_by FROM trust_rent_changes WHERE lease_id = %s", (lease,)
    ).fetchone() == (staff.person_id,)


def test_less_than_a_full_months_notice_is_refused_in_words(client, staff, lease):
    # From this month's first, notice was due by the first of last month.
    refused(
        change(client, lease, effective_on=FIRST),
        422,
        f"written notice of a full month comes first: for a change from {FIRST},"
        f" give it by {months_from(TODAY, -1)}",
    )
    refused(
        change(client, lease, notice_given_on=TODAY + timedelta(days=1)),
        422,
        "notice is recorded once it was given, not before",
    )
    refused(
        change(client, lease, effective_on=IN_TWO + timedelta(days=4)),
        422,
        "a rent change starts on the first of a month",
    )


def test_a_lease_with_an_end_date_names_the_term_that_allows_it(client, conn, staff, pmc):
    tenant = conn.execute(
        "SELECT tenant_id FROM trust_ledger_accounts WHERE ledger_account_id = %s",
        (pmc.prepaid_rent,),
    ).fetchone()[0]
    unit = add_unit(conn, pmc.pmc_id, pmc.owners[0].property_id, "Unit 2")
    fixed = open_lease(
        conn, pmc.pmc_id, unit, months_from(TODAY, -3), months_from(TODAY, 9), "1500.00", [tenant]
    )

    refused(
        change(client, fixed),
        409,
        "a lease with an end date changes its rent during it only as its terms allow;"
        " name the term",
    )
    ok(change(client, fixed, lease_terms="Section 4: rent may change on 30 days' notice"), 201)


def test_a_month_charged_already_or_changed_already_is_refused(client, conn, staff, pmc, lease):
    ok(change(client, lease), 201)
    refused(
        change(client, lease, monthly_rent="1600.00"),
        409,
        "this lease's rent changes from that month already",
    )
    conn.execute(
        "INSERT INTO trust_charges (pmc_id, lease_id, due_on, kind, amount)"
        " VALUES (%s, %s, %s, 'rent', 1575.00)",
        (pmc.pmc_id, lease, months_from(TODAY, 3)),
    )
    refused(
        change(client, lease, effective_on=months_from(TODAY, 3)),
        409,
        f"rent from {months_from(TODAY, 3)} is charged already; change it from a later month",
    )


def test_only_the_pmcs_staff_change_rent(client, other_client, conn, app_conn, pmc, lease):
    refused(change(client, lease), 401)
    join_as_owner(client, app_conn, pmc)
    refused(change(client, lease), 403)
    other = make_pmc(conn)
    join_as_staff(other_client, conn, app_conn, other, name="Staff 2")
    refused(change(other_client, lease), 404)


@pytest.mark.parametrize(
    "body",
    [
        {"monthly_rent": "0"},
        {"monthly_rent": "1.001"},
        {"served_by": "carrier pigeon"},
        {"lease_terms": " "},
        {"effective_on": "soon"},
        {"notice_given_on": None},
    ],
)
def test_bad_input_is_refused(client, conn, staff, lease, body):
    refused(change(client, lease, **body), 422)
    assert conn.execute(
        "SELECT count(*) FROM trust_rent_changes WHERE lease_id = %s", (lease,)
    ).fetchone() == (0,)


def test_the_rent_run_charges_the_new_rent_from_its_month(client, conn, staff, pmc, lease):
    ok(change(client, lease), 201)
    for due in (NEXT_MONTH, IN_TWO):
        conn.execute("SELECT trust_charge_rent_due(%s, %s)", (pmc.pmc_id, due))

    assert conn.execute(
        "SELECT due_on, amount FROM trust_charges WHERE lease_id = %s AND kind = 'rent'"
        " ORDER BY due_on",
        (lease,),
    ).fetchall() == [(NEXT_MONTH, Decimal("1500.00")), (IN_TWO, Decimal("1575.00"))]
