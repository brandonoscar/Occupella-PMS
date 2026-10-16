-- migrate:up
-- How a payout left trust (the founder's punch list, ADR 0006: Pay owners "choose check or EFT";
-- "Check / EFT is recorded as the method; the PMS writes no bank file"). A row of
-- `trust_payout_methods` says how an owner draw or a bill's payment of the PMC was sent: by check
-- or by EFT. Once per payout, append-only, audited, naming who recorded it. It describes money
-- already moved and moves none.

-- On a live database, give up rather than queue behind a long transaction while holding locks.
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '60s';

CREATE TABLE trust_payout_methods (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    pmc_id uuid NOT NULL REFERENCES trust_pmcs (pmc_id),
    transfer_id text NOT NULL UNIQUE REFERENCES pgledger_transfers (id),
    method text NOT NULL CHECK (method IN ('check', 'EFT')),
    recorded_by uuid,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    FOREIGN KEY (pmc_id, recorded_by) REFERENCES trust_staff (pmc_id, id)
);
CREATE INDEX trust_payout_methods_recorded_by ON trust_payout_methods (pmc_id, recorded_by);

-- A payout is an owner draw or a bill's payment, of the row's PMC.
CREATE FUNCTION trust_check_payout_method() RETURNS trigger
LANGUAGE plpgsql
SET search_path = public, pg_temp
AS $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM trust_owner_draws AS d
        WHERE d.transfer_id = NEW.transfer_id AND d.pmc_id = NEW.pmc_id
    ) AND NOT EXISTS (
        SELECT 1 FROM trust_bill_payments AS b
        WHERE b.transfer_id = NEW.transfer_id AND b.pmc_id = NEW.pmc_id AND b.step = 'paid'
    ) THEN
        RAISE EXCEPTION 'trust: transfer % is not an owner draw or a bill paid of the PMC',
            NEW.transfer_id
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER trust_payout_method BEFORE INSERT ON trust_payout_methods
FOR EACH ROW EXECUTE FUNCTION trust_check_payout_method();
CREATE TRIGGER trust_recorded_by BEFORE INSERT ON trust_payout_methods
FOR EACH ROW EXECUTE FUNCTION trust_name_recorder();
CREATE TRIGGER trust_append_only
BEFORE UPDATE OR DELETE OR TRUNCATE ON trust_payout_methods
FOR EACH STATEMENT EXECUTE FUNCTION trust_refuse_ledger_rewrite();
CREATE TRIGGER trust_audit AFTER INSERT OR UPDATE OR DELETE ON trust_payout_methods
FOR EACH ROW EXECUTE FUNCTION trust_audit();

REVOKE ALL ON trust_payout_methods FROM PUBLIC;
GRANT SELECT, INSERT ON trust_payout_methods TO trust_app;
GRANT SELECT ON trust_payout_methods TO trust_ai_agent;

-- migrate:down
DO $$
BEGIN
    RAISE EXCEPTION 'payout methods cannot be rolled back: they are records';
END;
$$;
