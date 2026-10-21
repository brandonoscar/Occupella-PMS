"""Seeds a synthetic PMC for the browser tests, on the database in DATABASE_URL (already
migrated), and prints what the tests need as JSON: invite tokens and emails.

usage: PYTHONPATH=src python frontend/e2e/seed.py > frontend/e2e/.seed.json

Last month's rent goes to the owner's two properties, so the statement the owner portal shows
first (last month) has postings. A second owner's property has a tenant on a lease, with last
month's rent paid, this month's due and a maintenance request; a vacant unit there has an open
application link. For lease offers, another vacant unit has an approved application, and a
third unit a tenant on a month-to-month lease to renew. Then the demo PMC (src/pms/demo.py),
with invites for its manager and its owner of three properties, and another for the
accessibility sweep. Last, a demo PMC's books
(`demo.books`, without what the demo adds to every other page) for each feature test
(frontend/e2e/features.spec.ts), so each starts from the same books and none sees another's
changes. Every name is synthetic.
"""

import json
import os
import secrets
import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Any

import psycopg

from pms import accounts, demo


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
        # Leasing: a vacant unit with an open application link, and a second staff member to
        # take its applications (the first one's invite is used by another test).
        vacant = one(
            "INSERT INTO trust_units (pmc_id, property_id, display_name)"
            " VALUES (%s, %s, 'E2E Unit 2') RETURNING id",
            (pmc, prop_three),
        )
        leasing_staff = one(
            "INSERT INTO trust_staff (pmc_id, display_name) VALUES (%s, 'Leasing E2E')"
            " RETURNING id",
            (pmc,),
        )
        apply_token = secrets.token_urlsafe(32)
        conn.execute(
            "INSERT INTO trust_application_links"
            " (pmc_id, unit_id, token_sha256, monthly_rent, opened_by)"
            " VALUES (%s, %s, %s, 1200.00, %s)",
            (pmc, vacant, accounts.digest(apply_token), leasing_staff),
        )
        conn.execute(
            "INSERT INTO trust_maintenance_requests"
            " (pmc_id, lease_id, tenant_id, summary, entry_allowed)"
            " VALUES (%s, %s, %s, 'The back door sticks', true)",
            (pmc, lease, renter),
        )
        # Lease offers: an approved application for a vacant unit, a tenant whose lease is to be
        # renewed, and a staff member of their own to offer both.
        offers_staff = one(
            "INSERT INTO trust_staff (pmc_id, display_name) VALUES (%s, 'Offers E2E') RETURNING id",
            (pmc,),
        )
        unit_four = one(
            "INSERT INTO trust_units (pmc_id, property_id, display_name)"
            " VALUES (%s, %s, 'E2E Unit 4') RETURNING id",
            (pmc, prop_three),
        )
        link_four = one(
            "INSERT INTO trust_application_links"
            " (pmc_id, unit_id, token_sha256, monthly_rent, opened_by)"
            " VALUES (%s, %s, %s, 1100.00, %s) RETURNING id",
            (pmc, unit_four, accounts.digest(secrets.token_urlsafe(32)), offers_staff),
        )
        approved = one(
            "INSERT INTO trust_applications"
            " (pmc_id, link_id, status_token_sha256, move_in_on, monthly_income)"
            " VALUES (%s, %s, %s, %s, 4000.00) RETURNING id",
            (
                pmc,
                link_four,
                accounts.digest(secrets.token_urlsafe(32)),
                today.date() + timedelta(days=30),
            ),
        )
        conn.execute(
            "INSERT INTO trust_applicants (pmc_id, application_id, position, display_name, email)"
            " VALUES (%s, %s, 1, 'Mover E2E', %s)",
            (pmc, approved, f"mover.{tag}@example.com"),
        )
        conn.execute(
            "INSERT INTO trust_application_decisions"
            " (pmc_id, application_id, decision, decided_by) VALUES (%s, %s, 'approved', %s)",
            (pmc, approved, offers_staff),
        )
        unit_three = one(
            "INSERT INTO trust_units (pmc_id, property_id, display_name)"
            " VALUES (%s, %s, 'E2E Unit 3') RETURNING id",
            (pmc, prop_three),
        )
        renewer = one(
            "INSERT INTO trust_tenants (pmc_id, property_id, display_name)"
            " VALUES (%s, %s, 'Renewer E2E') RETURNING id",
            (pmc, prop_three),
        )
        conn.execute(
            "SELECT trust_open_lease(%s, %s, %s, NULL, 1000.00, %s::uuid[])",
            (pmc, unit_three, first_of_last_month, [renewer]),
        )
        # Setup: a staff member of their own, to set up a property from nothing.
        setup_staff = one(
            "INSERT INTO trust_staff (pmc_id, display_name) VALUES (%s, 'Setup E2E') RETURNING id",
            (pmc,),
        )
        # Rent: a tenant owing this month's rent on a lease of their own, and a staff member to
        # record their payment.
        rent_staff = one(
            "INSERT INTO trust_staff (pmc_id, display_name) VALUES (%s, 'Rent E2E') RETURNING id",
            (pmc,),
        )
        unit_five = one(
            "INSERT INTO trust_units (pmc_id, property_id, display_name)"
            " VALUES (%s, %s, 'E2E Unit 5') RETURNING id",
            (pmc, prop_three),
        )
        payer = one(
            "INSERT INTO trust_tenants (pmc_id, property_id, display_name)"
            " VALUES (%s, %s, 'Payer E2E') RETURNING id",
            (pmc, prop_three),
        )
        payer_lease = one(
            "SELECT trust_open_lease(%s, %s, %s, NULL, 800.00, %s::uuid[])",
            (pmc, unit_five, first_of_last_month, [payer]),
        )
        conn.execute(
            "INSERT INTO trust_charges (pmc_id, lease_id, due_on, kind, amount, memo)"
            " VALUES (%s, %s, %s, 'rent', 800.00, 'Rent')",
            (pmc, payer_lease, first_of_this_month),
        )
        # Bills and owners: an owner of their own holding last month's rent, and a staff member
        # to pay a vendor's bill from it and draw the rest.
        bills_staff = one(
            "INSERT INTO trust_staff (pmc_id, display_name) VALUES (%s, 'Bills E2E') RETURNING id",
            (pmc,),
        )
        payee = one(
            "INSERT INTO trust_owners (pmc_id, display_name) VALUES (%s, 'Payee E2E') RETURNING id",
            (pmc,),
        )
        prop_seven = one(
            "INSERT INTO trust_properties (pmc_id, display_name) VALUES (%s, 'E2E Property 7')"
            " RETURNING id",
            (pmc,),
        )
        payee_account = one(
            "SELECT trust_open_ledger_account(%s, %s, 'owner_property', %s, %s, NULL)",
            (pmc, bank, payee, prop_seven),
        )
        conn.execute(
            "SELECT pgledger_create_transfer(%s, %s, 1000.00, %s, %s)",
            (cash, payee_account, last_month, json.dumps({"memo": "Rent"})),
        )
        # Month-end: a staff member to reconcile last month from the bank's statement.
        month_end_staff = one(
            "INSERT INTO trust_staff (pmc_id, display_name) VALUES (%s, 'Month-end E2E')"
            " RETURNING id",
            (pmc,),
        )
        # Switching: a staff member, and an operating trust account with nothing in it yet, to
        # open with balances from the old system.
        switch_staff = one(
            "INSERT INTO trust_staff (pmc_id, display_name) VALUES (%s, 'Switch E2E') RETURNING id",
            (pmc,),
        )
        cutover_bank = one(
            "INSERT INTO trust_bank_accounts (pmc_id, kind, display_name)"
            " VALUES (%s, 'operating', 'E2E cutover trust account') RETURNING id",
            (pmc,),
        )
        for kind in ("bank_cash", "pmc_income"):
            conn.execute(
                "SELECT trust_open_ledger_account(%s, %s, %s, NULL, NULL, NULL)",
                (pmc, cutover_bank, kind),
            )
        # Documents: a staff member who keeps an owner's document for Owner E2E to read.
        documents_staff = one(
            "INSERT INTO trust_staff (pmc_id, display_name) VALUES (%s, 'Documents E2E')"
            " RETURNING id",
            (pmc,),
        )
        # Move-out: a lease that ended last month, its tenant's deposit held in a security
        # deposit trust account, and a staff member who may pay it back.
        move_out_staff = one(
            "INSERT INTO trust_staff (pmc_id, display_name) VALUES (%s, 'Move-out E2E')"
            " RETURNING id",
            (pmc,),
        )
        deposit_bank = one(
            "INSERT INTO trust_bank_accounts (pmc_id, kind, display_name)"
            " VALUES (%s, 'security_deposit', 'E2E security deposit trust account') RETURNING id",
            (pmc,),
        )
        deposit_cash = one(
            "SELECT trust_open_ledger_account(%s, %s, 'bank_cash', NULL, NULL, NULL)",
            (pmc, deposit_bank),
        )
        landlord = one(
            "INSERT INTO trust_owners (pmc_id, display_name) VALUES (%s, 'Landlord E2E')"
            " RETURNING id",
            (pmc,),
        )
        prop_eight = one(
            "INSERT INTO trust_properties (pmc_id, display_name) VALUES (%s, 'E2E Property 8')"
            " RETURNING id",
            (pmc,),
        )
        conn.execute(
            "SELECT trust_open_ledger_account(%s, %s, 'owner_property', %s, %s, NULL)",
            (pmc, bank, landlord, prop_eight),
        )
        unit_eight = one(
            "INSERT INTO trust_units (pmc_id, property_id, display_name)"
            " VALUES (%s, %s, 'E2E Unit 8') RETURNING id",
            (pmc, prop_eight),
        )
        leaver = one(
            "INSERT INTO trust_tenants (pmc_id, property_id, display_name)"
            " VALUES (%s, %s, 'Leaver E2E') RETURNING id",
            (pmc, prop_eight),
        )
        conn.execute(
            "SELECT trust_open_lease(%s, %s, %s, %s, 900.00, %s::uuid[])",
            (
                pmc,
                unit_eight,
                first_of_last_month - timedelta(days=60),
                first_of_this_month - timedelta(days=1),
                [leaver],
            ),
        )
        leaver_deposit = one(
            "SELECT trust_open_ledger_account(%s, %s, 'tenant_deposit', NULL, NULL, %s)",
            (pmc, deposit_bank, leaver),
        )
        conn.execute(
            "SELECT pgledger_create_transfer(%s, %s, 900.00, %s, %s)",
            (
                deposit_cash,
                leaver_deposit,
                last_month - timedelta(days=60),
                json.dumps({"memo": "Security deposit"}),
            ),
        )
        # Month-end is approved by someone other than whoever prepared it.
        approver_staff = one(
            "INSERT INTO trust_staff (pmc_id, display_name) VALUES (%s, 'Approver E2E')"
            " RETURNING id",
            (pmc,),
        )
        # Roles: Staff E2E is the PMC's first admin and grants the rest, as an admin would.
        conn.execute(
            "INSERT INTO trust_staff_roles (pmc_id, staff_id, role, granted)"
            " VALUES (%s, %s, 'admin', true)",
            (pmc, staff),
        )
        conn.execute("SELECT set_config('trust.staff_id', %s, false)", (str(staff),))
        for member, role in (
            (bills_staff, "disburse"),
            (switch_staff, "approve"),
            (approver_staff, "approve"),
            (move_out_staff, "disburse"),
        ):
            conn.execute(
                "INSERT INTO trust_staff_roles (pmc_id, staff_id, role, granted)"
                " VALUES (%s, %s, %s, true)",
                (pmc, member, role),
            )
        conn.execute("SELECT set_config('trust.staff_id', '', false)")
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
            "apply_token": apply_token,
            "leasing_email": f"leasing.{tag}@example.com",
            "leasing_invite": accounts.create_invite(
                conn, pmc, "staff", leasing_staff, f"leasing.{tag}@example.com", None
            ).token,
            "offers_invite": accounts.create_invite(
                conn, pmc, "staff", offers_staff, f"offers.{tag}@example.com", None
            ).token,
            "renewer_invite": accounts.create_invite(
                conn, pmc, "tenant", renewer, f"renewer.{tag}@example.com", None
            ).token,
            "rent_invite": accounts.create_invite(
                conn, pmc, "staff", rent_staff, f"rent.{tag}@example.com", None
            ).token,
            "bills_invite": accounts.create_invite(
                conn, pmc, "staff", bills_staff, f"bills.{tag}@example.com", None
            ).token,
            "month_end_invite": accounts.create_invite(
                conn, pmc, "staff", month_end_staff, f"month-end.{tag}@example.com", None
            ).token,
            "approver_invite": accounts.create_invite(
                conn, pmc, "staff", approver_staff, f"approver.{tag}@example.com", None
            ).token,
            "move_out_invite": accounts.create_invite(
                conn, pmc, "staff", move_out_staff, f"move-out.{tag}@example.com", None
            ).token,
            "documents_invite": accounts.create_invite(
                conn, pmc, "staff", documents_staff, f"documents.{tag}@example.com", None
            ).token,
            "switch_invite": accounts.create_invite(
                conn, pmc, "staff", switch_staff, f"switch.{tag}@example.com", None
            ).token,
            "setup_invite": accounts.create_invite(
                conn, pmc, "staff", setup_staff, f"setup.{tag}@example.com", None
            ).token,
            "renter_email": f"renter.{tag}@example.com",
            "renter_invite": accounts.create_invite(
                conn, pmc, "tenant", renter, f"renter.{tag}@example.com", None
            ).token,
        }
    # The demo PMC (src/pms/demo.py), built as staff build one: its manager and owner.
    with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True) as app:
        app.execute("SET ROLE trust_app")
        made = demo.build(app)
        seeded["demo_manager_invite"] = accounts.create_invite(
            app, made.pmc_id, "staff", made.manager, f"demo-manager.{tag}@example.com", None
        ).token
        seeded["demo_owner_invite"] = accounts.create_invite(
            app,
            made.pmc_id,
            "owner",
            made.owners["Demo Owner A"],
            f"demo-owner.{tag}@example.com",
            None,
        ).token
        # Another demo for the accessibility sweep (portal.spec.ts), so it reads every page as
        # the demo first shows it: its manager, an owner and a tenant.
        sweep = demo.build(app)
        seeded["accessibility"] = {
            "pmc_id": str(sweep.pmc_id),
            **{
                who: accounts.create_invite(
                    app, sweep.pmc_id, kind, person, f"a11y-{who}.{tag}@example.com", None
                ).token
                for who, kind, person in (
                    ("manager", "staff", sweep.manager),
                    ("owner", "owner", sweep.owners["Demo Owner A"]),
                    ("tenant", "tenant", sweep.tenants["Taylor Demo"]),
                )
            },
        }
        seeded["features"] = {name: feature_pmc(app, tag, name) for name in FEATURES}
        # Year close: books as of this year's first day, so last year's November is approved and
        # its December waits for approval in each trust bank account, whatever the day today.
        seeded["features"]["year-close"] = feature_pmc(
            app, tag, "year-close", today=date(datetime.now(UTC).year, 1, 1)
        )
    print(json.dumps(seeded))


# The feature tests (frontend/e2e/features.spec.ts), each on a demo PMC of its own.
FEATURES = (
    "rent",
    "recurring",
    "bills",
    "recurring-bills",
    "owners",
    "roles",
    "owner-portal",
    "tenant-portal",
    "listings",
    "downloads",
    "move-out",
    "switching",
    "documents",
    "setup",
    "expenses",
    "budgets",
    "custom-roles",
    "journal",
    "vendors",
    "inspections",
    "company-fees",
    "cutover-bills",
    "signers",
    "rent-changes",
    "renters-insurance",
    "rentals",
    "people",
    "general-ledger",
    "maintenance-pages",
    "groups",
    "company",
    "email",
    "automatic-email",
    "rent-in",
    "waive",
    "lease-people",
    "rent-due-day",
    "owner-fees",
    "shared-bills",
    "ownership",
    "housing-assistance",
    "utility-billing",
    "emergencies",
    "recurring-work-orders",
    "vendor-links",
    "listing-photos",
    "leads",
    "screening-criteria",
    "lease-transfers",
    "calendar",
    "own-contact",
    "charge-types",
    "phone",
    "quickbooks",
    "two-homes",
    "history",
    "assistance-compliance",
)


def feature_pmc(
    app: psycopg.Connection[Any], tag: str, name: str, today: date | None = None
) -> dict[str, Any]:
    """A demo PMC's books (as of today, or another day) and invites (with their emails) for its
    people: the manager (admin, approve, disburse), the bookkeeper (no role), both owners and two
    tenants."""
    made = demo.books(app, today)
    people = {
        "manager": ("staff", made.manager),
        "bookkeeper": ("staff", made.bookkeeper),
        "owner_a": ("owner", made.owners["Demo Owner A"]),
        "owner_b": ("owner", made.owners["Demo Owner B"]),
        "taylor": ("tenant", made.tenants["Taylor Demo"]),
        "drew": ("tenant", made.tenants["Drew Sample"]),
    }
    emails = {who: f"{name}-{who}.{tag}@example.com" for who in people}
    invites = {
        who: accounts.create_invite(app, made.pmc_id, kind, person, emails[who], None).token
        for who, (kind, person) in people.items()
    }
    return {"pmc_id": str(made.pmc_id), "emails": emails, **invites}


if __name__ == "__main__":
    main()
