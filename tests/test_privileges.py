"""The exact privileges of the app role, and of a role nobody granted anything.

Money can only move through the ledger functions, so trust_app's grants are a money guard. This
pins them down completely: any grant added, dropped or widened by a migration fails here.
`pms_nobody` stands in for any other role on the server (PUBLIC), such as a Supabase `anon`.

trust_ai_agent, the role behind the API key Occupella will hold, reads what trust_app reads and
runs the reports: it can't write a row or call a function that writes (the AI never moves money).

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
    "trust_payment_reversals": {"SELECT"},  # written only by trust_reverse_payment
    "trust_management_agreements": {"SELECT", "INSERT"},
    "trust_management_fees": {"SELECT"},  # written only by trust_post_management_fee
    "trust_leasing_fees": {"SELECT"},  # written only by trust_post_leasing_fee
    "trust_owner_draws": {"SELECT"},  # written only by trust_draw_owner
    "trust_bills": {"SELECT", "INSERT"},
    "trust_bill_approvals": {"SELECT", "INSERT"},
    "trust_bill_payments": {"SELECT"},  # written only by trust_set_aside_bill, trust_pay_bill
    "trust_work_orders": {"SELECT", "INSERT"},
    "trust_work_order_steps": {"SELECT", "INSERT"},
    "trust_late_fee_policies": {"SELECT", "INSERT"},
    "trust_late_fees": {"SELECT"},  # written only by trust_assess_late_fees
    "trust_opening_balances": {"SELECT"},  # written only by trust_post_opening_balances
    "trust_staff": {"SELECT", "INSERT"},
    "trust_audit_log": {"SELECT"},  # written only by the trust_audit trigger
    "trust_audit_log_id_seq": set(),  # the log's ids; only the trigger draws them
    "trust_payment_holds": {"SELECT", "INSERT"},
    "trust_payment_hold_releases": {"SELECT", "INSERT"},
    "trust_payment_hold_allowances": {"SELECT", "INSERT"},
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
    "trust_staff",
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
    "trust_reverse_payment(uuid,uuid,text,text,numeric)": {"trust_app"},
    "trust_post_management_fee(uuid,text,date,date,timestamptz)": {"trust_app"},
    "trust_post_leasing_fee(uuid,uuid,text,timestamptz)": {"trust_app"},
    "trust_draw_owner(uuid,text,text,numeric,timestamptz)": {"trust_app"},
    "trust_set_aside_bill(uuid,uuid,timestamptz)": {"trust_app"},
    "trust_pay_bill(uuid,uuid,timestamptz)": {"trust_app"},
    "trust_charge_rent_due(uuid,date)": {"trust_app"},
    "trust_assess_late_fees(uuid,date)": {"trust_app"},
    "trust_post_opening_balances(uuid,uuid,date,jsonb,numeric,text)": {"trust_app"},
    # Helpers the fee, draw and bill functions call as the owner: nobody else.
    "trust_lock_owner_account(uuid,text)": set(),
    "trust_agreement_on(text,date)": set(),
    "trust_post_fee(trust_ledger_accounts,numeric,timestamptz,text)": set(),
    "trust_lock_bill(uuid,uuid)": set(),
    "trust_unpaid_bills(text,date)": set(),
    # Records every write as the owner: no role may call it, or write the log, on its own.
    "trust_audit()": set(),
    # Checks a payment hold as the owner, to take its lease's lock: no role calls it on its own.
    "trust_check_payment_hold()": set(),
    # Reports read trust records, so only the app and the AI; they run with the caller's own
    # read grants.
    "trust_report_three_way_reconciliation(uuid,uuid)": {"trust_app", "trust_ai_agent"},
    "trust_report_owner_statement(uuid,uuid,timestamptz,timestamptz)": {
        "trust_app",
        "trust_ai_agent",
    },
    "trust_report_rent_roll(uuid,date)": {"trust_app", "trust_ai_agent"},
    "trust_report_owner_balances(uuid,date)": {"trust_app", "trust_ai_agent"},
    "trust_report_unpaid_bills(uuid,date)": {"trust_app", "trust_ai_agent"},
    "trust_report_delinquency(uuid,date)": {"trust_app", "trust_ai_agent"},
    "trust_report_tenant_ledger(uuid,uuid,date)": {"trust_app", "trust_ai_agent"},
    "trust_report_trial_balance(uuid,date)": {"trust_app", "trust_ai_agent"},
    "trust_report_security_deposits(uuid,date)": {"trust_app", "trust_ai_agent"},
    "trust_report_general_ledger(uuid,date,date)": {"trust_app", "trust_ai_agent"},
    "trust_report_audit_log(uuid,date,date)": {"trust_app", "trust_ai_agent"},
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
    "trust_check_agreement_account()": {"PUBLIC"},
    "trust_check_bill_account()": {"PUBLIC"},
    "trust_check_work_order()": {"PUBLIC"},
    "trust_check_work_order_step()": {"PUBLIC"},
    "trust_check_hold_release()": {"PUBLIC"},
    "trust_check_hold_allowance()": {"PUBLIC"},
    "trust_refuse_moving_opened_at()": {"PUBLIC"},
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
    "trust_reverse_payment(uuid,uuid,text,text,numeric)",
    "trust_post_management_fee(uuid,text,date,date,timestamptz)",
    "trust_post_leasing_fee(uuid,uuid,text,timestamptz)",
    "trust_draw_owner(uuid,text,text,numeric,timestamptz)",
    "trust_set_aside_bill(uuid,uuid,timestamptz)",
    "trust_pay_bill(uuid,uuid,timestamptz)",
    "trust_assess_late_fees(uuid,date)",
    "trust_post_opening_balances(uuid,uuid,date,jsonb,numeric,text)",
    "trust_audit()",  # writes the audit log, which no other role may write
    "trust_check_payment_hold()",  # locks the hold's lease, which the app can't
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
def test_ai_role_reads_only_what_the_app_reads(conn, table):
    assert granted(conn, "trust_ai_agent", table) == TABLES[table] & {"SELECT"}


def test_ai_role_holds_no_column_grant(conn):
    # trust_app renames records through column grants; the AI role must hold none, since a
    # column-level UPDATE or INSERT is a write the table-level pins above don't show.
    grants = conn.execute(
        """
        SELECT c.relname, a.attname, x.privilege_type
        FROM pg_attribute a
        JOIN pg_class c ON c.oid = a.attrelid, aclexplode(a.attacl) x
        WHERE c.relnamespace = 'public'::regnamespace
          AND x.grantee = 'trust_ai_agent'::regrole
        """
    ).fetchall()
    assert grants == []


@pytest.mark.parametrize("signature", FUNCTIONS)
def test_ai_role_calls_only_the_reports(conn, signature):
    # PUBLIC's functions are the id helpers, a balance check and trigger functions, none of
    # which writes; everything else it may call is a report.
    allowed = conn.execute(
        "SELECT has_function_privilege('trust_ai_agent', %s, 'EXECUTE')", (signature,)
    ).fetchone()[0]
    assert allowed is (signature.startswith("trust_report_") or "PUBLIC" in FUNCTIONS[signature])


def test_the_function_that_writes_the_ledger_keeps_its_search_path_pinned(conn):
    # pgledger_create_transfers does every ledger write (trust_post_transfers calls it). It runs
    # as its caller since 20261009000007 and keeps the search_path 20261009000002 pinned, so
    # whoever calls it, the tables it writes are the schema's own, never look-alikes earlier on
    # a session's search_path.
    config = conn.execute(
        "SELECT proconfig FROM pg_proc WHERE oid = %s::regprocedure",
        ("pgledger_create_transfers(transfer_request[],timestamptz,jsonb)",),
    ).fetchone()[0]
    assert "search_path=public, pg_temp" in (config or [])


def test_ai_role_has_no_powers_beyond_its_grants(conn):
    attributes = conn.execute(
        "SELECT rolsuper, rolcreaterole, rolcreatedb, rolcanlogin, rolreplication, rolbypassrls"
        " FROM pg_roles WHERE rolname = 'trust_ai_agent'"
    ).fetchone()
    assert attributes == (False,) * 6
    # Not a member of trust_app or any other role whose grants it would inherit.
    memberships = conn.execute(
        "SELECT roleid::regrole::text FROM pg_auth_members WHERE member = 'trust_ai_agent'::regrole"
    ).fetchall()
    assert memberships == []


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
