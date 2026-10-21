"""A vendor's private link, at the database (migration 20261018000086; the founder's review, q069).
The API's side is in tests/api/test_vendor_links_api.py. Every name here is synthetic."""

import hashlib
import uuid
from datetime import UTC, date, datetime

import psycopg
import pytest
from helpers import acting_as, add_staff, add_unit, add_work_order, make_pmc, step_work_order

NOW = datetime(2026, 10, 18, 9, tzinfo=UTC)


def link(conn, pmc_id, vendor, token=None):
    """A link with its own token (the test database is shared: no two alike)."""
    token = token or uuid.uuid4().hex
    return conn.execute(
        "INSERT INTO trust_vendor_links (pmc_id, vendor_id, token_sha256) VALUES (%s, %s, %s)"
        " RETURNING id",
        (pmc_id, vendor, hashlib.sha256(token.encode()).digest()),
    ).fetchone()[0]


def revoke(conn, pmc_id, of):
    conn.execute(
        "INSERT INTO trust_vendor_link_revocations (link_id, pmc_id) VALUES (%s, %s)", (of, pmc_id)
    )


def vendor(conn, pmc_id, name):
    return conn.execute(
        "INSERT INTO trust_vendors (pmc_id, display_name) VALUES (%s, %s) RETURNING id",
        (pmc_id, name),
    ).fetchone()[0]


class Job:
    """A work order at Unit 1, assigned to the PMC's vendor."""

    def __init__(self, conn):
        self.pmc = make_pmc(conn)
        pmc_id = self.pmc.pmc_id
        unit = add_unit(conn, pmc_id, self.pmc.owners[0].property_id, "Unit 1")
        self.order = add_work_order(conn, pmc_id, self.pmc.owners[0].property_id, unit, "Leak", NOW)
        step_work_order(conn, pmc_id, self.order, "assigned", NOW, self.pmc.vendor_id)
        self.link = link(conn, pmc_id, self.pmc.vendor_id)


@pytest.fixture
def job(conn):
    return Job(conn)


def through(conn, job, step, on=None, by=None):
    conn.execute(
        "INSERT INTO trust_work_order_steps"
        " (pmc_id, work_order_id, step, scheduled_for, taken_at, vendor_link_id)"
        " VALUES (%s, %s, %s, %s, now(), %s)",
        (job.pmc.pmc_id, job.order, step, on, by or job.link),
    )


def test_the_assigned_vendor_schedules_and_completes_through_their_link(conn, job):
    through(conn, job, "scheduled", date(2026, 10, 20))
    through(conn, job, "completed")
    assert conn.execute(
        "SELECT step, vendor_link_id FROM trust_work_order_steps WHERE work_order_id = %s"
        " ORDER BY created_at",
        (job.order,),
    ).fetchall() == [("assigned", None), ("scheduled", job.link), ("completed", job.link)]


@pytest.mark.parametrize("step", ["assigned", "cancelled"])
def test_a_link_only_schedules_or_completes(conn, job, step):
    with pytest.raises(psycopg.errors.CheckViolation, match="nothing else"):
        through(conn, job, step)


def test_only_the_vendor_last_assigned_and_only_while_the_link_stands(conn, job):
    pmc_id = job.pmc.pmc_id
    other = vendor(conn, pmc_id, "Vendor 2")
    theirs = link(conn, pmc_id, other)
    with pytest.raises(psycopg.errors.CheckViolation, match="not assigned"), conn.transaction():
        through(conn, job, "completed", by=theirs)
    # Reassigned to Vendor 2: the first vendor's link no longer reaches it.
    step_work_order(conn, pmc_id, job.order, "assigned", NOW.replace(hour=10), other)
    with pytest.raises(psycopg.errors.CheckViolation, match="not assigned"), conn.transaction():
        through(conn, job, "completed")
    revoke(conn, pmc_id, theirs)
    with pytest.raises(psycopg.errors.CheckViolation, match="taken back"), conn.transaction():
        through(conn, job, "completed", by=theirs)
    # Another company's link names no vendor of this one.
    elsewhere = make_pmc(conn)
    their_link = link(conn, elsewhere.pmc_id, elsewhere.vendor_id)
    with pytest.raises(psycopg.errors.CheckViolation), conn.transaction():
        through(conn, job, "completed", by=their_link)


def test_links_are_kept_only_as_digests_once_each_append_only_and_name_who(conn, job):
    pmc_id = job.pmc.pmc_id
    clerk = add_staff(conn, pmc_id, "Clerk 1")
    with acting_as(conn, clerk):
        token = uuid.uuid4().hex
        made = link(conn, pmc_id, job.pmc.vendor_id, token)
        revoke(conn, pmc_id, made)
    assert conn.execute(
        "SELECT l.recorded_by, r.recorded_by FROM trust_vendor_links AS l"
        " JOIN trust_vendor_link_revocations AS r ON r.link_id = l.id WHERE l.id = %s",
        (made,),
    ).fetchone() == (clerk, clerk)
    with pytest.raises(psycopg.errors.UniqueViolation), conn.transaction():
        revoke(conn, pmc_id, made)
    with pytest.raises(psycopg.errors.UniqueViolation), conn.transaction():
        link(conn, pmc_id, job.pmc.vendor_id, token)
    with pytest.raises(psycopg.errors.CheckViolation), conn.transaction():
        conn.execute(
            "INSERT INTO trust_vendor_links (pmc_id, vendor_id, token_sha256)"
            " VALUES (%s, %s, '\\x00')",
            (pmc_id, job.pmc.vendor_id),
        )
    for query in (
        "UPDATE trust_vendor_links SET vendor_id = vendor_id",
        "DELETE FROM trust_vendor_link_revocations",
    ):
        with pytest.raises(psycopg.errors.RestrictViolation, match="append-only"):
            conn.execute(query)
    assert conn.execute(
        "SELECT count(*) FROM trust_audit_log WHERE pmc_id = %s"
        " AND table_name IN ('trust_vendor_links', 'trust_vendor_link_revocations')",
        (pmc_id,),
    ).fetchone() == (3,)
