"""Property-based tests for staff signing in through a host app (migration 20261107000200,
pms.external, docs/adr/0013-sign-in-through-a-host.md).

Hypothesis picks a few people the host signs in as staff of one company, and a sequence of their
sign-ins, each with whether the host says they are the company's admin at that moment. A model
follows along: the books open at the first admin's sign-in; the host's admins are the books'
admins, except that the company's last admin keeps the role.

Invariants (ci/registry.toml maps them to this test):
  - before an admin of the host has signed in, the company's books aren't open, and staff who
    aren't its admin are refused;
  - once open, the books' admins are exactly the model's: the role follows the host's word at each
    sign-in, and the company never loses its last admin;
  - each person is one staff member with one login, linked once, however often they sign in;
  - another company's admins never change with it.

Run more examples locally with HYPOTHESIS_PROFILE=nightly.
"""

import uuid

import psycopg
import pytest
from helpers import make_pmc
from hypothesis import event, given
from hypothesis import strategies as st

from pms import external

ISSUER = "occupella"
PEOPLE = 4
STEPS = st.lists(
    st.tuples(st.integers(min_value=0, max_value=PEOPLE - 1), st.booleans()),
    min_size=1,
    max_size=12,
)


def admins(conn, pmc_id) -> set:
    return {
        row[0]
        for row in conn.execute(
            "SELECT s.id FROM trust_staff AS s"
            " WHERE s.pmc_id = %s AND trust_staff_holds(s.pmc_id, s.id, 'admin')",
            (pmc_id,),
        ).fetchall()
    }


@given(steps=STEPS)
def check(conn, app_conn, steps):
    company = uuid.uuid4()
    other = make_pmc(conn)
    others_admins = admins(conn, other.pmc_id)
    tag = uuid.uuid4().hex[:8]
    subjects = [f"person-{tag}-{n}" for n in range(PEOPLE)]
    staff_ids: dict[int, uuid.UUID] = {}
    holders: set[int] = set()
    opened = False

    for who, admin in steps:
        person = external.Person(
            subject=subjects[who],
            email=f"person{who}.{tag}@example.com",
            name=f"Staff {who}",
            staff_of={company: external.Membership(admin=admin, company_name="Company A")},
            second_step=True,
        )
        if not opened and not admin:
            event("refused before the books open")
            with pytest.raises(external.BooksNotOpen):
                external.sign_in(app_conn, ISSUER, person, company, None)
            assert not conn.execute(
                "SELECT 1 FROM trust_pmcs WHERE pmc_id = %s", (company,)
            ).fetchone()
            continue
        signed = external.sign_in(app_conn, ISSUER, person, company, None)
        opened = True
        staff_ids.setdefault(who, signed.session.person_id)
        assert signed.session.person_id == staff_ids[who]
        if admin:
            holders.add(who)
        elif who in holders and len(holders) > 1:
            holders.discard(who)
        elif who in holders:
            event("the last admin kept")

        assert admins(conn, company) == {staff_ids[n] for n in holders}
        assert holders
        assert conn.execute(
            "SELECT count(*), count(DISTINCT l.id) FROM trust_staff AS s"
            " JOIN trust_logins AS l ON l.staff_id = s.id WHERE s.pmc_id = %s",
            (company,),
        ).fetchone() == (len(staff_ids), len(staff_ids))
        assert conn.execute(
            "SELECT count(*), count(DISTINCT subject) FROM trust_external_identities"
            " WHERE pmc_id = %s",
            (company,),
        ).fetchone() == (len(staff_ids), len(staff_ids))

    assert admins(conn, other.pmc_id) == others_admins


def test_the_hosts_admins_are_the_books_admins_and_a_company_keeps_one(database_url):
    with (
        psycopg.connect(database_url, autocommit=True) as conn,
        psycopg.connect(database_url, autocommit=True) as app_conn,
    ):
        app_conn.execute("SET ROLE trust_app")
        check(conn, app_conn)
