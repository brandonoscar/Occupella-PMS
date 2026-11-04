-- migrate:up
-- Maintenance requests, the founder's review of 11/03, item 12:
--   trust_maintenance_requests.category, .location
--                                     what kind of repair it is (plumbing, electrical, appliance,
--                                     heating and cooling, pests, other) and where in the unit, as
--                                     the tenant says; null on requests filed before.
--   trust_maintenance_request_cancellations
--                                     a tenant on the lease takes a request back while it is still
--                                     received (no work order yet), once; staff open no work order
--                                     for it after. A cancellation and a work order wait for each
--                                     other, so only one of them happens.
--   trust_maintenance_request_notes   a tenant on the lease adds a note to an open request (not
--                                     taken back, and its work order, if any, not closed).
-- All append-only and audited; none moves money. The AI role reads the cancellations as it reads
-- the requests, and never the notes: a tenant's own words, as messages are (AI_NEVER_READS).

-- On a live database, give up rather than queue behind a long transaction while holding locks.
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '60s';

ALTER TABLE trust_maintenance_requests
    ADD COLUMN category text CHECK (
        category IN ('plumbing', 'electrical', 'appliance', 'heating_cooling', 'pests', 'other')
    ),
    ADD COLUMN location text CHECK (
        location ~ '\S' AND length(location) <= 200 AND location !~ '[\r\n]'
    );

CREATE TABLE trust_maintenance_request_cancellations (
    request_id uuid PRIMARY KEY,
    pmc_id uuid NOT NULL,
    lease_id uuid NOT NULL,
    -- The tenant on the lease who took it back.
    tenant_id uuid NOT NULL,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    FOREIGN KEY (pmc_id, request_id) REFERENCES trust_maintenance_requests (pmc_id, id),
    FOREIGN KEY (lease_id, tenant_id) REFERENCES trust_lease_tenants (lease_id, tenant_id)
);
CREATE INDEX trust_maintenance_request_cancellations_by_pmc
ON trust_maintenance_request_cancellations (pmc_id, created_at);

CREATE TABLE trust_maintenance_request_notes (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    pmc_id uuid NOT NULL,
    request_id uuid NOT NULL,
    lease_id uuid NOT NULL,
    -- The tenant on the lease who wrote it.
    tenant_id uuid NOT NULL,
    body text NOT NULL CHECK (body ~ '\S' AND length(body) <= 2000),
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    FOREIGN KEY (pmc_id, request_id) REFERENCES trust_maintenance_requests (pmc_id, id),
    FOREIGN KEY (lease_id, tenant_id) REFERENCES trust_lease_tenants (lease_id, tenant_id)
);
CREATE INDEX trust_maintenance_request_notes_by_request
ON trust_maintenance_request_notes (request_id, created_at);
CREATE INDEX trust_maintenance_request_notes_by_pmc
ON trust_maintenance_request_notes (pmc_id, created_at);

-- A cancellation or a note is of its request's lease, by a tenant still on it; a cancellation
-- only while the request has no work order, a note only while it is open. Taking a request back,
-- adding a note and opening its work order each take the request's own lock, so they wait for
-- each other and each sees what the one before did.
CREATE FUNCTION trust_check_request_change() RETURNS trigger
LANGUAGE plpgsql
SET search_path = public, pg_temp
AS $$
DECLARE
    v_lease uuid;
    v_order uuid;
BEGIN
    PERFORM pg_advisory_xact_lock(hashtextextended('maintenance request ' || NEW.request_id, 0));
    SELECT r.lease_id INTO v_lease FROM trust_maintenance_requests AS r WHERE r.id = NEW.request_id;
    IF v_lease IS DISTINCT FROM NEW.lease_id THEN
        RAISE EXCEPTION 'trust: request % is not of lease %', NEW.request_id, NEW.lease_id
            USING ERRCODE = 'check_violation';
    END IF;
    IF EXISTS (
        SELECT 1 FROM trust_lease_departures AS d
        WHERE d.lease_id = NEW.lease_id AND d.tenant_id = NEW.tenant_id
          AND d.left_on <= (clock_timestamp() AT TIME ZONE 'UTC')::date
    ) THEN
        RAISE EXCEPTION 'trust: this tenant has left lease % already', NEW.lease_id
            USING ERRCODE = 'check_violation';
    END IF;
    IF EXISTS (
        SELECT 1 FROM trust_maintenance_request_cancellations AS c
        WHERE c.request_id = NEW.request_id
    ) THEN
        RAISE EXCEPTION 'trust: request % was cancelled', NEW.request_id
            USING ERRCODE = 'check_violation';
    END IF;
    SELECT w.work_order_id INTO v_order
    FROM trust_maintenance_request_work_orders AS w WHERE w.request_id = NEW.request_id;
    IF TG_TABLE_NAME = 'trust_maintenance_request_cancellations' AND v_order IS NOT NULL THEN
        RAISE EXCEPTION 'trust: request % has a work order; it can no longer be cancelled',
            NEW.request_id
            USING ERRCODE = 'check_violation';
    END IF;
    IF TG_TABLE_NAME = 'trust_maintenance_request_notes' AND EXISTS (
        SELECT 1 FROM trust_work_order_steps AS s
        WHERE s.work_order_id = v_order AND s.step IN ('completed', 'cancelled')
    ) THEN
        RAISE EXCEPTION 'trust: request % is closed', NEW.request_id
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$;
REVOKE ALL ON FUNCTION trust_check_request_change() FROM PUBLIC;
CREATE TRIGGER trust_check_request_change
BEFORE INSERT ON trust_maintenance_request_cancellations
FOR EACH ROW EXECUTE FUNCTION trust_check_request_change();
CREATE TRIGGER trust_check_request_change
BEFORE INSERT ON trust_maintenance_request_notes
FOR EACH ROW EXECUTE FUNCTION trust_check_request_change();

-- Staff open no work order for a request its tenant took back.
CREATE FUNCTION trust_refuse_cancelled_request_order() RETURNS trigger
LANGUAGE plpgsql
SET search_path = public, pg_temp
AS $$
BEGIN
    PERFORM pg_advisory_xact_lock(hashtextextended('maintenance request ' || NEW.request_id, 0));
    IF EXISTS (
        SELECT 1 FROM trust_maintenance_request_cancellations AS c
        WHERE c.request_id = NEW.request_id
    ) THEN
        RAISE EXCEPTION 'trust: request % was cancelled by its tenant', NEW.request_id
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$;
REVOKE ALL ON FUNCTION trust_refuse_cancelled_request_order() FROM PUBLIC;
CREATE TRIGGER trust_refuse_cancelled_request_order
BEFORE INSERT ON trust_maintenance_request_work_orders
FOR EACH ROW EXECUTE FUNCTION trust_refuse_cancelled_request_order();

CREATE TRIGGER trust_append_only
BEFORE UPDATE OR DELETE OR TRUNCATE ON trust_maintenance_request_cancellations
FOR EACH STATEMENT EXECUTE FUNCTION trust_refuse_ledger_rewrite();
CREATE TRIGGER trust_append_only
BEFORE UPDATE OR DELETE OR TRUNCATE ON trust_maintenance_request_notes
FOR EACH STATEMENT EXECUTE FUNCTION trust_refuse_ledger_rewrite();
CREATE TRIGGER trust_audit AFTER INSERT OR UPDATE OR DELETE
ON trust_maintenance_request_cancellations
FOR EACH ROW EXECUTE FUNCTION trust_audit();
CREATE TRIGGER trust_audit AFTER INSERT OR UPDATE OR DELETE ON trust_maintenance_request_notes
FOR EACH ROW EXECUTE FUNCTION trust_audit();

REVOKE ALL ON trust_maintenance_request_cancellations FROM PUBLIC;
REVOKE ALL ON trust_maintenance_request_notes FROM PUBLIC;
GRANT SELECT, INSERT ON trust_maintenance_request_cancellations TO trust_app;
GRANT SELECT, INSERT ON trust_maintenance_request_notes TO trust_app;
GRANT SELECT ON trust_maintenance_request_cancellations TO trust_ai_agent;

-- migrate:down
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '60s';
-- What tenants said of their requests is their record: undoing it would lose it.
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM trust_maintenance_request_cancellations)
       OR EXISTS (SELECT 1 FROM trust_maintenance_request_notes)
       OR EXISTS (
           SELECT 1 FROM trust_maintenance_requests
           WHERE category IS NOT NULL OR location IS NOT NULL
       ) THEN
        RAISE EXCEPTION 'refusing to undo request details: tenants have used them';
    END IF;
END;
$$;
DROP TRIGGER trust_refuse_cancelled_request_order ON trust_maintenance_request_work_orders;
-- squawk-ignore ban-drop-function
DROP FUNCTION trust_refuse_cancelled_request_order();
-- squawk-ignore ban-drop-table
DROP TABLE trust_maintenance_request_notes;
-- squawk-ignore ban-drop-table
DROP TABLE trust_maintenance_request_cancellations;
-- squawk-ignore ban-drop-function
DROP FUNCTION trust_check_request_change();
-- squawk-ignore ban-drop-column
ALTER TABLE trust_maintenance_requests DROP COLUMN location;
-- squawk-ignore ban-drop-column
ALTER TABLE trust_maintenance_requests DROP COLUMN category;
