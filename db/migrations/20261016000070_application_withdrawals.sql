-- migrate:up
-- Withdrawn applications (the founder's punch list, ADR 0006: an applicant's status is New,
-- Screening, Approved, Rejected or Withdrawn): an applicant who no longer wants the home, as
-- staff record it (`trust_application_withdrawals`, once per application, append-only, naming
-- who recorded it, and why if the applicant said). It comes before the application is declined
-- or made a lease; after it the application takes no screening, decision or lease offer, and no
-- offer of it becomes a lease. Each of those waits for a withdrawal of its application, as the
-- offers of one application wait for each other. Applications are about people, so the AI role
-- never reads it, nor its rows in the audit log. Nothing here moves money.

-- On a live database, give up rather than queue behind a long transaction while holding locks.
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '60s';

CREATE TABLE trust_application_withdrawals (
    application_id uuid PRIMARY KEY,
    pmc_id uuid NOT NULL REFERENCES trust_pmcs (pmc_id),
    reason text CHECK (btrim(reason) <> '' AND char_length(reason) <= 500),
    recorded_by uuid,
    recorded_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    FOREIGN KEY (pmc_id, application_id) REFERENCES trust_applications (pmc_id, id),
    FOREIGN KEY (pmc_id, recorded_by) REFERENCES trust_staff (pmc_id, id)
);
CREATE INDEX trust_application_withdrawals_recorded_by
ON trust_application_withdrawals (pmc_id, recorded_by);

CREATE TRIGGER trust_recorded_by BEFORE INSERT ON trust_application_withdrawals
FOR EACH ROW EXECUTE FUNCTION trust_name_recorder();
CREATE TRIGGER trust_append_only
BEFORE UPDATE OR DELETE OR TRUNCATE ON trust_application_withdrawals
FOR EACH STATEMENT EXECUTE FUNCTION trust_refuse_ledger_rewrite();
CREATE TRIGGER trust_audit AFTER INSERT OR UPDATE OR DELETE ON trust_application_withdrawals
FOR EACH ROW EXECUTE FUNCTION trust_audit();

-- A withdrawal comes before a decline and before a lease is made of the application.
CREATE FUNCTION trust_check_application_withdrawal() RETURNS trigger
LANGUAGE plpgsql
SET search_path = public, pg_temp
AS $$
BEGIN
    PERFORM pg_advisory_xact_lock(hashtextextended('lease offer of ' || NEW.application_id, 0));
    IF EXISTS (
        SELECT d.application_id FROM trust_application_decisions AS d
        WHERE d.application_id = NEW.application_id AND d.decision = 'declined'
    ) THEN
        RAISE EXCEPTION 'trust: a declined application is not withdrawn'
            USING ERRCODE = 'check_violation';
    END IF;
    IF EXISTS (
        SELECT o.id FROM trust_lease_offers AS o
        JOIN trust_lease_offer_completions AS c ON c.offer_id = o.id
        WHERE o.application_id = NEW.application_id
    ) THEN
        RAISE EXCEPTION 'trust: this application is a lease already; it is not withdrawn'
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER trust_application_withdrawal_open
BEFORE INSERT ON trust_application_withdrawals
FOR EACH ROW EXECUTE FUNCTION trust_check_application_withdrawal();

-- A withdrawn application takes nothing more: no screening, decision or lease offer.
CREATE FUNCTION trust_check_application_not_withdrawn() RETURNS trigger
LANGUAGE plpgsql
SET search_path = public, pg_temp
AS $$
BEGIN
    IF NEW.application_id IS NULL THEN
        RETURN NEW;  -- a renewal's offer
    END IF;
    PERFORM pg_advisory_xact_lock(hashtextextended('lease offer of ' || NEW.application_id, 0));
    IF EXISTS (
        SELECT w.application_id FROM trust_application_withdrawals AS w
        WHERE w.application_id = NEW.application_id
    ) THEN
        RAISE EXCEPTION 'trust: the applicant withdrew this application; it takes nothing more'
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER trust_application_not_withdrawn
BEFORE INSERT ON trust_screenings
FOR EACH ROW EXECUTE FUNCTION trust_check_application_not_withdrawn();
CREATE TRIGGER trust_application_not_withdrawn
BEFORE INSERT ON trust_application_decisions
FOR EACH ROW EXECUTE FUNCTION trust_check_application_not_withdrawn();
CREATE TRIGGER trust_application_not_withdrawn
BEFORE INSERT ON trust_lease_offers
FOR EACH ROW EXECUTE FUNCTION trust_check_application_not_withdrawn();

-- No offer of a withdrawn application becomes a lease. trust_complete_lease_offer holds the
-- application's lock already, so a withdrawal and a completion wait for each other.
CREATE FUNCTION trust_check_completion_not_withdrawn() RETURNS trigger
LANGUAGE plpgsql
SET search_path = public, pg_temp
AS $$
BEGIN
    IF EXISTS (
        SELECT w.application_id FROM trust_lease_offers AS o
        JOIN trust_application_withdrawals AS w ON w.application_id = o.application_id
        WHERE o.id = NEW.offer_id
    ) THEN
        RAISE EXCEPTION 'trust: the applicant withdrew this application; it is made no lease'
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER trust_application_not_withdrawn
BEFORE INSERT ON trust_lease_offer_completions
FOR EACH ROW EXECUTE FUNCTION trust_check_completion_not_withdrawn();

REVOKE ALL ON trust_application_withdrawals FROM PUBLIC;
GRANT SELECT, INSERT ON trust_application_withdrawals TO trust_app;

-- migrate:down
DO $$
BEGIN
    RAISE EXCEPTION 'application_withdrawals cannot be rolled back: withdrawals are records';
END;
$$;
