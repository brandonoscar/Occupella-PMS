"""A tenant renting at two properties at once (migration 20261021000105; the founder's review q009).

Someone already a tenant at one property who rents at another gets a record there linked to their
first. Each record keeps its own leases and money; the person has one portal login, their first
record's, and trust_tenant_records names every record of theirs.
"""

from datetime import date

import psycopg
import pytest
from helpers import (
    acting_as,
    add_staff,
    add_tenant,
    add_unit,
    in_background,
    make_pmc,
    open_lease,
    wait_until_blocked,
)

JAN_1 = date(2026, 1, 1)


@pytest.fixture
def pmc(conn):
    return make_pmc(conn)


def link(conn, pmc_id, tenant, same_as):
    conn.execute(
        "INSERT INTO trust_tenant_links (pmc_id, tenant_id, same_as) VALUES (%s, %s, %s)",
        (pmc_id, tenant, same_as),
    )


def records(conn, pmc_id, tenant):
    return {
        row[0]
        for row in conn.execute("SELECT trust_tenant_records(%s, %s)", (pmc_id, tenant)).fetchall()
    }


def login(conn, pmc_id, tenant, email="tenant1@example.com"):
    return conn.execute(
        "INSERT INTO trust_logins (pmc_id, kind, tenant_id, email) VALUES (%s, 'tenant', %s, %s)"
        " RETURNING id",
        (pmc_id, tenant, email),
    ).fetchone()[0]


def property_of(pmc, n):
    return pmc.owners[n].property_id


def test_a_tenant_renting_at_another_property_is_a_record_there_linked_to_their_first(conn, pmc):
    first = add_tenant(conn, pmc.pmc_id, property_of(pmc, 0), "Tenant 1")
    there = add_tenant(conn, pmc.pmc_id, property_of(pmc, 1), "Tenant 1")
    somebody = add_tenant(conn, pmc.pmc_id, property_of(pmc, 1), "Tenant 2")
    home = add_unit(conn, pmc.pmc_id, property_of(pmc, 0))
    open_lease(conn, pmc.pmc_id, home, JAN_1, None, "1000.00", [first])
    link(conn, pmc.pmc_id, there, first)
    # A lease at the second property, at the same time as the first: its tenant is the record
    # there.
    away = add_unit(conn, pmc.pmc_id, property_of(pmc, 1))
    second = open_lease(conn, pmc.pmc_id, away, JAN_1, None, "900.00", [there, somebody])

    assert records(conn, pmc.pmc_id, first) == records(conn, pmc.pmc_id, there) == {first, there}
    assert records(conn, pmc.pmc_id, somebody) == {somebody}
    assert records(conn, make_pmc(conn).pmc_id, first) == set()
    assert conn.execute(
        "SELECT tenant_id FROM trust_lease_tenants WHERE lease_id = %s AND tenant_id = %s",
        (second, there),
    ).fetchone() == (there,)


def test_a_link_names_who_made_it_and_is_never_rewritten(conn, pmc):
    first = add_tenant(conn, pmc.pmc_id, property_of(pmc, 0), "Tenant 1")
    there = add_tenant(conn, pmc.pmc_id, property_of(pmc, 1), "Tenant 1")
    staff = add_staff(conn, pmc.pmc_id)
    with acting_as(conn, staff):
        link(conn, pmc.pmc_id, there, first)

    assert conn.execute(
        "SELECT recorded_by FROM trust_tenant_links WHERE tenant_id = %s", (there,)
    ).fetchone() == (staff,)
    for change in (
        "UPDATE trust_tenant_links SET same_as = same_as",
        "DELETE FROM trust_tenant_links",
    ):
        with pytest.raises(psycopg.errors.RestrictViolation, match="append-only"):
            conn.execute(change)
    with pytest.raises(psycopg.errors.CheckViolation, match="linked to their first record already"):
        link(conn, pmc.pmc_id, there, first)


def test_a_record_links_to_the_persons_first_and_a_first_record_links_to_nobody(conn, pmc):
    first = add_tenant(conn, pmc.pmc_id, property_of(pmc, 0), "Tenant 1")
    there = add_tenant(conn, pmc.pmc_id, property_of(pmc, 1), "Tenant 1")
    third = add_tenant(conn, pmc.pmc_id, property_of(pmc, 1), "Tenant 3")
    link(conn, pmc.pmc_id, there, first)

    with pytest.raises(psycopg.errors.CheckViolation, match="link to their first"):
        link(conn, pmc.pmc_id, third, there)
    with pytest.raises(psycopg.errors.CheckViolation, match="link Tenant 3 to them instead"):
        link(conn, pmc.pmc_id, first, third)
    with pytest.raises(psycopg.errors.CheckViolation):
        link(conn, pmc.pmc_id, first, first)


def test_the_person_has_one_record_a_property(conn, pmc):
    first = add_tenant(conn, pmc.pmc_id, property_of(pmc, 0), "Tenant 1")
    same_place = add_tenant(conn, pmc.pmc_id, property_of(pmc, 0), "Tenant 1")
    there = add_tenant(conn, pmc.pmc_id, property_of(pmc, 1), "Tenant 1")
    there_again = add_tenant(conn, pmc.pmc_id, property_of(pmc, 1), "Tenant 1")

    with pytest.raises(psycopg.errors.CheckViolation, match="is a tenant at Property 1 already"):
        link(conn, pmc.pmc_id, same_place, first)
    link(conn, pmc.pmc_id, there, first)
    with pytest.raises(psycopg.errors.CheckViolation, match="is a tenant at Property 2 already"):
        link(conn, pmc.pmc_id, there_again, first)


def test_the_person_signs_in_through_their_first_record_only(conn, pmc):
    first = add_tenant(conn, pmc.pmc_id, property_of(pmc, 0), "Tenant 1")
    there = add_tenant(conn, pmc.pmc_id, property_of(pmc, 1), "Tenant 1")
    signs_in = add_tenant(conn, pmc.pmc_id, property_of(pmc, 1), "Tenant 2")
    gone = add_tenant(conn, pmc.pmc_id, property_of(pmc, 1), "Tenant 3")
    other = add_tenant(conn, pmc.pmc_id, property_of(pmc, 0), "Tenant 3")
    login(conn, pmc.pmc_id, signs_in, "tenant2@example.com")
    disabled = login(conn, pmc.pmc_id, gone, "tenant3@example.com")
    conn.execute("UPDATE trust_logins SET disabled_at = now() WHERE id = %s", (disabled,))

    # A record that signs in links nobody to it the other way round.
    with pytest.raises(psycopg.errors.CheckViolation, match="Tenant 2 signs in to the portal"):
        link(conn, pmc.pmc_id, signs_in, first)
    # A disabled login signs in to nothing.
    link(conn, pmc.pmc_id, gone, other)
    link(conn, pmc.pmc_id, there, first)
    with pytest.raises(psycopg.errors.CheckViolation, match="signs in as Tenant 1"):
        login(conn, pmc.pmc_id, there)
    login(conn, pmc.pmc_id, first)


def test_the_app_links_and_reads_records_and_the_ai_reads_links_only(conn, app_conn, ai_conn, pmc):
    first = add_tenant(conn, pmc.pmc_id, property_of(pmc, 0), "Tenant 1")
    there = add_tenant(conn, pmc.pmc_id, property_of(pmc, 1), "Tenant 1")
    link(app_conn, pmc.pmc_id, there, first)

    assert records(app_conn, pmc.pmc_id, there) == {first, there}
    assert ai_conn.execute(
        "SELECT same_as FROM trust_tenant_links WHERE tenant_id = %s", (there,)
    ).fetchone() == (first,)
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        records(ai_conn, pmc.pmc_id, there)


@pytest.mark.timing
def test_a_login_waits_for_a_link_of_its_record_and_then_is_refused(conn, database_url, pmc):
    first = add_tenant(conn, pmc.pmc_id, property_of(pmc, 0), "Tenant 1")
    there = add_tenant(conn, pmc.pmc_id, property_of(pmc, 1), "Tenant 1")
    with psycopg.connect(database_url) as linking:
        link(linking, pmc.pmc_id, there, first)  # not committed yet
        thread, pid, outcome = in_background(database_url, lambda w: login(w, pmc.pmc_id, there))
        wait_until_blocked(conn, pid)
        linking.commit()
    thread.join(timeout=30)

    assert isinstance(outcome.get("error"), psycopg.errors.CheckViolation), outcome
    assert conn.execute(
        "SELECT count(*) FROM trust_logins WHERE tenant_id = %s", (there,)
    ).fetchone() == (0,)


@pytest.mark.timing
def test_a_link_waits_for_a_login_of_its_record_and_then_is_refused(conn, database_url, pmc):
    first = add_tenant(conn, pmc.pmc_id, property_of(pmc, 0), "Tenant 1")
    there = add_tenant(conn, pmc.pmc_id, property_of(pmc, 1), "Tenant 1")
    with psycopg.connect(database_url) as signing_in:
        login(signing_in, pmc.pmc_id, there)  # not committed yet
        thread, pid, outcome = in_background(
            database_url, lambda w: link(w, pmc.pmc_id, there, first)
        )
        wait_until_blocked(conn, pid)
        signing_in.commit()
    thread.join(timeout=30)

    assert isinstance(outcome.get("error"), psycopg.errors.CheckViolation), outcome
    assert "signs in to the portal" in str(outcome["error"])
