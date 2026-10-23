-- migrate:up
-- Balance notices (the founder, 2026-10-23, lane 2's first item): from the rent roll or the
-- delinquency report, staff pick leases that owe and email each of their tenants a balance notice
-- made from one of the company's own notice templates (migration 0119), listing what is open by
-- charge. It is a reminder, not the legal notice for nonpayment: it says so, and the PMS refuses
-- a template with pay-or-quit or eviction words for it (src/pms/api/balance_notices.py).
--
--   trust_balance_notice_runs  one send: the template version it came from, the day the notices
--                              are dated, naming who sent them.
--   trust_balance_notices      a notice of the run for a lease (the trust_notices row, as filled
--                              in, so its PDF reads the same whenever it is printed) and what the
--                              lease owed when it was made; once per lease in a run.
--   trust_emails.notice_id     each tenant's email of it: a staff member's message (the outbox's
--                              rules for what staff write hold as they are) that also names its
--                              notice, once per notice and tenant, so it shows on the tenant's
--                              Email tab as a balance notice.
-- Runs and notices are append-only and audited; emails are not audited (migration 0042). The AI
-- role reads none of it, as it reads no notice.

-- On a live database, give up rather than queue behind a long transaction while holding locks.
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '60s';

CREATE TABLE trust_balance_notice_runs (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    pmc_id uuid NOT NULL REFERENCES trust_pmcs (pmc_id),
    template_version_id uuid NOT NULL,
    dated_on date NOT NULL,
    recorded_by uuid,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    UNIQUE (pmc_id, id),
    FOREIGN KEY (pmc_id, template_version_id)
        REFERENCES trust_notice_template_versions (pmc_id, id),
    FOREIGN KEY (pmc_id, recorded_by) REFERENCES trust_staff (pmc_id, id)
);
CREATE INDEX trust_balance_notice_runs_pmc ON trust_balance_notice_runs (pmc_id, created_at);
CREATE INDEX trust_balance_notice_runs_version
ON trust_balance_notice_runs (pmc_id, template_version_id);
CREATE INDEX trust_balance_notice_runs_recorded_by
ON trust_balance_notice_runs (pmc_id, recorded_by);

CREATE TABLE trust_balance_notices (
    notice_id uuid PRIMARY KEY REFERENCES trust_notices (id),
    pmc_id uuid NOT NULL REFERENCES trust_pmcs (pmc_id),
    run_id uuid NOT NULL,
    lease_id uuid NOT NULL,
    -- What the lease owed when the notice was made: a notice goes only to a lease that owes.
    owed numeric NOT NULL CHECK (owed > 0 AND owed = round(owed, 2)),
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    UNIQUE (run_id, lease_id),
    FOREIGN KEY (pmc_id, run_id) REFERENCES trust_balance_notice_runs (pmc_id, id),
    FOREIGN KEY (pmc_id, lease_id) REFERENCES trust_leases (pmc_id, id)
);
CREATE INDEX trust_balance_notices_lease ON trust_balance_notices (pmc_id, lease_id, created_at);

-- A run's notice is the company's notice for that lease, made from the run's template version on
-- the run's day.
CREATE FUNCTION trust_check_balance_notice() RETURNS trigger
LANGUAGE plpgsql
SET search_path = public, pg_temp
AS $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM trust_notices AS n
        JOIN trust_balance_notice_runs AS r ON r.id = NEW.run_id
        WHERE n.id = NEW.notice_id AND n.pmc_id = NEW.pmc_id AND n.lease_id = NEW.lease_id
          AND n.template_version_id = r.template_version_id AND n.dated_on = r.dated_on
    ) THEN
        RAISE EXCEPTION 'trust: a balance notice is its run''s notice for its lease'
            USING ERRCODE = 'check_violation';
    END IF;
    NEW.created_at := clock_timestamp();
    RETURN NEW;
END;
$$;
REVOKE ALL ON FUNCTION trust_check_balance_notice() FROM PUBLIC;

CREATE TRIGGER trust_check_balance_notice BEFORE INSERT ON trust_balance_notices
FOR EACH ROW EXECUTE FUNCTION trust_check_balance_notice();
CREATE TRIGGER trust_recorded_by BEFORE INSERT ON trust_balance_notice_runs
FOR EACH ROW EXECUTE FUNCTION trust_name_recorder();
CREATE TRIGGER trust_append_only
BEFORE UPDATE OR DELETE OR TRUNCATE ON trust_balance_notice_runs
FOR EACH STATEMENT EXECUTE FUNCTION trust_refuse_ledger_rewrite();
CREATE TRIGGER trust_append_only
BEFORE UPDATE OR DELETE OR TRUNCATE ON trust_balance_notices
FOR EACH STATEMENT EXECUTE FUNCTION trust_refuse_ledger_rewrite();
CREATE TRIGGER trust_audit AFTER INSERT OR UPDATE OR DELETE ON trust_balance_notice_runs
FOR EACH ROW EXECUTE FUNCTION trust_audit();
CREATE TRIGGER trust_audit AFTER INSERT OR UPDATE OR DELETE ON trust_balance_notices
FOR EACH ROW EXECUTE FUNCTION trust_audit();

REVOKE ALL ON trust_balance_notice_runs, trust_balance_notices FROM PUBLIC;
GRANT SELECT, INSERT ON trust_balance_notice_runs, trust_balance_notices TO trust_app;

-- Each tenant's email of a notice: a message from the staff member who sent it, to that tenant.
-- Every email so far names no notice, so the check and the key hold for them; both are added NOT
-- VALID and checked for every new email.
ALTER TABLE trust_emails ADD COLUMN notice_id uuid;
ALTER TABLE trust_emails ADD CONSTRAINT trust_emails_notice_check
CHECK (notice_id IS NULL OR (kind = 'message' AND tenant_id IS NOT NULL)) NOT VALID;
-- squawk-ignore adding-foreign-key-constraint
ALTER TABLE trust_emails ADD CONSTRAINT trust_emails_notice_id_fkey
FOREIGN KEY (notice_id) REFERENCES trust_balance_notices (notice_id) NOT VALID;
-- Once per notice and tenant. No email names a notice yet, so this builds in moments;
-- CONCURRENTLY can't run inside the migration's transaction.
-- squawk-ignore require-concurrent-index-creation
CREATE UNIQUE INDEX trust_emails_one_balance_notice ON trust_emails (notice_id, tenant_id)
WHERE notice_id IS NOT NULL;


-- migrate:down
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '60s';

-- squawk-ignore ban-drop-column
ALTER TABLE trust_emails DROP COLUMN notice_id;
-- squawk-ignore ban-drop-table
DROP TABLE trust_balance_notices;
-- squawk-ignore ban-drop-table
DROP TABLE trust_balance_notice_runs;
-- squawk-ignore ban-drop-function
DROP FUNCTION trust_check_balance_notice();
