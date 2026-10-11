-- migrate:up
-- Rental applications, screening and adverse-action records: the first half of leasing basics
-- (founder, 2026-10-11: docs/adr/0002-portals-and-exports.md; applications come through a link
-- per unit). E-sign and renewals come next.
--
--   trust_application_links   a link staff open for a unit, kept only as its token's SHA-256,
--                             with the rent it is offered at. Anyone with it may apply.
--   trust_application_link_closings
--                             when staff closed a link; a closed link takes no application.
--   trust_applications        an application through a link: when the household would move
--                             in, its monthly income, and a status token (kept as its SHA-256)
--                             the applicant uses to see where it stands.
--   trust_applicants          the people on an application, the first being the one who
--                             applied. Only a name and an email: the screening partner collects
--                             anything else on its own side.
--   trust_screenings          a screening partner's answer for an application: approve,
--                             conditional or decline, with its reasons and who reported it.
--   trust_application_decisions
--                             the PMC's one decision on an application, maybe resting on a
--                             screening of it; a conditional approval says its conditions.
--   trust_adverse_actions     the notice given for a declined or conditional decision that
--                             rests on a screening: the reasons, the reporting agency and the
--                             day it was given. Each such decision has one, and nothing else
--                             does (checked at commit).
--
-- Every table is append-only. No AI role reads any of them: applicants' details and consumer
-- report results stay with the PMC.

-- On a live database, give up rather than queue behind a long transaction while holding locks.
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '60s';

CREATE TABLE trust_application_links (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    pmc_id uuid NOT NULL REFERENCES trust_pmcs (pmc_id),
    unit_id uuid NOT NULL,
    token_sha256 bytea NOT NULL UNIQUE CHECK (length(token_sha256) = 32),
    monthly_rent numeric NOT NULL CHECK (monthly_rent > 0),
    opened_by uuid NOT NULL,
    opened_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (pmc_id, id),
    FOREIGN KEY (pmc_id, unit_id) REFERENCES trust_units (pmc_id, id),
    FOREIGN KEY (pmc_id, opened_by) REFERENCES trust_staff (pmc_id, id)
);
CREATE INDEX trust_application_links_unit_id ON trust_application_links (unit_id);

CREATE TABLE trust_application_link_closings (
    link_id uuid PRIMARY KEY,
    pmc_id uuid NOT NULL REFERENCES trust_pmcs (pmc_id),
    closed_by uuid NOT NULL,
    closed_at timestamptz NOT NULL DEFAULT now(),
    FOREIGN KEY (pmc_id, link_id) REFERENCES trust_application_links (pmc_id, id),
    FOREIGN KEY (pmc_id, closed_by) REFERENCES trust_staff (pmc_id, id)
);

CREATE TABLE trust_applications (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    pmc_id uuid NOT NULL REFERENCES trust_pmcs (pmc_id),
    link_id uuid NOT NULL,
    status_token_sha256 bytea NOT NULL UNIQUE CHECK (length(status_token_sha256) = 32),
    move_in_on date NOT NULL,
    monthly_income numeric NOT NULL CHECK (monthly_income >= 0),
    submitted_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (pmc_id, id),
    FOREIGN KEY (pmc_id, link_id) REFERENCES trust_application_links (pmc_id, id)
);
CREATE INDEX trust_applications_link_id ON trust_applications (link_id);

CREATE TABLE trust_applicants (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    pmc_id uuid NOT NULL REFERENCES trust_pmcs (pmc_id),
    application_id uuid NOT NULL,
    position bigint NOT NULL CHECK (position BETWEEN 1 AND 6),
    display_name text NOT NULL CHECK (display_name ~ '\S' AND length(display_name) <= 200),
    email text NOT NULL CHECK (
        email = lower(email) AND email ~ '^[^@\s]+@[^@\s]+\.[^@\s]+$' AND length(email) <= 254
    ),
    UNIQUE (application_id, position),
    UNIQUE (application_id, email),
    FOREIGN KEY (pmc_id, application_id) REFERENCES trust_applications (pmc_id, id)
);

CREATE TABLE trust_screenings (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    pmc_id uuid NOT NULL REFERENCES trust_pmcs (pmc_id),
    application_id uuid NOT NULL,
    partner text NOT NULL CHECK (partner ~ '\S'),
    partner_reference text NOT NULL CHECK (partner_reference ~ '\S'),
    -- Who the applicant asks for a copy of the report, or disputes it with: the partner's
    -- name, address and phone as it gives them.
    agency text NOT NULL CHECK (agency ~ '\S'),
    recommendation text NOT NULL CHECK (recommendation IN ('approve', 'conditional', 'decline')),
    reasons text[] NOT NULL,
    requested_by uuid NOT NULL,
    reported_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (pmc_id, id),
    -- An approval needs no reasons; a condition or a decline gives at least one.
    CHECK ((recommendation = 'approve') = (cardinality(reasons) = 0)),
    FOREIGN KEY (pmc_id, application_id) REFERENCES trust_applications (pmc_id, id),
    FOREIGN KEY (pmc_id, requested_by) REFERENCES trust_staff (pmc_id, id)
);
CREATE INDEX trust_screenings_application_id ON trust_screenings (application_id);

CREATE TABLE trust_application_decisions (
    application_id uuid PRIMARY KEY,
    pmc_id uuid NOT NULL REFERENCES trust_pmcs (pmc_id),
    decision text NOT NULL CHECK (decision IN ('approved', 'conditional', 'declined')),
    conditions text CHECK (conditions ~ '\S' AND length(conditions) <= 2000),
    screening_id uuid,
    decided_by uuid NOT NULL,
    decided_at timestamptz NOT NULL DEFAULT now(),
    CHECK ((decision = 'conditional') = (conditions IS NOT NULL)),
    -- trust_adverse_actions refers to a decision by both.
    UNIQUE (pmc_id, application_id),
    FOREIGN KEY (pmc_id, application_id) REFERENCES trust_applications (pmc_id, id),
    FOREIGN KEY (pmc_id, screening_id) REFERENCES trust_screenings (pmc_id, id),
    FOREIGN KEY (pmc_id, decided_by) REFERENCES trust_staff (pmc_id, id)
);

CREATE TABLE trust_adverse_actions (
    application_id uuid PRIMARY KEY,
    pmc_id uuid NOT NULL REFERENCES trust_pmcs (pmc_id),
    reasons text[] NOT NULL CHECK (cardinality(reasons) BETWEEN 1 AND 20),
    agency text NOT NULL CHECK (agency ~ '\S'),
    notice_given_on date NOT NULL,
    recorded_by uuid NOT NULL,
    recorded_at timestamptz NOT NULL DEFAULT now(),
    FOREIGN KEY (pmc_id, application_id)
    REFERENCES trust_application_decisions (pmc_id, application_id),
    FOREIGN KEY (pmc_id, recorded_by) REFERENCES trust_staff (pmc_id, id)
);

CREATE TRIGGER trust_append_only
BEFORE UPDATE OR DELETE OR TRUNCATE ON trust_application_links
FOR EACH STATEMENT EXECUTE FUNCTION trust_refuse_ledger_rewrite();
CREATE TRIGGER trust_append_only
BEFORE UPDATE OR DELETE OR TRUNCATE ON trust_application_link_closings
FOR EACH STATEMENT EXECUTE FUNCTION trust_refuse_ledger_rewrite();
CREATE TRIGGER trust_append_only
BEFORE UPDATE OR DELETE OR TRUNCATE ON trust_applications
FOR EACH STATEMENT EXECUTE FUNCTION trust_refuse_ledger_rewrite();
CREATE TRIGGER trust_append_only
BEFORE UPDATE OR DELETE OR TRUNCATE ON trust_applicants
FOR EACH STATEMENT EXECUTE FUNCTION trust_refuse_ledger_rewrite();
CREATE TRIGGER trust_append_only
BEFORE UPDATE OR DELETE OR TRUNCATE ON trust_screenings
FOR EACH STATEMENT EXECUTE FUNCTION trust_refuse_ledger_rewrite();
CREATE TRIGGER trust_append_only
BEFORE UPDATE OR DELETE OR TRUNCATE ON trust_application_decisions
FOR EACH STATEMENT EXECUTE FUNCTION trust_refuse_ledger_rewrite();
CREATE TRIGGER trust_append_only
BEFORE UPDATE OR DELETE OR TRUNCATE ON trust_adverse_actions
FOR EACH STATEMENT EXECUTE FUNCTION trust_refuse_ledger_rewrite();

-- An application comes through a link that is still open. An application and the link's
-- closing wait for each other (an advisory lock on the link, which needs no grant); then the
-- application reads the closings committed before it, which only READ COMMITTED does.
CREATE FUNCTION trust_check_application() RETURNS trigger
LANGUAGE plpgsql
SET search_path = public, pg_temp
AS $$
BEGIN
    IF current_setting('transaction_isolation') <> 'read committed' THEN
        RAISE EXCEPTION 'trust: take applications at READ COMMITTED, not %',
            current_setting('transaction_isolation')
            USING ERRCODE = 'invalid_transaction_state';
    END IF;
    PERFORM pg_advisory_xact_lock(hashtextextended('application link ' || NEW.link_id, 0));
    IF EXISTS (
        SELECT c.link_id FROM trust_application_link_closings AS c WHERE c.link_id = NEW.link_id
    ) THEN
        RAISE EXCEPTION 'trust: application link % is closed', NEW.link_id
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER trust_application_link_open
BEFORE INSERT ON trust_applications
FOR EACH ROW EXECUTE FUNCTION trust_check_application();

-- Closing a link takes the same lock, so it waits for an application under way.
CREATE FUNCTION trust_lock_application_link() RETURNS trigger
LANGUAGE plpgsql
SET search_path = public, pg_temp
AS $$
BEGIN
    PERFORM pg_advisory_xact_lock(hashtextextended('application link ' || NEW.link_id, 0));
    RETURN NEW;
END;
$$;

CREATE TRIGGER trust_application_link_lock
BEFORE INSERT ON trust_application_link_closings
FOR EACH ROW EXECUTE FUNCTION trust_lock_application_link();

-- A decision rests only on a screening of the same application.
CREATE FUNCTION trust_check_application_decision() RETURNS trigger
LANGUAGE plpgsql
SET search_path = public, pg_temp
AS $$
BEGIN
    IF NEW.screening_id IS NOT NULL AND NOT EXISTS (
        SELECT s.id FROM trust_screenings AS s
        WHERE s.id = NEW.screening_id AND s.application_id = NEW.application_id
    ) THEN
        RAISE EXCEPTION 'trust: screening % is not of application %',
            NEW.screening_id, NEW.application_id
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER trust_application_decision_screening
BEFORE INSERT ON trust_application_decisions
FOR EACH ROW EXECUTE FUNCTION trust_check_application_decision();

-- At commit: a declined or conditional decision that rests on a screening has its adverse
-- action, and an adverse action belongs to such a decision. Both inserts go in one transaction.
CREATE FUNCTION trust_check_adverse_action() RETURNS trigger
LANGUAGE plpgsql
SET search_path = public, pg_temp
AS $$
DECLARE
    v_needed boolean;
    v_given boolean;
BEGIN
    SELECT d.decision IN ('declined', 'conditional') AND d.screening_id IS NOT NULL
    INTO v_needed
    FROM trust_application_decisions AS d
    WHERE d.application_id = NEW.application_id;
    v_given := EXISTS (
        SELECT a.application_id FROM trust_adverse_actions AS a
        WHERE a.application_id = NEW.application_id
    );
    IF v_needed AND NOT v_given THEN
        RAISE EXCEPTION 'trust: application % was % on a screening; record the adverse action '
            'notice with the decision', NEW.application_id,
            (SELECT d.decision FROM trust_application_decisions AS d
             WHERE d.application_id = NEW.application_id)
            USING ERRCODE = 'check_violation';
    END IF;
    IF v_given AND NOT v_needed THEN
        RAISE EXCEPTION 'trust: an adverse action belongs to a declined or conditional decision '
            'that rests on a screening; application % has none', NEW.application_id
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NULL;
END;
$$;

CREATE CONSTRAINT TRIGGER trust_adverse_action_given
AFTER INSERT ON trust_application_decisions
DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW EXECUTE FUNCTION trust_check_adverse_action();
CREATE CONSTRAINT TRIGGER trust_adverse_action_belongs
AFTER INSERT ON trust_adverse_actions
DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW EXECUTE FUNCTION trust_check_adverse_action();

CREATE TRIGGER trust_audit AFTER INSERT OR UPDATE OR DELETE ON trust_application_links
FOR EACH ROW EXECUTE FUNCTION trust_audit();
CREATE TRIGGER trust_audit AFTER INSERT OR UPDATE OR DELETE ON trust_application_link_closings
FOR EACH ROW EXECUTE FUNCTION trust_audit();
CREATE TRIGGER trust_audit AFTER INSERT OR UPDATE OR DELETE ON trust_applications
FOR EACH ROW EXECUTE FUNCTION trust_audit();
CREATE TRIGGER trust_audit AFTER INSERT OR UPDATE OR DELETE ON trust_applicants
FOR EACH ROW EXECUTE FUNCTION trust_audit();
CREATE TRIGGER trust_audit AFTER INSERT OR UPDATE OR DELETE ON trust_screenings
FOR EACH ROW EXECUTE FUNCTION trust_audit();
CREATE TRIGGER trust_audit AFTER INSERT OR UPDATE OR DELETE ON trust_application_decisions
FOR EACH ROW EXECUTE FUNCTION trust_audit();
CREATE TRIGGER trust_audit AFTER INSERT OR UPDATE OR DELETE ON trust_adverse_actions
FOR EACH ROW EXECUTE FUNCTION trust_audit();

REVOKE ALL ON trust_application_links, trust_application_link_closings, trust_applications,
trust_applicants, trust_screenings, trust_application_decisions, trust_adverse_actions
FROM PUBLIC;
GRANT SELECT, INSERT ON trust_application_links, trust_application_link_closings,
trust_applications, trust_applicants, trust_screenings, trust_application_decisions,
trust_adverse_actions TO trust_app;

-- migrate:down
DO $$
BEGIN
    RAISE EXCEPTION 'rental_applications cannot be rolled back: applications and their '
        'decisions are records';
END;
$$;
