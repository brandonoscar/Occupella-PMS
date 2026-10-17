"""Documents (migration 20261012000036): a vendor's W-9, a bill's receipt, a lease's move-out
photos and an owner's documents. A document is about one thing of its PMC, its file is the
PDF, JPEG or PNG its row says (its first bytes, size and SHA-256), and it has its file at commit.
The audit log records the document but never copies the file's bytes; the AI never reads them.
Every file here is synthetic."""

from datetime import UTC, date, datetime

import psycopg
import pytest
from helpers import (
    JPEG,
    PDF,
    PNG,
    acting_as,
    add_document,
    add_staff,
    add_unit,
    add_work_order,
    enter_bill,
    make_pmc,
    open_lease,
)

FILES = {"application/pdf": PDF, "image/png": PNG, "image/jpeg": JPEG}
# Each kind and the record it is about (migrations 0036, 0072 and 0074).
COLUMN = {
    "w9": "vendor_id",
    "receipt": "bill_id",
    "move_out_photo": "lease_id",
    "owner_document": "owner_id",
    "work_order_photo": "work_order_id",
    "property_document": "property_id",
    "unit_document": "unit_id",
    "tenant_document": "tenant_id",
    "lease_document": "lease_id",
    "vendor_document": "vendor_id",
}


def refused(match, error=psycopg.errors.CheckViolation):
    return pytest.raises(error, match=match)


@pytest.fixture
def pmc(conn):
    return make_pmc(conn)


@pytest.fixture
def about(conn, pmc):
    """One thing of the PMC each kind of document may be about."""
    bill = enter_bill(
        conn,
        pmc.pmc_id,
        pmc.vendor_id,
        pmc.owners[0].account,
        "INV-1",
        date(2026, 1, 5),
        date(2026, 2, 1),
        "100.00",
    )
    tenant = conn.execute(
        "SELECT tenant_id FROM trust_ledger_accounts WHERE ledger_account_id = %s",
        (pmc.tenant_deposit,),
    ).fetchone()[0]
    unit = add_unit(conn, pmc.pmc_id, pmc.owners[0].property_id)
    lease = open_lease(conn, pmc.pmc_id, unit, date(2026, 1, 1), None, "1000.00", [tenant])
    order = add_work_order(
        conn, pmc.pmc_id, pmc.owners[0].property_id, unit, "Leak", datetime(2026, 1, 5, tzinfo=UTC)
    )
    return {
        "w9": pmc.vendor_id,
        "receipt": bill,
        "move_out_photo": lease,
        "owner_document": pmc.owners[0].owner_id,
        "work_order_photo": order,
        "property_document": pmc.owners[0].property_id,
        "unit_document": unit,
        "tenant_document": tenant,
        "lease_document": lease,
        "vendor_document": pmc.vendor_id,
    }


KINDS = list(COLUMN)


def document(conn, document_id):
    return conn.execute(
        "SELECT kind, title, content_type, size, uploaded_by FROM trust_documents WHERE id = %s",
        (document_id,),
    ).fetchone()


@pytest.mark.parametrize(
    ("kind", "content_type"),
    [
        ("w9", "application/pdf"),
        ("receipt", "image/jpeg"),
        ("move_out_photo", "image/png"),
        ("owner_document", "application/pdf"),
        ("work_order_photo", "image/jpeg"),
        ("property_document", "application/pdf"),
        ("unit_document", "image/png"),
        ("tenant_document", "application/pdf"),
        ("lease_document", "application/pdf"),
        ("vendor_document", "application/pdf"),
    ],
)
def test_a_document_is_kept_with_its_file_and_who_uploaded_it(
    conn, app_conn, pmc, about, kind, content_type
):
    staff = add_staff(conn, pmc.pmc_id)
    content = FILES[content_type]
    with acting_as(app_conn, staff):
        made = add_document(app_conn, pmc.pmc_id, kind, about[kind], content, content_type, "Scan")

    assert document(conn, made) == (kind, "Scan", content_type, len(content), staff)
    assert conn.execute(
        "SELECT content FROM trust_document_files WHERE document_id = %s", (made,)
    ).fetchone() == (content,)


def test_nobody_named_uploads_as_the_server(conn, pmc, about):
    made = add_document(conn, pmc.pmc_id, "w9", about["w9"])
    assert document(conn, made)[4] is None


def test_a_row_does_not_say_who_uploaded_or_removed_it(conn, pmc, about):
    """Only the staff member the app names is recorded, never one the row names itself."""
    staff = add_staff(conn, pmc.pmc_id)
    with conn.transaction():
        made = conn.execute(
            "INSERT INTO trust_documents (pmc_id, kind, vendor_id, title, content_type, size,"
            " sha256, uploaded_by) VALUES (%s, 'w9', %s, 'Scan', 'application/pdf', %s,"
            " sha256(%s), %s) RETURNING id",
            (pmc.pmc_id, about["w9"], len(PDF), PDF, staff),
        ).fetchone()[0]
        conn.execute(
            "INSERT INTO trust_document_files (document_id, pmc_id, content) VALUES (%s, %s, %s)",
            (made, pmc.pmc_id, PDF),
        )
    conn.execute(
        "INSERT INTO trust_document_removals (document_id, pmc_id, removed_by) VALUES (%s, %s, %s)",
        (made, pmc.pmc_id, staff),
    )
    assert conn.execute(
        "SELECT d.uploaded_by, r.removed_by FROM trust_documents AS d"
        " JOIN trust_document_removals AS r ON r.document_id = d.id WHERE d.id = %s",
        (made,),
    ).fetchone() == (None, None)


@pytest.mark.parametrize("kind", KINDS)
def test_each_kind_is_about_its_one_kind_of_thing(conn, pmc, about, kind):
    # Another kind's record, in a column of its own.
    other = next(k for k in about if COLUMN[k] != COLUMN[kind])
    for about_what in ({}, {kind: about[kind], other: about[other]}):
        names = ", ".join(COLUMN[k] for k in about_what)
        with refused(r"trust_documents_\w*check"), conn.transaction():
            conn.execute(
                f"INSERT INTO trust_documents (pmc_id, kind, {names + ', ' if names else ''}"
                " title, content_type, size, sha256)"
                f" VALUES (%s, %s, {'%s, ' * len(about_what)}'x', 'application/pdf', 1,"
                " sha256('x'::bytea))",
                (pmc.pmc_id, kind, *about_what.values()),
            )


def test_a_document_is_one_of_its_kinds(conn, pmc):
    with refused("trust_documents_kind_check"), conn.transaction():
        conn.execute(
            "INSERT INTO trust_documents (pmc_id, kind, title, content_type, size, sha256)"
            " VALUES (%s, 'lease', 'x', 'application/pdf', 1, sha256('x'::bytea))",
            (pmc.pmc_id,),
        )


@pytest.mark.parametrize("kind", KINDS)
def test_a_document_is_about_its_own_pmcs_records(conn, pmc, about, kind):
    other = make_pmc(conn)
    with refused("foreign key", psycopg.errors.ForeignKeyViolation):
        add_document(conn, other.pmc_id, kind, about[kind])


def test_a_file_and_a_removal_are_of_their_own_pmcs_document(conn, pmc, about):
    other = make_pmc(conn)
    with refused("foreign key", psycopg.errors.ForeignKeyViolation), conn.transaction():
        document_id = conn.execute(
            "INSERT INTO trust_documents (pmc_id, kind, vendor_id, title, content_type, size,"
            " sha256) VALUES (%s, 'w9', %s, 'Scan', 'application/pdf', %s, sha256(%s))"
            " RETURNING id",
            (pmc.pmc_id, about["w9"], len(PDF), PDF),
        ).fetchone()[0]
        conn.execute(
            "INSERT INTO trust_document_files (document_id, pmc_id, content) VALUES (%s, %s, %s)",
            (document_id, other.pmc_id, PDF),
        )
    made = add_document(conn, pmc.pmc_id, "w9", about["w9"])
    with refused("foreign key", psycopg.errors.ForeignKeyViolation):
        conn.execute(
            "INSERT INTO trust_document_removals (document_id, pmc_id) VALUES (%s, %s)",
            (made, other.pmc_id),
        )


@pytest.mark.parametrize(
    ("column", "value", "why"),
    [
        ("title", "  ", "trust_documents_title_check"),
        ("title", "x" * 201, "trust_documents_title_check"),
        ("content_type", "text/html", "trust_documents_content_type_check"),
        ("size", 0, "trust_documents_size_check"),
        ("size", 5 * 1024 * 1024 + 1, "trust_documents_size_check"),
        ("sha256", b"short", "trust_documents_sha256_check"),
    ],
)
def test_a_documents_details_stay_in_range(conn, pmc, about, column, value, why):
    row = {"title": "Scan", "content_type": "application/pdf", "size": 1, "sha256": b"\0" * 32}
    row[column] = value
    with refused(why), conn.transaction():
        conn.execute(
            "INSERT INTO trust_documents (pmc_id, kind, vendor_id, title, content_type, size,"
            " sha256) VALUES (%s, 'w9', %s, %s, %s, %s, %s)",
            (
                pmc.pmc_id,
                about["w9"],
                row["title"],
                row["content_type"],
                row["size"],
                row["sha256"],
            ),
        )


@pytest.mark.parametrize(
    ("said", "sent", "why"),
    [
        ("application/pdf", PNG, "is not a application/pdf"),
        ("image/png", JPEG, "is not a image/png"),
        ("image/jpeg", PDF, "is not a image/jpeg"),
    ],
)
def test_a_file_is_the_type_its_document_says(conn, pmc, about, said, sent, why):
    with refused(why), conn.transaction():
        document_id = conn.execute(
            "INSERT INTO trust_documents (pmc_id, kind, vendor_id, title, content_type, size,"
            " sha256) VALUES (%s, 'w9', %s, 'Scan', %s, %s, sha256(%s)) RETURNING id",
            (pmc.pmc_id, about["w9"], said, len(sent), sent),
        ).fetchone()[0]
        conn.execute(
            "INSERT INTO trust_document_files (document_id, pmc_id, content) VALUES (%s, %s, %s)",
            (document_id, pmc.pmc_id, sent),
        )


@pytest.mark.parametrize(("size", "digest_of"), [(len(PDF) + 1, PDF), (len(PDF), PDF + b"!")])
def test_a_file_is_the_size_and_digest_its_document_says(conn, pmc, about, size, digest_of):
    with refused("is not the .* bytes it says"), conn.transaction():
        document_id = conn.execute(
            "INSERT INTO trust_documents (pmc_id, kind, vendor_id, title, content_type, size,"
            " sha256) VALUES (%s, 'w9', %s, 'Scan', 'application/pdf', %s, sha256(%s))"
            " RETURNING id",
            (pmc.pmc_id, about["w9"], size, digest_of),
        ).fetchone()[0]
        conn.execute(
            "INSERT INTO trust_document_files (document_id, pmc_id, content) VALUES (%s, %s, %s)",
            (document_id, pmc.pmc_id, PDF),
        )


def test_a_document_has_its_file_at_commit(conn, pmc, about):
    with refused("has no file"), conn.transaction():
        conn.execute(
            "INSERT INTO trust_documents (pmc_id, kind, vendor_id, title, content_type, size,"
            " sha256) VALUES (%s, 'w9', %s, 'Scan', 'application/pdf', %s, sha256(%s))",
            (pmc.pmc_id, about["w9"], len(PDF), PDF),
        )
    assert conn.execute(
        "SELECT count(*) FROM trust_documents WHERE pmc_id = %s", (pmc.pmc_id,)
    ).fetchone() == (0,)


def test_documents_are_append_only_and_a_removal_says_who(conn, app_conn, pmc, about):
    made = add_document(conn, pmc.pmc_id, "w9", about["w9"])
    for table in ("trust_documents", "trust_document_files", "trust_document_removals"):
        for query in (f"UPDATE {table} SET pmc_id = pmc_id", f"DELETE FROM {table}"):
            with refused("append-only", psycopg.errors.RestrictViolation):
                conn.execute(query)
    staff = add_staff(conn, pmc.pmc_id)
    with acting_as(app_conn, staff):
        app_conn.execute(
            "INSERT INTO trust_document_removals (document_id, pmc_id, removed_by)"
            " VALUES (%s, %s, NULL)",
            (made, pmc.pmc_id),
        )
    assert conn.execute(
        "SELECT removed_by FROM trust_document_removals WHERE document_id = %s", (made,)
    ).fetchone() == (staff,)
    with refused("duplicate key", psycopg.errors.UniqueViolation):
        conn.execute(
            "INSERT INTO trust_document_removals (document_id, pmc_id) VALUES (%s, %s)",
            (made, pmc.pmc_id),
        )


def test_the_log_records_the_document_and_never_its_bytes(conn, pmc, about):
    made = add_document(conn, pmc.pmc_id, "w9", about["w9"])
    logged = conn.execute(
        "SELECT table_name, row_data ->> 'id' FROM trust_audit_log WHERE pmc_id = %s"
        " AND table_name LIKE 'trust_document%%'",
        (pmc.pmc_id,),
    ).fetchall()
    assert logged == [("trust_documents", str(made))]
    assert not conn.execute(
        "SELECT 1 FROM trust_audit_log WHERE row_data::text LIKE %s",
        (f"%{PDF.hex()}%",),
    ).fetchone()


def test_the_ai_knows_a_document_exists_and_never_reads_it(conn, ai_conn, pmc, about):
    add_document(conn, pmc.pmc_id, "w9", about["w9"])
    assert ai_conn.execute(
        "SELECT title FROM trust_documents WHERE pmc_id = %s", (pmc.pmc_id,)
    ).fetchall() == [("W-9",)]
    with refused("permission denied", psycopg.errors.InsufficientPrivilege):
        ai_conn.execute("SELECT content FROM trust_document_files")
