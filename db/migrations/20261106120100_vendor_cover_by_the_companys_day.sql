-- migrate:up
-- A vendor's insurance covers them through the company's last day of the policy
-- (docs/edge-cases.md D-153). trust_check_work_order_step_vendor read the step's day as UTC's, so
-- a New York company assigning a vendor at 9 pm on the policy's last day (02:00 UTC the next day)
-- was refused while the vendor was still covered. The step's day is now the company's
-- (trust_pmc_day, migration 20261104000900). No money moves.

SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '60s';

CREATE OR REPLACE FUNCTION trust_check_work_order_step_vendor() RETURNS trigger
LANGUAGE plpgsql
SET search_path = public, pg_temp
AS $$
DECLARE
    v_day date := trust_pmc_day(NEW.pmc_id, NEW.taken_at);
    v_expires date;
BEGIN
    IF NEW.step = 'scheduled' AND NOT EXISTS (
        SELECT s.id FROM trust_work_order_steps AS s
        WHERE s.work_order_id = NEW.work_order_id AND s.step = 'assigned'
    ) THEN
        RAISE EXCEPTION 'trust: a work order is scheduled once a vendor is assigned to it'
            USING ERRCODE = 'check_violation';
    END IF;
    IF NEW.step = 'assigned' THEN
        SELECT i.expires_on INTO v_expires
        FROM trust_vendor_insurance AS i
        WHERE i.vendor_id = NEW.vendor_id AND i.starts_on <= v_day
        ORDER BY i.expires_on DESC
        LIMIT 1;
        IF v_expires < v_day THEN
            RAISE EXCEPTION 'trust: the vendor''s insurance expired on %; record its renewal '
                'before assigning them', to_char(v_expires, 'MM/DD/YYYY')
                USING ERRCODE = 'check_violation';
        END IF;
    END IF;
    RETURN NEW;
END;
$$;

-- migrate:down
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '60s';

CREATE OR REPLACE FUNCTION trust_check_work_order_step_vendor() RETURNS trigger
LANGUAGE plpgsql
SET search_path = public, pg_temp
AS $$
DECLARE
    v_day date := (NEW.taken_at AT TIME ZONE 'UTC')::date;
    v_expires date;
BEGIN
    IF NEW.step = 'scheduled' AND NOT EXISTS (
        SELECT s.id FROM trust_work_order_steps AS s
        WHERE s.work_order_id = NEW.work_order_id AND s.step = 'assigned'
    ) THEN
        RAISE EXCEPTION 'trust: a work order is scheduled once a vendor is assigned to it'
            USING ERRCODE = 'check_violation';
    END IF;
    IF NEW.step = 'assigned' THEN
        SELECT i.expires_on INTO v_expires
        FROM trust_vendor_insurance AS i
        WHERE i.vendor_id = NEW.vendor_id AND i.starts_on <= v_day
        ORDER BY i.expires_on DESC
        LIMIT 1;
        IF v_expires < v_day THEN
            RAISE EXCEPTION 'trust: the vendor''s insurance expired on %; record its renewal '
                'before assigning them', to_char(v_expires, 'MM/DD/YYYY')
                USING ERRCODE = 'check_violation';
        END IF;
    END IF;
    RETURN NEW;
END;
$$;
