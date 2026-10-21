"""Signing links by email, at the database (migration 20261018000090; the founder's review, q081).
The API's side is in tests/api/test_offers.py. Every name and address here is synthetic."""

from datetime import date

import psycopg
import pytest
from helpers import (
    add_staff,
    add_tenant,
    add_unit,
    make_pmc,
    offer_lease,
    open_application_link,
    record_decision,
    submit_application,
)

INSERT = (
    "INSERT INTO trust_emails (pmc_id, kind, to_address, subject, body, offer_id, signer_position,"
    " tenant_id, needs_link) VALUES (%s, %s, 's@example.com', 'S', 'B', %s, %s, %s, %s)"
)


def test_a_signing_email_names_one_signer_of_an_offer_once_and_needs_its_link(conn):
    pmc = make_pmc(conn)
    pmc_id = pmc.pmc_id
    staff = add_staff(conn, pmc_id, "Leasing staff")
    unit = add_unit(conn, pmc_id, pmc.owners[0].property_id, "Unit 1")
    application = submit_application(conn, pmc_id, open_application_link(conn, pmc_id, unit, staff))
    record_decision(conn, pmc_id, application, staff, "approved")
    offer = offer_lease(conn, pmc_id, unit, staff, date(2027, 1, 1), application_id=application)
    tenant = add_tenant(conn, pmc_id, pmc.owners[0].property_id, "Tenant 1")

    for kind, about, position, named, link in (
        ("signing_request", offer, None, None, True),  # no signer
        ("signing_request", None, 1, None, True),  # no offer
        ("signing_request", offer, 1, None, False),  # its link isn't held
        ("signing_request", offer, 1, tenant, True),  # names a tenant too
        ("message", offer, 1, tenant, False),  # only a signing email names an offer
    ):
        with pytest.raises(psycopg.errors.CheckViolation), conn.transaction():
            conn.execute(INSERT, (pmc_id, kind, about, position, named, link))
    # Another kind of email, its sender named, names no offer and no signer, even one alone.
    for about, position in ((offer, None), (None, 1), (offer, 1)):
        with pytest.raises(psycopg.errors.CheckViolation), conn.transaction():
            conn.execute(
                "INSERT INTO trust_emails (pmc_id, kind, to_address, subject, body, offer_id,"
                " signer_position, tenant_id, sent_by) VALUES (%s, 'message', 's@example.com',"
                " 'S', 'B', %s, %s, %s, %s)",
                (pmc_id, about, position, tenant, staff),
            )
    # A signer the offer doesn't have, or another company's offer.
    with pytest.raises(psycopg.errors.ForeignKeyViolation), conn.transaction():
        conn.execute(INSERT, (pmc_id, "signing_request", offer, 2, None, True))
    with pytest.raises(psycopg.errors.ForeignKeyViolation), conn.transaction():
        conn.execute(INSERT, (make_pmc(conn).pmc_id, "signing_request", offer, 1, None, True))

    conn.execute(INSERT, (pmc_id, "signing_request", offer, 1, None, True))
    with pytest.raises(psycopg.errors.UniqueViolation):
        conn.execute(INSERT, (pmc_id, "signing_request", offer, 1, None, True))
