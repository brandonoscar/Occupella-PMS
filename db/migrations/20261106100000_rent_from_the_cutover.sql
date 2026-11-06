-- migrate:up
-- Rent from the cutover (docs/edge-cases.md M-144). A company switching mid-month brings in its
-- leases after the old system charged the month's rent, and collected it or carried it over as an
-- unpaid charge. The first night after the switch charged every lease whose rent was collected the
-- whole month again (only a rent carried over stopped it), and its late fee once past the grace.
-- Now the rent run charges no rent or monthly charge due before the cutover of the trust bank
-- account holding the lease's property's owners' money (trust_lease_cutover): that month is the
-- old system's, whose unpaid charges come over with their own due dates. A lease due on the first
-- is due then whatever day Run rent now names, so a run for a day after the cutover leaves the
-- cutover's month alone too. From the cutover on, every month is charged as before; a company
-- with no opening balances is charged as before. Charges already written stay as they are
-- (append-only): a month charged twice before this is corrected by a credit.

SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '60s';

-- The day a lease's money starts in the PMS: the latest cutover of a trust bank account holding
-- an owner's account at its property; NULL when none was opened with balances carried over.
CREATE FUNCTION trust_lease_cutover(p_lease_id uuid) RETURNS date
    LANGUAGE sql STABLE
    SET search_path TO 'public', 'pg_temp'
    AS $$
    SELECT max(o.opened_on)
    FROM trust_leases AS l
    JOIN trust_units AS u ON u.id = l.unit_id
    JOIN trust_ledger_accounts AS a
      ON a.property_id = u.property_id AND a.kind = 'owner_property'
    JOIN trust_opening_balances AS o ON o.bank_account_id = a.bank_account_id
    WHERE l.id = p_lease_id;
$$;

REVOKE ALL ON FUNCTION trust_lease_cutover(uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION trust_lease_cutover(uuid) TO trust_app;

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
      -- The month's rent was due (on its first, or the lease's first day) by the PMS's books,
      -- not the old system's before the cutover.
      AND greatest(date_trunc('month', p_due_on::timestamp)::date, l.starts_on)
          >= coalesce(trust_lease_cutover(l.id), '-infinity'::date)
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
      AND l.starts_on >= coalesce(trust_lease_cutover(l.id), '-infinity'::date)
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
      -- Due by the PMS's books, not the old system's before the cutover, as rent is.
      AND greatest(date_trunc('month', p_due_on::timestamp)::date, l.starts_on, r.starts_on)
          >= coalesce(trust_lease_cutover(l.id), '-infinity'::date)
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
      AND l.starts_on >= coalesce(trust_lease_cutover(l.id), '-infinity'::date)
    ORDER BY r.id
    ON CONFLICT (recurring_charge_id, due_on) WHERE recurring_charge_id IS NOT NULL DO NOTHING;
    GET DIAGNOSTICS v_first = ROW_COUNT;
    RETURN v_charged + v_first;
END;
$$;

CREATE OR REPLACE FUNCTION trust_charge_rent_on_due_days(p_pmc_id uuid, p_month date, p_as_of date)
RETURNS integer
LANGUAGE plpgsql
SET search_path TO 'public', 'pg_temp'
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
      -- Due by the PMS's books, not the old system's before the cutover.
      AND d.due_on >= coalesce(trust_lease_cutover(l.id), '-infinity'::date)
    ORDER BY l.id
    ON CONFLICT (lease_id, due_on) WHERE kind = 'rent' DO NOTHING;
    GET DIAGNOSTICS v_charged = ROW_COUNT;
    RETURN v_charged;
END;
$$;

CREATE OR REPLACE FUNCTION trust_charge_recurring_on_due_days(
    p_pmc_id uuid, p_month date, p_as_of date
) RETURNS integer
LANGUAGE plpgsql
SET search_path TO 'public', 'pg_temp'
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
      -- Due by the PMS's books, not the old system's before the cutover.
      AND d.due_on >= coalesce(trust_lease_cutover(l.id), '-infinity'::date)
    ORDER BY r.id
    ON CONFLICT (recurring_charge_id, due_on) WHERE recurring_charge_id IS NOT NULL DO NOTHING;
    GET DIAGNOSTICS v_charged = ROW_COUNT;
    RETURN v_charged;
END;
$$;

-- migrate:down
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

CREATE OR REPLACE FUNCTION trust_charge_rent_on_due_days(p_pmc_id uuid, p_month date, p_as_of date)
RETURNS integer
LANGUAGE plpgsql
SET search_path TO 'public', 'pg_temp'
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

CREATE OR REPLACE FUNCTION trust_charge_recurring_on_due_days(
    p_pmc_id uuid, p_month date, p_as_of date
) RETURNS integer
LANGUAGE plpgsql
SET search_path TO 'public', 'pg_temp'
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

DROP FUNCTION trust_lease_cutover(uuid);
