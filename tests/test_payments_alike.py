"""A payment like one recorded (docs/edge-cases.md H-008, U-138): staff's Receive payment refuses
a tenant's payment of the amount received the same day as one already recorded to the lease
under another request id, unless staff say it is another payment (rent.refuse_alike). Two staff
entering the same check from two tabs at once wait for each other on the lease's lock, so the
second sees the first. Every name here is synthetic."""

import uuid
from datetime import date
from decimal import Decimal

import psycopg
import pytest
from fastapi import HTTPException
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


def record(connection, pmc, lease, request=None, amount="450.00", alike_refused=True, **how):
    return rent.record_payment(
        connection,
        pmc.pmc_id,
        lease,
        request or uuid.uuid4(),
        Decimal(amount),
        DAY,
        alike_refused=alike_refused,
        **how,
    )


def payments(conn, lease):
    return conn.execute(
        "SELECT count(*) FROM pgledger_transfers WHERE metadata ->> 'kind' = 'tenant payment'"
        " AND metadata ->> 'lease_id' = %s",
        (str(lease),),
    ).fetchone()[0]


def test_a_payment_like_one_recorded_is_refused_unless_staff_say_it_is_another(conn, pmc):
    lease = a_lease(conn, pmc)
    first = uuid.uuid4()
    record(conn, pmc, lease, first)

    with pytest.raises(HTTPException) as refused:
        record(conn, pmc, lease)
    record(conn, pmc, lease, first)  # a retry of the first returns it
    record(conn, pmc, lease, alike_refused=False)  # staff said it is another payment

    assert refused.value.status_code == 409
    assert "is recorded on this lease already" in str(refused.value.detail)
    assert payments(conn, lease) == 2


def test_a_payment_alike_but_by_another_way_or_check_number_is_its_own(conn, pmc):
    lease = a_lease(conn, pmc)
    record(conn, pmc, lease, method="check", reference="Check 2101")
    record(conn, pmc, lease, method="cash")  # another way
    record(conn, pmc, lease, method="check", reference="Check 2102")  # another check
    # The same check, its number written another way, or none given: alike.
    for how in ({"method": "check", "reference": " check 2101 "}, {"method": "check"}, {}):
        with pytest.raises(HTTPException):
            record(conn, pmc, lease, **how)
    assert payments(conn, lease) == 3


@pytest.mark.timing
def test_the_same_check_from_two_tabs_at_once_is_recorded_once(conn, pmc, database_url):
    lease = a_lease(conn, pmc)
    with psycopg.connect(database_url) as first:
        record(first, pmc, lease)  # holds the lease's lock until it commits

        def second(worker):
            with worker.transaction():
                return record(worker, pmc, lease)

        thread, pid, outcome = in_background(database_url, second)
        wait_until_blocked(conn, pid)
        first.commit()
        thread.join(timeout=30)

    assert isinstance(outcome.get("error"), HTTPException), outcome
    assert outcome["error"].status_code == 409
    assert payments(conn, lease) == 1
