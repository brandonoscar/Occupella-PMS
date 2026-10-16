-- migrate:up
-- Rent posts by itself (the founder's punch list, ADR 0006: "Without this, rent is never
-- charged unless someone remembers to click"). `trust_run_rent` runs what the rent roll's two
-- buttons ran: every lease's rent and recurring charges for a due date, then the late fees of
-- rents still open after their grace days as of a day, and records the run
-- (`trust_rent_runs`, append-only, audited): the nightly run names nobody; a run by hand names
-- the staff member, who must hold admin. Each part charges once per lease (or recurring charge
-- or rent) and date, so a run again charges nothing new. What tenants owe stays out of the trust
-- ledger: nothing here moves money.

-- On a live database, give up rather than queue behind a long transaction while holding locks.
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '60s';

CREATE TABLE trust_rent_runs (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    pmc_id uuid NOT NULL REFERENCES trust_pmcs (pmc_id),
    due_on date NOT NULL,
    late_fees_as_of date NOT NULL,
    rents bigint NOT NULL CHECK (rents >= 0),
    recurring bigint NOT NULL CHECK (recurring >= 0),
    late_fees bigint NOT NULL CHECK (late_fees >= 0),
    -- The staff member who ran it by hand; none for the nightly run.
    recorded_by uuid,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    FOREIGN KEY (pmc_id, recorded_by) REFERENCES trust_staff (pmc_id, id)
);
CREATE INDEX trust_rent_runs_pmc ON trust_rent_runs (pmc_id, created_at);
CREATE INDEX trust_rent_runs_recorded_by ON trust_rent_runs (pmc_id, recorded_by);

CREATE TRIGGER trust_recorded_by BEFORE INSERT ON trust_rent_runs
FOR EACH ROW EXECUTE FUNCTION trust_name_recorder();
CREATE TRIGGER trust_append_only
BEFORE UPDATE OR DELETE OR TRUNCATE ON trust_rent_runs
FOR EACH STATEMENT EXECUTE FUNCTION trust_refuse_ledger_rewrite();
CREATE TRIGGER trust_audit AFTER INSERT OR UPDATE OR DELETE ON trust_rent_runs
FOR EACH ROW EXECUTE FUNCTION trust_audit();

REVOKE ALL ON trust_rent_runs FROM PUBLIC;
GRANT SELECT, INSERT ON trust_rent_runs TO trust_app;
GRANT SELECT ON trust_rent_runs TO trust_ai_agent;

-- One run: rent and recurring charges due on p_due_on, late fees as of p_late_fees_as_of. It runs
-- with the app's own grants, as the two rent functions it calls do.
CREATE FUNCTION trust_run_rent(p_pmc_id uuid, p_due_on date, p_late_fees_as_of date)
RETURNS uuid
LANGUAGE plpgsql
SET search_path = public, pg_temp
AS $$
DECLARE
    v_rents integer;
    v_recurring integer;
    v_late_fees integer;
    v_id uuid;
BEGIN
    -- By hand, only an admin runs it; the nightly run names nobody.
    PERFORM trust_require_role(
        p_pmc_id, nullif(current_setting('trust.staff_id', true), '')::uuid, 'admin',
        'run rent by hand'
    );
    v_rents := trust_charge_rent_due(p_pmc_id, p_due_on);
    v_recurring := trust_charge_recurring_due(p_pmc_id, p_due_on);
    v_late_fees := trust_assess_late_fees(p_pmc_id, p_late_fees_as_of);
    INSERT INTO trust_rent_runs (pmc_id, due_on, late_fees_as_of, rents, recurring, late_fees)
    VALUES (p_pmc_id, p_due_on, p_late_fees_as_of, v_rents, v_recurring, v_late_fees)
    RETURNING id INTO v_id;
    RETURN v_id;
END;
$$;
REVOKE ALL ON FUNCTION trust_run_rent(uuid, date, date) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION trust_run_rent(uuid, date, date) TO trust_app;

-- migrate:down
DO $$
BEGIN
    RAISE EXCEPTION 'rent runs cannot be rolled back: they are records';
END;
$$;
