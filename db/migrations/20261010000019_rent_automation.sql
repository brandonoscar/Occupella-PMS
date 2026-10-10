-- migrate:up
-- Rent automation (founder decision, 2026-10-10: Buildium/AppFolio parity, database first).
--
--   trust_charge_rent_due     charges every lease in effect on a due date its monthly rent, due
--                             that day. A lease is charged rent at most once per due date
--                             (migration 20261010000012), so a retried or doubled run bills
--                             nothing twice. A lease that starts after the due date isn't
--                             charged for it: its first part-month is charged by hand.
--   trust_late_fee_policies   a property's late fee terms, in force from a date (new terms are a
--                             new row): days of grace after rent is due, a flat fee, a percent
--                             of the rent left unpaid, and a cap (NULL: none).
--   trust_late_fees           the late fee charged for one rent charge: what was unpaid when its
--                             grace ran out, the fee, and the fee's own charge.
--   trust_assess_late_fees    charges each rent still unpaid at the end of its last day of grace
--                             (UTC) its late fee, under the terms in force on its due date. Once
--                             per rent charge, so it is safe to retry and to run every day.
--
-- Unpaid means the rent less the payments matched to it that are dated before its grace ran
-- out, plus what of them bounced back before then. A payment dated later is late, however soon
-- the run comes after it. A late fee is a charge like any other: due the first day the rent is
-- late, shown on the rent roll, and paid with trust_apply_payment.

-- On a live database, give up rather than queue behind a long transaction while holding locks.
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '60s';

CREATE TABLE trust_late_fee_policies (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    pmc_id uuid NOT NULL REFERENCES trust_pmcs (pmc_id),
    property_id uuid NOT NULL,
    starts_on date NOT NULL,
    -- Days are added to a due date, which takes an integer (there is no date + bigint), and a
    -- year of grace is past any real lease's terms.
    -- squawk-ignore prefer-bigint-over-int
    grace_days integer NOT NULL CHECK (grace_days BETWEEN 0 AND 365),
    flat_fee numeric NOT NULL DEFAULT 0 CHECK (flat_fee >= 0),
    percent numeric NOT NULL DEFAULT 0 CHECK (percent BETWEEN 0 AND 100),
    maximum numeric CHECK (maximum >= 0),  -- NULL: no cap
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (property_id, starts_on),
    FOREIGN KEY (pmc_id, property_id) REFERENCES trust_properties (pmc_id, id)
);

CREATE TABLE trust_late_fees (
    rent_charge_id uuid PRIMARY KEY,
    pmc_id uuid NOT NULL REFERENCES trust_pmcs (pmc_id),
    policy_id uuid NOT NULL REFERENCES trust_late_fee_policies (id),
    unpaid numeric NOT NULL CHECK (unpaid > 0),
    fee numeric NOT NULL CHECK (fee > 0),
    fee_charge_id uuid NOT NULL UNIQUE,
    created_at timestamptz NOT NULL DEFAULT now(),
    FOREIGN KEY (pmc_id, rent_charge_id) REFERENCES trust_charges (pmc_id, id),
    FOREIGN KEY (pmc_id, fee_charge_id) REFERENCES trust_charges (pmc_id, id)
);
CREATE INDEX trust_late_fees_policy_id ON trust_late_fees (policy_id);

-- Records: kept for good. New terms are a new policy; a fee charged by mistake is answered
-- with a credit.
CREATE TRIGGER trust_append_only
BEFORE UPDATE OR DELETE OR TRUNCATE ON trust_late_fee_policies
FOR EACH STATEMENT EXECUTE FUNCTION trust_refuse_ledger_rewrite();
CREATE TRIGGER trust_append_only
BEFORE UPDATE OR DELETE OR TRUNCATE ON trust_late_fees
FOR EACH STATEMENT EXECUTE FUNCTION trust_refuse_ledger_rewrite();

REVOKE ALL ON trust_late_fee_policies, trust_late_fees FROM PUBLIC;
-- The app records late fee terms; late fees are charged only by trust_assess_late_fees.
GRANT SELECT, INSERT ON trust_late_fee_policies TO trust_app;
GRANT SELECT ON trust_late_fees TO trust_app;
GRANT SELECT ON trust_late_fee_policies, trust_late_fees TO trust_ai_agent;

-- Charge every lease of PMC p_pmc_id in effect on p_due_on its monthly rent, due that day; the
-- number of leases charged. A lease charged rent for that day already is skipped, so a retry
-- or two runs at once bill once. Runs with the caller's grants: the app may enter charges.
CREATE FUNCTION trust_charge_rent_due(p_pmc_id uuid, p_due_on date)
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

REVOKE ALL ON FUNCTION trust_charge_rent_due(uuid, date) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION trust_charge_rent_due(uuid, date) TO trust_app;

-- Charge the late fee of each rent of PMC p_pmc_id whose grace ran out before p_as_of and that
-- was unpaid then; the number of fees charged. Each rent charge is locked first, so this waits
-- for payments being matched to it and for another run, then reads what they committed (READ
-- COMMITTED only), and charges a rent's fee once. Runs as the owner.
CREATE FUNCTION trust_assess_late_fees(p_pmc_id uuid, p_as_of date)
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

REVOKE ALL ON FUNCTION trust_assess_late_fees(uuid, date) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION trust_assess_late_fees(uuid, date) TO trust_app;

-- migrate:down
DO $$
BEGIN
    RAISE EXCEPTION 'rent_automation cannot be rolled back: late fees are records';
END;
$$;
