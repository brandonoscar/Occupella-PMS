-- migrate:up
-- Prorated recurring charges (founder decision, 2026-10-13: "Prorate them too"): a recurring fee
-- or credit (pet rent, parking, a concession) is charged for a part month the way rent is
-- (migration 0045): its amount x days of the lease in that month / days in that month, rounded
-- to the cent.
--
-- trust_charge_recurring_due(pmc, due date) now charges, for the calendar month of the due date:
--   - every recurring charge in force on the due date whose lease is in effect then, as before,
--     due that day: its amount for the days of the month the lease is in effect (all of it for a
--     lease that neither starts nor ends in the month);
--   - every recurring charge in force on the first day of a lease that starts later in that
--     month, with nothing charged for it in the month yet, due on the lease's first day: its
--     amount for the days of the month the lease is in effect. As with rent, the run never
--     reaches back into an earlier month.
-- Still once per recurring charge and due date; a part month's memo says which days, and one
-- that rounds to nothing is not charged.
--
-- A charge naming a recurring charge was of its amount (the key to trust_recurring_charges held
-- the amount); a part month's is less. The key now holds its PMC, lease and kind, and a trigger
-- checks the amount: what the run charges for that due date, given the lease's dates when the
-- charge is entered.

-- On a live database, give up rather than queue behind a long transaction while holding locks.
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '60s';

-- What the key to it names now. (The key with the amount stays, for the down step.) Recurring
-- charges run to a few per lease, so building it scans little.
-- squawk-ignore require-concurrent-index-creation
CREATE UNIQUE INDEX trust_recurring_charges_lease_kind
ON trust_recurring_charges (pmc_id, lease_id, id, kind);

-- The key with the amount gives way to the narrower key below and the amount trigger after it:
-- the PMC, lease and kind stay held by the key, and the amount by the trigger.
-- squawk-ignore ban-drop-constraint
ALTER TABLE trust_charges DROP CONSTRAINT trust_charges_recurring_charge_fkey;
-- Every charge already naming a recurring charge holds the narrower key above, and charges run
-- to one row per lease and month, so validating it scans little; a separate NOT VALID /
-- VALIDATE step would buy nothing.
-- squawk-ignore adding-foreign-key-constraint, constraint-missing-not-valid
ALTER TABLE trust_charges ADD CONSTRAINT trust_charges_recurring_charge_fkey
FOREIGN KEY (pmc_id, lease_id, recurring_charge_id, kind)
REFERENCES trust_recurring_charges (pmc_id, lease_id, id, kind);

-- A charge naming a recurring charge of its PMC, lease and kind is its amount for the days of
-- the due date's month its lease is in effect. One naming another's is left to the key.
CREATE FUNCTION trust_check_recurring_charge_amount() RETURNS trigger
LANGUAGE plpgsql
SET search_path = public, pg_temp
AS $$
DECLARE
    v_amount numeric;
BEGIN
    SELECT p.amount INTO v_amount
    FROM trust_recurring_charges AS r
    JOIN trust_leases AS l ON l.id = r.lease_id
    CROSS JOIN LATERAL trust_rent_for_month(l.starts_on, l.ends_on, r.amount, NEW.due_on) AS p
    WHERE r.id = NEW.recurring_charge_id AND r.pmc_id = NEW.pmc_id
      AND r.lease_id = NEW.lease_id AND r.kind = NEW.kind;
    IF FOUND AND NEW.amount IS DISTINCT FROM v_amount THEN
        RAISE EXCEPTION 'trust: a recurring charge due % is % for the days its lease is in '
            'effect that month, not %', NEW.due_on, v_amount, NEW.amount
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER trust_recurring_charge_amount
BEFORE INSERT ON trust_charges
FOR EACH ROW WHEN (NEW.recurring_charge_id IS NOT NULL)
EXECUTE FUNCTION trust_check_recurring_charge_amount();

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
SET LOCAL statement_timeout = '60s';

CREATE OR REPLACE FUNCTION trust_charge_recurring_due(p_pmc_id uuid, p_due_on date)
RETURNS integer
LANGUAGE plpgsql
SET search_path = public, pg_temp
AS $$
DECLARE
    v_charged integer;
BEGIN
    IF p_due_on IS NULL THEN
        RAISE EXCEPTION 'trust: recurring charges are charged for a due date; none was given'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    INSERT INTO trust_charges (pmc_id, lease_id, due_on, kind, amount, memo, recurring_charge_id)
    SELECT r.pmc_id, r.lease_id, p_due_on, r.kind, r.amount,
           r.memo || ' due ' || to_char(p_due_on, 'YYYY-MM-DD'), r.id
    FROM trust_recurring_charges AS r
    JOIN trust_leases AS l ON l.id = r.lease_id
    LEFT JOIN trust_recurring_charge_ends AS e ON e.recurring_charge_id = r.id
    WHERE r.pmc_id = p_pmc_id AND r.starts_on <= p_due_on
      AND (e.ends_on IS NULL OR p_due_on < e.ends_on)
      AND l.starts_on <= p_due_on AND (l.ends_on IS NULL OR p_due_on <= l.ends_on)
    ORDER BY r.id
    ON CONFLICT (recurring_charge_id, due_on) WHERE recurring_charge_id IS NOT NULL DO NOTHING;
    GET DIAGNOSTICS v_charged = ROW_COUNT;
    RETURN v_charged;
END;
$$;

DROP TRIGGER trust_recurring_charge_amount ON trust_charges;
-- Nothing but the trigger above calls it.
-- squawk-ignore ban-drop-function
DROP FUNCTION trust_check_recurring_charge_amount();
-- A part month charged since refuses the key with the amount: it can't be rolled back then.
-- The narrower key gives way to the key with the amount it replaced.
-- squawk-ignore ban-drop-constraint
ALTER TABLE trust_charges DROP CONSTRAINT trust_charges_recurring_charge_fkey;
-- squawk-ignore adding-foreign-key-constraint, constraint-missing-not-valid
ALTER TABLE trust_charges ADD CONSTRAINT trust_charges_recurring_charge_fkey
FOREIGN KEY (pmc_id, lease_id, recurring_charge_id, kind, amount)
REFERENCES trust_recurring_charges (pmc_id, lease_id, id, kind, amount);
-- Recurring charges run to a few per lease, so dropping it takes moments; CONCURRENTLY can't
-- run inside the migration's transaction.
-- squawk-ignore require-concurrent-index-deletion
DROP INDEX trust_recurring_charges_lease_kind;
