-- migrate:up
-- Prepaid rent used by itself (the founder's review q031: an opt-in each company turns on). A
-- tenant's payment over what the lease owes is held as their prepaid rent (src/pms/api/rent.py);
-- staff use it with Use prepaid rent. A company that turns this on has the nightly run, and an
-- admin's Run rent now, use each lease's tenants' prepaid rent for what the lease owes once its new
-- charges post, as Use prepaid rent does (rent first, oldest first, into the owners' accounts by
-- their shares), once a day per lease. A lease on a payment hold, or at a property whose owner's
-- account staff must choose, is left for staff.
--
--   trust_prepaid_rent_settings   whether it is on; append-only, the latest row holding, naming
--                                 who changed it. Off until a company turns it on.

-- On a live database, give up rather than queue behind a long transaction while holding locks.
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '60s';

CREATE TABLE trust_prepaid_rent_settings (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    pmc_id uuid NOT NULL REFERENCES trust_pmcs (pmc_id),
    automatic boolean NOT NULL,
    recorded_by uuid,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    FOREIGN KEY (pmc_id, recorded_by) REFERENCES trust_staff (pmc_id, id)
);
CREATE INDEX trust_prepaid_rent_settings_pmc ON trust_prepaid_rent_settings (pmc_id, created_at);
CREATE INDEX trust_prepaid_rent_settings_recorded_by
ON trust_prepaid_rent_settings (pmc_id, recorded_by);

CREATE TRIGGER trust_recorded_by BEFORE INSERT ON trust_prepaid_rent_settings
FOR EACH ROW EXECUTE FUNCTION trust_name_recorder();
CREATE TRIGGER trust_append_only
BEFORE UPDATE OR DELETE OR TRUNCATE ON trust_prepaid_rent_settings
FOR EACH STATEMENT EXECUTE FUNCTION trust_refuse_ledger_rewrite();
CREATE TRIGGER trust_audit AFTER INSERT OR UPDATE OR DELETE ON trust_prepaid_rent_settings
FOR EACH ROW EXECUTE FUNCTION trust_audit();

REVOKE ALL ON trust_prepaid_rent_settings FROM PUBLIC;
GRANT SELECT, INSERT ON trust_prepaid_rent_settings TO trust_app;
GRANT SELECT ON trust_prepaid_rent_settings TO trust_ai_agent;


-- migrate:down
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '60s';

-- squawk-ignore ban-drop-table
DROP TABLE trust_prepaid_rent_settings;
