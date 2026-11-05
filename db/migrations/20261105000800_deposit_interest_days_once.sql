-- migrate:up
-- A deposit's interest is paid once for each day (docs/edge-cases.md M-081, M-082).
--
-- A tenant's deposit is one account, whatever leases they are on, and each lease's interest
-- counted all of it from that lease's own next day to pay: a tenant on two leases at once (two
-- units of one property) was paid each day twice, and so was a renewal and the lease it renewed,
-- the old one paid through a day after its end. trust_deposit_interest_on now leaves out each day
-- of the account some payment has paid already (one with a line for it), so whichever lease pays
-- a day first pays it, and the other finds nothing due for it. A tenant's records are of one
-- property, so the rate is the same either way. Payments already made stay as they are.

SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '60s';

CREATE OR REPLACE FUNCTION trust_deposit_interest_on(p_account text, p_property_id uuid, p_from date, p_through date) RETURNS numeric
    LANGUAGE sql STABLE
    SET search_path TO 'public', 'pg_temp'
    AS $$
    WITH moves AS (
        SELECT (tr.event_at AT TIME ZONE 'UTC')::date AS day, sum(e.amount) AS amount
        FROM pgledger_entries AS e
        JOIN pgledger_transfers AS tr ON tr.id = e.transfer_id
        WHERE e.account_id = p_account
          AND tr.event_at < (p_through + 1)::timestamp AT TIME ZONE 'UTC'
        GROUP BY 1
    ),
    days AS (
        SELECT d::date AS day
        FROM generate_series(p_from::timestamp, p_through::timestamp, interval '1 day') AS d
    ),
    held AS (
        SELECT d.day,
               (SELECT coalesce(sum(m.amount), 0) FROM moves AS m WHERE m.day < p_from)
               + coalesce(sum(m.amount) OVER (ORDER BY d.day), 0) AS balance
        FROM days AS d
        LEFT JOIN moves AS m ON m.day = d.day
    ),
    terms AS (
        SELECT t.starts_on, lead(t.starts_on) OVER (ORDER BY t.starts_on) AS next_on,
               t.annual_percent
        FROM trust_deposit_interest_terms AS t
        WHERE t.property_id = p_property_id
    )
    SELECT coalesce(sum(
        h.balance * t.annual_percent / 100
        / ((date_trunc('year', h.day) + interval '1 year')::date - date_trunc('year', h.day)::date)
    ), 0)
    FROM held AS h
    JOIN terms AS t ON t.starts_on <= h.day AND (t.next_on IS NULL OR h.day < t.next_on)
    -- A day of a deposit account is paid once, by whichever lease paid it: a tenant on two
    -- leases (M-082), or a renewal and the lease it renewed (M-081), share the account.
    WHERE NOT EXISTS (
        SELECT 1
        FROM trust_deposit_interest_lines AS li
        JOIN trust_deposit_interest_payments AS p ON p.id = li.payment_id
        WHERE li.deposit_account = p_account AND h.day BETWEEN p.from_on AND p.through_on
    )
$$;

-- migrate:down
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '60s';

CREATE OR REPLACE FUNCTION trust_deposit_interest_on(p_account text, p_property_id uuid, p_from date, p_through date) RETURNS numeric
    LANGUAGE sql STABLE
    SET search_path TO 'public', 'pg_temp'
    AS $$
    WITH moves AS (
        SELECT (tr.event_at AT TIME ZONE 'UTC')::date AS day, sum(e.amount) AS amount
        FROM pgledger_entries AS e
        JOIN pgledger_transfers AS tr ON tr.id = e.transfer_id
        WHERE e.account_id = p_account
          AND tr.event_at < (p_through + 1)::timestamp AT TIME ZONE 'UTC'
        GROUP BY 1
    ),
    days AS (
        SELECT d::date AS day
        FROM generate_series(p_from::timestamp, p_through::timestamp, interval '1 day') AS d
    ),
    held AS (
        SELECT d.day,
               (SELECT coalesce(sum(m.amount), 0) FROM moves AS m WHERE m.day < p_from)
               + coalesce(sum(m.amount) OVER (ORDER BY d.day), 0) AS balance
        FROM days AS d
        LEFT JOIN moves AS m ON m.day = d.day
    ),
    terms AS (
        SELECT t.starts_on, lead(t.starts_on) OVER (ORDER BY t.starts_on) AS next_on,
               t.annual_percent
        FROM trust_deposit_interest_terms AS t
        WHERE t.property_id = p_property_id
    )
    SELECT coalesce(sum(
        h.balance * t.annual_percent / 100
        / ((date_trunc('year', h.day) + interval '1 year')::date - date_trunc('year', h.day)::date)
    ), 0)
    FROM held AS h
    JOIN terms AS t ON t.starts_on <= h.day AND (t.next_on IS NULL OR h.day < t.next_on)
$$;
