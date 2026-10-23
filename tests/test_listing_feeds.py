"""Listing feeds (migration 20261023000250): staff's choice that a listing on the public page goes
to listing sites through the company's feeds, each choice changing it and naming who made it,
and the leasing contact the feeds name. Both are records, never rewritten. Every name and number
here is synthetic."""

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
def listing(conn, pmc, staff):
    unit = add_unit(conn, pmc.pmc_id, pmc.owners[0].property_id)
    link = open_application_link(conn, pmc.pmc_id, unit, staff)
    return conn.execute(
        "INSERT INTO trust_listings (pmc_id, link_id, listed_by) VALUES (%s, %s, %s) RETURNING id",
        (pmc.pmc_id, link, staff),
    ).fetchone()[0]


def choose(conn, pmc_id, listing_id, in_feed, staff_id=None):
    if staff_id is not None:
        conn.execute("SELECT set_config('trust.staff_id', %s, true)", (str(staff_id),))
    conn.execute(
        "INSERT INTO trust_listing_feed_choices (pmc_id, listing_id, in_feed) VALUES (%s, %s, %s)",
        (pmc_id, listing_id, in_feed),
    )


def in_feed(conn, listing_id):
    return conn.execute("SELECT trust_listing_in_feed(%s)", (listing_id,)).fetchone()[0]


def contact(conn, pmc_id, phone="2025550142", email="leasing@example.com", name=None):
    conn.execute(
        "INSERT INTO trust_listing_feed_contacts (pmc_id, contact_name, email, phone)"
        " VALUES (%s, %s, %s, %s)",
        (pmc_id, name, email, phone),
    )


def test_a_listing_goes_in_and_comes_out_by_its_latest_choice(app_conn, pmc, staff, listing):
    assert in_feed(app_conn, listing) is False
    choose(app_conn, pmc.pmc_id, listing, True, staff)
    assert in_feed(app_conn, listing) is True
    choose(app_conn, pmc.pmc_id, listing, False, staff)
    assert in_feed(app_conn, listing) is False
    choose(app_conn, pmc.pmc_id, listing, True, staff)

    assert in_feed(app_conn, listing) is True
    assert app_conn.execute(
        "SELECT in_feed, recorded_by FROM trust_listing_feed_choices WHERE listing_id = %s"
        " ORDER BY created_at",
        (listing,),
    ).fetchall() == [(True, staff), (False, staff), (True, staff)]


@pytest.mark.parametrize(("first", "then"), [(None, False), (True, True)])
def test_a_choice_that_changes_nothing_is_refused(conn, pmc, staff, listing, first, then):
    if first is not None:
        choose(conn, pmc.pmc_id, listing, first, staff)

    with pytest.raises(psycopg.errors.CheckViolation, match="already in|not in"):
        choose(conn, pmc.pmc_id, listing, then, staff)


def test_a_choice_stays_inside_its_pmc(conn, pmc, staff, listing):
    other = make_pmc(conn)
    theirs = add_staff(conn, other.pmc_id, "Staff 9")

    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        choose(conn, other.pmc_id, listing, True)
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        # Naming another PMC's staff member as who chose it.
        choose(conn, pmc.pmc_id, listing, True, theirs)


def test_the_contact_takes_ten_digits_and_an_email(conn, pmc):
    contact(conn, pmc.pmc_id, name="Leasing office")
    for bad in (
        {"phone": "555-0142"},
        {"phone": "202555014"},
        {"email": "leasing"},
        {"name": " Leasing "},
        {"name": "x" * 101},
    ):
        with pytest.raises(psycopg.errors.CheckViolation), conn.transaction():
            contact(conn, pmc.pmc_id, **bad)


@pytest.mark.parametrize("table", ["trust_listing_feed_choices", "trust_listing_feed_contacts"])
def test_choices_and_contacts_are_never_rewritten(conn, pmc, staff, listing, table):
    choose(conn, pmc.pmc_id, listing, True, staff)
    contact(conn, pmc.pmc_id)
    for statement in (
        f"UPDATE {table} SET pmc_id = pmc_id",
        f"DELETE FROM {table}",
        f"TRUNCATE {table}",
    ):
        with (
            pytest.raises(psycopg.errors.RestrictViolation, match="append-only"),
            conn.transaction(),
        ):
            conn.execute(statement)


def test_the_ai_role_reads_the_choices_but_not_the_contact(conn, pmc, staff, listing):
    choose(conn, pmc.pmc_id, listing, True, staff)
    contact(conn, pmc.pmc_id)
    conn.execute("SET LOCAL ROLE trust_ai_agent")

    assert conn.execute("SELECT count(*) FROM trust_listing_feed_choices").fetchone() == (1,)
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        conn.execute("SELECT count(*) FROM trust_listing_feed_contacts")
