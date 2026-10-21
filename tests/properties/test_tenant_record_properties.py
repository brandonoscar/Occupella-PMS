"""Property-based tests for a tenant's records at several properties (migration 20261021000105;
the founder's review q009).

Hypothesis makes up to five tenant records on up to three properties, then asks at random to link
one record to another's person (as the API does: to that person's first record) and to give a
record a portal login. A model of each person's records says which requests stand: a record joins
a person while it is linked to nothing, is nobody's first, signs in to nothing, and sits at a
property where the person has no record; a login goes only to a record linked to nothing, once.
The database must take exactly those and refuse the rest; after each, `trust_tenant_records`
must give every record its person's records, each person must sign in at most once and only as
their first, and nothing may move in the ledger.
Run more examples locally with HYPOTHESIS_PROFILE=nightly.
"""

import psycopg
from helpers import add_tenant, make_pmc
from hypothesis import event, given
from hypothesis import strategies as st

REQUESTS = st.lists(
    st.one_of(
        st.tuples(st.just("link"), st.integers(0, 4), st.integers(0, 4)),
        st.tuples(st.just("login"), st.integers(0, 4), st.just(0)),
    ),
    min_size=1,
    max_size=14,
)


@given(places=st.lists(st.integers(0, 2), min_size=2, max_size=5), requests=REQUESTS)
def check(conn, places, requests):
    pmc = make_pmc(conn, owners=3)
    records = [
        add_tenant(conn, pmc.pmc_id, pmc.owners[place].property_id, f"Tenant {n + 1}")
        for n, place in enumerate(places)
    ]
    moved = conn.execute("SELECT count(*) FROM pgledger_transfers").fetchone()[0]
    same_as: dict[int, int] = {}  # a linked record -> its person's first record
    signs_in: set[int] = set()

    def first(n: int) -> int:
        return same_as.get(n, n)

    def person(n: int) -> set[int]:
        return {m for m in range(len(records)) if first(m) == first(n)}

    for kind, a, b in requests:
        if a >= len(records) or b >= len(records):
            continue
        if kind == "link":
            target = first(b)
            stands = (
                a != target
                and a not in same_as
                and a not in same_as.values()
                and a not in signs_in
                and places[a] not in {places[m] for m in person(target)}
            )
            write = (
                "INSERT INTO trust_tenant_links (pmc_id, tenant_id, same_as) VALUES (%s, %s, %s)",
                (pmc.pmc_id, records[a], records[target]),
            )
        else:
            stands = a not in same_as and a not in signs_in
            write = (
                "INSERT INTO trust_logins (pmc_id, kind, tenant_id, email)"
                " VALUES (%s, 'tenant', %s, %s)",
                (pmc.pmc_id, records[a], f"tenant{a + 1}.{pmc.pmc_id.hex[:8]}@example.com"),
            )
        try:
            with conn.transaction():
                conn.execute(*write)
            took = True
        except (psycopg.errors.CheckViolation, psycopg.errors.UniqueViolation):
            took = False
        event(f"{kind} {'stands' if stands else 'refused'}")
        assert took == stands, (kind, a, b, same_as, signs_in)
        if took and kind == "link":
            same_as[a] = target
        elif took:
            signs_in.add(a)

        for n, record in enumerate(records):
            got = {
                row[0]
                for row in conn.execute(
                    "SELECT trust_tenant_records(%s, %s)", (pmc.pmc_id, record)
                ).fetchall()
            }
            assert got == {records[m] for m in person(n)}, n
        logins = conn.execute(
            "SELECT tenant_id FROM trust_logins WHERE pmc_id = %s AND disabled_at IS NULL",
            (pmc.pmc_id,),
        ).fetchall()
        assert sorted(r[0] for r in logins) == sorted(records[n] for n in signs_in)
        assert all(n not in same_as for n in signs_in)
        assert all(len(person(n) & signs_in) <= 1 for n in range(len(records)))
    assert conn.execute("SELECT count(*) FROM pgledger_transfers").fetchone()[0] == moved


def test_a_person_is_one_record_a_property_and_signs_in_once_as_their_first(database_url):
    with psycopg.connect(database_url, autocommit=True) as conn:
        check(conn)
