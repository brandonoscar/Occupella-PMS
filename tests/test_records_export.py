"""Everything of a company in one download (src/pms/exports.py, records_zip; the founder's review
q132): each of its record tables the caller may read as a CSV file, its files (documents, leases
offered, signatures drawn) as files, and its ledger. Never a credential: logins' secrets and
sessions stay out, and so does every token's hash, in its table and in the audit log's copies of
rows. Only the company's own rows, the same bytes for the same books."""

import csv
import io
import uuid
import zipfile

import pytest
from helpers import make_pmc

from pms import demo, exports


@pytest.fixture(scope="module")
def everything(database_url):
    import psycopg

    with psycopg.connect(database_url, autocommit=True) as app:
        app.execute("SET ROLE trust_app")
        built = demo.build(app)
        other = make_pmc(app)
        app.execute(
            "UPDATE trust_pmcs SET display_name = 'Elsewhere Holdings' WHERE pmc_id = %s",
            (other.pmc_id,),
        )
        first = exports.records_zip(app, built.pmc_id)
        again = exports.records_zip(app, built.pmc_id)
        documents = app.execute(
            "SELECT d.title, f.content FROM trust_documents AS d"
            " JOIN trust_document_files AS f ON f.document_id = d.id WHERE d.pmc_id = %s",
            (built.pmc_id,),
        ).fetchall()
        yield built, first, again, documents


def table(archive, name):
    return list(csv.DictReader(io.StringIO(archive.read(name).decode())))


def test_every_record_table_its_files_and_its_ledger_come_out(everything):
    _, data, _, documents = everything
    archive = zipfile.ZipFile(io.BytesIO(data))
    names = set(archive.namelist())

    for name in (
        "records/trust_properties.csv",
        "records/trust_tenants.csv",
        "records/trust_contacts.csv",
        "records/trust_notes.csv",
        "records/trust_charges.csv",
        "records/trust_charge_payments.csv",
        "records/trust_work_order_steps.csv",
        "records/trust_bills.csv",
        "records/trust_documents.csv",
        "records/trust_audit_log.csv",
        "ledger/accounts.csv",
        "ledger/transfers.csv",
        "ledger/entries.csv",
    ):
        assert name in names, name
    assert table(archive, "records/trust_properties.csv")
    # Each document's bytes are a file of its own, named in its CSV row.
    assert documents
    files = table(archive, "records/trust_document_files.csv")
    kept = {archive.read(row["content"]) for row in files}
    assert {bytes(content) for _, content in documents} <= kept
    assert all(row["content"].startswith("files/trust_document_files/") for row in files)


def test_no_credential_comes_out(everything):
    _, data, _, _ = everything
    archive = zipfile.ZipFile(io.BytesIO(data))
    names = archive.namelist()

    assert "records/trust_login_secrets.csv" not in names
    assert "records/trust_sessions.csv" not in names
    for name in names:
        if name.endswith(".csv"):
            header = archive.read(name).decode().splitlines()[0].split(",")
            assert not set(header) & {"token_hash", "token_sha256", "status_token_sha256"}, name
            assert not {"password_hash", "totp_secret"} & set(header), name
    audit = archive.read("records/trust_audit_log.csv").decode()
    assert "token_hash" not in audit and "token_sha256" not in audit
    assert "trust_sessions" not in audit and "trust_login_secrets" not in audit


def test_only_the_companys_rows_and_the_same_bytes_for_the_same_books(everything):
    built, first, again, _ = everything
    archive = zipfile.ZipFile(io.BytesIO(first))
    text = b"".join(archive.read(n) for n in archive.namelist() if n.endswith(".csv"))

    assert first == again
    assert b"Elsewhere Holdings" not in text
    assert {row["pmc_id"] for row in table(archive, "records/trust_properties.csv")} == {
        str(built.pmc_id)
    }


def test_an_unknown_company_is_refused(app_conn):
    with pytest.raises(exports.ExportError, match="no PMC"):
        exports.records_zip(app_conn, uuid.uuid4())


@pytest.mark.parametrize(
    ("first_bytes", "extension"),
    [
        (b"%PDF-1.4", "pdf"),
        (b"\x89PNG\r\n\x1a\n", "png"),
        (b"\xff\xd8\xff\xe0", "jpg"),
        (b"GIF89a", "bin"),
    ],
)
def test_a_files_name_ends_as_its_first_bytes_say(first_bytes, extension):
    assert exports._extension(first_bytes + b" synthetic file") == extension
