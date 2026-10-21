"""Property-based tests for notes (migration 20261016000066, src/pms/api/notes.py).

Hypothesis writes notes about a random mix of a company's records (two properties, a unit, a
lease, a tenant, an owner, a vendor), some of them about another company's records, which are
refused. Each record's notes, as the API reads them (`notes_of`), are exactly those written about
it, newest first; another company's are never among them.
Run more examples locally with HYPOTHESIS_PROFILE=nightly.
"""

from datetime import UTC, datetime

import psycopg
from helpers import add_tenant, add_unit, make_pmc, open_lease
from hypothesis import event, given
from hypothesis import strategies as st

from pms.api.notes import notes_of

TODAY = datetime.now(UTC).date()
RECORDS = ["property", "other property", "unit", "lease", "tenant", "owner", "vendor"]
# Any text a note can hold: no NUL and no lone surrogate (the API refuses both;
# tests/api/test_notes_api.py).
CHARACTERS = st.characters(exclude_characters="\x00", exclude_categories=["Cs"])
BODY = st.text(CHARACTERS, min_size=1, max_size=20).filter(str.strip)
WRITES = st.lists(
    st.tuples(st.sampled_from(RECORDS), st.booleans(), BODY),
    min_size=1,
    max_size=12,
)


def records_of(conn, pmc):
    prop = pmc.owners[0].property_id
    unit = add_unit(conn, pmc.pmc_id, prop, "Unit 1")
    tenant = add_tenant(conn, pmc.pmc_id, prop, "Tenant A")
    return {
        "property": ("property", prop),
        "other property": ("property", pmc.owners[1].property_id),
        "unit": ("unit", unit),
        "lease": ("lease", open_lease(conn, pmc.pmc_id, unit, TODAY, None, "900.00", [tenant])),
        "tenant": ("tenant", tenant),
        "owner": ("owner", pmc.owners[0].owner_id),
        "vendor": ("vendor", pmc.vendor_id),
    }


@given(writes=WRITES)
def check(conn, writes):
    ours, theirs = make_pmc(conn), make_pmc(conn)
    mine, other = records_of(conn, ours), records_of(conn, theirs)
    written: dict[str, list[str]] = {name: [] for name in RECORDS}
    for name, elsewhere, body in writes:
        about, about_id = (other if elsewhere else mine)[name]
        try:
            conn.execute(
                f"INSERT INTO trust_notes (pmc_id, {about}_id, body) VALUES (%s, %s, %s)",
                (ours.pmc_id, about_id, body),
            )
            took = True
        except psycopg.errors.ForeignKeyViolation:
            took = False
        event("another company's record: refused" if elsewhere else "own record: written")
        assert took is not elsewhere
        if took:
            written[name].append(body)
    for name, (about, about_id) in mine.items():
        assert [n.body for n in notes_of(conn, ours.pmc_id, about, about_id)] == written[name][::-1]
        assert notes_of(conn, theirs.pmc_id, about, about_id) == []


def test_a_records_notes_are_exactly_those_written_about_it_newest_first(database_url):
    with psycopg.connect(database_url, autocommit=True) as conn:
        check(conn)
