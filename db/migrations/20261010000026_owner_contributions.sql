-- migrate:up
-- Owner contributions (founder decision, 2026-10-10: Buildium/AppFolio parity, "do 1 through 3").
--
-- An owner sends money in to cover a repair or a shortfall. Posted as plain cash into the
-- owner's property account, it would look like a tenant's payment: it could be matched to a
-- tenant's rent, which would mark the rent paid and take a management fee on it.
-- trust_record_owner_contribution posts it as its own kind: from the trust bank account's cash
-- into the owner's property account, recorded in trust_owner_contributions with its memo, once
-- per request key (safe to retry: the same request returns the original transfer, a different
-- one under the key is refused). trust_apply_payment refuses to match a contribution to any
-- charge. Runs as the owner, at READ COMMITTED.

-- On a live database, give up rather than queue behind a long transaction while holding locks.
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '60s';

CREATE TABLE trust_owner_contributions (
    pmc_id uuid NOT NULL REFERENCES trust_pmcs (pmc_id),
    request_key text NOT NULL CHECK (request_key <> '' AND length(request_key) <= 200),
    ledger_account_id text NOT NULL REFERENCES trust_ledger_accounts (ledger_account_id),
    amount numeric NOT NULL CHECK (amount > 0),
    transfer_id text NOT NULL UNIQUE REFERENCES pgledger_transfers (id),
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (pmc_id, request_key)
);

CREATE TRIGGER trust_append_only
BEFORE UPDATE OR DELETE OR TRUNCATE ON trust_owner_contributions
FOR EACH STATEMENT EXECUTE FUNCTION trust_refuse_ledger_rewrite();
CREATE TRIGGER trust_audit AFTER INSERT OR UPDATE OR DELETE ON trust_owner_contributions
FOR EACH ROW EXECUTE FUNCTION trust_audit();

REVOKE ALL ON trust_owner_contributions FROM PUBLIC;
GRANT SELECT ON trust_owner_contributions TO trust_app, trust_ai_agent;

CREATE FUNCTION trust_record_owner_contribution(
    p_pmc_id uuid,
    p_account text,
    p_request_key text,
    p_amount numeric,
    p_event_at timestamptz,
    p_memo text DEFAULT NULL
) RETURNS text
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
DECLARE
    v_account trust_ledger_accounts;
    v_taken trust_owner_contributions;
    v_cash text;
    v_transfer_id text;
BEGIN
    IF p_amount IS NULL OR p_amount <= 0 OR p_amount <> round(p_amount, 2) THEN
        RAISE EXCEPTION 'trust: a contribution is a positive amount in cents, not %', p_amount
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    -- READ COMMITTED, and contributions to one account wait for each other.
    v_account := trust_lock_owner_account(p_pmc_id, p_account);

    SELECT c.* INTO v_taken
    FROM trust_owner_contributions AS c
    WHERE c.pmc_id = p_pmc_id AND c.request_key = p_request_key;
    IF FOUND THEN
        IF (v_taken.ledger_account_id, v_taken.amount) IS DISTINCT FROM (p_account, p_amount) THEN
            RAISE EXCEPTION 'trust: request key % already recorded % into %',
                p_request_key, v_taken.amount, v_taken.ledger_account_id
                USING ERRCODE = 'unique_violation',
                      HINT = 'Use a new key for a new contribution; resend a key only to retry.';
        END IF;
        RETURN v_taken.transfer_id;  -- a retry: the original contribution
    END IF;

    -- Money reaches an owner's account through its trust bank account's cash.
    SELECT t.ledger_account_id INTO v_cash
    FROM trust_ledger_accounts AS t
    WHERE t.bank_account_id = v_account.bank_account_id AND t.kind = 'bank_cash';
    IF NOT FOUND THEN
        RAISE EXCEPTION 'trust: open the cash account (bank_cash) of trust bank account % first',
            v_account.bank_account_id
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    SELECT t.id INTO v_transfer_id
    FROM pgledger_create_transfers(
        ARRAY[(v_cash, p_account, p_amount)::transfer_request],
        p_event_at,
        jsonb_build_object('memo', coalesce(nullif(p_memo, ''), 'Owner contribution'))
    ) AS t;

    INSERT INTO trust_owner_contributions (
        pmc_id, request_key, ledger_account_id, amount, transfer_id
    ) VALUES (p_pmc_id, p_request_key, p_account, p_amount, v_transfer_id);
    RETURN v_transfer_id;
END;
$$;

REVOKE ALL ON FUNCTION trust_record_owner_contribution(uuid, text, text, numeric, timestamptz, text)
FROM PUBLIC;
GRANT EXECUTE ON FUNCTION
    trust_record_owner_contribution(uuid, text, text, numeric, timestamptz, text)
TO trust_app;

-- Match a payment to a charge: as before (migration 20261010000025), and never an owner's
-- contribution. CREATE OR REPLACE keeps its grants.
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
    IF EXISTS (
        SELECT c.transfer_id FROM trust_owner_contributions AS c
        WHERE c.transfer_id = p_transfer_id
    ) THEN
        RAISE EXCEPTION 'trust: transfer % is an owner''s contribution, not a tenant''s payment',
            p_transfer_id
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
    RAISE EXCEPTION 'owner_contributions cannot be rolled back: contributions are ledger history';
END;
$$;
