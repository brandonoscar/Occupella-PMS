"""pgledger's ids are ULIDs made by the vendored pgsql-ulid helpers. Every ledger row is keyed by
one, so the conversions must be exact and must refuse garbage."""

import psycopg
import pytest
from hypothesis import given
from hypothesis import strategies as st

ULID_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


@pytest.fixture(scope="module")
def id_conn(database_url):
    with psycopg.connect(database_url, autocommit=True) as connection:
        yield connection


@given(value=st.uuids())
def test_uuid_to_ulid_and_back_is_exact(id_conn, value):
    ulid = id_conn.execute("SELECT uuid_to_ulid(%s)", (value,)).fetchone()[0]
    assert len(ulid) == 26
    assert set(ulid) <= set(ULID_ALPHABET)
    assert id_conn.execute("SELECT ulid_to_uuid(%s)", (ulid,)).fetchone()[0] == value


@given(first=st.uuids(), second=st.uuids())
def test_ulid_text_sorts_like_the_uuid_bytes(id_conn, first, second):
    a, b = (
        id_conn.execute("SELECT uuid_to_ulid(%s)", (value,)).fetchone()[0]
        for value in (first, second)
    )
    assert (a < b) == (first.bytes < second.bytes)


NEAR_ULIDS = st.text(
    alphabet=ULID_ALPHABET + ULID_ALPHABET.lower() + "ILOU", min_size=25, max_size=27
)
ASCII = st.text(alphabet=st.characters(max_codepoint=127, exclude_characters="\x00"), max_size=40)


@given(text=st.one_of(NEAR_ULIDS, ASCII))
def test_malformed_ulid_is_refused(id_conn, text):
    letters = ULID_ALPHABET + ULID_ALPHABET.lower()
    valid = len(text) == 26 and text[0] in "01234567" and all(c in letters for c in text)
    if valid:
        id_conn.execute("SELECT ulid_to_uuid(%s)", (text,))
    else:
        with pytest.raises(psycopg.errors.RaiseException, match="Invalid ULID"):
            id_conn.execute("SELECT ulid_to_uuid(%s)", (text,))
