"""Balance notices in the database (migration 20261023000201): a run of notices from one template
version on one day, naming who sent it; each notice of it is its notice for one lease, from that
version on that day, made for a lease that owed; each tenant's email of it is a staff member's
message to that tenant, once. Runs and notices are append-only and audited; the AI role reads
none of it. The API's side is in tests/api/test_balance_notices_api.py."""

from datetime import date
from uuid import uuid4

import psycopg
import pytest
from helpers import add_staff, add_tenant, add_unit, make_pmc, open_lease

DAY = date(2026, 10, 23)


@pytest.fixture
def pmc(conn):
    return make_pmc(conn)


@pytest.fixture
def made(conn, pmc):
    """A notice template's version, a lease, a staff member and a notice of the lease from it."""
    staff = add_staff(conn, pmc.pmc_id)
    prop = pmc.owners[0].property_id
    tenant = add_tenant(conn, pmc.pmc_id, prop, "Reese Sample")
    lease = open_lease(
        conn, pmc.pmc_id, add_unit(conn, pmc.pmc_id, prop), date(2026, 1, 1), None, "900.00",
        [tenant],
    )  # fmt: skip
    template = conn.execute(
        "INSERT INTO trust_notice_templates (pmc_id) VALUES (%s) RETURNING id", (pmc.pmc_id,)
    ).fetchone()[0]
    version = conn.execute(
        "INSERT INTO trust_notice_template_versions (pmc_id, template_id, name, body)"
        " VALUES (%s, %s, 'Reminder', 'Hello.') RETURNING id",
        (pmc.pmc_id, template),
    ).fetchone()[0]
    return {"staff": staff, "tenant": tenant, "lease": lease, "version": version}


def notice(conn, pmc, made, day=DAY, lease=None):
    return conn.execute(
        "INSERT INTO trust_notices (pmc_id, lease_id, template_version_id, dated_on, title,"
        " letterhead, body) VALUES (%s, %s, %s, %s, 'Reminder', 'Synthetic PMC', 'Hello.')"
        " RETURNING id",
        (pmc.pmc_id, lease or made["lease"], made["version"], day),
    ).fetchone()[0]


def run(conn, pmc, made, day=DAY):
    return conn.execute(
        "INSERT INTO trust_balance_notice_runs (pmc_id, template_version_id, dated_on)"
        " VALUES (%s, %s, %s) RETURNING id",
        (pmc.pmc_id, made["version"], day),
    ).fetchone()[0]


def balance(conn, pmc, notice_id, run_id, lease, owed="125.00"):
    conn.execute(
        "INSERT INTO trust_balance_notices (notice_id, pmc_id, run_id, lease_id, owed)"
        " VALUES (%s, %s, %s, %s, %s)",
        (notice_id, pmc.pmc_id, run_id, lease, owed),
    )


def test_a_balance_notice_is_its_runs_notice_for_its_lease(conn, pmc, made):
    sent = run(conn, pmc, made)
    other_day = notice(conn, pmc, made, date(2026, 10, 24))
    with pytest.raises(psycopg.errors.CheckViolation, match="its run's notice"), conn.transaction():
        balance(conn, pmc, other_day, sent, made["lease"])  # another day
    prop = pmc.owners[0].property_id
    second = open_lease(
        conn, pmc.pmc_id, add_unit(conn, pmc.pmc_id, prop, "Unit 2"), date(2026, 1, 1), None,
        "900.00", [add_tenant(conn, pmc.pmc_id, prop, "Avery Sample")],
    )  # fmt: skip
    mine = notice(conn, pmc, made)
    with pytest.raises(psycopg.errors.CheckViolation, match="its run's notice"), conn.transaction():
        balance(conn, pmc, mine, sent, second)  # another lease
    balance(conn, pmc, mine, sent, made["lease"])
    with pytest.raises(psycopg.errors.UniqueViolation), conn.transaction():
        balance(conn, pmc, notice(conn, pmc, made), sent, made["lease"])  # once a run per lease


def test_a_balance_notice_goes_only_to_a_lease_that_owes_in_whole_cents(conn, pmc, made):
    sent = run(conn, pmc, made)
    for owed in ("0", "-5.00", "1.005"):
        with pytest.raises(psycopg.errors.CheckViolation), conn.transaction():
            balance(conn, pmc, notice(conn, pmc, made), sent, made["lease"], owed)


def test_its_email_is_a_staff_message_to_one_of_its_tenants_once(conn, pmc, made):
    mine = notice(conn, pmc, made)
    balance(conn, pmc, mine, run(conn, pmc, made), made["lease"])

    def email(kind="message", tenant=made["tenant"], sent_by=made["staff"], notice_id=mine):
        conn.execute(
            "INSERT INTO trust_emails (pmc_id, kind, tenant_id, to_address, subject, body,"
            " sent_by, notice_id) VALUES (%s, %s, %s, 'reese@example.com', 'Reminder', 'Hello.',"
            " %s, %s)",
            (pmc.pmc_id, kind, tenant, sent_by, notice_id),
        )

    email()
    with pytest.raises(psycopg.errors.UniqueViolation), conn.transaction():
        email()
    with pytest.raises(psycopg.errors.ForeignKeyViolation), conn.transaction():
        email(notice_id=notice(conn, pmc, made))  # a notice that is no balance notice
    with pytest.raises(psycopg.errors.CheckViolation), conn.transaction():
        conn.execute(
            "INSERT INTO trust_emails (pmc_id, kind, owner_id, to_address, subject, body,"
            " sent_by, notice_id) VALUES (%s, 'message', %s, 'o@example.com', 'R', 'H', %s, %s)",
            (pmc.pmc_id, pmc.owners[0].owner_id, made["staff"], mine),
        )  # to an owner


def test_runs_and_notices_are_append_only_and_audited(conn, pmc, made):
    sent = run(conn, pmc, made)
    balance(conn, pmc, notice(conn, pmc, made), sent, made["lease"])
    for table in ("trust_balance_notice_runs", "trust_balance_notices"):
        for change in (f"UPDATE {table} SET pmc_id = pmc_id", f"DELETE FROM {table}"):
            with pytest.raises(psycopg.errors.RestrictViolation), conn.transaction():
                conn.execute(change)
    logged = {
        row[0]
        for row in conn.execute(
            "SELECT DISTINCT table_name FROM trust_audit_log WHERE pmc_id = %s", (pmc.pmc_id,)
        ).fetchall()
    }
    assert {"trust_balance_notice_runs", "trust_balance_notices"} <= logged


def test_a_run_names_who_sent_it(conn, pmc, made):
    conn.execute("SELECT set_config('trust.staff_id', %s, false)", (str(made["staff"]),))
    try:
        sent = run(conn, pmc, made)
    finally:
        conn.execute("SELECT set_config('trust.staff_id', '', false)")
    assert conn.execute(
        "SELECT recorded_by FROM trust_balance_notice_runs WHERE id = %s", (sent,)
    ).fetchone() == (made["staff"],)
    other = make_pmc(conn)
    with pytest.raises(psycopg.errors.ForeignKeyViolation), conn.transaction():
        conn.execute(
            "INSERT INTO trust_balance_notice_runs (pmc_id, template_version_id, dated_on)"
            " VALUES (%s, %s, %s)",
            (other.pmc_id, made["version"], DAY),
        )  # another company's template
    with pytest.raises(psycopg.errors.CheckViolation), conn.transaction():
        balance(conn, pmc, uuid4(), sent, made["lease"])  # no notice at all
