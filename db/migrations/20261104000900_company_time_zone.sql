-- migrate:up
-- A company's time zone (the founder's review of 11/03, item 13): rent paid at 9 pm Eastern on
-- October 31 is October's, not November's. A company's day (the day its money is dated, the day
-- its nightly run belongs to, what "today" means on its pages) is the date in its own time zone,
-- not UTC's. The books already date money by its day (a transfer's event_at is that day at 00:00
-- UTC) and every report reads days, so no report changes: only what "today" is.
--
-- A company picks one of the US time zones or UTC in Company setup (trust_company_profiles, the
-- latest row holding); one that picked none is in America/New_York. Every zone allowed is at or
-- behind UTC, so a company's day is never after UTC's: the database's own "not after today"
-- checks, in UTC, never refuse a company's today.

-- On a live database, give up rather than queue behind a long transaction while holding locks.
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '60s';

ALTER TABLE trust_company_profiles ADD COLUMN time_zone text
CHECK (time_zone IN (
    'America/New_York', 'America/Chicago', 'America/Denver', 'America/Phoenix',
    'America/Los_Angeles', 'America/Anchorage', 'Pacific/Honolulu', 'America/Puerto_Rico', 'UTC'
));

-- The company's time zone: its latest profile's, else America/New_York.
CREATE FUNCTION trust_pmc_time_zone(p_pmc_id uuid) RETURNS text
LANGUAGE sql STABLE
SET search_path = public, pg_temp
AS $$
    SELECT coalesce(
        (SELECT p.time_zone FROM trust_company_profiles AS p
         WHERE p.pmc_id = p_pmc_id
         ORDER BY p.created_at DESC, p.id DESC LIMIT 1),
        'America/New_York'
    )
$$;
REVOKE ALL ON FUNCTION trust_pmc_time_zone(uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION trust_pmc_time_zone(uuid) TO trust_app;

-- The company's day at a moment (now, by default): the date in its time zone.
CREATE FUNCTION trust_pmc_day(p_pmc_id uuid, p_at timestamptz DEFAULT now()) RETURNS date
LANGUAGE sql STABLE
SET search_path = public, pg_temp
AS $$
    SELECT (p_at AT TIME ZONE trust_pmc_time_zone(p_pmc_id))::date
$$;
REVOKE ALL ON FUNCTION trust_pmc_day(uuid, timestamptz) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION trust_pmc_day(uuid, timestamptz) TO trust_app;

-- A tenant's notice to move out is for the company's today or later, and a tenant who left
-- (by the company's day) changes no request: both counted UTC's day, a day early in the evening.
CREATE OR REPLACE FUNCTION trust_check_move_out_notice() RETURNS trigger
LANGUAGE plpgsql
SET search_path = public, pg_temp
AS $$
DECLARE
    today date := trust_pmc_day(NEW.pmc_id, clock_timestamp());
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

CREATE OR REPLACE FUNCTION trust_check_request_change() RETURNS trigger
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
          AND d.left_on <= trust_pmc_day(NEW.pmc_id, clock_timestamp())
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

-- migrate:down
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '60s';
-- A company's choice is its record: undoing it would lose it.
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM trust_company_profiles WHERE time_zone IS NOT NULL) THEN
        RAISE EXCEPTION 'refusing to undo company time zones: a company picked one';
    END IF;
END;
$$;
CREATE OR REPLACE FUNCTION trust_check_move_out_notice() RETURNS trigger
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

CREATE OR REPLACE FUNCTION trust_check_request_change() RETURNS trigger
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

-- squawk-ignore ban-drop-function
DROP FUNCTION trust_pmc_day(uuid, timestamptz);
-- squawk-ignore ban-drop-function
DROP FUNCTION trust_pmc_time_zone(uuid);
-- squawk-ignore ban-drop-column
ALTER TABLE trust_company_profiles DROP COLUMN time_zone;
