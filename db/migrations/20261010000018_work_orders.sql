-- migrate:up
-- Work orders and the unpaid bills report (founder decision, 2026-10-10: Buildium/AppFolio
-- parity).
--
--   trust_work_orders       a job at one property, and maybe one of its units: what needs
--                           doing and when it was opened. Never edited.
--   trust_work_order_steps  what happened to it since, in order: assigned to a vendor (again
--                           to reassign it), then completed or cancelled. A completed or
--                           cancelled work order is closed: no step follows.
--   trust_bills.work_order_id  the work order a bill is for, if any: one of the same property
--                           that is not cancelled.
--
-- Report trust_report_unpaid_bills: as of the end of a day (UTC), every bill not yet paid,
-- grouped by vendor, with what was set aside for it and how far past due it is.

-- On a live database, give up rather than queue behind a long transaction while holding locks.
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '60s';

CREATE TABLE trust_work_orders (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    pmc_id uuid NOT NULL REFERENCES trust_pmcs (pmc_id),
    property_id uuid NOT NULL,
    unit_id uuid,
    summary text NOT NULL CHECK (summary <> ''),
    opened_at timestamptz NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (pmc_id, id),
    FOREIGN KEY (pmc_id, property_id) REFERENCES trust_properties (pmc_id, id),
    FOREIGN KEY (pmc_id, unit_id) REFERENCES trust_units (pmc_id, id)
);
CREATE INDEX trust_work_orders_property_id ON trust_work_orders (property_id);

CREATE TABLE trust_work_order_steps (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    pmc_id uuid NOT NULL REFERENCES trust_pmcs (pmc_id),
    work_order_id uuid NOT NULL,
    step text NOT NULL CHECK (step IN ('assigned', 'completed', 'cancelled')),
    vendor_id uuid,
    taken_at timestamptz NOT NULL,
    note text,
    created_at timestamptz NOT NULL DEFAULT now(),
    FOREIGN KEY (pmc_id, work_order_id) REFERENCES trust_work_orders (pmc_id, id),
    FOREIGN KEY (pmc_id, vendor_id) REFERENCES trust_vendors (pmc_id, id),
    CHECK ((step = 'assigned') = (vendor_id IS NOT NULL))
);
-- At most one closing step per work order, even from two transactions at once.
CREATE UNIQUE INDEX trust_work_order_steps_one_close
ON trust_work_order_steps (work_order_id) WHERE step IN ('completed', 'cancelled');

-- The bill a vendor sends for a work order. Every existing bill gets NULL: no work order.
ALTER TABLE trust_bills ADD COLUMN work_order_id uuid;
-- trust_bills is small next to the ledger and every row has work_order_id NULL, so validating
-- the key scans little; a separate NOT VALID / VALIDATE step would buy nothing.
-- squawk-ignore adding-foreign-key-constraint, constraint-missing-not-valid
ALTER TABLE trust_bills ADD CONSTRAINT trust_bills_pmc_id_work_order_id_fkey
FOREIGN KEY (pmc_id, work_order_id) REFERENCES trust_work_orders (pmc_id, id);
-- squawk-ignore require-concurrent-index-creation
CREATE INDEX trust_bills_work_order_id ON trust_bills (work_order_id);

CREATE TRIGGER trust_append_only
BEFORE UPDATE OR DELETE OR TRUNCATE ON trust_work_orders
FOR EACH STATEMENT EXECUTE FUNCTION trust_refuse_ledger_rewrite();
CREATE TRIGGER trust_append_only
BEFORE UPDATE OR DELETE OR TRUNCATE ON trust_work_order_steps
FOR EACH STATEMENT EXECUTE FUNCTION trust_refuse_ledger_rewrite();

-- A work order's unit is one of its property's.
CREATE FUNCTION trust_check_work_order() RETURNS trigger
LANGUAGE plpgsql
SET search_path = public, pg_temp
AS $$
BEGIN
    IF NEW.unit_id IS NOT NULL AND NOT EXISTS (
        SELECT u.id FROM trust_units AS u
        WHERE u.id = NEW.unit_id AND u.property_id = NEW.property_id
    ) THEN
        RAISE EXCEPTION 'trust: unit % is not one of property %''s', NEW.unit_id, NEW.property_id
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER trust_work_order_unit
BEFORE INSERT ON trust_work_orders
FOR EACH ROW EXECUTE FUNCTION trust_check_work_order();

-- Steps follow one another: none before the work order was opened or before the step it
-- follows, and none after it was completed or cancelled. Steps of one work order wait for each
-- other (an advisory lock needs no grant), then each reads the steps committed before it, which
-- only READ COMMITTED does.
CREATE FUNCTION trust_check_work_order_step() RETURNS trigger
LANGUAGE plpgsql
SET search_path = public, pg_temp
AS $$
DECLARE
    v_closed trust_work_order_steps;
    v_earliest timestamptz;
BEGIN
    IF current_setting('transaction_isolation') <> 'read committed' THEN
        RAISE EXCEPTION 'trust: record work order steps at READ COMMITTED, not %',
            current_setting('transaction_isolation')
            USING ERRCODE = 'invalid_transaction_state';
    END IF;
    PERFORM pg_advisory_xact_lock(hashtextextended(NEW.work_order_id::text, 0));

    -- Asked outright, not read off "the last step": steps written in one transaction share
    -- their time, so the last can't always be told apart.
    SELECT s.* INTO v_closed
    FROM trust_work_order_steps AS s
    WHERE s.work_order_id = NEW.work_order_id AND s.step IN ('completed', 'cancelled');
    IF FOUND THEN
        RAISE EXCEPTION 'trust: work order % was % at %; nothing follows that',
            NEW.work_order_id, v_closed.step, v_closed.taken_at
            USING ERRCODE = 'check_violation';
    END IF;
    SELECT greatest(w.opened_at, max(s.taken_at)) INTO v_earliest
    FROM trust_work_orders AS w
    LEFT JOIN trust_work_order_steps AS s ON s.work_order_id = w.id
    WHERE w.id = NEW.work_order_id
    GROUP BY w.opened_at;
    IF NEW.taken_at < v_earliest THEN
        RAISE EXCEPTION 'trust: a step of work order % can''t be dated before %',
            NEW.work_order_id, v_earliest
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER trust_work_order_step_order
BEFORE INSERT ON trust_work_order_steps
FOR EACH ROW EXECUTE FUNCTION trust_check_work_order_step();

-- A bill is paid from one owner's property account of its PMC (migration 20261010000017); its
-- work order, if it has one, is for that property and not cancelled. CREATE OR REPLACE keeps
-- the trigger.
CREATE OR REPLACE FUNCTION trust_check_bill_account() RETURNS trigger
LANGUAGE plpgsql
SET search_path = public, pg_temp
AS $$
DECLARE
    v_property uuid;
BEGIN
    SELECT t.property_id INTO v_property
    FROM trust_ledger_accounts AS t
    WHERE t.ledger_account_id = NEW.ledger_account_id AND t.pmc_id = NEW.pmc_id
      AND t.kind = 'owner_property';
    IF NOT FOUND THEN
        RAISE EXCEPTION 'trust: a bill is paid from an owner''s property account in its own '
            'PMC; % is not one in PMC %', NEW.ledger_account_id, NEW.pmc_id
            USING ERRCODE = 'check_violation';
    END IF;
    IF NEW.work_order_id IS NOT NULL AND NOT EXISTS (
        SELECT w.id FROM trust_work_orders AS w
        WHERE w.id = NEW.work_order_id AND w.property_id = v_property
    ) THEN
        RAISE EXCEPTION 'trust: work order % is not for the property of account %',
            NEW.work_order_id, NEW.ledger_account_id
            USING ERRCODE = 'check_violation';
    END IF;
    IF EXISTS (
        SELECT s.id FROM trust_work_order_steps AS s
        WHERE s.work_order_id = NEW.work_order_id AND s.step = 'cancelled'
    ) THEN
        RAISE EXCEPTION 'trust: work order % was cancelled; it takes no bills', NEW.work_order_id
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$;

REVOKE ALL ON trust_work_orders, trust_work_order_steps FROM PUBLIC;
GRANT SELECT, INSERT ON trust_work_orders, trust_work_order_steps TO trust_app;
GRANT SELECT ON trust_work_orders, trust_work_order_steps TO trust_ai_agent;

-- Report: as of the end of a day (UTC), every bill dated by then and not paid by then, by vendor
-- (byte order), then due date and reference: its property, dates, amount, what was set aside
-- for it, and how many days past due it is, in buckets; a subtotal per vendor and a total.
CREATE FUNCTION trust_report_unpaid_bills(
    p_pmc_id uuid,
    p_as_of date
) RETURNS TABLE (
    line integer,
    item text,
    vendor text,
    reference text,
    property text,
    bill_date date,
    due_on date,
    days_past_due integer,
    bucket text,
    amount numeric,
    set_aside numeric
)
LANGUAGE plpgsql
STABLE
SET search_path = public, pg_temp
AS $$
#variable_conflict use_column
DECLARE
    v_cutoff timestamptz := (p_as_of + 1)::timestamp AT TIME ZONE 'UTC';
BEGIN
    IF p_as_of IS NULL THEN
        RAISE EXCEPTION 'trust: unpaid bills are as of a day; none was given'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    IF NOT EXISTS (SELECT pmc.pmc_id FROM trust_pmcs AS pmc WHERE pmc.pmc_id = p_pmc_id) THEN
        RAISE EXCEPTION 'trust: no PMC %', p_pmc_id
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    RETURN QUERY
    WITH steps AS (
        SELECT p.bill_id, p.step
        FROM trust_bill_payments AS p
        JOIN pgledger_transfers AS t ON t.id = p.transfer_id
        WHERE p.pmc_id = p_pmc_id AND t.event_at < v_cutoff
    ),
    unpaid AS (
        SELECT v.display_name AS vendor_name, b.reference AS ref, pr.display_name AS place,
               b.bill_date AS billed, b.due_on AS due, greatest(p_as_of - b.due_on, 0) AS late,
               b.amount AS owed,
               CASE WHEN EXISTS (
                   SELECT s.bill_id FROM steps AS s
                   WHERE s.bill_id = b.id AND s.step = 'set_aside'
               ) THEN b.amount ELSE 0.00 END AS held
        FROM trust_bills AS b
        JOIN trust_vendors AS v ON v.id = b.vendor_id
        JOIN trust_ledger_accounts AS t ON t.ledger_account_id = b.ledger_account_id
        JOIN trust_properties AS pr ON pr.id = t.property_id
        WHERE b.pmc_id = p_pmc_id AND b.bill_date <= p_as_of
          AND NOT EXISTS (
              SELECT s.bill_id FROM steps AS s WHERE s.bill_id = b.id AND s.step = 'paid'
          )
    ),
    lines AS (
        SELECT 0 AS section, NULL::text AS vendor_name, 1 AS step, NULL::date AS due,
               NULL::text AS ref, 'PMC' AS item, pmc.display_name AS place, NULL::date AS billed,
               NULL::integer AS late, NULL::numeric AS owed, NULL::numeric AS held
        FROM trust_pmcs AS pmc WHERE pmc.pmc_id = p_pmc_id
        UNION ALL
        SELECT 0, NULL, 2, NULL, NULL, 'as of',
               to_char(p_as_of, 'YYYY-MM-DD') || ', end of day UTC', NULL, NULL, NULL, NULL
        UNION ALL
        SELECT 1, u.vendor_name, 0, u.due, u.ref, 'bill', u.place, u.billed, u.late, u.owed,
               u.held
        FROM unpaid AS u
        UNION ALL
        SELECT 1, u.vendor_name, 1, NULL, NULL, 'vendor total', NULL, NULL, NULL,
               sum(u.owed), sum(u.held)
        FROM unpaid AS u GROUP BY u.vendor_name
        UNION ALL
        SELECT 2, NULL, 0, NULL, NULL, 'total', NULL, NULL, NULL,
               coalesce(sum(u.owed), 0.00), coalesce(sum(u.held), 0.00)
        FROM unpaid AS u
    )
    SELECT row_number() OVER (
               ORDER BY l.section, l.vendor_name COLLATE "C", l.step, l.due,
                        l.ref COLLATE "C"
           )::integer,
           l.item,
           l.vendor_name,
           l.ref,
           -- The PMC's name and the day sit in the property column of the first two lines.
           l.place,
           l.billed,
           l.due,
           l.late,
           CASE
               WHEN l.late IS NULL THEN NULL
               WHEN l.late = 0 THEN 'current'
               WHEN l.late <= 30 THEN '1-30'
               WHEN l.late <= 60 THEN '31-60'
               WHEN l.late <= 90 THEN '61-90'
               ELSE 'over 90'
           END,
           round(l.owed, 2),
           round(l.held, 2)
    FROM lines AS l
    ORDER BY 1;
END;
$$;

REVOKE ALL ON FUNCTION trust_report_unpaid_bills(uuid, date) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION trust_report_unpaid_bills(uuid, date) TO trust_app, trust_ai_agent;

-- migrate:down
DO $$
BEGIN
    RAISE EXCEPTION 'work_orders cannot be rolled back: work orders and their steps are records';
END;
$$;
