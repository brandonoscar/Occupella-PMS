-- migrate:up
-- A vendor as Buildium keeps one (the founder's punch list, ADR 0006, step 10): their trade,
-- whether they are a 1099 vendor and how they are paid (`trust_vendor_profiles`, append-only, the
-- latest row for a vendor holding, naming who recorded it). No tax ID is kept: the number stays
-- on the W-9 itself (decision 3). Whether a vendor gets a 1099 decides nothing here: the PMS files
-- nothing and applies no threshold. How they are paid describes the payouts staff record (check
-- or EFT, as trust_payout_methods records each one) and moves no money.

-- On a live database, give up rather than queue behind a long transaction while holding locks.
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '60s';

CREATE TABLE trust_vendor_profiles (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    pmc_id uuid NOT NULL REFERENCES trust_pmcs (pmc_id),
    vendor_id uuid NOT NULL,
    -- What they do: Plumbing, Roofing, Landscaping...
    trade text CHECK (btrim(trade) <> '' AND char_length(trade) <= 100),
    gets_1099 boolean NOT NULL,
    payment_method text CHECK (payment_method IN ('check', 'EFT')),
    recorded_by uuid,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    FOREIGN KEY (pmc_id, vendor_id) REFERENCES trust_vendors (pmc_id, id),
    FOREIGN KEY (pmc_id, recorded_by) REFERENCES trust_staff (pmc_id, id)
);
CREATE INDEX trust_vendor_profiles_vendor ON trust_vendor_profiles (pmc_id, vendor_id, created_at);
CREATE INDEX trust_vendor_profiles_recorded_by ON trust_vendor_profiles (pmc_id, recorded_by);

CREATE TRIGGER trust_recorded_by BEFORE INSERT ON trust_vendor_profiles
FOR EACH ROW EXECUTE FUNCTION trust_name_recorder();
CREATE TRIGGER trust_append_only
BEFORE UPDATE OR DELETE OR TRUNCATE ON trust_vendor_profiles
FOR EACH STATEMENT EXECUTE FUNCTION trust_refuse_ledger_rewrite();
CREATE TRIGGER trust_audit AFTER INSERT OR UPDATE OR DELETE ON trust_vendor_profiles
FOR EACH ROW EXECUTE FUNCTION trust_audit();

REVOKE ALL ON trust_vendor_profiles FROM PUBLIC;
GRANT SELECT, INSERT ON trust_vendor_profiles TO trust_app;
GRANT SELECT ON trust_vendor_profiles TO trust_ai_agent;

-- migrate:down
DO $$
BEGIN
    RAISE EXCEPTION 'vendor_profiles cannot be rolled back: profiles are records';
END;
$$;
