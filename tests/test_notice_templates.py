"""Notice templates as the database keeps them (migration 20261021000119; the founder's review
q095): a version is of a template not removed, under a name no other current template of the
company has whatever its case; a removal is once; a notice is of a lease and a version of the
company; nothing is rewritten; the AI role reads templates but never a notice."""

from datetime import date

import psycopg
import pytest
from helpers import add_tenant, add_unit, make_pmc, open_lease


@pytest.fixture
def pmc(conn):
    return make_pmc(conn)


def template(conn, pmc_id, name="Late rent", body="Dear {{tenant_names}}"):
    with conn.transaction():
        made = conn.execute(
            "INSERT INTO trust_notice_templates (pmc_id) VALUES (%s) RETURNING id", (pmc_id,)
        ).fetchone()[0]
        version(conn, pmc_id, made, name, body)
    return made


def version(conn, pmc_id, template_id, name, body="Text"):
    return conn.execute(
        "INSERT INTO trust_notice_template_versions (pmc_id, template_id, name, body)"
        " VALUES (%s, %s, %s, %s) RETURNING id",
        (pmc_id, template_id, name, body),
    ).fetchone()[0]


def remove(conn, pmc_id, template_id):
    conn.execute(
        "INSERT INTO trust_notice_template_removals (template_id, pmc_id) VALUES (%s, %s)",
        (template_id, pmc_id),
    )


def a_lease(conn, pmc):
    prop = pmc.owners[0].property_id
    tenant = add_tenant(conn, pmc.pmc_id, prop, "Tenant 9")
    unit = add_unit(conn, pmc.pmc_id, prop, "1A")
    return open_lease(conn, pmc.pmc_id, unit, date(2026, 1, 1), None, "900.00", [tenant])


def notice(conn, pmc_id, lease_id, version_id):
    return conn.execute(
        "INSERT INTO trust_notices (pmc_id, lease_id, template_version_id, dated_on, title,"
        " letterhead, body) VALUES (%s, %s, %s, '2026-10-21', 'Late rent', 'Co', 'Dear T')"
        " RETURNING id",
        (pmc_id, lease_id, version_id),
    ).fetchone()[0]


def test_a_name_is_the_companys_once_whatever_its_case(conn, pmc):
    late = template(conn, pmc.pmc_id)
    template(conn, make_pmc(conn).pmc_id)  # another company may use it

    with pytest.raises(psycopg.errors.UniqueViolation, match="named LATE RENT already"):
        template(conn, pmc.pmc_id, "LATE RENT")
    entry = template(conn, pmc.pmc_id, "Entry")
    with pytest.raises(psycopg.errors.UniqueViolation, match="named  late rent  already"):
        version(conn, pmc.pmc_id, entry, " late rent ")
    version(conn, pmc.pmc_id, late, "Late rent")  # its own name again is its own

    remove(conn, pmc.pmc_id, late)
    template(conn, pmc.pmc_id, "late rent")  # free once the other is removed


def test_a_removed_template_takes_no_new_version_and_is_removed_once(conn, pmc):
    late = template(conn, pmc.pmc_id)
    remove(conn, pmc.pmc_id, late)

    with pytest.raises(psycopg.errors.CheckViolation, match="is removed"):
        version(conn, pmc.pmc_id, late, "Late rent 2")
    with pytest.raises(psycopg.errors.UniqueViolation):
        remove(conn, pmc.pmc_id, late)


def test_a_notice_is_of_a_lease_and_a_version_of_its_company(conn, pmc):
    lease = a_lease(conn, pmc)
    theirs = make_pmc(conn)
    their_version = conn.execute(
        "SELECT id FROM trust_notice_template_versions WHERE template_id = %s",
        (template(conn, theirs.pmc_id),),
    ).fetchone()[0]
    ours = conn.execute(
        "SELECT id FROM trust_notice_template_versions WHERE template_id = %s",
        (template(conn, pmc.pmc_id),),
    ).fetchone()[0]

    notice(conn, pmc.pmc_id, lease, ours)
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        notice(conn, pmc.pmc_id, lease, their_version)
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        notice(conn, theirs.pmc_id, lease, their_version)


def test_nothing_is_rewritten(conn, pmc):
    late = template(conn, pmc.pmc_id)
    version_id = conn.execute(
        "SELECT id FROM trust_notice_template_versions WHERE template_id = %s", (late,)
    ).fetchone()[0]
    notice(conn, pmc.pmc_id, a_lease(conn, pmc), version_id)
    remove(conn, pmc.pmc_id, late)

    for change in (
        "UPDATE trust_notice_templates SET created_at = now()",
        "DELETE FROM trust_notice_templates",
        "UPDATE trust_notice_template_versions SET body = 'x'",
        "DELETE FROM trust_notice_template_versions",
        "DELETE FROM trust_notice_template_removals",
        "UPDATE trust_notices SET body = 'x'",
        "DELETE FROM trust_notices",
    ):
        with pytest.raises(psycopg.errors.RestrictViolation, match="append-only"):
            conn.execute(change)


@pytest.mark.parametrize(
    ("name", "body"), [(" ", "x"), ("x" * 101, "x"), ("x", " \n "), ("x", "y" * 20001)]
)
def test_a_version_has_a_name_and_some_text(conn, pmc, name, body):
    with pytest.raises(psycopg.errors.CheckViolation):
        template(conn, pmc.pmc_id, name, body)


def test_the_app_keeps_them_and_the_ai_reads_templates_but_never_a_notice(
    conn, app_conn, ai_conn, pmc
):
    late = template(app_conn, pmc.pmc_id)
    version_id = app_conn.execute(
        "SELECT id FROM trust_notice_template_versions WHERE template_id = %s", (late,)
    ).fetchone()[0]
    notice(app_conn, pmc.pmc_id, a_lease(conn, pmc), version_id)

    assert ai_conn.execute(
        "SELECT name FROM trust_notice_template_versions WHERE template_id = %s", (late,)
    ).fetchall() == [("Late rent",)]
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        ai_conn.execute("SELECT body FROM trust_notices")
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        app_conn.execute("DELETE FROM trust_notices")
