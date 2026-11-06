"""Who keeps a fee (migration 20261106000220, src/pms/api/rent.py): a charge type says whether
its fees' money goes to the owner or to the company. A tenant's payment, and prepaid rent used,
pay a fee the company keeps into the company's fee account in the same trust bank account, and
the owner's account takes the rest; the owner's income statements and management fee never
count the company's part. Before this every fee went to the owner, as a fee of a type the owner
keeps (or of no type) still does.
"""

import csv
import io
import json
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from helpers import (
    add_agreement,
    add_tenant,
    add_unit,
    bank_sums,
    make_pmc,
    open_account,
    open_lease,
    post_management_fee,
)
from people import join_as_staff

from pms.api import rent

NOW = datetime.now(UTC)
TODAY = NOW.date()
DAY = timedelta(days=1)
# The first of last month: a whole month of rent and monthly charges before today.
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
    return answer.json()


def add_type(client, name, payee="owner"):
    return ok(send(client, "/api/setup/charge-types", name=name, payee=payee))["id"]


def fee(client, lease, amount, memo, charge_type=None, due_on=START):
    return ok(
        send(
            client,
            f"/api/rent/leases/{lease}/charges",
            kind="fee",
            amount=amount,
            due_on=due_on,
            memo=memo,
            charge_type_id=charge_type,
        )
    )["id"]


def pay(client, lease, amount, request_id=None):
    return ok(
        send(
            client,
            f"/api/rent/leases/{lease}/payments",
            request_id=request_id or uuid.uuid4(),
            amount=amount,
            received_on=TODAY,
        )
    )


def balance(conn, account):
    return conn.execute(
        "SELECT balance FROM pgledger_accounts WHERE id = %s", (account,)
    ).fetchone()[0]


def paid_into(conn, transfers):
    """Each charge a payment paid, with how much and the kind of account its transfer went to."""
    return {
        memo: (Decimal(amount), kind)
        for memo, amount, kind in conn.execute(
            "SELECT CASE c.kind WHEN 'rent' THEN 'rent' ELSE c.memo END, cp.amount, a.kind"
            " FROM trust_charge_payments AS cp"
            " JOIN trust_charges AS c ON c.id = cp.charge_id"
            " JOIN pgledger_transfers AS t ON t.id = cp.transfer_id"
            " JOIN trust_ledger_accounts AS a ON a.ledger_account_id = t.to_account_id"
            " WHERE cp.transfer_id = ANY(%s)",
            (transfers,),
        ).fetchall()
    }


def test_before_this_every_fee_a_payment_pays_goes_to_the_owner(client, conn, staff, pmc, lease):
    # A fee of no type and one of a type the owner keeps: the owner's account takes them all.
    pets = add_type(client, "Pet rent")
    fee(client, lease, "40.00", "Key copy")
    fee(client, lease, "25.00", "Dog", pets)

    paid = pay(client, lease, "65.00")

    assert (paid["to_owner"], paid["to_company"], paid["to_prepaid"]) == ("65.00", "0.00", "0.00")
    assert paid_into(conn, paid["transfers"]) == {
        "Key copy": (Decimal("40.00"), "owner_property"),
        "Dog": (Decimal("25.00"), "owner_property"),
    }
    assert balance(conn, pmc.owners[0].account) == Decimal("65.00")
    assert balance(conn, pmc.pmc_income) == Decimal("0.00")


def test_a_fee_the_company_keeps_goes_to_its_fee_account_and_the_owner_gets_the_rest(
    client, conn, staff, pmc, lease
):
    admin = add_type(client, "Admin fee", "company")
    pets = add_type(client, "Pet rent")
    ok(send(client, "/api/rent/run", due_on=START, late_fees_as_of=START), 200)
    fee(client, lease, "75.00", "Admin", admin)
    fee(client, lease, "25.00", "Dog", pets)

    # Rent first, then the fees oldest first; 50.00 more is prepaid rent.
    paid = pay(client, lease, "1150.00")

    assert (paid["to_owner"], paid["to_company"], paid["to_prepaid"]) == (
        "1100.00",
        "75.00",
        "50.00",
    )
    assert paid_into(conn, paid["transfers"]) == {
        "rent": (Decimal("1000.00"), "owner_property"),
        "Admin": (Decimal("75.00"), "pmc_income"),
        "Dog": (Decimal("25.00"), "owner_property"),
    }
    assert balance(conn, pmc.owners[0].account) == Decimal("1025.00")
    assert balance(conn, pmc.pmc_income) == Decimal("75.00")
    assert balance(conn, pmc.prepaid_rent) == Decimal("50.00")
    # The trust bank account ties out: its cash is what it holds for everyone.
    assert set(bank_sums(conn, pmc.pmc_id).values()) == {Decimal(0)}
    # Sent again, the payment is as it was.
    again = conn.execute(
        "SELECT metadata ->> 'key' FROM pgledger_transfers WHERE id = %s", (paid["transfers"][0],)
    ).fetchone()[0]
    replayed = pay(client, lease, "1150.00", uuid.UUID(again.removeprefix("payment/")))
    assert replayed == paid


def test_a_trust_bank_account_with_no_fee_account_gets_one_for_a_fee_the_company_keeps(
    client, conn, staff, pmc
):
    # Setup opens the company's fee account with every operating trust bank account; this one
    # was made with only its cash, as books set up by hand may be.
    bank = conn.execute(
        "INSERT INTO trust_bank_accounts (pmc_id, kind, display_name)"
        " VALUES (%s, 'operating', 'Hand-made operating account') RETURNING id",
        (pmc.pmc_id,),
    ).fetchone()[0]
    open_account(conn, pmc.pmc_id, bank, "bank_cash")
    home = conn.execute(
        "INSERT INTO trust_properties (pmc_id, display_name) VALUES (%s, 'Property 3')"
        " RETURNING id",
        (pmc.pmc_id,),
    ).fetchone()[0]
    owners = open_account(conn, pmc.pmc_id, bank, "owner_property", pmc.owners[1].owner_id, home)
    tenant = add_tenant(conn, pmc.pmc_id, home, "Tenant 3")
    lease = open_lease(
        conn, pmc.pmc_id, add_unit(conn, pmc.pmc_id, home), START, None, "900.00", [tenant]
    )
    admin = add_type(client, "Admin fee", "company")
    fee(client, lease, "75.00", "Admin", admin)
    fee(client, lease, "40.00", "Key copy")

    paid = pay(client, lease, "115.00")

    (fees,) = conn.execute(
        "SELECT ledger_account_id FROM trust_ledger_accounts"
        " WHERE bank_account_id = %s AND kind = 'pmc_income'",
        (bank,),
    ).fetchone()
    assert (paid["to_owner"], paid["to_company"]) == ("115.00", "75.00")
    assert (balance(conn, fees), balance(conn, owners)) == (Decimal("75.00"), Decimal("40.00"))
    assert balance(conn, pmc.pmc_income) == Decimal("0.00")
    assert set(bank_sums(conn, pmc.pmc_id).values()) == {Decimal(0)}


def test_part_of_a_payment_pays_part_of_a_fee_the_company_keeps(client, conn, staff, pmc, lease):
    admin = add_type(client, "Admin fee", "company")
    fee(client, lease, "75.00", "Admin", admin)

    first = pay(client, lease, "30.00")
    second = pay(client, lease, "45.00")

    assert [first["to_company"], second["to_company"]] == ["30.00", "45.00"]
    assert balance(conn, pmc.pmc_income) == Decimal("75.00")
    assert balance(conn, pmc.owners[0].account) == Decimal("0.00")


def test_a_payee_changed_counts_for_fees_charged_from_then_on(client, conn, staff, pmc, lease):
    renewal = add_type(client, "Renewal fee")
    fee(client, lease, "100.00", "First renewal", renewal)
    ok(send(client, f"/api/setup/charge-types/{renewal}/payee", payee="company"))
    fee(client, lease, "100.00", "Second renewal", renewal)

    paid = pay(client, lease, "200.00")

    assert paid_into(conn, paid["transfers"]) == {
        "First renewal": (Decimal("100.00"), "owner_property"),
        "Second renewal": (Decimal("100.00"), "pmc_income"),
    }


def test_prepaid_rent_used_pays_a_fee_the_company_keeps_into_its_fee_account(
    client, conn, staff, pmc, lease
):
    admin = add_type(client, "Admin fee", "company")
    pay(client, lease, "300.00")  # nothing owed yet: all of it prepaid rent
    fee(client, lease, "75.00", "Admin", admin)
    fee(client, lease, "40.00", "Key copy")

    used = ok(
        send(client, f"/api/rent/leases/{lease}/use-prepaid", request_id=uuid.uuid4(), on=TODAY)
    )

    assert (used["to_owner"], used["to_company"]) == ("115.00", "75.00")
    assert paid_into(conn, used["transfers"]) == {
        "Admin": (Decimal("75.00"), "pmc_income"),
        "Key copy": (Decimal("40.00"), "owner_property"),
    }
    assert balance(conn, pmc.prepaid_rent) == Decimal("185.00")
    assert set(bank_sums(conn, pmc.pmc_id).values()) == {Decimal(0)}


def test_a_payment_that_bounces_takes_the_companys_part_back_too(client, conn, staff, pmc, lease):
    admin = add_type(client, "Admin fee", "company")
    fee(client, lease, "75.00", "Admin", admin)
    fee(client, lease, "40.00", "Key copy")
    paid = pay(client, lease, "115.00")

    ok(
        send(client, f"/api/rent/payments/{paid['transfers'][0]}/bounce", on=TODAY),
        201,
    )

    assert balance(conn, pmc.pmc_income) == Decimal("0.00")
    assert balance(conn, pmc.owners[0].account) == Decimal("0.00")
    assert rent.owed(conn, lease) == Decimal("115.00")  # the property charges no NSF fee
    assert set(bank_sums(conn, pmc.pmc_id).values()) == {Decimal(0)}


def test_the_owners_statements_and_management_fee_leave_the_companys_fees_out(
    client, conn, staff, pmc, lease
):
    benefit = add_type(client, "Resident benefit package", "company")
    parking = add_type(client, "Parking")
    add_agreement(conn, pmc.pmc_id, pmc.owners[0].account, START, fee_percent="10")
    # Each month: rent, a benefit package the company keeps and parking the owner keeps.
    for memo, amount, kind in (("Benefits", "20.00", benefit), ("Space 1", "50.00", parking)):
        ok(
            send(
                client,
                f"/api/rent/leases/{lease}/recurring-charges",
                kind="fee",
                amount=amount,
                memo=memo,
                starts_on=START,
                charge_type_id=kind,
            )
        )
    ok(send(client, "/api/rent/run", due_on=START, late_fees_as_of=START), 200)
    fee(client, lease, "75.00", "Admin", add_type(client, "Admin fee", "company"))
    pay(client, lease, "1145.00")

    # 10% of what was collected into the owner's account as rent and monthly charges: the rent
    # and the parking, never the benefit package.
    assert post_management_fee(
        conn, pmc.pmc_id, pmc.owners[0].account, TODAY.replace(day=1), TODAY + DAY, NOW
    ) == Decimal("105.00")
    year = TODAY.year
    args = [str(pmc.owners[0].owner_id), f"{year}-01-01", f"{year}-12-31"]
    for name in ("trust_report_income_statement", "trust_report_income_statement_accrual"):
        report = client.get(f"/api/reports/{name}", params={"arg": args})
        assert report.status_code == 200, report.text
        lines = {
            r["Item"]: r["Amount"]
            for r in csv.DictReader(io.StringIO(report.text))
            if r["Property"] == "Property 1"
        }
        income = {k: v for k, v in lines.items() if k.startswith("income:") and v != "0.00"}
        # The owner's income is the rent and the parking; the company's fees are on no line.
        assert income == {"income: rent": "1000.00", "income: Parking": "50.00"}, name
        assert lines["total income"] == "1050.00", name
    assert balance(conn, pmc.pmc_income) == Decimal("200.00")  # 20 + 75 + the fee of 105


def test_another_companys_fee_type_is_never_its_payee(client, conn, staff, pmc):
    theirs = make_pmc(conn)
    kind = conn.execute(
        "INSERT INTO trust_charge_types (pmc_id, name) VALUES (%s, 'Admin fee') RETURNING id",
        (theirs.pmc_id,),
    ).fetchone()[0]

    answer = send(client, f"/api/setup/charge-types/{kind}/payee", payee="company")

    assert answer.status_code == 404
    assert conn.execute(
        "SELECT count(*) FROM trust_charge_type_payees WHERE charge_type_id = %s", (kind,)
    ).fetchone() == (0,)
