"""The old system's past transactions kept as history (migration 20261021000107; the founder's
review q129): each line is one tenant's, owner's property account's or vendor's, of its file's kind,
never dated after it came in, append-only, and never in the ledger."""

from datetime import UTC, date, datetime, timedelta

import psycopg
import pytest
from helpers import add_tenant, make_pmc


@pytest.fixture
def pmc(conn):
    return make_pmc(conn)


def a_file(conn, pmc_id, kind, digest=b"\x01" * 32, lines=1):
    return conn.execute(
        "INSERT INTO trust_history_files (pmc_id, kind, file_sha256, line_count)"
        " VALUES (%s, %s, %s, %s) RETURNING id",
        (pmc_id, kind, digest, lines),
    ).fetchone()[0]


def a_line(conn, pmc_id, file_id, column, party, posted=date(2025, 3, 1), line=2, unit=None):
    conn.execute(
        f"INSERT INTO trust_history_lines (pmc_id, file_id, line_no, {column}, posted_on, kind,"  # noqa: S608
        " description, amount, unit) VALUES (%s, %s, %s, %s, %s, 'Rent', 'March rent', 1200.00,"
        " %s)",
        (pmc_id, file_id, line, party, posted, unit),
    )


def test_each_line_is_of_its_files_kind_of_person(conn, pmc):
    tenant = add_tenant(conn, pmc.pmc_id, pmc.owners[0].property_id, "Tenant 9")
    tenants = a_file(conn, pmc.pmc_id, "tenant")
    owners = a_file(conn, pmc.pmc_id, "owner", b"\x02" * 32)
    a_line(conn, pmc.pmc_id, tenants, "tenant_id", tenant, unit="Apt 1")
    a_line(conn, pmc.pmc_id, owners, "owner_account", pmc.owners[0].account)

    with pytest.raises(psycopg.errors.CheckViolation, match="is not a tenant's"):
        a_line(conn, pmc.pmc_id, tenants, "vendor_id", pmc.vendor_id, line=3)
    with pytest.raises(psycopg.errors.CheckViolation, match="not an owner's property account"):
        a_line(conn, pmc.pmc_id, owners, "owner_account", pmc.operating_cash, line=3)
    with pytest.raises(psycopg.errors.CheckViolation):  # a unit is a tenant's line's only
        a_line(conn, pmc.pmc_id, owners, "owner_account", pmc.owners[0].account, line=4,
               unit="Apt 1")  # fmt: skip
    with pytest.raises(psycopg.errors.UniqueViolation):
        a_line(conn, pmc.pmc_id, tenants, "tenant_id", tenant)


def test_nothing_is_dated_after_it_came_in(conn, pmc):
    tenant = add_tenant(conn, pmc.pmc_id, pmc.owners[0].property_id, "Tenant 9")
    tenants = a_file(conn, pmc.pmc_id, "tenant")
    tomorrow = datetime.now(UTC).date() + timedelta(days=1)

    with pytest.raises(psycopg.errors.CheckViolation, match="which has not come yet"):
        a_line(conn, pmc.pmc_id, tenants, "tenant_id", tenant, posted=tomorrow)


def test_a_file_comes_in_once_and_history_is_never_rewritten_nor_posted(conn, pmc):
    moved = conn.execute("SELECT count(*) FROM pgledger_transfers").fetchone()[0]
    vendors = a_file(conn, pmc.pmc_id, "vendor")
    a_line(conn, pmc.pmc_id, vendors, "vendor_id", pmc.vendor_id)

    with pytest.raises(psycopg.errors.UniqueViolation):
        a_file(conn, pmc.pmc_id, "vendor")
    for change in (
        "UPDATE trust_history_lines SET amount = 0",
        "DELETE FROM trust_history_lines",
        "UPDATE trust_history_files SET line_count = 9",
        "DELETE FROM trust_history_files",
    ):
        with pytest.raises(psycopg.errors.RestrictViolation, match="append-only"):
            conn.execute(change)
    assert conn.execute("SELECT count(*) FROM pgledger_transfers").fetchone()[0] == moved


def test_a_line_is_of_its_own_company(conn, pmc):
    theirs = make_pmc(conn)
    vendors = a_file(conn, pmc.pmc_id, "vendor")

    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        a_line(conn, pmc.pmc_id, vendors, "vendor_id", theirs.vendor_id)
    with pytest.raises(psycopg.errors.CheckViolation, match="not an owner's property account"):
        owners = a_file(conn, pmc.pmc_id, "owner", b"\x03" * 32)
        a_line(conn, pmc.pmc_id, owners, "owner_account", theirs.owners[0].account)


def test_the_app_brings_history_in_and_the_ai_reads_it(conn, app_conn, ai_conn, pmc):
    vendors = a_file(app_conn, pmc.pmc_id, "vendor")
    a_line(app_conn, pmc.pmc_id, vendors, "vendor_id", pmc.vendor_id)

    assert ai_conn.execute(
        "SELECT description, amount FROM trust_history_lines WHERE file_id = %s", (vendors,)
    ).fetchall() == [("March rent", 1200)]
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        a_file(ai_conn, pmc.pmc_id, "vendor", b"\x04" * 32)
