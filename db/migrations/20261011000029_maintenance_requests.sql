-- migrate:up
-- Maintenance requests, for the tenant portal (founder, 2026-10-11:
-- docs/adr/0002-portals-and-exports.md).
--
--   trust_maintenance_requests  what a tenant asks to have fixed: who asked, under which of
--                               their leases, what, and whether staff may enter while they're
--                               out. Never edited. Only a tenant on the lease can ask under it.
--   trust_maintenance_request_work_orders
--                               the work order staff opened for a request, at most one each
--                               way. It is for the lease's unit. What happens next is that work
--                               order's steps (assigned, completed or cancelled), which the
--                               tenant reads as the request's status.

-- On a live database, give up rather than queue behind a long transaction while holding locks.
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '60s';

CREATE TABLE trust_maintenance_requests (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    pmc_id uuid NOT NULL REFERENCES trust_pmcs (pmc_id),
    lease_id uuid NOT NULL,
    tenant_id uuid NOT NULL,
    summary text NOT NULL CHECK (summary ~ '\S' AND length(summary) <= 2000),
    entry_allowed boolean NOT NULL,
    submitted_at timestamptz NOT NULL DEFAULT now(),
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (pmc_id, id),
    FOREIGN KEY (pmc_id, lease_id) REFERENCES trust_leases (pmc_id, id),
    FOREIGN KEY (pmc_id, tenant_id) REFERENCES trust_tenants (pmc_id, id),
    -- The tenant who asks is one of the lease's.
    FOREIGN KEY (lease_id, tenant_id) REFERENCES trust_lease_tenants (lease_id, tenant_id)
);
CREATE INDEX trust_maintenance_requests_lease_id ON trust_maintenance_requests (lease_id);
CREATE INDEX trust_maintenance_requests_tenant_id ON trust_maintenance_requests (tenant_id);

CREATE TABLE trust_maintenance_request_work_orders (
    request_id uuid PRIMARY KEY,
    pmc_id uuid NOT NULL REFERENCES trust_pmcs (pmc_id),
    work_order_id uuid NOT NULL UNIQUE,
    created_at timestamptz NOT NULL DEFAULT now(),
    FOREIGN KEY (pmc_id, request_id) REFERENCES trust_maintenance_requests (pmc_id, id),
    FOREIGN KEY (pmc_id, work_order_id) REFERENCES trust_work_orders (pmc_id, id)
);

CREATE TRIGGER trust_append_only
BEFORE UPDATE OR DELETE OR TRUNCATE ON trust_maintenance_requests
FOR EACH STATEMENT EXECUTE FUNCTION trust_refuse_ledger_rewrite();
CREATE TRIGGER trust_append_only
BEFORE UPDATE OR DELETE OR TRUNCATE ON trust_maintenance_request_work_orders
FOR EACH STATEMENT EXECUTE FUNCTION trust_refuse_ledger_rewrite();

-- The work order for a request is for the lease's unit. (Its property follows: a work order's
-- unit is one of its property's, trust_check_work_order.)
CREATE FUNCTION trust_check_request_work_order() RETURNS trigger
LANGUAGE plpgsql
SET search_path = public, pg_temp
AS $$
BEGIN
    IF NOT EXISTS (
        SELECT w.id
        FROM trust_maintenance_requests AS r
        JOIN trust_leases AS l ON l.id = r.lease_id
        JOIN trust_units AS u ON u.id = l.unit_id
        JOIN trust_work_orders AS w ON w.id = NEW.work_order_id AND w.unit_id = u.id
        WHERE r.id = NEW.request_id
    ) THEN
        RAISE EXCEPTION 'trust: work order % is not for the unit of request %''s lease',
            NEW.work_order_id, NEW.request_id
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER trust_request_work_order_unit
BEFORE INSERT ON trust_maintenance_request_work_orders
FOR EACH ROW EXECUTE FUNCTION trust_check_request_work_order();

CREATE TRIGGER trust_audit AFTER INSERT OR UPDATE OR DELETE ON trust_maintenance_requests
FOR EACH ROW EXECUTE FUNCTION trust_audit();
CREATE TRIGGER trust_audit AFTER INSERT OR UPDATE OR DELETE
ON trust_maintenance_request_work_orders
FOR EACH ROW EXECUTE FUNCTION trust_audit();

REVOKE ALL ON trust_maintenance_requests, trust_maintenance_request_work_orders FROM PUBLIC;
GRANT SELECT, INSERT ON trust_maintenance_requests, trust_maintenance_request_work_orders
TO trust_app;
GRANT SELECT ON trust_maintenance_requests, trust_maintenance_request_work_orders
TO trust_ai_agent;

-- migrate:down
DO $$
BEGIN
    RAISE EXCEPTION 'maintenance_requests cannot be rolled back: requests are records';
END;
$$;
