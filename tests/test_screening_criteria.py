"""Screening criteria and decisions against a screening, at the database (migration
20261018000089; the founder's review, q085). The API's side is in
tests/api/test_screening_criteria_api.py. Every name here is synthetic."""

import psycopg
import pytest
from helpers import (
    acting_as,
    add_staff,
    add_unit,
    make_pmc,
    open_application_link,
    record_decision,
    record_screening,
    submit_application,
)

INSERT = (
    "INSERT INTO trust_screening_criteria (pmc_id, approve_multiple, conditional_multiple, written)"
    " VALUES (%s, %s, %s, %s) RETURNING id"
)


class Office:
    def __init__(self, conn):
        self.pmc = make_pmc(conn)
        pmc_id = self.pmc.pmc_id
        self.staff = add_staff(conn, pmc_id, "Clerk 1")
        unit = add_unit(conn, pmc_id, self.pmc.owners[0].property_id, "Unit 1")
        self.link = open_application_link(conn, pmc_id, unit, self.staff)

    def application(self, conn):
        return submit_application(conn, self.pmc.pmc_id, self.link)


@pytest.fixture
def office(conn):
    return Office(conn)


def test_criteria_ask_at_least_the_rent_and_conditions_no_more_than_approval(conn, office):
    pmc_id = office.pmc.pmc_id
    conn.execute(INSERT, (pmc_id, "2.50", "2.00", "Income of 2.5 times the rent."))
    conn.execute(INSERT, (pmc_id, "1.00", "0.00", None))
    for approve, conditional, written in (
        ("0.99", "0.50", None),
        ("10.01", "2.00", None),
        ("3.00", "3.01", None),
        ("3.00", "-0.01", None),
        ("3.00", "2.00", " "),
        ("3.00", "2.00", "x" * 4001),
    ):
        with pytest.raises(psycopg.errors.CheckViolation), conn.transaction():
            conn.execute(INSERT, (pmc_id, approve, conditional, written))


def test_criteria_are_append_only_audited_and_name_who(conn, office):
    pmc_id = office.pmc.pmc_id
    with acting_as(conn, office.staff):
        (made,) = conn.execute(INSERT, (pmc_id, "3.00", "2.00", None)).fetchone()
    assert conn.execute(
        "SELECT recorded_by FROM trust_screening_criteria WHERE id = %s", (made,)
    ).fetchone() == (office.staff,)
    for query in (
        "UPDATE trust_screening_criteria SET written = 'x'",
        "DELETE FROM trust_screening_criteria",
    ):
        with pytest.raises(psycopg.errors.RestrictViolation, match="append-only"):
            conn.execute(query)
    assert conn.execute(
        "SELECT count(*) FROM trust_audit_log WHERE pmc_id = %s"
        " AND table_name = 'trust_screening_criteria'",
        (pmc_id,),
    ).fetchone() == (1,)


def test_a_screening_names_criteria_of_its_own_company(conn, office):
    pmc_id = office.pmc.pmc_id
    application = office.application(conn)
    screening = record_screening(conn, pmc_id, application, office.staff)
    (ours,) = conn.execute(INSERT, (pmc_id, "3.00", "2.00", None)).fetchone()
    (theirs,) = conn.execute(INSERT, (make_pmc(conn).pmc_id, "3.00", "2.00", None)).fetchone()
    for criteria, refused in ((ours, None), (theirs, psycopg.errors.ForeignKeyViolation)):
        update = (
            "INSERT INTO trust_screenings (pmc_id, application_id, partner, partner_reference,"
            " agency, recommendation, reasons, requested_by, criteria_id)"
            " VALUES (%s, %s, 'Partner', 'r', 'Agency', 'approve', '{}', %s, %s)"
        )
        if refused is None:
            conn.execute(update, (pmc_id, application, office.staff, criteria))
        else:
            with pytest.raises(refused), conn.transaction():
                conn.execute(update, (pmc_id, application, office.staff, criteria))
    assert screening is not None


@pytest.mark.parametrize(
    ("recommendation", "decision", "against"),
    [
        ("approve", "approved", False),
        ("approve", "conditional", True),
        ("approve", "declined", True),
        ("conditional", "conditional", False),
        ("conditional", "approved", True),
        ("decline", "declined", False),
        ("decline", "approved", True),
    ],
)
def test_a_decision_against_the_recommendation_says_why_and_no_other_does(
    conn, office, recommendation, decision, against
):
    pmc_id = office.pmc.pmc_id
    application = office.application(conn)
    record_screening(
        conn,
        pmc_id,
        application,
        office.staff,
        recommendation,
        ["Income"] if recommendation != "approve" else [],
    )
    conditions = "A co-signer" if decision == "conditional" else None

    wrong = None if against else "Because"
    with pytest.raises(psycopg.errors.CheckViolation, match="says why"), conn.transaction():
        record_decision(conn, pmc_id, application, office.staff, decision, None, conditions, wrong)
    record_decision(
        conn,
        pmc_id,
        application,
        office.staff,
        decision,
        None,
        conditions,
        "The owner asked for it" if against else None,
    )


def test_only_the_latest_screening_counts_and_none_needs_no_reason(conn, office):
    pmc_id = office.pmc.pmc_id
    unscreened, twice = office.application(conn), office.application(conn)
    with pytest.raises(psycopg.errors.CheckViolation, match="says why"), conn.transaction():
        record_decision(conn, pmc_id, unscreened, office.staff, "declined", override_reason="x")
    record_decision(conn, pmc_id, unscreened, office.staff, "declined")
    # A decline, then (a moment later) an approval: the approval is the one that counts.
    conn.execute(
        "INSERT INTO trust_screenings (pmc_id, application_id, partner, partner_reference, agency,"
        " recommendation, reasons, requested_by, reported_at) VALUES"
        " (%s, %s, 'Partner', 'r1', 'Agency', 'decline', '{Income}', %s,"
        "  now() - interval '1 hour'),"
        " (%s, %s, 'Partner', 'r2', 'Agency', 'approve', '{}', %s, now())",
        (pmc_id, twice, office.staff, pmc_id, twice, office.staff),
    )
    record_decision(conn, pmc_id, twice, office.staff, "approved")
    for reason in (" ", "x" * 501):
        other = office.application(conn)
        record_screening(conn, pmc_id, other, office.staff, "decline", ["Income"])
        with pytest.raises(psycopg.errors.CheckViolation), conn.transaction():
            record_decision(conn, pmc_id, other, office.staff, "approved", override_reason=reason)
