"""The exact privileges of the app role, and of a role nobody granted anything.

Money can only move through the ledger functions, so trust_app's grants are a money guard. This
pins them down completely: any grant added, dropped or widened by a migration fails here.
`pms_nobody` stands in for any other role on the server (PUBLIC), such as a Supabase `anon`.

Every table, view and function in the schema is pinned, not just the ones that exist today:
test_every_relation_and_function_is_pinned fails on a new one until it is listed here, so no
migration adds one without deciding who may use it. That matters most for functions, because
Postgres lets PUBLIC call a new function unless the migration revokes it.
"""

import pytest
from helpers import NOT_FROM_AN_EXTENSION

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
    "trust_vendors": {"SELECT", "INSERT"},
    "trust_ledger_accounts": {"SELECT"},
    "trust_idempotency_keys": set(),  # only trust_post_transfers reads and writes it
    "trust_reconciliations": {"SELECT"},  # written only by trust_approve_reconciliation
    "trust_units": {"SELECT", "INSERT"},
    "trust_leases": {"SELECT"},  # written only by trust_open_lease and trust_end_lease
    "trust_lease_tenants": {"SELECT"},  # written only by trust_open_lease
    "trust_charges": {"SELECT", "INSERT"},
    "trust_charge_payments": {"SELECT"},  # written only by trust_apply_payment
    "schema_migrations": set(),  # dbmate's record of applied migrations
}
PRIVILEGES = ["SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE", "REFERENCES", "TRIGGER"]
RENAMEABLE = [
    "trust_pmcs",
    "trust_bank_accounts",
    "trust_owners",
    "trust_properties",
    "trust_tenants",
    "trust_vendors",
    "trust_units",
]

# Who may call each function, besides the role that owns it.
FUNCTIONS = {
    # The write paths: the app role and nobody else.
    "trust_open_ledger_account(uuid,uuid,text,uuid,uuid,uuid)": {"trust_app"},
    "trust_post_transfers(uuid,text,transfer_request[],timestamptz,jsonb)": {"trust_app"},
    "trust_open_vendor_account(uuid,uuid,uuid)": {"trust_app"},
    "trust_approve_reconciliation(uuid,uuid,timestamptz,timestamptz,numeric,text,text)": {
        "trust_app"
    },
    "trust_open_lease(uuid,uuid,date,date,numeric,uuid[])": {"trust_app"},
    "trust_end_lease(uuid,uuid,date)": {"trust_app"},
    "trust_apply_payment(uuid,uuid,text,numeric)": {"trust_app"},
    # Reports read trust records, so only the app; they run with the caller's own read grants.
    "trust_report_three_way_reconciliation(uuid,uuid)": {"trust_app"},
    "trust_report_owner_statement(uuid,uuid,timestamptz,timestamptz)": {"trust_app"},
    "trust_report_rent_roll(uuid,date)": {"trust_app"},
    # pgledger's posting functions take no idempotency key, so a retried request could post
    # twice: only the owner calls them (trust_post_transfers does, as the owner).
    "pgledger_create_transfer(text,text,numeric,timestamptz,jsonb)": set(),
    "pgledger_create_transfers(transfer_request[])": set(),
    "pgledger_create_transfers(transfer_request[],timestamptz,jsonb)": set(),
    # Opens a bare pgledger account with no trust kind; only trust_open_ledger_account may.
    "pgledger_create_account(text,text,boolean,boolean,jsonb)": set(),
    # Id helpers and checks that read nothing they are not handed: callable by anyone.
    "parse_ulid(text)": {"PUBLIC"},
    "format_ulid(bytea)": {"PUBLIC"},
    "ulid_to_uuid(text)": {"PUBLIC"},
    "uuid_to_ulid(uuid)": {"PUBLIC"},
    "pgledger_uuidv7()": {"PUBLIC"},
    "pgledger_uuidv7_exists()": {"PUBLIC"},
    "pgledger_uuidv7_microsecond()": {"PUBLIC"},
    "pgledger_generate_id(text)": {"PUBLIC"},
    "pgledger_check_account_balance_constraints(pgledger_accounts)": {"PUBLIC"},
    # Trigger functions: Postgres refuses to call one outside its trigger.
    "trust_refuse_ledger_rewrite()": {"PUBLIC"},
    "trust_refuse_negative_balance()": {"PUBLIC"},
    "trust_check_transfer_scope()": {"PUBLIC"},
    "trust_check_bank_tie_out()": {"PUBLIC"},
    "trust_refuse_posting_into_closed_period()": {"PUBLIC"},
    "trust_refuse_moving_closed_through()": {"PUBLIC"},
    "trust_check_account_bank_kind()": {"PUBLIC"},
    "trust_refuse_changing_bank_kind()": {"PUBLIC"},
    "trust_refuse_overlapping_leases()": {"PUBLIC"},
}
MONEY_FUNCTIONS = [signature for signature, callers in FUNCTIONS.items() if "PUBLIC" not in callers]
# Run as the table owner, so trust_app needs no write grant of its own.
SECURITY_DEFINER = {
    "trust_open_ledger_account(uuid,uuid,text,uuid,uuid,uuid)",
    "trust_post_transfers(uuid,text,transfer_request[],timestamptz,jsonb)",
    "trust_open_vendor_account(uuid,uuid,uuid)",
    "trust_approve_reconciliation(uuid,uuid,timestamptz,timestamptz,numeric,text,text)",
    "trust_open_lease(uuid,uuid,date,date,numeric,uuid[])",
    "trust_end_lease(uuid,uuid,date)",
    "trust_apply_payment(uuid,uuid,text,numeric)",
}


@pytest.fixture
def nobody(conn):
    conn.execute(
        "DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'pms_nobody') "
        "THEN CREATE ROLE pms_nobody NOLOGIN; END IF; END $$"
    )
    return "pms_nobody"


# Everything a migration created in the public schema.
RELATIONS = f"""
    SELECT c.relname FROM pg_class c
    WHERE c.relnamespace = 'public'::regnamespace AND c.relkind IN ('r', 'p', 'v', 'm', 'S', 'f')
      AND {NOT_FROM_AN_EXTENSION.format(catalog="'pg_class'", oid="c.oid")}
"""
ROUTINES = f"""
    SELECT p.oid::regprocedure::text FROM pg_proc p
    WHERE p.pronamespace = 'public'::regnamespace
      AND {NOT_FROM_AN_EXTENSION.format(catalog="'pg_proc'", oid="p.oid")}
"""


def callers(conn, signature):
    """Roles other than the owner that hold EXECUTE, with PUBLIC spelled out."""
    return {
        row[0]
        for row in conn.execute(
            """
            SELECT CASE WHEN a.grantee = 0 THEN 'PUBLIC' ELSE a.grantee::regrole::text END
            FROM pg_proc p, aclexplode(coalesce(p.proacl, acldefault('f', p.proowner))) a
            WHERE p.oid = %s::regprocedure AND a.privilege_type = 'EXECUTE'
              AND a.grantee <> p.proowner
            """,
            (signature,),
        )
    }


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


def test_every_relation_and_function_is_pinned(conn):
    relations = {row[0] for row in conn.execute(RELATIONS)}
    routines = {row[0] for row in conn.execute(ROUTINES)}
    pinned = {
        row[0]
        for row in conn.execute(
            "SELECT s::regprocedure::text FROM unnest(%s::text[]) AS s", (list(FUNCTIONS),)
        )
    }

    assert relations == set(TABLES), "pin every table and view in TABLES, with its grants"
    assert routines == pinned, "pin every function in FUNCTIONS, with who may call it"


@pytest.mark.parametrize("signature", FUNCTIONS)
def test_function_callers_are_exact(conn, signature):
    assert callers(conn, signature) == FUNCTIONS[signature]


@pytest.mark.parametrize("signature", FUNCTIONS)
def test_app_role_function_privileges_are_exact(conn, signature):
    allowed = conn.execute(
        "SELECT has_function_privilege('trust_app', %s, 'EXECUTE')", (signature,)
    ).fetchone()[0]
    assert allowed is bool(FUNCTIONS[signature] & {"trust_app", "PUBLIC"})


def test_no_function_that_runs_as_owner_is_callable_by_public(conn):
    # Independent of the pins above: a SECURITY DEFINER function runs with the owner's rights,
    # so leaving PUBLIC's default EXECUTE on one would let any role on the server use them.
    exposed = [
        signature
        for (signature,) in conn.execute(ROUTINES + " AND p.prosecdef")
        if "PUBLIC" in callers(conn, signature)
    ]
    assert exposed == []


def test_no_role_but_the_owner_may_delete_or_truncate(conn):
    # Ledger history is append-only and the app role never deletes (CLAUDE.md), whatever a
    # migration grants.
    grants = conn.execute(
        """
        SELECT c.relname, CASE WHEN a.grantee = 0 THEN 'PUBLIC' ELSE a.grantee::regrole::text END,
               a.privilege_type
        FROM pg_class c, aclexplode(coalesce(c.relacl, acldefault('r', c.relowner))) a
        WHERE c.relnamespace = 'public'::regnamespace AND a.grantee <> c.relowner
          AND a.privilege_type IN ('DELETE', 'TRUNCATE')
        """
    ).fetchall()
    assert grants == []


@pytest.mark.parametrize("table", TABLES)
def test_other_roles_get_no_table_privileges(conn, nobody, table):
    assert granted(conn, nobody, table) == set()


@pytest.mark.parametrize("signature", MONEY_FUNCTIONS)
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
