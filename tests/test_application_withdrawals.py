"""Withdrawn applications (migration 20261016000070): an applicant who no longer wants the home,
recorded once, before the application is declined or made a lease; after it the application
takes no screening, decision or lease offer, and none of its offers becomes a lease. The API's
side is tested in tests/api/test_applicants_api.py."""

from datetime import date

import psycopg
import pytest
from helpers import (
    acting_as,
    add_staff,
    add_unit,
    complete_offer,
    in_background,
    make_pmc,
    offer_lease,
    open_application_link,
    record_decision,
    record_screening,
    send_offer,
    sign_offer,
    submit_application,
    wait_until_blocked,
)

JAN_1 = date(2027, 1, 1)


class Place:
    """Unit 1 with an open application link, a staff member and one application through it."""

    def __init__(self, conn):
        self.pmc = make_pmc(conn)
        self.pmc_id = self.pmc.pmc_id
        self.unit = add_unit(conn, self.pmc_id, self.pmc.owners[0].property_id)
        self.staff = add_staff(conn, self.pmc_id)
        self.link = open_application_link(conn, self.pmc_id, self.unit, self.staff)
        self.application = submit_application(
            conn, self.pmc_id, self.link, applicants=[("Applicant A", "a@example.com")]
        )

    def withdraw(self, conn, application=None, reason=None):
        conn.execute(
            "INSERT INTO trust_application_withdrawals (pmc_id, application_id, reason)"
            " VALUES (%s, %s, %s)",
            (self.pmc_id, application or self.application, reason),
        )

    def signed_offer(self, conn):
        offer = offer_lease(
            conn,
            self.pmc_id,
            self.unit,
            self.staff,
            JAN_1,
            application_id=self.application,
            signers=(("Applicant A", "a@example.com"),),
        )
        send_offer(conn, self.pmc_id, offer, self.staff)
        sign_offer(conn, self.pmc_id, offer, 1)
        return offer


@pytest.fixture
def place(conn):
    return Place(conn)


def test_the_app_records_a_withdrawal_with_why_and_who(conn, app_conn, place):
    with acting_as(app_conn, place.staff):
        place.withdraw(app_conn, reason="Found another home")

    assert conn.execute(
        "SELECT reason, recorded_by FROM trust_application_withdrawals WHERE application_id = %s",
        (place.application,),
    ).fetchone() == ("Found another home", place.staff)


def test_an_application_is_withdrawn_once(conn, place):
    place.withdraw(conn)

    with pytest.raises(psycopg.errors.UniqueViolation):
        place.withdraw(conn)


@pytest.mark.parametrize("reason", ["   ", "x" * 501])
def test_a_reason_says_something_in_at_most_500_characters(conn, place, reason):
    with pytest.raises(psycopg.errors.CheckViolation):
        place.withdraw(conn, reason=reason)


def test_an_approved_application_may_be_withdrawn_and_a_declined_one_not(conn, place):
    record_decision(conn, place.pmc_id, place.application, place.staff, "approved")
    place.withdraw(conn)
    declined = submit_application(conn, place.pmc_id, place.link)
    record_decision(conn, place.pmc_id, declined, place.staff, "declined")

    with pytest.raises(psycopg.errors.CheckViolation, match="a declined application is not"):
        place.withdraw(conn, declined)


def test_an_application_made_a_lease_is_not_withdrawn(conn, place):
    record_decision(conn, place.pmc_id, place.application, place.staff, "approved")
    complete_offer(conn, place.pmc_id, place.signed_offer(conn), place.staff)

    with pytest.raises(psycopg.errors.CheckViolation, match="is a lease already"):
        place.withdraw(conn)


def test_a_withdrawn_application_takes_no_screening_decision_or_offer(conn, place):
    place.withdraw(conn)

    with pytest.raises(psycopg.errors.CheckViolation, match="withdrew this application"):
        record_screening(conn, place.pmc_id, place.application, place.staff)
    with pytest.raises(psycopg.errors.CheckViolation, match="withdrew this application"):
        record_decision(conn, place.pmc_id, place.application, place.staff, "approved")


def test_a_withdrawn_approval_takes_no_new_offer(conn, place):
    record_decision(conn, place.pmc_id, place.application, place.staff, "approved")
    place.withdraw(conn)

    with pytest.raises(psycopg.errors.CheckViolation, match="withdrew this application"):
        place.signed_offer(conn)


def test_no_offer_of_a_withdrawn_application_becomes_a_lease(conn, place):
    record_decision(conn, place.pmc_id, place.application, place.staff, "approved")
    offer = place.signed_offer(conn)
    place.withdraw(conn)

    with pytest.raises(psycopg.errors.CheckViolation, match="it is made no lease"):
        complete_offer(conn, place.pmc_id, offer, place.staff)
    assert conn.execute(
        "SELECT count(*) FROM trust_leases WHERE unit_id = %s", (place.unit,)
    ).fetchone() == (0,)


def test_a_renewal_offer_is_not_held_to_an_application(conn, place):
    # A renewal names no application, so withdrawals have nothing to say about it.
    record_decision(conn, place.pmc_id, place.application, place.staff, "approved")
    lease = complete_offer(conn, place.pmc_id, place.signed_offer(conn), place.staff)

    renewal = offer_lease(
        conn, place.pmc_id, place.unit, place.staff, date(2027, 7, 1), renews_lease_id=lease
    )

    assert conn.execute(
        "SELECT renews_lease_id FROM trust_lease_offers WHERE id = %s", (renewal,)
    ).fetchone() == (lease,)


@pytest.mark.timing
def test_a_withdrawal_waits_for_a_lease_being_made_and_then_is_refused(conn, database_url, place):
    record_decision(conn, place.pmc_id, place.application, place.staff, "approved")
    offer = place.signed_offer(conn)
    with psycopg.connect(database_url) as first:
        complete_offer(first, place.pmc_id, offer, place.staff)  # not committed yet
        thread, pid, outcome = in_background(database_url, place.withdraw)
        wait_until_blocked(conn, pid)
        first.commit()
    thread.join(timeout=30)

    assert isinstance(outcome.get("error"), psycopg.errors.CheckViolation), outcome
    assert "is a lease already" in str(outcome["error"])


@pytest.mark.timing
def test_a_lease_being_made_waits_for_a_withdrawal_and_then_is_refused(conn, database_url, place):
    record_decision(conn, place.pmc_id, place.application, place.staff, "approved")
    offer = place.signed_offer(conn)
    with psycopg.connect(database_url) as first:
        place.withdraw(first)  # not committed yet
        thread, pid, outcome = in_background(
            database_url, lambda w: complete_offer(w, place.pmc_id, offer, place.staff)
        )
        wait_until_blocked(conn, pid)
        first.commit()
    thread.join(timeout=30)

    assert isinstance(outcome.get("error"), psycopg.errors.CheckViolation), outcome
    assert "it is made no lease" in str(outcome["error"])


def test_a_withdrawal_stays_in_its_pmc(conn, place):
    other = make_pmc(conn)

    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        conn.execute(
            "INSERT INTO trust_application_withdrawals (pmc_id, application_id) VALUES (%s, %s)",
            (other.pmc_id, place.application),
        )


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE trust_application_withdrawals SET reason = 'Changed'",
        "DELETE FROM trust_application_withdrawals",
    ],
)
def test_a_withdrawal_is_never_rewritten(conn, place, statement):
    place.withdraw(conn)

    with pytest.raises(psycopg.errors.RestrictViolation, match="append-only"):
        conn.execute(statement)
