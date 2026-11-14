-- migrate:up
-- A payment online counts for late fees from the day the tenant started it (docs/edge-cases.md
-- D-080, D-081; docs/law.md, "Rent paid online").
--
-- A bank payment takes days to settle, and the PMS records it on the day the partner reports it
-- paid. A tenant who paid online on the due day, or whose autopay started it then, was charged a
-- late fee whenever the grace was shorter than the settling: the run looked at the books when the
-- grace ended, and the payment wasn't in them yet. Now:
--
--   trust_online_started_by   whether a posting is a tenant's payment online (its key the
--                             online payment's, method online) started by a day, the company's
--                             day of trust_online_payments.created_at: it counts as paid then.
--   trust_online_on_its_way   whether a lease has a payment online started by a day still on its
--                             way to the books: not failed, come back or expired, not posted,
--                             and reported paid or started within the last 14 days (the same
--                             window as autopay's ON_ITS_WAY and Banking's "no word").
--   trust_assess_late_fees    a rent past its grace waits while one is on its way; then its own
--                             payments by the end of its grace count those started by then, and
--                             what is open of it leaves out what they paid of it.
--
-- A payment that fails, expires or is given up is no longer on its way and never paid: the rent
-- is late as it would be with no payment, and its fee is charged at the next run, dated the day
-- after its grace as always. One that comes back after it was paid is a bounce, as a staff
-- payment's is: it counted by the end of grace, so no late fee, and the property's NSF fee.

SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '60s';

-- Whether a posting is a tenant's payment online that the tenant (or their autopay) started by a
-- day: the company's day it was started, not the day the partner reported it paid.
CREATE FUNCTION trust_online_started_by(p_transfer pgledger_transfers, p_day date)
RETURNS boolean
    LANGUAGE sql STABLE
    SET search_path TO 'public', 'pg_temp'
    AS $$
    -- Its key is "payment/" and the online payment's id (rent.record_payment's, with the online
    -- payment's id as its request id), so the payment is found by its primary key.
    SELECT p_transfer.metadata ->> 'method' = 'online'
       AND p_transfer.metadata ->> 'kind' = 'tenant payment'
       AND EXISTS (
           SELECT 1 FROM trust_online_payments AS op
           WHERE op.id = CASE
                   WHEN p_transfer.metadata ->> 'key'
                        ~ '^payment/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
                   THEN substr(p_transfer.metadata ->> 'key', 9)::uuid
                 END
             AND trust_pmc_day(op.pmc_id, op.created_at) <= p_day
       )
$$;
REVOKE ALL ON FUNCTION trust_online_started_by(pgledger_transfers, date) FROM PUBLIC;

-- Whether a lease has a payment online started by a day still on its way to the books.
CREATE FUNCTION trust_online_on_its_way(p_lease_id uuid, p_day date) RETURNS boolean
    LANGUAGE sql STABLE
    SET search_path TO 'public', 'pg_temp'
    AS $$
    SELECT EXISTS (
        SELECT 1 FROM trust_online_payments AS op
        WHERE op.lease_id = p_lease_id
          AND trust_pmc_day(op.pmc_id, op.created_at) <= p_day
          AND NOT EXISTS (
              SELECT 1 FROM trust_online_payment_events AS e
              WHERE e.payment_id = op.id AND e.kind IN ('failed', 'returned', 'expired')
          )
          AND NOT EXISTS (
              SELECT 1 FROM pgledger_transfers AS t
              WHERE t.metadata ->> 'key' = 'payment/' || op.id::text
          )
          AND (op.created_at >= now() - interval '14 days'
               OR EXISTS (SELECT 1 FROM trust_online_payment_events AS e
                          WHERE e.payment_id = op.id AND e.kind = 'paid'))
    )
$$;
REVOKE ALL ON FUNCTION trust_online_on_its_way(uuid, date) FROM PUBLIC;

CREATE OR REPLACE FUNCTION trust_assess_late_fees(p_pmc_id uuid, p_as_of date) RETURNS integer
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path TO 'public', 'pg_temp'
    AS $$
DECLARE
    v_rent record;
    v_due uuid[] := '{}'::uuid[];
    v_cutoff timestamptz;
    v_unpaid numeric;
    v_fee numeric;
    v_fee_id uuid;
    v_charged integer := 0;
    v_fee_day date;
    v_closed date;
BEGIN
    IF current_setting('transaction_isolation') <> 'read committed' THEN
        RAISE EXCEPTION 'trust: assess late fees at READ COMMITTED, not %',
            current_setting('transaction_isolation')
            USING ERRCODE = 'invalid_transaction_state';
    END IF;
    IF p_as_of IS NULL THEN
        RAISE EXCEPTION 'trust: late fees are assessed as of a day; none was given'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    -- The rents past their grace without a late fee, locked one by one in id order: one lock
    -- order for every run, so two at once can't deadlock.
    FOR v_rent IN
        SELECT c.id, c.lease_id, c.due_on + p.grace_days AS grace_ends
        FROM trust_charges AS c
        JOIN trust_leases AS l ON l.id = c.lease_id
        JOIN trust_units AS u ON u.id = l.unit_id
        -- The terms in force on the rent's due date: the latest starting on or before it.
        JOIN LATERAL (
            SELECT t.grace_days
            FROM trust_late_fee_policies AS t
            WHERE t.property_id = u.property_id AND t.starts_on <= c.due_on
            ORDER BY t.starts_on DESC
            LIMIT 1
        ) AS p ON true
        WHERE c.pmc_id = p_pmc_id AND c.kind = 'rent'
          AND c.due_on + p.grace_days < p_as_of
          AND NOT EXISTS (
              SELECT f.rent_charge_id FROM trust_late_fees AS f WHERE f.rent_charge_id = c.id
          )
          -- Its own payments by the end of its grace, less what of them bounced by then, have not
          -- covered it. Credits and waivers only take more off what is open, so a rent its own
          -- payments covered is not open then, and is charged no late fee, whatever else holds.
          AND c.amount
              > coalesce((
                  SELECT sum(cp.amount)
                  FROM trust_charge_payments AS cp
                  JOIN pgledger_transfers AS t ON t.id = cp.transfer_id
                  WHERE cp.charge_id = c.id
                    AND (t.event_at < (c.due_on + p.grace_days + 1)::timestamp AT TIME ZONE 'UTC'
                         OR trust_online_started_by(t, c.due_on + p.grace_days))
              ), 0)
              - coalesce((
                  SELECT sum(r.amount)
                  FROM trust_payment_reversals AS r
                  JOIN pgledger_transfers AS t ON t.id = r.reversal_id
                  WHERE r.charge_id = c.id
                    AND t.event_at < (c.due_on + p.grace_days + 1)::timestamp AT TIME ZONE 'UTC'
              ), 0)
        ORDER BY c.id
    LOOP
        PERFORM c.id FROM trust_charges AS c WHERE c.id = v_rent.id FOR NO KEY UPDATE;
        CONTINUE WHEN EXISTS (
            SELECT f.rent_charge_id FROM trust_late_fees AS f WHERE f.rent_charge_id = v_rent.id
        );
        -- Nor while a payment online of its lease, started by the end of its grace, is still on
        -- its way to the books: it counts from the day it was started, so whether this rent is
        -- late waits until the partner says how it went (docs/edge-cases.md D-080, D-081). Asked
        -- only of the rents left open, so a rent paid in time costs a run no more than before.
        CONTINUE WHEN trust_online_on_its_way(v_rent.lease_id, v_rent.grace_ends);
        v_due := v_due || v_rent.id;
    END LOOP;

    -- Then each, in the order its grace ends, as a run every day would reach them.
    FOR v_rent IN
        SELECT c.id, c.lease_id, c.due_on, p.id AS policy_id, p.grace_days, p.flat_fee,
               p.percent, p.maximum
        FROM trust_charges AS c
        JOIN trust_leases AS l ON l.id = c.lease_id
        JOIN trust_units AS u ON u.id = l.unit_id
        JOIN LATERAL (
            SELECT t.id, t.grace_days, t.flat_fee, t.percent, t.maximum
            FROM trust_late_fee_policies AS t
            WHERE t.property_id = u.property_id AND t.starts_on <= c.due_on
            ORDER BY t.starts_on DESC
            LIMIT 1
        ) AS p ON true
        WHERE c.id = ANY (v_due)
        ORDER BY c.due_on + p.grace_days, c.due_on, c.id
    LOOP
        v_fee_day := v_rent.due_on + v_rent.grace_days + 1;
        -- A fee that would fall in a closed year is never charged: the year is locked (migration
        -- 20261021000106), and the one charge it refuses stopped the company's whole run, rent
        -- included, every night (docs/edge-cases.md M-050). The PMC is locked FOR SHARE first,
        -- as the year's own check does, so a close under way and this wait for each other.
        IF v_fee_day < date_trunc('year', now() AT TIME ZONE 'UTC')::date THEN
            IF v_closed IS NULL THEN
                PERFORM 1 FROM trust_pmcs AS p WHERE p.pmc_id = p_pmc_id FOR SHARE;
                v_closed := coalesce(trust_closed_until(p_pmc_id), '-infinity'::date);
            END IF;
            CONTINUE WHEN v_fee_day <= v_closed;
        END IF;
        v_cutoff := (v_rent.due_on + v_rent.grace_days + 1)::timestamp AT TIME ZONE 'UTC';
        -- What is open of it then: payments and bounces dated before then, and the lease's
        -- credits due by its last day of grace, oldest charges first (the fees this run charged
        -- for the rents whose grace ended before its own among them)...
        SELECT o.open INTO v_unpaid
        FROM trust_open_charges(p_pmc_id, v_rent.lease_id, v_cutoff) AS o
        WHERE o.charge_id = v_rent.id;
        -- ...less what a payment online started by the end of its grace paid of it, though the
        -- partner reported it paid after (D-080, D-081).
        v_unpaid := v_unpaid - coalesce((
            SELECT sum(cp.amount)
            FROM trust_charge_payments AS cp
            JOIN pgledger_transfers AS t ON t.id = cp.transfer_id
            WHERE cp.charge_id = v_rent.id AND t.event_at >= v_cutoff
              AND trust_online_started_by(t, v_rent.due_on + v_rent.grace_days)
        ), 0);
        CONTINUE WHEN v_unpaid <= 0;
        v_fee := least(
            round(v_rent.flat_fee + v_rent.percent / 100 * v_unpaid, 2),
            v_rent.maximum
        );
        CONTINUE WHEN v_fee <= 0;

        INSERT INTO trust_charges (pmc_id, lease_id, due_on, kind, amount, memo)
        VALUES (
            p_pmc_id, v_rent.lease_id, v_fee_day, 'fee', v_fee,
            'Late fee, rent due ' || to_char(v_rent.due_on, 'YYYY-MM-DD')
        )
        RETURNING id INTO v_fee_id;
        INSERT INTO trust_late_fees (rent_charge_id, pmc_id, policy_id, unpaid, fee, fee_charge_id)
        VALUES (v_rent.id, p_pmc_id, v_rent.policy_id, v_unpaid, v_fee, v_fee_id);
        v_charged := v_charged + 1;
    END LOOP;
    RETURN v_charged;
END;
$$;

-- migrate:down
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '60s';

CREATE OR REPLACE FUNCTION trust_assess_late_fees(p_pmc_id uuid, p_as_of date) RETURNS integer
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path TO 'public', 'pg_temp'
    AS $$
DECLARE
    v_rent record;
    v_due uuid[] := '{}'::uuid[];
    v_cutoff timestamptz;
    v_unpaid numeric;
    v_fee numeric;
    v_fee_id uuid;
    v_charged integer := 0;
    v_fee_day date;
    v_closed date;
BEGIN
    IF current_setting('transaction_isolation') <> 'read committed' THEN
        RAISE EXCEPTION 'trust: assess late fees at READ COMMITTED, not %',
            current_setting('transaction_isolation')
            USING ERRCODE = 'invalid_transaction_state';
    END IF;
    IF p_as_of IS NULL THEN
        RAISE EXCEPTION 'trust: late fees are assessed as of a day; none was given'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    -- The rents past their grace without a late fee, locked one by one in id order: one lock
    -- order for every run, so two at once can't deadlock.
    FOR v_rent IN
        SELECT c.id
        FROM trust_charges AS c
        JOIN trust_leases AS l ON l.id = c.lease_id
        JOIN trust_units AS u ON u.id = l.unit_id
        -- The terms in force on the rent's due date: the latest starting on or before it.
        JOIN LATERAL (
            SELECT t.grace_days
            FROM trust_late_fee_policies AS t
            WHERE t.property_id = u.property_id AND t.starts_on <= c.due_on
            ORDER BY t.starts_on DESC
            LIMIT 1
        ) AS p ON true
        WHERE c.pmc_id = p_pmc_id AND c.kind = 'rent'
          AND c.due_on + p.grace_days < p_as_of
          AND NOT EXISTS (
              SELECT f.rent_charge_id FROM trust_late_fees AS f WHERE f.rent_charge_id = c.id
          )
          -- Its own payments by the end of its grace, less what of them bounced by then, have not
          -- covered it. Credits and waivers only take more off what is open, so a rent its own
          -- payments covered is not open then, and is charged no late fee, whatever else holds.
          AND c.amount
              > coalesce((
                  SELECT sum(cp.amount)
                  FROM trust_charge_payments AS cp
                  JOIN pgledger_transfers AS t ON t.id = cp.transfer_id
                  WHERE cp.charge_id = c.id
                    AND t.event_at < (c.due_on + p.grace_days + 1)::timestamp AT TIME ZONE 'UTC'
              ), 0)
              - coalesce((
                  SELECT sum(r.amount)
                  FROM trust_payment_reversals AS r
                  JOIN pgledger_transfers AS t ON t.id = r.reversal_id
                  WHERE r.charge_id = c.id
                    AND t.event_at < (c.due_on + p.grace_days + 1)::timestamp AT TIME ZONE 'UTC'
              ), 0)
        ORDER BY c.id
    LOOP
        PERFORM c.id FROM trust_charges AS c WHERE c.id = v_rent.id FOR NO KEY UPDATE;
        CONTINUE WHEN EXISTS (
            SELECT f.rent_charge_id FROM trust_late_fees AS f WHERE f.rent_charge_id = v_rent.id
        );
        v_due := v_due || v_rent.id;
    END LOOP;

    -- Then each, in the order its grace ends, as a run every day would reach them.
    FOR v_rent IN
        SELECT c.id, c.lease_id, c.due_on, p.id AS policy_id, p.grace_days, p.flat_fee,
               p.percent, p.maximum
        FROM trust_charges AS c
        JOIN trust_leases AS l ON l.id = c.lease_id
        JOIN trust_units AS u ON u.id = l.unit_id
        JOIN LATERAL (
            SELECT t.id, t.grace_days, t.flat_fee, t.percent, t.maximum
            FROM trust_late_fee_policies AS t
            WHERE t.property_id = u.property_id AND t.starts_on <= c.due_on
            ORDER BY t.starts_on DESC
            LIMIT 1
        ) AS p ON true
        WHERE c.id = ANY (v_due)
        ORDER BY c.due_on + p.grace_days, c.due_on, c.id
    LOOP
        v_fee_day := v_rent.due_on + v_rent.grace_days + 1;
        -- A fee that would fall in a closed year is never charged: the year is locked (migration
        -- 20261021000106), and the one charge it refuses stopped the company's whole run, rent
        -- included, every night (docs/edge-cases.md M-050). The PMC is locked FOR SHARE first,
        -- as the year's own check does, so a close under way and this wait for each other.
        IF v_fee_day < date_trunc('year', now() AT TIME ZONE 'UTC')::date THEN
            IF v_closed IS NULL THEN
                PERFORM 1 FROM trust_pmcs AS p WHERE p.pmc_id = p_pmc_id FOR SHARE;
                v_closed := coalesce(trust_closed_until(p_pmc_id), '-infinity'::date);
            END IF;
            CONTINUE WHEN v_fee_day <= v_closed;
        END IF;
        v_cutoff := (v_rent.due_on + v_rent.grace_days + 1)::timestamp AT TIME ZONE 'UTC';
        -- What is open of it then: payments and bounces dated before then, and the lease's
        -- credits due by its last day of grace, oldest charges first (the fees this run charged
        -- for the rents whose grace ended before its own among them).
        SELECT o.open INTO v_unpaid
        FROM trust_open_charges(p_pmc_id, v_rent.lease_id, v_cutoff) AS o
        WHERE o.charge_id = v_rent.id;
        CONTINUE WHEN v_unpaid <= 0;
        v_fee := least(
            round(v_rent.flat_fee + v_rent.percent / 100 * v_unpaid, 2),
            v_rent.maximum
        );
        CONTINUE WHEN v_fee <= 0;

        INSERT INTO trust_charges (pmc_id, lease_id, due_on, kind, amount, memo)
        VALUES (
            p_pmc_id, v_rent.lease_id, v_fee_day, 'fee', v_fee,
            'Late fee, rent due ' || to_char(v_rent.due_on, 'YYYY-MM-DD')
        )
        RETURNING id INTO v_fee_id;
        INSERT INTO trust_late_fees (rent_charge_id, pmc_id, policy_id, unpaid, fee, fee_charge_id)
        VALUES (v_rent.id, p_pmc_id, v_rent.policy_id, v_unpaid, v_fee, v_fee_id);
        v_charged := v_charged + 1;
    END LOOP;
    RETURN v_charged;
END;
$$;

-- The two helpers this migration added, which nothing else calls.
-- squawk-ignore ban-drop-function
DROP FUNCTION trust_online_on_its_way(uuid, date);
-- squawk-ignore ban-drop-function
DROP FUNCTION trust_online_started_by(pgledger_transfers, date);
