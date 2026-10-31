-- migrate:up
-- API keys (ADR 0011, the founder 2026-10-31): software, such as an AI, reads the PMS through
-- its own API with a key an admin made in Settings, AI, and nothing else.
--
--   trust_api_keys             a key: its name, the first characters of its secret (shown in
--                              lists), the secret kept only as its SHA-256, and the admin who made
--                              it. Append-only.
--   trust_api_key_revocations  a key taken back, once, by an admin.
--
-- A request with a key runs as trust_ai_agent in its transaction (SET LOCAL ROLE): it reads what
-- that role reads and runs the reports, and holds no grant that writes, so no key moves money.
-- The database holds that line, not the app. trust_app may SET ROLE to it, never inherit it: the
-- app's own grants stay its own, and the AI role is a member of nothing. Like logins and links,
-- the AI role reads neither table.

-- On a live database, give up rather than queue behind a long transaction while holding locks.
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '60s';

CREATE TABLE trust_api_keys (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    pmc_id uuid NOT NULL REFERENCES trust_pmcs (pmc_id),
    name text NOT NULL CHECK (name = btrim(name) AND char_length(name) BETWEEN 1 AND 60),
    -- "occ_" and the secret's next eight characters: enough to tell keys apart, never to use one.
    prefix text NOT NULL CHECK (prefix ~ '^occ_[A-Za-z0-9_-]{8}$'),
    secret_sha256 bytea NOT NULL UNIQUE CHECK (length(secret_sha256) = 32),
    -- The admin who made it: the request names them (the command line names nobody, and makes none).
    recorded_by uuid NOT NULL,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    UNIQUE (pmc_id, id),
    FOREIGN KEY (pmc_id, recorded_by) REFERENCES trust_staff (pmc_id, id)
);
CREATE INDEX trust_api_keys_recorded_by ON trust_api_keys (pmc_id, recorded_by);

CREATE TABLE trust_api_key_revocations (
    key_id uuid PRIMARY KEY,
    pmc_id uuid NOT NULL,
    recorded_by uuid NOT NULL,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    FOREIGN KEY (pmc_id, key_id) REFERENCES trust_api_keys (pmc_id, id),
    FOREIGN KEY (pmc_id, recorded_by) REFERENCES trust_staff (pmc_id, id)
);
CREATE INDEX trust_api_key_revocations_recorded_by ON trust_api_key_revocations (
    pmc_id, recorded_by
);

-- Only an admin makes a key or takes one back.
CREATE FUNCTION trust_check_api_key_admin() RETURNS trigger
LANGUAGE plpgsql
SET search_path = public, pg_temp
AS $$
BEGIN
    PERFORM trust_require_role(
        NEW.pmc_id,
        NEW.recorded_by,
        'admin',
        CASE TG_TABLE_NAME WHEN 'trust_api_keys' THEN 'make an API key'
            ELSE 'take an API key back' END
    );
    RETURN NEW;
END;
$$;
REVOKE ALL ON FUNCTION trust_check_api_key_admin() FROM PUBLIC;

-- Each table: who recorded it (trust_recorded_by, then the admin check, named to fire after it),
-- append-only, audited.
CREATE TRIGGER trust_recorded_by BEFORE INSERT ON trust_api_keys
FOR EACH ROW EXECUTE FUNCTION trust_name_recorder();
CREATE TRIGGER trust_recorded_by_admin BEFORE INSERT ON trust_api_keys
FOR EACH ROW EXECUTE FUNCTION trust_check_api_key_admin();
CREATE TRIGGER trust_append_only
BEFORE UPDATE OR DELETE OR TRUNCATE ON trust_api_keys
FOR EACH STATEMENT EXECUTE FUNCTION trust_refuse_ledger_rewrite();
CREATE TRIGGER trust_audit AFTER INSERT OR UPDATE OR DELETE ON trust_api_keys
FOR EACH ROW EXECUTE FUNCTION trust_audit();

REVOKE ALL ON trust_api_keys FROM PUBLIC;
GRANT SELECT, INSERT ON trust_api_keys TO trust_app;

CREATE TRIGGER trust_recorded_by BEFORE INSERT ON trust_api_key_revocations
FOR EACH ROW EXECUTE FUNCTION trust_name_recorder();
CREATE TRIGGER trust_recorded_by_admin BEFORE INSERT ON trust_api_key_revocations
FOR EACH ROW EXECUTE FUNCTION trust_check_api_key_admin();
CREATE TRIGGER trust_append_only
BEFORE UPDATE OR DELETE OR TRUNCATE ON trust_api_key_revocations
FOR EACH STATEMENT EXECUTE FUNCTION trust_refuse_ledger_rewrite();
CREATE TRIGGER trust_audit AFTER INSERT OR UPDATE OR DELETE ON trust_api_key_revocations
FOR EACH ROW EXECUTE FUNCTION trust_audit();

REVOKE ALL ON trust_api_key_revocations FROM PUBLIC;
GRANT SELECT, INSERT ON trust_api_key_revocations TO trust_app;

-- A key's request: the app looks the key up as itself, then SET LOCAL ROLE trust_ai_agent for the
-- rest of the request's transaction. INHERIT FALSE: trust_app never holds the AI role's grants
-- (they are a subset of its own anyway), it only becomes it. Roles belong to the whole server, so
-- this holds for every database on it, as the roles themselves do.
GRANT trust_ai_agent TO trust_app WITH INHERIT FALSE, SET TRUE;

-- migrate:down
DO $$
BEGIN
    RAISE EXCEPTION 'api_keys cannot be rolled back: keys and their revocations are records, and '
        'the role membership belongs to the whole server';
END;
$$;
