"""Property groups (migration 20261016000065): a PMC's named sets of its properties. A group is
renamed, never deleted; a property joins or leaves it by a row, the latest holding, and every row
changes something. Rows are kept for good and name who wrote them."""

import psycopg
import pytest
from helpers import acting_as, make_pmc, staff_named


@pytest.fixture
def pmc(conn):
    return make_pmc(conn, owners=3)


def group(conn, pmc, name="North"):
    return conn.execute(
        "INSERT INTO trust_property_groups (pmc_id, display_name) VALUES (%s, %s) RETURNING id",
        (pmc.pmc_id, name),
    ).fetchone()[0]


def member(conn, pmc, group_id, property_id, joins=True):
    conn.execute(
        "INSERT INTO trust_property_group_members (pmc_id, group_id, property_id, member)"
        " VALUES (%s, %s, %s, %s)",
        (pmc.pmc_id, group_id, property_id, joins),
    )


def properties_of(conn, group_id):
    return {row[0] for row in conn.execute("SELECT trust_group_properties(%s)", (group_id,))}


def test_a_property_joins_and_leaves_a_group_by_rows(app_conn, pmc):
    first, second, third = (o.property_id for o in pmc.owners)
    north = group(app_conn, pmc)
    assert properties_of(app_conn, north) == set()

    member(app_conn, pmc, north, first)
    member(app_conn, pmc, north, second)
    member(app_conn, pmc, north, first, joins=False)
    member(app_conn, pmc, north, third)
    member(app_conn, pmc, north, first)

    assert properties_of(app_conn, north) == {first, second, third}
    # Another group holds its own properties.
    south = group(app_conn, pmc, "South")
    member(app_conn, pmc, south, second)
    assert properties_of(app_conn, south) == {second}


def test_a_row_changes_something(app_conn, pmc):
    first = pmc.owners[0].property_id
    north = group(app_conn, pmc)
    with pytest.raises(psycopg.errors.CheckViolation, match="Property 1 is not in North"):
        member(app_conn, pmc, north, first, joins=False)
    member(app_conn, pmc, north, first)
    with pytest.raises(psycopg.errors.CheckViolation, match="Property 1 is already in North"):
        member(app_conn, pmc, north, first)


def test_a_group_holds_only_its_own_companys_properties(app_conn, conn, pmc):
    other = make_pmc(conn)
    north = group(app_conn, pmc)
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        member(app_conn, pmc, north, other.owners[0].property_id)
    theirs = group(conn, other, "Theirs")
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        member(app_conn, pmc, theirs, pmc.owners[0].property_id)


def test_a_group_name_is_once_per_company_whatever_its_case(app_conn, conn, pmc):
    group(app_conn, pmc, "North")
    with pytest.raises(psycopg.errors.UniqueViolation):
        group(app_conn, pmc, "NORTH")
    group(conn, make_pmc(conn), "North")
    for bad in ("", " North", "x" * 101):
        with pytest.raises(psycopg.errors.CheckViolation):
            group(app_conn, pmc, bad)


def test_a_group_is_renamed_never_deleted_and_rows_are_kept(app_conn, conn, pmc):
    north = group(app_conn, pmc)
    member(app_conn, pmc, north, pmc.owners[0].property_id)
    app_conn.execute(
        "UPDATE trust_property_groups SET display_name = 'North portfolio' WHERE id = %s", (north,)
    )
    for statement in (
        "DELETE FROM trust_property_groups",
        "UPDATE trust_property_groups SET pmc_id = pmc_id",
        "UPDATE trust_property_group_members SET member = NOT member",
        "DELETE FROM trust_property_group_members",
    ):
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            app_conn.execute(statement)
    # Not even the owner of the tables rewrites a membership row.
    for statement in (
        "UPDATE trust_property_group_members SET member = NOT member",
        "DELETE FROM trust_property_group_members",
    ):
        with pytest.raises(psycopg.errors.RestrictViolation, match="append-only"):
            conn.execute(statement)


def test_each_row_names_who_wrote_it_and_is_audited(app_conn, conn, pmc):
    manager = staff_named(conn, pmc.pmc_id, "Manager 1")
    with acting_as(app_conn, manager):
        north = group(app_conn, pmc)
        member(app_conn, pmc, north, pmc.owners[0].property_id)
        app_conn.execute(
            "UPDATE trust_property_groups SET display_name = 'North 2' WHERE id = %s", (north,)
        )
    assert conn.execute(
        "SELECT recorded_by FROM trust_property_group_members WHERE group_id = %s", (north,)
    ).fetchone() == (manager,)
    logged = conn.execute(
        "SELECT table_name, action, staff_id FROM trust_audit_log"
        " WHERE pmc_id = %s AND table_name LIKE 'trust_property_group%%' ORDER BY id",
        (pmc.pmc_id,),
    ).fetchall()
    assert logged == [
        ("trust_property_groups", "insert", manager),
        ("trust_property_group_members", "insert", manager),
        ("trust_property_groups", "update", manager),
    ]
