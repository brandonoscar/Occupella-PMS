"""A unit's listing photos, at the database (migration 20261018000087; the founder's review,
q076). The API's side is in tests/api/test_listing_photos_api.py. Every name here is synthetic."""

import psycopg
import pytest
from helpers import JPEG, PDF, PNG, add_document, add_unit, make_pmc

INSERT = (
    "INSERT INTO trust_documents (pmc_id, kind, unit_id, property_id, title, content_type, size,"
    " sha256) VALUES (%s, %s, %s, %s, 'Photo', %s, 1, sha256('x'))"
)


def test_a_listing_photo_is_an_image_about_one_unit(conn):
    pmc = make_pmc(conn)
    place = pmc.owners[0].property_id
    unit = add_unit(conn, pmc.pmc_id, place, "Unit 1")
    photos = [
        add_document(conn, pmc.pmc_id, "listing_photo", unit, PNG, "image/png", "Kitchen"),
        add_document(conn, pmc.pmc_id, "listing_photo", unit, JPEG, "image/jpeg", "Porch"),
    ]
    # A unit's other documents still may be PDFs.
    add_document(conn, pmc.pmc_id, "unit_document", unit, PDF, "application/pdf", "Floor plan")
    assert conn.execute(
        "SELECT count(*) FROM trust_documents WHERE id = ANY(%s) AND unit_id = %s",
        (photos, unit),
    ).fetchone() == (2,)

    for kind, about_unit, about_property, content_type, check in (
        ("listing_photo", unit, None, "application/pdf", "listing_photo_check"),
        ("listing_photo", None, None, "image/png", "unit_check"),
        ("property_document", unit, place, "image/png", "unit_check"),
    ):
        refused = pytest.raises(psycopg.errors.CheckViolation, match=check)
        with refused, conn.transaction():
            conn.execute(INSERT, (pmc.pmc_id, kind, about_unit, about_property, content_type))
    other = make_pmc(conn).pmc_id
    with pytest.raises(psycopg.errors.ForeignKeyViolation), conn.transaction():
        conn.execute(INSERT, (other, "listing_photo", unit, None, "image/png"))
