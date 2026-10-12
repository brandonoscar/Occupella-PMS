"""The login tables (migration 20261011000028) and the command line that makes the first
invite (src/pms/admin.py). The API's behaviour is tested in tests/api/."""

import uuid

import psycopg
import pytest
from helpers import add_staff, make_pmc
from hypothesis import HealthCheck, example, given, settings
from hypothesis import strategies as st

from pms import accounts, admin


@pytest.fixture
def pmc(conn):
    return make_pmc(conn)


def add_login(conn, pmc, kind="owner", email="o@example.com", person=None):
    person = person or pmc.owners[0].owner_id
    return conn.execute(
        f"INSERT INTO trust_logins (pmc_id, kind, {kind}_id, email) VALUES (%s, %s, %s, %s)"
        " RETURNING id",
        (pmc.pmc_id, kind, person, email),
    ).fetchone()[0]


@pytest.mark.parametrize(
    "email", ["Owner@Example.com", "no-at-sign.example.com", "a@b", "a b@example.com"]
)
def test_an_email_is_lowercase_and_shaped_like_one(conn, pmc, email):
    with pytest.raises(psycopg.errors.CheckViolation):
        add_login(conn, pmc, email=email)


@settings(suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(st.text(alphabet="ab.@ \t", max_size=12))
@example("a@" + "." * 5000 + " ")
@example("a@.b.")
@example("a@b..")
@example("a@.b")
def test_the_app_and_the_database_accept_the_same_emails(conn, pmc, email):
    """accounts.EMAIL is the database's check rewritten so it can't backtrack (CodeQL
    py/polynomial-redos); both must still accept the same addresses. (Python's \\s also counts
    a few characters Postgres doesn't, such as U+00A0, so the app refuses those first.)"""
    try:
        with conn.transaction(force_rollback=True):
            add_login(conn, pmc, email=email)
        database_accepts = True
    except psycopg.errors.CheckViolation:
        database_accepts = False
    assert bool(accounts.EMAIL.fullmatch(email)) == database_accepts


def test_a_login_is_for_one_kind_of_person(conn, pmc):
    with pytest.raises(psycopg.errors.CheckViolation):
        conn.execute(
            "INSERT INTO trust_logins (pmc_id, kind, owner_id, tenant_id, email)"
            " VALUES (%s, 'owner', %s, %s, 'x@example.com')",
            (pmc.pmc_id, pmc.owners[0].owner_id, uuid.uuid4()),
        )


def test_one_login_per_person_and_per_email_in_a_pmc(conn, pmc):
    add_login(conn, pmc)
    other = make_pmc(conn)

    with pytest.raises(psycopg.errors.UniqueViolation):
        add_login(conn, pmc, email="second@example.com")
    with pytest.raises(psycopg.errors.UniqueViolation):
        add_login(conn, pmc, email="o@example.com", person=pmc.owners[1].owner_id)
    add_login(conn, other, email="o@example.com", person=other.owners[0].owner_id)  # another PMC


def test_a_login_is_for_a_person_of_its_own_pmc(conn, pmc):
    other = make_pmc(conn)

    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        add_login(conn, pmc, person=other.owners[0].owner_id)


def test_tokens_are_stored_as_32_byte_hashes(conn, pmc):
    login = add_login(conn, pmc)

    with pytest.raises(psycopg.errors.CheckViolation):
        conn.execute(
            "INSERT INTO trust_invites (pmc_id, login_id, token_hash, expires_at)"
            " VALUES (%s, %s, %s, now() + interval '1 day')",
            (pmc.pmc_id, login, b"too short"),
        )


def test_only_argon2id_hashes_are_stored(conn, pmc):
    login = add_login(conn, pmc)

    with pytest.raises(psycopg.errors.CheckViolation):
        conn.execute(
            "INSERT INTO trust_login_secrets (login_id, pmc_id, password_hash)"
            " VALUES (%s, %s, 'plain text')",
            (login, pmc.pmc_id),
        )


def test_the_app_never_deletes_a_login_invite_session_or_secret(conn, app_conn, pmc):
    for table in ("trust_logins", "trust_invites", "trust_sessions", "trust_login_secrets"):
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            app_conn.execute(f"DELETE FROM {table} WHERE false")


def test_the_ai_role_reads_no_login_or_credential(ai_conn):
    for table in ("trust_logins", "trust_invites", "trust_sessions", "trust_login_secrets"):
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            ai_conn.execute(f"SELECT 1 FROM {table}")


def test_no_password_hash_reaches_the_audit_log(conn, app_conn, pmc):
    token = accounts.create_invite(
        app_conn, pmc.pmc_id, "owner", pmc.owners[0].owner_id, "o@example.com", None
    ).token
    accounts.accept_invite(app_conn, token, "a long enough password")

    logged = conn.execute(
        "SELECT count(*) FILTER (WHERE row_data::text LIKE '%%argon2%%'),"
        " count(*) FILTER (WHERE table_name = 'trust_login_secrets'),"
        " count(*) FILTER (WHERE table_name = 'trust_sessions')"
        " FROM trust_audit_log WHERE pmc_id = %s",
        (pmc.pmc_id,),
    ).fetchone()
    assert logged == (0, 0, 1)


# --- the command line ---------------------------------------------------------------------


def test_the_command_line_adds_staff_and_prints_their_invite(
    conn, database_url, monkeypatch, capsys, pmc
):
    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.setenv("PMS_PUBLIC_URL", "https://pms.example.com/")

    assert admin.main(["add-staff", str(pmc.pmc_id), "First Staff"]) == 0
    staff_id = uuid.UUID(capsys.readouterr().out.strip())
    assert admin.main(["make-admin", str(pmc.pmc_id), str(staff_id)]) == 0
    assert capsys.readouterr().out == f"{staff_id} is an admin\n"
    assert admin.main(["invite", str(pmc.pmc_id), "staff", str(staff_id), "First@Example.com"]) == 0

    printed = capsys.readouterr()
    assert printed.out.startswith("https://pms.example.com/invite#")
    assert printed.err.startswith("expires ")
    token = printed.out.strip().split("#", 1)[1]
    with psycopg.connect(database_url, autocommit=True) as app_conn:
        app_conn.execute("SET ROLE trust_app")
        assert accounts.view_invite(app_conn, token).email == "first@example.com"


def test_the_command_line_says_why_it_refused(conn, database_url, monkeypatch, capsys, pmc):
    monkeypatch.setenv("DATABASE_URL", database_url)
    staff_id = add_staff(conn, pmc.pmc_id)

    unknown = admin.main(["invite", str(pmc.pmc_id), "owner", str(uuid.uuid4()), "o@example.com"])
    no_pmc = admin.main(["add-staff", str(uuid.uuid4()), "Nobody"])
    first_admin = admin.main(["make-admin", str(pmc.pmc_id), str(staff_id)])
    second_admin = admin.main(["make-admin", str(pmc.pmc_id), str(add_staff(conn, pmc.pmc_id))])

    assert (unknown, no_pmc, first_admin, second_admin) == (2, 2, 0, 2)
    err = capsys.readouterr().err
    assert "refused: no owner" in err
    assert "refused: insert or update on table" in err
    assert "refused: name the admin who changes a role" in err
    assert conn.execute(
        "SELECT count(*) FROM trust_logins WHERE staff_id = %s", (staff_id,)
    ).fetchone() == (0,)


def test_a_login_is_only_for_staff_owners_and_tenants(app_conn, pmc):
    with pytest.raises(accounts.Refused, match="a login is for staff, owner, tenant, not 'vendor'"):
        accounts.create_invite(app_conn, pmc.pmc_id, "vendor", pmc.vendor_id, "v@example.com", None)
