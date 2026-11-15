-- migrate:up
-- A rent first charged after its own grace had run out takes its grace from the day the run
-- charged it (docs/edge-cases.md D-083).
--
-- A lease entered late, or a month the nightly run catches up after the server was down (D-016,
-- D-017), or an admin's Run rent now for an earlier month, charges a rent whose due day and grace
-- are already past. The same run then charged its late fee, before the tenant could see the rent.
-- Now:
--
--   trust_rents_charged_late       each rent a rent run charged after its due day, and the
--                                  day it was charged (the run's, or trust.charged_on: the
--                                  company's today when the nightly run catches up a month or
--                                  Run rent now assesses late fees as of a day gone by), once.
--   trust_note_rents_charged_late  the run's rents charged in this transaction, due before its
--                                  day; trust_run_rent calls it before the late fees.
--   trust_late_fee_grace_ends      the last day of a rent's grace: its due day plus the grace
--                                  days, or, for a rent charged after that day had passed, the
--                                  day it was charged plus the grace days.
--   trust_assess_late_fees         counts every rent's grace, its payments by then and its fee's
--                                  day from trust_late_fee_grace_ends.
--
-- A rent charged on or before the end of its grace keeps its grace as before.

SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '60s';

CREATE TABLE trust_rents_charged_late (
    rent_charge_id uuid PRIMARY KEY,
    pmc_id uuid NOT NULL REFERENCES trust_pmcs (pmc_id),
    -- The day of the run that charged it: its late fees' day.
    charged_on date NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    FOREIGN KEY (pmc_id, rent_charge_id) REFERENCES trust_charges (pmc_id, id)
);
CREATE INDEX trust_rents_charged_late_pmc ON trust_rents_charged_late (pmc_id);
CREATE TRIGGER trust_append_only
BEFORE UPDATE OR DELETE OR TRUNCATE ON trust_rents_charged_late
FOR EACH STATEMENT EXECUTE FUNCTION trust_refuse_ledger_rewrite();
CREATE TRIGGER trust_audit AFTER INSERT OR UPDATE OR DELETE ON trust_rents_charged_late
FOR EACH ROW EXECUTE FUNCTION trust_audit();
REVOKE ALL ON trust_rents_charged_late FROM PUBLIC;
GRANT SELECT ON trust_rents_charged_late TO trust_app;
GRANT SELECT ON trust_rents_charged_late TO trust_ai_agent;

-- The rents this transaction charged for a run's month, due before the day they were charged:
-- the run's day, or, when the run is for an earlier day than the one it runs on (the nightly
-- run's catch-up of a month the server missed, Run rent now's late fees as of a day gone by),
-- the day the caller names in trust.charged_on for the transaction. Written by the owner, so the
-- app marks only rents it charged in the same transaction, never an older one.
CREATE FUNCTION trust_note_rents_charged_late(p_pmc_id uuid, p_month date, p_day date)
RETURNS integer
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path TO 'public', 'pg_temp'
    AS $$
DECLARE
    v_noted integer;
    v_on date := greatest(
        p_day, nullif(current_setting('trust.charged_on', true), '')::date
    );
BEGIN
    INSERT INTO trust_rents_charged_late (rent_charge_id, pmc_id, charged_on)
    SELECT c.id, c.pmc_id, v_on
    FROM trust_charges AS c
    WHERE c.pmc_id = p_pmc_id AND c.kind = 'rent'
      AND c.due_on >= date_trunc('month', p_month::timestamp)::date
      AND c.due_on < (date_trunc('month', p_month::timestamp) + interval '1 month')::date
      AND c.due_on < v_on
      AND c.created_at = now()  -- charged in this transaction, by this run
    ORDER BY c.id
    ON CONFLICT (rent_charge_id) DO NOTHING;
    GET DIAGNOSTICS v_noted = ROW_COUNT;
    RETURN v_noted;
END;
$$;
REVOKE ALL ON FUNCTION trust_note_rents_charged_late(uuid, date, date) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION trust_note_rents_charged_late(uuid, date, date) TO trust_app;

-- The last day of a rent's grace under terms of so many grace days.
CREATE FUNCTION trust_late_fee_grace_ends(p_rent_id uuid, p_due_on date, p_grace_days integer)
RETURNS date
    LANGUAGE sql STABLE
    SET search_path TO 'public', 'pg_temp'
    AS $$
    SELECT CASE
        WHEN r.charged_on > p_due_on + p_grace_days THEN r.charged_on + p_grace_days
        ELSE p_due_on + p_grace_days
    END
    FROM (SELECT (SELECT l.charged_on FROM trust_rents_charged_late AS l
                  WHERE l.rent_charge_id = p_rent_id) AS charged_on) AS r
$$;
REVOKE ALL ON FUNCTION trust_late_fee_grace_ends(uuid, date, integer) FROM PUBLIC;

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
        SELECT c.id, c.lease_id, trust_late_fee_grace_ends(c.id, c.due_on, p.grace_days)
                   AS grace_ends
        FROM trust_charges AS c
        JOIN trust_leases AS l ON l.id = c.lease_id
        JOIN trust_units AS u ON u.id = l.unit_id
        -- The terms in force on the rent's due date: the latest starting on or before it.
        JOIN LATERAL (
            SELECT t.grace_days
            FROM trust_late_fee_policies AS t
            WHERE t.property_id = u.property_id AND t.starts_on <= c.due_on
              -- On file by the day its fee comes due (docs/edge-cases.md M-049, D-082): terms
              -- entered later, from a day in the past, charge no fee on a rent whose grace had
              -- run out, as a run every day would have charged none then.
              AND trust_pmc_day(t.pmc_id, t.created_at)
                  <= trust_late_fee_grace_ends(c.id, c.due_on, t.grace_days) + 1
            ORDER BY t.starts_on DESC
            LIMIT 1
        ) AS p ON true
        WHERE c.pmc_id = p_pmc_id AND c.kind = 'rent'
          AND trust_late_fee_grace_ends(c.id, c.due_on, p.grace_days) < p_as_of
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
                    AND (t.event_at < (trust_late_fee_grace_ends(c.id, c.due_on, p.grace_days)
                                       + 1)::timestamp AT TIME ZONE 'UTC'
                         OR trust_online_started_by(
                             t, trust_late_fee_grace_ends(c.id, c.due_on, p.grace_days)))
              ), 0)
              - coalesce((
                  SELECT sum(r.amount)
                  FROM trust_payment_reversals AS r
                  JOIN pgledger_transfers AS t ON t.id = r.reversal_id
                  WHERE r.charge_id = c.id
                    AND t.event_at < (trust_late_fee_grace_ends(c.id, c.due_on, p.grace_days)
                                      + 1)::timestamp AT TIME ZONE 'UTC'
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
               p.percent, p.maximum,
               trust_late_fee_grace_ends(c.id, c.due_on, p.grace_days) AS grace_ends
        FROM trust_charges AS c
        JOIN trust_leases AS l ON l.id = c.lease_id
        JOIN trust_units AS u ON u.id = l.unit_id
        JOIN LATERAL (
            SELECT t.id, t.grace_days, t.flat_fee, t.percent, t.maximum
            FROM trust_late_fee_policies AS t
            WHERE t.property_id = u.property_id AND t.starts_on <= c.due_on
              AND trust_pmc_day(t.pmc_id, t.created_at)
                  <= trust_late_fee_grace_ends(c.id, c.due_on, t.grace_days) + 1
            ORDER BY t.starts_on DESC
            LIMIT 1
        ) AS p ON true
        WHERE c.id = ANY (v_due)
        ORDER BY trust_late_fee_grace_ends(c.id, c.due_on, p.grace_days), c.due_on, c.id
    LOOP
        v_fee_day := v_rent.grace_ends + 1;
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
        v_cutoff := (v_rent.grace_ends + 1)::timestamp AT TIME ZONE 'UTC';
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
              AND trust_online_started_by(t, v_rent.grace_ends)
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

CREATE OR REPLACE FUNCTION trust_run_rent(p_pmc_id uuid, p_due_on date, p_late_fees_as_of date) RETURNS uuid
    LANGUAGE plpgsql
    SET search_path TO 'public', 'pg_temp'
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
    -- A rent this run charged after its grace had run out takes its grace from the run's day
    -- (docs/edge-cases.md D-083).
    PERFORM trust_note_rents_charged_late(p_pmc_id, p_due_on, p_late_fees_as_of);
    v_late_fees := trust_assess_late_fees(p_pmc_id, p_late_fees_as_of);
    INSERT INTO trust_rent_runs (pmc_id, due_on, late_fees_as_of, rents, recurring, late_fees)
    VALUES (p_pmc_id, p_due_on, p_late_fees_as_of, v_rents, v_recurring, v_late_fees)
    RETURNING id INTO v_id;
    RETURN v_id;
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
        SELECT c.id, c.lease_id, c.due_on + p.grace_days AS grace_ends
        FROM trust_charges AS c
        JOIN trust_leases AS l ON l.id = c.lease_id
        JOIN trust_units AS u ON u.id = l.unit_id
        -- The terms in force on the rent's due date: the latest starting on or before it.
        JOIN LATERAL (
            SELECT t.grace_days
            FROM trust_late_fee_policies AS t
            WHERE t.property_id = u.property_id AND t.starts_on <= c.due_on
              -- On file by the day its fee comes due (docs/edge-cases.md M-049, D-082): terms
              -- entered later, from a day in the past, charge no fee on a rent whose grace had
              -- run out, as a run every day would have charged none then.
              AND trust_pmc_day(t.pmc_id, t.created_at) <= c.due_on + t.grace_days + 1
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
              AND trust_pmc_day(t.pmc_id, t.created_at) <= c.due_on + t.grace_days + 1
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

CREATE OR REPLACE FUNCTION trust_run_rent(p_pmc_id uuid, p_due_on date, p_late_fees_as_of date) RETURNS uuid
    LANGUAGE plpgsql
    SET search_path TO 'public', 'pg_temp'
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

-- squawk-ignore ban-drop-function
DROP FUNCTION trust_late_fee_grace_ends(uuid, date, integer);
-- squawk-ignore ban-drop-function
DROP FUNCTION trust_note_rents_charged_late(uuid, date, date);
-- squawk-ignore ban-drop-table
DROP TABLE trust_rents_charged_late;
