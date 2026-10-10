-- migrate:up
-- Units, leases and what tenants owe (issue #10, founder decisions of 2026-10-10: minimal, in
-- Phase 0; a units table; charges outside the trust ledger; several tenants per lease).
--
--   trust_units          a property's rentable units (a single-family home has one).
--   trust_leases         one unit, a first day, a last day (NULL while month to month) and the
--                        monthly rent. Two leases of one unit never overlap.
--   trust_lease_tenants  the tenants on a lease, each a tenant of the unit's property.
--   trust_charges        what a lease's tenants owe: rent, a fee, or a credit that takes some
--                        off. Append-only; a mistake is corrected with a credit. A lease is
--                        charged rent at most once per due date, so a retried run can't bill
--                        twice.
--   trust_charge_payments which ledger transfer paid how much of which charge.
--
-- Charges are not money held in trust, so they stay out of pgledger: the trust ledger holds only
-- cash received, and the bank tie-out keeps working. A payment is posted to the ledger as usual
-- (into the property owner's account, from cash or from money held for a tenant on the lease)
-- and then matched to the charges it pays with trust_apply_payment. A charge is never paid past its amount, a transfer
-- never pays more than it moved, and the same match retried returns the original.

-- On a live database, give up rather than queue behind a long transaction while holding locks.
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '60s';

CREATE TABLE trust_units (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    pmc_id uuid NOT NULL REFERENCES trust_pmcs (pmc_id),
    property_id uuid NOT NULL,
    display_name text NOT NULL CHECK (display_name <> ''),
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (pmc_id, id),
    FOREIGN KEY (pmc_id, property_id) REFERENCES trust_properties (pmc_id, id)
);
CREATE INDEX trust_units_property_id ON trust_units (property_id);

CREATE TABLE trust_leases (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    pmc_id uuid NOT NULL REFERENCES trust_pmcs (pmc_id),
    unit_id uuid NOT NULL,
    starts_on date NOT NULL,
    ends_on date,  -- the last day; NULL while month to month
    monthly_rent numeric NOT NULL CHECK (monthly_rent > 0),
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (pmc_id, id),
    CHECK (ends_on >= starts_on),
    FOREIGN KEY (pmc_id, unit_id) REFERENCES trust_units (pmc_id, id)
);
CREATE INDEX trust_leases_unit_id ON trust_leases (unit_id, starts_on);

CREATE TABLE trust_lease_tenants (
    pmc_id uuid NOT NULL REFERENCES trust_pmcs (pmc_id),
    lease_id uuid NOT NULL,
    tenant_id uuid NOT NULL,
    PRIMARY KEY (lease_id, tenant_id),
    FOREIGN KEY (pmc_id, lease_id) REFERENCES trust_leases (pmc_id, id),
    FOREIGN KEY (pmc_id, tenant_id) REFERENCES trust_tenants (pmc_id, id)
);
CREATE INDEX trust_lease_tenants_tenant_id ON trust_lease_tenants (tenant_id);

CREATE TABLE trust_charges (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    pmc_id uuid NOT NULL REFERENCES trust_pmcs (pmc_id),
    lease_id uuid NOT NULL,
    due_on date NOT NULL,
    kind text NOT NULL CHECK (kind IN ('rent', 'fee', 'credit')),
    amount numeric NOT NULL CHECK (amount > 0),  -- a credit's amount comes off what is owed
    memo text,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (pmc_id, id),
    FOREIGN KEY (pmc_id, lease_id) REFERENCES trust_leases (pmc_id, id)
);
CREATE INDEX trust_charges_lease_id ON trust_charges (lease_id, due_on);
CREATE UNIQUE INDEX trust_charges_one_rent_per_due_date
ON trust_charges (lease_id, due_on) WHERE kind = 'rent';

CREATE TABLE trust_charge_payments (
    pmc_id uuid NOT NULL REFERENCES trust_pmcs (pmc_id),
    charge_id uuid NOT NULL,
    transfer_id text NOT NULL REFERENCES pgledger_transfers (id),
    amount numeric NOT NULL CHECK (amount > 0),
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (charge_id, transfer_id),
    FOREIGN KEY (pmc_id, charge_id) REFERENCES trust_charges (pmc_id, id)
);
CREATE INDEX trust_charge_payments_transfer_id ON trust_charge_payments (transfer_id);

-- Records: kept for good, like the ledger. (A lease changes only its last day, through
-- trust_end_lease.)
CREATE TRIGGER trust_append_only
BEFORE UPDATE OR DELETE OR TRUNCATE ON trust_lease_tenants
FOR EACH STATEMENT EXECUTE FUNCTION trust_refuse_ledger_rewrite();
CREATE TRIGGER trust_append_only
BEFORE UPDATE OR DELETE OR TRUNCATE ON trust_charges
FOR EACH STATEMENT EXECUTE FUNCTION trust_refuse_ledger_rewrite();
CREATE TRIGGER trust_append_only
BEFORE UPDATE OR DELETE OR TRUNCATE ON trust_charge_payments
FOR EACH STATEMENT EXECUTE FUNCTION trust_refuse_ledger_rewrite();

REVOKE ALL ON trust_units, trust_leases, trust_lease_tenants, trust_charges, trust_charge_payments
FROM PUBLIC;
-- Units are records like properties: the app adds and renames them. Leases and payment matches
-- go through the functions below, which check what a constraint can't; charges are plain rows.
GRANT SELECT, INSERT ON trust_units, trust_charges TO trust_app;
GRANT UPDATE (display_name) ON trust_units TO trust_app;
GRANT SELECT ON trust_leases, trust_lease_tenants, trust_charge_payments TO trust_app;

-- Two leases of one unit never overlap, whoever writes them. Each lease write waits for the
-- others on its unit (the unit's row lock) and then reads every committed lease, which only
-- READ COMMITTED does: at REPEATABLE READ or SERIALIZABLE it would read an older snapshot.
CREATE FUNCTION trust_refuse_overlapping_leases() RETURNS trigger
LANGUAGE plpgsql
SET search_path = public, pg_temp
AS $$
DECLARE
    v_other uuid;
BEGIN
    IF current_setting('transaction_isolation') <> 'read committed' THEN
        RAISE EXCEPTION 'trust: write a lease at READ COMMITTED, not %',
            current_setting('transaction_isolation')
            USING ERRCODE = 'invalid_transaction_state';
    END IF;
    PERFORM u.id FROM trust_units AS u WHERE u.id = NEW.unit_id FOR NO KEY UPDATE;

    SELECT l.id INTO v_other
    FROM trust_leases AS l
    WHERE l.unit_id = NEW.unit_id
      AND l.id <> NEW.id
      AND l.starts_on <= coalesce(NEW.ends_on, 'infinity'::date)
      AND NEW.starts_on <= coalesce(l.ends_on, 'infinity'::date)
    LIMIT 1;
    IF FOUND THEN
        RAISE EXCEPTION 'trust: the lease would overlap lease % of the same unit', v_other
            USING ERRCODE = 'exclusion_violation',
                  HINT = 'End the other lease first; a unit has one lease at a time.';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER trust_one_lease_at_a_time
BEFORE INSERT OR UPDATE ON trust_leases
FOR EACH ROW EXECUTE FUNCTION trust_refuse_overlapping_leases();

-- Open a lease of a unit for one or more of the property's tenants. Runs as the owner.
CREATE FUNCTION trust_open_lease(
    p_pmc_id uuid,
    p_unit_id uuid,
    p_starts_on date,
    p_ends_on date,
    p_monthly_rent numeric,
    p_tenant_ids uuid []
) RETURNS uuid
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
DECLARE
    v_property_id uuid;
    v_lease_id uuid;
BEGIN
    SELECT u.property_id INTO v_property_id
    FROM trust_units AS u
    WHERE u.id = p_unit_id AND u.pmc_id = p_pmc_id;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'trust: no unit % in PMC %', p_unit_id, p_pmc_id
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    IF coalesce(cardinality(p_tenant_ids), 0) = 0 THEN
        RAISE EXCEPTION 'trust: a lease needs at least one tenant'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    IF EXISTS (
        SELECT 1 FROM unnest(p_tenant_ids) AS r (tenant_id)
        LEFT JOIN trust_tenants AS t ON t.id = r.tenant_id AND t.pmc_id = p_pmc_id
        WHERE t.property_id IS DISTINCT FROM v_property_id
    ) THEN
        RAISE EXCEPTION 'trust: every tenant on a lease must be a tenant of the unit''s property'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    INSERT INTO trust_leases (pmc_id, unit_id, starts_on, ends_on, monthly_rent)
    VALUES (p_pmc_id, p_unit_id, p_starts_on, p_ends_on, p_monthly_rent)
    RETURNING id INTO v_lease_id;

    INSERT INTO trust_lease_tenants (pmc_id, lease_id, tenant_id)
    SELECT DISTINCT p_pmc_id, v_lease_id, r.tenant_id FROM unnest(p_tenant_ids) AS r (tenant_id);

    RETURN v_lease_id;
END;
$$;

REVOKE ALL ON FUNCTION trust_open_lease(uuid, uuid, date, date, numeric, uuid []) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION trust_open_lease(uuid, uuid, date, date, numeric, uuid []) TO trust_app;

-- Set a lease's last day, or clear it: a move-out, a fixed term turned month to month, or the
-- day before a renewal's new lease starts. Runs as the owner.
CREATE FUNCTION trust_end_lease(
    p_pmc_id uuid,
    p_lease_id uuid,
    p_ends_on date
) RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
BEGIN
    UPDATE trust_leases SET ends_on = p_ends_on WHERE id = p_lease_id AND pmc_id = p_pmc_id;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'trust: no lease % in PMC %', p_lease_id, p_pmc_id
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
END;
$$;

REVOKE ALL ON FUNCTION trust_end_lease(uuid, uuid, date) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION trust_end_lease(uuid, uuid, date) TO trust_app;

-- Match part of a ledger transfer to a charge it pays. The transfer must bring money into the
-- owner's account for the charge's property, from cash or from money held for a tenant on the
-- charge's lease: prepaid rent, or a deposit kept at move-out. Safe to retry: the same charge and transfer return the same match, and a
-- different amount for them is refused. Runs as the owner.
CREATE FUNCTION trust_apply_payment(
    p_pmc_id uuid,
    p_charge_id uuid,
    p_transfer_id text,
    p_amount numeric
) RETURNS numeric
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
DECLARE
    v_charge trust_charges;
    v_property_id uuid;
    v_transfer pgledger_transfers;
    v_existing numeric;
    v_paid numeric;
BEGIN
    -- The sums below must count every match that committed while this waited for the locks.
    IF current_setting('transaction_isolation') <> 'read committed' THEN
        RAISE EXCEPTION 'trust: apply a payment at READ COMMITTED, not %',
            current_setting('transaction_isolation')
            USING ERRCODE = 'invalid_transaction_state';
    END IF;
    IF p_amount IS NULL OR p_amount <= 0 THEN
        RAISE EXCEPTION 'trust: a payment applies a positive amount, not %', p_amount
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    -- Matches to one charge, and then to one transfer, wait for each other.
    SELECT c.* INTO v_charge
    FROM trust_charges AS c
    WHERE c.id = p_charge_id AND c.pmc_id = p_pmc_id
    FOR NO KEY UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'trust: no charge % in PMC %', p_charge_id, p_pmc_id
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    IF v_charge.kind = 'credit' THEN
        RAISE EXCEPTION 'trust: a credit is not paid; it takes its amount off what is owed'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    SELECT t.* INTO v_transfer
    FROM pgledger_transfers AS t
    WHERE t.id = p_transfer_id
    FOR NO KEY UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'trust: no transfer %', p_transfer_id
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    SELECT u.property_id INTO v_property_id
    FROM trust_leases AS l JOIN trust_units AS u ON u.id = l.unit_id
    WHERE l.id = v_charge.lease_id;
    IF NOT EXISTS (
        SELECT 1 FROM trust_ledger_accounts AS t
        WHERE t.ledger_account_id = v_transfer.to_account_id AND t.pmc_id = p_pmc_id
          AND t.kind = 'owner_property' AND t.property_id = v_property_id
    ) OR NOT EXISTS (
        SELECT 1 FROM trust_ledger_accounts AS f
        WHERE f.ledger_account_id = v_transfer.from_account_id AND f.pmc_id = p_pmc_id
          AND (
              f.kind = 'bank_cash'
              OR (f.kind IN ('prepaid_rent', 'tenant_deposit') AND f.tenant_id IN (
                  SELECT lt.tenant_id FROM trust_lease_tenants AS lt
                  WHERE lt.lease_id = v_charge.lease_id
              ))
          )
    ) THEN
        RAISE EXCEPTION 'trust: transfer % did not pay into the owner''s account for this '
            'charge''s property, from cash or money held for a tenant on its lease', p_transfer_id
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    SELECT p.amount INTO v_existing
    FROM trust_charge_payments AS p
    WHERE p.charge_id = p_charge_id AND p.transfer_id = p_transfer_id;
    IF FOUND THEN
        IF v_existing <> p_amount THEN
            RAISE EXCEPTION 'trust: transfer % already pays % of charge %, not %',
                p_transfer_id, v_existing, p_charge_id, p_amount
                USING ERRCODE = 'unique_violation';
        END IF;
        RETURN v_existing;  -- a retry: the original match
    END IF;

    SELECT coalesce(sum(p.amount), 0) INTO v_paid
    FROM trust_charge_payments AS p WHERE p.charge_id = p_charge_id;
    IF v_paid + p_amount > v_charge.amount THEN
        RAISE EXCEPTION 'trust: charge % is % and already paid %; % more is too much',
            p_charge_id, v_charge.amount, v_paid, p_amount
            USING ERRCODE = 'check_violation';
    END IF;

    SELECT coalesce(sum(p.amount), 0) INTO v_paid
    FROM trust_charge_payments AS p WHERE p.transfer_id = p_transfer_id;
    IF v_paid + p_amount > v_transfer.amount THEN
        RAISE EXCEPTION 'trust: transfer % moved % and already pays %; % more is too much',
            p_transfer_id, v_transfer.amount, v_paid, p_amount
            USING ERRCODE = 'check_violation';
    END IF;

    INSERT INTO trust_charge_payments (pmc_id, charge_id, transfer_id, amount)
    VALUES (p_pmc_id, p_charge_id, p_transfer_id, p_amount);
    RETURN p_amount;
END;
$$;

REVOKE ALL ON FUNCTION trust_apply_payment(uuid, uuid, text, numeric) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION trust_apply_payment(uuid, uuid, text, numeric) TO trust_app;

-- migrate:down
DO $$
BEGIN
    RAISE EXCEPTION 'leases_and_charges cannot be rolled back: leases and charges are records';
END;
$$;
