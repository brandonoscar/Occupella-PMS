"""API keys through the API (ADR 0011, migration 0201, src/pms/api/ai_keys.py): an admin makes a
key and sees its secret once, lists keys and takes one back; software with a key reads its
company's books as the AI's role, trust_ai_agent, and nothing else. Every name here is
synthetic."""

import uuid
from datetime import UTC, datetime

import psycopg
import pytest
from fastapi.testclient import TestClient
from helpers import acting_as, add_staff, give_roles, make_pmc
from people import join_as, join_as_owner, join_as_staff, tenant_of

from pms import accounts
from pms.api import ai_keys, create_app

TODAY = str(datetime.now(UTC).date())
NOT_VALID = "this key was taken back or doesn't exist"
ADMINS = "only an admin sees, makes and takes back API keys"


@pytest.fixture
def admin(client, conn, app_conn, pmc):
    return join_as_staff(client, conn, app_conn, pmc, name="Admin 1")


@pytest.fixture
def software(settings):
    """Software calling the API: no browser, no cookie, only the key it is given."""
    with TestClient(create_app(settings)) as test_client:
        yield test_client


def ok(answer, status=200):
    assert answer.status_code == status, answer.text
    return answer.json() if answer.content else None


def refused(answer, status, detail):
    assert answer.status_code == status, answer.text
    assert answer.json()["detail"] == detail, answer.text


def make_key(client, name="Assistant"):
    return ok(client.post("/api/ai/keys", json={"name": name}), 201)


def bearer(secret):
    return {"Authorization": f"Bearer {secret}"}


def company_of(conn, pmc):
    return conn.execute(
        "SELECT display_name FROM trust_pmcs WHERE pmc_id = %s", (pmc.pmc_id,)
    ).fetchone()[0]


# --- an admin's keys ----------------------------------------------------------------------------


def test_an_admin_makes_a_key_and_sees_its_secret_once(client, conn, pmc, admin):
    made = make_key(client, "  Occupella AI  ")
    listed = ok(client.get("/api/ai/keys"))

    assert made["name"] == "Occupella AI"
    assert made["secret"].startswith("occ_") and len(made["secret"]) == 47
    assert made["prefix"] == made["secret"][:12]
    assert listed == [
        {
            "id": made["id"],
            "name": "Occupella AI",
            "prefix": made["prefix"],
            "made_at": listed[0]["made_at"],
            "made_by": "Admin 1",
            "revoked_at": None,
        }
    ]
    # Kept only as its SHA-256: the secret is in no list, no row and no audit row.
    stored = conn.execute(
        "SELECT secret_sha256, recorded_by FROM trust_api_keys WHERE id = %s", (made["id"],)
    ).fetchone()
    assert (bytes(stored[0]), stored[1]) == (accounts.digest(made["secret"]), admin.person_id)
    secret_part = made["secret"][4:]
    assert secret_part not in client.get("/api/ai/keys").text
    assert conn.execute(
        "SELECT count(*) FROM trust_audit_log WHERE pmc_id = %s AND (row_data::text LIKE %s"
        " OR coalesce(before::text, '') LIKE %s)",
        (pmc.pmc_id, f"%{secret_part}%", f"%{secret_part}%"),
    ).fetchone() == (0,)
    assert conn.execute(
        "SELECT count(*) FROM trust_audit_log WHERE table_name = 'trust_api_keys'"
        " AND row_data ->> 'id' = %s AND staff_id = %s",
        (made["id"], admin.person_id),
    ).fetchone() == (1,)


def test_keys_are_listed_newest_first_each_standing_or_taken_back(client, pmc, admin):
    first = make_key(client, "First")
    second = make_key(client, "Second")
    ok(client.post(f"/api/ai/keys/{first['id']}/revoke", json={}), 204)

    listed = ok(client.get("/api/ai/keys"))

    assert [(k["name"], k["revoked_at"] is None) for k in listed] == [
        ("Second", True),
        ("First", False),
    ]
    assert listed[0]["id"] == second["id"]


def test_a_key_taken_back_opens_nothing_from_its_next_request(client, software, conn, admin):
    made = make_key(client)
    ok(software.get("/api/ai/me", headers=bearer(made["secret"])))

    ok(client.post(f"/api/ai/keys/{made['id']}/revoke", json={}), 204)

    refused(software.get("/api/ai/me", headers=bearer(made["secret"])), 401, NOT_VALID)
    assert conn.execute(
        "SELECT recorded_by FROM trust_api_key_revocations WHERE key_id = %s", (made["id"],)
    ).fetchone() == (admin.person_id,)


@pytest.mark.parametrize(
    "name",
    ["", "   ", "x" * 61, None, 7],
)
def test_a_key_needs_a_name_of_up_to_60_characters(client, admin, name):
    answer = client.post("/api/ai/keys", json={"name": name})

    assert answer.status_code == 422, answer.text


def test_a_name_of_60_characters_is_kept(client, admin):
    assert make_key(client, "k" * 60)["name"] == "k" * 60


def test_taking_back_an_unknown_or_another_companys_key_is_not_found(
    client, other_client, conn, app_conn, admin
):
    elsewhere = make_pmc(conn)
    join_as_staff(other_client, conn, app_conn, elsewhere, name="Admin 2")
    theirs = make_key(other_client, "Theirs")

    for key_id in (theirs["id"], str(uuid.uuid4())):
        refused(client.post(f"/api/ai/keys/{key_id}/revoke", json={}), 404, "no such API key")
    assert client.post("/api/ai/keys/not-an-id/revoke", json={}).status_code == 422
    # Theirs still stands.
    assert ok(other_client.get("/api/ai/keys"))[0]["revoked_at"] is None
    assert [k["name"] for k in ok(client.get("/api/ai/keys"))] == []


def test_a_key_is_taken_back_once(client, admin):
    made = make_key(client)
    ok(client.post(f"/api/ai/keys/{made['id']}/revoke", json={}), 204)

    refused(
        client.post(f"/api/ai/keys/{made['id']}/revoke", json={}),
        409,
        "this key was taken back already",
    )


def test_only_an_admin_sees_makes_and_takes_back_keys(
    client, other_client, conn, app_conn, pmc, admin
):
    made = make_key(client)
    join_as_staff(other_client, conn, app_conn, pmc, name="Clerk 1", roles=("approve", "disburse"))

    refused(other_client.get("/api/ai/keys"), 403, ADMINS)
    refused(other_client.post("/api/ai/keys", json={"name": "Mine"}), 403, ADMINS)
    refused(other_client.post(f"/api/ai/keys/{made['id']}/revoke", json={}), 403, ADMINS)
    assert [k["revoked_at"] for k in ok(client.get("/api/ai/keys"))] == [None]


@pytest.mark.parametrize("kind", ["owner", "tenant"])
def test_an_owner_or_a_tenant_has_no_keys(client, other_client, conn, app_conn, pmc, admin, kind):
    made = make_key(client)
    if kind == "owner":
        join_as_owner(other_client, app_conn, pmc)
    else:
        join_as(other_client, app_conn, pmc.pmc_id, "tenant", tenant_of(conn, pmc))

    for answer in (
        other_client.get("/api/ai/keys"),
        other_client.post("/api/ai/keys", json={"name": "Mine"}),
        other_client.post(f"/api/ai/keys/{made['id']}/revoke", json={}),
    ):
        refused(answer, 403, "only the PMC's staff may do this")


def test_no_session_has_no_keys(client, software, admin):
    made = make_key(client)

    for answer in (
        software.get("/api/ai/keys"),
        software.post("/api/ai/keys", json={"name": "Mine"}),
        software.post(f"/api/ai/keys/{made['id']}/revoke", json={}),
    ):
        refused(answer, 401, "sign in first")


def test_the_database_takes_a_key_and_its_revocation_from_an_admin_only(conn, app_conn, pmc):
    """Below the API: the trigger refuses a staff member without admin, and a key names who made
    it (the command line names nobody, and makes none)."""
    boss = add_staff(conn, pmc.pmc_id, "Admin 3")
    give_roles(conn, pmc.pmc_id, boss, ["admin"])
    clerk = add_staff(conn, pmc.pmc_id, "Clerk 2")
    insert = (
        "INSERT INTO trust_api_keys (pmc_id, name, prefix, secret_sha256)"
        " VALUES (%s, 'Direct', 'occ_direct01', %s) RETURNING id"
    )
    revoke = "INSERT INTO trust_api_key_revocations (key_id, pmc_id) VALUES (%s, %s)"
    digest = accounts.digest(f"occ_{uuid.uuid4().hex}")

    refused_to = pytest.raises(
        psycopg.errors.InsufficientPrivilege, match="may not make an API key"
    )
    with refused_to, acting_as(app_conn, clerk):
        app_conn.execute(insert, (pmc.pmc_id, digest))
    with pytest.raises(psycopg.errors.NotNullViolation):
        app_conn.execute(insert, (pmc.pmc_id, digest))
    with acting_as(app_conn, boss):
        key_id = app_conn.execute(insert, (pmc.pmc_id, digest)).fetchone()[0]
    refused_to = pytest.raises(
        psycopg.errors.InsufficientPrivilege, match="may not take an API key back"
    )
    with refused_to, acting_as(app_conn, clerk):
        app_conn.execute(revoke, (key_id, pmc.pmc_id))
    with pytest.raises(psycopg.errors.NotNullViolation):
        app_conn.execute(revoke, (key_id, pmc.pmc_id))
    with acting_as(app_conn, boss):
        app_conn.execute(revoke, (key_id, pmc.pmc_id))
    assert conn.execute(
        "SELECT recorded_by FROM trust_api_key_revocations WHERE key_id = %s", (key_id,)
    ).fetchone() == (boss,)


def test_keys_and_revocations_are_never_rewritten(client, conn, admin):
    made = make_key(client)
    ok(client.post(f"/api/ai/keys/{made['id']}/revoke", json={}), 204)

    for statement in (
        "UPDATE trust_api_keys SET name = 'Renamed' WHERE id = %s",
        "DELETE FROM trust_api_key_revocations WHERE key_id = %s",
        "DELETE FROM trust_api_keys WHERE id = %s",
    ):
        with pytest.raises(psycopg.errors.RestrictViolation, match="append-only"):
            conn.execute(statement, (made["id"],))


# --- software, with a key -----------------------------------------------------------------------


def test_a_key_reads_whose_books_it_is(client, software, conn, pmc, admin):
    made = make_key(client, "Occupella AI")

    seen = ok(software.get("/api/ai/me", headers=bearer(made["secret"])))

    assert seen == {"company": company_of(conn, pmc), "key": "Occupella AI"}


@pytest.mark.parametrize(
    "header",
    [
        None,
        "",
        "Bearer",
        "Bearer ",
        "Basic dXNlcjpwYXNz",
        "Bearer not-a-key",
        "Bearer occ_short",
        "Bearer occ_" + "x" * 101,
        "Bearer occ_" + "a" * 43,  # well formed, but no such key
        "Token occ_" + "a" * 43,
    ],
)
def test_a_missing_malformed_or_unknown_key_is_refused_alike(client, software, admin, header):
    make_key(client)
    headers = {} if header is None else {"Authorization": header}

    for path in ("/api/ai/me", f"/api/ai/reports/rent_roll?arg={TODAY}"):
        answer = software.get(path, headers=headers)
        refused(answer, 401, NOT_VALID)
        assert answer.headers["WWW-Authenticate"] == "Bearer"


def test_a_key_is_no_session_and_a_session_is_no_key(client, software, admin):
    made = make_key(client)
    key = bearer(made["secret"])

    # A key on the cookie endpoints: refused as no session.
    for answer in (
        software.get("/api/me", headers=key),
        software.get("/api/dashboard", headers=key),
        software.get(f"/api/reports/trust_report_rent_roll?arg={TODAY}", headers=key),
        software.get("/api/ai/keys", headers=key),
        software.post("/api/ai/keys", json={"name": "By the key"}, headers=key),
        software.post(f"/api/ai/keys/{made['id']}/revoke", json={}, headers=key),
        software.post("/api/notes", json={"about": "x"}, headers=key),
    ):
        refused(answer, 401, "sign in first")
    # A signed-in admin's cookie on the key endpoints: refused as no key.
    refused(client.get("/api/ai/me"), 401, NOT_VALID)
    # Nothing changed: the key stands, and is the only one.
    assert [k["revoked_at"] for k in ok(client.get("/api/ai/keys"))] == [None]


def test_a_key_runs_a_report_of_its_own_company(client, software, conn, pmc, admin):
    made = make_key(client)
    key = bearer(made["secret"])

    roll = ok(software.get(f"/api/ai/reports/rent_roll?arg={TODAY}", headers=key))
    named = ok(software.get(f"/api/ai/reports/trust_report_rent_roll?arg={TODAY}", headers=key))
    staffs = ok(client.get(f"/api/reports/trust_report_rent_roll?arg={TODAY}&format=json"))

    assert roll == named
    assert roll["columns"] == staffs["columns"]
    assert roll["columns"][:3] == ["line", "item", "property"]
    assert all(isinstance(v, str) or v is None for row in roll["rows"] for v in row)


def test_a_reports_company_is_always_the_keys(client, software, conn, app_conn, pmc):
    """The key's company is the report's first argument, whatever the request says: another
    company's id as an argument is one argument too many, and another company's owner is not
    found in the key's."""
    join_as_staff(client, conn, app_conn, pmc, name="Admin 1")
    elsewhere = make_pmc(conn)
    conn.execute(
        "UPDATE trust_owners SET display_name = 'Elsewhere Owner' WHERE pmc_id = %s",
        (elsewhere.pmc_id,),
    )
    key = bearer(make_key(client)["secret"])

    directory = ok(software.get(f"/api/ai/reports/owner_directory?arg={TODAY}", headers=key))
    too_many = software.get(
        f"/api/ai/reports/owner_directory?arg={elsewhere.pmc_id}&arg={TODAY}", headers=key
    )
    theirs = software.get(
        "/api/ai/reports/owner_statement",
        params={"arg": [str(elsewhere.owners[0].owner_id), "2026-01-01", "2026-02-01"]},
        headers=key,
    )

    owners = {row[directory["columns"].index("owner")] for row in directory["rows"]}
    assert owners >= {"Owner 1", "Owner 2"} and "Elsewhere Owner" not in owners
    refused(too_many, 422, "trust_report_owner_directory takes 2 arguments (uuid, date), not 3")
    assert theirs.status_code == 422, theirs.text
    assert f"in PMC {pmc.pmc_id}" in theirs.json()["detail"]


@pytest.mark.parametrize(
    ("name", "args", "status", "detail"),
    [
        ("no_such_report", [TODAY], 404, "no report named trust_report_no_such_report"),
        ("Rent-Roll", [TODAY], 404, None),
        ("rent_roll", [], 422, "trust_report_rent_roll takes 2 arguments (uuid, date), not 1"),
        ("rent_roll", ["not a day"], 422, None),
        ("rent_roll", [TODAY, TODAY], 422, None),
    ],
)
def test_a_report_the_key_cant_run_is_refused_in_words(
    client, software, admin, name, args, status, detail
):
    key = bearer(make_key(client)["secret"])

    answer = software.get(f"/api/ai/reports/{name}", params={"arg": args}, headers=key)

    assert answer.status_code == status, answer.text
    said = answer.json()["detail"]
    assert isinstance(said, str) and said
    if detail is not None:
        assert said == detail


def test_a_keys_request_runs_as_the_ai_role(client, software, conn, pmc, admin):
    """The same audit log report: staff see the key's own row, and the key, reading as
    trust_ai_agent, doesn't (row-level security shows that role only the rows of tables it may
    read, and it reads no key)."""
    key = bearer(make_key(client)["secret"])
    path = f"trust_report_audit_log?arg={TODAY}&arg={TODAY}"

    staffs = ok(client.get(f"/api/reports/{path}&format=json"))
    keys = ok(software.get(f"/api/ai/reports/{path}", headers=key))

    def records(table):
        return {row[table["columns"].index("record")] for row in table["rows"]}

    assert "api_keys" in records(staffs)
    assert "logins" in records(staffs)
    assert not {"api_keys", "logins", "login_secrets", "invites"} & records(keys)


def test_a_report_the_ai_role_may_not_run_is_refused_though_the_app_could(
    client, software, conn, admin
):
    """Were a report's grant to the AI's role ever taken away, a key could not run it, though
    staff (trust_app) still could: the database, not the app, holds the line."""
    key = bearer(make_key(client)["secret"])
    signature = "trust_report_vacancy(uuid, date)"
    conn.execute(f"REVOKE EXECUTE ON FUNCTION {signature} FROM trust_ai_agent")
    try:
        answer = software.get(f"/api/ai/reports/vacancy?arg={TODAY}", headers=key)
        staffs = client.get(f"/api/reports/trust_report_vacancy?arg={TODAY}&format=json")
    finally:
        conn.execute(f"GRANT EXECUTE ON FUNCTION {signature} TO trust_ai_agent")

    refused(answer, 403, "an API key may not run trust_report_vacancy")
    assert staffs.status_code == 200, staffs.text
    assert ok(software.get(f"/api/ai/reports/vacancy?arg={TODAY}", headers=key))["columns"]


def test_each_key_is_rate_limited(client, software, admin, monkeypatch):
    monkeypatch.setattr(ai_keys, "PER_MINUTE", 3)
    busy = bearer(make_key(client, "Busy")["secret"])
    calm = bearer(make_key(client, "Calm")["secret"])

    answers = [software.get("/api/ai/me", headers=busy) for _ in range(4)]

    assert [a.status_code for a in answers] == [200, 200, 200, 429]
    wait = int(answers[-1].headers["Retry-After"])
    assert 1 <= wait <= 60
    assert (
        answers[-1]
        .json()["detail"]
        .startswith("this key made 3 requests in the last minute: try again in ")
    )
    # Another key of the same company is counted on its own.
    ok(software.get("/api/ai/me", headers=calm))


def test_a_refused_key_counts_toward_nothing(client, software, admin, monkeypatch):
    monkeypatch.setattr(ai_keys, "PER_MINUTE", 1)
    made = make_key(client)
    unknown = bearer("occ_" + "b" * 43)

    for _ in range(3):
        refused(software.get("/api/ai/me", headers=unknown), 401, NOT_VALID)

    ok(software.get("/api/ai/me", headers=bearer(made["secret"])))
