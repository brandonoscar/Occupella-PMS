"""Helpers shared by the tests: a synthetic PMC and thin wrappers over the ledger functions.

Every fixture is synthetic: no real names, addresses, bank numbers or tax IDs.
"""

import os
import shutil
import threading
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from pathlib import Path
from uuid import UUID

import psycopg
from psycopg import sql
from psycopg.types.json import Jsonb

from tools.check_rollback import Dbmate
from tools.scratch_db import scratch_database

MIGRATIONS = Path(
    os.environ.get("PMS_MIGRATIONS_DIR", Path(__file__).resolve().parents[1] / "db/migrations")
)

# A WHERE clause for catalog queries: leave out objects that belong to an extension, such as
# plpgsql_check, which only the coverage job's test database loads. Format in the catalog
# (`'pg_proc'`) and the object's oid column (`p.oid`).
NOT_FROM_AN_EXTENSION = """
    NOT EXISTS (
        SELECT 1 FROM pg_depend d
        WHERE d.classid = {catalog}::regclass AND d.objid = {oid} AND d.deptype = 'e'
    )
"""


@dataclass
class Owner:
    owner_id: UUID
    property_id: UUID
    account: str  # owner_property ledger account


@dataclass
class Pmc:
    pmc_id: UUID
    operating_bank_id: UUID
    deposit_bank_id: UUID
    operating_cash: str  # bank_cash ledger account of the operating trust account
    deposit_cash: str  # bank_cash ledger account of the security-deposit trust account
    pmc_income: str
    tenant_deposit: str
    prepaid_rent: str  # the tenant's rent paid ahead, in the operating trust account
    vendor_id: UUID
    vendor_payable: str  # money set aside for the vendor's bill, in the operating trust account
    owners: list[Owner] = field(default_factory=list)

    def accounts(self) -> list[str]:
        return [
            self.operating_cash,
            self.deposit_cash,
            self.pmc_income,
            self.tenant_deposit,
            self.prepaid_rent,
            self.vendor_payable,
            *(owner.account for owner in self.owners),
        ]

    def cash_for(self, account) -> str:
        """The bank_cash account of the trust bank account that `account` sits in."""
        in_deposit_bank = account in (self.deposit_cash, self.tenant_deposit)
        return self.deposit_cash if in_deposit_bank else self.operating_cash


def _one(connection, query, params=()):
    return connection.execute(query, params).fetchone()[0]


def open_account(
    connection, pmc_id, bank_id, kind, owner_id=None, property_id=None, tenant_id=None
):
    return _one(
        connection,
        "SELECT trust_open_ledger_account(%s, %s, %s, %s, %s, %s)",
        (pmc_id, bank_id, kind, owner_id, property_id, tenant_id),
    )


def make_pmc(connection, owners=2) -> Pmc:
    """A synthetic PMC: two trust bank accounts, `owners` owners with one property each, one
    tenant, and every kind of ledger account. Balances all start at zero."""
    tag = uuid.uuid4().hex[:8]
    pmc_id = _one(
        connection,
        "INSERT INTO trust_pmcs (display_name) VALUES (%s) RETURNING pmc_id",
        (f"Synthetic PMC {tag}",),
    )
    banks = {}
    for kind in ("operating", "security_deposit"):
        banks[kind] = _one(
            connection,
            "INSERT INTO trust_bank_accounts (pmc_id, kind, display_name)"
            " VALUES (%s, %s, %s) RETURNING id",
            (pmc_id, kind, f"Synthetic {kind} trust account"),
        )

    owner_rows = []
    for n in range(1, owners + 1):
        owner_id = _one(
            connection,
            "INSERT INTO trust_owners (pmc_id, display_name) VALUES (%s, %s) RETURNING id",
            (pmc_id, f"Owner {n}"),
        )
        property_id = _one(
            connection,
            "INSERT INTO trust_properties (pmc_id, display_name) VALUES (%s, %s) RETURNING id",
            (pmc_id, f"Property {n}"),
        )
        account = open_account(
            connection, pmc_id, banks["operating"], "owner_property", owner_id, property_id
        )
        owner_rows.append(Owner(owner_id, property_id, account))

    tenant_id = _one(
        connection,
        "INSERT INTO trust_tenants (pmc_id, property_id, display_name)"
        " VALUES (%s, %s, %s) RETURNING id",
        (pmc_id, owner_rows[0].property_id, "Tenant 1"),
    )

    vendor_id = _one(
        connection,
        "INSERT INTO trust_vendors (pmc_id, display_name) VALUES (%s, %s) RETURNING id",
        (pmc_id, "Vendor 1"),
    )

    return Pmc(
        pmc_id=pmc_id,
        operating_bank_id=banks["operating"],
        deposit_bank_id=banks["security_deposit"],
        operating_cash=open_account(connection, pmc_id, banks["operating"], "bank_cash"),
        deposit_cash=open_account(connection, pmc_id, banks["security_deposit"], "bank_cash"),
        pmc_income=open_account(connection, pmc_id, banks["operating"], "pmc_income"),
        tenant_deposit=open_account(
            connection, pmc_id, banks["security_deposit"], "tenant_deposit", tenant_id=tenant_id
        ),
        prepaid_rent=open_account(
            connection, pmc_id, banks["operating"], "prepaid_rent", tenant_id=tenant_id
        ),
        vendor_id=vendor_id,
        vendor_payable=_one(
            connection,
            "SELECT trust_open_vendor_account(%s, %s, %s)",
            (pmc_id, banks["operating"], vendor_id),
        ),
        owners=owner_rows,
    )


def bank_sums(connection, pmc_id) -> dict:
    """Per trust bank account of the PMC, the sum of every balance in it: zero when it ties out."""
    return dict(
        connection.execute(
            """
            SELECT t.bank_account_id, sum(a.balance)
            FROM trust_ledger_accounts t JOIN pgledger_accounts a ON a.id = t.ledger_account_id
            WHERE t.pmc_id = %s
            GROUP BY t.bank_account_id
            """,
            (pmc_id,),
        ).fetchall()
    )


def transfer(connection, from_account, to_account, amount) -> str:
    return _one(
        connection,
        "SELECT id FROM pgledger_create_transfer(%s, %s, %s)",
        (from_account, to_account, Decimal(amount)),
    )


def transfer_batch(connection, requests, event_at=None) -> list[str]:
    """Post several transfers in one call: all of them are written, or none. Only the owner
    role may; the app posts through `post`, with an idempotency key."""
    rows = sql.SQL(", ").join(sql.SQL("(%s, %s, %s::numeric)::transfer_request") for _ in requests)
    query = sql.SQL("SELECT id FROM pgledger_create_transfers(ARRAY[{}], %s)").format(rows)
    params = [*(value for request in requests for value in request), event_at]
    return [row[0] for row in connection.execute(query, params).fetchall()]


def post(connection, pmc_id, key, requests, event_at=None, metadata=None) -> list[str]:
    """Post through trust_post_transfers with an idempotency key; the transfer ids, in order."""
    rows = sql.SQL(", ").join(sql.SQL("(%s, %s, %s::numeric)::transfer_request") for _ in requests)
    query = sql.SQL(
        "SELECT id FROM trust_post_transfers(%s, %s, ARRAY[{}]::transfer_request[], %s, %s)"
    ).format(rows)
    params = [pmc_id, key, *(value for request in requests for value in request)]
    params += [event_at, None if metadata is None else Jsonb(metadata)]
    return [row[0] for row in connection.execute(query, params).fetchall()]


def approve(
    connection,
    pmc_id,
    bank_id,
    period_start,
    period_end,
    statement_balance="0.00",
    prepared_by="Preparer 1",
    approved_by="Approver 1",
) -> UUID:
    """Approve a reconciliation of one trust bank account, closing it through period_end."""
    return _one(
        connection,
        "SELECT trust_approve_reconciliation(%s, %s, %s, %s, %s, %s, %s)",
        (
            pmc_id,
            bank_id,
            period_start,
            period_end,
            Decimal(statement_balance),
            prepared_by,
            approved_by,
        ),
    )


def add_unit(connection, pmc_id, property_id, name="Unit 1") -> UUID:
    return _one(
        connection,
        "INSERT INTO trust_units (pmc_id, property_id, display_name) VALUES (%s, %s, %s)"
        " RETURNING id",
        (pmc_id, property_id, name),
    )


def add_tenant(connection, pmc_id, property_id, name) -> UUID:
    return _one(
        connection,
        "INSERT INTO trust_tenants (pmc_id, property_id, display_name) VALUES (%s, %s, %s)"
        " RETURNING id",
        (pmc_id, property_id, name),
    )


def open_lease(connection, pmc_id, unit_id, starts_on, ends_on, rent, tenant_ids) -> UUID:
    return _one(
        connection,
        "SELECT trust_open_lease(%s, %s, %s, %s, %s, %s::uuid[])",
        (pmc_id, unit_id, starts_on, ends_on, Decimal(rent), list(tenant_ids)),
    )


# Synthetic files of each type a document may be: just their first bytes and a few more.
PDF = b"%PDF-1.4\n% synthetic\n"
PNG = b"\x89PNG\r\n\x1a\n synthetic"
JPEG = b"\xff\xd8\xff\xe0 synthetic"


def add_document(
    connection, pmc_id, kind, about, content=PDF, content_type="application/pdf", title="W-9"
) -> UUID:
    """A document of a kind about one thing (a vendor, bill, lease, owner or work order), with
    its file, in one transaction."""
    column = {
        "w9": "vendor_id",
        "receipt": "bill_id",
        "move_out_photo": "lease_id",
        "owner_document": "owner_id",
        "work_order_photo": "work_order_id",
        "property_document": "property_id",
        "unit_document": "unit_id",
        "tenant_document": "tenant_id",
        "lease_document": "lease_id",
        "vendor_document": "vendor_id",
        "request_photo": "request_id",
    }[kind]
    with connection.transaction():
        document = _one(
            connection,
            f"INSERT INTO trust_documents (pmc_id, kind, {column}, title, content_type, size,"
            " sha256) VALUES (%s, %s, %s, %s, %s, %s, sha256(%s)) RETURNING id",
            (pmc_id, kind, about, title, content_type, len(content), content),
        )
        connection.execute(
            "INSERT INTO trust_document_files (document_id, pmc_id, content) VALUES (%s, %s, %s)",
            (document, pmc_id, content),
        )
    return document


def return_deposit(connection, pmc_id, lease_id, request_key, event_at, owner_account=None):
    """Settle an ended lease's deposits: keep what it owes for the owner, return the rest."""
    return _one(
        connection,
        "SELECT trust_return_deposit(%s, %s, %s, %s, %s)",
        (pmc_id, lease_id, owner_account, request_key, event_at),
    )


def end_lease(connection, pmc_id, lease_id, ends_on) -> None:
    connection.execute("SELECT trust_end_lease(%s, %s, %s)", (pmc_id, lease_id, ends_on))


def charge(connection, pmc_id, lease_id, due_on, amount, kind="rent", memo=None) -> UUID:
    return _one(
        connection,
        "INSERT INTO trust_charges (pmc_id, lease_id, due_on, kind, amount, memo)"
        " VALUES (%s, %s, %s, %s, %s, %s) RETURNING id",
        (pmc_id, lease_id, due_on, kind, Decimal(amount), memo),
    )


def apply_payment(connection, pmc_id, charge_id, transfer_id, amount) -> Decimal:
    return _one(
        connection,
        "SELECT trust_apply_payment(%s, %s, %s, %s)",
        (pmc_id, charge_id, transfer_id, None if amount is None else Decimal(amount)),
    )


def reverse_payment(connection, pmc_id, charge_id, transfer_id, reversal_id, amount) -> Decimal:
    return _one(
        connection,
        "SELECT trust_reverse_payment(%s, %s, %s, %s, %s)",
        (
            pmc_id,
            charge_id,
            transfer_id,
            reversal_id,
            None if amount is None else Decimal(amount),
        ),
    )


def add_agreement(
    connection,
    pmc_id,
    account,
    starts_on,
    fee_percent="0",
    minimum_fee="0",
    flat_fee="0",
    leasing_fee_percent="0",
    reserve="0",
    approval_limit="0",
    bill_markup_percent="0",
) -> UUID:
    """A management agreement for one owner's property account, in force from starts_on."""
    return _one(
        connection,
        "INSERT INTO trust_management_agreements (pmc_id, ledger_account_id, starts_on,"
        " fee_percent, minimum_fee, flat_fee, leasing_fee_percent, reserve, approval_limit,"
        " bill_markup_percent) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id",
        (
            pmc_id,
            account,
            starts_on,
            Decimal(fee_percent),
            Decimal(minimum_fee),
            Decimal(flat_fee),
            Decimal(leasing_fee_percent),
            Decimal(reserve),
            Decimal(approval_limit),
            Decimal(bill_markup_percent),
        ),
    )


def post_management_fee(connection, pmc_id, account, period_start, period_end, event_at):
    return _one(
        connection,
        "SELECT trust_post_management_fee(%s, %s, %s, %s, %s)",
        (pmc_id, account, period_start, period_end, event_at),
    )


def post_leasing_fee(connection, pmc_id, lease_id, account, event_at):
    return _one(
        connection,
        "SELECT trust_post_leasing_fee(%s, %s, %s, %s)",
        (pmc_id, lease_id, account, event_at),
    )


def draw_owner(connection, pmc_id, account, request_key, amount, event_at):
    """Pay the owner `amount` (None: everything available) from one property's account."""
    return _one(
        connection,
        "SELECT trust_draw_owner(%s, %s, %s, %s, %s)",
        (pmc_id, account, request_key, None if amount is None else Decimal(amount), event_at),
    )


def withdraw_pmc_fees(connection, pmc_id, bank_account_id, request_key, amount, event_at):
    """Take `amount` (None: everything) of the company's fees out of a trust bank account."""
    return _one(
        connection,
        "SELECT trust_withdraw_pmc_fees(%s, %s, %s, %s, %s)",
        (
            pmc_id,
            bank_account_id,
            request_key,
            None if amount is None else Decimal(amount),
            event_at,
        ),
    )


def enter_bill(
    connection,
    pmc_id,
    vendor_id,
    account,
    reference,
    bill_date,
    due_on,
    amount,
    memo="Repair",
    work_order_id=None,
) -> UUID:
    """A vendor's bill against one owner's property account, maybe for a work order."""
    return _one(
        connection,
        "INSERT INTO trust_bills (pmc_id, vendor_id, ledger_account_id, reference, bill_date,"
        " due_on, amount, memo, work_order_id) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)"
        " RETURNING id",
        (
            pmc_id,
            vendor_id,
            account,
            reference,
            bill_date,
            due_on,
            Decimal(amount),
            memo,
            work_order_id,
        ),
    )


def add_work_order(connection, pmc_id, property_id, unit_id, summary, opened_at) -> UUID:
    """A work order at a property, maybe at one of its units."""
    return _one(
        connection,
        "INSERT INTO trust_work_orders (pmc_id, property_id, unit_id, summary, opened_at)"
        " VALUES (%s, %s, %s, %s, %s) RETURNING id",
        (pmc_id, property_id, unit_id, summary, opened_at),
    )


def step_work_order(
    connection, pmc_id, work_order_id, step, taken_at, vendor_id=None, scheduled_for=None
) -> None:
    """Record what happened to a work order: assigned (to vendor_id), scheduled (for a day),
    completed or cancelled."""
    connection.execute(
        "INSERT INTO trust_work_order_steps"
        " (pmc_id, work_order_id, step, vendor_id, taken_at, scheduled_for)"
        " VALUES (%s, %s, %s, %s, %s, %s)",
        (pmc_id, work_order_id, step, vendor_id, taken_at, scheduled_for),
    )


def file_request(connection, pmc_id, lease_id, tenant_id, summary, entry_allowed=False) -> UUID:
    """A maintenance request a tenant files under one of their leases."""
    return _one(
        connection,
        "INSERT INTO trust_maintenance_requests"
        " (pmc_id, lease_id, tenant_id, summary, entry_allowed)"
        " VALUES (%s, %s, %s, %s, %s) RETURNING id",
        (pmc_id, lease_id, tenant_id, summary, entry_allowed),
    )


def link_work_order(connection, pmc_id, request_id, work_order_id) -> None:
    """The work order staff opened for a maintenance request."""
    connection.execute(
        "INSERT INTO trust_maintenance_request_work_orders (pmc_id, request_id, work_order_id)"
        " VALUES (%s, %s, %s)",
        (pmc_id, request_id, work_order_id),
    )


def open_application_link(connection, pmc_id, unit_id, staff_id, rent="1500.00") -> UUID:
    """An application link for a unit; its token is random bytes no test needs back."""
    return _one(
        connection,
        "INSERT INTO trust_application_links"
        " (pmc_id, unit_id, token_sha256, monthly_rent, opened_by)"
        " VALUES (%s, %s, %s, %s, %s) RETURNING id",
        (pmc_id, unit_id, os.urandom(32), Decimal(rent), staff_id),
    )


def submit_application(
    connection, pmc_id, link_id, income="4500.00", applicants=(("Applicant 1", None),)
) -> UUID:
    """An application through a link, with its applicants (name, email or a made-up one)."""
    application = _one(
        connection,
        "INSERT INTO trust_applications"
        " (pmc_id, link_id, status_token_sha256, move_in_on, monthly_income)"
        " VALUES (%s, %s, %s, DATE '2026-11-01', %s) RETURNING id",
        (pmc_id, link_id, os.urandom(32), Decimal(income)),
    )
    for n, (name, email) in enumerate(applicants, 1):
        connection.execute(
            "INSERT INTO trust_applicants (pmc_id, application_id, position, display_name, email)"
            " VALUES (%s, %s, %s, %s, %s)",
            (
                pmc_id,
                application,
                n,
                name,
                email or f"applicant{n}.{application.hex[:8]}@example.com",
            ),
        )
    return application


def record_screening(
    connection, pmc_id, application_id, staff_id, recommendation="approve", reasons=()
) -> UUID:
    return _one(
        connection,
        "INSERT INTO trust_screenings (pmc_id, application_id, partner, partner_reference,"
        " agency, recommendation, reasons, requested_by)"
        " VALUES (%s, %s, 'Test partner', 'ref-1', 'Test agency (synthetic)', %s, %s, %s)"
        " RETURNING id",
        (pmc_id, application_id, recommendation, list(reasons), staff_id),
    )


def record_decision(
    connection, pmc_id, application_id, staff_id, decision, screening_id=None, conditions=None
) -> None:
    connection.execute(
        "INSERT INTO trust_application_decisions"
        " (pmc_id, application_id, decision, conditions, screening_id, decided_by)"
        " VALUES (%s, %s, %s, %s, %s, %s)",
        (pmc_id, application_id, decision, conditions, screening_id, staff_id),
    )


def record_adverse_action(
    connection, pmc_id, application_id, staff_id, reasons=("Income too low",)
) -> None:
    connection.execute(
        "INSERT INTO trust_adverse_actions"
        " (pmc_id, application_id, reasons, agency, notice_given_on, recorded_by)"
        " VALUES (%s, %s, %s, 'Test agency (synthetic)', DATE '2026-10-11', %s)",
        (pmc_id, application_id, list(reasons), staff_id),
    )


LEASE_PDF = b"%PDF-1.4 a synthetic lease"


def offer_lease(
    connection,
    pmc_id,
    unit_id,
    staff_id,
    starts_on,
    ends_on=None,
    rent="1500.00",
    application_id=None,
    renews_lease_id=None,
    signers=(("Signer 1", "signer1@example.com"),),
) -> UUID:
    """A lease offer (for an application or renewing a lease) and its signers."""
    offer = _one(
        connection,
        "INSERT INTO trust_lease_offers (pmc_id, application_id, renews_lease_id, unit_id,"
        " starts_on, ends_on, monthly_rent, document_title, document, offered_by)"
        " VALUES (%s, %s, %s, %s, %s, %s, %s, 'Lease', %s, %s) RETURNING id",
        (
            pmc_id,
            application_id,
            renews_lease_id,
            unit_id,
            starts_on,
            ends_on,
            Decimal(rent),
            LEASE_PDF,
            staff_id,
        ),
    )
    for n, (name, email) in enumerate(signers, 1):
        connection.execute(
            "INSERT INTO trust_lease_offer_signers (pmc_id, offer_id, position, display_name,"
            " email) VALUES (%s, %s, %s, %s, %s)",
            (pmc_id, offer, n, name, email),
        )
    return offer


def send_offer(connection, pmc_id, offer_id, staff_id) -> None:
    connection.execute(
        "INSERT INTO trust_signature_requests (pmc_id, offer_id, partner, partner_reference,"
        " sent_by) VALUES (%s, %s, 'Test partner', %s, %s)",
        (pmc_id, offer_id, f"ref-{offer_id}", staff_id),
    )


def sign_offer(connection, pmc_id, offer_id, position, signed_at=None) -> None:
    connection.execute(
        "INSERT INTO trust_signatures (pmc_id, offer_id, position, signed_at)"
        " VALUES (%s, %s, %s, coalesce(%s, now()))",
        (pmc_id, offer_id, position, signed_at),
    )


def complete_offer(connection, pmc_id, offer_id, staff_id) -> UUID:
    return _one(
        connection,
        "SELECT trust_complete_lease_offer(%s, %s, %s)",
        (pmc_id, offer_id, staff_id),
    )


def approve_bill(connection, pmc_id, bill_id, approved_on, approved_by, owner_id=None) -> None:
    """Record the owner's approval of a bill over the approval limit (owner_id: given in the
    portal)."""
    connection.execute(
        "INSERT INTO trust_bill_approvals (pmc_id, bill_id, approved_on, approved_by, owner_id)"
        " VALUES (%s, %s, %s, %s, %s)",
        (pmc_id, bill_id, approved_on, approved_by, owner_id),
    )


def decline_bill(
    connection, pmc_id, bill_id, declined_on, declined_by, reason="Too much", owner_id=None
) -> None:
    """Record the owner's decline of a bill (owner_id: declined in the portal)."""
    connection.execute(
        "INSERT INTO trust_bill_declines"
        " (pmc_id, bill_id, declined_on, declined_by, reason, owner_id)"
        " VALUES (%s, %s, %s, %s, %s, %s)",
        (pmc_id, bill_id, declined_on, declined_by, reason, owner_id),
    )


def set_aside_bill(connection, pmc_id, bill_id, event_at) -> str:
    return _one(connection, "SELECT trust_set_aside_bill(%s, %s, %s)", (pmc_id, bill_id, event_at))


def pay_bill(connection, pmc_id, bill_id, event_at) -> str:
    return _one(connection, "SELECT trust_pay_bill(%s, %s, %s)", (pmc_id, bill_id, event_at))


def charge_rent_due(connection, pmc_id, due_on) -> int:
    """Charge every lease in effect on due_on its monthly rent; the number charged."""
    return _one(connection, "SELECT trust_charge_rent_due(%s, %s)", (pmc_id, due_on))


def add_recurring_charge(
    connection, pmc_id, lease_id, amount, starts_on, memo="Pet rent", kind="fee"
) -> UUID:
    """A fee (or credit) the lease owes every month besides its rent, from starts_on."""
    return _one(
        connection,
        "INSERT INTO trust_recurring_charges (pmc_id, lease_id, kind, amount, memo, starts_on)"
        " VALUES (%s, %s, %s, %s, %s, %s) RETURNING id",
        (pmc_id, lease_id, kind, Decimal(amount), memo, starts_on),
    )


def stop_recurring_charge(connection, pmc_id, recurring_charge_id, ends_on) -> None:
    """No longer charged for due dates from ends_on."""
    connection.execute(
        "INSERT INTO trust_recurring_charge_ends (pmc_id, recurring_charge_id, ends_on)"
        " VALUES (%s, %s, %s)",
        (pmc_id, recurring_charge_id, ends_on),
    )


def charge_recurring_due(connection, pmc_id, due_on) -> int:
    """Charge every recurring charge in force on due_on; the number charged."""
    return _one(connection, "SELECT trust_charge_recurring_due(%s, %s)", (pmc_id, due_on))


def add_late_fee_policy(
    connection, pmc_id, property_id, starts_on, grace_days, flat_fee="0", percent="0", maximum=None
) -> UUID:
    """A property's late fee terms, in force from starts_on."""
    return _one(
        connection,
        "INSERT INTO trust_late_fee_policies"
        " (pmc_id, property_id, starts_on, grace_days, flat_fee, percent, maximum)"
        " VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id",
        (
            pmc_id,
            property_id,
            starts_on,
            grace_days,
            Decimal(flat_fee),
            Decimal(percent),
            None if maximum is None else Decimal(maximum),
        ),
    )


def add_nsf_fee_policy(connection, pmc_id, property_id, starts_on, fee) -> UUID:
    """A property's NSF fee, in force from starts_on."""
    return _one(
        connection,
        "INSERT INTO trust_nsf_fee_policies (pmc_id, property_id, starts_on, fee)"
        " VALUES (%s, %s, %s, %s) RETURNING id",
        (pmc_id, property_id, starts_on, Decimal(fee)),
    )


def charge_nsf_fee(connection, pmc_id, returned_key) -> UUID | None:
    """Charge the NSF fee of the payment returned under returned_key; its charge, or None."""
    return _one(connection, "SELECT trust_charge_nsf_fee(%s, %s)", (pmc_id, returned_key))


def assess_late_fees(connection, pmc_id, as_of) -> int:
    """Charge the late fee of each rent unpaid when its grace ran out before as_of."""
    return _one(connection, "SELECT trust_assess_late_fees(%s, %s)", (pmc_id, as_of))


def open_with_balances(
    connection, pmc_id, bank_id, opened_on, balances, book_cash, entered_by="Preparer 1"
) -> list[str]:
    """Open a trust bank account with the balances carried over from another system:
    `balances` maps each ledger account to what it held at the start of opened_on. The
    transfer ids, one per account in byte order of its id."""
    return _one(
        connection,
        "SELECT trust_post_opening_balances(%s, %s, %s, %s, %s, %s)",
        (
            pmc_id,
            bank_id,
            opened_on,
            Jsonb(balances),
            None if book_cash is None else Decimal(book_cash),
            entered_by,
        ),
    )


def add_recurring_bill(
    connection,
    pmc_id,
    vendor_id,
    account,
    prefix,
    amount,
    day_of_month,
    starts_on,
    memo="Landscaping",
) -> UUID:
    """A bill that comes every month on day_of_month, from starts_on, paid from `account`."""
    return _one(
        connection,
        "INSERT INTO trust_recurring_bills (pmc_id, vendor_id, ledger_account_id,"
        " reference_prefix, amount, memo, day_of_month, starts_on)"
        " VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING id",
        (pmc_id, vendor_id, account, prefix, Decimal(amount), memo, day_of_month, starts_on),
    )


def end_recurring_bill(connection, pmc_id, recurring_bill_id, ends_on) -> None:
    """Stop a recurring bill: no bill due on or after ends_on."""
    connection.execute(
        "INSERT INTO trust_recurring_bill_ends (pmc_id, recurring_bill_id, ends_on)"
        " VALUES (%s, %s, %s)",
        (pmc_id, recurring_bill_id, ends_on),
    )


def enter_recurring_bills(connection, pmc_id, through) -> int:
    """Enter every recurring bill's monthly bills due on or before `through`; how many."""
    return _one(connection, "SELECT trust_enter_recurring_bills(%s, %s)", (pmc_id, through))


def carry_over_vendor_payments(connection, pmc_id, vendor_id, year, paid_before, amount) -> None:
    """What the old system paid a vendor in `year` before the cutover, `paid_before`."""
    connection.execute(
        "INSERT INTO trust_vendor_payments_carried_over"
        " (pmc_id, vendor_id, year, paid_before, amount) VALUES (%s, %s, %s, %s, %s)",
        (pmc_id, vendor_id, year, paid_before, Decimal(amount)),
    )


def contribute(connection, pmc_id, account, request_key, amount, event_at=None, memo=None) -> str:
    """An owner's money into their property account, as its own kind; the transfer id."""
    return _one(
        connection,
        "SELECT trust_record_owner_contribution(%s, %s, %s, %s, %s, %s)",
        (pmc_id, account, request_key, None if amount is None else Decimal(amount), event_at, memo),
    )


def hold_payments(connection, pmc_id, lease_id, starts_on, reason="Eviction filed") -> UUID:
    """Stop payments dated on or after starts_on (UTC) being matched to the lease's charges."""
    return _one(
        connection,
        "INSERT INTO trust_payment_holds (pmc_id, lease_id, starts_on, reason)"
        " VALUES (%s, %s, %s, %s) RETURNING id",
        (pmc_id, lease_id, starts_on, reason),
    )


def release_hold(connection, pmc_id, hold_id, ends_on, released_by="Manager 1") -> None:
    """End a payment hold: payments dated on or after ends_on are matched again."""
    connection.execute(
        "INSERT INTO trust_payment_hold_releases (pmc_id, hold_id, ends_on, released_by)"
        " VALUES (%s, %s, %s, %s)",
        (pmc_id, hold_id, ends_on, released_by),
    )


def allow_payment(connection, pmc_id, hold_id, transfer_id, allowed_by="Manager 1") -> None:
    """Accept one payment despite a hold."""
    connection.execute(
        "INSERT INTO trust_payment_hold_allowances (pmc_id, hold_id, transfer_id, allowed_by)"
        " VALUES (%s, %s, %s, %s)",
        (pmc_id, hold_id, transfer_id, allowed_by),
    )


def add_staff(connection, pmc_id, name="Staff 1") -> UUID:
    """A member of the PMC's staff, whom the application names on the writes they make."""
    return _one(
        connection,
        "INSERT INTO trust_staff (pmc_id, display_name) VALUES (%s, %s) RETURNING id",
        (pmc_id, name),
    )


@contextmanager
def acting_as(connection, staff_id) -> Iterator[None]:
    """Name a staff member (or, with None, nobody) on every write the session makes inside the
    block, as the application will for whoever is signed in."""
    before = connection.execute(
        "SELECT coalesce(current_setting('trust.staff_id', true), '')"
    ).fetchone()[0]
    connection.execute(
        "SELECT set_config('trust.staff_id', %s, false)",
        ("" if staff_id is None else str(staff_id),),
    )
    try:
        yield
    finally:
        connection.execute("SELECT set_config('trust.staff_id', %s, false)", (before,))


ROLES = ("admin", "approve", "disburse")


def change_role(connection, pmc_id, staff_id, role, granted=True, by=None) -> None:
    """Grant (or take away) a role as the admin `by`; with None, nobody is named, as for a PMC's
    first admin."""
    with acting_as(connection, by):
        connection.execute(
            "INSERT INTO trust_staff_roles (pmc_id, staff_id, role, granted)"
            " VALUES (%s, %s, %s, %s)",
            (pmc_id, staff_id, role, granted),
        )


def roles_of(connection, pmc_id, staff_id) -> set[str]:
    return {
        role
        for role in ROLES
        if _one(connection, "SELECT trust_staff_holds(%s, %s, %s)", (pmc_id, staff_id, role))
    }


def admin_of(connection, pmc_id) -> UUID:
    """The PMC's first admin; with none, an 'Admin 1' made one."""
    row = connection.execute(
        "SELECT s.id FROM trust_staff AS s WHERE s.pmc_id = %s"
        " AND trust_staff_holds(s.pmc_id, s.id, 'admin') ORDER BY s.created_at, s.id LIMIT 1",
        (pmc_id,),
    ).fetchone()
    if row is not None:
        return row[0]
    admin = add_staff(connection, pmc_id, "Admin 1")
    change_role(connection, pmc_id, admin, "admin")
    return admin


def give_roles(connection, pmc_id, staff_id, roles) -> None:
    for role in roles:
        first_admin = (
            role == "admin"
            and not connection.execute(
                "SELECT 1 FROM trust_staff_roles WHERE pmc_id = %s AND role = 'admin'", (pmc_id,)
            ).fetchone()
        )
        by = None if first_admin else admin_of(connection, pmc_id)
        change_role(connection, pmc_id, staff_id, role, by=by)


def staff_named(connection, pmc_id, name, roles=()) -> UUID:
    """The PMC's staff member of that name, added with these roles if there is none."""
    row = connection.execute(
        "SELECT id FROM trust_staff WHERE pmc_id = %s AND display_name = %s", (pmc_id, name)
    ).fetchone()
    if row is not None:
        return row[0]
    staff_id = add_staff(connection, pmc_id, name)
    give_roles(connection, pmc_id, staff_id, roles)
    return staff_id


def propose_entry(connection, pmc_id, staff_id, event_on, memo, lines) -> UUID:
    """A journal entry proposed by a staff member: lines of (from, to, amount)."""
    rows = sql.SQL(", ").join(sql.SQL("(%s, %s, %s::numeric)::transfer_request") for _ in lines)
    query = sql.SQL(
        "SELECT trust_propose_journal_entry(%s, %s, %s, ARRAY[{}]::transfer_request[])"
    ).format(rows)
    with acting_as(connection, staff_id):
        return _one(
            connection,
            query,
            [pmc_id, event_on, memo, *(value for line in lines for value in line)],
        )


def post_entry(connection, pmc_id, entry_id, staff_id) -> list[str]:
    """Post a journal entry as a staff member; its transfer ids, line by line."""
    with acting_as(connection, staff_id):
        return _one(connection, "SELECT trust_post_journal_entry(%s, %s)", (pmc_id, entry_id))


def decline_entry(connection, pmc_id, entry_id, staff_id, reason="Not needed") -> None:
    with acting_as(connection, staff_id):
        connection.execute(
            "SELECT trust_decline_journal_entry(%s, %s, %s)", (pmc_id, entry_id, reason)
        )


def import_statement(
    connection,
    pmc_id,
    bank_id,
    staff_id,
    first_day,
    last_day,
    lines,
    opening="0.00",
    source="csv",
    file_sha256=None,
) -> tuple[UUID, list[UUID]]:
    """Import a bank statement of `lines` ((posted_on, amount, description, reference) each),
    whole, in one transaction: its id and its lines' ids, in order."""
    total = sum((Decimal(line[1]) for line in lines), Decimal(opening))
    with connection.transaction():
        statement = _one(
            connection,
            "INSERT INTO trust_bank_statements (pmc_id, bank_account_id, first_day, last_day,"
            " opening_balance, closing_balance, line_count, source, file_sha256, imported_by)"
            " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id",
            (
                pmc_id,
                bank_id,
                first_day,
                last_day,
                Decimal(opening),
                total,
                len(lines),
                source,
                file_sha256 or uuid.uuid4().hex * 2,
                staff_id,
            ),
        )
        ids = [
            _one(
                connection,
                "INSERT INTO trust_bank_statement_lines (pmc_id, statement_id, line_no,"
                " posted_on, amount, description, bank_reference)"
                " VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id",
                (pmc_id, statement, n, posted_on, Decimal(amount), description, reference),
            )
            for n, (posted_on, amount, description, reference) in enumerate(lines, start=1)
        ]
    return statement, ids


def match_lines(connection, pmc_id, bank_id, line_ids, transfer_ids, staff_id, item_ids=()) -> UUID:
    """Match statement lines to transfers (and cutover items the bank cleared)."""
    return _one(
        connection,
        "SELECT trust_match_bank_lines(%s, %s, %s::uuid[], %s::text[], %s::uuid[], %s)",
        (pmc_id, bank_id, list(line_ids), list(transfer_ids), list(item_ids), staff_id),
    )


def unmatch_lines(connection, pmc_id, match_id, staff_id) -> None:
    connection.execute("SELECT trust_unmatch_bank_lines(%s, %s, %s)", (pmc_id, match_id, staff_id))


def bank_reconciliation(connection, pmc_id, bank_id, through) -> list[tuple]:
    """trust_report_bank_reconciliation's rows: (line, item, detail, amount)."""
    return connection.execute(
        "SELECT line, item, detail, amount FROM trust_report_bank_reconciliation(%s, %s, %s)",
        (pmc_id, bank_id, through),
    ).fetchall()


def prepare_bank(connection, pmc_id, bank_id, through, staff_id) -> UUID:
    """Month-end through a day, prepared by a staff member."""
    with acting_as(connection, staff_id):
        return _one(
            connection,
            "SELECT trust_prepare_bank_reconciliation(%s, %s, %s)",
            (pmc_id, bank_id, through),
        )


def approve_prepared(connection, pmc_id, preparation_id, staff_id) -> UUID:
    with acting_as(connection, staff_id):
        return _one(
            connection,
            "SELECT trust_approve_bank_reconciliation(%s, %s)",
            (pmc_id, preparation_id),
        )


def approve_bank(connection, pmc_id, bank_id, through) -> UUID:
    """Month-end through a day, prepared by 'Preparer 1' and approved by 'Approver 1'."""
    preparer = staff_named(connection, pmc_id, "Preparer 1")
    approver = staff_named(connection, pmc_id, "Approver 1", ["approve"])
    prepared = prepare_bank(connection, pmc_id, bank_id, through, preparer)
    return approve_prepared(connection, pmc_id, prepared, approver)


def carry_over(
    connection, pmc_id, bank_id, staff_id, issued_on, amount, description="Check", reference=None
) -> UUID:
    """A check (negative) or deposit the bank had not cleared at the account's cutover."""
    return _one(
        connection,
        "INSERT INTO trust_cutover_items (pmc_id, bank_account_id, issued_on, amount,"
        " description, reference, entered_by) VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id",
        (pmc_id, bank_id, issued_on, Decimal(amount), description, reference, staff_id),
    )


def sign_off(connection, pmc_id, bank_id, staff_id) -> None:
    connection.execute(
        "INSERT INTO trust_cutover_signoffs (pmc_id, bank_account_id, signed_by)"
        " VALUES (%s, %s, %s)",
        (pmc_id, bank_id, staff_id),
    )


def match_opening(connection, pmc_id, bank_id, line_id, staff_id) -> UUID:
    return _one(
        connection,
        "SELECT trust_match_cutover_opening(%s, %s, %s, %s)",
        (pmc_id, bank_id, line_id, staff_id),
    )


def balance(connection, account) -> Decimal:
    return _one(connection, "SELECT balance FROM pgledger_accounts WHERE id = %s", (account,))


def snapshot(connection, accounts):
    """Everything a refused transfer must leave untouched: balances, versions, transfers and
    entries for the given accounts."""
    return connection.execute(
        """
        SELECT
            (SELECT array_agg((id, balance, version) ORDER BY id)
               FROM pgledger_accounts WHERE id = ANY(%(ids)s)),
            (SELECT count(*) FROM pgledger_transfers
              WHERE from_account_id = ANY(%(ids)s) OR to_account_id = ANY(%(ids)s)),
            (SELECT count(*) FROM pgledger_entries WHERE account_id = ANY(%(ids)s))
        """,
        {"ids": list(accounts)},
    ).fetchone()


RUNNING_BALANCE_BREAKS = """
WITH ordered AS (
    SELECT e.account_id, e.account_version, e.amount,
           e.account_previous_balance AS previous, e.account_current_balance AS current,
           lag(e.account_current_balance, 1, 0::numeric) OVER w AS expected_previous,
           row_number() OVER w AS position
    FROM pgledger_entries e
    WHERE e.account_id = ANY(%(ids)s)
    WINDOW w AS (PARTITION BY e.account_id ORDER BY e.account_version)
)
SELECT account_id, account_version FROM ordered
WHERE previous <> expected_previous OR previous + amount <> current
   OR account_version <> position
UNION ALL
SELECT a.id, a.version
FROM pgledger_accounts a
LEFT JOIN LATERAL (
    SELECT count(*) AS entries,
           (array_agg(e.account_current_balance ORDER BY e.account_version DESC))[1] AS last
    FROM pgledger_entries e WHERE e.account_id = a.id
) s ON true
WHERE a.id = ANY(%(ids)s) AND (a.version <> s.entries OR a.balance <> coalesce(s.last, 0))
"""


def running_balance_breaks(connection, accounts) -> list[tuple]:
    """Entries whose running balance doesn't chain, or accounts whose version and balance don't
    match their last entry. Each entry must start where the previous one ended (from zero), add
    its amount, and carry the account's version: 1, 2, 3... A ledger an auditor can walk line
    by line (Cal. Reg. 2831's running balance)."""
    return connection.execute(RUNNING_BALANCE_BREAKS, {"ids": list(accounts)}).fetchall()


@contextmanager
def language_sorted_database(database_url) -> Iterator[psycopg.Connection]:
    """A connection to a fresh, fully migrated database that sorts text by English rules (ICU
    en-US), as most servers do: "owner a" sorts before "Owner b" there, and after it in byte
    order. The test database itself may sort either way, so a report's ordering is checked here."""
    with scratch_database(database_url, "pms_icu", icu_locale="en-US") as url:
        migrated = Dbmate(shutil.which("dbmate"), MIGRATIONS, url).run("--no-dump-schema", "up")
        assert migrated.returncode == 0, migrated.stderr
        with psycopg.connect(url, autocommit=True) as conn:
            assert conn.execute("SELECT 'owner a' < 'Owner b'").fetchone() == (True,)
            yield conn


def wait_until_blocked(conn, pid):
    for _ in range(1000):
        row = conn.execute(
            "SELECT wait_event_type FROM pg_stat_activity WHERE pid = %s", (pid,)
        ).fetchone()
        if row and row[0] == "Lock":
            return
        time.sleep(0.01)
    raise AssertionError("the second transaction never waited for the first")


def in_background(database_url, work):
    """Run work(connection) on a new autocommit connection in a thread; return the thread,
    the connection's backend pid and a dict that receives the result or the error."""
    outcome: dict = {}
    ready = threading.Event()
    pid: list[int] = []

    def run():
        with psycopg.connect(database_url, autocommit=True) as worker:
            worker.execute("SET lock_timeout = '20s'")
            pid.append(worker.execute("SELECT pg_backend_pid()").fetchone()[0])
            ready.set()
            try:
                outcome["result"] = work(worker)
            except Exception as exc:  # asserted by the caller
                outcome["error"] = exc

    thread = threading.Thread(target=run)
    thread.start()
    assert ready.wait(timeout=30)
    return thread, pid[0], outcome


def sign_for(
    connection,
    pmc_id,
    bank_account_id,
    staff_id,
    role="signer",
    granted=True,
    license_number="LIC-0001",
    bond=None,
    on=date(2026, 1, 1),
    by=None,
) -> None:
    """Name a trust bank account's broker (as the admin `by`) or authorize a signer (as its broker
    `by`), licensed or, with `bond` (amount, the day it runs out), bonded; granted false takes it
    away. With nobody named, as the server's command line."""
    amount, expires = bond if bond is not None else (None, None)
    with acting_as(connection, by):
        connection.execute(
            "INSERT INTO trust_account_signers (pmc_id, bank_account_id, staff_id, role, granted,"
            " license_number, bond_amount, bond_expires_on, authorized_on)"
            " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (
                pmc_id,
                bank_account_id,
                staff_id,
                role,
                granted,
                license_number if granted and bond is None else None,
                None if amount is None else Decimal(amount),
                expires,
                on,
            ),
        )
