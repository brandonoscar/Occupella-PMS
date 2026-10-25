-- migrate:up
-- The tie-out checks only what can untie a trust bank account (the enterprise test, 2026-10-25).
--
-- At commit, trust_bank_tie_out checks each transfer of the transaction: every trust bank account
-- it touched must still hold, in all its accounts, exactly its cash (their balances sum to zero).
-- It summed the whole bank account for every transfer, so a transaction of n transfers in a bank
-- account of m ledger accounts read n times m rows: opening the deposits of 20,000 tenants took
-- 25 minutes, and every payment in a bank account of 20,000 accounts read all of them.
--
-- A transfer between two accounts of the same trust bank account leaves that bank account's sum as
-- it was (one balance down by the amount, the other up by it), and touches no other one. So it
-- can't untie anything, and the check skips it. A transfer into or out of a trust bank account,
-- or between two of them, is checked as before, at commit, against every balance: whatever else
-- the transaction did is in the sums it reads. What every transaction leaves is unchanged: each
-- trust bank account ties out at commit, as every transaction before it left it.

SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '60s';

CREATE OR REPLACE FUNCTION trust_check_bank_tie_out() RETURNS trigger
LANGUAGE plpgsql
SET search_path = public, pg_temp
AS $$
DECLARE
    v_banks uuid[];
    v_mapped integer;
    v_bank uuid;
    v_off numeric;
BEGIN
    SELECT array_agg(DISTINCT t.bank_account_id), count(*) INTO v_banks, v_mapped
    FROM trust_ledger_accounts AS t
    WHERE t.ledger_account_id IN (NEW.from_account_id, NEW.to_account_id);

    -- Two accounts (each a trust account's, once) of one trust bank account: its sum is as it was.
    IF NEW.from_account_id <> NEW.to_account_id AND v_mapped = 2
            AND cardinality(v_banks) = 1 THEN
        RETURN NULL;
    END IF;

    FOREACH v_bank IN ARRAY coalesce(v_banks, '{}'::uuid[]) LOOP
        SELECT sum(a.balance) INTO v_off
        FROM trust_ledger_accounts AS t
        JOIN pgledger_accounts AS a ON a.id = t.ledger_account_id
        WHERE t.bank_account_id = v_bank;

        IF v_off <> 0 THEN
            RAISE EXCEPTION 'trust: trust bank account % does not tie out (off by %)',
                v_bank, v_off
                USING ERRCODE = 'check_violation',
                      HINT = 'Money held in one trust bank account moves to another only with '
                             'its cash: post both transfers in one transaction.';
        END IF;
    END LOOP;
    RETURN NULL;
END;
$$;

-- migrate:down
CREATE OR REPLACE FUNCTION trust_check_bank_tie_out() RETURNS trigger
LANGUAGE plpgsql
SET search_path = public, pg_temp
AS $$
DECLARE
    v_bank uuid;
    v_off numeric;
BEGIN
    FOR v_bank IN
        SELECT DISTINCT t.bank_account_id
        FROM trust_ledger_accounts AS t
        WHERE t.ledger_account_id IN (NEW.from_account_id, NEW.to_account_id)
    LOOP
        SELECT sum(a.balance) INTO v_off
        FROM trust_ledger_accounts AS t
        JOIN pgledger_accounts AS a ON a.id = t.ledger_account_id
        WHERE t.bank_account_id = v_bank;

        IF v_off <> 0 THEN
            RAISE EXCEPTION 'trust: trust bank account % does not tie out (off by %)',
                v_bank, v_off
                USING ERRCODE = 'check_violation',
                      HINT = 'Money held in one trust bank account moves to another only with '
                             'its cash: post both transfers in one transaction.';
        END IF;
    END LOOP;
    RETURN NULL;
END;
$$;
