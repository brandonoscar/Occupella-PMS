-- migrate:up
-- A late fee run charges the same fees whatever order its rents' ids fall in. A late fee is an
-- open charge like any other, so a lease's credits come off it in its turn (oldest first, by due
-- date). The run took its rents in id order: when one run reached two rents of a lease with a
-- credit, the first rent's new fee could take the credit before the second rent's unpaid amount
-- was worked out, or not, as the ids fell. Found by the rent roll property test.
--
-- Now the run first locks its rents in id order, as before (so two runs at once can't deadlock,
-- and a rent another run charged meanwhile is skipped), then charges them in the order their
-- grace ends (then by due date, then id): each rent sees the fees of the rents whose grace ended
-- before its own, as a run every day would.

-- On a live database, give up rather than queue behind a long transaction while holding locks.
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
    v_due uuid[] := '{}'::uuid[];
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
