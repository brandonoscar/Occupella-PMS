"""Staff download reports and the ledger through the API (src/pms/api, src/pms/exports).

The PMC always comes from the session: a report's first argument is the PMC (pinned below for
every report), and the API never takes it from the request.
"""

import csv
import io
import zipfile
from datetime import UTC, datetime

import pytest
from helpers import make_pmc, transfer_batch
from people import join_as_owner, join_as_staff, join_as_tenant

NOON = datetime(2026, 3, 1, 12, tzinfo=UTC)


def rows(data: bytes) -> list[list[str]]:
    return list(csv.reader(io.StringIO(data.decode(), newline="")))


def test_every_report_takes_the_pmc_first(conn):
    firsts = conn.execute(
        "SELECT proname, proargnames[1], format_type(proargtypes[0], NULL) FROM pg_proc"
        " WHERE pronamespace = 'public'::regnamespace AND proname LIKE 'trust\\_report\\_%'"
    ).fetchall()

    assert firsts
    assert {(first, kind) for _, first, kind in firsts} == {("p_pmc_id", "uuid")}


@pytest.mark.parametrize(
    ("fmt", "media", "magic"),
    [
        ("csv", "text/csv; charset=utf-8", b"line,item"),
        ("xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", b"PK"),
        ("pdf", "application/pdf", b"%PDF-"),
    ],
)
def test_staff_download_a_report_in_each_format(client, conn, app_conn, pmc, fmt, media, magic):
    join_as_staff(client, conn, app_conn, pmc)

    answer = client.get(
        "/api/reports/trust_report_rent_roll", params={"arg": "2026-01-31", "format": fmt}
    )

    assert answer.status_code == 200
    assert answer.headers["content-type"] == media
    assert answer.headers["content-disposition"] == f'attachment; filename="rent_roll.{fmt}"'
    assert answer.content.startswith(magic)


def test_the_report_is_always_the_sessions_pmc(client, conn, app_conn, pmc):
    join_as_staff(client, conn, app_conn, pmc)
    other = make_pmc(conn)

    ours = rows(
        client.get("/api/reports/trust_report_vendor_payments", params={"arg": "2026"}).content
    )
    smuggled = client.get(
        "/api/reports/trust_report_vendor_payments",
        params=[("arg", str(other.pmc_id)), ("arg", "2026")],
    )

    pmc_name = conn.execute(
        "SELECT display_name FROM trust_pmcs WHERE pmc_id = %s", (pmc.pmc_id,)
    ).fetchone()[0]
    assert ours[1] == ["1", "PMC", pmc_name, "", ""]
    assert smuggled.status_code == 422
    assert "takes 2 arguments" in smuggled.json()["detail"]


@pytest.mark.parametrize(
    ("name", "args", "status"),
    [
        ("trust_report_nothing", [], 404),
        ("trust_post_transfers", [], 404),
        ("pg_sleep", [], 404),
        ("trust_report_rent_roll", [], 422),
        ("trust_report_rent_roll", ["not a day"], 422),
        ("trust_report_general_ledger", ["2026-02-01", "2026-01-01"], 422),
        ("trust_report_owner_statement", ["nope", "2026-01-01", "2026-02-01"], 422),
    ],
)
def test_a_bad_report_request_is_refused(client, conn, app_conn, pmc, name, args, status):
    join_as_staff(client, conn, app_conn, pmc)

    answer = client.get(f"/api/reports/{name}", params=[("arg", a) for a in args])

    assert answer.status_code == status
    assert answer.json()["detail"]


def test_an_unknown_format_is_refused(client, conn, app_conn, pmc):
    join_as_staff(client, conn, app_conn, pmc)

    answer = client.get(
        "/api/reports/trust_report_rent_roll", params={"arg": "2026-01-31", "format": "docx"}
    )

    assert answer.status_code == 422


@pytest.mark.parametrize("caller", ["nobody", "owner", "tenant"])
def test_only_staff_download(client, conn, app_conn, pmc, caller):
    if caller == "owner":
        join_as_owner(client, app_conn, pmc)
    elif caller == "tenant":
        join_as_tenant(client, conn, app_conn, pmc)
    expected = 401 if caller == "nobody" else 403

    report = client.get("/api/reports/trust_report_rent_roll", params={"arg": "2026-01-31"})
    ledger = client.get("/api/ledger.zip")

    assert (report.status_code, ledger.status_code) == (expected, expected)


def test_staff_download_their_pmcs_ledger(client, conn, app_conn, pmc):
    join_as_staff(client, conn, app_conn, pmc)
    transfer_batch(conn, [(pmc.operating_cash, pmc.owners[0].account, "10.00")], NOON)

    answer = client.get("/api/ledger.zip")

    assert answer.status_code == 200
    assert answer.headers["content-disposition"] == 'attachment; filename="ledger.zip"'
    archive = zipfile.ZipFile(io.BytesIO(answer.content))
    transfers = rows(archive.read("transfers.csv"))
    assert len(transfers) == 2 and transfers[1][5] == "10.00"


@pytest.mark.parametrize("path", ["/api/ledger.zip", "/api/records.zip"])
def test_the_schema_says_a_zip_comes_back(client, path):
    # What a client (or OWASP ZAP, rule 100001) reads the answer by: the zip it is, not JSON.
    answers = client.get("/openapi.json").json()["paths"][path]["get"]["responses"]

    assert list(answers["200"]["content"]) == ["application/zip"]


def test_an_admin_downloads_everything_of_their_company(client, conn, app_conn, pmc):
    """Settings, Data export (q132): every record, its files and its ledger, in one zip."""
    join_as_staff(client, conn, app_conn, pmc)
    transfer_batch(conn, [(pmc.operating_cash, pmc.owners[0].account, "10.00")], NOON)

    answer = client.get("/api/records.zip")

    assert answer.status_code == 200
    assert answer.headers["content-disposition"] == 'attachment; filename="records.zip"'
    archive = zipfile.ZipFile(io.BytesIO(answer.content))
    names = archive.namelist()
    assert {"records/trust_properties.csv", "ledger/transfers.csv"} <= set(names)
    assert "records/trust_login_secrets.csv" not in names
    assert "records/trust_sessions.csv" not in names


def test_only_an_admin_downloads_everything(client, conn, app_conn, pmc):
    join_as_staff(client, conn, app_conn, pmc, roles=("approve", "disburse"))

    answer = client.get("/api/records.zip")

    assert (answer.status_code, answer.json()["detail"]) == (
        403,
        "only an admin downloads everything",
    )


@pytest.mark.parametrize("caller", ["nobody", "owner", "tenant"])
def test_nobody_but_staff_reaches_everything(client, conn, app_conn, pmc, caller):
    if caller == "owner":
        join_as_owner(client, app_conn, pmc)
    elif caller == "tenant":
        join_as_tenant(client, conn, app_conn, pmc)

    assert client.get("/api/records.zip").status_code == (401 if caller == "nobody" else 403)
