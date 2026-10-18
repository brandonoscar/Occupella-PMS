"""A tenant's payment receipt (src/pms/api/receipts.py): one PDF per payment, for staff from the
lease's ledger and for the tenant in the portal, saying who paid, for which home, when, how much
and how, what it paid of each charge, what was held as prepaid rent and whether it came back.
Every name and address here is synthetic."""

import io
import json
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from helpers import add_nsf_fee_policy, add_tenant, add_unit, charge, make_pmc, open_lease
from people import join_as_owner, join_as_staff, join_as_tenant, tenant_of
from pypdf import PdfReader

from pms.api import rent

TODAY = datetime.now(UTC).date()
DAY = timedelta(days=1)
START = TODAY - timedelta(days=60)


def text(answer):
    assert answer.status_code == 200, answer.text
    assert answer.headers["content-type"] == "application/pdf"
    return " ".join(page.extract_text() for page in PdfReader(io.BytesIO(answer.content)).pages)


def us(day):
    return f"{day:%m/%d/%Y}"


@pytest.fixture
def lease(conn, pmc):
    unit = add_unit(conn, pmc.pmc_id, pmc.owners[0].property_id, name="Unit 4")
    made = open_lease(conn, pmc.pmc_id, unit, START, None, "1500.00", [tenant_of(conn, pmc)])
    charge(conn, pmc.pmc_id, made, START, "1500.00", memo="Rent")
    charge(conn, pmc.pmc_id, made, START + 5 * DAY, "50.00", kind="fee", memo="Late fee")
    return made


def pay(client, lease, amount="1600.00", **more):
    answer = client.post(
        f"/api/rent/leases/{lease}/payments",
        json=json.loads(
            json.dumps(
                {"request_id": uuid.uuid4(), "amount": amount, "received_on": TODAY, **more},
                default=str,
            )
        ),
    )
    assert answer.status_code == 201, answer.text
    return answer.json()["transfers"][0]


def test_staff_print_a_payments_receipt(client, conn, app_conn, pmc, lease):
    join_as_staff(client, conn, app_conn, pmc)
    ok = client.post(
        "/api/company",
        json={
            "name": "Synthetic Property Co",
            "address": {
                "address_line_1": "100 Example Street",
                "city": "Sampletown",
                "state": "ZZ",
                "postal_code": "00000",
            },
        },
    )
    assert ok.status_code == 200, ok.text
    transfer = pay(client, lease, method="cashier's check", reference="No. 8")

    answer = client.get(f"/api/rent/payments/{transfer}/receipt")
    shown = text(answer)
    assert answer.headers["content-disposition"] == (
        f'attachment; filename="receipt-{TODAY.isoformat()}.pdf"'
    )
    for part in (
        "Payment receipt",
        "Tenant 1",
        "Property 1, Unit 4",
        us(TODAY),
        "$1,600.00",
        "Cashier's check No. 8",
        f"Rent, due {us(START)}: $1,500.00",
        f"Late fee, due {us(START + 5 * DAY)}: $50.00",
        "Held as prepaid rent",
        "$50.00",
        "Synthetic Property Co, 100 Example Street, Sampletown, ZZ 00000",
        transfer,
    ):
        assert part in shown, part
    assert "Returned" not in shown


def test_a_bounced_payments_receipt_says_it_came_back(client, conn, app_conn, pmc, lease):
    join_as_staff(client, conn, app_conn, pmc)
    add_nsf_fee_policy(conn, pmc.pmc_id, pmc.owners[0].property_id, START, "0")
    transfer = pay(client, lease, amount="1550.00")
    bounced = client.post(f"/api/rent/payments/{transfer}/bounce", json={"on": str(TODAY)})
    assert bounced.status_code == 201, bounced.text
    assert f"This payment came back unpaid on {us(TODAY)}." in text(
        client.get(f"/api/rent/payments/{transfer}/receipt")
    )


def test_a_tenant_downloads_the_receipts_of_their_own_payments(
    client, other_client, conn, app_conn, pmc, lease
):
    join_as_staff(other_client, conn, app_conn, pmc)
    transfer = pay(other_client, lease, amount="1500.00", method="check", reference="Check 101")
    join_as_tenant(client, conn, app_conn, pmc)

    [mine] = client.get("/api/tenant/leases").json()
    assert [(p["transfer_id"], p["amount"], p["reference"]) for p in mine["payments"]] == [
        (transfer, "1500.00", "Check 101")
    ]
    assert "Check Check 101" in text(client.get(f"/api/tenant/payments/{transfer}/receipt"))


def test_a_payment_not_theirs_reads_as_not_found(client, other_client, conn, app_conn, pmc, lease):
    # Another tenant of the same company pays on a lease of their own.
    join_as_staff(other_client, conn, app_conn, pmc)
    neighbour = add_tenant(conn, pmc.pmc_id, pmc.owners[0].property_id, "Tenant 9")
    unit = add_unit(conn, pmc.pmc_id, pmc.owners[0].property_id, name="Unit 9")
    theirs = open_lease(conn, pmc.pmc_id, unit, START, None, "900.00", [neighbour])
    charge(conn, pmc.pmc_id, theirs, START, "900.00")
    transfer = pay(other_client, theirs, amount="900.00")
    join_as_tenant(client, conn, app_conn, pmc)  # Tenant 1, not on that lease
    answer = client.get(f"/api/tenant/payments/{transfer}/receipt")
    assert answer.status_code == 404 and answer.json()["detail"] == "no such payment of yours"
    assert client.get("/api/tenant/payments/not-a-transfer/receipt").status_code == 404
    # Nor does their payment show on Tenant 1's leases.
    assert all(
        p["transfer_id"] != transfer
        for x in client.get("/api/tenant/leases").json()
        for p in x["payments"]
    )


def test_staff_find_no_receipt_for_what_is_not_a_tenant_payment(client, conn, app_conn, pmc, lease):
    join_as_staff(client, conn, app_conn, pmc)
    assert client.get("/api/rent/payments/not-a-transfer/receipt").status_code == 404
    # Another company's payment is not found either.
    other = make_pmc(conn)
    unit = add_unit(conn, other.pmc_id, other.owners[0].property_id)
    theirs = open_lease(conn, other.pmc_id, unit, START, None, "900.00", [tenant_of(conn, other)])
    with conn.transaction():
        made = rent.record_payment(
            conn, other.pmc_id, theirs, uuid.uuid4(), Decimal("100.00"), TODAY
        )
    assert client.get(f"/api/rent/payments/{made.transfers[0]}/receipt").status_code == 404


@pytest.mark.parametrize("caller", ["nobody", "owner", "tenant"])
def test_only_staff_read_a_receipt_from_the_rent_pages(client, conn, app_conn, pmc, lease, caller):
    if caller == "owner":
        join_as_owner(client, app_conn, pmc)
    elif caller == "tenant":
        join_as_tenant(client, conn, app_conn, pmc)
    status = 401 if caller == "nobody" else 403
    assert client.get("/api/rent/payments/x/receipt").status_code == status
    if caller != "tenant":
        assert client.get("/api/tenant/payments/x/receipt").status_code == status
