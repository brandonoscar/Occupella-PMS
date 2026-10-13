-- migrate:up
-- Prorated rent (founder decisions, 2026-10-13, product item 3): a part month is charged for
-- its actual days ("monthly rent x days of the lease in that month / days in that month,
-- rounded to the cent"), and the rent run charges a lease's first part month, due on the
-- lease's first day; the last month is cut to the lease's end the same way.
--
-- trust_charge_rent_due(pmc, due date) now charges, for the calendar month of the due date:
--   - every lease in effect on the due date, as before, due that day: its rent for the days of
--     the month it is in effect (all of them for a lease that neither starts nor ends in it);
--   - every lease that starts later in that month, with no rent charged in the month yet, due
--     on its first day: its rent for the days of the month it is in effect. A lease entered
--     after the month's run is charged by running it again. (The run never reaches back into an
--     earlier month: a lease brought over from another system had that month charged there.)
-- Rent is still charged at most once per lease and due date, so a retried or doubled run bills
-- nothing twice; a full month is still "Rent due YYYY-MM-DD", and a part month says which days.
-- A part month whose rent rounds to nothing (a day of a few cents) is not charged.

-- On a live database, give up rather than queue behind a long transaction while holding locks.
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '60s';

-- The rent of a lease from p_starts_on to p_ends_on (NULL: no end) at p_monthly_rent, for the
-- days it is in effect in the month of p_day: the amount, rounded to the cent, and how a charge
-- says which days ('' for the whole month, else ", 2026-03-15 to 2026-03-31 (17 of 31 days)").
CREATE FUNCTION trust_rent_for_month(
    p_starts_on date, p_ends_on date, p_monthly_rent numeric, p_day date
)
RETURNS TABLE (amount numeric, days text)
LANGUAGE sql
IMMUTABLE
SET search_path = public, pg_temp
AS $$
    SELECT round(p_monthly_rent * (d.last_day - d.first_day + 1) / d.month_days, 2),
           CASE WHEN d.last_day - d.first_day + 1 = d.month_days THEN ''
                ELSE ', ' || to_char(d.first_day, 'YYYY-MM-DD') || ' to '
                     || to_char(d.last_day, 'YYYY-MM-DD') || ' ('
                     || (d.last_day - d.first_day + 1) || ' of ' || d.month_days || ' days)'
           END
    FROM (
        SELECT greatest(p_starts_on, m.month_start) AS first_day,
               least(coalesce(p_ends_on, m.month_end), m.month_end) AS last_day,
               m.month_end - m.month_start + 1 AS month_days
        FROM (
            SELECT date_trunc('month', p_day::timestamp)::date AS month_start,
                   (date_trunc('month', p_day::timestamp) + interval '1 month - 1 day')::date
                       AS month_end
        ) AS m
    ) AS d
$$;

REVOKE ALL ON FUNCTION trust_rent_for_month(date, date, numeric, date) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION trust_rent_for_month(date, date, numeric, date) TO trust_app;

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

    -- Every lease in effect on the due date: its rent for the days of the month it is in effect.
    INSERT INTO trust_charges (pmc_id, lease_id, due_on, kind, amount, memo)
    SELECT l.pmc_id, l.id, p_due_on, 'rent', r.amount,
           'Rent due ' || to_char(p_due_on, 'YYYY-MM-DD') || r.days
    FROM trust_leases AS l
    CROSS JOIN LATERAL trust_rent_for_month(
        l.starts_on, l.ends_on, l.monthly_rent, p_due_on
    ) AS r
    WHERE l.pmc_id = p_pmc_id AND l.starts_on <= p_due_on
      AND (l.ends_on IS NULL OR p_due_on <= l.ends_on)
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
BEGIN
    IF p_due_on IS NULL THEN
        RAISE EXCEPTION 'trust: rent is charged for a due date; none was given'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    INSERT INTO trust_charges (pmc_id, lease_id, due_on, kind, amount, memo)
    SELECT l.pmc_id, l.id, p_due_on, 'rent', l.monthly_rent,
           'Rent due ' || to_char(p_due_on, 'YYYY-MM-DD')
    FROM trust_leases AS l
    WHERE l.pmc_id = p_pmc_id AND l.starts_on <= p_due_on
      AND (l.ends_on IS NULL OR p_due_on <= l.ends_on)
    ORDER BY l.id
    ON CONFLICT (lease_id, due_on) WHERE kind = 'rent' DO NOTHING;
    GET DIAGNOSTICS v_charged = ROW_COUNT;
    RETURN v_charged;
END;
$$;

-- Nothing but the rent run above calls it.
-- squawk-ignore ban-drop-function
DROP FUNCTION trust_rent_for_month(date, date, numeric, date);
