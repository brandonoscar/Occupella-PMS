-- migrate:up
-- The day of the month a lease's rent is due (the founder's review, q013: "they get to pick the
-- date"). Until now rent was due on the first; a lease may now pick any day from 1 to 28, from
-- the first of a month on.
--
--   trust_rent_due_days   a lease's rent due day from a month on (append-only; the latest from a
--                         month or before holds, else the first), naming who recorded it. A month
--                         whose rent is charged already keeps its due date: a new due day starts
--                         after the last month charged.
--
-- A lease's due date in a month is its due day, kept within the days the lease is in effect that
-- month (trust_rent_due_on): a lease starting after it is due on its first day, as before, and one
-- ending before it on its last. Leases due on the first are charged as before
-- (trust_charge_rent_due and trust_charge_recurring_due leave the others alone); the rest are
-- charged on their due date (trust_charge_rent_on_due_days and
-- trust_charge_recurring_on_due_days), the month's rent and recurring charges for the days the
-- lease is in effect, once a month. The rent run calls all four: rent posts by itself on the day
-- it is due. Late fees count their grace from the due date as before. No money moves.

-- On a live database, give up rather than queue behind a long transaction while holding locks.
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '60s';

CREATE TABLE trust_rent_due_days (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    pmc_id uuid NOT NULL REFERENCES trust_pmcs (pmc_id),
    lease_id uuid NOT NULL,
    starts_on date NOT NULL CHECK (extract(day FROM starts_on) = 1),
    -- squawk-ignore prefer-bigint-over-int
    due_day int NOT NULL CHECK (due_day BETWEEN 1 AND 28),
    recorded_by uuid,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    UNIQUE (lease_id, starts_on),
    FOREIGN KEY (pmc_id, lease_id) REFERENCES trust_leases (pmc_id, id),
    FOREIGN KEY (pmc_id, recorded_by) REFERENCES trust_staff (pmc_id, id)
);
CREATE INDEX trust_rent_due_days_pmc ON trust_rent_due_days (pmc_id, lease_id);
CREATE INDEX trust_rent_due_days_recorded_by ON trust_rent_due_days (pmc_id, recorded_by);

-- A due day starts in a month of the lease, after the last month whose rent is charged. Rent runs
-- and due-day changes of the PMC wait for each other, as runs and lease ends do.
CREATE FUNCTION trust_check_rent_due_day() RETURNS trigger
LANGUAGE plpgsql
SET search_path = public, pg_temp
AS $$
DECLARE
    v_lease trust_leases;
    v_charged date;
BEGIN
    SELECT l.* INTO v_lease FROM trust_leases AS l
    WHERE l.id = NEW.lease_id AND l.pmc_id = NEW.pmc_id;
    IF NOT FOUND THEN
        RETURN NEW;  -- no such lease of the PMC: the foreign key refuses it
    END IF;
    PERFORM pg_advisory_xact_lock(hashtextextended('lease days of ' || NEW.pmc_id, 0));
    IF NEW.starts_on < date_trunc('month', v_lease.starts_on::timestamp)::date
       OR NEW.starts_on > v_lease.ends_on THEN
        RAISE EXCEPTION 'trust: a rent due day starts in a month of the lease, from % to %',
            v_lease.starts_on, coalesce(v_lease.ends_on::text, 'month to month')
            USING ERRCODE = 'check_violation';
    END IF;
    SELECT max(c.due_on) INTO v_charged FROM trust_charges AS c
    WHERE c.lease_id = NEW.lease_id AND c.kind = 'rent' AND c.due_on >= NEW.starts_on;
    IF v_charged IS NOT NULL THEN
        RAISE EXCEPTION 'trust: rent due % is charged already; a new due day starts after its month',
            v_charged
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$;
REVOKE ALL ON FUNCTION trust_check_rent_due_day() FROM PUBLIC;

CREATE TRIGGER trust_rent_due_day_in_time BEFORE INSERT ON trust_rent_due_days
FOR EACH ROW EXECUTE FUNCTION trust_check_rent_due_day();
CREATE TRIGGER trust_recorded_by BEFORE INSERT ON trust_rent_due_days
FOR EACH ROW EXECUTE FUNCTION trust_name_recorder();
CREATE TRIGGER trust_append_only
BEFORE UPDATE OR DELETE OR TRUNCATE ON trust_rent_due_days
FOR EACH STATEMENT EXECUTE FUNCTION trust_refuse_ledger_rewrite();
CREATE TRIGGER trust_audit AFTER INSERT OR UPDATE OR DELETE ON trust_rent_due_days
FOR EACH ROW EXECUTE FUNCTION trust_audit();

REVOKE ALL ON trust_rent_due_days FROM PUBLIC;
GRANT SELECT, INSERT ON trust_rent_due_days TO trust_app;
GRANT SELECT ON trust_rent_due_days TO trust_ai_agent;

-- The day of the month a lease's rent is due in the month of a day: 1 unless the lease picked
-- another from that month or before.
CREATE FUNCTION trust_rent_due_day(p_lease_id uuid, p_day date) RETURNS integer
LANGUAGE sql STABLE
SET search_path = public, pg_temp
AS $$
    SELECT coalesce((
        SELECT d.due_day FROM trust_rent_due_days AS d
        WHERE d.lease_id = p_lease_id AND d.starts_on <= p_day
        ORDER BY d.starts_on DESC
        LIMIT 1
    ), 1)
$$;
REVOKE ALL ON FUNCTION trust_rent_due_day(uuid, date) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION trust_rent_due_day(uuid, date) TO trust_app;

-- A lease's rent due date in the month of a day: its due day, kept within the days the lease is
-- in effect that month; NULL when it isn't in effect at all that month.
CREATE FUNCTION trust_rent_due_on(p_lease_id uuid, p_day date) RETURNS date
LANGUAGE sql STABLE
SET search_path = public, pg_temp
AS $$
    SELECT CASE
        WHEN l.starts_on > m.month_end OR l.ends_on < m.month_start THEN NULL
        ELSE least(
            greatest(m.month_start + (trust_rent_due_day(l.id, m.month_start) - 1), l.starts_on),
            coalesce(l.ends_on, m.month_end)
        )
    END
    FROM trust_leases AS l
    CROSS JOIN (
        SELECT date_trunc('month', p_day::timestamp)::date AS month_start,
               (date_trunc('month', p_day::timestamp) + interval '1 month - 1 day')::date
                   AS month_end
    ) AS m
    WHERE l.id = p_lease_id
$$;
REVOKE ALL ON FUNCTION trust_rent_due_on(uuid, date) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION trust_rent_due_on(uuid, date) TO trust_app;

-- Leases due on the first, as before (0067); a lease due on another day is charged on it.
CREATE OR REPLACE FUNCTION trust_charge_rent_due(p_pmc_id uuid, p_due_on date)
RETURNS integer
LANGUAGE plpgsql
SET search_path = public, pg_temp
AS $$
DECLARE
    v_charged integer;
    v_first integer;
BEGIN
    IF p_due_on IS NULL THEN
        RAISE EXCEPTION 'trust: rent is charged for a due date; none was given'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    -- Leases' ends don't change while the run charges (trust_end_lease waits for it).
    PERFORM pg_advisory_xact_lock_shared(hashtextextended('lease days of ' || p_pmc_id, 0));

    -- Every lease in effect on the due date: its rent for the days of the month it is in effect,
    -- at the rent in force that month (migration 0061).
    INSERT INTO trust_charges (pmc_id, lease_id, due_on, kind, amount, memo)
    SELECT l.pmc_id, l.id, p_due_on, 'rent', r.amount,
           'Rent due ' || to_char(p_due_on, 'YYYY-MM-DD') || r.days
    FROM trust_leases AS l
    CROSS JOIN LATERAL trust_rent_for_month(
        l.starts_on, l.ends_on, trust_rent_on(l.id, p_due_on), p_due_on
    ) AS r
    WHERE l.pmc_id = p_pmc_id AND l.starts_on <= p_due_on
      AND (l.ends_on IS NULL OR p_due_on <= l.ends_on)
      AND trust_rent_due_day(l.id, p_due_on) = 1
      AND r.amount > 0  -- a day of a few cents' rent rounds to nothing
    ORDER BY l.id
    ON CONFLICT (lease_id, due_on) WHERE kind = 'rent' DO NOTHING;
    GET DIAGNOSTICS v_charged = ROW_COUNT;

    -- Every lease that starts later this month, with no rent charged in the month yet: its first
    -- month's rent, due on its first day.
    INSERT INTO trust_charges (pmc_id, lease_id, due_on, kind, amount, memo)
    SELECT l.pmc_id, l.id, l.starts_on, 'rent', r.amount,
           'Rent due ' || to_char(l.starts_on, 'YYYY-MM-DD') || r.days
    FROM trust_leases AS l
    CROSS JOIN LATERAL trust_rent_for_month(
        l.starts_on, l.ends_on, l.monthly_rent, l.starts_on
    ) AS r
    WHERE l.pmc_id = p_pmc_id AND l.starts_on > p_due_on AND r.amount > 0
      AND date_trunc('month', l.starts_on::timestamp) = date_trunc('month', p_due_on::timestamp)
      AND trust_rent_due_day(l.id, l.starts_on) = 1
      AND NOT EXISTS (
          SELECT c.id FROM trust_charges AS c
          WHERE c.lease_id = l.id AND c.kind = 'rent'
            AND date_trunc('month', c.due_on::timestamp)
                = date_trunc('month', l.starts_on::timestamp)
      )
    ORDER BY l.id
    ON CONFLICT (lease_id, due_on) WHERE kind = 'rent' DO NOTHING;
    GET DIAGNOSTICS v_first = ROW_COUNT;
    RETURN v_charged + v_first;
END;
$$;

CREATE OR REPLACE FUNCTION trust_charge_recurring_due(p_pmc_id uuid, p_due_on date)
RETURNS integer
LANGUAGE plpgsql
SET search_path = public, pg_temp
AS $$
DECLARE
    v_charged integer;
    v_first integer;
BEGIN
    IF p_due_on IS NULL THEN
        RAISE EXCEPTION 'trust: recurring charges are charged for a due date; none was given'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    -- Leases' ends don't change while the run charges (trust_end_lease waits for it).
    PERFORM pg_advisory_xact_lock_shared(hashtextextended('lease days of ' || p_pmc_id, 0));

    -- Every recurring charge in force on the due date whose lease is in effect then: its amount
    -- for the days of the month the lease is in effect.
    INSERT INTO trust_charges (pmc_id, lease_id, due_on, kind, amount, memo, recurring_charge_id)
    SELECT r.pmc_id, r.lease_id, p_due_on, r.kind, p.amount,
           r.memo || ' due ' || to_char(p_due_on, 'YYYY-MM-DD') || p.days, r.id
    FROM trust_recurring_charges AS r
    JOIN trust_leases AS l ON l.id = r.lease_id
    LEFT JOIN trust_recurring_charge_ends AS e ON e.recurring_charge_id = r.id
    CROSS JOIN LATERAL trust_rent_for_month(l.starts_on, l.ends_on, r.amount, p_due_on) AS p
    WHERE r.pmc_id = p_pmc_id AND r.starts_on <= p_due_on
      AND (e.ends_on IS NULL OR p_due_on < e.ends_on)
      AND l.starts_on <= p_due_on AND (l.ends_on IS NULL OR p_due_on <= l.ends_on)
      AND trust_rent_due_day(l.id, p_due_on) = 1
      AND p.amount > 0  -- a day of a few cents rounds to nothing
    ORDER BY r.id
    ON CONFLICT (recurring_charge_id, due_on) WHERE recurring_charge_id IS NOT NULL DO NOTHING;
    GET DIAGNOSTICS v_charged = ROW_COUNT;

    -- Every recurring charge in force on the first day of a lease that starts later this month,
    -- with nothing charged for it in the month yet: its first part month, due on that day.
    INSERT INTO trust_charges (pmc_id, lease_id, due_on, kind, amount, memo, recurring_charge_id)
    SELECT r.pmc_id, r.lease_id, l.starts_on, r.kind, p.amount,
           r.memo || ' due ' || to_char(l.starts_on, 'YYYY-MM-DD') || p.days, r.id
    FROM trust_recurring_charges AS r
    JOIN trust_leases AS l ON l.id = r.lease_id
    LEFT JOIN trust_recurring_charge_ends AS e ON e.recurring_charge_id = r.id
    CROSS JOIN LATERAL trust_rent_for_month(l.starts_on, l.ends_on, r.amount, l.starts_on) AS p
    WHERE r.pmc_id = p_pmc_id AND l.starts_on > p_due_on
      AND date_trunc('month', l.starts_on::timestamp) = date_trunc('month', p_due_on::timestamp)
      AND r.starts_on <= l.starts_on AND (e.ends_on IS NULL OR l.starts_on < e.ends_on)
      AND trust_rent_due_day(l.id, l.starts_on) = 1
      AND p.amount > 0
      AND NOT EXISTS (
          SELECT c.id FROM trust_charges AS c
          WHERE c.recurring_charge_id = r.id
            AND date_trunc('month', c.due_on::timestamp)
                = date_trunc('month', l.starts_on::timestamp)
      )
    ORDER BY r.id
    ON CONFLICT (recurring_charge_id, due_on) WHERE recurring_charge_id IS NOT NULL DO NOTHING;
    GET DIAGNOSTICS v_first = ROW_COUNT;
    RETURN v_charged + v_first;
END;
$$;

-- Each lease due on another day than the first, once its due date in the month of p_month has
-- come by p_as_of: the month's rent for the days it is in effect, at the rent in force then, due
-- on that date, once a month (a month with rent charged already, a renewal's say, is left).
CREATE FUNCTION trust_charge_rent_on_due_days(p_pmc_id uuid, p_month date, p_as_of date)
RETURNS integer
LANGUAGE plpgsql
SET search_path = public, pg_temp
AS $$
DECLARE
    v_charged integer;
BEGIN
    IF p_month IS NULL OR p_as_of IS NULL THEN
        RAISE EXCEPTION 'trust: rent is charged for a month as of a day; one was not given'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    PERFORM pg_advisory_xact_lock_shared(hashtextextended('lease days of ' || p_pmc_id, 0));

    INSERT INTO trust_charges (pmc_id, lease_id, due_on, kind, amount, memo)
    SELECT l.pmc_id, l.id, d.due_on, 'rent', r.amount,
           'Rent due ' || to_char(d.due_on, 'YYYY-MM-DD') || r.days
    FROM trust_leases AS l
    CROSS JOIN LATERAL (SELECT trust_rent_due_on(l.id, p_month) AS due_on) AS d
    CROSS JOIN LATERAL trust_rent_for_month(
        l.starts_on, l.ends_on, trust_rent_on(l.id, d.due_on), d.due_on
    ) AS r
    WHERE l.pmc_id = p_pmc_id
      AND trust_rent_due_day(l.id, p_month) <> 1
      AND d.due_on IS NOT NULL AND d.due_on <= p_as_of
      AND r.amount > 0
      AND NOT EXISTS (
          SELECT c.id FROM trust_charges AS c
          WHERE c.lease_id = l.id AND c.kind = 'rent'
            AND date_trunc('month', c.due_on::timestamp)
                = date_trunc('month', p_month::timestamp)
      )
    ORDER BY l.id
    ON CONFLICT (lease_id, due_on) WHERE kind = 'rent' DO NOTHING;
    GET DIAGNOSTICS v_charged = ROW_COUNT;
    RETURN v_charged;
END;
$$;
REVOKE ALL ON FUNCTION trust_charge_rent_on_due_days(uuid, date, date) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION trust_charge_rent_on_due_days(uuid, date, date) TO trust_app;

-- The same for each recurring charge in force on its lease's due date.
CREATE FUNCTION trust_charge_recurring_on_due_days(p_pmc_id uuid, p_month date, p_as_of date)
RETURNS integer
LANGUAGE plpgsql
SET search_path = public, pg_temp
AS $$
DECLARE
    v_charged integer;
BEGIN
    IF p_month IS NULL OR p_as_of IS NULL THEN
        RAISE EXCEPTION 'trust: recurring charges are charged for a month as of a day; one was '
            'not given'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    PERFORM pg_advisory_xact_lock_shared(hashtextextended('lease days of ' || p_pmc_id, 0));

    INSERT INTO trust_charges (pmc_id, lease_id, due_on, kind, amount, memo, recurring_charge_id)
    SELECT r.pmc_id, r.lease_id, d.due_on, r.kind, p.amount,
           r.memo || ' due ' || to_char(d.due_on, 'YYYY-MM-DD') || p.days, r.id
    FROM trust_recurring_charges AS r
    JOIN trust_leases AS l ON l.id = r.lease_id
    LEFT JOIN trust_recurring_charge_ends AS e ON e.recurring_charge_id = r.id
    CROSS JOIN LATERAL (SELECT trust_rent_due_on(l.id, p_month) AS due_on) AS d
    CROSS JOIN LATERAL trust_rent_for_month(l.starts_on, l.ends_on, r.amount, d.due_on) AS p
    WHERE r.pmc_id = p_pmc_id
      AND trust_rent_due_day(l.id, p_month) <> 1
      AND d.due_on IS NOT NULL AND d.due_on <= p_as_of
      AND r.starts_on <= d.due_on AND (e.ends_on IS NULL OR d.due_on < e.ends_on)
      AND p.amount > 0
      AND NOT EXISTS (
          SELECT c.id FROM trust_charges AS c
          WHERE c.recurring_charge_id = r.id
            AND date_trunc('month', c.due_on::timestamp)
                = date_trunc('month', p_month::timestamp)
      )
    ORDER BY r.id
    ON CONFLICT (recurring_charge_id, due_on) WHERE recurring_charge_id IS NOT NULL DO NOTHING;
    GET DIAGNOSTICS v_charged = ROW_COUNT;
    RETURN v_charged;
END;
$$;
REVOKE ALL ON FUNCTION trust_charge_recurring_on_due_days(uuid, date, date) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION trust_charge_recurring_on_due_days(uuid, date, date) TO trust_app;

-- The rent run: leases due on the first as before, and each other lease on its due date.
CREATE OR REPLACE FUNCTION trust_run_rent(p_pmc_id uuid, p_due_on date, p_late_fees_as_of date)
RETURNS uuid
LANGUAGE plpgsql
SET search_path = public, pg_temp
AS $$
DECLARE
    v_rents integer;
    v_recurring integer;
    v_late_fees integer;
    v_id uuid;
BEGIN
    -- By hand, only an admin runs it; the nightly run names nobody.
    PERFORM trust_require_role(
        p_pmc_id, nullif(current_setting('trust.staff_id', true), '')::uuid, 'admin',
        'run rent by hand'
    );
    v_rents := trust_charge_rent_due(p_pmc_id, p_due_on)
        + trust_charge_rent_on_due_days(p_pmc_id, p_due_on, p_late_fees_as_of);
    v_recurring := trust_charge_recurring_due(p_pmc_id, p_due_on)
        + trust_charge_recurring_on_due_days(p_pmc_id, p_due_on, p_late_fees_as_of);
    v_late_fees := trust_assess_late_fees(p_pmc_id, p_late_fees_as_of);
    INSERT INTO trust_rent_runs (pmc_id, due_on, late_fees_as_of, rents, recurring, late_fees)
    VALUES (p_pmc_id, p_due_on, p_late_fees_as_of, v_rents, v_recurring, v_late_fees)
    RETURNING id INTO v_id;
    RETURN v_id;
END;
$$;

-- migrate:down
DO $$
BEGIN
    RAISE EXCEPTION 'rent_due_days cannot be rolled back: due days are records';
END;
$$;
