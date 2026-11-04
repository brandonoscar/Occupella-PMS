-- migrate:up
-- An owner's approval of a big bill is signed (the founder's review of 11/03, item 1): over the
-- amount the company sets (`trust_owner_approval_settings`, append-only, the latest row holding,
-- naming who changed it; $5,000.00 without one), the owner types their full name to approve in
-- the portal, and the approval keeps what they typed (`trust_bill_approvals.signed_name`). The
-- API checks the name against the owner's own; who approves always comes from the session.

-- On a live database, give up rather than queue behind a long transaction while holding locks.
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '60s';

CREATE TABLE trust_owner_approval_settings (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    pmc_id uuid NOT NULL REFERENCES trust_pmcs (pmc_id),
    -- A bill (with its markup) over this amount takes the owner's typed name to approve.
    sign_over numeric(12, 2) NOT NULL CHECK (sign_over >= 0),
    recorded_by uuid,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    FOREIGN KEY (pmc_id, recorded_by) REFERENCES trust_staff (pmc_id, id)
);
CREATE INDEX trust_owner_approval_settings_pmc
ON trust_owner_approval_settings (pmc_id, created_at);
CREATE INDEX trust_owner_approval_settings_recorded_by
ON trust_owner_approval_settings (pmc_id, recorded_by);

CREATE TRIGGER trust_recorded_by BEFORE INSERT ON trust_owner_approval_settings
FOR EACH ROW EXECUTE FUNCTION trust_name_recorder();
CREATE TRIGGER trust_append_only
BEFORE UPDATE OR DELETE OR TRUNCATE ON trust_owner_approval_settings
FOR EACH STATEMENT EXECUTE FUNCTION trust_refuse_ledger_rewrite();
CREATE TRIGGER trust_audit AFTER INSERT OR UPDATE OR DELETE ON trust_owner_approval_settings
FOR EACH ROW EXECUTE FUNCTION trust_audit();

REVOKE ALL ON trust_owner_approval_settings FROM PUBLIC;
GRANT SELECT, INSERT ON trust_owner_approval_settings TO trust_app;
GRANT SELECT ON trust_owner_approval_settings TO trust_ai_agent;

-- What the owner typed to approve, when the bill took it.
ALTER TABLE trust_bill_approvals ADD COLUMN signed_name text;
ALTER TABLE trust_bill_approvals ADD CONSTRAINT trust_bill_approvals_signed_name
    CHECK (signed_name IS NULL OR (signed_name ~ '\S' AND length(signed_name) <= 200))
    NOT VALID;
-- Validated in the same transaction (dbmate runs each migration in one): the new column is null
-- in every row, so it reads the table once, under the 5 s lock timeout above.
-- squawk-ignore constraint-missing-not-valid
ALTER TABLE trust_bill_approvals VALIDATE CONSTRAINT trust_bill_approvals_signed_name;

-- migrate:down
SET LOCAL lock_timeout = '5s';
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM trust_bill_approvals WHERE signed_name IS NOT NULL)
       OR EXISTS (SELECT 1 FROM trust_owner_approval_settings) THEN
        RAISE EXCEPTION 'refusing to undo signed approvals: owners have signed some';
    END IF;
END;
$$;
-- squawk-ignore ban-drop-constraint
ALTER TABLE trust_bill_approvals DROP CONSTRAINT trust_bill_approvals_signed_name;
-- squawk-ignore ban-drop-column
ALTER TABLE trust_bill_approvals DROP COLUMN signed_name;
-- squawk-ignore ban-drop-table
DROP TABLE trust_owner_approval_settings;
