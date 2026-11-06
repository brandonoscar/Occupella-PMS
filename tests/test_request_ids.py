"""What staff record under their panel's request id (docs/edge-cases.md C-025, C-026): a charge or
a recurring charge sent twice at once, two tabs or a resend while the first still runs, waits on
the request id's lock and is made once (src/pms/api/rent.py made_before). Every name here is
synthetic."""

import uuid
from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import psycopg
import pytest
from helpers import add_unit, in_background, make_pmc, open_lease, wait_until_blocked

from pms.api import rent

DAY = date(2026, 3, 5)


@pytest.fixture
def pmc(conn):
    return make_pmc(conn)


def a_lease(conn, pmc):
    tenant = conn.execute(
        "SELECT tenant_id FROM trust_ledger_accounts WHERE ledger_account_id = %s",
        (pmc.prepaid_rent,),
    ).fetchone()[0]
    unit = add_unit(conn, pmc.pmc_id, pmc.owners[0].property_id)
    return open_lease(conn, pmc.pmc_id, unit, date(2026, 1, 1), None, "1200.00", [tenant])


def charge(connection, pmc, lease, request):
    return rent.add_charge(
        SimpleNamespace(pmc_id=pmc.pmc_id),
        connection,
        lease,
        rent.NewCharge(
            request_id=request, kind="fee", amount=Decimal("75.00"), due_on=DAY, memo="Lockout"
        ),
    ).id


def recurring(connection, pmc, lease, request):
    return rent.add_recurring_charge(
        SimpleNamespace(pmc_id=pmc.pmc_id),
        connection,
        lease,
        rent.NewRecurringCharge(
            request_id=request, kind="fee", amount=Decimal("35.00"), starts_on=DAY, memo="Pet rent"
        ),
    ).id


@pytest.mark.timing
@pytest.mark.parametrize(
    ("make", "table"), [(charge, "trust_charges"), (recurring, "trust_recurring_charges")]
)
def test_one_request_sent_twice_at_once_is_made_once(conn, pmc, database_url, make, table):
    lease = a_lease(conn, pmc)
    request = uuid.uuid4()
    with psycopg.connect(database_url) as first:
        made = make(first, pmc, lease, request)  # holds the request id's lock until it commits

        def second(worker):
            with worker.transaction():
                return make(worker, pmc, lease, request)

        thread, pid, outcome = in_background(database_url, second)
        wait_until_blocked(conn, pid)
        first.commit()
        thread.join(timeout=30)

    assert outcome == {"result": made}
    assert conn.execute(
        f"SELECT count(*) FROM {table} WHERE lease_id = %s",  # noqa: S608
        (lease,),
    ).fetchone() == (1,)
