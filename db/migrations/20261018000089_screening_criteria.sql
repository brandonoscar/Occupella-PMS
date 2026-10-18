-- migrate:up
-- Screening criteria (the founder's review, q085; docs/law.md): the same written criteria for every
-- applicant, applied by the screening partner, and a decision that goes against a screening's
-- recommendation saying why.
--
--   trust_screening_criteria          a company's criteria from a moment: income of at least
--                                     approve_multiple times the rent is approved, at least
--                                     conditional_multiple times approved on conditions, less
--                                     declined; and the criteria in words, shown to applicants.
--                                     Append-only, the latest row holding, naming who changed it.
--   trust_screenings.criteria_id      the criteria a screening applied (none: the partner's own).
--   trust_application_decisions
--     .override_reason                why a decision went against the recommendation of the
--                                     application's latest screening; required then, and only then.
--
-- Like screenings and decisions, the AI role reads none of it.

-- On a live database, give up rather than queue behind a long transaction while holding locks.
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '60s';

CREATE TABLE trust_screening_criteria (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    pmc_id uuid NOT NULL REFERENCES trust_pmcs (pmc_id),
    approve_multiple numeric(4, 2) NOT NULL CHECK (approve_multiple BETWEEN 1 AND 10),
    conditional_multiple numeric(4, 2) NOT NULL CHECK (conditional_multiple >= 0),
    written text CHECK (written IS NULL OR (written ~ '\S' AND length(written) <= 4000)),
    recorded_by uuid,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    UNIQUE (pmc_id, id),
    CHECK (conditional_multiple <= approve_multiple),
    FOREIGN KEY (pmc_id, recorded_by) REFERENCES trust_staff (pmc_id, id)
);
CREATE INDEX trust_screening_criteria_pmc ON trust_screening_criteria (pmc_id, created_at);
CREATE INDEX trust_screening_criteria_recorded_by ON trust_screening_criteria (
    pmc_id, recorded_by
);

CREATE TRIGGER trust_recorded_by BEFORE INSERT ON trust_screening_criteria
FOR EACH ROW EXECUTE FUNCTION trust_name_recorder();
CREATE TRIGGER trust_append_only
BEFORE UPDATE OR DELETE OR TRUNCATE ON trust_screening_criteria
FOR EACH STATEMENT EXECUTE FUNCTION trust_refuse_ledger_rewrite();
CREATE TRIGGER trust_audit AFTER INSERT OR UPDATE OR DELETE ON trust_screening_criteria
FOR EACH ROW EXECUTE FUNCTION trust_audit();

REVOKE ALL ON trust_screening_criteria FROM PUBLIC;
GRANT SELECT, INSERT ON trust_screening_criteria TO trust_app;

ALTER TABLE trust_screenings ADD COLUMN criteria_id uuid;
-- squawk-ignore adding-foreign-key-constraint
ALTER TABLE trust_screenings ADD CONSTRAINT trust_screenings_pmc_id_criteria_id_fkey
FOREIGN KEY (pmc_id, criteria_id) REFERENCES trust_screening_criteria (pmc_id, id) NOT VALID;
-- squawk-ignore require-concurrent-index-creation
CREATE INDEX trust_screenings_criteria ON trust_screenings (criteria_id);

ALTER TABLE trust_application_decisions ADD COLUMN override_reason text;
ALTER TABLE trust_application_decisions ADD CONSTRAINT trust_application_decisions_override_check
CHECK (override_reason IS NULL OR (override_reason ~ '\S' AND length(override_reason) <= 500))
NOT VALID;

-- A decision against the recommendation of the application's latest screening says why, and no
-- other decision does. Approve goes with approved, conditional with conditional, decline with
-- declined.
CREATE FUNCTION trust_check_decision_override() RETURNS trigger
LANGUAGE plpgsql
SET search_path = public, pg_temp
AS $$
DECLARE
    v_recommendation text;
BEGIN
    SELECT s.recommendation INTO v_recommendation
    FROM trust_screenings AS s
    WHERE s.application_id = NEW.application_id
    ORDER BY s.reported_at DESC, s.id DESC
    LIMIT 1;
    IF (v_recommendation IS NOT NULL AND NEW.decision <> CASE v_recommendation
            WHEN 'approve' THEN 'approved'
            WHEN 'conditional' THEN 'conditional'
            ELSE 'declined'
        END) <> (NEW.override_reason IS NOT NULL) THEN
        RAISE EXCEPTION 'trust: a decision against its screening''s recommendation says why, and no other decision does'
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$;
REVOKE ALL ON FUNCTION trust_check_decision_override() FROM PUBLIC;

CREATE TRIGGER trust_decision_override BEFORE INSERT ON trust_application_decisions
FOR EACH ROW EXECUTE FUNCTION trust_check_decision_override();

-- migrate:down
DO $$
BEGIN
    RAISE EXCEPTION 'screening_criteria cannot be rolled back: criteria and decisions are records';
END;
$$;
