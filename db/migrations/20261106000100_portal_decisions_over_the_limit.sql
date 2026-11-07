-- migrate:up
-- An owner decides in the portal only the bills over their approval limit (the AI pen test of
-- 11/06).
--
-- The rule was the API's and the page's: the portal lists a bill within the limit when the account
-- can't cover it (Short), and POST /api/owner/bills/<id>/decline took it anyway. The decline was
-- for good (one decision per bill), the bill stopped holding money back, and the next draw paid
-- that money to the owner. trust_check_bill_decision now refuses an owner's own decision on a
-- bill within the limit of the agreement in force on its date, as trust_set_aside_bill counts it.
-- A decision staff record (migration 20261104000200) or the switching tool carries over names
-- how it was made and is left as it was.

SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '60s';

CREATE OR REPLACE FUNCTION trust_check_bill_decision() RETURNS trigger
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path TO 'public', 'pg_temp'
    AS $$
DECLARE
    v_bill trust_bills;
    v_owner uuid;
    -- Only a decline row says whether it is a void; an approval never is.
    v_void boolean := coalesce((to_jsonb(NEW) ->> 'void')::boolean, false);
    v_was trust_bill_declines;
BEGIN
    SELECT b.* INTO v_bill FROM trust_bills AS b
    WHERE b.id = NEW.bill_id AND b.pmc_id = NEW.pmc_id;
    IF NOT FOUND THEN
        RETURN NEW;  -- the key to trust_bills refuses it
    END IF;
    PERFORM trust_lock_bill(NEW.pmc_id, NEW.bill_id);
    SELECT d.* INTO v_was FROM trust_bill_declines AS d WHERE d.bill_id = NEW.bill_id;
    IF FOUND AND v_was.void THEN
        RAISE EXCEPTION 'trust: bill % is void already', v_bill.reference
            USING ERRCODE = 'unique_violation';
    END IF;
    IF FOUND AND v_void THEN
        RAISE EXCEPTION 'trust: bill % was declined by its owner; it is void already',
            v_bill.reference
            USING ERRCODE = 'unique_violation';
    END IF;
    IF NOT v_void AND (
        FOUND OR EXISTS (SELECT a.bill_id FROM trust_bill_approvals AS a
                         WHERE a.bill_id = NEW.bill_id)
    ) THEN
        RAISE EXCEPTION 'trust: bill % has its owner''s decision already', v_bill.reference
            USING ERRCODE = 'unique_violation';
    END IF;
    IF TG_TABLE_NAME = 'trust_bill_declines' AND EXISTS (
        SELECT p.bill_id FROM trust_bill_payments AS p
        WHERE p.bill_id = NEW.bill_id AND p.step = 'set_aside'
    ) THEN
        IF v_void THEN
            RAISE EXCEPTION 'trust: money was taken from the owner''s account for bill % '
                'already; it can''t be voided', v_bill.reference
                USING ERRCODE = 'check_violation';
        END IF;
        RAISE EXCEPTION 'trust: money was taken from the owner''s account for bill % '
            'already; it can''t be declined', v_bill.reference
            USING ERRCODE = 'check_violation';
    END IF;
    IF NEW.owner_id IS NOT NULL THEN
        SELECT t.owner_id INTO v_owner
        FROM trust_ledger_accounts AS t WHERE t.ledger_account_id = v_bill.ledger_account_id;
        IF v_owner IS DISTINCT FROM NEW.owner_id THEN
            RAISE EXCEPTION 'trust: only the owner of bill %''s property account decides it in '
                'the portal', v_bill.reference
                USING ERRCODE = 'check_violation';
        END IF;
        -- In the portal an owner decides the bills over their approval limit, no others: one
        -- within it is paid without them, and a decline would stop it for good (the AI pen test
        -- of 11/06). A decision staff record, or one the switching tool carries over, says how
        -- it was made (`method`) and keeps its own rules.
        IF (to_jsonb(NEW) ->> 'method') IS NULL AND v_bill.amount <= coalesce(
            (trust_agreement_on(v_bill.ledger_account_id, v_bill.bill_date)).approval_limit, 0
        ) THEN
            RAISE EXCEPTION 'trust: bill % is %, within its owner''s approval limit; it is paid '
                'without the owner''s decision', v_bill.reference, v_bill.amount
                USING ERRCODE = 'check_violation';
        END IF;
    END IF;
    RETURN NEW;
END;
$$;


-- migrate:down
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '60s';

CREATE OR REPLACE FUNCTION trust_check_bill_decision() RETURNS trigger
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path TO 'public', 'pg_temp'
    AS $$
DECLARE
    v_bill trust_bills;
    v_owner uuid;
    -- Only a decline row says whether it is a void; an approval never is.
    v_void boolean := coalesce((to_jsonb(NEW) ->> 'void')::boolean, false);
    v_was trust_bill_declines;
BEGIN
    SELECT b.* INTO v_bill FROM trust_bills AS b
    WHERE b.id = NEW.bill_id AND b.pmc_id = NEW.pmc_id;
    IF NOT FOUND THEN
        RETURN NEW;  -- the key to trust_bills refuses it
    END IF;
    PERFORM trust_lock_bill(NEW.pmc_id, NEW.bill_id);
    SELECT d.* INTO v_was FROM trust_bill_declines AS d WHERE d.bill_id = NEW.bill_id;
    IF FOUND AND v_was.void THEN
        RAISE EXCEPTION 'trust: bill % is void already', v_bill.reference
            USING ERRCODE = 'unique_violation';
    END IF;
    IF FOUND AND v_void THEN
        RAISE EXCEPTION 'trust: bill % was declined by its owner; it is void already',
            v_bill.reference
            USING ERRCODE = 'unique_violation';
    END IF;
    IF NOT v_void AND (
        FOUND OR EXISTS (SELECT a.bill_id FROM trust_bill_approvals AS a
                         WHERE a.bill_id = NEW.bill_id)
    ) THEN
        RAISE EXCEPTION 'trust: bill % has its owner''s decision already', v_bill.reference
            USING ERRCODE = 'unique_violation';
    END IF;
    IF TG_TABLE_NAME = 'trust_bill_declines' AND EXISTS (
        SELECT p.bill_id FROM trust_bill_payments AS p
        WHERE p.bill_id = NEW.bill_id AND p.step = 'set_aside'
    ) THEN
        IF v_void THEN
            RAISE EXCEPTION 'trust: money was taken from the owner''s account for bill % '
                'already; it can''t be voided', v_bill.reference
                USING ERRCODE = 'check_violation';
        END IF;
        RAISE EXCEPTION 'trust: money was taken from the owner''s account for bill % '
            'already; it can''t be declined', v_bill.reference
            USING ERRCODE = 'check_violation';
    END IF;
    IF NEW.owner_id IS NOT NULL THEN
        SELECT t.owner_id INTO v_owner
        FROM trust_ledger_accounts AS t WHERE t.ledger_account_id = v_bill.ledger_account_id;
        IF v_owner IS DISTINCT FROM NEW.owner_id THEN
            RAISE EXCEPTION 'trust: only the owner of bill %''s property account decides it in '
                'the portal', v_bill.reference
                USING ERRCODE = 'check_violation';
        END IF;
    END IF;
    RETURN NEW;
END;
$$;
