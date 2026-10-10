-- migrate:up
-- Payment holds (founder decision, 2026-10-10: Buildium/AppFolio parity, "do 1 through 3").
--
-- Once an eviction is filed, accepting rent can waive it, so property managers stop taking
-- payments on the lease. A payment hold records that: from a day, payments dated on or after it
-- (UTC) are not matched to the lease's charges, whoever tries. A hold ends with a release, from
-- a later day. A payment the PMC decides to take anyway (a full redemption, say) is allowed one
-- transfer at a time, with who allowed it. Which payments to refuse, and when, is the PMC's
-- call: no state's rule is built in.
--
-- The money itself still reaches the trust ledger: a hold refuses the match in
-- trust_apply_payment, so the charge stays unpaid until the PMC allows the payment or returns
-- it. A match made before the hold stands, and so does a retry of it.
--
-- Holds, releases and allowances are records, append-only like the ledger; the app writes them
-- as plain rows and a trigger checks what a constraint can't. Placing a hold takes the lease's
-- row lock, which trust_apply_payment also takes, so a match in flight finishes first and a
-- match after it sees the hold.

-- On a live database, give up rather than queue behind a long transaction while holding locks.
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '60s';

CREATE TABLE trust_payment_holds (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    pmc_id uuid NOT NULL REFERENCES trust_pmcs (pmc_id),
    lease_id uuid NOT NULL,
    starts_on date NOT NULL,
    reason text NOT NULL CHECK (reason <> ''),
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (pmc_id, id),
    FOREIGN KEY (pmc_id, lease_id) REFERENCES trust_leases (pmc_id, id)
);
CREATE INDEX trust_payment_holds_lease_id ON trust_payment_holds (lease_id);

CREATE TABLE trust_payment_hold_releases (
    hold_id uuid PRIMARY KEY,
    pmc_id uuid NOT NULL REFERENCES trust_pmcs (pmc_id),
    ends_on date NOT NULL,
    released_by text NOT NULL CHECK (released_by <> ''),
    created_at timestamptz NOT NULL DEFAULT now(),
    FOREIGN KEY (pmc_id, hold_id) REFERENCES trust_payment_holds (pmc_id, id)
);

CREATE TABLE trust_payment_hold_allowances (
    hold_id uuid NOT NULL,
    transfer_id text NOT NULL REFERENCES pgledger_transfers (id),
    pmc_id uuid NOT NULL REFERENCES trust_pmcs (pmc_id),
    allowed_by text NOT NULL CHECK (allowed_by <> ''),
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (hold_id, transfer_id),
    FOREIGN KEY (pmc_id, hold_id) REFERENCES trust_payment_holds (pmc_id, id)
);

CREATE TRIGGER trust_append_only
BEFORE UPDATE OR DELETE OR TRUNCATE ON trust_payment_holds
FOR EACH STATEMENT EXECUTE FUNCTION trust_refuse_ledger_rewrite();
CREATE TRIGGER trust_append_only
BEFORE UPDATE OR DELETE OR TRUNCATE ON trust_payment_hold_releases
FOR EACH STATEMENT EXECUTE FUNCTION trust_refuse_ledger_rewrite();
CREATE TRIGGER trust_append_only
BEFORE UPDATE OR DELETE OR TRUNCATE ON trust_payment_hold_allowances
FOR EACH STATEMENT EXECUTE FUNCTION trust_refuse_ledger_rewrite();

CREATE TRIGGER trust_audit AFTER INSERT OR UPDATE OR DELETE ON trust_payment_holds
FOR EACH ROW EXECUTE FUNCTION trust_audit();
CREATE TRIGGER trust_audit AFTER INSERT OR UPDATE OR DELETE ON trust_payment_hold_releases
FOR EACH ROW EXECUTE FUNCTION trust_audit();
CREATE TRIGGER trust_audit AFTER INSERT OR UPDATE OR DELETE ON trust_payment_hold_allowances
FOR EACH ROW EXECUTE FUNCTION trust_audit();

REVOKE ALL ON trust_payment_holds, trust_payment_hold_releases, trust_payment_hold_allowances
FROM PUBLIC;
GRANT SELECT, INSERT ON
    trust_payment_holds, trust_payment_hold_releases, trust_payment_hold_allowances
TO trust_app;
GRANT SELECT ON
    trust_payment_holds, trust_payment_hold_releases, trust_payment_hold_allowances
TO trust_ai_agent;

-- What a constraint can't check, one trigger function per table. A hold takes its lease's
-- lock: as the owner, since the lock needs a write grant on trust_leases the app doesn't have.
CREATE FUNCTION trust_check_payment_hold() RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
BEGIN
    -- Waits for matches in flight on the lease (they hold its row FOR SHARE), and holds new
    -- ones off until this commits.
    PERFORM l.id FROM trust_leases AS l WHERE l.id = NEW.lease_id FOR NO KEY UPDATE;
    RETURN NEW;
END;
$$;

REVOKE ALL ON FUNCTION trust_check_payment_hold() FROM PUBLIC;

CREATE TRIGGER trust_check_payment_hold
BEFORE INSERT ON trust_payment_holds
FOR EACH ROW EXECUTE FUNCTION trust_check_payment_hold();

-- A release ends its hold after the hold starts.
CREATE FUNCTION trust_check_hold_release() RETURNS trigger
LANGUAGE plpgsql
SET search_path = public, pg_temp
AS $$
DECLARE
    v_starts_on date;
BEGIN
    SELECT h.starts_on INTO v_starts_on FROM trust_payment_holds AS h WHERE h.id = NEW.hold_id;
    IF NEW.ends_on <= v_starts_on THEN
        RAISE EXCEPTION 'trust: hold % starts on %; its release must end it later, not on %',
            NEW.hold_id, v_starts_on, NEW.ends_on
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER trust_check_hold_release
BEFORE INSERT ON trust_payment_hold_releases
FOR EACH ROW EXECUTE FUNCTION trust_check_hold_release();

-- An allowance names a transfer of the hold's PMC.
CREATE FUNCTION trust_check_hold_allowance() RETURNS trigger
LANGUAGE plpgsql
SET search_path = public, pg_temp
AS $$
BEGIN
    IF NOT EXISTS (
        SELECT t.ledger_account_id
        FROM pgledger_transfers AS tr
        JOIN trust_ledger_accounts AS t ON t.ledger_account_id = tr.from_account_id
        WHERE tr.id = NEW.transfer_id AND t.pmc_id = NEW.pmc_id
    ) THEN
        RAISE EXCEPTION 'trust: transfer % is not in PMC %', NEW.transfer_id, NEW.pmc_id
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER trust_check_hold_allowance
BEFORE INSERT ON trust_payment_hold_allowances
FOR EACH ROW EXECUTE FUNCTION trust_check_hold_allowance();

-- Match a payment to a charge: as before (migration 20261010000014), and refused while the
-- charge's lease is on a payment hold that doesn't allow the transfer. CREATE OR REPLACE keeps
-- its grants.
CREATE OR REPLACE FUNCTION trust_apply_payment(
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
    v_hold trust_payment_holds;
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
        SELECT t.ledger_account_id FROM trust_ledger_accounts AS t
        WHERE t.ledger_account_id = v_transfer.to_account_id AND t.pmc_id = p_pmc_id
          AND t.kind = 'owner_property' AND t.property_id = v_property_id
    ) OR NOT EXISTS (
        SELECT f.ledger_account_id FROM trust_ledger_accounts AS f
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

    -- A payment hold on the lease (an eviction, say) in force on the transfer's day (UTC)
    -- refuses it, unless the transfer was allowed for that hold. The lease's row lock orders
    -- this against a hold being placed: one waits for the other, then sees it.
    PERFORM l.id FROM trust_leases AS l WHERE l.id = v_charge.lease_id FOR SHARE;
    SELECT h.* INTO v_hold
    FROM trust_payment_holds AS h
    LEFT JOIN trust_payment_hold_releases AS r ON r.hold_id = h.id
    WHERE h.lease_id = v_charge.lease_id
      AND h.starts_on <= (v_transfer.event_at AT TIME ZONE 'UTC')::date
      AND (r.ends_on IS NULL OR (v_transfer.event_at AT TIME ZONE 'UTC')::date < r.ends_on)
      AND NOT EXISTS (
          SELECT a.hold_id FROM trust_payment_hold_allowances AS a
          WHERE a.hold_id = h.id AND a.transfer_id = p_transfer_id
      )
    ORDER BY h.starts_on, h.id
    LIMIT 1;
    IF FOUND THEN
        RAISE EXCEPTION 'trust: lease % is on a payment hold from % (%); transfer % is not '
            'allowed for it', v_charge.lease_id, v_hold.starts_on, v_hold.reason, p_transfer_id
            USING ERRCODE = 'check_violation',
                  HINT = 'Record an allowance for this transfer (trust_payment_hold_allowances) '
                         'to accept it.';
    END IF;

    -- Paid so far: matched, less what bounced back (trust_reverse_payment).
    SELECT coalesce(sum(p.amount), 0) INTO v_paid
    FROM trust_charge_payments AS p WHERE p.charge_id = p_charge_id;
    SELECT v_paid - coalesce(sum(r.amount), 0) INTO v_paid
    FROM trust_payment_reversals AS r WHERE r.charge_id = p_charge_id;
    IF v_paid + p_amount > v_charge.amount THEN
        RAISE EXCEPTION 'trust: charge % is % and already paid %; % more is too much',
            p_charge_id, v_charge.amount, v_paid, p_amount
            USING ERRCODE = 'check_violation';
    END IF;

    -- A transfer pays only once, even after a reversal: its money left the owner's account.
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


-- migrate:down
DO $$
BEGIN
    RAISE EXCEPTION 'payment_holds cannot be rolled back: holds and allowances are records';
END;
$$;
