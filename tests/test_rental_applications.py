"""Rental applications (migration 20261011000030): application links, applications and their
applicants, screenings, decisions and adverse action records. The API's side is tested in
tests/api/test_apply.py and tests/api/test_leasing.py.
"""

import psycopg
import pytest
from helpers import (
    add_staff,
    add_unit,
    in_background,
    make_pmc,
    open_application_link,
    record_adverse_action,
    record_decision,
    record_screening,
    submit_application,
    wait_until_blocked,
)

TABLES = [
    "trust_application_links",
    "trust_application_link_closings",
    "trust_applications",
    "trust_applicants",
    "trust_screenings",
    "trust_application_decisions",
    "trust_adverse_actions",
]


class Place:
    """Unit 1 of Property 1, a staff member and an open application link for the unit."""

    def __init__(self, conn):
        self.pmc = make_pmc(conn)
        self.pmc_id = self.pmc.pmc_id
        self.unit = add_unit(conn, self.pmc_id, self.pmc.owners[0].property_id)
        self.staff = add_staff(conn, self.pmc_id)
        self.link = open_application_link(conn, self.pmc_id, self.unit, self.staff)

    def close(self, conn, link=None):
        conn.execute(
            "INSERT INTO trust_application_link_closings (pmc_id, link_id, closed_by)"
            " VALUES (%s, %s, %s)",
            (self.pmc_id, link or self.link, self.staff),
        )


@pytest.fixture
def place(conn):
    return Place(conn)


def test_the_app_takes_an_application_from_link_to_decision(conn, app_conn, place):
    pmc_id, staff = place.pmc_id, place.staff
    application = submit_application(
        app_conn, pmc_id, place.link, applicants=[("Applicant A", None), ("Applicant B", None)]
    )
    screening = record_screening(
        app_conn, pmc_id, application, staff, "conditional", ["Income below 3x rent"]
    )
    with app_conn.transaction():
        record_decision(
            app_conn, pmc_id, application, staff, "conditional", screening, "Extra deposit"
        )
        record_adverse_action(app_conn, pmc_id, application, staff, ["Income below 3x rent"])

    assert conn.execute(
        "SELECT d.decision, d.conditions, a.reasons, (SELECT count(*) FROM trust_applicants AS p"
        "  WHERE p.application_id = d.application_id)"
        " FROM trust_application_decisions AS d"
        " JOIN trust_adverse_actions AS a USING (application_id)"
        " WHERE d.application_id = %s",
        (application,),
    ).fetchone() == ("conditional", "Extra deposit", ["Income below 3x rent"], 2)


def test_a_closed_link_takes_no_application_and_closes_once(conn, place):
    place.close(conn)

    with pytest.raises(psycopg.errors.CheckViolation, match="is closed"):
        submit_application(conn, place.pmc_id, place.link)
    with pytest.raises(psycopg.errors.UniqueViolation):
        place.close(conn)


@pytest.mark.timing
def test_closing_a_link_waits_for_an_application_on_its_way(conn, database_url, place):
    with psycopg.connect(database_url) as first:
        application = submit_application(first, place.pmc_id, place.link)  # not committed yet
        thread, pid, outcome = in_background(database_url, place.close)
        wait_until_blocked(conn, pid)
        first.commit()
    thread.join(timeout=30)

    assert "error" not in outcome, outcome
    assert conn.execute(
        "SELECT count(*) FROM trust_applications WHERE id = %s", (application,)
    ).fetchone() == (1,)
    with pytest.raises(psycopg.errors.CheckViolation, match="is closed"):
        submit_application(conn, place.pmc_id, place.link)


def test_applications_are_taken_at_read_committed(database_url, place):
    with psycopg.connect(database_url) as repeatable:
        repeatable.isolation_level = psycopg.IsolationLevel.REPEATABLE_READ
        with pytest.raises(psycopg.errors.InvalidTransactionState, match="READ COMMITTED"):
            submit_application(repeatable, place.pmc_id, place.link)


@pytest.mark.parametrize(
    ("position", "name", "email"),
    [
        (0, "Applicant", "a@example.com"),
        (7, "Applicant", "a@example.com"),
        (1, "   ", "a@example.com"),
        (1, "x" * 201, "a@example.com"),
        (1, "Applicant", "A@example.com"),
        (1, "Applicant", "not an email"),
    ],
)
def test_an_applicant_has_a_place_a_name_and_an_email(conn, place, position, name, email):
    application = submit_application(conn, place.pmc_id, place.link, applicants=[])
    with pytest.raises(psycopg.errors.CheckViolation):
        conn.execute(
            "INSERT INTO trust_applicants (pmc_id, application_id, position, display_name, email)"
            " VALUES (%s, %s, %s, %s, %s)",
            (place.pmc_id, application, position, name, email),
        )


@pytest.mark.parametrize(
    "applicants",
    [
        [("Applicant A", "same@example.com"), ("Applicant B", "same@example.com")],
    ],
)
def test_each_applicant_of_an_application_has_their_own_email(conn, place, applicants):
    with pytest.raises(psycopg.errors.UniqueViolation):
        submit_application(conn, place.pmc_id, place.link, applicants=applicants)


@pytest.mark.parametrize(
    ("recommendation", "reasons"),
    [("approve", ["a reason"]), ("decline", []), ("conditional", []), ("maybe", ["a reason"])],
)
def test_a_screening_gives_reasons_exactly_when_it_isnt_an_approval(
    conn, place, recommendation, reasons
):
    application = submit_application(conn, place.pmc_id, place.link)
    with pytest.raises(psycopg.errors.CheckViolation):
        record_screening(conn, place.pmc_id, application, place.staff, recommendation, reasons)


def test_a_decision_rests_only_on_a_screening_of_its_own_application(conn, place):
    first = submit_application(conn, place.pmc_id, place.link)
    second = submit_application(conn, place.pmc_id, place.link)
    screening = record_screening(conn, place.pmc_id, first, place.staff)

    with pytest.raises(psycopg.errors.CheckViolation, match="is not of application"):
        record_decision(conn, place.pmc_id, second, place.staff, "approved", screening)


@pytest.mark.parametrize(
    ("decision", "conditions"),
    [
        ("conditional", None),
        ("conditional", "  "),
        ("approved", "Extra deposit"),
        ("declined", "Extra deposit"),
        ("waitlisted", None),
    ],
)
def test_only_a_conditional_approval_has_conditions(conn, place, decision, conditions):
    application = submit_application(conn, place.pmc_id, place.link)
    with pytest.raises(psycopg.errors.CheckViolation):
        record_decision(conn, place.pmc_id, application, place.staff, decision, None, conditions)


def test_an_application_has_one_decision(conn, place):
    application = submit_application(conn, place.pmc_id, place.link)
    record_decision(conn, place.pmc_id, application, place.staff, "approved")

    with pytest.raises(psycopg.errors.UniqueViolation):
        record_decision(conn, place.pmc_id, application, place.staff, "declined")


@pytest.mark.parametrize(
    ("recommendation", "reasons", "decision", "conditions"),
    [
        ("decline", ["Income too low"], "declined", None),
        ("conditional", ["Income too low"], "conditional", "Extra deposit"),
        # The PMC's own decline, against an approving screening: the screening still counts.
        ("approve", [], "declined", None),
    ],
)
def test_a_decision_against_the_applicant_on_a_screening_needs_its_notice_at_commit(
    conn, place, recommendation, reasons, decision, conditions
):
    application = submit_application(conn, place.pmc_id, place.link)
    screening = record_screening(
        conn, place.pmc_id, application, place.staff, recommendation, reasons
    )

    refused = pytest.raises(psycopg.errors.CheckViolation, match="record the adverse action notice")
    with refused, conn.transaction():
        record_decision(
            conn, place.pmc_id, application, place.staff, decision, screening, conditions
        )
    with conn.transaction():
        record_decision(
            conn, place.pmc_id, application, place.staff, decision, screening, conditions
        )
        record_adverse_action(conn, place.pmc_id, application, place.staff)


@pytest.mark.parametrize("decision", ["approved on a screening", "declined without one"])
def test_an_adverse_action_belongs_only_to_such_a_decision(conn, place, decision):
    application = submit_application(conn, place.pmc_id, place.link)
    screening = record_screening(conn, place.pmc_id, application, place.staff)
    refused = pytest.raises(psycopg.errors.CheckViolation, match="an adverse action belongs")
    with refused, conn.transaction():
        if decision == "approved on a screening":
            record_decision(conn, place.pmc_id, application, place.staff, "approved", screening)
        else:
            record_decision(conn, place.pmc_id, application, place.staff, "declined")
        record_adverse_action(conn, place.pmc_id, application, place.staff)


@pytest.mark.parametrize("decision", ["approved on a screening", "declined without one"])
def test_a_notice_added_later_to_a_decision_that_needs_none_is_refused(conn, place, decision):
    application = submit_application(conn, place.pmc_id, place.link)
    screening = record_screening(conn, place.pmc_id, application, place.staff)
    if decision == "approved on a screening":
        record_decision(conn, place.pmc_id, application, place.staff, "approved", screening)
    else:
        record_decision(conn, place.pmc_id, application, place.staff, "declined")

    refused = pytest.raises(psycopg.errors.CheckViolation, match="an adverse action belongs")
    with refused, conn.transaction():
        record_adverse_action(conn, place.pmc_id, application, place.staff)


def test_an_adverse_action_needs_a_decision_and_a_reason(conn, place):
    application = submit_application(conn, place.pmc_id, place.link)
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        record_adverse_action(conn, place.pmc_id, application, place.staff)
    screening = record_screening(conn, place.pmc_id, application, place.staff, "decline", ["x"])
    with pytest.raises(psycopg.errors.CheckViolation), conn.transaction():
        record_decision(conn, place.pmc_id, application, place.staff, "declined", screening)
        record_adverse_action(conn, place.pmc_id, application, place.staff, reasons=[])


def test_the_longest_name_and_email_are_kept(conn, place):
    application = submit_application(conn, place.pmc_id, place.link, applicants=[])
    email = "a" * 242 + "@example.com"  # 254 characters
    conn.execute(
        "INSERT INTO trust_applicants (pmc_id, application_id, position, display_name, email)"
        " VALUES (%s, %s, 6, %s, %s)",
        (place.pmc_id, application, "x" * 200, email),
    )
    with pytest.raises(psycopg.errors.CheckViolation):
        conn.execute(
            "INSERT INTO trust_applicants (pmc_id, application_id, position, display_name, email)"
            " VALUES (%s, %s, 5, 'A', %s)",
            (place.pmc_id, application, "a" + email),
        )


@pytest.mark.parametrize(("rent", "kept"), [("0.01", True), ("0", False), ("-1", False)])
def test_a_link_offers_a_rent_above_zero(conn, place, rent, kept):
    if kept:
        open_application_link(conn, place.pmc_id, place.unit, place.staff, rent)
        return
    with pytest.raises(psycopg.errors.CheckViolation):
        open_application_link(conn, place.pmc_id, place.unit, place.staff, rent)


@pytest.mark.parametrize(("income", "kept"), [("0", True), ("-0.01", False)])
def test_an_income_is_zero_or_more(conn, place, income, kept):
    if kept:
        submit_application(conn, place.pmc_id, place.link, income=income)
        return
    with pytest.raises(psycopg.errors.CheckViolation):
        submit_application(conn, place.pmc_id, place.link, income=income)


@pytest.mark.parametrize("column", ["links", "applications"])
@pytest.mark.parametrize("size", [31, 33])
def test_a_token_is_kept_as_its_32_byte_sha256(conn, place, column, size):
    with pytest.raises(psycopg.errors.CheckViolation):
        if column == "links":
            conn.execute(
                "INSERT INTO trust_application_links"
                " (pmc_id, unit_id, token_sha256, monthly_rent, opened_by)"
                " VALUES (%s, %s, %s, 1, %s)",
                (place.pmc_id, place.unit, b"x" * size, place.staff),
            )
        else:
            conn.execute(
                "INSERT INTO trust_applications"
                " (pmc_id, link_id, status_token_sha256, move_in_on, monthly_income)"
                " VALUES (%s, %s, %s, DATE '2026-11-01', 1)",
                (place.pmc_id, place.link, b"x" * size),
            )


def test_each_status_token_and_link_token_is_one_of_a_kind(conn, place):
    same = b"s" * 32
    insert = (
        "INSERT INTO trust_applications"
        " (pmc_id, link_id, status_token_sha256, move_in_on, monthly_income)"
        " VALUES (%s, %s, %s, DATE '2026-11-01', 1)"
    )
    conn.execute(insert, (place.pmc_id, place.link, same))
    with pytest.raises(psycopg.errors.UniqueViolation):
        conn.execute(insert, (place.pmc_id, place.link, same))
    link_token = conn.execute(
        "SELECT token_sha256 FROM trust_application_links WHERE id = %s", (place.link,)
    ).fetchone()[0]
    with pytest.raises(psycopg.errors.UniqueViolation):
        conn.execute(
            "INSERT INTO trust_application_links"
            " (pmc_id, unit_id, token_sha256, monthly_rent, opened_by)"
            " VALUES (%s, %s, %s, 1, %s)",
            (place.pmc_id, place.unit, link_token, place.staff),
        )


@pytest.mark.parametrize(("count", "kept"), [(20, True), (21, False)])
def test_a_notice_gives_at_most_20_reasons(conn, place, count, kept):
    application = submit_application(conn, place.pmc_id, place.link)
    screening = record_screening(conn, place.pmc_id, application, place.staff, "decline", ["x"])

    def give():
        with conn.transaction():
            record_decision(conn, place.pmc_id, application, place.staff, "declined", screening)
            record_adverse_action(
                conn, place.pmc_id, application, place.staff, [f"r{n}" for n in range(count)]
            )

    if kept:
        give()
        return
    with pytest.raises(psycopg.errors.CheckViolation):
        give()


@pytest.mark.parametrize("table", TABLES)
@pytest.mark.parametrize("statement", ["UPDATE {} SET pmc_id = pmc_id", "DELETE FROM {}"])
def test_leasing_records_are_never_rewritten(conn, place, table, statement):
    with pytest.raises(psycopg.errors.RestrictViolation, match="append-only"):
        conn.execute(statement.format(table))


def test_everything_stays_in_its_pmc(conn, place):
    other = Place(conn)
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        open_application_link(conn, place.pmc_id, other.unit, place.staff)
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        open_application_link(conn, place.pmc_id, place.unit, other.staff)
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        submit_application(conn, place.pmc_id, other.link)
    theirs = submit_application(conn, other.pmc_id, other.link)
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        record_screening(conn, place.pmc_id, theirs, place.staff)
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        record_decision(conn, place.pmc_id, theirs, place.staff, "approved")


def test_every_write_is_in_the_audit_log(conn, app_conn, place):
    application = submit_application(app_conn, place.pmc_id, place.link)
    place.close(app_conn)

    logged = conn.execute(
        "SELECT table_name FROM trust_audit_log WHERE pmc_id = %s"
        " AND table_name IN ('trust_applications', 'trust_applicants',"
        " 'trust_application_link_closings') ORDER BY id",
        (place.pmc_id,),
    ).fetchall()
    assert application is not None
    assert logged == [
        ("trust_applications",),
        ("trust_applicants",),
        ("trust_application_link_closings",),
    ]


@pytest.mark.parametrize("table", TABLES)
def test_the_ai_role_reads_none_of_it(conn, table):
    assert conn.execute(
        "SELECT has_table_privilege('trust_ai_agent', %s, 'SELECT')", (table,)
    ).fetchone() == (False,)
