"""Seeds a synthetic PMC for the browser tests, on the database in DATABASE_URL (already
migrated), and prints what the tests need as JSON: invite tokens and emails.

usage: PYTHONPATH=src python frontend/e2e/seed.py > frontend/e2e/.seed.json

Last month's rent goes to the owner's two properties, so the statement the owner portal shows
first (last month) has postings. Every name is synthetic.
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
        }
    print(json.dumps(seeded))


if __name__ == "__main__":
    main()
