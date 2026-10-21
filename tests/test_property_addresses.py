"""A property's street address and where it is (migration 20261021000109): the latest address
holds, a location counts only for the address it was found for, and both are records. Every
address here is synthetic."""

from decimal import Decimal

import psycopg
import pytest
from helpers import make_pmc


@pytest.fixture
def pmc(conn):
    return make_pmc(conn)


def address(conn, pmc_id, property_id, line_1="100 Synthetic Way", **more):
    fields = {"line_2": None, "city": "Demo City", "state": "CA", "zip": "00001", **more}
    return conn.execute(
        "INSERT INTO trust_property_addresses"
        " (pmc_id, property_id, line_1, line_2, city, state, zip)"
        " VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id",
        (
            pmc_id,
            property_id,
            line_1,
            fields["line_2"],
            fields["city"],
            fields["state"],
            fields["zip"],
        ),
    ).fetchone()[0]


def locate(conn, pmc_id, address_id, latitude="37.000001", longitude="-120.000001"):
    conn.execute(
        "INSERT INTO trust_property_locations"
        " (pmc_id, address_id, latitude, longitude, matched, geocoder)"
        " VALUES (%s, %s, %s, %s, 'MATCHED', 'fake')",
        (pmc_id, address_id, Decimal(latitude), Decimal(longitude)),
    )


def now(conn, property_id):
    found = conn.execute("SELECT line_1 FROM trust_property_address(%s)", (property_id,)).fetchone()
    at = conn.execute("SELECT latitude FROM trust_property_location(%s)", (property_id,)).fetchone()
    return (found and found[0], at and at[0])


def test_the_latest_address_holds_and_its_own_location_with_it(conn, pmc):
    prop = pmc.owners[0].property_id
    assert now(conn, prop) == (None, None)

    first = address(conn, pmc.pmc_id, prop)
    locate(conn, pmc.pmc_id, first)
    locate(conn, pmc.pmc_id, first, latitude="37.5")
    assert now(conn, prop) == ("100 Synthetic Way", Decimal("37.5"))

    # A new address isn't where the old one was until it is found.
    second = address(conn, pmc.pmc_id, prop, "200 Example Street")
    assert now(conn, prop) == ("200 Example Street", None)
    locate(conn, pmc.pmc_id, second, latitude="40.25")
    assert now(conn, prop) == ("200 Example Street", Decimal("40.25"))
    # Another property's address is its own.
    assert now(conn, pmc.owners[1].property_id) == (None, None)


@pytest.mark.parametrize(
    "bad",
    [
        {"line_1": ""},
        {"line_1": " 100 Synthetic Way"},
        {"line_2": ""},
        {"city": ""},
        {"state": "ZZ"},
        {"state": "ca"},
        {"zip": "1234"},
        {"zip": "00001-12"},
        {"zip": "ABCDE"},
    ],
)
def test_an_address_is_whole_with_a_usps_state_and_a_zip_code(conn, pmc, bad):
    with pytest.raises(psycopg.errors.CheckViolation):
        address(conn, pmc.pmc_id, pmc.owners[0].property_id, **bad)


def test_a_zip_plus_four_and_a_territory_are_addresses(conn, pmc):
    address(conn, pmc.pmc_id, pmc.owners[0].property_id, state="PR", zip="00001-0001")
    assert now(conn, pmc.owners[0].property_id)[0] == "100 Synthetic Way"


@pytest.mark.parametrize(
    ("latitude", "longitude"),
    [("90.000001", "0"), ("-91", "0"), ("0", "180.5"), ("0", "-181"), ("1.0000001", "0")],
)
def test_a_location_is_on_the_globe_to_six_places(conn, pmc, latitude, longitude):
    at = address(conn, pmc.pmc_id, pmc.owners[0].property_id)
    with pytest.raises(psycopg.errors.CheckViolation):
        locate(conn, pmc.pmc_id, at, latitude, longitude)


def test_an_address_and_its_location_are_of_one_company(conn, pmc):
    other = make_pmc(conn)
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        address(conn, other.pmc_id, pmc.owners[0].property_id)
    at = address(conn, pmc.pmc_id, pmc.owners[0].property_id)
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        locate(conn, other.pmc_id, at)


def test_addresses_are_never_rewritten_and_the_ai_reads_them(conn, ai_conn, pmc):
    prop = pmc.owners[0].property_id
    locate(conn, pmc.pmc_id, address(conn, pmc.pmc_id, prop))

    for change in (
        "UPDATE trust_property_addresses SET city = 'Elsewhere'",
        "DELETE FROM trust_property_addresses",
        "UPDATE trust_property_locations SET latitude = 0",
        "DELETE FROM trust_property_locations",
    ):
        with pytest.raises(psycopg.errors.RestrictViolation, match="append-only"):
            conn.execute(change)
    # A property's address is the home's, not a person's contact details.
    assert ai_conn.execute(
        "SELECT a.city, g.geocoder FROM trust_property_addresses AS a"
        " JOIN trust_property_locations AS g ON g.address_id = a.id WHERE a.property_id = %s",
        (prop,),
    ).fetchall() == [("Demo City", "fake")]
