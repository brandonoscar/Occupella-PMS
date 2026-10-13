-- migrate:up
-- Credits come off the oldest open charges (founder decision, 2026-10-13: "Oldest charges
-- first"). A credit (a concession, a recurring credit, one staff add) takes its amount off what a
-- lease owes, but it was never applied to a charge: a rent it covered still looked unpaid, so
-- the late fee run charged a late fee on it and the rent page showed it open, though the lease
-- owed nothing. What a lease owes and the delinquency report already count a credit against the
-- oldest charges; now the late fees and the rent page do too.
--
--   trust_open_charges(pmc, lease or NULL for all, moment)
--       each charge of the PMC's leases that is not a credit, with what is open of it at the
--       moment: its amount, less what payments dated before then matched to it, plus what of
--       them bounced back before then, less its share of the lease's credits due before the
--       moment's day (UTC). Credits come off the open charges oldest first: by due date, then by
--       id, as the delinquency report takes them. At 'infinity' every payment, bounce and credit
--       counts.
--   trust_assess_late_fees
--       a rent's unpaid amount at the end of its last day of grace is what is open of it then.

-- On a live database, give up rather than queue behind a long transaction while holding locks.
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '60s';

CREATE FUNCTION trust_open_charges(p_pmc_id uuid, p_lease_id uuid, p_at timestamptz)
RETURNS TABLE (lease_id uuid, charge_id uuid, open numeric)
LANGUAGE sql
STABLE
SET search_path = public, pg_temp
AS $$
    WITH charged AS (
        SELECT c.id, c.lease_id, c.kind, c.amount, c.due_on,
               c.amount
               - coalesce((
                   SELECT sum(cp.amount)
                   FROM trust_charge_payments AS cp
                   JOIN pgledger_transfers AS t ON t.id = cp.transfer_id
                   WHERE cp.charge_id = c.id AND t.event_at < p_at
               ), 0)
               + coalesce((
                   SELECT sum(r.amount)
                   FROM trust_payment_reversals AS r
                   JOIN pgledger_transfers AS t ON t.id = r.reversal_id
                   WHERE r.charge_id = c.id AND t.event_at < p_at
               ), 0) AS left_owing
        FROM trust_charges AS c
        WHERE c.pmc_id = p_pmc_id AND (p_lease_id IS NULL OR c.lease_id = p_lease_id)
    ),
    credits AS (
        SELECT k.lease_id, sum(k.amount) AS amount
        FROM charged AS k
        WHERE k.kind = 'credit' AND k.due_on < (p_at AT TIME ZONE 'UTC')::date
        GROUP BY k.lease_id
    ),
    owing AS (
        SELECT k.lease_id, k.id, k.left_owing,
               coalesce(sum(k.left_owing) OVER (
                   PARTITION BY k.lease_id ORDER BY k.due_on, k.id
                   ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING
               ), 0) AS owing_before
        FROM charged AS k
        WHERE k.kind <> 'credit'
    )
    SELECT o.lease_id, o.id,
           o.left_owing
           - least(o.left_owing, greatest(coalesce(cr.amount, 0) - o.owing_before, 0))
    FROM owing AS o
    LEFT JOIN credits AS cr ON cr.lease_id = o.lease_id
$$;

REVOKE ALL ON FUNCTION trust_open_charges(uuid, uuid, timestamptz) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION trust_open_charges(uuid, uuid, timestamptz) TO trust_app;

CREATE OR REPLACE FUNCTION trust_assess_late_fees(p_pmc_id uuid, p_as_of date)
RETURNS integer
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
DECLARE
    v_rent record;
    v_cutoff timestamptz;
    v_unpaid numeric;
    v_fee numeric;
    v_fee_id uuid;
    v_charged integer := 0;
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

    FOR v_rent IN
        SELECT c.id, c.lease_id, c.due_on, c.amount, p.id AS policy_id, p.grace_days,
               p.flat_fee, p.percent, p.maximum
        FROM trust_charges AS c
        JOIN trust_leases AS l ON l.id = c.lease_id
        JOIN trust_units AS u ON u.id = l.unit_id
        -- The terms in force on the rent's due date: the latest starting on or before it.
        JOIN LATERAL (
            SELECT t.id, t.grace_days, t.flat_fee, t.percent, t.maximum
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
        ORDER BY c.id  -- one lock order for every run, so two at once can't deadlock
    LOOP
        PERFORM c.id FROM trust_charges AS c WHERE c.id = v_rent.id FOR NO KEY UPDATE;
        CONTINUE WHEN EXISTS (
            SELECT f.rent_charge_id FROM trust_late_fees AS f WHERE f.rent_charge_id = v_rent.id
        );

        v_cutoff := (v_rent.due_on + v_rent.grace_days + 1)::timestamp AT TIME ZONE 'UTC';
        -- What is open of it then: payments and bounces dated before then, and the lease's
        -- credits due by its last day of grace, oldest charges first.
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
            p_pmc_id, v_rent.lease_id, v_rent.due_on + v_rent.grace_days + 1, 'fee', v_fee,
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

CREATE OR REPLACE FUNCTION trust_assess_late_fees(p_pmc_id uuid, p_as_of date)
RETURNS integer
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
DECLARE
    v_rent record;
    v_cutoff timestamptz;
    v_unpaid numeric;
    v_fee numeric;
    v_fee_id uuid;
    v_charged integer := 0;
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

    FOR v_rent IN
        SELECT c.id, c.lease_id, c.due_on, c.amount, p.id AS policy_id, p.grace_days,
               p.flat_fee, p.percent, p.maximum
        FROM trust_charges AS c
        JOIN trust_leases AS l ON l.id = c.lease_id
        JOIN trust_units AS u ON u.id = l.unit_id
        -- The terms in force on the rent's due date: the latest starting on or before it.
        JOIN LATERAL (
            SELECT t.id, t.grace_days, t.flat_fee, t.percent, t.maximum
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
        ORDER BY c.id  -- one lock order for every run, so two at once can't deadlock
    LOOP
        PERFORM c.id FROM trust_charges AS c WHERE c.id = v_rent.id FOR NO KEY UPDATE;
        CONTINUE WHEN EXISTS (
            SELECT f.rent_charge_id FROM trust_late_fees AS f WHERE f.rent_charge_id = v_rent.id
        );

        v_cutoff := (v_rent.due_on + v_rent.grace_days + 1)::timestamp AT TIME ZONE 'UTC';
        v_unpaid := v_rent.amount
            - coalesce((
                SELECT sum(cp.amount)
                FROM trust_charge_payments AS cp
                JOIN pgledger_transfers AS t ON t.id = cp.transfer_id
                WHERE cp.charge_id = v_rent.id AND t.event_at < v_cutoff
            ), 0)
            + coalesce((
                SELECT sum(r.amount)
                FROM trust_payment_reversals AS r
                JOIN pgledger_transfers AS t ON t.id = r.reversal_id
                WHERE r.charge_id = v_rent.id AND t.event_at < v_cutoff
            ), 0);
        CONTINUE WHEN v_unpaid <= 0;
        v_fee := least(
            round(v_rent.flat_fee + v_rent.percent / 100 * v_unpaid, 2),
            v_rent.maximum
        );
        CONTINUE WHEN v_fee <= 0;

        INSERT INTO trust_charges (pmc_id, lease_id, due_on, kind, amount, memo)
        VALUES (
            p_pmc_id, v_rent.lease_id, v_rent.due_on + v_rent.grace_days + 1, 'fee', v_fee,
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

-- Nothing but the late fees above and the app call it.
-- squawk-ignore ban-drop-function
DROP FUNCTION trust_open_charges(uuid, uuid, timestamptz);
