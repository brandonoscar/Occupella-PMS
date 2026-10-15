"""Custom staff roles (migration 20261015000057): roles of named areas an admin gives to staff,
and trust_staff_may, which the app asks before every staff write. A staff member with no custom
role may change every area; one with roles, only what one of them allows."""

import psycopg
import pytest
from helpers import acting_as, add_staff, admin_of, make_pmc

from pms.api.deps import AREAS as APP_AREAS

AREAS = [
    "rent", "bills", "owners", "budgets", "journal", "month-end", "switching", "setup",
    "move-out", "documents", "leasing", "maintenance", "people",
]  # fmt: skip


@pytest.fixture
def pmc(conn):
    return make_pmc(conn)


def make_role(conn, pmc_id, name, areas=(), by=None):
    with acting_as(conn, by):
        role = conn.execute(
            "INSERT INTO trust_custom_roles (pmc_id, name) VALUES (%s, %s) RETURNING id",
            (pmc_id, name),
        ).fetchone()[0]
    for area in areas:
        allow(conn, pmc_id, role, area, by=by)
    return role


def allow(conn, pmc_id, role, area, granted=True, by=None):
    with acting_as(conn, by):
        conn.execute(
            "INSERT INTO trust_custom_role_areas (pmc_id, role_id, area, granted)"
            " VALUES (%s, %s, %s, %s)",
            (pmc_id, role, area, granted),
        )


def give(conn, pmc_id, staff_id, role, granted=True, by=None):
    with acting_as(conn, by):
        conn.execute(
            "INSERT INTO trust_staff_custom_roles (pmc_id, staff_id, role_id, granted)"
            " VALUES (%s, %s, %s, %s)",
            (pmc_id, staff_id, role, granted),
        )


def areas(conn, pmc_id, staff_id):
    return [
        area
        for area in AREAS
        if conn.execute("SELECT trust_staff_may(%s, %s, %s)", (pmc_id, staff_id, area)).fetchone()[
            0
        ]
    ]


def test_the_areas_are_the_apps():
    assert list(APP_AREAS) == AREAS


def test_a_staff_member_with_no_custom_role_may_change_every_area(conn, app_conn, pmc):
    clerk = add_staff(conn, pmc.pmc_id, "Clerk 1")
    assert areas(app_conn, pmc.pmc_id, clerk) == AREAS
    # A role given and taken away again leaves none.
    role = make_role(conn, pmc.pmc_id, "Bookkeeping", ["journal"])
    give(conn, pmc.pmc_id, clerk, role)
    give(conn, pmc.pmc_id, clerk, role, granted=False)
    assert areas(app_conn, pmc.pmc_id, clerk) == AREAS


def test_a_staff_member_with_roles_may_change_what_one_of_them_allows(conn, app_conn, pmc):
    clerk = add_staff(conn, pmc.pmc_id, "Clerk 1")
    books = make_role(conn, pmc.pmc_id, "Bookkeeping", ["journal", "bills"])
    leasing = make_role(conn, pmc.pmc_id, "Leasing agent", ["leasing"])
    empty = make_role(conn, pmc.pmc_id, "Look only")

    give(conn, pmc.pmc_id, clerk, empty)
    assert areas(app_conn, pmc.pmc_id, clerk) == []
    give(conn, pmc.pmc_id, clerk, books)
    give(conn, pmc.pmc_id, clerk, leasing)
    assert areas(app_conn, pmc.pmc_id, clerk) == ["bills", "journal", "leasing"]
    # An area's latest row holds.
    allow(conn, pmc.pmc_id, books, "bills", granted=False)
    allow(conn, pmc.pmc_id, books, "rent")
    assert areas(app_conn, pmc.pmc_id, clerk) == ["rent", "journal", "leasing"]
    give(conn, pmc.pmc_id, clerk, books, granted=False)
    assert areas(app_conn, pmc.pmc_id, clerk) == ["leasing"]


def test_only_an_admin_changes_a_custom_role(conn, pmc):
    admin = admin_of(conn, pmc.pmc_id)
    clerk = add_staff(conn, pmc.pmc_id, "Clerk 1")
    role = make_role(conn, pmc.pmc_id, "Bookkeeping", ["journal"], by=admin)

    for change in (
        lambda: make_role(conn, pmc.pmc_id, "Mine", by=clerk),
        lambda: allow(conn, pmc.pmc_id, role, "rent", by=clerk),
        lambda: give(conn, pmc.pmc_id, clerk, role, by=clerk),
    ):
        with pytest.raises(
            psycopg.errors.InsufficientPrivilege,
            match="Clerk 1 may not change a custom role .the admin role.",
        ):
            change()

    # Each change names who made it; the command line names nobody.
    give(conn, pmc.pmc_id, clerk, role, by=admin)
    assert conn.execute(
        "SELECT changed_by FROM trust_staff_custom_roles WHERE role_id = %s", (role,)
    ).fetchall() == [(admin,)]
    assert conn.execute(
        "SELECT changed_by FROM trust_custom_roles WHERE id = %s", (role,)
    ).fetchone() == (admin,)


def test_custom_roles_are_append_only(conn, pmc):
    clerk = add_staff(conn, pmc.pmc_id, "Clerk 1")
    give(conn, pmc.pmc_id, clerk, make_role(conn, pmc.pmc_id, "Bookkeeping", ["journal"]))
    for table in ("trust_custom_roles", "trust_custom_role_areas", "trust_staff_custom_roles"):
        for query in (f"UPDATE {table} SET pmc_id = pmc_id", f"DELETE FROM {table}"):
            with pytest.raises(psycopg.errors.RestrictViolation, match="append-only"):
                conn.execute(query)


def test_a_role_its_areas_and_its_staff_are_of_one_pmc(conn, pmc):
    other = make_pmc(conn)
    theirs = add_staff(conn, other.pmc_id, "Clerk 2")
    role = make_role(conn, pmc.pmc_id, "Bookkeeping")
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        give(conn, pmc.pmc_id, theirs, role)
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        allow(conn, other.pmc_id, role, "rent")
    with pytest.raises(psycopg.errors.UniqueViolation):
        make_role(conn, pmc.pmc_id, "Bookkeeping")
    with pytest.raises(psycopg.errors.CheckViolation):
        allow(conn, pmc.pmc_id, role, "payroll")
