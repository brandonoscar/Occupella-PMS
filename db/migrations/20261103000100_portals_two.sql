-- migrate:up
-- The owner and tenant portals, second version (the founder's choice of 11/03, "C"): what a
-- person who rents or owns asks of their manager, in the portal.
--
--   trust_contacts.by_person         an owner gives their own contact details, as a tenant does.
--   trust_messages                   a conversation between the company and one tenant or owner:
--                                    each message by the person (recorded_by null) or by the
--                                    staff member the app names (recorded_by).
--   trust_move_out_notices           a tenant on a lease says the day they will leave; the latest
--                                    of a lease's holds. Staff end the lease as before: a notice
--                                    ends nothing and moves no money.
--   trust_announcements              what staff post to the tenants of one property, or of every
--                                    property; trust_announcement_removals takes one down, once.
--   trust_documents.uploaded_by_tenant
--                                    a tenant files a document of their lease (a lease_document),
--                                    an insurance certificate say, from the portal.
--
-- All append-only and audited; none moves money. The AI role reads none of the new tables: a
-- message or a notice is a person's own words, as a note is (AI_NEVER_READS).

-- On a live database, give up rather than queue behind a long transaction while holding locks.
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '60s';

-- An owner's own contact details, given in the portal, as a tenant's (migration 0064's rule
-- widened): by the person means no staff member recorded them.
ALTER TABLE trust_contacts DROP CONSTRAINT trust_contacts_by_person;
ALTER TABLE trust_contacts ADD CONSTRAINT trust_contacts_by_person
    CHECK (NOT by_person OR (num_nonnulls(tenant_id, owner_id) = 1 AND recorded_by IS NULL))
    NOT VALID;
ALTER TABLE trust_contacts VALIDATE CONSTRAINT trust_contacts_by_person;

CREATE TABLE trust_messages (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    pmc_id uuid NOT NULL REFERENCES trust_pmcs (pmc_id),
    -- Whose conversation it is: exactly one tenant or owner of the company.
    tenant_id uuid,
    owner_id uuid,
    -- The staff member who wrote it (named by the app, trust_name_recorder); null when the tenant
    -- or owner did.
    recorded_by uuid,
    body text NOT NULL CHECK (body ~ '\S' AND length(body) <= 4000),
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    CHECK (num_nonnulls(tenant_id, owner_id) = 1),
    UNIQUE (pmc_id, id),
    FOREIGN KEY (pmc_id, tenant_id) REFERENCES trust_tenants (pmc_id, id),
    FOREIGN KEY (pmc_id, owner_id) REFERENCES trust_owners (pmc_id, id),
    FOREIGN KEY (pmc_id, recorded_by) REFERENCES trust_staff (pmc_id, id)
);
CREATE INDEX trust_messages_by_tenant ON trust_messages (tenant_id, created_at)
WHERE tenant_id IS NOT NULL;
CREATE INDEX trust_messages_by_owner ON trust_messages (owner_id, created_at)
WHERE owner_id IS NOT NULL;
CREATE INDEX trust_messages_by_pmc ON trust_messages (pmc_id, created_at);

CREATE TABLE trust_move_out_notices (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    pmc_id uuid NOT NULL REFERENCES trust_pmcs (pmc_id),
    lease_id uuid NOT NULL,
    -- The tenant on the lease who gave it.
    tenant_id uuid NOT NULL,
    -- The last day they will live there.
    leaving_on date NOT NULL,
    note text CHECK (note ~ '\S' AND length(note) <= 1000),
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    UNIQUE (pmc_id, id),
    FOREIGN KEY (pmc_id, lease_id) REFERENCES trust_leases (pmc_id, id),
    FOREIGN KEY (lease_id, tenant_id) REFERENCES trust_lease_tenants (lease_id, tenant_id)
);
CREATE INDEX trust_move_out_notices_by_lease ON trust_move_out_notices (lease_id, created_at);
CREATE INDEX trust_move_out_notices_by_pmc ON trust_move_out_notices (pmc_id, created_at);

-- A notice is given for a lease not yet ended, by a tenant still on it, for a day from today
-- (UTC) on, within the lease and at most two years ahead. It changes nothing of the lease, so it
-- takes no lock: one given as staff end the lease only says what the tenant said.
CREATE FUNCTION trust_check_move_out_notice() RETURNS trigger
LANGUAGE plpgsql
SET search_path = public, pg_temp
AS $$
DECLARE
    today date := (clock_timestamp() AT TIME ZONE 'UTC')::date;
    lease record;
BEGIN
    SELECT l.starts_on, l.ends_on INTO lease
    FROM trust_leases AS l WHERE l.id = NEW.lease_id;
    IF lease.ends_on IS NOT NULL AND lease.ends_on < today THEN
        RAISE EXCEPTION 'trust: lease % ended on %; there is no notice to give', NEW.lease_id,
            to_char(lease.ends_on, 'MM/DD/YYYY')
            USING ERRCODE = 'check_violation';
    END IF;
    IF EXISTS (
        SELECT 1 FROM trust_lease_departures AS d
        WHERE d.lease_id = NEW.lease_id AND d.tenant_id = NEW.tenant_id
    ) THEN
        RAISE EXCEPTION 'trust: this tenant has left lease % already', NEW.lease_id
            USING ERRCODE = 'check_violation';
    END IF;
    IF NEW.leaving_on < today OR NEW.leaving_on < lease.starts_on THEN
        RAISE EXCEPTION 'trust: the day you leave is today or later, within the lease'
            USING ERRCODE = 'check_violation';
    END IF;
    IF NEW.leaving_on > today + 730 THEN
        RAISE EXCEPTION 'trust: a notice is for a day within the next two years'
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$;
REVOKE ALL ON FUNCTION trust_check_move_out_notice() FROM PUBLIC;
CREATE TRIGGER trust_check_move_out_notice
BEFORE INSERT ON trust_move_out_notices
FOR EACH ROW EXECUTE FUNCTION trust_check_move_out_notice();

CREATE TABLE trust_announcements (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    pmc_id uuid NOT NULL REFERENCES trust_pmcs (pmc_id),
    -- The property whose tenants see it; null: the tenants of every property.
    property_id uuid,
    title text NOT NULL CHECK (title ~ '\S' AND length(title) <= 200 AND title !~ '[\r\n]'),
    body text NOT NULL CHECK (body ~ '\S' AND length(body) <= 4000),
    -- The staff member who posted it (trust_name_recorder); never the command line.
    recorded_by uuid NOT NULL,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    UNIQUE (pmc_id, id),
    FOREIGN KEY (pmc_id, property_id) REFERENCES trust_properties (pmc_id, id),
    FOREIGN KEY (pmc_id, recorded_by) REFERENCES trust_staff (pmc_id, id)
);
CREATE INDEX trust_announcements_by_pmc ON trust_announcements (pmc_id, created_at);

CREATE TABLE trust_announcement_removals (
    announcement_id uuid PRIMARY KEY,
    pmc_id uuid NOT NULL,
    recorded_by uuid NOT NULL,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    FOREIGN KEY (pmc_id, announcement_id) REFERENCES trust_announcements (pmc_id, id),
    FOREIGN KEY (pmc_id, recorded_by) REFERENCES trust_staff (pmc_id, id)
);

-- Who wrote a message, posted an announcement or took one down: the staff member the app names
-- (trust.staff_id), as notes name theirs; a tenant's or an owner's own message names nobody.
CREATE TRIGGER trust_name_recorder BEFORE INSERT ON trust_messages
FOR EACH ROW EXECUTE FUNCTION trust_name_recorder();
CREATE TRIGGER trust_name_recorder BEFORE INSERT ON trust_announcements
FOR EACH ROW EXECUTE FUNCTION trust_name_recorder();
CREATE TRIGGER trust_name_recorder BEFORE INSERT ON trust_announcement_removals
FOR EACH ROW EXECUTE FUNCTION trust_name_recorder();

-- A tenant's own document of their lease: a lease_document no staff member uploaded, by a tenant
-- on that lease.
ALTER TABLE trust_documents ADD COLUMN uploaded_by_tenant uuid;
ALTER TABLE trust_documents ADD CONSTRAINT trust_documents_tenant_upload
    CHECK (uploaded_by_tenant IS NULL OR (kind = 'lease_document' AND uploaded_by IS NULL))
    NOT VALID;
ALTER TABLE trust_documents VALIDATE CONSTRAINT trust_documents_tenant_upload;
ALTER TABLE trust_documents ADD CONSTRAINT trust_documents_uploaded_by_tenant_fkey
    FOREIGN KEY (lease_id, uploaded_by_tenant) REFERENCES trust_lease_tenants (lease_id, tenant_id)
    NOT VALID;
ALTER TABLE trust_documents VALIDATE CONSTRAINT trust_documents_uploaded_by_tenant_fkey;

CREATE TRIGGER trust_append_only
BEFORE UPDATE OR DELETE OR TRUNCATE ON trust_messages
FOR EACH STATEMENT EXECUTE FUNCTION trust_refuse_ledger_rewrite();
CREATE TRIGGER trust_append_only
BEFORE UPDATE OR DELETE OR TRUNCATE ON trust_move_out_notices
FOR EACH STATEMENT EXECUTE FUNCTION trust_refuse_ledger_rewrite();
CREATE TRIGGER trust_append_only
BEFORE UPDATE OR DELETE OR TRUNCATE ON trust_announcements
FOR EACH STATEMENT EXECUTE FUNCTION trust_refuse_ledger_rewrite();
CREATE TRIGGER trust_append_only
BEFORE UPDATE OR DELETE OR TRUNCATE ON trust_announcement_removals
FOR EACH STATEMENT EXECUTE FUNCTION trust_refuse_ledger_rewrite();
CREATE TRIGGER trust_audit AFTER INSERT OR UPDATE OR DELETE ON trust_messages
FOR EACH ROW EXECUTE FUNCTION trust_audit();
CREATE TRIGGER trust_audit AFTER INSERT OR UPDATE OR DELETE ON trust_move_out_notices
FOR EACH ROW EXECUTE FUNCTION trust_audit();
CREATE TRIGGER trust_audit AFTER INSERT OR UPDATE OR DELETE ON trust_announcements
FOR EACH ROW EXECUTE FUNCTION trust_audit();
CREATE TRIGGER trust_audit AFTER INSERT OR UPDATE OR DELETE ON trust_announcement_removals
FOR EACH ROW EXECUTE FUNCTION trust_audit();

REVOKE ALL ON trust_messages FROM PUBLIC;
REVOKE ALL ON trust_move_out_notices FROM PUBLIC;
REVOKE ALL ON trust_announcements FROM PUBLIC;
REVOKE ALL ON trust_announcement_removals FROM PUBLIC;
GRANT SELECT, INSERT ON trust_messages TO trust_app;
GRANT SELECT, INSERT ON trust_move_out_notices TO trust_app;
GRANT SELECT, INSERT ON trust_announcements TO trust_app;
GRANT SELECT, INSERT ON trust_announcement_removals TO trust_app;

-- migrate:down
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '60s';
-- What tenants and owners wrote, and what they were told, is their record: undoing the tables
-- would lose it.
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM trust_messages)
       OR EXISTS (SELECT 1 FROM trust_move_out_notices)
       OR EXISTS (SELECT 1 FROM trust_announcements)
       OR EXISTS (SELECT 1 FROM trust_documents WHERE uploaded_by_tenant IS NOT NULL)
       OR EXISTS (SELECT 1 FROM trust_contacts WHERE by_person AND owner_id IS NOT NULL) THEN
        RAISE EXCEPTION 'refusing to undo the portals: tenants and owners have written in them';
    END IF;
END;
$$;
ALTER TABLE trust_documents DROP CONSTRAINT trust_documents_uploaded_by_tenant_fkey;
ALTER TABLE trust_documents DROP CONSTRAINT trust_documents_tenant_upload;
ALTER TABLE trust_documents DROP COLUMN uploaded_by_tenant;
DROP TABLE trust_announcement_removals;
DROP TABLE trust_announcements;
DROP TABLE trust_move_out_notices;
DROP FUNCTION trust_check_move_out_notice();
DROP TABLE trust_messages;
ALTER TABLE trust_contacts DROP CONSTRAINT trust_contacts_by_person;
ALTER TABLE trust_contacts ADD CONSTRAINT trust_contacts_by_person
    CHECK (NOT by_person OR (tenant_id IS NOT NULL AND recorded_by IS NULL)) NOT VALID;
ALTER TABLE trust_contacts VALIDATE CONSTRAINT trust_contacts_by_person;
