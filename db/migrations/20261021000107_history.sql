-- migrate:up
-- The old system's past transactions (the founder's review q129), kept beside the ledger.
--
-- A company switching brings over balances at the cutover (migration 0029), not the years before
-- it. Now it may bring those too, as history: each tenant's, owner's and vendor's past lines as
-- the old system printed them, from CSV files at Settings, Import (src/pms/api/switching.py).
--
--   trust_history_files   a file brought in: whose history it holds (tenants', owners' or
--                         vendors'), its SHA-256 (a file goes in once), its rows, who brought it in.
--   trust_history_lines   its lines, each one person's: a tenant's (with the unit as the old
--                         system named it), an owner's property account's or a vendor's; the day,
--                         the old system's word for it, what it was, the amount as printed there
--                         and its reference.
--
-- History is never posted: nothing in it touches the ledger, a balance, a charge or a report, so
-- the books start at the cutover as they did. Staff read it under "Before the switch" on each
-- person's page. Append-only; the AI role reads it as it reads the ledger.

-- On a live database, give up rather than queue behind a long transaction while holding locks.
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '60s';

CREATE TABLE trust_history_files (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    pmc_id uuid NOT NULL REFERENCES trust_pmcs (pmc_id),
    kind text NOT NULL CHECK (kind IN ('tenant', 'owner', 'vendor')),
    file_sha256 bytea NOT NULL CHECK (length(file_sha256) = 32),
    -- A CSV file's lines, far below an integer's reach.
    -- squawk-ignore prefer-bigint-over-int
    line_count integer NOT NULL CHECK (line_count > 0),
    recorded_by uuid,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    UNIQUE (pmc_id, file_sha256),
    UNIQUE (pmc_id, id),
    FOREIGN KEY (pmc_id, recorded_by) REFERENCES trust_staff (pmc_id, id)
);
CREATE INDEX trust_history_files_recorded_by ON trust_history_files (pmc_id, recorded_by);

CREATE TABLE trust_history_lines (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    pmc_id uuid NOT NULL REFERENCES trust_pmcs (pmc_id),
    file_id uuid NOT NULL,
    -- squawk-ignore prefer-bigint-over-int
    line_no integer NOT NULL CHECK (line_no > 0),
    tenant_id uuid,
    owner_account text REFERENCES trust_ledger_accounts (ledger_account_id),
    vendor_id uuid,
    posted_on date NOT NULL,
    kind text NOT NULL CHECK (kind ~ '\S' AND length(kind) <= 50),
    description text NOT NULL CHECK (description ~ '\S' AND length(description) <= 200),
    amount numeric NOT NULL CHECK (amount = round(amount, 2) AND abs(amount) < 1000000000),
    reference text CHECK (reference ~ '\S' AND length(reference) <= 100),
    unit text CHECK (unit ~ '\S' AND length(unit) <= 200),
    CHECK (num_nonnulls(tenant_id, owner_account, vendor_id) = 1),
    CHECK (unit IS NULL OR tenant_id IS NOT NULL),
    UNIQUE (file_id, line_no),
    FOREIGN KEY (pmc_id, file_id) REFERENCES trust_history_files (pmc_id, id),
    FOREIGN KEY (pmc_id, tenant_id) REFERENCES trust_tenants (pmc_id, id),
    FOREIGN KEY (pmc_id, vendor_id) REFERENCES trust_vendors (pmc_id, id)
);
CREATE INDEX trust_history_lines_tenant ON trust_history_lines (pmc_id, tenant_id, posted_on);
CREATE INDEX trust_history_lines_owner ON trust_history_lines (owner_account, posted_on);
CREATE INDEX trust_history_lines_vendor ON trust_history_lines (pmc_id, vendor_id, posted_on);

-- A line is of its file's kind of person, an owner's line is of an owner's property account of
-- the PMC, and nothing is dated after the day it was brought in.
CREATE FUNCTION trust_check_history_line() RETURNS trigger
LANGUAGE plpgsql
SET search_path = public, pg_temp
AS $$
DECLARE
    v_kind text;
BEGIN
    SELECT f.kind INTO v_kind FROM trust_history_files AS f
    WHERE f.pmc_id = NEW.pmc_id AND f.id = NEW.file_id;
    IF v_kind IS DISTINCT FROM (CASE WHEN NEW.tenant_id IS NOT NULL THEN 'tenant'
                                     WHEN NEW.owner_account IS NOT NULL THEN 'owner'
                                     ELSE 'vendor' END) THEN
        RAISE EXCEPTION 'trust: line % of a file of % history is not a %''s', NEW.line_no,
            v_kind, v_kind
            USING ERRCODE = 'check_violation';
    END IF;
    IF NEW.owner_account IS NOT NULL AND NOT EXISTS (
        SELECT 1 FROM trust_ledger_accounts AS a
        WHERE a.ledger_account_id = NEW.owner_account AND a.pmc_id = NEW.pmc_id
          AND a.kind = 'owner_property'
    ) THEN
        RAISE EXCEPTION 'trust: % is not an owner''s property account of PMC %',
            NEW.owner_account, NEW.pmc_id
            USING ERRCODE = 'check_violation';
    END IF;
    IF NEW.posted_on > (now() AT TIME ZONE 'UTC')::date THEN
        RAISE EXCEPTION 'trust: line % is dated %, which has not come yet', NEW.line_no,
            to_char(NEW.posted_on, 'MM/DD/YYYY')
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER trust_check_history_line BEFORE INSERT ON trust_history_lines
FOR EACH ROW EXECUTE FUNCTION trust_check_history_line();

CREATE TRIGGER trust_recorded_by BEFORE INSERT ON trust_history_files
FOR EACH ROW EXECUTE FUNCTION trust_name_recorder();

CREATE TRIGGER trust_append_only
BEFORE UPDATE OR DELETE OR TRUNCATE ON trust_history_files
FOR EACH STATEMENT EXECUTE FUNCTION trust_refuse_ledger_rewrite();
CREATE TRIGGER trust_append_only
BEFORE UPDATE OR DELETE OR TRUNCATE ON trust_history_lines
FOR EACH STATEMENT EXECUTE FUNCTION trust_refuse_ledger_rewrite();
CREATE TRIGGER trust_audit AFTER INSERT OR UPDATE OR DELETE ON trust_history_files
FOR EACH ROW EXECUTE FUNCTION trust_audit();
CREATE TRIGGER trust_audit AFTER INSERT OR UPDATE OR DELETE ON trust_history_lines
FOR EACH ROW EXECUTE FUNCTION trust_audit();

REVOKE ALL ON trust_history_files FROM PUBLIC;
REVOKE ALL ON trust_history_lines FROM PUBLIC;
GRANT SELECT, INSERT ON trust_history_files TO trust_app;
GRANT SELECT, INSERT ON trust_history_lines TO trust_app;
GRANT SELECT ON trust_history_files TO trust_ai_agent;
GRANT SELECT ON trust_history_lines TO trust_ai_agent;
REVOKE ALL ON FUNCTION trust_check_history_line() FROM PUBLIC;

-- migrate:down
DO $$
BEGIN
    RAISE EXCEPTION 'history cannot be rolled back: what was brought over is a record';
END;
$$;
