"""Receipts for what a deposit keeps, as the database keeps them (migration 20261022000120; the
founder's review q039): a receipt is one of the lease's documents linked to one of its fees, once;
nothing is rewritten; the AI role reads the link but never the file; and a deposit statement's
email names one tenant and their lease. Every name is synthetic."""

from datetime import date

import psycopg
import pytest
from helpers import add_document, add_tenant, add_unit, make_pmc, open_lease


@pytest.fixture
def pmc(conn):
    return make_pmc(conn)


def a_lease(conn, pmc, unit_name="1A", tenant_name="Tenant 9"):
    prop = pmc.owners[0].property_id
    tenant = add_tenant(conn, pmc.pmc_id, prop, tenant_name)
    unit = add_unit(conn, pmc.pmc_id, prop, unit_name)
    return open_lease(conn, pmc.pmc_id, unit, date(2026, 1, 1), None, "900.00", [tenant]), tenant


def a_charge(conn, pmc, lease, kind="fee"):
    return conn.execute(
        "INSERT INTO trust_charges (pmc_id, lease_id, due_on, kind, amount, memo)"
        " VALUES (%s, %s, '2026-10-31', %s, 120, 'Cleaning') RETURNING id",
        (pmc.pmc_id, lease, kind),
    ).fetchone()[0]


def link(conn, pmc_id, charge, document):
    conn.execute(
        "INSERT INTO trust_deduction_receipts (pmc_id, charge_id, document_id) VALUES (%s, %s, %s)",
        (pmc_id, charge, document),
    )


def test_a_receipt_is_a_document_of_the_fees_own_lease_once(conn, pmc):
    lease, _ = a_lease(conn, pmc)
    other, _ = a_lease(conn, pmc, "1B", "Tenant 8")
    fee = a_charge(conn, pmc, lease)
    invoice = add_document(conn, pmc.pmc_id, "lease_document", lease, title="Invoice")

    link(conn, pmc.pmc_id, fee, invoice)
    with pytest.raises(psycopg.errors.UniqueViolation):
        link(conn, pmc.pmc_id, fee, invoice)
    refused = psycopg.errors.CheckViolation
    for document, charge in (
        (add_document(conn, pmc.pmc_id, "lease_document", other, title="Theirs"), fee),
        (add_document(conn, pmc.pmc_id, "move_out_photo", lease, title="Photo"), fee),
        (invoice, a_charge(conn, pmc, lease, kind="rent")),
    ):
        with pytest.raises(refused, match="a receipt is a document of the lease"):
            link(conn, pmc.pmc_id, charge, document)


def test_a_receipt_and_its_fee_are_of_one_company(conn, pmc):
    lease, _ = a_lease(conn, pmc)
    fee = a_charge(conn, pmc, lease)
    invoice = add_document(conn, pmc.pmc_id, "lease_document", lease, title="Invoice")
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        link(conn, make_pmc(conn).pmc_id, fee, invoice)


def test_nothing_is_rewritten_and_the_ai_reads_the_link_only(conn, app_conn, ai_conn, pmc):
    lease, _ = a_lease(conn, pmc)
    fee = a_charge(conn, pmc, lease)
    invoice = add_document(conn, pmc.pmc_id, "lease_document", lease, title="Invoice")
    link(app_conn, pmc.pmc_id, fee, invoice)

    for change in (
        "UPDATE trust_deduction_receipts SET created_at = now()",
        "DELETE FROM trust_deduction_receipts",
    ):
        with pytest.raises(psycopg.errors.RestrictViolation, match="append-only"):
            conn.execute(change)
    assert ai_conn.execute(
        "SELECT charge_id FROM trust_deduction_receipts WHERE document_id = %s", (invoice,)
    ).fetchall() == [(fee,)]
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        ai_conn.execute("SELECT content FROM trust_document_files")
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        app_conn.execute("DELETE FROM trust_deduction_receipts")


def test_a_deposit_statement_email_names_one_tenant_and_their_lease(conn, pmc):
    lease, tenant = a_lease(conn, pmc)

    def write(**about):
        columns = ["pmc_id", "kind", "to_address", "subject", "body", *about]
        conn.execute(
            f"INSERT INTO trust_emails ({', '.join(columns)})"
            f" VALUES ({', '.join(['%s'] * len(columns))})",
            (pmc.pmc_id, "deposit_statement", "t@example.com", "S", "B", *about.values()),
        )

    # Only the constraint is tried here: the email it takes is never kept, so no delivery of
    # another test's tries to make a statement for a deposit not returned.
    with conn.transaction(force_rollback=True):
        write(tenant_id=tenant, lease_id=lease)
    for about in ({"tenant_id": tenant}, {"lease_id": lease}):
        with pytest.raises(psycopg.errors.CheckViolation, match="recipient_check"):
            write(**about)
    with pytest.raises(psycopg.errors.CheckViolation, match="recipient_check"):
        write(tenant_id=tenant, lease_id=lease, owner_id=pmc.owners[0].owner_id)
