"""Prepaid rent used by itself (migration 0117, src/pms/api/rent.py; the founder's review q031): a
company turns it on in Company setup, and then the nightly run and an admin's Run rent now use
each lease's tenants' prepaid rent for what the lease owes once its new charges post, as Use
prepaid rent does, once a day per lease. A lease on a payment hold, or at a property whose owner's
account staff must choose, is left for staff. Off until a company turns it on."""

import json
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import psycopg
import pytest
from helpers import (
    add_tenant,
    add_unit,
    balance,
    bank_sums,
    charge,
    hold_payments,
    make_pmc,
    open_account,
    open_lease,
)
from people import join_as_owner, join_as_staff, join_as_tenant

from pms import nightly
from pms.api import rent

TODAY = datetime.now(UTC).date()
DAY = timedelta(days=1)
FIRST = TODAY.replace(day=1)
START = FIRST - 60 * DAY
URL = "http://localhost:8000"


@pytest.fixture
def staff(client, conn, app_conn, pmc):
    return join_as_staff(client, conn, app_conn, pmc)


def ok(answer, status=200):
    assert answer.status_code == status, answer.text
    return None if status == 204 else answer.json()


def refused(answer, status):
    assert answer.status_code == status, answer.text


def post(client, path, **body):
    return client.post(path, json=json.loads(json.dumps(body, default=str)))


def turn(client, automatic):
    return post(client, "/api/rent/prepaid/automatic", automatic=automatic)


class Lease:
    """A tenant of their own on a lease of a unit since START at $1200.00 a month, at the PMC's
    first property unless another is named."""

    def __init__(self, conn, pmc, unit, prop=None):
        prop = prop or pmc.owners[0].property_id
        self.pmc_id = pmc.pmc_id
        self.tenant = add_tenant(conn, pmc.pmc_id, prop, f"Tenant {unit}")
        unit_id = add_unit(conn, pmc.pmc_id, prop, f"Unit {unit}")
        self.id = open_lease(conn, pmc.pmc_id, unit_id, START, None, "1200.00", [self.tenant])

    def pay(self, conn, amount):
        """A payment staff record today: what the lease owes, the rest prepaid rent."""
        with conn.transaction():
            rent.record_payment(conn, self.pmc_id, self.id, uuid.uuid4(), Decimal(amount), TODAY)

    def prepaid(self, conn):
        (held,) = conn.execute(
            "SELECT coalesce(sum(b.balance), 0) FROM trust_ledger_accounts AS a"
            " JOIN pgledger_accounts AS b ON b.id = a.ledger_account_id"
            " WHERE a.tenant_id = %s AND a.kind = 'prepaid_rent'",
            (self.tenant,),
        ).fetchone()
        return held

    def owed(self, conn):
        return rent.owed(conn, self.id)


def turn_on(conn, pmc):
    conn.execute(
        "INSERT INTO trust_prepaid_rent_settings (pmc_id, automatic) VALUES (%s, true)",
        (pmc.pmc_id,),
    )


def settings_rows(conn, pmc):
    return conn.execute(
        "SELECT count(*) FROM trust_prepaid_rent_settings WHERE pmc_id = %s", (pmc.pmc_id,)
    ).fetchone()[0]


def test_it_is_off_until_staff_turn_it_on_naming_who_did(client, conn, staff, pmc):
    assert ok(client.get("/api/rent/prepaid/automatic")) == {
        "automatic": False,
        "changed_at": None,
        "changed_by": None,
    }

    on = ok(turn(client, True))
    again = ok(turn(client, True))
    off = ok(turn(client, False))

    assert (on["automatic"], on["changed_by"]) == (True, "Staff 1")
    assert again == on  # saving what holds already writes nothing
    assert (off["automatic"], off["changed_by"]) == (False, "Staff 1")
    assert ok(client.get("/api/rent/prepaid/automatic")) == off
    assert settings_rows(conn, pmc) == 2
    assert conn.execute(
        "SELECT count(*) FROM trust_audit_log WHERE table_name = 'trust_prepaid_rent_settings'"
        " AND staff_id = %s",
        (staff.person_id,),
    ).fetchone() == (2,)


@pytest.mark.parametrize("body", [{}, {"automatic": None}, {"automatic": "maybe"}, {"on": True}])
def test_bad_input_is_refused(client, conn, staff, pmc, body):
    refused(client.post("/api/rent/prepaid/automatic", json=body), 422)
    assert settings_rows(conn, pmc) == 0


@pytest.mark.parametrize("caller", ["nobody", "owner", "tenant"])
def test_only_staff_see_and_change_it(client, conn, app_conn, pmc, caller):
    if caller == "owner":
        join_as_owner(client, app_conn, pmc)
    elif caller == "tenant":
        join_as_tenant(client, conn, app_conn, pmc)
    status = 401 if caller == "nobody" else 403
    refused(client.get("/api/rent/prepaid/automatic"), status)
    refused(turn(client, True), status)
    assert settings_rows(conn, pmc) == 0


def test_a_custom_role_without_rent_cant_change_it(
    client, other_client, conn, app_conn, staff, pmc
):
    clerk = join_as_staff(other_client, conn, app_conn, pmc, name="Clerk 2", roles=[])
    role = ok(client.post("/api/custom-roles", json={"name": "Books", "areas": ["bills"]}), 201)
    ok(
        client.post(
            f"/api/custom-roles/{role['id']}/staff",
            json={"staff_id": str(clerk.person_id), "granted": True},
        ),
        204,
    )

    refused(turn(other_client, True), 403)
    ok(other_client.get("/api/rent/prepaid/automatic"))  # reading stays open
    assert settings_rows(conn, pmc) == 0


def test_run_rent_now_uses_prepaid_rent_for_the_new_rent_once_it_is_on(
    client, conn, app_conn, staff, pmc
):
    lease = Lease(conn, pmc, 1)
    lease.pay(conn, "300.00")  # nothing owed yet: all of it prepaid rent

    off = ok(post(client, "/api/rent/run", due_on=FIRST, late_fees_as_of=TODAY))
    assert (off["rents"], off["prepaid_used"]) == (1, 0)
    assert (lease.owed(conn), lease.prepaid(conn)) == (Decimal("1200.00"), Decimal("300.00"))

    ok(turn(client, True))
    on = ok(post(client, "/api/rent/run", due_on=FIRST, late_fees_as_of=TODAY))

    assert (on["rents"], on["prepaid_used"]) == (0, 1)
    assert (lease.owed(conn), lease.prepaid(conn)) == (Decimal("900.00"), Decimal(0))
    assert balance(conn, pmc.owners[0].account) == Decimal("300.00")
    assert set(bank_sums(conn, pmc.pmc_id).values()) == {Decimal(0)}
    (kind,) = conn.execute(
        "SELECT DISTINCT t.metadata ->> 'kind' FROM pgledger_transfers AS t"
        " WHERE t.metadata ->> 'lease_id' = %s AND t.metadata ->> 'key' LIKE 'prepaid/%%'",
        (str(lease.id),),
    ).fetchone()
    assert kind == "prepaid rent used"  # as Use prepaid rent records it, matched to the rent
    assert conn.execute(
        "SELECT sum(p.amount) FROM trust_charge_payments AS p"
        " JOIN trust_charges AS c ON c.id = p.charge_id WHERE c.lease_id = %s",
        (lease.id,),
    ).fetchone() == (Decimal("300.00"),)


def test_a_lease_uses_its_prepaid_rent_once_a_day(conn, app_conn, pmc):
    lease = Lease(conn, pmc, 1)
    lease.pay(conn, "2000.00")
    charge(conn, pmc.pmc_id, lease.id, FIRST, "1200.00")
    turn_on(conn, pmc)

    assert rent.use_prepaid_by_itself(app_conn, pmc.pmc_id, TODAY - DAY) == 1
    charge(conn, pmc.pmc_id, lease.id, TODAY, "100.00", "fee")
    assert rent.use_prepaid_by_itself(app_conn, pmc.pmc_id, TODAY - DAY) == 0  # once that day
    assert (lease.owed(conn), lease.prepaid(conn)) == (Decimal("100.00"), Decimal("800.00"))

    assert rent.use_prepaid_by_itself(app_conn, pmc.pmc_id, TODAY) == 1
    assert (lease.owed(conn), lease.prepaid(conn)) == (Decimal(0), Decimal("700.00"))
    assert rent.use_prepaid_by_itself(app_conn, pmc.pmc_id, TODAY) == 0  # nothing owed


def test_a_lease_on_hold_or_whose_owners_account_staff_choose_is_left_for_staff(
    conn, app_conn, pmc
):
    second = pmc.owners[1].property_id
    held, chosen, paid = Lease(conn, pmc, 1), Lease(conn, pmc, 2, second), Lease(conn, pmc, 3)
    for lease in (held, chosen, paid):
        lease.pay(conn, "500.00")
        charge(conn, pmc.pmc_id, lease.id, FIRST, "1200.00")
    hold_payments(conn, pmc.pmc_id, held.id, FIRST)
    # Another owner's account at the second property: staff choose which one is paid.
    open_account(
        conn, pmc.pmc_id, pmc.operating_bank_id, "owner_property", pmc.owners[0].owner_id, second
    )
    turn_on(conn, pmc)

    assert rent.use_prepaid_by_itself(app_conn, pmc.pmc_id, TODAY) == 1

    for lease in (held, chosen):
        assert (lease.owed(conn), lease.prepaid(conn)) == (Decimal("1200.00"), Decimal("500.00"))
    assert (paid.owed(conn), paid.prepaid(conn)) == (Decimal("700.00"), Decimal(0))
    assert set(bank_sums(conn, pmc.pmc_id).values()) == {Decimal(0)}


def test_the_nightly_run_uses_it_for_its_company_only(conn, app_conn, pmc):
    ours, theirs = Lease(conn, pmc, 1), Lease(conn, make_pmc(conn), 1)
    for lease in (ours, theirs):
        lease.pay(conn, "400.00")
    turn_on(conn, pmc)
    other = theirs.pmc_id

    mine = nightly.run_pmc(app_conn, pmc.pmc_id, FIRST, URL)
    theirs_ran = nightly.run_pmc(app_conn, other, FIRST, URL)

    assert (mine.rents, mine.prepaid_used) == (1, 1)
    assert (theirs_ran.rents, theirs_ran.prepaid_used) == (1, 0)  # never turned on
    assert (ours.owed(conn), ours.prepaid(conn)) == (Decimal("800.00"), Decimal(0))
    assert (theirs.owed(conn), theirs.prepaid(conn)) == (Decimal("1200.00"), Decimal("400.00"))


def test_the_settings_are_append_only(conn, pmc):
    turn_on(conn, pmc)
    for change in (
        "UPDATE trust_prepaid_rent_settings SET automatic = false",
        "DELETE FROM trust_prepaid_rent_settings",
    ):
        with pytest.raises(psycopg.errors.RestrictViolation, match="append-only"):
            conn.execute(change)
    assert settings_rows(conn, pmc) == 1
