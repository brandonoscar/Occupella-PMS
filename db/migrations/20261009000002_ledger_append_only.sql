-- migrate:up
-- The ledger is append-only. A transfer and its entries are history: a mistake is corrected by
-- a new reversing transfer, never by editing or deleting rows. Two locks enforce it:
--   1. triggers refuse UPDATE, DELETE and TRUNCATE on pgledger_transfers and pgledger_entries
--      for every role, the table owner included;
--   2. the app role, trust_app, has no write grant on any pgledger table. It reads them, and it
--      writes only by calling pgledger_create_transfer(s), which run as the table owner.
-- pgledger_accounts is not append-only: pgledger updates balances in place.

CREATE FUNCTION trust_refuse_ledger_rewrite() RETURNS trigger
LANGUAGE plpgsql
SET search_path = public, pg_temp
AS $$
BEGIN
    RAISE EXCEPTION 'trust: % is append-only, % refused', TG_TABLE_NAME, TG_OP
        USING ERRCODE = 'restrict_violation',
              HINT = 'Correct a mistake with a new reversing transfer.';
END;
$$;

-- Statement-level, so an UPDATE or DELETE is refused even when it matches no rows, and TRUNCATE
-- (which skips row triggers) is covered too.
CREATE TRIGGER trust_append_only
BEFORE UPDATE OR DELETE OR TRUNCATE ON pgledger_transfers
FOR EACH STATEMENT EXECUTE FUNCTION trust_refuse_ledger_rewrite();

CREATE TRIGGER trust_append_only
BEFORE UPDATE OR DELETE OR TRUNCATE ON pgledger_entries
FOR EACH STATEMENT EXECUTE FUNCTION trust_refuse_ledger_rewrite();

-- The app role. NOLOGIN: a deployment grants it to the login role the app connects as.
-- Roles belong to the whole server, not one database, so create it only if it is missing.
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'trust_app') THEN
        CREATE ROLE trust_app NOLOGIN;
    END IF;
END;
$$;

REVOKE ALL ON
    pgledger_accounts, pgledger_transfers, pgledger_entries,
    pgledger_accounts_view, pgledger_transfers_view, pgledger_entries_view
FROM PUBLIC;

GRANT SELECT ON
    pgledger_accounts, pgledger_transfers, pgledger_entries,
    pgledger_accounts_view, pgledger_transfers_view, pgledger_entries_view
TO trust_app;

-- Spelled out so a reader doesn't have to infer it: no direct writes for the app role.
REVOKE INSERT, UPDATE, DELETE, TRUNCATE ON
    pgledger_accounts, pgledger_transfers, pgledger_entries
FROM trust_app;

-- pgledger_create_transfers does the writes. Running it as its owner lets trust_app post a
-- transfer without holding any write grant itself. A fixed search_path stops a caller from
-- swapping in look-alike tables.
ALTER FUNCTION pgledger_create_transfers(transfer_request [], timestamptz, jsonb)
SECURITY DEFINER SET search_path = public, pg_temp;

-- Only trust_app may post transfers. Accounts are opened through trust_open_ledger_account
-- (next migration), so nobody but the owner calls pgledger_create_account directly.
REVOKE EXECUTE ON FUNCTION
    pgledger_create_account(text, text, boolean, boolean, jsonb),
    pgledger_create_transfer(text, text, numeric, timestamptz, jsonb),
    pgledger_create_transfers(transfer_request []),
    pgledger_create_transfers(transfer_request [], timestamptz, jsonb)
FROM PUBLIC;

GRANT EXECUTE ON FUNCTION
    pgledger_create_transfer(text, text, numeric, timestamptz, jsonb),
    pgledger_create_transfers(transfer_request []),
    pgledger_create_transfers(transfer_request [], timestamptz, jsonb)
TO trust_app;

-- migrate:down
-- Trust records are kept for years, so the ledger guards are never rolled back.
DO $$ BEGIN RAISE EXCEPTION 'ledger_append_only cannot be rolled back'; END $$;
