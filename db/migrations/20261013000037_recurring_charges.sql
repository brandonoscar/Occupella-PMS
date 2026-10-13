-- migrate:up
-- Recurring lease charges (founder decision, 2026-10-13: "do these 3", product item 1).
--
--   trust_recurring_charges        a charge a lease owes every month besides its rent (pet rent,
--                                  parking, storage) or a credit it gets every month (a
--                                  concession): its lease, what for, amount and first due date.
--                                  trust_recurring_charge_ends stops one from a day.
--   trust_charges.recurring_charge_id
--                                  the recurring charge a charge was made for: NULL for rent and
--                                  a one-off fee or credit. A key ties the charge to the
--                                  recurring charge's PMC, lease, kind and amount.
--   trust_charge_recurring_due     charges, for a due date, every recurring charge in force on
--                                  it (from its first day, until the day it stops) whose lease is
--                                  in effect that day: a fee or credit due that day, at most once
--                                  per recurring charge and due date, so a retried or doubled run
--                                  charges nothing twice. The staff rent run calls it with
--                                  trust_charge_rent_due. Runs with the caller's grants.
--
-- A recurring charge's charges are charges like any other: shown on the rent roll and the
-- tenant's ledger, and paid with trust_apply_payment. Late fees are charged on rent only.

-- On a live database, give up rather than queue behind a long transaction while holding locks.
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '60s';

CREATE TABLE trust_recurring_charges (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    pmc_id uuid NOT NULL REFERENCES trust_pmcs (pmc_id),
    lease_id uuid NOT NULL,
    kind text NOT NULL CHECK (kind IN ('fee', 'credit')),
    amount numeric NOT NULL CHECK (amount > 0 AND amount = round(amount, 2)),
    memo text NOT NULL CHECK (memo <> ''),
    starts_on date NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (pmc_id, id),
    -- The key a charge made for it names, so the charge is of its lease, kind and amount.
    UNIQUE (pmc_id, lease_id, id, kind, amount),
    FOREIGN KEY (pmc_id, lease_id) REFERENCES trust_leases (pmc_id, id)
);
CREATE INDEX trust_recurring_charges_lease_id ON trust_recurring_charges (lease_id);

CREATE TABLE trust_recurring_charge_ends (
    recurring_charge_id uuid PRIMARY KEY,
    pmc_id uuid NOT NULL REFERENCES trust_pmcs (pmc_id),
    ends_on date NOT NULL,  -- the first due date it is no longer charged for
    created_at timestamptz NOT NULL DEFAULT now(),
    FOREIGN KEY (pmc_id, recurring_charge_id) REFERENCES trust_recurring_charges (pmc_id, id)
);

-- Every existing charge gets NULL: rent or a one-off fee or credit.
ALTER TABLE trust_charges ADD COLUMN recurring_charge_id uuid;
-- Every row has recurring_charge_id NULL, which a key doesn't check, and charges run to one row
-- per lease and month, so validating it scans little; a separate NOT VALID / VALIDATE step
-- would buy nothing.
-- squawk-ignore adding-foreign-key-constraint, constraint-missing-not-valid
ALTER TABLE trust_charges ADD CONSTRAINT trust_charges_recurring_charge_fkey
FOREIGN KEY (pmc_id, lease_id, recurring_charge_id, kind, amount)
REFERENCES trust_recurring_charges (pmc_id, lease_id, id, kind, amount);
-- Once per recurring charge and due date, even from two runs at once.
-- squawk-ignore require-concurrent-index-creation
CREATE UNIQUE INDEX trust_charges_one_per_recurring_due_date
ON trust_charges (recurring_charge_id, due_on) WHERE recurring_charge_id IS NOT NULL;

-- Records: kept for good. New terms are a new recurring charge; one that should not have been
-- is stopped, and a charge it made by mistake is answered with a credit.
CREATE TRIGGER trust_append_only
BEFORE UPDATE OR DELETE OR TRUNCATE ON trust_recurring_charges
FOR EACH STATEMENT EXECUTE FUNCTION trust_refuse_ledger_rewrite();
CREATE TRIGGER trust_append_only
BEFORE UPDATE OR DELETE OR TRUNCATE ON trust_recurring_charge_ends
FOR EACH STATEMENT EXECUTE FUNCTION trust_refuse_ledger_rewrite();
CREATE TRIGGER trust_audit AFTER INSERT OR UPDATE OR DELETE ON trust_recurring_charges
FOR EACH ROW EXECUTE FUNCTION trust_audit();
CREATE TRIGGER trust_audit AFTER INSERT OR UPDATE OR DELETE ON trust_recurring_charge_ends
FOR EACH ROW EXECUTE FUNCTION trust_audit();

REVOKE ALL ON trust_recurring_charges, trust_recurring_charge_ends FROM PUBLIC;
GRANT SELECT, INSERT ON trust_recurring_charges, trust_recurring_charge_ends TO trust_app;
GRANT SELECT ON trust_recurring_charges, trust_recurring_charge_ends TO trust_ai_agent;

-- Charge every recurring charge of PMC p_pmc_id in force on p_due_on, whose lease is in effect
-- that day, its amount due that day; the number charged. One charged for that day already is
-- skipped, so a retry or two runs at once charge once. Runs with the caller's grants: the app
-- may enter charges.
CREATE FUNCTION trust_charge_recurring_due(p_pmc_id uuid, p_due_on date)
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

REVOKE ALL ON FUNCTION trust_charge_recurring_due(uuid, date) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION trust_charge_recurring_due(uuid, date) TO trust_app;

-- migrate:down
DO $$
BEGIN
    RAISE EXCEPTION 'recurring_charges cannot be rolled back: recurring charges are records';
END;
$$;
