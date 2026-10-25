"""Lease templates as the database keeps them (migration 20261022000121): a template is for a
notice or a lease from when it is made; a notice comes only from a notice template's version and a
lease offer only from a lease template's, once per offer; nothing is rewritten; the AI role reads
which version an offer came from. Every name is synthetic."""

from datetime import date

import psycopg
import pytest
from helpers import add_staff, add_tenant, add_unit, make_pmc, offer_lease, open_lease


@pytest.fixture
def pmc(conn):
    return make_pmc(conn)


def template(conn, pmc_id, used_for, name):
    with conn.transaction():
        made = conn.execute(
            "INSERT INTO trust_notice_templates (pmc_id, used_for) VALUES (%s, %s) RETURNING id",
            (pmc_id, used_for),
        ).fetchone()[0]
        return conn.execute(
            "INSERT INTO trust_notice_template_versions (pmc_id, template_id, name, body)"
            " VALUES (%s, %s, %s, 'Text') RETURNING id",
            (pmc_id, made, name),
        ).fetchone()[0]


def an_offer(conn, pmc, unit_name="1A"):
    """The renewal of a lease ending 12/31/2026, from 01/01/2027."""
    prop = pmc.owners[0].property_id
    unit = add_unit(conn, pmc.pmc_id, prop, unit_name)
    tenant = add_tenant(conn, pmc.pmc_id, prop, f"Tenant {unit_name}")
    lease = open_lease(
        conn, pmc.pmc_id, unit, date(2026, 1, 1), date(2026, 12, 31), "900", [tenant]
    )
    staff = add_staff(conn, pmc.pmc_id)
    return offer_lease(conn, pmc.pmc_id, unit, staff, date(2027, 1, 1), renews_lease_id=lease)


def a_lease(conn, pmc):
    prop = pmc.owners[0].property_id
    unit = add_unit(conn, pmc.pmc_id, prop, "1B")
    tenant = add_tenant(conn, pmc.pmc_id, prop, "Tenant 9")
    return open_lease(conn, pmc.pmc_id, unit, date(2026, 1, 1), None, "900.00", [tenant])


def from_template(conn, pmc_id, offer, version):
    conn.execute(
        "INSERT INTO trust_lease_offer_templates (pmc_id, offer_id, template_version_id)"
        " VALUES (%s, %s, %s)",
        (pmc_id, offer, version),
    )


def notice(conn, pmc_id, lease, version):
    conn.execute(
        "INSERT INTO trust_notices (pmc_id, lease_id, template_version_id, dated_on, title,"
        " letterhead, body) VALUES (%s, %s, %s, '2026-10-22', 'T', 'Co', 'Text')",
        (pmc_id, lease, version),
    )


def test_a_template_is_for_a_notice_or_a_lease(conn, pmc):
    assert conn.execute(
        "SELECT used_for FROM trust_notice_templates WHERE id = (SELECT template_id FROM"
        " trust_notice_template_versions WHERE id = %s)",
        (template(conn, pmc.pmc_id, "notice", "Late rent"),),
    ).fetchone() == ("notice",)
    with pytest.raises(psycopg.errors.CheckViolation, match="used_for_check"):
        template(conn, pmc.pmc_id, "flyer", "Flyer")


def test_each_is_made_from_a_template_for_its_use_and_an_offer_from_one_once(conn, pmc):
    lease_version = template(conn, pmc.pmc_id, "lease", "Residential lease")
    notice_version = template(conn, pmc.pmc_id, "notice", "Late rent")
    offer = an_offer(conn, pmc)
    lease = a_lease(conn, pmc)
    refused = psycopg.errors.CheckViolation

    with pytest.raises(refused, match="a lease is made from a lease template, not a notice one"):
        from_template(conn, pmc.pmc_id, offer, notice_version)
    with pytest.raises(refused, match="a notice is made from a notice template, not a lease one"):
        notice(conn, pmc.pmc_id, lease, lease_version)
    from_template(conn, pmc.pmc_id, offer, lease_version)
    notice(conn, pmc.pmc_id, lease, notice_version)
    with pytest.raises(psycopg.errors.UniqueViolation):
        from_template(conn, pmc.pmc_id, offer, lease_version)
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        from_template(conn, make_pmc(conn).pmc_id, an_offer(conn, pmc, "1C"), lease_version)


def test_nothing_is_rewritten_and_the_ai_reads_which_version(conn, app_conn, ai_conn, pmc):
    version = template(conn, pmc.pmc_id, "lease", "Residential lease")
    offer = an_offer(conn, pmc)
    from_template(app_conn, pmc.pmc_id, offer, version)

    for change in (
        "UPDATE trust_lease_offer_templates SET created_at = now()",
        "DELETE FROM trust_lease_offer_templates",
        "UPDATE trust_notice_templates SET used_for = 'lease'",
    ):
        with pytest.raises(psycopg.errors.RestrictViolation, match="append-only"):
            conn.execute(change)
    assert ai_conn.execute(
        "SELECT template_version_id FROM trust_lease_offer_templates WHERE offer_id = %s",
        (offer,),
    ).fetchall() == [(version,)]
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        app_conn.execute("DELETE FROM trust_lease_offer_templates")
