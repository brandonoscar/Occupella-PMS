"""Draft lease offers (migration 20261016000071): an offer's security deposit and monthly charges,
written while it is a draft and made the lease's recurring charges when it becomes the lease;
and voids, before a lease is made, after which the offer is never sent, signed or made a lease.
The API's side is tested in tests/api/test_offers.py."""

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
    send_offer,
    sign_offer,
    submit_application,
    wait_until_blocked,
)

JAN_1 = date(2027, 1, 1)


class Place:
    """Unit 1 with an approved application and a draft offer for it, signed by its one signer
    once sent."""

    def __init__(self, conn):
        self.pmc = make_pmc(conn)
        self.pmc_id = self.pmc.pmc_id
        self.unit = add_unit(conn, self.pmc_id, self.pmc.owners[0].property_id)
        self.staff = add_staff(conn, self.pmc_id)
        link = open_application_link(conn, self.pmc_id, self.unit, self.staff)
        self.application = submit_application(
            conn, self.pmc_id, link, applicants=[("Applicant A", "a@example.com")]
        )
        record_decision(conn, self.pmc_id, self.application, self.staff, "approved")
        self.offer = offer_lease(
            conn,
            self.pmc_id,
            self.unit,
            self.staff,
            JAN_1,
            application_id=self.application,
            signers=(("Applicant A", "a@example.com"),),
        )

    def charge(self, conn, position=1, kind="fee", amount="35.00", memo="Pet rent", offer=None):
        conn.execute(
            "INSERT INTO trust_lease_offer_charges (pmc_id, offer_id, position, kind, amount,"
            " memo) VALUES (%s, %s, %s, %s, %s, %s)",
            (self.pmc_id, offer or self.offer, position, kind, amount, memo),
        )

    def void(self, conn, reason=None):
        conn.execute(
            "INSERT INTO trust_lease_offer_voids (pmc_id, offer_id, reason) VALUES (%s, %s, %s)",
            (self.pmc_id, self.offer, reason),
        )

    def send_and_sign(self, conn):
        send_offer(conn, self.pmc_id, self.offer, self.staff)
        sign_offer(conn, self.pmc_id, self.offer, 1)


@pytest.fixture
def place(conn):
    return Place(conn)


def test_the_lease_an_offer_becomes_owes_its_monthly_charges_from_its_first_day(conn, place):
    place.charge(conn, 1, "fee", "35.00", "Pet rent")
    place.charge(conn, 2, "credit", "10.00", "Autopay")
    place.send_and_sign(conn)

    lease = complete_offer(conn, place.pmc_id, place.offer, place.staff)

    assert conn.execute(
        "SELECT kind, amount, memo, starts_on FROM trust_recurring_charges WHERE lease_id = %s"
        " ORDER BY kind DESC",
        (lease,),
    ).fetchall() == [("fee", 35, "Pet rent", JAN_1), ("credit", 10, "Autopay", JAN_1)]


def test_an_offer_without_charges_makes_a_lease_with_none(conn, place):
    place.send_and_sign(conn)

    lease = complete_offer(conn, place.pmc_id, place.offer, place.staff)

    assert conn.execute(
        "SELECT count(*) FROM trust_recurring_charges WHERE lease_id = %s", (lease,)
    ).fetchone() == (0,)


def test_charges_are_written_only_while_the_offer_is_a_draft(conn, place):
    place.charge(conn, 1)
    send_offer(conn, place.pmc_id, place.offer, place.staff)

    with pytest.raises(psycopg.errors.CheckViolation, match="set before it is sent"):
        place.charge(conn, 2)


@pytest.mark.parametrize(
    ("position", "kind", "amount", "memo"),
    [
        (0, "fee", "5.00", "Parking"),
        (11, "fee", "5.00", "Parking"),
        (1, "rent", "5.00", "Parking"),
        (1, "fee", "0", "Parking"),
        (1, "fee", "5.001", "Parking"),
        (1, "fee", "5.00", "   "),
        (1, "fee", "5.00", "x" * 201),
    ],
)
def test_a_charge_is_a_fee_or_credit_of_whole_cents_with_a_memo(
    conn, place, position, kind, amount, memo
):
    with pytest.raises(psycopg.errors.CheckViolation):
        place.charge(conn, position, kind, amount, memo)


def test_a_charge_has_its_own_place_on_the_offer(conn, place):
    place.charge(conn, 1)

    with pytest.raises(psycopg.errors.UniqueViolation):
        place.charge(conn, 1, memo="Parking")


@pytest.mark.parametrize("deposit", ["0", "-1.00", "10.001"])
def test_a_security_deposit_asked_is_whole_cents_above_zero(conn, place, deposit):
    with pytest.raises(psycopg.errors.CheckViolation):
        conn.execute(
            "INSERT INTO trust_lease_offers (pmc_id, application_id, unit_id, starts_on,"
            " monthly_rent, security_deposit, document_title, document, offered_by)"
            " SELECT pmc_id, application_id, unit_id, starts_on, monthly_rent, %s,"
            " document_title, document, offered_by FROM trust_lease_offers WHERE id = %s",
            (deposit, place.offer),
        )


def test_the_app_voids_an_offer_naming_who_and_why(conn, app_conn, place):
    with acting_as(app_conn, place.staff):
        place.void(app_conn, "Terms changed")

    assert conn.execute(
        "SELECT reason, recorded_by FROM trust_lease_offer_voids WHERE offer_id = %s",
        (place.offer,),
    ).fetchone() == ("Terms changed", place.staff)


def test_an_offer_is_voided_once_and_a_reason_says_something(conn, place):
    with pytest.raises(psycopg.errors.CheckViolation):
        place.void(conn, "   ")
    place.void(conn)

    with pytest.raises(psycopg.errors.UniqueViolation):
        place.void(conn)


def test_a_void_offer_takes_no_signature(conn, place):
    send_offer(conn, place.pmc_id, place.offer, place.staff)
    place.void(conn)

    with pytest.raises(psycopg.errors.CheckViolation, match="was voided"):
        sign_offer(conn, place.pmc_id, place.offer, 1)


def test_a_signed_offer_voided_is_not_made_a_lease(conn, place):
    place.send_and_sign(conn)
    place.void(conn)

    with pytest.raises(psycopg.errors.CheckViolation, match="was voided"):
        complete_offer(conn, place.pmc_id, place.offer, place.staff)


def test_a_void_draft_is_never_sent(conn, place):
    place.void(conn)

    with pytest.raises(psycopg.errors.CheckViolation, match="was voided"):
        send_offer(conn, place.pmc_id, place.offer, place.staff)


def test_an_offer_that_is_a_lease_is_not_voided(conn, place):
    place.send_and_sign(conn)
    complete_offer(conn, place.pmc_id, place.offer, place.staff)

    with pytest.raises(psycopg.errors.CheckViolation, match="is a lease already"):
        place.void(conn)


def test_another_offer_for_the_application_becomes_the_lease_after_a_void(conn, place):
    place.send_and_sign(conn)
    place.void(conn)
    again = offer_lease(
        conn,
        place.pmc_id,
        place.unit,
        place.staff,
        JAN_1,
        application_id=place.application,
        signers=(("Applicant A", "a@example.com"),),
    )
    send_offer(conn, place.pmc_id, again, place.staff)
    sign_offer(conn, place.pmc_id, again, 1)

    assert complete_offer(conn, place.pmc_id, again, place.staff) is not None


@pytest.mark.timing
def test_a_void_waits_for_a_lease_being_made_and_then_is_refused(conn, database_url, place):
    place.send_and_sign(conn)
    with psycopg.connect(database_url) as first:
        complete_offer(first, place.pmc_id, place.offer, place.staff)  # not committed yet
        thread, pid, outcome = in_background(database_url, place.void)
        wait_until_blocked(conn, pid)
        first.commit()
    thread.join(timeout=30)

    assert isinstance(outcome.get("error"), psycopg.errors.CheckViolation), outcome
    assert "is a lease already" in str(outcome["error"])


@pytest.mark.timing
def test_a_lease_being_made_waits_for_a_void_and_then_is_refused(conn, database_url, place):
    place.send_and_sign(conn)
    with psycopg.connect(database_url) as first:
        place.void(first)  # not committed yet
        thread, pid, outcome = in_background(
            database_url, lambda w: complete_offer(w, place.pmc_id, place.offer, place.staff)
        )
        wait_until_blocked(conn, pid)
        first.commit()
    thread.join(timeout=30)

    assert isinstance(outcome.get("error"), psycopg.errors.CheckViolation), outcome
    assert "was voided" in str(outcome["error"])
    assert conn.execute(
        "SELECT count(*) FROM trust_leases WHERE unit_id = %s", (place.unit,)
    ).fetchone() == (0,)


def test_charges_and_voids_stay_in_their_pmc(conn, place):
    other = make_pmc(conn)

    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        conn.execute(
            "INSERT INTO trust_lease_offer_charges (pmc_id, offer_id, position, kind, amount,"
            " memo) VALUES (%s, %s, 1, 'fee', 5, 'Parking')",
            (other.pmc_id, place.offer),
        )
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        conn.execute(
            "INSERT INTO trust_lease_offer_voids (pmc_id, offer_id) VALUES (%s, %s)",
            (other.pmc_id, place.offer),
        )


@pytest.mark.parametrize("table", ["trust_lease_offer_charges", "trust_lease_offer_voids"])
@pytest.mark.parametrize("statement", ["UPDATE {} SET pmc_id = pmc_id", "DELETE FROM {}"])
def test_charges_and_voids_are_never_rewritten(conn, place, table, statement):
    place.charge(conn)
    place.void(conn)

    with pytest.raises(psycopg.errors.RestrictViolation, match="append-only"):
        conn.execute(statement.format(table))
