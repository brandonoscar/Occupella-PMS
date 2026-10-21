"""Property-based tests for calendar links (migration 20261020000098; src/pms/api/calendar.py).

Hypothesis makes links, takes them back and disables logins for a few people (two staff members
and two tenants of one company, a staff member of another) in any order. A model of each person's
standing link says what must hold after every step: a link answers only while it is its person's
latest one not taken back and the person can still sign in; taking back says whether there was one
to take; each person has at most one standing link; and a link answers only for its own company
and person. Run more examples locally with HYPOTHESIS_PROFILE=nightly.
"""

from types import SimpleNamespace

import psycopg
from helpers import add_staff, add_tenant, make_pmc
from hypothesis import given
from hypothesis import strategies as st

from pms import accounts
from pms.api import calendar

PEOPLE = ["staff A", "staff B", "tenant A", "tenant B", "elsewhere"]
STEP = st.tuples(st.sampled_from(["make", "take back", "disable"]), st.sampled_from(PEOPLE))
PUBLIC = "http://pms.test"
# What calendar.make reads of a request: the server's public address.
REQUEST = SimpleNamespace(
    app=SimpleNamespace(state=SimpleNamespace(settings=SimpleNamespace(public_url=PUBLIC)))
)


@given(steps=st.lists(STEP, max_size=12))
def check(conn, steps):
    home, other = make_pmc(conn), make_pmc(conn)
    prop = home.owners[0].property_id
    people = {
        "staff A": (home.pmc_id, "staff_id", add_staff(conn, home.pmc_id, "Staff A")),
        "staff B": (home.pmc_id, "staff_id", add_staff(conn, home.pmc_id, "Staff B")),
        "tenant A": (home.pmc_id, "tenant_id", add_tenant(conn, home.pmc_id, prop, "Tenant A")),
        "tenant B": (home.pmc_id, "tenant_id", add_tenant(conn, home.pmc_id, prop, "Tenant B")),
        "elsewhere": (other.pmc_id, "staff_id", add_staff(conn, other.pmc_id, "Staff C")),
    }
    for pmc_id, column, person in people.values():
        conn.execute(
            f"INSERT INTO trust_logins (pmc_id, kind, {column}, email)"  # noqa: S608
            " VALUES (%s, %s, %s, %s)",
            (pmc_id, column.removesuffix("_id"), person, f"{person.hex[:12]}@example.com"),
        )
    made: list[tuple[str, str]] = []  # every link made: who, its token
    standing: dict[str, str | None] = dict.fromkeys(PEOPLE)
    disabled: set[str] = set()

    for action, who in steps:
        pmc_id, column, person = people[who]
        with conn.transaction():
            if action == "make":
                url = calendar.make(conn, REQUEST, pmc_id, column, person, None).url
                token = url.removeprefix(f"{PUBLIC}/api/calendar/").removesuffix(".ics")
                made.append((who, token))
                standing[who] = token
            elif action == "take back":
                assert calendar.revoke(conn, pmc_id, column, person) == (standing[who] is not None)
                standing[who] = None
            else:
                conn.execute(
                    f"UPDATE trust_logins SET disabled_at = now() WHERE {column} = %s",  # noqa: S608
                    (person,),
                )
                disabled.add(who)

        for maker, token in made:
            row = conn.execute(calendar.FEED, (accounts.digest(token),)).fetchone()
            assert (row is not None) == (standing[maker] == token and maker not in disabled)
            if row is not None:
                pmc_id, column, person = people[maker]
                assert row[0] == pmc_id
                assert (row[1], row[2]) == (
                    (person, None) if column == "staff_id" else (None, person)
                )
        for who, (pmc_id, column, person) in people.items():
            found = calendar.standing(conn, pmc_id, column, person)
            assert (found is not None) == (standing[who] is not None)
        assert conn.execute(
            "SELECT count(*) FROM trust_calendar_feeds AS f WHERE f.pmc_id IN (%s, %s)"
            " AND NOT EXISTS (SELECT 1 FROM trust_calendar_feed_revocations AS r"
            " WHERE r.feed_id = f.id)",
            (home.pmc_id, other.pmc_id),
        ).fetchone() == (sum(token is not None for token in standing.values()),)


def test_only_a_persons_latest_link_answers_and_only_while_they_can_sign_in(database_url):
    with psycopg.connect(database_url, autocommit=True) as conn:
        check(conn)
