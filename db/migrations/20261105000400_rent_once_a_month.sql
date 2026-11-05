-- migrate:up
-- Rent and monthly charges once a month (docs/edge-cases.md M-034, M-035, D-025): the rent run
-- charged a lease due on the first again for any other day of a month it was run for, since only
-- a lease's rent per due date was unique. Run rent now with Rent due on set to the 15th charged
-- every such lease a second month's rent (and each monthly charge a second time), and a rent the
-- old system's unpaid charges carried over on another day of the month was charged again on the
-- first. Now the run charges a lease's rent for a month only while none of its rent is due in that
-- month, and a monthly charge only while nothing of it is; leases due on another day
-- (trust_charge_rent_on_due_days) were charged once a month already. Charges already written stay
-- as they are (append-only): a month charged twice before this is corrected by a credit.

-- On a live database, give up rather than queue behind a long transaction while holding locks.
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '60s';

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
      -- Once a month: none of this lease's rent is due in the month already (a run for another
      -- day of it, or a rent carried over from the old system), so no day charges it twice.
      AND NOT EXISTS (
          SELECT c.id FROM trust_charges AS c
          WHERE c.lease_id = l.id AND c.kind = 'rent'
            AND c.due_on >= date_trunc('month', p_due_on::timestamp)::date
            AND c.due_on < (date_trunc('month', p_due_on::timestamp) + interval '1 month')::date
      )
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
      -- Once a month: nothing of this monthly charge is due in the month already.
      AND NOT EXISTS (
          SELECT c.id FROM trust_charges AS c
          WHERE c.recurring_charge_id = r.id
            AND c.due_on >= date_trunc('month', p_due_on::timestamp)::date
            AND c.due_on < (date_trunc('month', p_due_on::timestamp) + interval '1 month')::date
      )
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

-- migrate:down
SET LOCAL lock_timeout = '5s';

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
