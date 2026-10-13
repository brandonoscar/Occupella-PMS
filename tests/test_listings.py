"""Vacancy listings (migration 20261013000047): an open application link listed on the PMC's
public page, at most once, by a member of its own staff; a removal takes it off, once. Both are
records, never rewritten."""

import psycopg
import pytest
from helpers import add_staff, add_unit, make_pmc, open_application_link


@pytest.fixture
def pmc(conn):
    return make_pmc(conn)


@pytest.fixture
def staff(conn, pmc):
    return add_staff(conn, pmc.pmc_id)


@pytest.fixture
def link(conn, pmc, staff):
    unit = add_unit(conn, pmc.pmc_id, pmc.owners[0].property_id)
    return open_application_link(conn, pmc.pmc_id, unit, staff)


def list_link(conn, pmc_id, link_id, staff_id, description=None):
    return conn.execute(
        "INSERT INTO trust_listings (pmc_id, link_id, description, listed_by)"
        " VALUES (%s, %s, %s, %s) RETURNING id",
        (pmc_id, link_id, description, staff_id),
    ).fetchone()[0]


def remove(conn, pmc_id, listing_id, staff_id):
    conn.execute(
        "INSERT INTO trust_listing_removals (pmc_id, listing_id, removed_by) VALUES (%s, %s, %s)",
        (pmc_id, listing_id, staff_id),
    )


def test_the_app_lists_a_link_once_and_removes_the_listing_once(app_conn, pmc, staff, link):
    listing = list_link(app_conn, pmc.pmc_id, link, staff, "Two bedrooms")
    with pytest.raises(psycopg.errors.UniqueViolation):
        list_link(app_conn, pmc.pmc_id, link, staff)
    remove(app_conn, pmc.pmc_id, listing, staff)
    with pytest.raises(psycopg.errors.UniqueViolation):
        remove(app_conn, pmc.pmc_id, listing, staff)


@pytest.mark.parametrize("description", ["", "   ", "x" * 2001])
def test_a_description_says_something_and_not_too_much(conn, pmc, staff, link, description):
    with pytest.raises(psycopg.errors.CheckViolation):
        list_link(conn, pmc.pmc_id, link, staff, description)


def test_the_longest_description_is_kept(conn, pmc, staff, link):
    listing = list_link(conn, pmc.pmc_id, link, staff, "x" * 2000)

    assert (
        conn.execute(
            "SELECT length(description) FROM trust_listings WHERE id = %s", (listing,)
        ).fetchone()[0]
        == 2000
    )


def test_a_listing_and_its_removal_stay_inside_their_pmc(conn, pmc, staff, link):
    other = make_pmc(conn)
    theirs = add_staff(conn, other.pmc_id, "Staff 9")

    for pmc_id, staff_id in ((other.pmc_id, theirs), (pmc.pmc_id, theirs)):
        with pytest.raises(psycopg.errors.ForeignKeyViolation):
            list_link(conn, pmc_id, link, staff_id)
    listing = list_link(conn, pmc.pmc_id, link, staff)
    for pmc_id, staff_id in ((other.pmc_id, theirs), (pmc.pmc_id, theirs)):
        with pytest.raises(psycopg.errors.ForeignKeyViolation):
            remove(conn, pmc_id, listing, staff_id)


@pytest.mark.parametrize("table", ["trust_listings", "trust_listing_removals"])
def test_listings_and_removals_are_never_rewritten(conn, pmc, staff, link, table):
    listing = list_link(conn, pmc.pmc_id, link, staff, "Two bedrooms")
    remove(conn, pmc.pmc_id, listing, staff)
    for statement in (f"UPDATE {table} SET pmc_id = pmc_id", f"DELETE FROM {table}"):
        with pytest.raises(psycopg.errors.RestrictViolation, match="append-only"):
            conn.execute(statement)
