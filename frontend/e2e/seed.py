"""Seeds a synthetic PMC for the browser tests, on the database in DATABASE_URL (already
migrated), and prints what the tests need as JSON: invite tokens and emails.

usage: PYTHONPATH=src python frontend/e2e/seed.py > frontend/e2e/.seed.json

Last month's rent goes to the owner's two properties, so the statement the owner portal shows
first (last month) has postings. A second owner's property has a tenant on a lease, with last
month's rent paid, this month's due and a maintenance request. Every name is synthetic.
"""

import json
import os
import uuid
from datetime import UTC, datetime, timedelta

import psycopg

from pms import accounts


def main() -> None:
    tag = uuid.uuid4().hex[:6]
    today = datetime.now(UTC)
    last_month = (today.replace(day=1) - timedelta(days=1)).replace(day=5, hour=12)
    with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True) as conn:
        one = lambda q, p=(): conn.execute(q, p).fetchone()[0]  # noqa: E731
        pmc = one(
            "INSERT INTO trust_pmcs (display_name) VALUES (%s) RETURNING pmc_id",
            (f"E2E PMC {tag}",),
        )
        bank = one(
            "INSERT INTO trust_bank_accounts (pmc_id, kind, display_name)"
            " VALUES (%s, 'operating', 'E2E operating trust account') RETURNING id",
            (pmc,),
        )
        cash = one(
            "SELECT trust_open_ledger_account(%s, %s, 'bank_cash', NULL, NULL, NULL)", (pmc, bank)
        )
        owner = one(
            "INSERT INTO trust_owners (pmc_id, display_name) VALUES (%s, 'Owner E2E') RETURNING id",
            (pmc,),
        )
        for n, rent in ((1, "1500.00"), (2, "1250.00")):
            prop = one(
                "INSERT INTO trust_properties (pmc_id, display_name) VALUES (%s, %s) RETURNING id",
                (pmc, f"E2E Property {n}"),
            )
            account = one(
                "SELECT trust_open_ledger_account(%s, %s, 'owner_property', %s, %s, NULL)",
                (pmc, bank, owner, prop),
            )
            conn.execute(
                "SELECT pgledger_create_transfer(%s, %s, %s, %s, %s)",
                (cash, account, rent, last_month, json.dumps({"memo": "Rent"})),
            )
        tenant = one(
            "INSERT INTO trust_tenants (pmc_id, property_id, display_name)"
            " VALUES (%s, %s, 'Tenant E2E') RETURNING id",
            (pmc, prop),
        )
        staff = one(
            "INSERT INTO trust_staff (pmc_id, display_name) VALUES (%s, 'Staff E2E') RETURNING id",
            (pmc,),
        )
        # The tenant portal's tenant: on a lease of a second owner's property (so the owner
        # test's numbers stay as they are), last month's rent paid and this month's due.
        owner_two = one(
            "INSERT INTO trust_owners (pmc_id, display_name) VALUES (%s, 'Owner Two E2E')"
            " RETURNING id",
            (pmc,),
        )
        prop_three = one(
            "INSERT INTO trust_properties (pmc_id, display_name) VALUES (%s, 'E2E Property 3')"
            " RETURNING id",
            (pmc,),
        )
        account_three = one(
            "SELECT trust_open_ledger_account(%s, %s, 'owner_property', %s, %s, NULL)",
            (pmc, bank, owner_two, prop_three),
        )
        unit = one(
            "INSERT INTO trust_units (pmc_id, property_id, display_name)"
            " VALUES (%s, %s, 'E2E Unit 1') RETURNING id",
            (pmc, prop_three),
        )
        renter = one(
            "INSERT INTO trust_tenants (pmc_id, property_id, display_name)"
            " VALUES (%s, %s, 'Tenant Two E2E') RETURNING id",
            (pmc, prop_three),
        )
        first_of_last_month, first_of_this_month = (
            last_month.date().replace(day=1),
            today.date().replace(day=1),
        )
        lease = one(
            "SELECT trust_open_lease(%s, %s, %s, NULL, 950.00, %s::uuid[])",
            (pmc, unit, first_of_last_month, [renter]),
        )
        charges = [
            one(
                "INSERT INTO trust_charges (pmc_id, lease_id, due_on, kind, amount, memo)"
                " VALUES (%s, %s, %s, 'rent', 950.00, 'Rent') RETURNING id",
                (pmc, lease, due),
            )
            for due in (first_of_last_month, first_of_this_month)
        ]
        paid = one(
            "SELECT id FROM pgledger_create_transfer(%s, %s, 950.00, %s, %s)",
            (cash, account_three, last_month, json.dumps({"memo": "Check 101"})),
        )
        conn.execute("SELECT trust_apply_payment(%s, %s, %s, 950.00)", (pmc, charges[0], paid))
        conn.execute(
            "INSERT INTO trust_vendors (pmc_id, display_name) VALUES (%s, 'Vendor E2E')", (pmc,)
        )
        conn.execute(
            "INSERT INTO trust_maintenance_requests"
            " (pmc_id, lease_id, tenant_id, summary, entry_allowed)"
            " VALUES (%s, %s, %s, 'The back door sticks', true)",
            (pmc, lease, renter),
        )
        owner_email, staff_email = f"owner.{tag}@example.com", f"staff.{tag}@example.com"
        seeded = {
            "pmc_id": str(pmc),
            "owner_email": owner_email,
            "owner_invite": accounts.create_invite(
                conn, pmc, "owner", owner, owner_email, None
            ).token,
            "staff_email": staff_email,
            "staff_invite": accounts.create_invite(
                conn, pmc, "staff", staff, staff_email, None
            ).token,
            "tenant_id": str(tenant),
            "renter_email": f"renter.{tag}@example.com",
            "renter_invite": accounts.create_invite(
                conn, pmc, "tenant", renter, f"renter.{tag}@example.com", None
            ).token,
        }
    print(json.dumps(seeded))


if __name__ == "__main__":
    main()
