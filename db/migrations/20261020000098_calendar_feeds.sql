-- migrate:up
-- Calendar feeds (the agreed order after e-sign; src/pms/api/calendar.py): a staff member or a tenant
-- subscribes their own calendar (Google, Apple, Outlook) to a link of the PMS's, which answers
-- with iCalendar text of what is coming: for staff, the company's move-ins and move-outs, visits
-- and due days of work orders, showings, bills due, rent changes and insurance running out (maybe
-- of one property group); for a tenant, their rent due days, their requests' visits and their
-- lease's last day.
--
--   trust_calendar_feeds             a person's feed: its token kept only as its SHA-256, naming
--                                    who made it (the staff member; nobody for a tenant's own).
--                                    A newer feed of the person takes the older back (the API
--                                    revokes it in the same transaction).
--   trust_calendar_feed_revocations  a feed taken back, once, naming who did it.
--
-- A calendar app fetches the feed with no login, so its token is in the link itself; the server's
-- access log never prints it. Events name homes and work, never a tenant's contact details. They
-- move no money, and the AI role reads none of it.

-- On a live database, give up rather than queue behind a long transaction while holding locks.
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '60s';

CREATE TABLE trust_calendar_feeds (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    pmc_id uuid NOT NULL REFERENCES trust_pmcs (pmc_id),
    staff_id uuid,
    tenant_id uuid,
    -- A staff member's feed may show one property group's properties only.
    group_id uuid,
    token_sha256 bytea NOT NULL UNIQUE CHECK (length(token_sha256) = 32),
    recorded_by uuid,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    UNIQUE (pmc_id, id),
    CHECK (num_nonnulls(staff_id, tenant_id) = 1),
    CHECK (group_id IS NULL OR staff_id IS NOT NULL),
    FOREIGN KEY (pmc_id, staff_id) REFERENCES trust_staff (pmc_id, id),
    FOREIGN KEY (pmc_id, tenant_id) REFERENCES trust_tenants (pmc_id, id),
    FOREIGN KEY (pmc_id, group_id) REFERENCES trust_property_groups (pmc_id, id),
    FOREIGN KEY (pmc_id, recorded_by) REFERENCES trust_staff (pmc_id, id)
);
CREATE INDEX trust_calendar_feeds_staff ON trust_calendar_feeds (pmc_id, staff_id);
CREATE INDEX trust_calendar_feeds_tenant ON trust_calendar_feeds (pmc_id, tenant_id);
CREATE INDEX trust_calendar_feeds_group ON trust_calendar_feeds (pmc_id, group_id);
CREATE INDEX trust_calendar_feeds_recorded_by ON trust_calendar_feeds (pmc_id, recorded_by);

CREATE TRIGGER trust_recorded_by BEFORE INSERT ON trust_calendar_feeds
FOR EACH ROW EXECUTE FUNCTION trust_name_recorder();
CREATE TRIGGER trust_append_only
BEFORE UPDATE OR DELETE OR TRUNCATE ON trust_calendar_feeds
FOR EACH STATEMENT EXECUTE FUNCTION trust_refuse_ledger_rewrite();
CREATE TRIGGER trust_audit AFTER INSERT OR UPDATE OR DELETE ON trust_calendar_feeds
FOR EACH ROW EXECUTE FUNCTION trust_audit();

REVOKE ALL ON trust_calendar_feeds FROM PUBLIC;
GRANT SELECT, INSERT ON trust_calendar_feeds TO trust_app;

CREATE TABLE trust_calendar_feed_revocations (
    feed_id uuid PRIMARY KEY,
    pmc_id uuid NOT NULL,
    recorded_by uuid,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    FOREIGN KEY (pmc_id, feed_id) REFERENCES trust_calendar_feeds (pmc_id, id),
    FOREIGN KEY (pmc_id, recorded_by) REFERENCES trust_staff (pmc_id, id)
);
CREATE INDEX trust_calendar_feed_revocations_recorded_by ON trust_calendar_feed_revocations (
    pmc_id, recorded_by
);

CREATE TRIGGER trust_recorded_by BEFORE INSERT ON trust_calendar_feed_revocations
FOR EACH ROW EXECUTE FUNCTION trust_name_recorder();
CREATE TRIGGER trust_append_only
BEFORE UPDATE OR DELETE OR TRUNCATE ON trust_calendar_feed_revocations
FOR EACH STATEMENT EXECUTE FUNCTION trust_refuse_ledger_rewrite();
CREATE TRIGGER trust_audit AFTER INSERT OR UPDATE OR DELETE ON trust_calendar_feed_revocations
FOR EACH ROW EXECUTE FUNCTION trust_audit();

REVOKE ALL ON trust_calendar_feed_revocations FROM PUBLIC;
GRANT SELECT, INSERT ON trust_calendar_feed_revocations TO trust_app;

-- migrate:down
DO $$
BEGIN
    RAISE EXCEPTION 'calendar_feeds cannot be rolled back: feeds and their revocations are '
        'records';
END;
$$;
