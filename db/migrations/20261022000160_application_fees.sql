-- migrate:up
-- Application fees (the founder, 2026-10-23: application fees among online payments). A company
-- may ask each applicant a fee, which the application's applicants pay online through the payment
-- processor once they apply (migration 0158's processor; applicants have no login, so their
-- application's status token lets them pay). The processor pays it into the company's trust bank
-- account with the rent, so the PMS records it there as the company's own fee, in the fee account
-- of the trust bank account its unit's owners keep their money in, to be taken out of trust as
-- every company fee is (migration 0059). The amount is the company's to set; no state's cap is
-- built in (docs/law.md).
--
-- trust_application_fee_settings: the fee each applicant pays, from now (0: none), append-only,
-- the latest holding, naming who. trust_application_fees: a fee payment an application started,
-- for an amount, by card or bank debit, through a processor; its steps
-- (trust_application_fee_steps): started (the processor's reference), then once succeeded (the
-- ledger transfer that recorded it) or failed (why). An application's fee is paid once. Each
-- processor event once; append-only and audited; the AI role reads none of it.

-- On a live database, give up rather than queue behind a long transaction while holding locks.
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '60s';

CREATE TABLE trust_application_fee_settings (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    pmc_id uuid NOT NULL REFERENCES trust_pmcs (pmc_id),
    -- What each applicant on an application pays.
    per_applicant numeric NOT NULL
        CHECK (per_applicant >= 0 AND per_applicant <= 1000 AND per_applicant = round(per_applicant, 2)),
    recorded_by uuid,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    FOREIGN KEY (pmc_id, recorded_by) REFERENCES trust_staff (pmc_id, id)
);
CREATE INDEX trust_application_fee_settings_pmc
ON trust_application_fee_settings (pmc_id, created_at);
CREATE INDEX trust_application_fee_settings_recorded_by
ON trust_application_fee_settings (pmc_id, recorded_by);

CREATE TABLE trust_application_fees (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    pmc_id uuid NOT NULL REFERENCES trust_pmcs (pmc_id),
    application_id uuid NOT NULL,
    amount numeric NOT NULL CHECK (amount > 0 AND amount = round(amount, 2)),
    method text NOT NULL CHECK (method IN ('card', 'bank debit')),
    processor text NOT NULL CHECK (processor IN ('fake', 'stripe')),
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    UNIQUE (pmc_id, id),
    FOREIGN KEY (pmc_id, application_id) REFERENCES trust_applications (pmc_id, id)
);
CREATE INDEX trust_application_fees_application
ON trust_application_fees (pmc_id, application_id, created_at);

CREATE TABLE trust_application_fee_steps (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    pmc_id uuid NOT NULL REFERENCES trust_pmcs (pmc_id),
    fee_id uuid NOT NULL,
    step text NOT NULL CHECK (step IN ('started', 'succeeded', 'failed')),
    reference text CHECK (char_length(reference) BETWEEN 1 AND 255),
    processor_event text UNIQUE CHECK (char_length(processor_event) BETWEEN 1 AND 255),
    transfer_id text REFERENCES pgledger_transfers (id),
    reason text CHECK (char_length(reason) BETWEEN 1 AND 500),
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    FOREIGN KEY (pmc_id, fee_id) REFERENCES trust_application_fees (pmc_id, id),
    CHECK (
        CASE step
            WHEN 'started' THEN reference IS NOT NULL AND transfer_id IS NULL AND reason IS NULL
            WHEN 'succeeded' THEN transfer_id IS NOT NULL AND reason IS NULL
            ELSE transfer_id IS NULL AND reason IS NOT NULL
        END
    )
);
CREATE UNIQUE INDEX trust_application_fee_steps_one ON trust_application_fee_steps (fee_id, step);
CREATE UNIQUE INDEX trust_application_fee_steps_reference
ON trust_application_fee_steps (reference) WHERE step = 'started';
CREATE INDEX trust_application_fee_steps_transfer ON trust_application_fee_steps (transfer_id);

-- A fee's steps come in order (started first, then one end), and an application's fee succeeds
-- once; an application's fees wait for each other.
CREATE FUNCTION trust_check_application_fee_step() RETURNS trigger
LANGUAGE plpgsql
SET search_path = public, pg_temp
AS $$
DECLARE
    v_application uuid;
    v_steps text[];
BEGIN
    SELECT f.application_id INTO v_application FROM trust_application_fees AS f
    WHERE f.pmc_id = NEW.pmc_id AND f.id = NEW.fee_id;
    PERFORM pg_advisory_xact_lock(hashtextextended('application fee ' || v_application, 0));
    SELECT coalesce(array_agg(s.step), '{}') INTO v_steps
    FROM trust_application_fee_steps AS s WHERE s.fee_id = NEW.fee_id;
    IF NEW.step = 'started' AND v_steps <> '{}' THEN
        RAISE EXCEPTION 'trust: a fee payment starts once' USING ERRCODE = 'check_violation';
    END IF;
    IF NEW.step <> 'started'
       AND (NOT 'started' = ANY (v_steps) OR v_steps && ARRAY['succeeded', 'failed']) THEN
        RAISE EXCEPTION 'trust: a fee payment that started ends once'
            USING ERRCODE = 'check_violation';
    END IF;
    IF NEW.step = 'succeeded' AND EXISTS (
        SELECT 1 FROM trust_application_fees AS f
        JOIN trust_application_fee_steps AS s ON s.fee_id = f.id AND s.step = 'succeeded'
        WHERE f.application_id = v_application
    ) THEN
        RAISE EXCEPTION 'trust: this application''s fee is paid already'
            USING ERRCODE = 'check_violation';
    END IF;
    NEW.created_at := clock_timestamp();
    RETURN NEW;
END;
$$;
REVOKE ALL ON FUNCTION trust_check_application_fee_step() FROM PUBLIC;

CREATE TRIGGER trust_recorded_by BEFORE INSERT ON trust_application_fee_settings
FOR EACH ROW EXECUTE FUNCTION trust_name_recorder();
CREATE TRIGGER trust_append_only
BEFORE UPDATE OR DELETE OR TRUNCATE ON trust_application_fee_settings
FOR EACH STATEMENT EXECUTE FUNCTION trust_refuse_ledger_rewrite();
CREATE TRIGGER trust_audit AFTER INSERT OR UPDATE OR DELETE ON trust_application_fee_settings
FOR EACH ROW EXECUTE FUNCTION trust_audit();

CREATE TRIGGER trust_append_only BEFORE UPDATE OR DELETE OR TRUNCATE ON trust_application_fees
FOR EACH STATEMENT EXECUTE FUNCTION trust_refuse_ledger_rewrite();
CREATE TRIGGER trust_audit AFTER INSERT OR UPDATE OR DELETE ON trust_application_fees
FOR EACH ROW EXECUTE FUNCTION trust_audit();

CREATE TRIGGER trust_check_application_fee_step BEFORE INSERT ON trust_application_fee_steps
FOR EACH ROW EXECUTE FUNCTION trust_check_application_fee_step();
CREATE TRIGGER trust_append_only
BEFORE UPDATE OR DELETE OR TRUNCATE ON trust_application_fee_steps
FOR EACH STATEMENT EXECUTE FUNCTION trust_refuse_ledger_rewrite();
CREATE TRIGGER trust_audit AFTER INSERT OR UPDATE OR DELETE ON trust_application_fee_steps
FOR EACH ROW EXECUTE FUNCTION trust_audit();

REVOKE ALL ON trust_application_fee_settings, trust_application_fees, trust_application_fee_steps
FROM PUBLIC;
GRANT SELECT, INSERT ON trust_application_fee_settings, trust_application_fees,
    trust_application_fee_steps TO trust_app;


-- migrate:down
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '60s';

-- squawk-ignore ban-drop-table
DROP TABLE trust_application_fee_steps;
-- squawk-ignore ban-drop-table
DROP TABLE trust_application_fees;
-- squawk-ignore ban-drop-table
DROP TABLE trust_application_fee_settings;
-- squawk-ignore ban-drop-function
DROP FUNCTION trust_check_application_fee_step();
