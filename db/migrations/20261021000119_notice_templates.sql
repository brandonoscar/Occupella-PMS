-- migrate:up
-- Notice templates (the founder's review q095): the company's own notices (late rent, entry,
-- non-renewal, anything it writes) with blanks the PMS fills from a lease, such as
-- {{tenant_names}} or {{balance_due}}, made into a PDF (src/pms/api/notice_templates.py). The
-- PMS writes no notice text and checks no state's notice period or wording: the words are the
-- company's, as its lease PDF is.
--
--   trust_notice_templates          a template, the same one through its versions.
--   trust_notice_template_versions  its name and text as staff saved them, the latest holding,
--                                   naming who saved it; a name once among the company's
--                                   templates not removed, whatever its case.
--   trust_notice_template_removals  a template taken off the list, once; it takes no new version.
--   trust_notices                   a notice made for a lease: the version it came from, the day
--                                   it is dated, and its title, letterhead and text as filled in,
--                                   so its PDF reads the same whenever it is downloaded. The AI
--                                   role never reads it.
-- All append-only and audited.

-- On a live database, give up rather than queue behind a long transaction while holding locks.
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '60s';

CREATE TABLE trust_notice_templates (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    pmc_id uuid NOT NULL REFERENCES trust_pmcs (pmc_id),
    recorded_by uuid,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    UNIQUE (pmc_id, id),
    FOREIGN KEY (pmc_id, recorded_by) REFERENCES trust_staff (pmc_id, id)
);
CREATE INDEX trust_notice_templates_recorded_by ON trust_notice_templates (pmc_id, recorded_by);

CREATE TABLE trust_notice_template_versions (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    pmc_id uuid NOT NULL REFERENCES trust_pmcs (pmc_id),
    template_id uuid NOT NULL,
    name text NOT NULL CHECK (name ~ '\S' AND length(name) <= 100),
    body text NOT NULL CHECK (body ~ '\S' AND length(body) <= 20000),
    recorded_by uuid,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    UNIQUE (pmc_id, id),
    FOREIGN KEY (pmc_id, template_id) REFERENCES trust_notice_templates (pmc_id, id),
    FOREIGN KEY (pmc_id, recorded_by) REFERENCES trust_staff (pmc_id, id)
);
CREATE INDEX trust_notice_template_versions_template
ON trust_notice_template_versions (template_id, created_at);
CREATE INDEX trust_notice_template_versions_recorded_by
ON trust_notice_template_versions (pmc_id, recorded_by);

CREATE TABLE trust_notice_template_removals (
    template_id uuid PRIMARY KEY,
    pmc_id uuid NOT NULL REFERENCES trust_pmcs (pmc_id),
    recorded_by uuid,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    FOREIGN KEY (pmc_id, template_id) REFERENCES trust_notice_templates (pmc_id, id),
    FOREIGN KEY (pmc_id, recorded_by) REFERENCES trust_staff (pmc_id, id)
);
CREATE INDEX trust_notice_template_removals_recorded_by
ON trust_notice_template_removals (pmc_id, recorded_by);

CREATE TABLE trust_notices (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    pmc_id uuid NOT NULL REFERENCES trust_pmcs (pmc_id),
    lease_id uuid NOT NULL,
    template_version_id uuid NOT NULL,
    dated_on date NOT NULL,
    title text NOT NULL CHECK (title ~ '\S' AND length(title) <= 100),
    letterhead text NOT NULL CHECK (letterhead ~ '\S' AND length(letterhead) <= 1000),
    body text NOT NULL CHECK (body ~ '\S' AND length(body) <= 40000),
    recorded_by uuid,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    FOREIGN KEY (pmc_id, lease_id) REFERENCES trust_leases (pmc_id, id),
    FOREIGN KEY (pmc_id, template_version_id) REFERENCES trust_notice_template_versions (pmc_id, id),
    FOREIGN KEY (pmc_id, recorded_by) REFERENCES trust_staff (pmc_id, id)
);
CREATE INDEX trust_notices_lease ON trust_notices (pmc_id, lease_id, created_at);
CREATE INDEX trust_notices_version ON trust_notices (pmc_id, template_version_id);
CREATE INDEX trust_notices_recorded_by ON trust_notices (pmc_id, recorded_by);

-- A version is of a template not removed, under a name no other such template of the company
-- has now, whatever its case. Saving and removing templates of one company wait for each other.
CREATE FUNCTION trust_check_notice_template_version() RETURNS trigger
LANGUAGE plpgsql
SET search_path = public, pg_temp
AS $$
BEGIN
    PERFORM pg_advisory_xact_lock(hashtextextended('notice templates ' || NEW.pmc_id::text, 0));
    IF EXISTS (
        SELECT 1 FROM trust_notice_template_removals AS r WHERE r.template_id = NEW.template_id
    ) THEN
        RAISE EXCEPTION 'trust: notice template % is removed', NEW.template_id
            USING ERRCODE = 'check_violation';
    END IF;
    IF EXISTS (
        SELECT 1
        FROM trust_notice_templates AS t
        CROSS JOIN LATERAL (
            SELECT v.name FROM trust_notice_template_versions AS v
            WHERE v.template_id = t.id ORDER BY v.created_at DESC, v.id DESC LIMIT 1
        ) AS latest
        WHERE t.pmc_id = NEW.pmc_id AND t.id <> NEW.template_id
          AND lower(btrim(latest.name)) = lower(btrim(NEW.name))
          AND NOT EXISTS (
              SELECT 1 FROM trust_notice_template_removals AS r WHERE r.template_id = t.id
          )
    ) THEN
        RAISE EXCEPTION 'trust: a notice template is named % already', NEW.name
            USING ERRCODE = 'unique_violation';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER trust_notice_template_version
BEFORE INSERT ON trust_notice_template_versions
FOR EACH ROW EXECUTE FUNCTION trust_check_notice_template_version();

-- A removal waits for a version being saved, and a version for a removal.
CREATE FUNCTION trust_lock_notice_templates() RETURNS trigger
LANGUAGE plpgsql
SET search_path = public, pg_temp
AS $$
BEGIN
    PERFORM pg_advisory_xact_lock(hashtextextended('notice templates ' || NEW.pmc_id::text, 0));
    RETURN NEW;
END;
$$;

CREATE TRIGGER trust_notice_template_removal
BEFORE INSERT ON trust_notice_template_removals
FOR EACH ROW EXECUTE FUNCTION trust_lock_notice_templates();

CREATE TRIGGER trust_recorded_by BEFORE INSERT ON trust_notice_templates
FOR EACH ROW EXECUTE FUNCTION trust_name_recorder();
CREATE TRIGGER trust_recorded_by BEFORE INSERT ON trust_notice_template_versions
FOR EACH ROW EXECUTE FUNCTION trust_name_recorder();
CREATE TRIGGER trust_recorded_by BEFORE INSERT ON trust_notice_template_removals
FOR EACH ROW EXECUTE FUNCTION trust_name_recorder();
CREATE TRIGGER trust_recorded_by BEFORE INSERT ON trust_notices
FOR EACH ROW EXECUTE FUNCTION trust_name_recorder();

CREATE TRIGGER trust_append_only
BEFORE UPDATE OR DELETE OR TRUNCATE ON trust_notice_templates
FOR EACH STATEMENT EXECUTE FUNCTION trust_refuse_ledger_rewrite();
CREATE TRIGGER trust_append_only
BEFORE UPDATE OR DELETE OR TRUNCATE ON trust_notice_template_versions
FOR EACH STATEMENT EXECUTE FUNCTION trust_refuse_ledger_rewrite();
CREATE TRIGGER trust_append_only
BEFORE UPDATE OR DELETE OR TRUNCATE ON trust_notice_template_removals
FOR EACH STATEMENT EXECUTE FUNCTION trust_refuse_ledger_rewrite();
CREATE TRIGGER trust_append_only
BEFORE UPDATE OR DELETE OR TRUNCATE ON trust_notices
FOR EACH STATEMENT EXECUTE FUNCTION trust_refuse_ledger_rewrite();

CREATE TRIGGER trust_audit AFTER INSERT OR UPDATE OR DELETE ON trust_notice_templates
FOR EACH ROW EXECUTE FUNCTION trust_audit();
CREATE TRIGGER trust_audit AFTER INSERT OR UPDATE OR DELETE ON trust_notice_template_versions
FOR EACH ROW EXECUTE FUNCTION trust_audit();
CREATE TRIGGER trust_audit AFTER INSERT OR UPDATE OR DELETE ON trust_notice_template_removals
FOR EACH ROW EXECUTE FUNCTION trust_audit();
CREATE TRIGGER trust_audit AFTER INSERT OR UPDATE OR DELETE ON trust_notices
FOR EACH ROW EXECUTE FUNCTION trust_audit();

REVOKE ALL ON trust_notice_templates FROM PUBLIC;
REVOKE ALL ON trust_notice_template_versions FROM PUBLIC;
REVOKE ALL ON trust_notice_template_removals FROM PUBLIC;
REVOKE ALL ON trust_notices FROM PUBLIC;
REVOKE ALL ON FUNCTION trust_check_notice_template_version() FROM PUBLIC;
REVOKE ALL ON FUNCTION trust_lock_notice_templates() FROM PUBLIC;
GRANT SELECT, INSERT ON trust_notice_templates TO trust_app;
GRANT SELECT, INSERT ON trust_notice_template_versions TO trust_app;
GRANT SELECT, INSERT ON trust_notice_template_removals TO trust_app;
GRANT SELECT, INSERT ON trust_notices TO trust_app;
GRANT SELECT ON trust_notice_templates TO trust_ai_agent;
GRANT SELECT ON trust_notice_template_versions TO trust_ai_agent;
GRANT SELECT ON trust_notice_template_removals TO trust_ai_agent;


-- migrate:down
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '60s';

-- squawk-ignore ban-drop-table
DROP TABLE trust_notices;
-- squawk-ignore ban-drop-table
DROP TABLE trust_notice_template_removals;
-- squawk-ignore ban-drop-table
DROP TABLE trust_notice_template_versions;
-- squawk-ignore ban-drop-table
DROP TABLE trust_notice_templates;
-- squawk-ignore ban-drop-function
DROP FUNCTION trust_lock_notice_templates();
-- squawk-ignore ban-drop-function
DROP FUNCTION trust_check_notice_template_version();
