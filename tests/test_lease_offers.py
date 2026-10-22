"""Lease offers, their signatures and completing them into leases (migration 20261011000031).
The API's side is tested in tests/api/test_offers.py."""

from datetime import UTC, date, datetime, timedelta

import psycopg
import pytest
from helpers import (
    LEASE_PDF,
    add_staff,
    add_tenant,
    add_unit,
    complete_offer,
    end_lease,
    in_background,
    make_pmc,
    offer_lease,
    open_application_link,
    open_lease,
    record_decision,
    return_deposit,
    send_offer,
    sign_offer,
    submit_application,
    transfer_batch,
    wait_until_blocked,
)

from pms.leasing import esign

TODAY = datetime.now(UTC).date()
JAN_1, JUN_30, JUL_1 = (
    date(2027, 1, 1),
    date(2027, 6, 30),
    date(2027, 7, 1),
)
TABLES = [
    "trust_lease_offers",
    "trust_lease_offer_signers",
    "trust_signature_requests",
    "trust_signatures",
    "trust_lease_offer_completions",
]


class Place:
    """Unit 1 with an approved application from two applicants; Unit 2 leased to Tenant 1."""

    def __init__(self, conn):
        self.pmc = make_pmc(conn)
        pmc_id, prop = self.pmc.pmc_id, self.pmc.owners[0].property_id
        self.pmc_id = pmc_id
        self.unit = add_unit(conn, pmc_id, prop, "Unit 1")
        self.leased_unit = add_unit(conn, pmc_id, prop, "Unit 2")
        self.staff = add_staff(conn, pmc_id)
        link = open_application_link(conn, pmc_id, self.unit, self.staff)
        self.application = submit_application(
            conn,
            pmc_id,
            link,
            applicants=[("Applicant A", "a@example.com"), ("Applicant B", "b@example.com")],
        )
        record_decision(conn, pmc_id, self.application, self.staff, "approved")
        self.tenant = add_tenant(conn, pmc_id, prop, "Tenant 1")
        self.lease = open_lease(
            conn, pmc_id, self.leased_unit, date(2026, 1, 1), None, "1200", [self.tenant]
        )

    def new_lease_offer(self, conn, **changes):
        terms = {
            "starts_on": JAN_1,
            "ends_on": JUN_30,
            "application_id": self.application,
            "signers": (("Applicant A", "a@example.com"), ("Applicant B", "b@example.com")),
        } | changes
        return offer_lease(conn, self.pmc_id, self.unit, self.staff, **terms)

    def renewal(self, conn, starts_on=JUL_1, **changes):
        terms = {"renews_lease_id": self.lease, "rent": "1250.00"} | changes
        return offer_lease(conn, self.pmc_id, self.leased_unit, self.staff, starts_on, **terms)

    def sign_all(self, conn, offer, signers=2):
        send_offer(conn, self.pmc_id, offer, self.staff)
        for position in range(1, signers + 1):
            sign_offer(conn, self.pmc_id, offer, position)


@pytest.fixture
def place(conn):
    return Place(conn)


def leases_of_unit(conn, unit):
    return conn.execute(
        "SELECT l.starts_on, l.ends_on, l.monthly_rent,"
        " array(SELECT t.display_name FROM trust_lease_tenants AS lt"
        "       JOIN trust_tenants AS t ON t.id = lt.tenant_id"
        "       WHERE lt.lease_id = l.id ORDER BY t.display_name)"
        " FROM trust_leases AS l WHERE l.unit_id = %s ORDER BY l.starts_on",
        (unit,),
    ).fetchall()


def test_a_signed_offer_for_an_application_opens_a_lease_for_its_signers(conn, app_conn, place):
    offer = place.new_lease_offer(app_conn)
    place.sign_all(app_conn, offer)

    lease = complete_offer(app_conn, place.pmc_id, offer, place.staff)

    assert leases_of_unit(conn, place.unit) == [
        (JAN_1, JUN_30, 1500, ["Applicant A", "Applicant B"])
    ]
    assert conn.execute(
        "SELECT lease_id FROM trust_lease_offer_completions WHERE offer_id = %s", (offer,)
    ).fetchone() == (lease,)


def test_completing_again_returns_the_same_lease(conn, place):
    offer = place.new_lease_offer(conn)
    place.sign_all(conn, offer)

    first = complete_offer(conn, place.pmc_id, offer, place.staff)

    assert complete_offer(conn, place.pmc_id, offer, place.staff) == first
    assert len(leases_of_unit(conn, place.unit)) == 1


def test_a_renewal_ends_the_lease_the_day_before_and_keeps_its_tenants(conn, place):
    offer = place.renewal(conn, signers=(("Tenant 1", "t1@example.com"),))
    place.sign_all(conn, offer, signers=1)

    complete_offer(conn, place.pmc_id, offer, place.staff)

    assert leases_of_unit(conn, place.leased_unit) == [
        (date(2026, 1, 1), JUN_30, 1200, ["Tenant 1"]),
        (JUL_1, None, 1250, ["Tenant 1"]),
    ]


# A lease ending a month before its renewal starts is no longer renewed (migration 0152): see
# test_a_renewal_of_a_lease_that_ends_before_it_takes_nothing_more.
@pytest.mark.parametrize(
    ("ends_on", "then_ends_on"),
    [
        (date(2026, 12, 31), date(2026, 12, 31)),
        (JAN_1, date(2026, 12, 31)),
        (JUN_30, date(2026, 12, 31)),
    ],
    ids=["ends the day before", "ends on the start day", "runs on"],
)
def test_a_renewal_ends_a_lease_only_if_it_runs_into_the_renewal(
    conn, place, ends_on, then_ends_on
):
    conn.execute("SELECT trust_end_lease(%s, %s, %s)", (place.pmc_id, place.lease, ends_on))
    offer = place.renewal(conn, starts_on=JAN_1, signers=(("Tenant 1", "t1@example.com"),))
    place.sign_all(conn, offer, signers=1)

    complete_offer(conn, place.pmc_id, offer, place.staff)

    assert [row[:2] for row in leases_of_unit(conn, place.leased_unit)] == [
        (date(2026, 1, 1), then_ends_on),
        (JAN_1, None),
    ]


CLOSED = "this renewal can no longer be sent, signed or made a lease"


def closed(conn, offer):
    return conn.execute("SELECT trust_renewal_closed(%s)", (offer,)).fetchone()[0]


def signing_link(conn, place, offer, position=1):
    """A signer's link of an offer sent to sign on the PMS's own page."""
    return conn.execute(
        "INSERT INTO trust_signing_links (pmc_id, offer_id, position, token_sha256)"
        " VALUES (%s, %s, %s, %s) RETURNING id",
        (place.pmc_id, offer, position, esign.token_digest(f"token-{offer}-{position}-x")),
    ).fetchone()[0]


def send_here(conn, place, offer):
    conn.execute(
        "INSERT INTO trust_signature_requests (pmc_id, offer_id, partner, partner_reference,"
        " sent_by) VALUES (%s, %s, %s, %s, %s)",
        (place.pmc_id, offer, esign.PARTNER, esign.reference(offer), place.staff),
    )


def test_a_renewal_of_a_lease_ended_before_it_is_never_sent_signed_or_made_a_lease(
    conn, app_conn, place
):
    """H7: staff ended a lease early while renewals of it waited: a draft, one sent to sign here
    with a signer's link, one sent elsewhere and signed by one of two, and one signed by all.
    None is sent, takes a link, a step through one or a signature, or becomes a lease after;
    the lease stays ended."""
    ended = TODAY - timedelta(days=7)
    starts = ended + timedelta(days=30)
    two = (("Tenant 1", "t1@example.com"), ("Tenant 2", "t2@example.com"))
    draft, here, elsewhere, signed = (
        place.renewal(conn, starts_on=starts, signers=two[:people]) for people in (2, 2, 2, 1)
    )
    send_here(conn, place, here)
    link = signing_link(conn, place, here, 1)
    send_offer(conn, place.pmc_id, elsewhere, place.staff)
    sign_offer(conn, place.pmc_id, elsewhere, 1)
    place.sign_all(conn, signed, signers=1)
    renewals = (draft, here, elsewhere, signed)
    assert [closed(app_conn, o) for o in renewals] == [None] * 4
    assert conn.execute("SELECT trust_signing_link_open(%s)", (link,)).fetchone() == (True,)

    end_lease(app_conn, place.pmc_id, place.lease, ended)

    why = f"the lease it renews ended on {ended:%m/%d/%Y}"
    assert [closed(app_conn, o) for o in renewals] == [why] * 4
    refused = f"{CLOSED}: {why}"
    with pytest.raises(psycopg.errors.CheckViolation, match=refused):
        send_offer(app_conn, place.pmc_id, draft, place.staff)
    with pytest.raises(psycopg.errors.CheckViolation, match=refused):
        signing_link(app_conn, place, here, 2)
    assert conn.execute("SELECT trust_signing_link_open(%s)", (link,)).fetchone() == (False,)
    with pytest.raises(psycopg.errors.CheckViolation, match="no longer signs"):
        app_conn.execute(
            "INSERT INTO trust_signing_events (pmc_id, link_id, kind, disclosure)"
            " VALUES (%s, %s, 'consented', 'The disclosures.')",
            (place.pmc_id, link),
        )
    with pytest.raises(psycopg.errors.CheckViolation, match=refused):
        sign_offer(conn, place.pmc_id, elsewhere, 2)
    with pytest.raises(psycopg.errors.CheckViolation, match=refused):
        complete_offer(app_conn, place.pmc_id, signed, place.staff)

    assert leases_of_unit(conn, place.leased_unit) == [
        (date(2026, 1, 1), ended, 1200, ["Tenant 1"])
    ]
    assert conn.execute(
        "SELECT (SELECT count(*) FROM trust_signature_requests WHERE offer_id = %s),"
        " (SELECT count(*) FROM trust_signing_links WHERE offer_id = %s),"
        " (SELECT count(*) FROM trust_signatures WHERE offer_id = %s),"
        " (SELECT count(*) FROM trust_lease_offer_completions WHERE offer_id = ANY(%s))",
        (draft, here, elsewhere, list(renewals)),
    ).fetchone() == (0, 1, 1, 0)
    # Voiding it is what is left to do.
    conn.execute(
        "INSERT INTO trust_lease_offer_voids (pmc_id, offer_id, reason)"
        " VALUES (%s, %s, 'The lease ended')",
        (place.pmc_id, draft),
    )


def test_a_renewal_of_a_lease_that_ends_more_than_a_day_before_it_takes_nothing_more(conn, place):
    """A fixed-term lease that ends, still to come, more than a day before its renewal starts:
    the renewal is not sent, and it says so."""
    ends = TODAY + timedelta(days=20)
    end_lease(conn, place.pmc_id, place.lease, ends)
    offer = place.renewal(conn, starts_on=ends + timedelta(days=40))

    why = f"the lease it renews ends on {ends:%m/%d/%Y}, not the day before the renewal starts"
    assert closed(conn, offer) == why
    with pytest.raises(psycopg.errors.CheckViolation, match=f"{CLOSED}: {why}"):
        send_offer(conn, place.pmc_id, offer, place.staff)
    # A renewal from the day after it ends is sent, signed and made the lease.
    after = place.renewal(conn, starts_on=ends + timedelta(days=1))
    assert closed(conn, after) is None
    place.sign_all(conn, after, signers=1)
    complete_offer(conn, place.pmc_id, after, place.staff)
    assert closed(conn, after) is None  # a lease now: nothing is left to refuse


def test_a_renewal_of_a_lease_moved_from_or_whose_deposit_was_returned_takes_nothing_more(conn):
    """Its tenants moved to another unit (migration 0091), or its deposit was returned
    (migration 0035): a renewal of the lease starting the day after it ended is refused all
    the same, and says why."""
    pmc = make_pmc(conn)
    staff = add_staff(conn, pmc.pmc_id)
    tenant = conn.execute(
        "SELECT tenant_id FROM trust_ledger_accounts WHERE ledger_account_id = %s",
        (pmc.tenant_deposit,),
    ).fetchone()[0]
    prop = pmc.owners[0].property_id
    unit, other, third = (add_unit(conn, pmc.pmc_id, prop, f"Unit {n}") for n in (1, 2, 3))

    # Tenant 1's lease ended on 06/30 and its renewal from 07/01 was sent; then the deposit went
    # back on 07/05.
    returned = open_lease(
        conn, pmc.pmc_id, third, date(2026, 1, 1), date(2026, 6, 30), "900.00", [tenant]
    )
    transfer_batch(
        conn, [(pmc.deposit_cash, pmc.tenant_deposit, "500.00")], datetime(2026, 1, 2, tzinfo=UTC)
    )
    sent = offer_lease(conn, pmc.pmc_id, third, staff, date(2026, 7, 1), renews_lease_id=returned)
    send_offer(conn, pmc.pmc_id, sent, staff)
    assert closed(conn, sent) is None
    return_deposit(conn, pmc.pmc_id, returned, "move-out-3", datetime(2026, 7, 5, 12, tzinfo=UTC))
    why = "the deposit of the lease it renews was returned on 07/05/2026"
    assert closed(conn, sent) == why
    with pytest.raises(psycopg.errors.CheckViolation, match=f"{CLOSED}: {why}"):
        sign_offer(conn, pmc.pmc_id, sent, 1)

    # Tenant 2's lease, renewed from 04/01 in a draft, moved to another unit that day.
    tenant_2 = add_tenant(conn, pmc.pmc_id, prop, "Tenant 2")
    moving = open_lease(conn, pmc.pmc_id, unit, date(2026, 1, 1), None, "1000.00", [tenant_2])
    renewal = offer_lease(conn, pmc.pmc_id, unit, staff, date(2026, 4, 1), renews_lease_id=moving)
    conn.execute(
        "SELECT trust_transfer_lease(%s, %s, %s, %s, NULL, 1000.00, NULL, NULL)",
        (pmc.pmc_id, moving, other, date(2026, 4, 1)),
    )
    why = "its tenants moved to another unit on 04/01/2026"
    assert closed(conn, renewal) == why
    with pytest.raises(psycopg.errors.CheckViolation, match=f"{CLOSED}: {why}"):
        send_offer(conn, pmc.pmc_id, renewal, staff)


@pytest.mark.timing
def test_ending_a_lease_and_sending_its_renewal_wait_for_each_other(conn, database_url, place):
    """A renewal sent as the lease it renews is ended early waits for the end, then sees it."""
    ended = TODAY - timedelta(days=7)
    offer = place.renewal(conn, starts_on=ended + timedelta(days=30))
    with psycopg.connect(database_url) as ending:
        end_lease(ending, place.pmc_id, place.lease, ended)  # not committed yet
        thread, pid, outcome = in_background(
            database_url, lambda w: send_offer(w, place.pmc_id, offer, place.staff)
        )
        wait_until_blocked(conn, pid)
        ending.commit()
    thread.join(timeout=30)

    assert isinstance(outcome.get("error"), psycopg.errors.CheckViolation), outcome
    assert CLOSED in str(outcome["error"])


@pytest.mark.parametrize("signed", [0, 1])
def test_an_offer_completes_only_once_everyone_has_signed(conn, place, signed):
    offer = place.new_lease_offer(conn)
    send_offer(conn, place.pmc_id, offer, place.staff)
    for position in range(1, signed + 1):
        sign_offer(conn, place.pmc_id, offer, position)

    with pytest.raises(psycopg.errors.CheckViolation, match="not signed by everyone"):
        complete_offer(conn, place.pmc_id, offer, place.staff)
    assert leases_of_unit(conn, place.unit) == []


def test_an_offer_with_no_signers_never_completes(conn, place):
    offer = place.new_lease_offer(conn, signers=())
    send_offer(conn, place.pmc_id, offer, place.staff)

    with pytest.raises(psycopg.errors.CheckViolation, match="not signed by everyone"):
        complete_offer(conn, place.pmc_id, offer, place.staff)


def test_an_application_or_a_lease_takes_one_completed_offer(conn, place):
    first = place.new_lease_offer(conn)
    second = place.new_lease_offer(conn)
    place.sign_all(conn, first)
    place.sign_all(conn, second)
    complete_offer(conn, place.pmc_id, first, place.staff)

    with pytest.raises(psycopg.errors.CheckViolation, match="became its lease already"):
        complete_offer(conn, place.pmc_id, second, place.staff)


@pytest.mark.timing
def test_two_offers_for_one_application_completing_at_once_make_one_lease(
    conn, database_url, place
):
    first, second = place.new_lease_offer(conn), place.new_lease_offer(conn)
    place.sign_all(conn, first)
    place.sign_all(conn, second)
    with psycopg.connect(database_url) as one:
        complete_offer(one, place.pmc_id, first, place.staff)  # not committed yet
        thread, pid, outcome = in_background(
            database_url, lambda w: complete_offer(w, place.pmc_id, second, place.staff)
        )
        wait_until_blocked(conn, pid)
        one.commit()
    thread.join(timeout=30)

    assert isinstance(outcome.get("error"), psycopg.errors.CheckViolation), outcome
    assert len(leases_of_unit(conn, place.unit)) == 1


def test_an_offer_is_for_an_approved_application_at_its_unit(conn, place):
    link = open_application_link(conn, place.pmc_id, place.unit, place.staff)
    undecided = submit_application(conn, place.pmc_id, link)
    declined = submit_application(conn, place.pmc_id, link)
    record_decision(conn, place.pmc_id, declined, place.staff, "declined")
    conditional = submit_application(conn, place.pmc_id, link)
    record_decision(conn, place.pmc_id, conditional, place.staff, "conditional", None, "Deposit")

    for application in (undecided, declined):
        with pytest.raises(psycopg.errors.CheckViolation, match="is not approved"):
            place.new_lease_offer(conn, application_id=application)
    with pytest.raises(psycopg.errors.CheckViolation, match="the offer is for unit"):
        offer_lease(
            conn,
            place.pmc_id,
            place.leased_unit,
            place.staff,
            JAN_1,
            application_id=place.application,
        )
    place.new_lease_offer(conn, application_id=conditional)


def test_a_renewal_is_at_its_leases_unit_and_starts_after_it(conn, place):
    with pytest.raises(psycopg.errors.CheckViolation, match="starts after"):
        place.renewal(conn, starts_on=date(2026, 1, 1))
    with pytest.raises(psycopg.errors.CheckViolation, match="the offer is for unit"):
        offer_lease(conn, place.pmc_id, place.unit, place.staff, JUL_1, renews_lease_id=place.lease)


@pytest.mark.parametrize(
    "terms",
    [
        {"application_id": None},  # for nothing
        "both",
        {"rent": "0"},
        {"ends_on": date(2026, 12, 31)},  # before it starts
    ],
)
def test_an_offer_is_for_one_thing_with_sound_terms(conn, place, terms):
    if terms == "both":
        terms = {"renews_lease_id": place.lease}
    with pytest.raises(psycopg.errors.CheckViolation):
        place.new_lease_offer(conn, **terms)


@pytest.mark.parametrize(
    "document",
    [b"%PDF", b"not a pdf at all", LEASE_PDF + b"x" * (5_000_001 - len(LEASE_PDF))],
    ids=["too short", "not a pdf", "a byte over 5 MB"],
)
def test_the_lease_document_is_a_pdf_of_at_most_5_mb(conn, place, document):
    with pytest.raises(psycopg.errors.CheckViolation):
        conn.execute(
            "INSERT INTO trust_lease_offers (pmc_id, application_id, unit_id, starts_on,"
            " monthly_rent, document_title, document, offered_by)"
            " VALUES (%s, %s, %s, %s, 1, 'Lease', %s, %s)",
            (place.pmc_id, place.application, place.unit, JAN_1, document, place.staff),
        )


def test_a_lease_document_of_5_mb_is_kept(conn, place):
    conn.execute(
        "INSERT INTO trust_lease_offers (pmc_id, application_id, unit_id, starts_on,"
        " monthly_rent, document_title, document, offered_by)"
        " VALUES (%s, %s, %s, %s, 1, 'Lease', %s, %s)",
        (
            place.pmc_id,
            place.application,
            place.unit,
            JAN_1,
            LEASE_PDF + b"x" * (5_000_000 - len(LEASE_PDF)),
            place.staff,
        ),
    )


def test_a_signature_is_for_a_sent_offer_and_one_of_its_signers(conn, place):
    offer = place.new_lease_offer(conn)
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        sign_offer(conn, place.pmc_id, offer, 1)  # not sent yet
    send_offer(conn, place.pmc_id, offer, place.staff)
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        sign_offer(conn, place.pmc_id, offer, 3)  # no third signer
    sign_offer(conn, place.pmc_id, offer, 1)
    with pytest.raises(psycopg.errors.UniqueViolation):
        sign_offer(conn, place.pmc_id, offer, 1)
    with pytest.raises(psycopg.errors.UniqueViolation):
        send_offer(conn, place.pmc_id, offer, place.staff)


@pytest.mark.parametrize("column", ["partner", "partner_reference"])
def test_a_signature_request_names_its_partner_and_reference(conn, place, column):
    offer = place.new_lease_offer(conn)
    names = {"partner": "Test partner", "partner_reference": "ref-1"} | {column: "  "}
    with pytest.raises(psycopg.errors.CheckViolation):
        conn.execute(
            "INSERT INTO trust_signature_requests (pmc_id, offer_id, partner, partner_reference,"
            " sent_by) VALUES (%s, %s, %s, %s, %s)",
            (place.pmc_id, offer, names["partner"], names["partner_reference"], place.staff),
        )


@pytest.mark.parametrize("title", ["  ", "T" * 201])
def test_a_document_title_says_something_in_at_most_200_characters(conn, place, title):
    with pytest.raises(psycopg.errors.CheckViolation):
        conn.execute(
            "INSERT INTO trust_lease_offers (pmc_id, application_id, unit_id, starts_on,"
            " monthly_rent, document_title, document, offered_by)"
            " VALUES (%s, %s, %s, %s, 1, %s, %s, %s)",
            (place.pmc_id, place.application, place.unit, JAN_1, title, LEASE_PDF, place.staff),
        )


def test_the_longest_title_name_and_email_are_kept(conn, place):
    email = "s" * (254 - len("@example.com")) + "@example.com"
    offer = place.new_lease_offer(conn, signers=(("N" * 200, email),))
    conn.execute(
        "INSERT INTO trust_lease_offers (pmc_id, application_id, unit_id, starts_on,"
        " monthly_rent, document_title, document, offered_by)"
        " VALUES (%s, %s, %s, %s, 1, %s, %s, %s)",
        (place.pmc_id, place.application, place.unit, JAN_1, "T" * 200, LEASE_PDF, place.staff),
    )

    assert conn.execute(
        "SELECT length(display_name), length(email) FROM trust_lease_offer_signers"
        " WHERE offer_id = %s",
        (offer,),
    ).fetchall() == [(200, 254)]


@pytest.mark.parametrize(
    "signers",
    [
        (("Same", "same@example.com"), ("Other", "same@example.com")),
        (("  ", "a@example.com"),),
        (("N" * 201, "a@example.com"),),
        (("A", "A@example.com"),),
        (("A", "not an email"),),
        (("A", "s" * (255 - len("@example.com")) + "@example.com"),),
        tuple((f"S{n}", f"s{n}@example.com") for n in range(7)),
    ],
    ids=["same email", "blank name", "long name", "upper case", "no email", "long email", "seven"],
)
def test_signers_have_names_emails_of_their_own_and_six_places(conn, place, signers):
    with pytest.raises((psycopg.errors.CheckViolation, psycopg.errors.UniqueViolation)):
        place.new_lease_offer(conn, signers=signers)


@pytest.mark.parametrize("position", [0, 7])
def test_a_signers_place_is_1_to_6(conn, place, position):
    offer = place.new_lease_offer(conn, signers=())
    with pytest.raises(psycopg.errors.CheckViolation):
        conn.execute(
            "INSERT INTO trust_lease_offer_signers (pmc_id, offer_id, position, display_name,"
            " email) VALUES (%s, %s, %s, 'A', 'a@example.com')",
            (place.pmc_id, offer, position),
        )


def test_a_lease_is_the_completion_of_one_offer(conn, place):
    offer = place.new_lease_offer(conn)
    place.sign_all(conn, offer)
    lease = complete_offer(conn, place.pmc_id, offer, place.staff)
    other = place.new_lease_offer(conn, starts_on=date(2028, 1, 1), ends_on=None)

    another_lease = open_lease(
        conn,
        place.pmc_id,
        add_unit(conn, place.pmc_id, place.pmc.owners[0].property_id, "U3"),
        JAN_1,
        None,
        "900",
        [place.tenant],
    )
    complete = (
        "INSERT INTO trust_lease_offer_completions (pmc_id, offer_id, lease_id, completed_by)"
        " VALUES (%s, %s, %s, %s)"
    )

    # Even the table's owner can't write a second offer's completion onto the same lease, or a
    # second completion of the same offer.
    with pytest.raises(psycopg.errors.UniqueViolation):
        conn.execute(complete, (place.pmc_id, other, lease, place.staff))
    with pytest.raises(psycopg.errors.UniqueViolation):
        conn.execute(complete, (place.pmc_id, offer, another_lease, place.staff))


def test_only_the_function_completes_an_offer(conn, app_conn, place):
    offer = place.new_lease_offer(conn)
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        app_conn.execute(
            "INSERT INTO trust_lease_offer_completions (pmc_id, offer_id, lease_id, completed_by)"
            " VALUES (%s, %s, %s, %s)",
            (place.pmc_id, offer, place.lease, place.staff),
        )


def test_offers_complete_at_read_committed(database_url, place, conn):
    offer = place.new_lease_offer(conn)
    place.sign_all(conn, offer)
    with psycopg.connect(database_url) as repeatable:
        repeatable.isolation_level = psycopg.IsolationLevel.REPEATABLE_READ
        # Its own check, before it waits on another offer for the same application.
        with pytest.raises(
            psycopg.errors.InvalidTransactionState,
            match="complete lease offers at READ COMMITTED, not repeatable read",
        ):
            complete_offer(repeatable, place.pmc_id, offer, place.staff)


def test_everything_stays_in_its_pmc(conn, place):
    other = Place(conn)
    refused = (psycopg.errors.ForeignKeyViolation, psycopg.errors.CheckViolation)

    with pytest.raises(refused):  # another PMC's application
        place.new_lease_offer(conn, application_id=other.application)
    with pytest.raises(refused):  # another PMC's lease, at its unit
        offer_lease(
            conn, place.pmc_id, other.leased_unit, place.staff, JUL_1, renews_lease_id=other.lease
        )
    with pytest.raises(refused):  # offered by another PMC's staff
        offer_lease(
            conn, place.pmc_id, place.unit, other.staff, JAN_1, application_id=place.application
        )
    offer = place.new_lease_offer(conn)
    with pytest.raises(refused):  # sent by another PMC's staff
        send_offer(conn, place.pmc_id, offer, other.staff)
    place.sign_all(conn, offer)
    with pytest.raises(refused):  # completed by another PMC's staff
        complete_offer(conn, place.pmc_id, offer, other.staff)

    assert conn.execute(
        "SELECT count(*) FROM trust_lease_offer_completions WHERE offer_id = %s", (offer,)
    ).fetchone() == (0,)


def test_an_offer_of_another_pmc_is_not_found(conn, place):
    other = Place(conn)
    offer = other.new_lease_offer(conn)
    with pytest.raises(psycopg.errors.InvalidParameterValue, match="no lease offer"):
        complete_offer(conn, place.pmc_id, offer, place.staff)


@pytest.mark.parametrize("table", TABLES)
@pytest.mark.parametrize("statement", ["UPDATE {} SET pmc_id = pmc_id", "DELETE FROM {}"])
def test_offers_and_signatures_are_never_rewritten(conn, place, table, statement):
    with pytest.raises(psycopg.errors.RestrictViolation, match="append-only"):
        conn.execute(statement.format(table))


@pytest.mark.parametrize("table", TABLES)
def test_the_ai_role_reads_none_of_it(conn, table):
    assert conn.execute(
        "SELECT has_table_privilege('trust_ai_agent', %s, 'SELECT')", (table,)
    ).fetchone() == (False,)
