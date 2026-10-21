"""Contact details (migration 20261016000064, ADR 0005): a tenant's, owner's or vendor's email,
phones and mailing address, the latest row holding; kept for good, naming who recorded them,
and never read by the AI role."""

import psycopg
import pytest
from helpers import acting_as, make_pmc, staff_named

ADDRESS = {
    "address_line_1": "100 Example Street",
    "address_line_2": "Suite 1",
    "city": "Sampletown",
    "state": "ZZ",
    "postal_code": "00000",
}


@pytest.fixture
def pmc(conn):
    return make_pmc(conn)


def tenant_of(conn, pmc):
    return conn.execute(
        "SELECT tenant_id FROM trust_ledger_accounts WHERE ledger_account_id = %s",
        (pmc.tenant_deposit,),
    ).fetchone()[0]


def record(conn, pmc, **fields):
    names = ", ".join(fields)
    marks = ", ".join(["%s"] * len(fields))
    conn.execute(
        f"INSERT INTO trust_contacts (pmc_id, {names}) VALUES (%s, {marks})",
        (pmc.pmc_id, *fields.values()),
    )


def latest(conn, column, person):
    return conn.execute(
        f"SELECT email, phone, city FROM trust_contacts WHERE {column} = %s"
        " ORDER BY created_at DESC, id DESC LIMIT 1",
        (person,),
    ).fetchone()


def test_each_person_has_their_details_and_a_change_is_a_new_row(conn, pmc):
    tenant = tenant_of(conn, pmc)
    record(conn, pmc, tenant_id=tenant, email="tenant1@example.com", phone="(555) 555-0100")
    record(
        conn, pmc, tenant_id=tenant, email="tenant1@example.net", phone="+1 555-555-0101", **ADDRESS
    )
    record(conn, pmc, owner_id=pmc.owners[0].owner_id, alternate_phone="555.555.0102")
    record(conn, pmc, vendor_id=pmc.vendor_id, email="vendor1@example.com")

    assert latest(conn, "tenant_id", tenant) == (
        "tenant1@example.net",
        "+1 555-555-0101",
        "Sampletown",
    )
    assert latest(conn, "owner_id", pmc.owners[0].owner_id) == (None, None, None)
    assert latest(conn, "vendor_id", pmc.vendor_id) == ("vendor1@example.com", None, None)


@pytest.mark.parametrize(
    "fields",
    [
        {"email": "not an email"},
        {"email": "a@b"},
        {"phone": "call me"},
        {"phone": "12"},
        {"alternate_phone": "555-555-0100 ext. 9"},
        # An address is whole, or not there.
        {"address_line_1": "100 Example Street"},
        {**ADDRESS, "postal_code": None},
        {"address_line_2": "Suite 1"},
        {"city": ""},
        {"address_line_1": "x" * 201, "city": "Sampletown", "state": "ZZ", "postal_code": "00000"},
    ],
)
def test_details_that_are_not_an_email_phone_or_whole_address_are_refused(conn, pmc, fields):
    with pytest.raises(psycopg.errors.CheckViolation):
        record(conn, pmc, tenant_id=tenant_of(conn, pmc), **fields)


def test_details_are_one_persons_of_the_pmc_and_name_who_recorded_them(conn, pmc):
    with pytest.raises(psycopg.errors.CheckViolation):
        record(conn, pmc, tenant_id=tenant_of(conn, pmc), owner_id=pmc.owners[0].owner_id)
    with pytest.raises(psycopg.errors.CheckViolation):
        record(conn, pmc, email="nobody@example.com")
    other = make_pmc(conn)
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        record(conn, other, vendor_id=pmc.vendor_id)

    manager = staff_named(conn, pmc.pmc_id, "Manager 1")
    with acting_as(conn, manager):
        record(conn, pmc, owner_id=pmc.owners[1].owner_id, phone="555-555-0103")
    assert conn.execute(
        "SELECT recorded_by FROM trust_contacts WHERE owner_id = %s", (pmc.owners[1].owner_id,)
    ).fetchall() == [(manager,)]


@pytest.mark.parametrize(
    "statement", ["UPDATE trust_contacts SET email = NULL", "DELETE FROM trust_contacts"]
)
def test_details_are_kept_for_good(conn, pmc, statement):
    record(conn, pmc, vendor_id=pmc.vendor_id, email="vendor1@example.com")
    with pytest.raises(psycopg.errors.RestrictViolation, match="append-only"):
        conn.execute(statement)


def test_the_ai_role_reads_neither_the_details_nor_their_audit_rows(conn, ai_conn, pmc):
    record(conn, pmc, vendor_id=pmc.vendor_id, email="vendor1@example.com")
    logged = (
        "SELECT count(*) FROM trust_audit_log WHERE table_name = 'trust_contacts' AND pmc_id = %s"
    )

    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        ai_conn.execute("SELECT 1 FROM trust_contacts")
    assert conn.execute(logged, (pmc.pmc_id,)).fetchone() == (1,)
    assert ai_conn.execute(logged, (pmc.pmc_id,)).fetchone() == (0,)
