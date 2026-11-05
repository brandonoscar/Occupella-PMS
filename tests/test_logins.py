"""The login tables (migration 20261011000028) and the command line that makes the first
invite (src/pms/admin.py). The API's behaviour is tested in tests/api/."""

import uuid

import psycopg
import pytest
from helpers import add_staff, make_pmc, roles_of
from hypothesis import HealthCheck, example, given, settings
from hypothesis import strategies as st

from pms import accounts, admin
from pms.api import setup


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


def test_setup_starts_a_company_with_its_first_admin_and_their_invite(
    conn, database_url, monkeypatch, capsys
):
    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.setenv("PMS_PUBLIC_URL", "https://pms.example.com/")
    name = f"Synthetic Setup Co {uuid.uuid4().hex[:8]}"

    assert admin.main(["setup", f"  {name} ", "First Admin", "First.Admin@Example.com"]) == 0

    printed = capsys.readouterr()
    company_line, admin_line, link = printed.out.strip().splitlines()
    pmc_id = uuid.UUID(company_line.removeprefix("company "))
    staff_id = uuid.UUID(admin_line.removeprefix("admin "))
    assert link.startswith("https://pms.example.com/invite#")
    assert printed.err.startswith("expires ")
    assert conn.execute(
        "SELECT display_name FROM trust_pmcs WHERE pmc_id = %s", (pmc_id,)
    ).fetchone() == (name,)
    assert conn.execute(
        "SELECT pmc_id, display_name FROM trust_staff WHERE id = %s", (staff_id,)
    ).fetchone() == (pmc_id, "First Admin")
    assert roles_of(conn, pmc_id, staff_id) == {"admin"}
    with psycopg.connect(database_url, autocommit=True) as app_conn:
        app_conn.execute("SET ROLE trust_app")
        invite = accounts.view_invite(app_conn, link.split("#", 1)[1])
    assert invite.email == "first.admin@example.com"
    # Its starting chart of accounts, each on its Schedule E line.
    assert sorted(
        conn.execute(
            "SELECT name, schedule_e_line FROM trust_expense_accounts WHERE pmc_id = %s",
            (pmc_id,),
        ).fetchall()
    ) == sorted(setup.STARTING_CHART)


def test_starting_chart_adds_what_a_company_lacks_once(conn, database_url, monkeypatch, capsys):
    monkeypatch.setenv("DATABASE_URL", database_url)
    pmc = make_pmc(conn)
    # The company already keeps repairs, spelled its own way, and one account of its own.
    conn.execute(
        "INSERT INTO trust_expense_accounts (pmc_id, name, schedule_e_line)"
        " VALUES (%s, 'REPAIRS', 14), (%s, 'Snow removal', 7)",
        (pmc.pmc_id, pmc.pmc_id),
    )

    assert admin.main(["starting-chart", str(pmc.pmc_id)]) == 0
    assert capsys.readouterr().out == f"added {len(setup.STARTING_CHART) - 1} expense accounts\n"
    assert admin.main(["starting-chart", str(pmc.pmc_id)]) == 0
    assert capsys.readouterr().out == "added 0 expense accounts\n"

    names = {
        row[0]
        for row in conn.execute(
            "SELECT name FROM trust_expense_accounts WHERE pmc_id = %s", (pmc.pmc_id,)
        )
    }
    assert names == {name for name, _ in setup.STARTING_CHART if name != "Repairs"} | {
        "REPAIRS",
        "Snow removal",
    }


def test_starting_chart_refuses_a_company_that_isnt_there(database_url, monkeypatch, capsys):
    monkeypatch.setenv("DATABASE_URL", database_url)
    assert admin.main(["starting-chart", str(uuid.uuid4())]) == 2
    assert capsys.readouterr().err == "refused: no company has that id\n"


def test_the_starting_chart_sits_on_expense_lines_and_never_on_management_fees():
    lines = {line for _, line in setup.STARTING_CHART}
    assert lines <= set(setup.SCHEDULE_E) and 11 not in lines
    names = [name.lower() for name, _ in setup.STARTING_CHART]
    assert len(names) == len(set(names))


def test_setup_is_all_or_nothing(conn, database_url, monkeypatch, capsys):
    monkeypatch.setenv("DATABASE_URL", database_url)
    name = f"Synthetic Refused Co {uuid.uuid4().hex[:8]}"

    bad_email = admin.main(["setup", name, "First Admin", "not an email"])
    no_name = admin.main(["setup", name, "  ", "first@example.com"])
    no_company = admin.main(["setup", " ", "First Admin", "first@example.com"])

    assert (bad_email, no_name, no_company) == (2, 2, 2)
    err = capsys.readouterr().err
    assert err.count("refused: ") == 3
    assert "a company and a person each need a name" in err
    assert conn.execute(
        "SELECT count(*) FROM trust_pmcs WHERE display_name = %s", (name,)
    ).fetchone() == (0,)


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
