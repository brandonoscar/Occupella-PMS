"""Payment files as the database keeps them (migration 20261021000118; the founder's review q035):
a file comes in once per PMC with all its lines; a line is recorded (a tenant's payment of its
amount and day to its lease, at commit) or set aside, once and never both; nothing is rewritten.
The money itself moves only through rent.record_payment (tests/api/test_payment_files_api.py)."""

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import psycopg
import pytest
from helpers import add_tenant, add_unit, make_pmc, open_lease

from pms.api import rent

TODAY = datetime.now(UTC).date()


@pytest.fixture
def pmc(conn):
    return make_pmc(conn)


@pytest.fixture
def lease(conn, pmc):
    prop = pmc.owners[0].property_id
    tenant = add_tenant(conn, pmc.pmc_id, prop, "Tenant 9")
    unit = add_unit(conn, pmc.pmc_id, prop, "1A")
    return open_lease(conn, pmc.pmc_id, unit, TODAY - timedelta(30), None, "900.00", [tenant])


def a_file(conn, pmc_id, lines=1, digest="a" * 64):
    return conn.execute(
        "INSERT INTO trust_payment_files (pmc_id, file_name, file_sha256, line_count)"
        " VALUES (%s, 'bank.csv', %s, %s) RETURNING id",
        (pmc_id, digest, lines),
    ).fetchone()[0]


def a_line(conn, pmc_id, file_id, line_no=2, amount="100.00", lease_id=None):
    return conn.execute(
        "INSERT INTO trust_payment_file_lines (pmc_id, file_id, line_no, received_on, amount,"
        " unit, lease_id) VALUES (%s, %s, %s, %s, %s, '1A', %s) RETURNING id",
        (pmc_id, file_id, line_no, TODAY, Decimal(amount), lease_id),
    ).fetchone()[0]


def a_file_of_one_line(conn, pmc_id, digest="a" * 64, lease_id=None):
    with conn.transaction():
        file_id = a_file(conn, pmc_id, digest=digest)
        return a_line(conn, pmc_id, file_id, lease_id=lease_id)


def post(conn, pmc_id, line_id, lease_id, request_id):
    conn.execute(
        "INSERT INTO trust_payment_file_postings (line_id, pmc_id, lease_id, request_id)"
        " VALUES (%s, %s, %s, %s)",
        (line_id, pmc_id, lease_id, request_id),
    )


def set_aside(conn, pmc_id, line_id, reason="Not ours"):
    conn.execute(
        "INSERT INTO trust_payment_file_skips (line_id, pmc_id, reason) VALUES (%s, %s, %s)",
        (line_id, pmc_id, reason),
    )


def pay(conn, pmc_id, lease_id, amount="100.00", day=TODAY):
    request_id = uuid.uuid4()
    rent.record_payment(conn, pmc_id, lease_id, request_id, Decimal(amount), day)
    return request_id


def test_a_file_has_all_its_lines_at_commit_and_no_more(conn, pmc):
    with (
        pytest.raises(psycopg.errors.CheckViolation, match="has 1 lines; it says 2"),
        conn.transaction(),
    ):
        file_id = a_file(conn, pmc.pmc_id, lines=2)
        a_line(conn, pmc.pmc_id, file_id)
    with (
        pytest.raises(psycopg.errors.CheckViolation, match="has 1 lines after its header"),
        conn.transaction(),
    ):
        file_id = a_file(conn, pmc.pmc_id, lines=1)
        a_line(conn, pmc.pmc_id, file_id, line_no=3)
    with conn.transaction():
        file_id = a_file(conn, pmc.pmc_id, lines=2)
        a_line(conn, pmc.pmc_id, file_id, line_no=2)
        a_line(conn, pmc.pmc_id, file_id, line_no=3)
    with pytest.raises(psycopg.errors.UniqueViolation):
        a_line(conn, pmc.pmc_id, file_id, line_no=3)


def test_a_file_comes_in_once_per_company(conn, pmc):
    a_file_of_one_line(conn, pmc.pmc_id)
    a_file_of_one_line(conn, make_pmc(conn).pmc_id)  # another company's copy of it

    with pytest.raises(psycopg.errors.UniqueViolation):
        a_file_of_one_line(conn, pmc.pmc_id)


def test_a_recorded_line_is_a_payment_of_its_amount_on_its_day_to_its_lease(conn, pmc, lease):
    line = a_file_of_one_line(conn, pmc.pmc_id, lease_id=lease)

    for request_id in (
        uuid.uuid4(),  # no payment at all
        pay(conn, pmc.pmc_id, lease, "99.99"),  # another amount
        pay(conn, pmc.pmc_id, lease, day=TODAY - timedelta(1)),  # another day
    ):
        with (
            pytest.raises(psycopg.errors.CheckViolation, match="has no payment of 100.00"),
            conn.transaction(),
        ):
            post(conn, pmc.pmc_id, line, lease, request_id)
    with conn.transaction():
        post(conn, pmc.pmc_id, line, lease, pay(conn, pmc.pmc_id, lease))


def test_a_line_is_recorded_or_set_aside_once_never_both(conn, pmc, lease):
    recorded = a_file_of_one_line(conn, pmc.pmc_id, lease_id=lease)
    aside = a_file_of_one_line(conn, pmc.pmc_id, digest="b" * 64)
    with conn.transaction():
        post(conn, pmc.pmc_id, recorded, lease, pay(conn, pmc.pmc_id, lease))
    set_aside(conn, pmc.pmc_id, aside)

    with pytest.raises(psycopg.errors.UniqueViolation, match="is recorded already"):
        set_aside(conn, pmc.pmc_id, recorded)
    with pytest.raises(psycopg.errors.UniqueViolation, match="is set aside already"):
        set_aside(conn, pmc.pmc_id, aside)
    with (
        pytest.raises(psycopg.errors.UniqueViolation, match="is set aside already"),
        conn.transaction(),
    ):
        post(conn, pmc.pmc_id, aside, lease, pay(conn, pmc.pmc_id, lease))
    with (
        pytest.raises(psycopg.errors.UniqueViolation, match="is recorded already"),
        conn.transaction(),
    ):
        post(conn, pmc.pmc_id, recorded, lease, pay(conn, pmc.pmc_id, lease))


def test_nothing_is_rewritten(conn, pmc, lease):
    line = a_file_of_one_line(conn, pmc.pmc_id, lease_id=lease)
    with conn.transaction():
        post(conn, pmc.pmc_id, line, lease, pay(conn, pmc.pmc_id, lease))
    set_aside(conn, pmc.pmc_id, a_file_of_one_line(conn, pmc.pmc_id, digest="b" * 64))

    for change in (
        "UPDATE trust_payment_files SET file_name = 'other.csv'",
        "DELETE FROM trust_payment_files",
        "UPDATE trust_payment_file_lines SET amount = 1",
        "DELETE FROM trust_payment_file_lines",
        "UPDATE trust_payment_file_postings SET request_id = gen_random_uuid()",
        "DELETE FROM trust_payment_file_postings",
        "UPDATE trust_payment_file_skips SET reason = 'other'",
        "DELETE FROM trust_payment_file_skips",
    ):
        with pytest.raises(psycopg.errors.RestrictViolation, match="append-only"):
            conn.execute(change)


@pytest.mark.parametrize(
    ("column", "value"),
    [
        ("amount", Decimal("0.00")),
        ("amount", Decimal("-5.00")),
        ("amount", Decimal("1.005")),
        ("method", "barter"),
        ("unit", " "),
        ("payer", "x" * 201),
    ],
)
def test_a_line_says_money_received_in_cents(conn, pmc, column, value):
    said = {"amount": Decimal("1.00"), "unit": "1A", column: value}
    with pytest.raises(psycopg.errors.CheckViolation), conn.transaction():
        file_id = a_file(conn, pmc.pmc_id)
        conn.execute(
            "INSERT INTO trust_payment_file_lines (pmc_id, file_id, line_no, received_on,"
            f" {', '.join(said)}) VALUES (%s, %s, 2, %s, {', '.join(['%s'] * len(said))})",  # noqa: S608
            (pmc.pmc_id, file_id, TODAY, *said.values()),
        )


def test_a_line_names_a_lease_of_its_own_company(conn, pmc):
    theirs = make_pmc(conn)
    prop = theirs.owners[0].property_id
    tenant = add_tenant(conn, theirs.pmc_id, prop, "Tenant 8")
    their_lease = open_lease(
        conn,
        theirs.pmc_id,
        add_unit(conn, theirs.pmc_id, prop, "9"),
        TODAY - timedelta(30),
        None,
        "900.00",
        [tenant],
    )

    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        a_file_of_one_line(conn, pmc.pmc_id, lease_id=their_lease)


def test_the_app_writes_them_and_the_ai_only_reads_them(conn, app_conn, ai_conn, pmc, lease):
    line = a_file_of_one_line(app_conn, pmc.pmc_id, lease_id=lease)

    assert ai_conn.execute(
        "SELECT amount, lease_id FROM trust_payment_file_lines WHERE id = %s", (line,)
    ).fetchall() == [(Decimal("100.00"), lease)]
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        set_aside(ai_conn, pmc.pmc_id, line)
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        app_conn.execute("DELETE FROM trust_payment_file_lines")
