"""A home's name as tenants, owners, vendors and applicants read it (migration 20261104000500;
the founder's review of 11/03, item 5): trust_unit_shown gives the unit's name only when it says
something, and pms.homes.home puts it after the property's."""

import pytest
from helpers import add_unit, make_pmc

from pms.homes import home


@pytest.fixture
def pmc(conn):
    return make_pmc(conn)


def shown(conn, unit):
    return conn.execute("SELECT trust_unit_shown(%s)", (unit,)).fetchone()[0]


def archive(conn, pmc, unit):
    conn.execute(
        "INSERT INTO trust_archivals (pmc_id, unit_id, archived) VALUES (%s, %s, true)",
        (pmc.pmc_id, unit),
    )


@pytest.mark.parametrize("name", ["House", "home", " Main ", "SFH", "Single-family", "Unit"])
def test_a_generic_unit_name_adds_nothing(conn, pmc, name):
    add_unit(conn, pmc.pmc_id, pmc.owners[0].property_id, "Unit 2")
    unit = add_unit(conn, pmc.pmc_id, pmc.owners[0].property_id, name)

    assert shown(conn, unit) is None


def test_a_propertys_only_unit_adds_nothing_and_two_say_which(conn, pmc):
    only = add_unit(conn, pmc.pmc_id, pmc.owners[0].property_id, "Unit A")
    assert shown(conn, only) is None

    other = add_unit(conn, pmc.pmc_id, pmc.owners[0].property_id, "Unit B")

    assert (shown(conn, only), shown(conn, other)) == ("Unit A", "Unit B")


def test_a_unit_archived_beside_it_leaves_it_the_only_one(conn, pmc):
    kept = add_unit(conn, pmc.pmc_id, pmc.owners[0].property_id, "Upstairs")
    gone = add_unit(conn, pmc.pmc_id, pmc.owners[0].property_id, "Downstairs")
    archive(conn, pmc, gone)

    assert shown(conn, kept) is None


def test_an_unknown_unit_shows_nothing(conn):
    assert shown(conn, "00000000-0000-0000-0000-000000000000") is None


@pytest.mark.parametrize(
    ("prop", "unit", "read"),
    [("Elm Court", "Unit 4", "Elm Court, Unit 4"), ("Cedar House", None, "Cedar House")],
)
def test_the_home_reads_the_property_then_the_unit_when_there_is_one(prop, unit, read):
    assert home(prop, unit) == read
