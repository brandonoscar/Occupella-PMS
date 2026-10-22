"""The security deposit a signed lease offer asked (the founder's review q036): asking holds
nothing, so the lease page, the tenant portal and the Dashboard say what the signed lease asked,
what is held of it for the lease's tenants and what is still to receive, until staff record it
(rent.deposits_asked). A pet or key deposit is not the security deposit, and a lease whose
deposits were returned asks nothing more. Every name is synthetic."""

import json
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from helpers import (
    add_staff,
    add_unit,
    complete_offer,
    make_pmc,
    offer_lease,
    open_lease,
    send_offer,
    sign_offer,
)
from people import join_as_staff, join_as_tenant

TODAY = datetime.now(UTC).date()
DAY = timedelta(days=1)


@pytest.fixture
def staff(client, conn, app_conn, pmc):
    return join_as_staff(client, conn, app_conn, pmc)


def ok(answer, status=200):
    assert answer.status_code == status, answer.text
    return answer.json()


def tenant_of(conn, pmc):
    return conn.execute(
        "SELECT tenant_id FROM trust_ledger_accounts WHERE ledger_account_id = %s",
        (pmc.prepaid_rent,),
    ).fetchone()[0]


def renewed(conn, pmc, deposit="1500.00", unit_name="Unit 7", ends_on=None):
    """Tenant 1's lease of a unit to the 30th day from today, renewed from the day after by a
    signed offer asking a security deposit (None: asking none). The renewal keeps the lease's
    tenants."""
    unit = add_unit(conn, pmc.pmc_id, pmc.owners[0].property_id, unit_name)
    lease = open_lease(
        conn,
        pmc.pmc_id,
        unit,
        TODAY - 90 * DAY,
        TODAY + 30 * DAY,
        "1200.00",
        [tenant_of(conn, pmc)],
    )
    leasing = add_staff(conn, pmc.pmc_id, f"Leasing {unit_name}")
    offer = offer_lease(
        conn,
        pmc.pmc_id,
        unit,
        leasing,
        TODAY + 31 * DAY,
        rent="1250.00",
        ends_on=ends_on,
        renews_lease_id=lease,
        security_deposit=deposit,
    )
    send_offer(conn, pmc.pmc_id, offer, leasing)
    sign_offer(conn, pmc.pmc_id, offer, 1)
    return complete_offer(conn, pmc.pmc_id, offer, leasing)


def deposit(client, tenant, amount, kind="security"):
    return ok(
        client.post(
            "/api/rent/deposits",
            json=json.loads(
                json.dumps(
                    {
                        "request_id": uuid.uuid4(),
                        "tenant_id": tenant,
                        "amount": amount,
                        "received_on": TODAY,
                        "kind": kind,
                    },
                    default=str,
                )
            ),
        ),
        201,
    )


def asked(client, lease):
    return ok(client.get(f"/api/leases/{lease}"))["deposit_asked"]


def waiting(client):
    return [
        (d["lease_id"], d["asked"], d["to_receive"])
        for d in ok(client.get("/api/today"))["deposits_to_receive"]
    ]


def test_the_lease_says_what_its_signed_offer_asked_until_staff_record_it(client, conn, staff, pmc):
    lease = renewed(conn, pmc)
    tenant = tenant_of(conn, pmc)

    assert asked(client, lease) == {"asked": "1500.00", "held": "0.00", "to_receive": "1500.00"}
    assert waiting(client) == [(str(lease), "1500.00", "1500.00")]

    deposit(client, tenant, "1000.00")
    deposit(client, tenant, "200.00", kind="pet")  # a pet deposit is not the security deposit
    assert asked(client, lease) == {"asked": "1500.00", "held": "1000.00", "to_receive": "500.00"}
    assert waiting(client) == [(str(lease), "1500.00", "500.00")]

    deposit(client, tenant, "500.00")
    assert asked(client, lease) == {"asked": "1500.00", "held": "1500.00", "to_receive": "0.00"}
    assert waiting(client) == []
    # Nothing was held by asking: the deposits recorded are what the security deposit account holds.
    assert conn.execute(
        "SELECT sum(b.balance) FROM trust_ledger_accounts AS a"
        " JOIN pgledger_accounts AS b ON b.id = a.ledger_account_id"
        " WHERE a.tenant_id = %s AND a.kind = 'tenant_deposit'",
        (tenant,),
    ).fetchone() == (1700,)


def test_more_held_than_asked_leaves_nothing_to_receive(client, conn, staff, pmc):
    lease = renewed(conn, pmc, deposit="300.00")
    deposit(client, tenant_of(conn, pmc), "450.00")

    assert asked(client, lease) == {"asked": "300.00", "held": "450.00", "to_receive": "0.00"}


def test_a_lease_whose_offer_asked_none_or_with_no_offer_says_nothing(client, conn, staff, pmc):
    renewal = renewed(conn, pmc, deposit=None)
    first = conn.execute(
        "SELECT renews_lease_id FROM trust_lease_offers AS o"
        " JOIN trust_lease_offer_completions AS c ON c.offer_id = o.id WHERE c.lease_id = %s",
        (renewal,),
    ).fetchone()[0]

    assert asked(client, renewal) is None
    assert asked(client, first) is None
    assert waiting(client) == []


def test_the_dashboard_lists_its_own_companys_leases_until_they_end(client, conn, staff, pmc):
    ours = renewed(conn, pmc, ends_on=TODAY + 400 * DAY)
    renewed(conn, make_pmc(conn))  # another company's

    assert waiting(client) == [(str(ours), "1500.00", "1500.00")]
    last = (TODAY + 400 * DAY).isoformat()
    after = (TODAY + 401 * DAY).isoformat()
    assert len(ok(client.get(f"/api/today?on={last}"))["deposits_to_receive"]) == 1
    assert ok(client.get(f"/api/today?on={after}"))["deposits_to_receive"] == []


def test_the_tenant_sees_what_their_signed_lease_asked(client, other_client, conn, app_conn, pmc):
    lease = renewed(conn, pmc)
    join_as_tenant(other_client, conn, app_conn, pmc)

    leases = {row["lease_id"]: row for row in ok(other_client.get("/api/tenant/leases"))}

    assert leases[str(lease)]["deposit_asked"] == {
        "asked": "1500.00",
        "held": "0.00",
        "to_receive": "1500.00",
    }
    assert [row["deposit_asked"] for key, row in leases.items() if key != str(lease)] == [None]


def test_only_staff_read_the_dashboard(client, conn, app_conn, pmc):
    assert client.get("/api/today").status_code == 401
    join_as_tenant(client, conn, app_conn, pmc)
    assert client.get("/api/today").status_code == 403
