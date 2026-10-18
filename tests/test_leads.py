"""Leads and showings, at the database (migration 20261018000088; the founder's review, q083). The
API's side is in tests/api/test_leads_api.py. Every name here is synthetic."""

from datetime import UTC, datetime

import psycopg
import pytest
from helpers import acting_as, add_staff, add_unit, make_pmc

AT = datetime(2026, 10, 20, 15, tzinfo=UTC)


def lead(conn, pmc_id, **fields):
    values = {"display_name": "Prospect 1", "email": "prospect.1@example.com", **fields}
    columns = ", ".join(["pmc_id", *values])
    marks = ", ".join(["%s"] * (len(values) + 1))
    return conn.execute(
        f"INSERT INTO trust_leads ({columns}) VALUES ({marks}) RETURNING id",
        (pmc_id, *values.values()),
    ).fetchone()[0]


def showing(conn, pmc_id, of, unit, by=None):
    return conn.execute(
        "INSERT INTO trust_showings (pmc_id, lead_id, unit_id, starts_at, shown_by)"
        " VALUES (%s, %s, %s, %s, %s) RETURNING id",
        (pmc_id, of, unit, AT, by),
    ).fetchone()[0]


def close(conn, pmc_id, of, outcome="lost", reason=None):
    conn.execute(
        "INSERT INTO trust_lead_closings (pmc_id, lead_id, outcome, reason)"
        " VALUES (%s, %s, %s, %s)",
        (pmc_id, of, outcome, reason),
    )


class Office:
    def __init__(self, conn):
        self.pmc = make_pmc(conn)
        self.place = self.pmc.owners[0].property_id
        self.unit = add_unit(conn, self.pmc.pmc_id, self.place, "Unit 1")
        self.clerk = add_staff(conn, self.pmc.pmc_id, "Clerk 1")


@pytest.fixture
def office(conn):
    return Office(conn)


def test_a_lead_needs_a_name_and_a_way_to_reach_them(conn, office):
    pmc_id = office.pmc.pmc_id
    lead(conn, pmc_id, email=None, phone="555-555-0100", property_id=office.place)
    lead(conn, pmc_id, property_id=office.place, unit_id=office.unit, source="Walk-in")
    for fields in (
        {"email": None},
        {"display_name": " "},
        {"email": "not an email"},
        {"phone": " "},
        {"source": "x" * 61},
        {"note": "x" * 2001},
    ):
        with pytest.raises(psycopg.errors.CheckViolation), conn.transaction():
            lead(conn, pmc_id, **fields)
    # A unit comes with its property.
    with pytest.raises(psycopg.errors.CheckViolation, match="not one of"), conn.transaction():
        lead(conn, pmc_id, unit_id=office.unit)


def test_the_unit_asked_about_is_the_propertys_and_the_companys(conn, office):
    pmc_id = office.pmc.pmc_id
    other_property = conn.execute(
        "INSERT INTO trust_properties (pmc_id, display_name) VALUES (%s, 'Property 2')"
        " RETURNING id",
        (pmc_id,),
    ).fetchone()[0]
    with pytest.raises(psycopg.errors.CheckViolation, match="not one of"), conn.transaction():
        lead(conn, pmc_id, property_id=other_property, unit_id=office.unit)
    elsewhere = make_pmc(conn)
    with pytest.raises(psycopg.errors.ForeignKeyViolation), conn.transaction():
        lead(conn, elsewhere.pmc_id, property_id=office.place)


def test_a_closed_lead_takes_no_showing_and_each_closes_and_goes_once(conn, office):
    pmc_id = office.pmc.pmc_id
    prospect = lead(conn, pmc_id)
    first = showing(conn, pmc_id, prospect, office.unit, office.clerk)
    conn.execute(
        "INSERT INTO trust_showing_outcomes (pmc_id, showing_id, outcome) VALUES (%s, %s, 'shown')",
        (pmc_id, first),
    )
    with pytest.raises(psycopg.errors.UniqueViolation), conn.transaction():
        conn.execute(
            "INSERT INTO trust_showing_outcomes (pmc_id, showing_id, outcome)"
            " VALUES (%s, %s, 'no_show')",
            (pmc_id, first),
        )
    close(conn, pmc_id, prospect, "applied")
    with pytest.raises(psycopg.errors.CheckViolation, match="closed"), conn.transaction():
        showing(conn, pmc_id, prospect, office.unit)
    with pytest.raises(psycopg.errors.UniqueViolation), conn.transaction():
        close(conn, pmc_id, prospect)
    for bad in (
        "INSERT INTO trust_lead_closings (pmc_id, lead_id, outcome) VALUES (%s, %s, 'won')",
        "INSERT INTO trust_showing_outcomes (pmc_id, showing_id, outcome) VALUES (%s, %s, 'late')",
    ):
        with pytest.raises(psycopg.errors.CheckViolation), conn.transaction():
            conn.execute(bad, (pmc_id, lead(conn, pmc_id)))
    # Another company's unit or staff member is not shown to this company's lead.
    elsewhere = make_pmc(conn)
    theirs = add_unit(conn, elsewhere.pmc_id, elsewhere.owners[0].property_id, "Unit 9")
    their_clerk = add_staff(conn, elsewhere.pmc_id, "Clerk 9")
    other = lead(conn, pmc_id)
    for unit, by in ((theirs, None), (office.unit, their_clerk)):
        with pytest.raises(psycopg.errors.ForeignKeyViolation), conn.transaction():
            showing(conn, pmc_id, other, unit, by)


def test_leads_and_showings_are_append_only_audited_and_name_who(conn, office):
    pmc_id = office.pmc.pmc_id
    with acting_as(conn, office.clerk):
        prospect = lead(conn, pmc_id)
        shown = showing(conn, pmc_id, prospect, office.unit)
        conn.execute(
            "INSERT INTO trust_showing_outcomes (pmc_id, showing_id, outcome, note)"
            " VALUES (%s, %s, 'cancelled', 'Rescheduling')",
            (pmc_id, shown),
        )
        close(conn, pmc_id, prospect, "lost", "Chose a place nearer work")
    assert (
        conn.execute(
            "SELECT l.recorded_by, s.recorded_by, o.recorded_by, c.recorded_by"
            " FROM trust_leads AS l"
            " JOIN trust_showings AS s ON s.lead_id = l.id"
            " JOIN trust_showing_outcomes AS o ON o.showing_id = s.id"
            " JOIN trust_lead_closings AS c ON c.lead_id = l.id WHERE l.id = %s",
            (prospect,),
        ).fetchone()
        == (office.clerk,) * 4
    )
    for table in ("trust_leads", "trust_showings", "trust_showing_outcomes", "trust_lead_closings"):
        with pytest.raises(psycopg.errors.RestrictViolation, match="append-only"):
            conn.execute(f"DELETE FROM {table}")
    assert conn.execute(
        "SELECT count(*) FROM trust_audit_log WHERE pmc_id = %s AND table_name IN"
        " ('trust_leads', 'trust_showings', 'trust_showing_outcomes', 'trust_lead_closings')",
        (pmc_id,),
    ).fetchone() == (4,)
