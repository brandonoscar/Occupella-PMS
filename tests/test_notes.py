"""Notes (migration 20261016000066): each about exactly one record of its PMC, kept for good,
naming who wrote it, audited, and never read by the AI role."""

import psycopg
import pytest
from helpers import acting_as, make_pmc, staff_named


@pytest.fixture
def pmc(conn):
    return make_pmc(conn)


def note(conn, pmc, body="Gate code changed.", **about):
    columns = ", ".join(["pmc_id", "body", *about])
    marks = ", ".join(["%s"] * (2 + len(about)))
    return conn.execute(
        f"INSERT INTO trust_notes ({columns}) VALUES ({marks}) RETURNING id",
        (pmc.pmc_id, body, *about.values()),
    ).fetchone()[0]


def test_a_note_is_about_exactly_one_record(app_conn, pmc):
    note(app_conn, pmc, property_id=pmc.owners[0].property_id)
    with pytest.raises(psycopg.errors.CheckViolation):
        note(app_conn, pmc)
    with pytest.raises(psycopg.errors.CheckViolation):
        note(app_conn, pmc, property_id=pmc.owners[0].property_id, owner_id=pmc.owners[0].owner_id)


def test_a_note_says_something(app_conn, pmc):
    for body in ("", "   ", "x" * 5001):
        with pytest.raises(psycopg.errors.CheckViolation):
            note(app_conn, pmc, body=body, vendor_id=pmc.vendor_id)


def test_a_note_is_about_its_own_companys_record(app_conn, conn, pmc):
    other = make_pmc(conn)
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        note(app_conn, pmc, owner_id=other.owners[0].owner_id)


def test_notes_are_kept_for_good_name_who_wrote_them_and_are_audited(app_conn, conn, pmc):
    manager = staff_named(conn, pmc.pmc_id, "Manager 1")
    with acting_as(app_conn, manager):
        made = note(app_conn, pmc, owner_id=pmc.owners[0].owner_id)
    by = conn.execute("SELECT recorded_by FROM trust_notes WHERE id = %s", (made,)).fetchone()
    assert by == (manager,)
    for statement in ("UPDATE trust_notes SET body = 'x'", "DELETE FROM trust_notes"):
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            app_conn.execute(statement)
        with pytest.raises(psycopg.errors.RestrictViolation, match="append-only"):
            conn.execute(statement)
    assert conn.execute(
        "SELECT action, staff_id FROM trust_audit_log"
        " WHERE pmc_id = %s AND table_name = 'trust_notes'",
        (pmc.pmc_id,),
    ).fetchall() == [("insert", manager)]


def test_the_ai_role_reads_no_note_nor_its_audit_row(app_conn, ai_conn, pmc):
    note(app_conn, pmc, vendor_id=pmc.vendor_id)
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        ai_conn.execute("SELECT * FROM trust_notes")
    assert ai_conn.execute(
        "SELECT count(*) FROM trust_audit_log WHERE table_name = 'trust_notes'"
    ).fetchone() == (0,)
