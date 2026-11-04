-- migrate:up
-- How to pay us (the founder's review of 11/03, item 4b): what a company tells its tenants and
-- owners about paying it (by check to an address, at the office...), in its own words. The
-- tenant's Pay online and the owner's Add money show it when the company takes no payment online,
-- in place of a line the server wrote. Append-only, the latest row holding (a null body clears
-- it), naming who changed it; the AI role never reads it, as it never reads the company's profile.

-- On a live database, give up rather than queue behind a long transaction while holding locks.
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '60s';

CREATE TABLE trust_payment_instructions (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    pmc_id uuid NOT NULL REFERENCES trust_pmcs (pmc_id),
    body text CHECK (body IS NULL OR (body ~ '\S' AND length(body) <= 2000)),
    recorded_by uuid,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    FOREIGN KEY (pmc_id, recorded_by) REFERENCES trust_staff (pmc_id, id)
);
CREATE INDEX trust_payment_instructions_pmc ON trust_payment_instructions (pmc_id, created_at);
CREATE INDEX trust_payment_instructions_recorded_by
ON trust_payment_instructions (pmc_id, recorded_by);

CREATE TRIGGER trust_recorded_by BEFORE INSERT ON trust_payment_instructions
FOR EACH ROW EXECUTE FUNCTION trust_name_recorder();
CREATE TRIGGER trust_append_only
BEFORE UPDATE OR DELETE OR TRUNCATE ON trust_payment_instructions
FOR EACH STATEMENT EXECUTE FUNCTION trust_refuse_ledger_rewrite();
CREATE TRIGGER trust_audit AFTER INSERT OR UPDATE OR DELETE ON trust_payment_instructions
FOR EACH ROW EXECUTE FUNCTION trust_audit();

REVOKE ALL ON trust_payment_instructions FROM PUBLIC;
GRANT SELECT, INSERT ON trust_payment_instructions TO trust_app;

-- migrate:down
SET LOCAL lock_timeout = '5s';
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM trust_payment_instructions) THEN
        RAISE EXCEPTION 'refusing to undo how to pay us: a company has written it';
    END IF;
END;
$$;
-- squawk-ignore ban-drop-table
DROP TABLE trust_payment_instructions;
