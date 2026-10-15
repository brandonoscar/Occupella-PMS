-- migrate:up
-- The company takes its earned fees out of trust (founder, 2026-10-15: "make sure what we are
-- doing is by law ... like commingling"). A management or leasing fee moves money from an
-- owner's account to the PMC's fee account (pmc_income), which still sits in the trust bank
-- account. Once earned it is the company's own money, and state license law forbids a broker to
-- keep their own money mixed with what they hold for others (commingling): West Virginia Code
-- 30-40-18(f), California Business and Professions Code 10176(e) and 10 CCR 2835 (take it out
-- within 25 days), Texas 22 TAC 535.146 (30 days), Oregon OAR 863-025-0027 (monthly). Until now
-- only a journal entry could move it out. The PMS keeps none of the broker's own money in trust
-- otherwise (no account holds it), which meets every state's cap on it (West Virginia: $100).
--
--   trust_pmc_fee_withdrawals   the company's fees taken out of a trust bank account, by request
--                               key: from its fee account to its cash, as the bank paid them out
--                               to the company's own account.
--
-- trust_withdraw_pmc_fees takes what is asked, or everything the fee account holds, never more
-- (the fee account never goes below zero), once per request key: the same key again returns
-- the original amount, a different withdrawal under it is refused. It pays out money, so a
-- named staff member needs the disburse role, as for an owner draw or a bill. It moves no
-- owner's or tenant's money. READ COMMITTED, the fee account locked so two at once wait.
--
-- Records: append-only and in the audit log. trust_ai_agent reads them and calls nothing here.

-- On a live database, give up rather than queue behind a long transaction while holding locks.
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '60s';

CREATE TABLE trust_pmc_fee_withdrawals (
    pmc_id uuid NOT NULL REFERENCES trust_pmcs (pmc_id),
    request_key text NOT NULL CHECK (request_key <> ''),
    ledger_account_id text NOT NULL REFERENCES trust_ledger_accounts (ledger_account_id),
    amount numeric NOT NULL CHECK (amount > 0),
    transfer_id text NOT NULL REFERENCES pgledger_transfers (id),
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (pmc_id, request_key)
);
CREATE INDEX trust_pmc_fee_withdrawals_ledger_account_id
ON trust_pmc_fee_withdrawals (ledger_account_id);
CREATE INDEX trust_pmc_fee_withdrawals_transfer_id ON trust_pmc_fee_withdrawals (transfer_id);

CREATE TRIGGER trust_append_only
BEFORE UPDATE OR DELETE OR TRUNCATE ON trust_pmc_fee_withdrawals
FOR EACH STATEMENT EXECUTE FUNCTION trust_refuse_ledger_rewrite();
CREATE TRIGGER trust_audit AFTER INSERT OR UPDATE OR DELETE ON trust_pmc_fee_withdrawals
FOR EACH ROW EXECUTE FUNCTION trust_audit();
CREATE TRIGGER trust_disburse
BEFORE INSERT ON trust_pmc_fee_withdrawals
FOR EACH ROW EXECUTE FUNCTION trust_check_staff_role('disburse', 'pay out money');

REVOKE ALL ON trust_pmc_fee_withdrawals FROM PUBLIC;
-- Withdrawals go through the function below.
GRANT SELECT ON trust_pmc_fee_withdrawals TO trust_app, trust_ai_agent;

CREATE FUNCTION trust_withdraw_pmc_fees(
    p_pmc_id uuid,
    p_bank_account_id uuid,
    p_request_key text,
    p_amount numeric,
    p_event_at timestamptz
)
RETURNS numeric
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
DECLARE
    v_fees text;
    v_cash text;
    v_taken trust_pmc_fee_withdrawals;
    v_held numeric;
    v_transfer_id text;
BEGIN
    IF current_setting('transaction_isolation') <> 'read committed' THEN
        RAISE EXCEPTION 'trust: take the company''s fees at READ COMMITTED, not %',
            current_setting('transaction_isolation')
            USING ERRCODE = 'invalid_transaction_state';
    END IF;
    IF coalesce(p_request_key, '') = '' THEN
        RAISE EXCEPTION 'trust: taking the company''s fees needs a request key, so a retry '
            'can''t pay twice'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    IF p_amount <= 0 THEN
        RAISE EXCEPTION 'trust: taking the company''s fees pays a positive amount, not %',
            p_amount
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    -- The fee account of the trust bank account, locked so two withdrawals wait for each other
    -- and the second reads the balance the first left.
    SELECT t.ledger_account_id INTO v_fees
    FROM trust_ledger_accounts AS t
    WHERE t.bank_account_id = p_bank_account_id AND t.pmc_id = p_pmc_id
      AND t.kind = 'pmc_income'
    FOR NO KEY UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'trust: trust bank account % of PMC % holds no fee account of the '
            'company', p_bank_account_id, p_pmc_id
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    SELECT w.* INTO v_taken
    FROM trust_pmc_fee_withdrawals AS w
    WHERE w.pmc_id = p_pmc_id AND w.request_key = p_request_key;
    IF FOUND THEN
        IF v_taken.ledger_account_id <> v_fees
            OR v_taken.amount <> coalesce(p_amount, v_taken.amount) THEN
            RAISE EXCEPTION 'trust: request key % already took % of the company''s fees from %',
                p_request_key, round(v_taken.amount, 2), v_taken.ledger_account_id
                USING ERRCODE = 'unique_violation',
                      HINT = 'Use a new key for a new withdrawal; resend a key only to retry.';
        END IF;
        RETURN v_taken.amount;  -- a retry: the original withdrawal
    END IF;

    SELECT a.balance INTO v_held FROM pgledger_accounts AS a WHERE a.id = v_fees;
    IF v_held <= 0 OR coalesce(p_amount, v_held) > v_held THEN
        RAISE EXCEPTION 'trust: % of the company''s fees is in %; % is too much',
            round(v_held, 2), v_fees, round(coalesce(p_amount, v_held), 2)
            USING ERRCODE = 'check_violation';
    END IF;

    SELECT t.ledger_account_id INTO v_cash
    FROM trust_ledger_accounts AS t
    WHERE t.bank_account_id = p_bank_account_id AND t.kind = 'bank_cash';
    SELECT t.id INTO v_transfer_id
    FROM pgledger_create_transfers(
        ARRAY[(v_fees, v_cash, coalesce(p_amount, v_held))::transfer_request],
        p_event_at,
        jsonb_build_object('memo', 'Company fees taken out of trust')
    ) AS t;

    INSERT INTO trust_pmc_fee_withdrawals (pmc_id, request_key, ledger_account_id, amount,
                                           transfer_id)
    VALUES (p_pmc_id, p_request_key, v_fees, coalesce(p_amount, v_held), v_transfer_id);
    RETURN coalesce(p_amount, v_held);
END;
$$;

REVOKE ALL ON FUNCTION trust_withdraw_pmc_fees(uuid, uuid, text, numeric, timestamptz)
FROM PUBLIC;
GRANT EXECUTE ON FUNCTION trust_withdraw_pmc_fees(uuid, uuid, text, numeric, timestamptz)
TO trust_app;

-- migrate:down
DO $$
BEGIN
    RAISE EXCEPTION 'fee withdrawals cannot be rolled back: they are records';
END;
$$;
