-- migrate:up
-- Announcements and staff to-dos (the founder, 2026-10-23, lane 2's third item; ADR 0009).
--
--   trust_announcements        what a staff member wrote to every tenant living at, or every owner
--                              of, a property, a property group or the whole company: its
--                              audience, where, subject and text, naming who sent it.
--   trust_emails.announcement_id
--                              each person's email of it: a staff member's message (the outbox's
--                              'message' kind) to a tenant or an owner, once per address.
--   trust_todos                a staff to-do: what to do, maybe notes and a property it is about,
--                              naming who wrote it.
--   trust_todo_assignments     who it is assigned to and the day it is due: the latest row holds,
--                              naming who changed it; a to-do has one from the start (checked at
--                              commit) and takes none once done.
--   trust_todo_completions     a to-do done, once, naming who did it.
--
-- All append-only and audited. Nothing here moves money. The AI role reads none of it, as it reads
-- no email and no note.

-- On a live database, give up rather than queue behind a long transaction while holding locks.
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '60s';

CREATE TABLE trust_announcements (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    pmc_id uuid NOT NULL REFERENCES trust_pmcs (pmc_id),
    audience text NOT NULL CHECK (audience IN ('tenants', 'owners')),
    -- Where: one property, one property group, or (both none) the whole company.
    property_id uuid,
    group_id uuid,
    subject text NOT NULL
        CHECK (length(subject) BETWEEN 1 AND 200 AND subject !~ '[\r\n]'),
    body text NOT NULL CHECK (length(body) BETWEEN 1 AND 8000),
    sent_by uuid NOT NULL,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    UNIQUE (pmc_id, id),
    FOREIGN KEY (pmc_id, property_id) REFERENCES trust_properties (pmc_id, id),
    FOREIGN KEY (pmc_id, group_id) REFERENCES trust_property_groups (pmc_id, id),
    FOREIGN KEY (pmc_id, sent_by) REFERENCES trust_staff (pmc_id, id),
    CHECK (property_id IS NULL OR group_id IS NULL)
);
CREATE INDEX trust_announcements_listed ON trust_announcements (pmc_id, created_at);
CREATE INDEX trust_announcements_property ON trust_announcements (pmc_id, property_id);
CREATE INDEX trust_announcements_group ON trust_announcements (pmc_id, group_id);
CREATE INDEX trust_announcements_sent_by ON trust_announcements (pmc_id, sent_by);

-- Each person's email of an announcement: a staff member's message to a tenant or an owner. Every
-- email so far names none, so the check and the key hold for them; both are added NOT VALID and
-- checked for every new email.
ALTER TABLE trust_emails ADD COLUMN announcement_id uuid;
ALTER TABLE trust_emails ADD CONSTRAINT trust_emails_announcement_check
CHECK (
    announcement_id IS NULL
    OR (kind = 'message' AND sent_by IS NOT NULL
        AND (tenant_id IS NOT NULL OR owner_id IS NOT NULL))
) NOT VALID;
-- squawk-ignore adding-foreign-key-constraint
ALTER TABLE trust_emails ADD CONSTRAINT trust_emails_announcement_id_fkey
FOREIGN KEY (announcement_id) REFERENCES trust_announcements (id) NOT VALID;
-- Once per announcement and address. No email names an announcement yet, so this builds in
-- moments; CONCURRENTLY can't run inside the migration's transaction.
-- squawk-ignore require-concurrent-index-creation
CREATE UNIQUE INDEX trust_emails_one_announcement
ON trust_emails (announcement_id, lower(to_address)) WHERE announcement_id IS NOT NULL;

CREATE TABLE trust_todos (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    pmc_id uuid NOT NULL REFERENCES trust_pmcs (pmc_id),
    title text NOT NULL
        CHECK (title = btrim(title) AND length(title) BETWEEN 1 AND 200 AND title !~ '[\r\n]'),
    notes text CHECK (length(notes) BETWEEN 1 AND 4000),
    property_id uuid,
    recorded_by uuid NOT NULL,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    UNIQUE (pmc_id, id),
    FOREIGN KEY (pmc_id, property_id) REFERENCES trust_properties (pmc_id, id),
    FOREIGN KEY (pmc_id, recorded_by) REFERENCES trust_staff (pmc_id, id)
);
CREATE INDEX trust_todos_listed ON trust_todos (pmc_id, created_at);
CREATE INDEX trust_todos_property ON trust_todos (pmc_id, property_id);
CREATE INDEX trust_todos_recorded_by ON trust_todos (pmc_id, recorded_by);

CREATE TABLE trust_todo_assignments (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    pmc_id uuid NOT NULL REFERENCES trust_pmcs (pmc_id),
    todo_id uuid NOT NULL,
    assigned_to uuid NOT NULL,
    due_on date NOT NULL,
    recorded_by uuid,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    FOREIGN KEY (pmc_id, todo_id) REFERENCES trust_todos (pmc_id, id),
    FOREIGN KEY (pmc_id, assigned_to) REFERENCES trust_staff (pmc_id, id),
    FOREIGN KEY (pmc_id, recorded_by) REFERENCES trust_staff (pmc_id, id)
);
CREATE INDEX trust_todo_assignments_todo ON trust_todo_assignments (pmc_id, todo_id, created_at);
CREATE INDEX trust_todo_assignments_assigned_to
ON trust_todo_assignments (pmc_id, assigned_to);
CREATE INDEX trust_todo_assignments_recorded_by
ON trust_todo_assignments (pmc_id, recorded_by);

CREATE TABLE trust_todo_completions (
    todo_id uuid PRIMARY KEY,
    pmc_id uuid NOT NULL REFERENCES trust_pmcs (pmc_id),
    done_by uuid NOT NULL,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    FOREIGN KEY (pmc_id, todo_id) REFERENCES trust_todos (pmc_id, id),
    FOREIGN KEY (pmc_id, done_by) REFERENCES trust_staff (pmc_id, id)
);
CREATE INDEX trust_todo_completions_done_by ON trust_todo_completions (pmc_id, done_by);

-- A to-do is assigned from the start: at commit, each new one has an assignment.
CREATE FUNCTION trust_check_todo_assigned() RETURNS trigger
LANGUAGE plpgsql
SET search_path = public, pg_temp
AS $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM trust_todo_assignments AS a WHERE a.todo_id = NEW.id) THEN
        RAISE EXCEPTION 'trust: a to-do is assigned to someone, with a day it is due'
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NULL;
END;
$$;
REVOKE ALL ON FUNCTION trust_check_todo_assigned() FROM PUBLIC;

-- A to-do done takes no new assignment; an assignment and its completion wait for each other.
CREATE FUNCTION trust_check_todo_open() RETURNS trigger
LANGUAGE plpgsql
SET search_path = public, pg_temp
AS $$
BEGIN
    PERFORM pg_advisory_xact_lock(hashtextextended('to-do ' || NEW.todo_id, 0));
    IF EXISTS (SELECT 1 FROM trust_todo_completions AS c WHERE c.todo_id = NEW.todo_id) THEN
        RAISE EXCEPTION 'trust: that to-do is done already'
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$;
REVOKE ALL ON FUNCTION trust_check_todo_open() FROM PUBLIC;

CREATE CONSTRAINT TRIGGER trust_todo_assigned AFTER INSERT ON trust_todos
DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW EXECUTE FUNCTION trust_check_todo_assigned();
CREATE TRIGGER trust_todo_open BEFORE INSERT ON trust_todo_assignments
FOR EACH ROW EXECUTE FUNCTION trust_check_todo_open();
CREATE TRIGGER trust_todo_open BEFORE INSERT ON trust_todo_completions
FOR EACH ROW EXECUTE FUNCTION trust_check_todo_open();
CREATE TRIGGER trust_recorded_by BEFORE INSERT ON trust_todo_assignments
FOR EACH ROW EXECUTE FUNCTION trust_name_recorder();

CREATE TRIGGER trust_append_only
BEFORE UPDATE OR DELETE OR TRUNCATE ON trust_announcements
FOR EACH STATEMENT EXECUTE FUNCTION trust_refuse_ledger_rewrite();
CREATE TRIGGER trust_append_only
BEFORE UPDATE OR DELETE OR TRUNCATE ON trust_todos
FOR EACH STATEMENT EXECUTE FUNCTION trust_refuse_ledger_rewrite();
CREATE TRIGGER trust_append_only
BEFORE UPDATE OR DELETE OR TRUNCATE ON trust_todo_assignments
FOR EACH STATEMENT EXECUTE FUNCTION trust_refuse_ledger_rewrite();
CREATE TRIGGER trust_append_only
BEFORE UPDATE OR DELETE OR TRUNCATE ON trust_todo_completions
FOR EACH STATEMENT EXECUTE FUNCTION trust_refuse_ledger_rewrite();
CREATE TRIGGER trust_audit AFTER INSERT OR UPDATE OR DELETE ON trust_announcements
FOR EACH ROW EXECUTE FUNCTION trust_audit();
CREATE TRIGGER trust_audit AFTER INSERT OR UPDATE OR DELETE ON trust_todos
FOR EACH ROW EXECUTE FUNCTION trust_audit();
CREATE TRIGGER trust_audit AFTER INSERT OR UPDATE OR DELETE ON trust_todo_assignments
FOR EACH ROW EXECUTE FUNCTION trust_audit();
CREATE TRIGGER trust_audit AFTER INSERT OR UPDATE OR DELETE ON trust_todo_completions
FOR EACH ROW EXECUTE FUNCTION trust_audit();

REVOKE ALL ON trust_announcements, trust_todos, trust_todo_assignments, trust_todo_completions
FROM PUBLIC;
GRANT SELECT, INSERT ON trust_announcements, trust_todos, trust_todo_assignments,
trust_todo_completions TO trust_app;


-- migrate:down
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '60s';

-- squawk-ignore ban-drop-column
ALTER TABLE trust_emails DROP COLUMN announcement_id;
-- squawk-ignore ban-drop-table
DROP TABLE trust_announcements;
-- squawk-ignore ban-drop-table
DROP TABLE trust_todo_completions;
-- squawk-ignore ban-drop-table
DROP TABLE trust_todo_assignments;
-- squawk-ignore ban-drop-table
DROP TABLE trust_todos;
-- squawk-ignore ban-drop-function
DROP FUNCTION trust_check_todo_open();
-- squawk-ignore ban-drop-function
DROP FUNCTION trust_check_todo_assigned();
