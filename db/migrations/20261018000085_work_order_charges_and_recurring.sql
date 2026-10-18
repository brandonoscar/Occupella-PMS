-- migrate:up
-- Maintenance, its second part (the founder's review, q072 and q073; docs/law.md):
--
--   a charge naming a work order   what a tenant owes for a repair (damage they caused): a
--                                  one-off fee of a lease at the work order's unit, or at a unit
--                                  of its property when it is the whole property's
--                                  (trust_charges.work_order_id). The owner still pays the
--                                  vendor's bill; the tenant's payment of the charge comes back to
--                                  the owner's account as any charge's does. Never a credit.
--   trust_recurring_work_orders    a job that comes back every few months (filters, gutters, a
--                                  yearly inspection), from a day, on a day of the month; an end
--                                  row stops it. trust_open_recurring_work_orders opens each one
--                                  due through a day, once per date, with its priority and, if it
--                                  names one, its vendor assigned (left unassigned when the
--                                  vendor's insurance ran out). The nightly run calls it.

-- On a live database, give up rather than queue behind a long transaction while holding locks.
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '60s';

-- --- a tenant charged for a work order ------------------------------------------------------

ALTER TABLE trust_charges ADD COLUMN work_order_id uuid;
-- Every charge so far names no work order, so it holds the check; added NOT VALID, checked for
-- every new charge.
ALTER TABLE trust_charges ADD CONSTRAINT trust_charges_work_order_fee
CHECK (work_order_id IS NULL OR kind = 'fee') NOT VALID;
-- squawk-ignore adding-foreign-key-constraint
ALTER TABLE trust_charges ADD CONSTRAINT trust_charges_pmc_id_work_order_id_fkey
FOREIGN KEY (pmc_id, work_order_id) REFERENCES trust_work_orders (pmc_id, id) NOT VALID;
-- squawk-ignore require-concurrent-index-creation
CREATE INDEX trust_charges_work_order_id ON trust_charges (work_order_id);

-- A charge naming a work order is of a lease at its unit, or at a unit of its property when it
-- is the whole property's.
CREATE FUNCTION trust_check_work_order_charge() RETURNS trigger
LANGUAGE plpgsql
SET search_path = public, pg_temp
AS $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM trust_work_orders AS w
        JOIN trust_leases AS l ON l.id = NEW.lease_id
        JOIN trust_units AS u ON u.id = l.unit_id
        WHERE w.id = NEW.work_order_id
          AND u.property_id = w.property_id
          AND (w.unit_id IS NULL OR w.unit_id = u.id)
    ) THEN
        RAISE EXCEPTION 'trust: a work order is charged only to a lease at its unit or property'
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$;
REVOKE ALL ON FUNCTION trust_check_work_order_charge() FROM PUBLIC;

CREATE TRIGGER trust_work_order_charge BEFORE INSERT ON trust_charges
FOR EACH ROW WHEN (NEW.work_order_id IS NOT NULL)
EXECUTE FUNCTION trust_check_work_order_charge();

-- --- recurring work orders ------------------------------------------------------------------

CREATE TABLE trust_recurring_work_orders (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    pmc_id uuid NOT NULL REFERENCES trust_pmcs (pmc_id),
    property_id uuid NOT NULL,
    unit_id uuid,
    summary text NOT NULL CHECK (summary ~ '\S' AND length(summary) <= 500),
    -- squawk-ignore prefer-bigint-over-int
    every_months integer NOT NULL CHECK (every_months BETWEEN 1 AND 12),
    -- squawk-ignore prefer-bigint-over-int
    day_of_month integer NOT NULL CHECK (day_of_month BETWEEN 1 AND 28),
    starts_on date NOT NULL,
    priority text NOT NULL DEFAULT 'normal'
    CHECK (priority IN ('low', 'normal', 'high', 'urgent')),
    vendor_id uuid,
    recorded_by uuid,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    UNIQUE (pmc_id, id),
    FOREIGN KEY (pmc_id, property_id) REFERENCES trust_properties (pmc_id, id),
    FOREIGN KEY (pmc_id, unit_id) REFERENCES trust_units (pmc_id, id),
    FOREIGN KEY (pmc_id, vendor_id) REFERENCES trust_vendors (pmc_id, id),
    FOREIGN KEY (pmc_id, recorded_by) REFERENCES trust_staff (pmc_id, id)
);
CREATE INDEX trust_recurring_work_orders_pmc ON trust_recurring_work_orders (pmc_id);
CREATE INDEX trust_recurring_work_orders_property ON trust_recurring_work_orders (
    pmc_id, property_id
);
CREATE INDEX trust_recurring_work_orders_unit ON trust_recurring_work_orders (pmc_id, unit_id);
CREATE INDEX trust_recurring_work_orders_vendor ON trust_recurring_work_orders (
    pmc_id, vendor_id
);
CREATE INDEX trust_recurring_work_orders_recorded_by ON trust_recurring_work_orders (
    pmc_id, recorded_by
);

-- Its unit is one of its property's, as a work order's is.
CREATE TRIGGER trust_work_order_unit BEFORE INSERT ON trust_recurring_work_orders
FOR EACH ROW EXECUTE FUNCTION trust_check_work_order();
CREATE TRIGGER trust_recorded_by BEFORE INSERT ON trust_recurring_work_orders
FOR EACH ROW EXECUTE FUNCTION trust_name_recorder();
CREATE TRIGGER trust_append_only
BEFORE UPDATE OR DELETE OR TRUNCATE ON trust_recurring_work_orders
FOR EACH STATEMENT EXECUTE FUNCTION trust_refuse_ledger_rewrite();
CREATE TRIGGER trust_audit AFTER INSERT OR UPDATE OR DELETE ON trust_recurring_work_orders
FOR EACH ROW EXECUTE FUNCTION trust_audit();

REVOKE ALL ON trust_recurring_work_orders FROM PUBLIC;
GRANT SELECT, INSERT ON trust_recurring_work_orders TO trust_app;
GRANT SELECT ON trust_recurring_work_orders TO trust_ai_agent;

-- It stops from a day, once: no work order is opened for that day or after.
CREATE TABLE trust_recurring_work_order_ends (
    recurring_work_order_id uuid PRIMARY KEY,
    pmc_id uuid NOT NULL,
    ends_on date NOT NULL,
    recorded_by uuid,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    FOREIGN KEY (pmc_id, recurring_work_order_id) REFERENCES trust_recurring_work_orders (
        pmc_id, id
    ),
    FOREIGN KEY (pmc_id, recorded_by) REFERENCES trust_staff (pmc_id, id)
);
CREATE INDEX trust_recurring_work_order_ends_recorded_by ON trust_recurring_work_order_ends (
    pmc_id, recorded_by
);

CREATE TRIGGER trust_recorded_by BEFORE INSERT ON trust_recurring_work_order_ends
FOR EACH ROW EXECUTE FUNCTION trust_name_recorder();
CREATE TRIGGER trust_append_only
BEFORE UPDATE OR DELETE OR TRUNCATE ON trust_recurring_work_order_ends
FOR EACH STATEMENT EXECUTE FUNCTION trust_refuse_ledger_rewrite();
CREATE TRIGGER trust_audit AFTER INSERT OR UPDATE OR DELETE ON trust_recurring_work_order_ends
FOR EACH ROW EXECUTE FUNCTION trust_audit();

REVOKE ALL ON trust_recurring_work_order_ends FROM PUBLIC;
GRANT SELECT, INSERT ON trust_recurring_work_order_ends TO trust_app;
GRANT SELECT ON trust_recurring_work_order_ends TO trust_ai_agent;

-- The work order a recurring one opened, and for which day: one per recurring work order and day.
ALTER TABLE trust_work_orders ADD COLUMN recurring_work_order_id uuid;
ALTER TABLE trust_work_orders ADD COLUMN recurring_on date;
ALTER TABLE trust_work_orders ADD CONSTRAINT trust_work_orders_recurring_check
CHECK ((recurring_work_order_id IS NULL) = (recurring_on IS NULL)) NOT VALID;
-- squawk-ignore adding-foreign-key-constraint
ALTER TABLE trust_work_orders ADD CONSTRAINT trust_work_orders_pmc_id_recurring_fkey
FOREIGN KEY (pmc_id, recurring_work_order_id)
REFERENCES trust_recurring_work_orders (pmc_id, id) NOT VALID;
-- squawk-ignore require-concurrent-index-creation
CREATE UNIQUE INDEX trust_work_orders_one_per_recurring_day ON trust_work_orders (
    recurring_work_order_id, recurring_on
);

-- Opens each recurring work order of a PMC due through a day: on its day of the month, every so
-- many months from the first such day on or after it starts, until it ends. Once per recurring
-- work order and day, so a run again opens nothing new. Each opens on its day (UTC) with its
-- priority, and with its vendor assigned unless their insurance ran out (then it waits for staff
-- to assign one). The number opened.
CREATE FUNCTION trust_open_recurring_work_orders(p_pmc_id uuid, p_through date)
RETURNS integer
LANGUAGE plpgsql
SET search_path = public, pg_temp
AS $$
DECLARE
    v_due record;
    v_order uuid;
    v_opened integer := 0;
BEGIN
    IF p_through IS NULL THEN
        RAISE EXCEPTION 'trust: open recurring work orders through a day; none was given'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    FOR v_due IN
        SELECT r.id, r.pmc_id, r.property_id, r.unit_id, r.summary, r.priority, r.vendor_id,
               d.due_on
        FROM trust_recurring_work_orders AS r
        LEFT JOIN trust_recurring_work_order_ends AS e ON e.recurring_work_order_id = r.id
        CROSS JOIN LATERAL (
            -- The first day of the month on or after it starts.
            SELECT date_trunc('month', r.starts_on)
                + CASE WHEN extract(DAY FROM r.starts_on) > r.day_of_month
                    THEN interval '1 month' ELSE interval '0' END AS first_month
        ) AS f
        CROSS JOIN LATERAL generate_series(
            f.first_month, p_through::timestamp, make_interval(months => r.every_months)
        ) AS m (month)
        CROSS JOIN LATERAL (SELECT (m.month::date + r.day_of_month - 1) AS due_on) AS d
        WHERE r.pmc_id = p_pmc_id
          AND d.due_on <= p_through
          AND (e.ends_on IS NULL OR d.due_on < e.ends_on)
        ORDER BY d.due_on, r.id
    LOOP
        INSERT INTO trust_work_orders (
            pmc_id, property_id, unit_id, summary, opened_at, recurring_work_order_id,
            recurring_on
        )
        VALUES (
            v_due.pmc_id, v_due.property_id, v_due.unit_id, v_due.summary,
            v_due.due_on::timestamp AT TIME ZONE 'UTC', v_due.id, v_due.due_on
        )
        ON CONFLICT (recurring_work_order_id, recurring_on) DO NOTHING
        RETURNING id INTO v_order;
        CONTINUE WHEN v_order IS NULL;
        v_opened := v_opened + 1;
        INSERT INTO trust_work_order_details (pmc_id, work_order_id, priority)
        VALUES (v_due.pmc_id, v_order, v_due.priority);
        IF v_due.vendor_id IS NOT NULL THEN
            BEGIN
                INSERT INTO trust_work_order_steps (
                    pmc_id, work_order_id, step, vendor_id, taken_at
                )
                VALUES (v_due.pmc_id, v_order, 'assigned', v_due.vendor_id, now());
            EXCEPTION
                WHEN check_violation THEN
                    -- The vendor's insurance ran out: it waits for staff to assign one.
                    v_order := NULL;
            END;
        END IF;
    END LOOP;
    RETURN v_opened;
END;
$$;
REVOKE ALL ON FUNCTION trust_open_recurring_work_orders(uuid, date) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION trust_open_recurring_work_orders(uuid, date) TO trust_app;

-- migrate:down
DO $$
BEGIN
    RAISE EXCEPTION 'work_order_charges_and_recurring cannot be rolled back: charges, recurring '
        'work orders and the work orders they opened are records';
END;
$$;
