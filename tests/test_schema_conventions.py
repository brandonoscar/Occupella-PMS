"""The schema conventions in CLAUDE.md, read from the database a migration actually built.

Each rule is checked against the catalog, not the migration text, so a new migration is held to
it the moment it runs. tests/test_privileges.py does the same for grants.
"""

import os

from helpers import NOT_FROM_AN_EXTENSION

# Copied verbatim from pgledger and the ULID helpers (THIRD_PARTY_NOTICES.md). Everything else
# the migrations add is ours and is named trust_*.
UPSTREAM = {
    "relations": {
        "pgledger_accounts",
        "pgledger_transfers",
        "pgledger_entries",
        "pgledger_accounts_view",
        "pgledger_transfers_view",
        "pgledger_entries_view",
        "schema_migrations",  # dbmate's
    },
    "functions": {
        "parse_ulid",
        "format_ulid",
        "ulid_to_uuid",
        "uuid_to_ulid",
        "pgledger_uuidv7",
        "pgledger_uuidv7_exists",
        "pgledger_uuidv7_microsecond",
        "pgledger_generate_id",
        "pgledger_check_account_balance_constraints",
        "pgledger_create_account",
        "pgledger_create_transfer",
        "pgledger_create_transfers",
    },
    "types": {"transfer_request"},
}
# Only pgcrypto may be added (CLAUDE.md: nothing a Supabase project couldn't run). plpgsql is
# built in; plpgsql_check is loaded by tools/sql_coverage.py into the coverage job's test
# database only, never by a migration.
EXTENSIONS = {"plpgsql", "pgcrypto"}
COVERAGE_ONLY_EXTENSIONS = {"plpgsql_check"}


def names(conn, query):
    return {row[0] for row in conn.execute(query)}


def test_every_table_we_add_carries_pmc_id(conn):
    # Our tables map to Occupella's companies.id through pmc_id at merge time.
    missing = conn.execute(
        """
        SELECT c.relname
        FROM pg_class c
        LEFT JOIN pg_attribute a
          ON a.attrelid = c.oid AND a.attname = 'pmc_id' AND NOT a.attisdropped
        WHERE c.relnamespace = 'public'::regnamespace AND c.relkind IN ('r', 'p')
          AND c.relname LIKE 'trust\\_%'
          AND (a.attname IS NULL OR a.atttypid <> 'uuid'::regtype OR NOT a.attnotnull)
        """
    ).fetchall()
    assert missing == [], "every trust_ table needs `pmc_id uuid NOT NULL`"


def test_everything_is_upstream_or_named_trust(conn):
    relations = names(
        conn,
        f"""
        SELECT c.relname FROM pg_class c
        WHERE c.relnamespace = 'public'::regnamespace
          AND c.relkind IN ('r', 'p', 'v', 'm', 'S', 'f')
          AND {NOT_FROM_AN_EXTENSION.format(catalog="'pg_class'", oid="c.oid")}
        """,
    )
    functions = names(
        conn,
        f"""
        SELECT p.proname FROM pg_proc p
        WHERE p.pronamespace = 'public'::regnamespace
          AND {NOT_FROM_AN_EXTENSION.format(catalog="'pg_proc'", oid="p.oid")}
        """,
    )
    # Standalone types (composite, enum, domain), not the row type every table has.
    types = names(
        conn,
        f"""
        SELECT t.typname FROM pg_type t
        LEFT JOIN pg_class c ON c.oid = t.typrelid
        WHERE t.typnamespace = 'public'::regnamespace AND t.typtype IN ('c', 'e', 'd')
          AND (c.oid IS NULL OR c.relkind = 'c')
          AND {NOT_FROM_AN_EXTENSION.format(catalog="'pg_type'", oid="t.oid")}
        """,
    )
    triggers = names(conn, "SELECT tgname FROM pg_trigger WHERE NOT tgisinternal")

    def misnamed(found, upstream):
        return sorted(name for name in found - upstream if not name.startswith("trust_"))

    assert misnamed(relations, UPSTREAM["relations"]) == []
    assert misnamed(functions, UPSTREAM["functions"]) == []
    assert misnamed(types, UPSTREAM["types"]) == []
    assert misnamed(triggers, set()) == []


def test_no_foreign_key_cascades(conn):
    # Deleting or re-keying a parent row must never reach into trust records.
    cascading = conn.execute(
        """
        SELECT conrelid::regclass::text, conname, confdeltype, confupdtype
        FROM pg_constraint
        WHERE contype = 'f' AND connamespace = 'public'::regnamespace
          AND (confdeltype IN ('c', 'n', 'd') OR confupdtype IN ('c', 'n', 'd'))
        """
    ).fetchall()
    assert cascading == [], "use the default NO ACTION (or RESTRICT) on every foreign key"


def test_migrations_load_only_allowed_extensions(conn):
    loaded = names(conn, "SELECT extname FROM pg_extension")
    allowed = set(EXTENSIONS)
    if os.environ.get("PMS_SQL_COVERAGE_OUT"):
        allowed |= COVERAGE_ONLY_EXTENSIONS
    assert loaded - allowed == set()
