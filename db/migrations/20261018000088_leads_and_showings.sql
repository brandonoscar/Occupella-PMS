-- migrate:up
-- Leads and showings (the founder's review, q083): a prospect who asked about renting, before any
-- application, and the showings staff set up for them.
--
--   trust_leads              a prospect: a name, an email or a phone, what they asked about (a
--                            property, maybe one of its units), where they came from and when they
--                            would move in, naming who recorded it.
--   trust_lead_closings      a lead closed, once: they applied, or the company lost them (why).
--   trust_showings           a showing of a unit to a lead at a time, maybe by a staff member; a
--                            closed lead takes none.
--   trust_showing_outcomes   how a showing went, once: shown, a no-show, or cancelled.
--
-- All append-only, audited and naming who. Nothing here records anything a fair housing law
-- protects. Like applicants, the AI role reads none of it.

-- On a live database, give up rather than queue behind a long transaction while holding locks.
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '60s';

CREATE TABLE trust_leads (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    pmc_id uuid NOT NULL REFERENCES trust_pmcs (pmc_id),
    display_name text NOT NULL CHECK (display_name ~ '\S' AND length(display_name) <= 200),
    email text CHECK (email IS NULL OR (email ~ '^[^@\s]+@[^@\s]+$' AND length(email) <= 320)),
    phone text CHECK (phone IS NULL OR (phone ~ '\S' AND length(phone) <= 40)),
    property_id uuid,
    unit_id uuid,
    source text CHECK (source IS NULL OR (source ~ '\S' AND length(source) <= 60)),
    move_in_on date,
    note text CHECK (note IS NULL OR (note ~ '\S' AND length(note) <= 2000)),
    recorded_by uuid,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    UNIQUE (pmc_id, id),
    -- A way to reach them.
    CHECK (email IS NOT NULL OR phone IS NOT NULL),
    FOREIGN KEY (pmc_id, property_id) REFERENCES trust_properties (pmc_id, id),
    FOREIGN KEY (pmc_id, unit_id) REFERENCES trust_units (pmc_id, id),
    FOREIGN KEY (pmc_id, recorded_by) REFERENCES trust_staff (pmc_id, id)
);
CREATE INDEX trust_leads_pmc ON trust_leads (pmc_id, created_at);
CREATE INDEX trust_leads_property ON trust_leads (pmc_id, property_id);
CREATE INDEX trust_leads_unit ON trust_leads (pmc_id, unit_id);
CREATE INDEX trust_leads_recorded_by ON trust_leads (pmc_id, recorded_by);

-- The unit asked about is one of the property's (as a work order's is), so a unit comes with its
-- property.
CREATE TRIGGER trust_lead_unit BEFORE INSERT ON trust_leads
FOR EACH ROW EXECUTE FUNCTION trust_check_work_order();
CREATE TRIGGER trust_recorded_by BEFORE INSERT ON trust_leads
FOR EACH ROW EXECUTE FUNCTION trust_name_recorder();
CREATE TRIGGER trust_append_only
BEFORE UPDATE OR DELETE OR TRUNCATE ON trust_leads
FOR EACH STATEMENT EXECUTE FUNCTION trust_refuse_ledger_rewrite();
CREATE TRIGGER trust_audit AFTER INSERT OR UPDATE OR DELETE ON trust_leads
FOR EACH ROW EXECUTE FUNCTION trust_audit();

REVOKE ALL ON trust_leads FROM PUBLIC;
GRANT SELECT, INSERT ON trust_leads TO trust_app;

CREATE TABLE trust_lead_closings (
    lead_id uuid PRIMARY KEY,
    pmc_id uuid NOT NULL,
    outcome text NOT NULL CHECK (outcome IN ('applied', 'lost')),
    reason text CHECK (reason IS NULL OR (reason ~ '\S' AND length(reason) <= 500)),
    recorded_by uuid,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    FOREIGN KEY (pmc_id, lead_id) REFERENCES trust_leads (pmc_id, id),
    FOREIGN KEY (pmc_id, recorded_by) REFERENCES trust_staff (pmc_id, id)
);
CREATE INDEX trust_lead_closings_recorded_by ON trust_lead_closings (pmc_id, recorded_by);

CREATE TABLE trust_showings (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    pmc_id uuid NOT NULL,
    lead_id uuid NOT NULL,
    unit_id uuid NOT NULL,
    starts_at timestamptz NOT NULL,
    shown_by uuid,
    recorded_by uuid,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    UNIQUE (pmc_id, id),
    FOREIGN KEY (pmc_id, lead_id) REFERENCES trust_leads (pmc_id, id),
    FOREIGN KEY (pmc_id, unit_id) REFERENCES trust_units (pmc_id, id),
    FOREIGN KEY (pmc_id, shown_by) REFERENCES trust_staff (pmc_id, id),
    FOREIGN KEY (pmc_id, recorded_by) REFERENCES trust_staff (pmc_id, id)
);
CREATE INDEX trust_showings_lead ON trust_showings (lead_id, starts_at);
CREATE INDEX trust_showings_unit ON trust_showings (pmc_id, unit_id);
CREATE INDEX trust_showings_shown_by ON trust_showings (pmc_id, shown_by);
CREATE INDEX trust_showings_recorded_by ON trust_showings (pmc_id, recorded_by);

CREATE TABLE trust_showing_outcomes (
    showing_id uuid PRIMARY KEY,
    pmc_id uuid NOT NULL,
    outcome text NOT NULL CHECK (outcome IN ('shown', 'no_show', 'cancelled')),
    note text CHECK (note IS NULL OR (note ~ '\S' AND length(note) <= 2000)),
    recorded_by uuid,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    FOREIGN KEY (pmc_id, showing_id) REFERENCES trust_showings (pmc_id, id),
    FOREIGN KEY (pmc_id, recorded_by) REFERENCES trust_staff (pmc_id, id)
);
CREATE INDEX trust_showing_outcomes_recorded_by ON trust_showing_outcomes (pmc_id, recorded_by);

-- A closed lead takes no showing. A lead's closing and its showings wait for each other, so one
-- set up while it closes is either before the closing or refused.
CREATE FUNCTION trust_check_lead_open() RETURNS trigger
LANGUAGE plpgsql
SET search_path = public, pg_temp
AS $$
BEGIN
    PERFORM pg_advisory_xact_lock(hashtextextended('trust lead ' || NEW.lead_id::text, 0));
    IF TG_TABLE_NAME = 'trust_showings' AND EXISTS (
        SELECT 1 FROM trust_lead_closings AS c WHERE c.lead_id = NEW.lead_id
    ) THEN
        RAISE EXCEPTION 'trust: this lead is closed' USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$;
REVOKE ALL ON FUNCTION trust_check_lead_open() FROM PUBLIC;

CREATE TRIGGER trust_lead_open BEFORE INSERT ON trust_showings
FOR EACH ROW EXECUTE FUNCTION trust_check_lead_open();
CREATE TRIGGER trust_lead_open BEFORE INSERT ON trust_lead_closings
FOR EACH ROW EXECUTE FUNCTION trust_check_lead_open();

CREATE TRIGGER trust_recorded_by BEFORE INSERT ON trust_lead_closings
FOR EACH ROW EXECUTE FUNCTION trust_name_recorder();
CREATE TRIGGER trust_append_only
BEFORE UPDATE OR DELETE OR TRUNCATE ON trust_lead_closings
FOR EACH STATEMENT EXECUTE FUNCTION trust_refuse_ledger_rewrite();
CREATE TRIGGER trust_audit AFTER INSERT OR UPDATE OR DELETE ON trust_lead_closings
FOR EACH ROW EXECUTE FUNCTION trust_audit();

CREATE TRIGGER trust_recorded_by BEFORE INSERT ON trust_showings
FOR EACH ROW EXECUTE FUNCTION trust_name_recorder();
CREATE TRIGGER trust_append_only
BEFORE UPDATE OR DELETE OR TRUNCATE ON trust_showings
FOR EACH STATEMENT EXECUTE FUNCTION trust_refuse_ledger_rewrite();
CREATE TRIGGER trust_audit AFTER INSERT OR UPDATE OR DELETE ON trust_showings
FOR EACH ROW EXECUTE FUNCTION trust_audit();

CREATE TRIGGER trust_recorded_by BEFORE INSERT ON trust_showing_outcomes
FOR EACH ROW EXECUTE FUNCTION trust_name_recorder();
CREATE TRIGGER trust_append_only
BEFORE UPDATE OR DELETE OR TRUNCATE ON trust_showing_outcomes
FOR EACH STATEMENT EXECUTE FUNCTION trust_refuse_ledger_rewrite();
CREATE TRIGGER trust_audit AFTER INSERT OR UPDATE OR DELETE ON trust_showing_outcomes
FOR EACH ROW EXECUTE FUNCTION trust_audit();

REVOKE ALL ON trust_lead_closings FROM PUBLIC;
GRANT SELECT, INSERT ON trust_lead_closings TO trust_app;
REVOKE ALL ON trust_showings FROM PUBLIC;
GRANT SELECT, INSERT ON trust_showings TO trust_app;
REVOKE ALL ON trust_showing_outcomes FROM PUBLIC;
GRANT SELECT, INSERT ON trust_showing_outcomes TO trust_app;

-- migrate:down
DO $$
BEGIN
    RAISE EXCEPTION 'leads_and_showings cannot be rolled back: leads and showings are records';
END;
$$;
