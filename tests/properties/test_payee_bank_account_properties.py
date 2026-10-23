"""Property-based tests for a payee's bank account (migration 20261022000155).

Hypothesis makes any nine characters and any account numbers:
  - trust_aba_routing_ok says yes exactly when the nine are digits whose weighted sum (3, 7, 1)
    is a multiple of ten, as a bank reads it;
  - a sealed account number opens to itself with its own context, and with no other.

Run more examples locally with HYPOTHESIS_PROFILE=nightly.
"""

import psycopg
from hypothesis import given
from hypothesis import strategies as st

from pms import sealed

DIGITS = st.text(alphabet="0123456789", min_size=9, max_size=9)
ANY = st.text(alphabet="0123456789a -", min_size=0, max_size=11)


def aba(routing: str) -> bool:
    if len(routing) != 9 or not routing.isdigit():
        return False
    d = [int(c) for c in routing]
    return (3 * (d[0] + d[3] + d[6]) + 7 * (d[1] + d[4] + d[7]) + d[2] + d[5] + d[8]) % 10 == 0


@given(routings=st.lists(DIGITS | ANY, min_size=1, max_size=40))
def check_routing(conn, routings):
    got = conn.execute(
        "SELECT r, trust_aba_routing_ok(r) FROM unnest(%s::text[]) AS r", (routings,)
    ).fetchall()
    assert [ok for _, ok in got] == [aba(r) for r in routings]


def test_the_routing_checksum_is_the_banks(database_url):
    with psycopg.connect(database_url, autocommit=True) as conn:
        check_routing(conn)


@given(
    number=st.text(alphabet="0123456789", min_size=4, max_size=17),
    context=st.text(min_size=1, max_size=60),
    other=st.text(min_size=1, max_size=60),
)
def test_a_sealed_number_opens_to_itself_with_its_own_context_alone(number, context, other):
    blob = sealed.seal(number, context)
    assert sealed.open_sealed(blob, context) == number
    if other != context:
        try:
            sealed.open_sealed(blob, other)
        except sealed.CannotOpen:
            pass
        else:
            raise AssertionError("opened with another context")
