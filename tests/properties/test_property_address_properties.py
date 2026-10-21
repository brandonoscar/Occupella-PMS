"""Property-based tests for property addresses and where they are (migration 20261021000109;
src/pms/api/addresses.py).

Hypothesis saves addresses and finds them for two properties in any order, through the API's own
functions, with a geocoder that lands each find of an address a little apart (as a real one may).
A model says what must hold after every step: a property's address is the latest saved, the same
address again writes nothing, a location is the latest found for that very address (a new address
has none until it is found), an address the geocoder doesn't know or a property with no address
keeps nothing, and one property's address is never another's. Run more examples locally with
HYPOTHESIS_PROFILE=nightly.
"""

from dataclasses import replace
from decimal import Decimal
from types import SimpleNamespace

import psycopg
from fastapi import HTTPException
from helpers import make_pmc
from hypothesis import given
from hypothesis import strategies as st

from pms import geocoding
from pms.api import addresses

STREETS = ["100 Synthetic Way", "200 Example Street", "1 Nowhere Lane"]
ADDRESS = st.tuples(
    st.sampled_from(STREETS),
    st.sampled_from([None, "Unit 2"]),
    st.sampled_from(["Demo City", "Sample Town"]),
    st.sampled_from(["CA", "TX"]),
    st.sampled_from(["00001", "00002-0003"]),
)
STEP = st.one_of(
    st.tuples(st.just("save"), st.integers(0, 1), ADDRESS),
    st.tuples(st.just("locate"), st.integers(0, 1), st.none()),
)


class Drifting:
    """The fake geocoder, but each find lands a little further north, as a real geocoder's
    answer for one address may change: so the latest find can be told from an earlier one."""

    name = "fake"

    def __init__(self) -> None:
        self.finds = 0
        self.last: geocoding.Match | None = None

    def locate(self, address: geocoding.Address) -> geocoding.Match | None:
        found = geocoding.FakeGeocoder().locate(address)
        if found is None:
            return None
        self.finds += 1
        self.last = replace(found, latitude=found.latitude + Decimal(self.finds) / 1000)
        return self.last


@given(steps=st.lists(STEP, max_size=12))
def check(conn, steps):
    pmc = make_pmc(conn)
    session = SimpleNamespace(pmc_id=pmc.pmc_id)
    drifting = Drifting()
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(geocoder=drifting)))
    props = [pmc.owners[0].property_id, pmc.owners[1].property_id]
    saved: list[tuple | None] = [None, None]
    rows = [0, 0]
    where: list[tuple[str, str] | None] = [None, None]

    for action, n, address in steps:
        prop = props[n]
        if action == "save":
            line_1, line_2, city, state, zip_code = address
            new = addresses.NewAddress(
                line_1=line_1, line_2=line_2, city=city, state=state, zip=zip_code
            )
            addresses.set_address(session, conn, prop, new)
            if saved[n] != address:
                saved[n], rows[n], where[n] = address, rows[n] + 1, None
        else:
            try:
                found = addresses.locate(request, session, conn, prop)
            except HTTPException as refused:
                assert saved[n] is None or saved[n][0] == "1 Nowhere Lane"
                assert refused.status_code == (409 if saved[n] is None else 422)
            else:
                assert saved[n] is not None and saved[n][0] != "1 Nowhere Lane"
                assert drifting.last is not None
                where[n] = (str(drifting.last.latitude), str(drifting.last.longitude))
                assert (found.latitude, found.longitude) == where[n]
                assert found.matched == geocoding.Address(*saved[n]).one_line().upper()

        for i, each in enumerate(props):
            now = addresses.address_of(conn, each)
            at = addresses.location_of(conn, each)
            assert (now and (now.line_1, now.line_2, now.city, now.state, now.zip)) == saved[i]
            if saved[i] is not None:
                assert now.one_line == geocoding.Address(*saved[i]).one_line()
            assert (at and (at.latitude, at.longitude)) == where[i]
            assert conn.execute(
                "SELECT count(*) FROM trust_property_addresses WHERE property_id = %s", (each,)
            ).fetchone() == (rows[i],)


def test_a_property_is_where_its_latest_address_was_last_found(database_url):
    with psycopg.connect(database_url, autocommit=True) as conn:
        check(conn)
