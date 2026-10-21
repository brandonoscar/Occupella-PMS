"""E-signing on the PMS's own page (migration 20261020000097, ADR 0008): a signer's links, what
they do through one (agree to sign electronically, open the lease, ask for paper) and their
signature with its evidence; and src/pms/leasing/esign.py and certificate.py. The API's side is
tested in tests/api/test_signing.py."""

import hashlib
import io
from datetime import UTC, date, datetime, timedelta

import psycopg
import pytest
from helpers import (
    LEASE_PDF,
    add_staff,
    add_unit,
    complete_offer,
    in_background,
    make_pmc,
    offer_lease,
    open_application_link,
    png,
    record_decision,
    send_offer,
    sign_offer,
    submit_application,
    wait_until_blocked,
)
from pypdf import PdfReader

from pms.leasing import certificate, esign

JAN_1 = date(2027, 1, 1)
SHA = hashlib.sha256(LEASE_PDF).digest()
DISCLOSURE = "The disclosures, as the signer read them."


class Place:
    """An approved application for Unit 1 and an offer for it to two applicants, sent to sign
    on the PMS's page."""

    def __init__(self, conn):
        self.pmc = make_pmc(conn)
        self.pmc_id = self.pmc.pmc_id
        self.unit = add_unit(conn, self.pmc_id, self.pmc.owners[0].property_id)
        self.staff = add_staff(conn, self.pmc_id)
        link = open_application_link(conn, self.pmc_id, self.unit, self.staff)
        self.application = submit_application(
            conn,
            self.pmc_id,
            link,
            applicants=[("Applicant A", "a@example.com"), ("Applicant B", "b@example.com")],
        )
        record_decision(conn, self.pmc_id, self.application, self.staff, "approved")
        self.offer = self.new_offer(conn)
        self.send(conn)

    def new_offer(self, conn):
        return offer_lease(
            conn,
            self.pmc_id,
            self.unit,
            self.staff,
            JAN_1,
            application_id=self.application,
            signers=(("Applicant A", "a@example.com"), ("Applicant B", "b@example.com")),
        )

    def send(self, conn, offer=None, partner=esign.PARTNER):
        offer = offer or self.offer
        conn.execute(
            "INSERT INTO trust_signature_requests (pmc_id, offer_id, partner, partner_reference,"
            " sent_by) VALUES (%s, %s, %s, %s, %s)",
            (self.pmc_id, offer, partner, esign.reference(offer), self.staff),
        )

    def link(self, conn, position=1, offer=None, made=None):
        token = f"token-{position}-{datetime.now(UTC).timestamp()}-{id(object())}"
        return conn.execute(
            "INSERT INTO trust_signing_links (pmc_id, offer_id, position, token_sha256,"
            " created_at) VALUES (%s, %s, %s, %s, coalesce(%s, clock_timestamp())) RETURNING id",
            (self.pmc_id, offer or self.offer, position, esign.token_digest(token), made),
        ).fetchone()[0]

    def event(self, conn, link, kind):
        conn.execute(
            "INSERT INTO trust_signing_events (pmc_id, link_id, kind, disclosure, ip, user_agent)"
            " VALUES (%s, %s, %s, %s, '192.0.2.10', 'A browser')",
            (self.pmc_id, link, kind, DISCLOSURE if kind == "consented" else None),
        )

    def ready(self, conn, position=1):
        """A link of the signer at `position` through which they agreed and opened the lease."""
        link = self.link(conn, position)
        self.event(conn, link, "consented")
        self.event(conn, link, "opened")
        return link

    def sign(self, conn, link, position=1, name="Applicant A", sha=SHA, drawing=None, offer=None):
        conn.execute(
            "INSERT INTO trust_signature_evidence (pmc_id, offer_id, position, link_id,"
            " typed_name, drawing, document_sha256, ip, user_agent)"
            " VALUES (%s, %s, %s, %s, %s, %s, %s, '192.0.2.10', 'A browser')",
            (self.pmc_id, offer or self.offer, position, link, name, drawing, sha),
        )

    def void(self, conn, offer=None):
        conn.execute(
            "INSERT INTO trust_lease_offer_voids (pmc_id, offer_id) VALUES (%s, %s)",
            (self.pmc_id, offer or self.offer),
        )


@pytest.fixture
def place(conn):
    return Place(conn)


def is_open(conn, link):
    return conn.execute("SELECT trust_signing_link_open(%s)", (link,)).fetchone()[0]


def refused(conn, message, work):
    with pytest.raises(psycopg.errors.CheckViolation, match=message), conn.transaction():
        work()


# --- signing ------------------------------------------------------------------------------------


def test_each_signer_signs_through_their_link_and_the_offer_becomes_the_lease(conn, place):
    first = place.ready(conn, 1)
    second = place.ready(conn, 2)

    place.sign(conn, first, 1, "  applicant a ", drawing=png())
    place.sign(conn, second, 2, "Applicant B")
    lease = complete_offer(conn, place.pmc_id, place.offer, place.staff)

    signed = conn.execute(
        "SELECT g.position, g.signed_at = e.signed_at FROM trust_signatures AS g"
        " JOIN trust_signature_evidence AS e USING (offer_id, position)"
        " WHERE g.offer_id = %s ORDER BY g.position",
        (place.offer,),
    ).fetchall()
    assert signed == [(1, True), (2, True)]
    assert lease is not None
    assert not is_open(conn, first)


def test_a_link_is_made_only_for_a_signer_of_an_offer_sent_here_still_to_sign(conn, place):
    draft = place.new_offer(conn)
    elsewhere = place.new_offer(conn)
    place.send(conn, elsewhere, partner="Another partner")
    voided = place.new_offer(conn)
    place.send(conn, voided)
    place.void(conn, voided)

    refused(conn, "not sent to sign here", lambda: place.link(conn, offer=draft))
    refused(conn, "not sent to sign here", lambda: place.link(conn, offer=elsewhere))
    refused(conn, "was voided", lambda: place.link(conn, offer=voided))
    with pytest.raises(psycopg.errors.ForeignKeyViolation), conn.transaction():
        place.link(conn, 3)
    place.sign(conn, place.ready(conn, 1), 1)
    refused(conn, "has signed already", lambda: place.link(conn, 1))
    place.sign(conn, place.ready(conn, 2), 2, "Applicant B")
    complete_offer(conn, place.pmc_id, place.offer, place.staff)
    refused(conn, "is a lease already", lambda: place.link(conn, 2))


def test_a_signer_agrees_before_opening_and_asking_for_paper_ends_the_link(conn, place):
    link = place.link(conn)
    other = place.link(conn)

    refused(conn, "agrees to sign electronically before", lambda: place.event(conn, link, "opened"))
    place.event(conn, link, "consented")
    place.event(conn, link, "paper")  # withdrawn after agreeing
    refused(conn, "asked to sign on paper", lambda: place.event(conn, link, "opened"))
    refused(conn, "asked to sign on paper", lambda: place.sign(conn, link))
    with pytest.raises(psycopg.errors.UniqueViolation), conn.transaction():
        place.event(conn, link, "paper")
    # The signer's other link still signs.
    place.event(conn, other, "consented")
    place.event(conn, other, "opened")
    place.sign(conn, other)


def test_a_consent_keeps_the_words_agreed_to_and_nothing_else_does(conn, place):
    link = place.link(conn)

    for kind, disclosure in (("consented", None), ("paper", DISCLOSURE)):
        with pytest.raises(psycopg.errors.CheckViolation), conn.transaction():
            conn.execute(
                "INSERT INTO trust_signing_events (pmc_id, link_id, kind, disclosure)"
                " VALUES (%s, %s, %s, %s)",
                (place.pmc_id, link, kind, disclosure),
            )


def test_a_link_signs_nothing_once_it_ran_out_or_its_offer_was_voided(conn, place):
    old = place.link(conn, made=datetime.now(UTC) - timedelta(days=30, minutes=1))
    fresh = place.link(conn, made=datetime.now(UTC) - timedelta(days=29, hours=23))
    ready = place.ready(conn, 2)

    assert (is_open(conn, old), is_open(conn, fresh)) == (False, True)
    refused(conn, "no longer signs", lambda: place.event(conn, old, "consented"))
    place.void(conn)
    assert not is_open(conn, fresh)
    refused(conn, "no longer signs", lambda: place.event(conn, fresh, "consented"))
    refused(conn, "no longer signs", lambda: place.sign(conn, ready, 2, "Applicant B"))


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"name": "Someone Else"}, "type your name as it is on the lease"),
        ({"sha": hashlib.sha256(b"another lease").digest()}, "not the lease sent to sign"),
        ({"position": 2, "name": "Applicant B"}, "another signer's"),
    ],
)
def test_a_signature_is_the_signers_own_name_on_the_lease_sent(conn, place, change, message):
    link = place.ready(conn, 1)

    refused(conn, message, lambda: place.sign(conn, link, **change))
    assert conn.execute(
        "SELECT count(*) FROM trust_signatures WHERE offer_id = %s", (place.offer,)
    ).fetchone() == (0,)


def test_a_signature_comes_after_agreeing_and_opening_through_that_link(conn, place):
    agreed = place.link(conn)
    place.event(conn, agreed, "consented")
    untouched = place.link(conn)

    refused(conn, "opens the lease first", lambda: place.sign(conn, agreed))
    refused(conn, "opens the lease first", lambda: place.sign(conn, untouched))


def test_a_drawing_is_a_png_of_at_most_100_kb(conn, place):
    link = place.ready(conn)

    for drawing in (b"GIF89a not a png", b"\x89PNG\r\n\x1a\n" + b"x" * 100_000):
        with pytest.raises(psycopg.errors.CheckViolation), conn.transaction():
            place.sign(conn, link, drawing=drawing)
    place.sign(conn, link, drawing=png())


def test_an_offer_sent_here_is_signed_only_with_its_evidence(conn, place):
    elsewhere = place.new_offer(conn)
    send_offer(conn, place.pmc_id, elsewhere, place.staff)  # through another partner

    refused(
        conn,
        "made through the signer's link",
        lambda: sign_offer(conn, place.pmc_id, place.offer, 1),
    )
    sign_offer(conn, place.pmc_id, elsewhere, 1)  # another partner's word, as before


def test_a_signer_signs_once(conn, place):
    first = place.ready(conn)
    place.sign(conn, first)

    refused(conn, "has signed already", lambda: place.link(conn))
    refused(conn, "no longer signs", lambda: place.event(conn, first, "paper"))


def test_the_times_are_the_databases_own(conn, place):
    link = place.link(conn)
    long_ago = datetime(2020, 1, 1, tzinfo=UTC)
    conn.execute(
        "INSERT INTO trust_signing_events (pmc_id, link_id, kind, disclosure, created_at)"
        " VALUES (%s, %s, 'consented', 'Read', %s)",
        (place.pmc_id, link, long_ago),
    )
    place.event(conn, link, "opened")
    conn.execute(
        "INSERT INTO trust_signature_evidence (pmc_id, offer_id, position, link_id, typed_name,"
        " document_sha256, signed_at) VALUES (%s, %s, 1, %s, 'Applicant A', %s, %s)",
        (place.pmc_id, place.offer, link, SHA, long_ago),
    )

    times = conn.execute(
        "SELECT (SELECT created_at FROM trust_signing_events"
        "        WHERE link_id = %(link)s AND kind = 'consented'),"
        " (SELECT signed_at FROM trust_signature_evidence WHERE link_id = %(link)s)",
        {"link": link},
    ).fetchone()
    assert all(t > long_ago + timedelta(days=365) for t in times)


def test_links_events_and_evidence_are_records(conn, place):
    link = place.ready(conn)
    place.sign(conn, link)

    for table in ("trust_signing_links", "trust_signing_events", "trust_signature_evidence"):
        with pytest.raises(psycopg.errors.RestrictViolation), conn.transaction():
            conn.execute(f"DELETE FROM {table}")  # noqa: S608
        with pytest.raises(psycopg.errors.RestrictViolation), conn.transaction():
            conn.execute(f"UPDATE {table} SET pmc_id = pmc_id")  # noqa: S608
    assert conn.execute(
        "SELECT array_agg(DISTINCT table_name ORDER BY table_name) FROM trust_audit_log"
        " WHERE table_name IN ('trust_signing_links', 'trust_signing_events',"
        " 'trust_signature_evidence', 'trust_signatures')"
    ).fetchone()[0] == [
        "trust_signature_evidence",
        "trust_signatures",
        "trust_signing_events",
        "trust_signing_links",
    ]


def test_links_stay_in_their_pmc(conn, place):
    other = make_pmc(conn)

    with pytest.raises(psycopg.errors.ForeignKeyViolation), conn.transaction():
        conn.execute(
            "INSERT INTO trust_signing_links (pmc_id, offer_id, position, token_sha256)"
            " VALUES (%s, %s, 1, %s)",
            (other.pmc_id, place.offer, b"x" * 32),
        )


@pytest.mark.timing
def test_a_signature_waits_for_a_void_and_then_is_refused(conn, database_url, place):
    link = place.ready(conn)
    with psycopg.connect(database_url) as first:
        place.void(first)  # not committed yet
        thread, pid, outcome = in_background(database_url, lambda w: place.sign(w, link))
        wait_until_blocked(conn, pid)
        first.commit()
    thread.join(timeout=30)

    assert isinstance(outcome.get("error"), psycopg.errors.CheckViolation), outcome
    assert "no longer signs" in str(outcome["error"])
    assert conn.execute(
        "SELECT count(*) FROM trust_signatures WHERE offer_id = %s", (place.offer,)
    ).fetchone() == (0,)


# --- the module ---------------------------------------------------------------------------------


def test_a_link_is_kept_only_as_its_digest(conn, place):
    url = esign.new_link(conn, place.pmc_id, place.offer, 1, "http://pms.example/")
    token = url.split("#", 1)[1]

    assert url.startswith("http://pms.example/sign#") and len(token) >= 40
    assert conn.execute(
        "SELECT position FROM trust_signing_links WHERE token_sha256 = %s",
        (hashlib.sha256(token.encode()).digest(),),
    ).fetchone() == (1,)
    assert esign.reference(place.offer) == f"pms-esign-{place.offer}"


def test_the_disclosures_say_what_esign_asks_and_how_to_reach_the_company():
    both = esign.disclosure("Demo Co", "1 Example Way, Springfield, ZZ 00000", "office@example.com")
    neither = esign.disclosure("Demo Co", None, None)

    for words in (
        "on paper instead, at no charge",
        "covers this lease only",
        "withdraw your agreement at any time before you sign",
        "a paper copy of the signed lease, at no charge",
        "a current web browser that opens PDF files",
        "you can read this page and open PDF files",
        "If your email address changes, tell Demo Co.",
    ):
        assert words in both and words in neither
    assert "write to Demo Co at office@example.com or at 1 Example Way" in both
    assert "contact Demo Co" in neither and "write to" not in neither
    assert esign.disclosure_digest(both) == hashlib.sha256(both.encode()).hexdigest()


# --- the certificate ----------------------------------------------------------------------------


def test_a_drawing_fits_only_as_a_png_of_a_signatures_size():
    assert certificate.drawing_fits(png(400, 150))
    assert not certificate.drawing_fits(png(2001, 10))
    assert not certificate.drawing_fits(png(10, 1001))
    assert not certificate.drawing_fits(b"\x89PNG\r\n\x1a\n" + b"\x00" * 4 + b"IDAT" + b"\x00" * 8)
    assert not certificate.drawing_fits(b"GIF89a")


def test_the_certificate_says_what_each_signer_did(conn):
    at = datetime(2026, 10, 20, 15, 4, 5, tzinfo=UTC)
    step = certificate.Step(at, "192.0.2.10", "A browser")
    signers = (
        certificate.Signer(
            name="Applicant Ä",
            email="a@example.com",
            role="tenant",
            first_link_at=at,
            consented=step,
            opened=step,
            paper=None,
            signed=step,
            typed_name="Applicant Ä",
            drawing=png(),
            disclosure=DISCLOSURE,
        ),
        certificate.Signer(
            name="Applicant B",
            email="b@example.com",
            role="guarantor",
            first_link_at=None,
            consented=None,
            opened=None,
            paper=certificate.Step(at, None, None),
            signed=None,
            typed_name=None,
            drawing=b"\x89PNG\r\n\x1a\n not one fpdf2 reads",
            disclosure=DISCLOSURE,
        ),
    )
    made = certificate.render(
        certificate.Certificate(
            company="Demo Co",
            document_title="Residential lease",
            home="Property 1, Unit 1",
            terms="01/01/2027 - Month to month, $1,500.00 a month",
            document_sha256=SHA.hex(),
            sent_at=at,
            sent_by="Staff 1",
            status="Signed by 1 of 2",
            signers=signers,
        ),
        at,
    )
    text = " ".join(page.extract_text() for page in PdfReader(io.BytesIO(made)).pages)
    text = " ".join(text.split())

    for words in (
        "Signing certificate",
        SHA.hex(),
        "Signer 1: Applicant Ä",
        "10/20/2026 15:04:05 UTC (from 192.0.2.10, browser: A browser)",
        "Signer 2: Applicant B",
        "Asked to sign on paper",
        "Not yet",
        "can't be shown here",
        "Disclosures, text 1",
        DISCLOSURE,
    ):
        assert words in text, words
    assert text.count("Disclosures, text") == 1
