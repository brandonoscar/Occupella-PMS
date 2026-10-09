"""The exact privileges of the app role, and of a role nobody granted anything.

Money can only move through the ledger functions, so trust_app's grants are a money guard. This
pins them down completely: any grant added, dropped or widened by a migration fails here.
`pms_nobody` stands in for any other role on the server (PUBLIC), such as a Supabase `anon`.
"""

import pytest

TABLES = {
    "pgledger_accounts": {"SELECT"},
    "pgledger_transfers": {"SELECT"},
    "pgledger_entries": {"SELECT"},
    "pgledger_accounts_view": {"SELECT"},
    "pgledger_transfers_view": {"SELECT"},
    "pgledger_entries_view": {"SELECT"},
    "trust_pmcs": {"SELECT", "INSERT"},
    "trust_bank_accounts": {"SELECT", "INSERT"},
    "trust_owners": {"SELECT", "INSERT"},
    "trust_properties": {"SELECT", "INSERT"},
    "trust_tenants": {"SELECT", "INSERT"},
    "trust_ledger_accounts": {"SELECT"},
}
PRIVILEGES = ["SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE", "REFERENCES", "TRIGGER"]
RENAMEABLE = [
    "trust_pmcs",
    "trust_bank_accounts",
    "trust_owners",
    "trust_properties",
    "trust_tenants",
]

FUNCTIONS = {
    "pgledger_create_transfer(text,text,numeric,timestamptz,jsonb)": True,
    "pgledger_create_transfers(transfer_request[])": True,
    "pgledger_create_transfers(transfer_request[],timestamptz,jsonb)": True,
    "pgledger_create_account(text,text,boolean,boolean,jsonb)": False,
    "trust_open_ledger_account(uuid,uuid,text,uuid,uuid,uuid)": True,
}
# Run as the table owner, so trust_app needs no write grant of its own.
SECURITY_DEFINER = {
    "pgledger_create_transfers(transfer_request[],timestamptz,jsonb)",
    "trust_open_ledger_account(uuid,uuid,text,uuid,uuid,uuid)",
}


@pytest.fixture
def nobody(conn):
    conn.execute(
        "DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'pms_nobody') "
        "THEN CREATE ROLE pms_nobody NOLOGIN; END IF; END $$"
    )
    return "pms_nobody"


def granted(conn, role, table):
    return {
        privilege
        for privilege in PRIVILEGES
        if conn.execute(
            "SELECT has_table_privilege(%s, %s, %s)", (role, table, privilege)
        ).fetchone()[0]
    }


@pytest.mark.parametrize("table", TABLES)
def test_app_role_table_privileges_are_exact(conn, table):
    assert granted(conn, "trust_app", table) == TABLES[table]


@pytest.mark.parametrize("table", RENAMEABLE)
def test_app_role_may_update_only_display_names(conn, table):
    columns = [
        row[0]
        for row in conn.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_name = %s", (table,)
        )
    ]
    updatable = {
        column
        for column in columns
        if conn.execute(
            "SELECT has_column_privilege('trust_app', %s, %s, 'UPDATE')", (table, column)
        ).fetchone()[0]
    }
    assert updatable == {"display_name"}


@pytest.mark.parametrize("signature", FUNCTIONS)
def test_app_role_function_privileges_are_exact(conn, signature):
    allowed = conn.execute(
        "SELECT has_function_privilege('trust_app', %s, 'EXECUTE')", (signature,)
    ).fetchone()[0]
    assert allowed is FUNCTIONS[signature]


@pytest.mark.parametrize("table", TABLES)
def test_other_roles_get_no_table_privileges(conn, nobody, table):
    assert granted(conn, nobody, table) == set()


@pytest.mark.parametrize("signature", FUNCTIONS)
def test_other_roles_cannot_call_money_functions(conn, nobody, signature):
    allowed = conn.execute(
        "SELECT has_function_privilege(%s, %s, 'EXECUTE')", (nobody, signature)
    ).fetchone()[0]
    assert allowed is False


@pytest.mark.parametrize("signature", FUNCTIONS)
def test_only_the_write_paths_run_as_owner_with_a_pinned_search_path(conn, signature):
    definer, config = conn.execute(
        "SELECT prosecdef, proconfig FROM pg_proc WHERE oid = %s::regprocedure", (signature,)
    ).fetchone()
    assert definer is (signature in SECURITY_DEFINER)
    if definer:
        assert "search_path=public, pg_temp" in (config or [])
