"""A database whose own HTTP API can reach the books is refused (src/pms/schema.py).

The OWASP review of 11/06: Supabase serves every table and function of a project's public schema
through its Data API to the roles `anon` and `authenticated`, and grants each new one to them (and
to `service_role`) by default; the anon key is in every browser that loads the project's pages.
The PMS's migrations revoke what PUBLIC holds, never what those roles were granted by name, and
its tables have no row-level security, so on such a project anyone with the anon key could read
every table and call the write paths that run as the owner (a draw, a payout) naming no staff
member. The app's pooled connections, the nightly run and the webhook courier now refuse a
database where any role such an API answers as holds anything in the schema."""

import psycopg
import pytest
from psycopg import sql

from pms import schema


def role(conn: psycopg.Connection, name: str) -> str:
    """A role of the server's (roles are the server's, not a database's), made once."""
    if conn.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (name,)).fetchone() is None:
        conn.execute(sql.SQL("CREATE ROLE {} NOLOGIN").format(sql.Identifier(name)))
    return name


@pytest.fixture
def api_roles(conn):
    """Supabase's roles, as its projects make them: authenticator (the API's login) may become
    anon, authenticated or service_role. Whatever a test grants them is taken back after it."""
    for name in ("anon", "authenticated", "service_role", "authenticator", "web_anon", "bi"):
        role(conn, name)
    for name in ("anon", "authenticated", "service_role"):
        conn.execute(f"GRANT {name} TO authenticator")
    conn.commit()
    yield
    for name in ("anon", "authenticated", "service_role", "web_anon", "bi"):
        conn.execute(f"REVOKE ALL ON ALL TABLES IN SCHEMA public FROM {name}")
        conn.execute(f"REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM {name}")
        conn.execute(f"REVOKE ALL ON ALL FUNCTIONS IN SCHEMA public FROM {name}")
    conn.execute("REVOKE web_anon FROM authenticator")
    conn.execute("REVOKE trust_app FROM anon")
    conn.commit()


def refused(app_conn) -> str:
    with pytest.raises(schema.Exposed) as found:
        schema.check(app_conn)
    return str(found.value)


def test_the_roles_alone_hold_nothing_and_say_nothing(api_roles, app_conn):
    schema.check(app_conn)  # Supabase's roles exist, with nothing of the books: nothing to say


def test_a_table_the_api_can_read_is_refused(api_roles, conn, app_conn):
    conn.execute("GRANT SELECT ON trust_tax_ids TO anon")
    conn.commit()
    assert refused(app_conn) == (
        "the database's own API can reach the books: anon holds SELECT on trust_tax_ids. Revoke"
        " what the API's roles hold in schema public, or turn that API off (docs/install.md,"
        " 'A database with its own API')"
    )


def test_what_a_supabase_project_grants_by_default_is_refused(api_roles, conn, app_conn):
    # What `ALTER DEFAULT PRIVILEGES ... GRANT ALL ... TO anon, authenticated, service_role`
    # gives every table, sequence and function a migration makes on such a project.
    for name in ("anon", "authenticated", "service_role"):
        conn.execute(f"GRANT ALL ON ALL TABLES IN SCHEMA public TO {name}")
        conn.execute(f"GRANT ALL ON ALL SEQUENCES IN SCHEMA public TO {name}")
        conn.execute(f"GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA public TO {name}")
    conn.commit()
    said = refused(app_conn)
    assert said.startswith("the database's own API can reach the books: anon holds ")
    assert " more. Revoke what the API's roles hold in schema public" in said


def test_a_function_the_api_can_call_is_refused(api_roles, conn, app_conn):
    conn.execute("GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA public TO authenticated")
    conn.commit()
    said = refused(app_conn)  # format_ulid first: pgledger's helpers sort before trust_*
    assert said.startswith("the database's own API can reach the books: authenticated holds")
    assert " EXECUTE on " in said


def test_a_role_the_api_login_may_become_is_refused(api_roles, conn, app_conn):
    conn.execute("GRANT web_anon TO authenticator")  # a PostgREST of its own's anonymous role
    conn.execute("GRANT SELECT ON trust_pmcs TO web_anon")
    conn.commit()
    assert "web_anon holds SELECT on trust_pmcs." in refused(app_conn)


def test_an_api_role_made_a_member_of_the_app_role_is_refused(api_roles, conn, app_conn):
    conn.execute("GRANT trust_app TO anon")  # anon may then act as the app itself
    conn.commit()
    # trust_app's grants, and trust_ai_agent's, which trust_app may become, are the API's now.
    assert refused(app_conn).startswith("the database's own API can reach the books: trust_")


def test_a_role_no_api_answers_as_may_hold_grants(api_roles, conn, app_conn):
    conn.execute("GRANT SELECT ON trust_pmcs TO bi")  # a company's own reporting login
    conn.commit()
    schema.check(app_conn)
