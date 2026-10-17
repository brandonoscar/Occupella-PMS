-- migrate:up
-- Work orders as Buildium works them (the founder's punch list, ADR 0006, step 10).
--
-- A work order's details (`trust_work_order_details`, append-only, the latest row for a work
-- order holding, naming who recorded it): its priority, what is wrong in words, the day it is due
-- and whether the vendor may let themselves in. A step 'scheduled' records the day the vendor
-- will come (`scheduled_for`), only after a vendor is assigned. A vendor whose insurance has run
-- out (of their policies of any coverage started by the day, the one ending last ended before it)
-- is not assigned a work order: record the renewal first. A vendor with no policy on file is not
-- refused; the vendors page and the Dashboard say so. A work order's photos are documents of
-- their own kind. The work orders report counts a scheduled work order with the
-- assigned ones. Nothing here moves money.

-- On a live database, give up rather than queue behind a long transaction while holding locks.
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '60s';

CREATE TABLE trust_work_order_details (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    pmc_id uuid NOT NULL REFERENCES trust_pmcs (pmc_id),
    work_order_id uuid NOT NULL,
    priority text NOT NULL CHECK (priority IN ('low', 'normal', 'high', 'urgent')),
    description text CHECK (btrim(description) <> '' AND char_length(description) <= 5000),
    due_on date,
    -- May the vendor let themselves in: yes, call the tenant first, or no; NULL unknown.
    entry text CHECK (entry IN ('allowed', 'call first', 'not allowed')),
    recorded_by uuid,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    FOREIGN KEY (pmc_id, work_order_id) REFERENCES trust_work_orders (pmc_id, id),
    FOREIGN KEY (pmc_id, recorded_by) REFERENCES trust_staff (pmc_id, id)
);
CREATE INDEX trust_work_order_details_work_order
ON trust_work_order_details (work_order_id, created_at);
CREATE INDEX trust_work_order_details_recorded_by
ON trust_work_order_details (pmc_id, recorded_by);

CREATE TRIGGER trust_recorded_by BEFORE INSERT ON trust_work_order_details
FOR EACH ROW EXECUTE FUNCTION trust_name_recorder();
CREATE TRIGGER trust_append_only
BEFORE UPDATE OR DELETE OR TRUNCATE ON trust_work_order_details
FOR EACH STATEMENT EXECUTE FUNCTION trust_refuse_ledger_rewrite();
CREATE TRIGGER trust_audit AFTER INSERT OR UPDATE OR DELETE ON trust_work_order_details
FOR EACH ROW EXECUTE FUNCTION trust_audit();

-- The scheduled step and its day. Every step so far is assigned, completed or cancelled, with no
-- day, so it holds the new checks; they are added NOT VALID, checked for every new step.
ALTER TABLE trust_work_order_steps ADD COLUMN scheduled_for date;
-- Replaced at once by a check that allows one step more; no guarantee is lost.
-- squawk-ignore ban-drop-constraint
ALTER TABLE trust_work_order_steps DROP CONSTRAINT trust_work_order_steps_step_check;
ALTER TABLE trust_work_order_steps ADD CONSTRAINT trust_work_order_steps_step_check
CHECK (step IN ('assigned', 'scheduled', 'completed', 'cancelled')) NOT VALID;
ALTER TABLE trust_work_order_steps ADD CONSTRAINT trust_work_order_steps_scheduled_check
CHECK ((step = 'scheduled') = (scheduled_for IS NOT NULL)) NOT VALID;

-- A work order's photos: documents of their own kind, each about one work order.
ALTER TABLE trust_documents ADD COLUMN work_order_id uuid;
-- Replaced at once by a check that allows one kind more; no guarantee is lost.
-- squawk-ignore ban-drop-constraint
ALTER TABLE trust_documents DROP CONSTRAINT trust_documents_kind_check;
ALTER TABLE trust_documents ADD CONSTRAINT trust_documents_kind_check CHECK (
    kind IN ('w9', 'receipt', 'move_out_photo', 'owner_document', 'work_order_photo')
) NOT VALID;
ALTER TABLE trust_documents ADD CONSTRAINT trust_documents_work_order_check
CHECK ((kind = 'work_order_photo') = (work_order_id IS NOT NULL)) NOT VALID;
-- squawk-ignore adding-foreign-key-constraint
ALTER TABLE trust_documents ADD CONSTRAINT trust_documents_pmc_id_work_order_id_fkey
FOREIGN KEY (pmc_id, work_order_id) REFERENCES trust_work_orders (pmc_id, id) NOT VALID;
-- squawk-ignore require-concurrent-index-creation
CREATE INDEX trust_documents_work_order_id ON trust_documents (work_order_id);

-- A step 'scheduled' follows an assignment; an assignment is not to a vendor whose insurance ran
-- out by the step's day (UTC): every policy of theirs started by then ended before it. Runs after
-- trust_work_order_step_order, which takes the work order's lock and keeps the steps in order.
CREATE FUNCTION trust_check_work_order_step_vendor() RETURNS trigger
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

CREATE TRIGGER trust_work_order_step_vendor
BEFORE INSERT ON trust_work_order_steps
FOR EACH ROW EXECUTE FUNCTION trust_check_work_order_step_vendor();

-- The work orders report (migration 0054): a scheduled work order counts with the assigned.
CREATE OR REPLACE FUNCTION trust_report_work_orders(p_pmc_id uuid, p_as_of date)
RETURNS TABLE (
    line integer, item text, property text, unit text, summary text, opened_on date,
    status text, vendor text, days_open integer, billed numeric
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
        RAISE EXCEPTION 'trust: work orders are as of a day; none was given'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    IF NOT EXISTS (SELECT pmc.pmc_id FROM trust_pmcs AS pmc WHERE pmc.pmc_id = p_pmc_id) THEN
        RAISE EXCEPTION 'trust: no PMC %', p_pmc_id
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    RETURN QUERY
    WITH orders AS (
        SELECT w.id, pr.display_name AS place, u.display_name AS unit_name, w.summary AS what,
               (w.opened_at AT TIME ZONE 'UTC')::date AS opened,
               coalesce(last.step, 'open') AS state,
               (last.taken_at AT TIME ZONE 'UTC')::date AS last_on,
               coalesce(last.step IN ('completed', 'cancelled'), false) AS closed,
               (SELECT v.display_name
                FROM trust_work_order_steps AS s
                JOIN trust_vendors AS v ON v.id = s.vendor_id
                WHERE s.work_order_id = w.id AND s.step = 'assigned' AND s.taken_at < v_cutoff
                ORDER BY s.taken_at DESC, s.created_at DESC, s.id DESC
                LIMIT 1) AS vendor_name,
               -- Bills entered for it by the day and not declined by then.
               coalesce((
                   SELECT sum(b.amount) FROM trust_bills AS b
                   WHERE b.work_order_id = w.id AND b.bill_date <= p_as_of
                     AND NOT EXISTS (
                         SELECT d.bill_id FROM trust_bill_declines AS d
                         WHERE d.bill_id = b.id AND d.declined_on <= p_as_of
                     )
               ), 0.00) AS billed_total
        FROM trust_work_orders AS w
        JOIN trust_properties AS pr ON pr.id = w.property_id
        LEFT JOIN trust_units AS u ON u.id = w.unit_id
        LEFT JOIN LATERAL (
            SELECT s.step, s.taken_at FROM trust_work_order_steps AS s
            WHERE s.work_order_id = w.id AND s.taken_at < v_cutoff
            ORDER BY s.taken_at DESC, s.created_at DESC, s.id DESC
            LIMIT 1
        ) AS last ON true
        WHERE w.pmc_id = p_pmc_id AND w.opened_at < v_cutoff
    ),
    lines AS (
        SELECT 0 AS part, 0 AS step, NULL::date AS opened, NULL::text AS place,
               NULL::uuid AS order_id, 'PMC' AS what, pmc.display_name AS label,
               NULL::text AS unit_name, NULL::text AS about, NULL::text AS state,
               NULL::text AS vendor_name, NULL::integer AS days, NULL::numeric AS billed_total
        FROM trust_pmcs AS pmc WHERE pmc.pmc_id = p_pmc_id
        UNION ALL
        SELECT 0, 1, NULL, NULL, NULL, 'as of',
               to_char(p_as_of, 'YYYY-MM-DD') || ', end of day UTC', NULL, NULL, NULL, NULL,
               NULL, NULL
        UNION ALL
        -- Open ones first, oldest first; then the closed ones.
        SELECT 1, CASE WHEN o.closed THEN 1 ELSE 0 END, o.opened, o.place, o.id,
               CASE WHEN o.closed THEN 'closed' ELSE 'open' END, o.place, o.unit_name, o.what,
               o.state, o.vendor_name,
               CASE WHEN o.closed THEN o.last_on ELSE p_as_of END - o.opened, o.billed_total
        FROM orders AS o
        UNION ALL
        SELECT 2, 0, NULL, NULL, NULL, 'total', NULL, NULL,
               count(*) FILTER (WHERE NOT o.closed) || ' open ('
               || count(*) FILTER (WHERE o.state IN ('assigned', 'scheduled')) || ' assigned), '
               || count(*) FILTER (WHERE o.state = 'completed') || ' completed, '
               || count(*) FILTER (WHERE o.state = 'cancelled') || ' cancelled',
               NULL, NULL, NULL, coalesce(sum(o.billed_total), 0.00)
        FROM orders AS o
    )
    SELECT (row_number() OVER (
                ORDER BY l.part, l.step, l.opened, l.place COLLATE "C", l.order_id
            ))::integer,
           l.what, l.label, l.unit_name, l.about, l.opened, l.state, l.vendor_name, l.days,
           l.billed_total
    FROM lines AS l
    ORDER BY 1;
END;
$$;


REVOKE ALL ON trust_work_order_details FROM PUBLIC;
GRANT SELECT, INSERT ON trust_work_order_details TO trust_app;
GRANT SELECT ON trust_work_order_details TO trust_ai_agent;

-- migrate:down
DO $$
BEGIN
    RAISE EXCEPTION 'work_order_details cannot be rolled back: details and steps are records';
END;
$$;
