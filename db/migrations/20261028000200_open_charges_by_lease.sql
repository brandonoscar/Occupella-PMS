-- migrate:up
-- One lease's open charges read from that lease's charges alone (the enterprise test, 2026-10-28).
--
-- trust_open_charges is a SQL function, planned once for any lease: its filter `p_lease_id IS
-- NULL OR c.lease_id = p_lease_id` could use no index, so finding one lease's open charges read
-- every charge the PMC was ever charged. Recording a payment, a lease's page and Receive payment
-- read it, and trust_assess_late_fees reads it for each rent past its grace, so each got slower
-- with every month of rent: on a company of 20,000 units the day it switched, 22,053 charges
-- read to find one lease's two, and 20,000 more each month. Now the lease's charges are found
-- by trust_charges_lease_id, and only with no lease given are they all read.
--
-- Its callers join what it gives to trust_charges by id (the order money pays them in). Told
-- nothing, the planner takes a set-returning function for 1,000 rows and hashes the whole table
-- to join one lease's few: 15 ms a call, twice a payment, on that company. ROWS 20, about a
-- lease's charges, has it look each one up by its key instead. With no lease given it gives more,
-- but only the open ones are joined, each by its key.
--
-- Which charges it reads, and what it gives, are unchanged.

CREATE OR REPLACE FUNCTION trust_open_charges(p_pmc_id uuid, p_lease_id uuid, p_at timestamp with time zone) RETURNS TABLE(lease_id uuid, charge_id uuid, open numeric)
    LANGUAGE sql STABLE ROWS 20
    SET search_path TO 'public', 'pg_temp'
    AS $$
    WITH mine AS (
        -- One lease's charges by its index, or, given no lease, every charge of the PMC: each
        -- branch runs only when its parameter says so.
        SELECT c.id, c.lease_id, c.kind, c.amount, c.due_on, c.created_at,
               trust_pay_rank(c) AS pay_rank
        FROM trust_charges AS c
        WHERE p_lease_id IS NOT NULL AND c.lease_id = p_lease_id AND c.pmc_id = p_pmc_id
        UNION ALL
        SELECT c.id, c.lease_id, c.kind, c.amount, c.due_on, c.created_at, trust_pay_rank(c)
        FROM trust_charges AS c
        WHERE p_lease_id IS NULL AND c.pmc_id = p_pmc_id
    ),
    charged AS (
        SELECT c.id, c.lease_id, c.kind, c.amount, c.due_on, c.created_at, c.pay_rank,
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
               ), 0) AS left_owing,
               EXISTS (SELECT 1 FROM trust_charge_waivers AS w
                       WHERE w.credit_charge_id = c.id) AS waiver
        FROM mine AS c
    ),
    -- What waivers due before the moment's day take off the charge each one names.
    waived AS (
        SELECT w.waived_charge_id AS id, sum(k.amount) AS amount
        FROM charged AS k
        JOIN trust_charge_waivers AS w ON w.credit_charge_id = k.id
        WHERE k.due_on < (p_at AT TIME ZONE 'UTC')::date
        GROUP BY w.waived_charge_id
    ),
    -- Each charge after its own waivers, and what of them it could not take.
    settled AS (
        SELECT k.lease_id, k.id, k.due_on, k.created_at, k.pay_rank,
               k.left_owing - least(greatest(k.left_owing, 0), coalesce(w.amount, 0)) AS left_owing,
               coalesce(w.amount, 0) - least(greatest(k.left_owing, 0), coalesce(w.amount, 0))
                   AS spare
        FROM charged AS k
        LEFT JOIN waived AS w ON w.id = k.id
        WHERE k.kind <> 'credit'
    ),
    -- The rest come off the open charges rent first (migration 0093): credits that waive nothing,
    -- and what a waiver could not take off its own charge.
    credits AS (
        SELECT x.lease_id, sum(x.amount) AS amount
        FROM (
            SELECT k.lease_id, k.amount FROM charged AS k
            WHERE k.kind = 'credit' AND NOT k.waiver
              AND k.due_on < (p_at AT TIME ZONE 'UTC')::date
            UNION ALL
            SELECT s.lease_id, s.spare FROM settled AS s WHERE s.spare > 0
        ) AS x
        GROUP BY x.lease_id
    ),
    owing AS (
        SELECT s.lease_id, s.id, s.left_owing,
               coalesce(sum(s.left_owing) OVER (
                   PARTITION BY s.lease_id
                   ORDER BY s.pay_rank = 2, s.due_on, s.pay_rank, s.created_at, s.id
                   ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING
               ), 0) AS owing_before
        FROM settled AS s
    )
    SELECT o.lease_id, o.id,
           o.left_owing
           - least(o.left_owing, greatest(coalesce(cr.amount, 0) - o.owing_before, 0))
    FROM owing AS o
    LEFT JOIN credits AS cr ON cr.lease_id = o.lease_id
$$;

-- migrate:down
CREATE OR REPLACE FUNCTION trust_open_charges(p_pmc_id uuid, p_lease_id uuid, p_at timestamp with time zone) RETURNS TABLE(lease_id uuid, charge_id uuid, open numeric)
    LANGUAGE sql STABLE
    SET search_path TO 'public', 'pg_temp'
    AS $$
    WITH charged AS (
        SELECT c.id, c.lease_id, c.kind, c.amount, c.due_on, c.created_at,
               trust_pay_rank(c) AS pay_rank,
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
               ), 0) AS left_owing,
               EXISTS (SELECT 1 FROM trust_charge_waivers AS w
                       WHERE w.credit_charge_id = c.id) AS waiver
        FROM trust_charges AS c
        WHERE c.pmc_id = p_pmc_id AND (p_lease_id IS NULL OR c.lease_id = p_lease_id)
    ),
    -- What waivers due before the moment's day take off the charge each one names.
    waived AS (
        SELECT w.waived_charge_id AS id, sum(k.amount) AS amount
        FROM charged AS k
        JOIN trust_charge_waivers AS w ON w.credit_charge_id = k.id
        WHERE k.due_on < (p_at AT TIME ZONE 'UTC')::date
        GROUP BY w.waived_charge_id
    ),
    -- Each charge after its own waivers, and what of them it could not take.
    settled AS (
        SELECT k.lease_id, k.id, k.due_on, k.created_at, k.pay_rank,
               k.left_owing - least(greatest(k.left_owing, 0), coalesce(w.amount, 0)) AS left_owing,
               coalesce(w.amount, 0) - least(greatest(k.left_owing, 0), coalesce(w.amount, 0))
                   AS spare
        FROM charged AS k
        LEFT JOIN waived AS w ON w.id = k.id
        WHERE k.kind <> 'credit'
    ),
    -- The rest come off the open charges rent first (migration 0093): credits that waive nothing,
    -- and what a waiver could not take off its own charge.
    credits AS (
        SELECT x.lease_id, sum(x.amount) AS amount
        FROM (
            SELECT k.lease_id, k.amount FROM charged AS k
            WHERE k.kind = 'credit' AND NOT k.waiver
              AND k.due_on < (p_at AT TIME ZONE 'UTC')::date
            UNION ALL
            SELECT s.lease_id, s.spare FROM settled AS s WHERE s.spare > 0
        ) AS x
        GROUP BY x.lease_id
    ),
    owing AS (
        SELECT s.lease_id, s.id, s.left_owing,
               coalesce(sum(s.left_owing) OVER (
                   PARTITION BY s.lease_id
                   ORDER BY s.pay_rank = 2, s.due_on, s.pay_rank, s.created_at, s.id
                   ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING
               ), 0) AS owing_before
        FROM settled AS s
    )
    SELECT o.lease_id, o.id,
           o.left_owing
           - least(o.left_owing, greatest(coalesce(cr.amount, 0) - o.owing_before, 0))
    FROM owing AS o
    LEFT JOIN credits AS cr ON cr.lease_id = o.lease_id
$$;
