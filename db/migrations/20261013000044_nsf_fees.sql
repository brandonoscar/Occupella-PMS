-- migrate:up
-- A fee for a payment the bank returned (founder decision, 2026-10-13, product item 4: "NSF fee
-- on a bounced payment, under terms set per property like late fees").
--
--   trust_nsf_fee_policies   a property's NSF fee, in force from a date (new terms are a new
--                            row; a fee of 0 charges none from that day).
--   trust_nsf_fees           the NSF fee charged for one returned payment: the terms, the fee
--                            and the fee's own charge.
--   trust_charge_nsf_fee     charges a returned payment's lease the NSF fee in force on the day
--                            it came back (UTC), due that day. Once per returned payment, so it
--                            is safe to retry.
--
-- A returned payment is what staff record when the bank sends a tenant's payment back: postings
-- under one key, its metadata saying so and naming the lease, each moving money from the owner's
-- account or the tenant's prepaid rent back to a trust bank account's cash. An NSF fee is a
-- charge like any other: shown on the rent roll and the tenant's ledger, and paid with
-- trust_apply_payment. It is a one-off fee, so neither a management fee nor a late fee counts it.

-- On a live database, give up rather than queue behind a long transaction while holding locks.
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '60s';

CREATE TABLE trust_nsf_fee_policies (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    pmc_id uuid NOT NULL REFERENCES trust_pmcs (pmc_id),
    property_id uuid NOT NULL,
    starts_on date NOT NULL,
    fee numeric NOT NULL CHECK (fee >= 0 AND fee = round(fee, 2)),  -- 0: none from that day
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (property_id, starts_on),
    FOREIGN KEY (pmc_id, property_id) REFERENCES trust_properties (pmc_id, id)
);

CREATE TABLE trust_nsf_fees (
    pmc_id uuid NOT NULL REFERENCES trust_pmcs (pmc_id),
    returned_key text NOT NULL,  -- the key the returned payment was posted under
    policy_id uuid NOT NULL REFERENCES trust_nsf_fee_policies (id),
    fee numeric NOT NULL CHECK (fee > 0),
    fee_charge_id uuid NOT NULL UNIQUE,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (pmc_id, returned_key),
    FOREIGN KEY (pmc_id, returned_key) REFERENCES trust_idempotency_keys (pmc_id, idempotency_key),
    FOREIGN KEY (pmc_id, fee_charge_id) REFERENCES trust_charges (pmc_id, id)
);
CREATE INDEX trust_nsf_fees_policy_id ON trust_nsf_fees (policy_id);

-- Records: kept for good. New terms are a new policy; a fee charged by mistake is answered
-- with a credit.
CREATE TRIGGER trust_append_only
BEFORE UPDATE OR DELETE OR TRUNCATE ON trust_nsf_fee_policies
FOR EACH STATEMENT EXECUTE FUNCTION trust_refuse_ledger_rewrite();
CREATE TRIGGER trust_append_only
BEFORE UPDATE OR DELETE OR TRUNCATE ON trust_nsf_fees
FOR EACH STATEMENT EXECUTE FUNCTION trust_refuse_ledger_rewrite();
CREATE TRIGGER trust_audit AFTER INSERT OR UPDATE OR DELETE ON trust_nsf_fee_policies
FOR EACH ROW EXECUTE FUNCTION trust_audit();
CREATE TRIGGER trust_audit AFTER INSERT OR UPDATE OR DELETE ON trust_nsf_fees
FOR EACH ROW EXECUTE FUNCTION trust_audit();

REVOKE ALL ON trust_nsf_fee_policies, trust_nsf_fees FROM PUBLIC;
-- The app records NSF fee terms; NSF fees are charged only by trust_charge_nsf_fee.
GRANT SELECT, INSERT ON trust_nsf_fee_policies TO trust_app;
GRANT SELECT ON trust_nsf_fees TO trust_app;
GRANT SELECT ON trust_nsf_fee_policies, trust_nsf_fees TO trust_ai_agent;

-- Charge the lease of the payment returned under key p_returned_key of PMC p_pmc_id the NSF fee
-- of its property in force on the day it came back (UTC), due that day; the fee's charge, or
-- NULL when the terms charge none. The key is locked first, so a retry or two calls at once
-- charge one fee, and the same key again returns the same charge. READ COMMITTED only. Runs as
-- the owner.
CREATE FUNCTION trust_charge_nsf_fee(p_pmc_id uuid, p_returned_key text)
RETURNS uuid
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
DECLARE
    v_key trust_idempotency_keys;
    v_lease trust_leases;
    v_on date;
    v_terms trust_nsf_fee_policies;
    v_fee_id uuid;
BEGIN
    IF current_setting('transaction_isolation') <> 'read committed' THEN
        RAISE EXCEPTION 'trust: charge an NSF fee at READ COMMITTED, not %',
            current_setting('transaction_isolation')
            USING ERRCODE = 'invalid_transaction_state';
    END IF;

    SELECT k.* INTO v_key FROM trust_idempotency_keys AS k
    WHERE k.pmc_id = p_pmc_id AND k.idempotency_key = p_returned_key
    FOR NO KEY UPDATE;
    IF NOT FOUND OR v_key.request -> 'metadata' ->> 'kind' IS DISTINCT FROM 'bounced payment' THEN
        RAISE EXCEPTION 'trust: no returned payment % in PMC %', p_returned_key, p_pmc_id
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    SELECT f.fee_charge_id INTO v_fee_id FROM trust_nsf_fees AS f
    WHERE f.pmc_id = p_pmc_id AND f.returned_key = p_returned_key;
    IF FOUND THEN
        RETURN v_fee_id;
    END IF;

    SELECT l.* INTO v_lease FROM trust_leases AS l
    WHERE l.pmc_id = p_pmc_id AND l.id::text = v_key.request -> 'metadata' ->> 'lease_id';
    IF NOT FOUND THEN
        RAISE EXCEPTION 'trust: returned payment % names no lease of PMC %',
            p_returned_key, p_pmc_id
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    -- Money the bank sent back: each posting moves it from what the payment paid into (the
    -- owner's account or the tenant's prepaid rent) back to a trust bank account's cash.
    IF EXISTS (
        SELECT t.id
        FROM pgledger_transfers AS t
        JOIN trust_ledger_accounts AS f ON f.ledger_account_id = t.from_account_id
        JOIN trust_ledger_accounts AS c ON c.ledger_account_id = t.to_account_id
        WHERE t.id = ANY (v_key.transfer_ids)
          AND (f.kind NOT IN ('owner_property', 'prepaid_rent') OR c.kind <> 'bank_cash')
    ) THEN
        RAISE EXCEPTION 'trust: % moves money that is not a returned payment', p_returned_key
            USING ERRCODE = 'check_violation';
    END IF;

    SELECT (min(t.event_at) AT TIME ZONE 'UTC')::date INTO v_on
    FROM pgledger_transfers AS t WHERE t.id = ANY (v_key.transfer_ids);
    -- The terms in force that day: the latest starting on or before it.
    SELECT p.* INTO v_terms
    FROM trust_nsf_fee_policies AS p
    JOIN trust_units AS u ON u.property_id = p.property_id
    WHERE u.id = v_lease.unit_id AND p.starts_on <= v_on
    ORDER BY p.starts_on DESC
    LIMIT 1;
    IF NOT FOUND OR v_terms.fee = 0 THEN
        RETURN NULL;
    END IF;

    INSERT INTO trust_charges (pmc_id, lease_id, due_on, kind, amount, memo)
    VALUES (
        p_pmc_id, v_lease.id, v_on, 'fee', v_terms.fee,
        'NSF fee, payment returned ' || to_char(v_on, 'YYYY-MM-DD')
    )
    RETURNING id INTO v_fee_id;
    INSERT INTO trust_nsf_fees (pmc_id, returned_key, policy_id, fee, fee_charge_id)
    VALUES (p_pmc_id, p_returned_key, v_terms.id, v_terms.fee, v_fee_id);
    RETURN v_fee_id;
END;
$$;

REVOKE ALL ON FUNCTION trust_charge_nsf_fee(uuid, text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION trust_charge_nsf_fee(uuid, text) TO trust_app;

-- migrate:down
DO $$
BEGIN
    RAISE EXCEPTION 'nsf_fees cannot be rolled back: NSF fees are records';
END;
$$;
