"""Property-based tests for property groups (migration 20261016000065) and the lists they narrow
(src/pms/api/scope.py).

Hypothesis makes a company of up to four properties and two groups, then asks for random joins
and leaves. A model of each group's properties says which requests stand: a property joins a
group it is not in, or leaves one it is in. The database must take exactly those and refuse the
rest, `trust_group_properties` must say what the model says after each, and the properties list
narrowed to a group, to one property, or to both, must be exactly the model's.
Run more examples locally with HYPOTHESIS_PROFILE=nightly.
"""

import psycopg
from helpers import make_pmc
from hypothesis import event, given
from hypothesis import strategies as st

from pms.api import scope

REQUESTS = st.lists(
    st.tuples(st.integers(0, 1), st.integers(0, 3), st.booleans()),  # group, property, joins
    min_size=1,
    max_size=12,
)

NARROWED = f"""
SELECT p.id FROM trust_properties AS p
WHERE p.pmc_id = %(pmc)s AND {scope.within("p.id")}
"""


@given(properties=st.integers(1, 4), requests=REQUESTS, pick=st.integers(0, 3))
def check(conn, properties, requests, pick):
    pmc = make_pmc(conn, owners=properties)
    props = [o.property_id for o in pmc.owners]
    groups = [
        conn.execute(
            "INSERT INTO trust_property_groups (pmc_id, display_name) VALUES (%s, %s) RETURNING id",
            (pmc.pmc_id, name),
        ).fetchone()[0]
        for name in ("North", "South")
    ]
    model: list[set] = [set(), set()]
    for g, p, joins in requests:
        if p >= len(props):
            continue
        stands = (props[p] in model[g]) != joins
        try:
            conn.execute(
                "INSERT INTO trust_property_group_members (pmc_id, group_id, property_id, member)"
                " VALUES (%s, %s, %s, %s)",
                (pmc.pmc_id, groups[g], props[p], joins),
            )
            took = True
        except psycopg.errors.CheckViolation:
            took = False
        event(f"{'join' if joins else 'leave'} {'stands' if stands else 'refused'}")
        assert took == stands, (g, p, joins)
        if took:
            (model[g].add if joins else model[g].discard)(props[p])
        for n, group in enumerate(groups):
            got = {r[0] for r in conn.execute("SELECT trust_group_properties(%s)", (group,))}
            assert got == model[n]

    def narrowed(group, property_id):
        rows = conn.execute(
            NARROWED, {"pmc": pmc.pmc_id, **scope.params(group, property_id)}
        ).fetchall()
        return {r[0] for r in rows}

    one = props[pick % len(props)]
    assert narrowed(None, None) == set(props)
    assert narrowed(None, one) == {one}
    for n, group in enumerate(groups):
        assert narrowed(group, None) == model[n]
        assert narrowed(group, one) == model[n] & {one}


def test_a_group_holds_exactly_the_properties_that_joined_and_did_not_leave(database_url):
    with psycopg.connect(database_url, autocommit=True) as conn:
        check(conn)
