-- migrate:up
-- The company covers an owner's shortfall when a payment bounces (GitHub issue #232,
-- docs/edge-cases.md M-060; the founder, 2026-11-15). A tenant's payment can come back after the
-- owner's money already went out (a draw down to the reserve, then the month's management fee):
-- undoing it would take the owner's property account below zero, which the ledger refuses, so
-- staff could not record a return the bank had already made. As California's trust rules expect
-- of a broker with a trust shortage, the company deposits its own money into trust to cover the
-- owner's shortfall, and the owner owes it back. An owner's balance never goes negative.
--
--   trust_return_shortfall          what each owner's property account a payment paid into
--                                   would be short if it bounced now (read only).
--   trust_company_advances          the company's own money into an owner's property account,
--                                   exactly what the bounce of one returned payment needed,
--                                   once per payment and account, naming the staff member.
--   trust_cover_returned_payment    records them, through trust_post_transfers under the key
--                                   'advance/<the payment's key>': cash into each short account.
--                                   A named staff member whose custom roles allow the rent area
--                                   (a bounce's own area: money comes into trust, nothing is paid
--                                   out, so not the disburse role). At commit the payment's bounce
--                                   is posted and took each account to exactly zero at its lowest.
--   trust_company_advance_repayments  what the owner paid back: from their property account to
--                                   the company's fee account in the same trust bank account,
--                                   never more than is owed, by staff who hold disburse (money
--                                   leaving an owner's account, as trust_take_owner_fee).
--   trust_repay_company_advance     records one, once per request key.
--   trust_company_advance_owed      what an account owes the company back today.
--
-- An advance is never a tenant's payment: no charge, tenant's or housing authority's, is matched
-- to it. All append-only and audited; the AI role reads both tables and calls none of this.

SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '60s';

CREATE TABLE trust_company_advances (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    pmc_id uuid NOT NULL REFERENCES trust_pmcs (pmc_id),
    request_key text NOT NULL CHECK (length(request_key) BETWEEN 1 AND 200),
    -- The returned payment: its postings' key and its first transfer.
    payment_key text NOT NULL CHECK (length(payment_key) BETWEEN 1 AND 200),
    payment_transfer_id text NOT NULL REFERENCES pgledger_transfers (id),
    ledger_account_id text NOT NULL REFERENCES trust_ledger_accounts (ledger_account_id),
    amount numeric NOT NULL CHECK (amount > 0 AND amount = round(amount, 2)),
    transfer_id text NOT NULL UNIQUE REFERENCES pgledger_transfers (id),
    recorded_by uuid NOT NULL,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    UNIQUE (pmc_id, payment_key, ledger_account_id),
    FOREIGN KEY (pmc_id, recorded_by) REFERENCES trust_staff (pmc_id, id)
);
CREATE INDEX trust_company_advances_account ON trust_company_advances (ledger_account_id);
CREATE INDEX trust_company_advances_request ON trust_company_advances (pmc_id, request_key);
CREATE INDEX trust_company_advances_recorded_by ON trust_company_advances (pmc_id, recorded_by);

CREATE TABLE trust_company_advance_repayments (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    pmc_id uuid NOT NULL REFERENCES trust_pmcs (pmc_id),
    request_key text NOT NULL CHECK (length(request_key) BETWEEN 1 AND 200),
    ledger_account_id text NOT NULL REFERENCES trust_ledger_accounts (ledger_account_id),
    amount numeric NOT NULL CHECK (amount > 0 AND amount = round(amount, 2)),
    transfer_id text NOT NULL UNIQUE REFERENCES pgledger_transfers (id),
    recorded_by uuid NOT NULL,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    UNIQUE (pmc_id, request_key),
    FOREIGN KEY (pmc_id, recorded_by) REFERENCES trust_staff (pmc_id, id)
);
CREATE INDEX trust_company_advance_repayments_account
ON trust_company_advance_repayments (ledger_account_id);
CREATE INDEX trust_company_advance_repayments_recorded_by
ON trust_company_advance_repayments (pmc_id, recorded_by);

REVOKE ALL ON trust_company_advances FROM PUBLIC;
REVOKE ALL ON trust_company_advance_repayments FROM PUBLIC;
GRANT SELECT ON trust_company_advances TO trust_app, trust_ai_agent;
GRANT SELECT ON trust_company_advance_repayments TO trust_app, trust_ai_agent;

CREATE TRIGGER trust_append_only
BEFORE UPDATE OR DELETE OR TRUNCATE ON trust_company_advances
FOR EACH STATEMENT EXECUTE FUNCTION trust_refuse_ledger_rewrite();
CREATE TRIGGER trust_audit AFTER INSERT OR UPDATE OR DELETE ON trust_company_advances
FOR EACH ROW EXECUTE FUNCTION trust_audit();
CREATE TRIGGER trust_append_only
BEFORE UPDATE OR DELETE OR TRUNCATE ON trust_company_advance_repayments
FOR EACH STATEMENT EXECUTE FUNCTION trust_refuse_ledger_rewrite();
CREATE TRIGGER trust_audit AFTER INSERT OR UPDATE OR DELETE ON trust_company_advance_repayments
FOR EACH ROW EXECUTE FUNCTION trust_audit();
-- Paying the company back takes money from the owner's account, as a fee does.
CREATE TRIGGER trust_disburse BEFORE INSERT ON trust_company_advance_repayments
FOR EACH ROW EXECUTE FUNCTION trust_check_staff_role('disburse', 'repay the company''s cover');

-- What each owner's property account a tenant's or housing authority's payment paid into would be
-- short if the payment bounced now: what the payment brought it, less what it holds. None for a
-- payment that bounced already.
CREATE FUNCTION trust_return_shortfall(p_pmc_id uuid, p_payment text)
RETURNS TABLE (ledger_account_id text, brought numeric, held numeric, short numeric)
LANGUAGE sql
STABLE
SET search_path = public, pg_temp
AS $$
    WITH payment AS (
        SELECT t.metadata ->> 'key' AS key
        FROM pgledger_transfers AS t
        JOIN trust_ledger_accounts AS a ON a.ledger_account_id = t.to_account_id
        WHERE t.id = p_payment AND a.pmc_id = p_pmc_id
          AND t.metadata ->> 'kind' IN ('tenant payment', 'housing authority payment')
          AND NOT EXISTS (
              SELECT 1 FROM pgledger_transfers AS b
              WHERE b.metadata ->> 'key' = 'bounce/' || (t.metadata ->> 'key')
          )
    )
    SELECT a.ledger_account_id, sum(t.amount), l.balance, greatest(sum(t.amount) - l.balance, 0)
    FROM payment AS p
    JOIN pgledger_transfers AS t ON t.metadata ->> 'key' = p.key
    JOIN trust_ledger_accounts AS a
        ON a.ledger_account_id = t.to_account_id AND a.pmc_id = p_pmc_id
       AND a.kind = 'owner_property'
    JOIN pgledger_accounts AS l ON l.id = a.ledger_account_id
    GROUP BY a.ledger_account_id, l.balance
    ORDER BY a.ledger_account_id COLLATE "C"
$$;
REVOKE ALL ON FUNCTION trust_return_shortfall(uuid, text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION trust_return_shortfall(uuid, text) TO trust_app;

-- What an owner's property account owes the company back today.
CREATE FUNCTION trust_company_advance_owed(p_account text) RETURNS numeric
LANGUAGE sql
STABLE
SET search_path = public, pg_temp
AS $$
    SELECT coalesce((SELECT sum(v.amount) FROM trust_company_advances AS v
                     WHERE v.ledger_account_id = p_account), 0)
         - coalesce((SELECT sum(r.amount) FROM trust_company_advance_repayments AS r
                     WHERE r.ledger_account_id = p_account), 0)
$$;
REVOKE ALL ON FUNCTION trust_company_advance_owed(text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION trust_company_advance_owed(text) TO trust_app;

CREATE FUNCTION trust_cover_returned_payment(
    p_pmc_id uuid,
    p_payment text,
    p_request_key text,
    p_event_at timestamptz
) RETURNS numeric
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
DECLARE
    v_staff uuid := nullif(current_setting('trust.staff_id', true), '')::uuid;
    v_payment pgledger_transfers;
    v_key text;
    v_first text;
    v_earlier text;
    v_short record;
    v_requests transfer_request[] := ARRAY[]::transfer_request[];
    v_cash text;
    v_total numeric := 0;
BEGIN
    IF v_staff IS NULL THEN
        RAISE EXCEPTION 'trust: name the staff member who covers an owner''s shortfall'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    IF NOT trust_staff_may(p_pmc_id, v_staff, 'rent') THEN
        RAISE EXCEPTION 'trust: staff member % may not record rent work, so not a returned '
            'payment''s cover', v_staff
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF coalesce(p_request_key, '') = '' OR length(p_request_key) > 200 THEN
        RAISE EXCEPTION 'trust: a cover needs a request key, so a retry can''t cover twice'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    SELECT t.* INTO v_payment
    FROM pgledger_transfers AS t
    JOIN trust_ledger_accounts AS a ON a.ledger_account_id = t.to_account_id
    WHERE t.id = p_payment AND a.pmc_id = p_pmc_id
      AND t.metadata ->> 'kind' IN ('tenant payment', 'housing authority payment');
    IF NOT FOUND THEN
        RAISE EXCEPTION 'trust: % is not a tenant''s or housing authority''s payment in PMC %',
            p_payment, p_pmc_id
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    IF v_payment.metadata ? 'unapplied_id' THEN
        RAISE EXCEPTION 'trust: % is unapplied cash applied to a lease: it does not bounce',
            p_payment
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    v_key := v_payment.metadata ->> 'key';
    -- Covers of one payment wait for each other.
    PERFORM pg_advisory_xact_lock(hashtextextended('company cover of ' || v_key, 0));

    SELECT t.id INTO v_first
    FROM pgledger_transfers AS t
    WHERE t.metadata ->> 'key' = v_key
    ORDER BY t.created_at, t.id
    LIMIT 1;

    -- A retry: the same request key returns what it advanced; another payment's key is refused.
    SELECT min(v.payment_key) INTO v_earlier
    FROM trust_company_advances AS v
    WHERE v.pmc_id = p_pmc_id AND v.request_key = p_request_key;
    IF v_earlier IS NOT NULL THEN
        IF v_earlier <> v_key THEN
            RAISE EXCEPTION 'trust: request key % already covered another returned payment',
                p_request_key
                USING ERRCODE = 'unique_violation',
                      HINT = 'Use a new key for a new cover; resend a key only to retry.';
        END IF;
        RETURN (SELECT sum(v.amount) FROM trust_company_advances AS v
                WHERE v.pmc_id = p_pmc_id AND v.request_key = p_request_key);
    END IF;
    IF EXISTS (SELECT 1 FROM trust_company_advances AS v
               WHERE v.pmc_id = p_pmc_id AND v.payment_key = v_key) THEN
        RAISE EXCEPTION 'trust: the company already covered the return of %', v_first
            USING ERRCODE = 'unique_violation';
    END IF;
    IF EXISTS (SELECT 1 FROM trust_idempotency_keys AS k
               WHERE k.pmc_id = p_pmc_id AND k.idempotency_key = 'bounce/' || v_key) THEN
        RAISE EXCEPTION 'trust: the payment % bounced already', v_first
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    -- Each owner's account the payment paid into, locked (its ledger row too, so what it holds
    -- holds until this commits), then what it is short.
    PERFORM trust_lock_owner_account(p_pmc_id, s.ledger_account_id)
    FROM trust_return_shortfall(p_pmc_id, p_payment) AS s;
    PERFORM l.id FROM pgledger_accounts AS l
    WHERE l.id IN (SELECT s.ledger_account_id FROM trust_return_shortfall(p_pmc_id, p_payment) AS s)
    ORDER BY l.id
    FOR NO KEY UPDATE;
    FOR v_short IN
        SELECT s.ledger_account_id, s.short, a.bank_account_id
        FROM trust_return_shortfall(p_pmc_id, p_payment) AS s
        JOIN trust_ledger_accounts AS a ON a.ledger_account_id = s.ledger_account_id
        WHERE s.short > 0
        ORDER BY s.ledger_account_id COLLATE "C"
    LOOP
        SELECT c.ledger_account_id INTO v_cash
        FROM trust_ledger_accounts AS c
        WHERE c.bank_account_id = v_short.bank_account_id AND c.kind = 'bank_cash';
        IF NOT FOUND THEN
            RAISE EXCEPTION 'trust: open the cash account (bank_cash) of trust bank account % first',
                v_short.bank_account_id
                USING ERRCODE = 'invalid_parameter_value';
        END IF;
        v_requests := v_requests || (v_cash, v_short.ledger_account_id, v_short.short)::transfer_request;
        v_total := v_total + v_short.short;
    END LOOP;
    IF cardinality(v_requests) = 0 THEN
        RETURN 0;  -- nothing is short: the bounce posts as it is
    END IF;

    -- One posting, one transfer into each short account (each account once).
    INSERT INTO trust_company_advances (
        pmc_id, request_key, payment_key, payment_transfer_id, ledger_account_id, amount,
        transfer_id, recorded_by
    )
    SELECT p_pmc_id, p_request_key, v_key, v_first, p.to_account_id, p.amount, p.id, v_staff
    FROM trust_post_transfers(
        p_pmc_id,
        'advance/' || v_key,
        v_requests,
        p_event_at,
        jsonb_build_object(
            'key', 'advance/' || v_key,
            'kind', 'company advance',
            'payment_key', v_key,
            'request_key', p_request_key,
            'memo', 'Covered by the company for a returned payment (owed back to the company)'
        )
    ) AS p;
    RETURN v_total;
END;
$$;
REVOKE ALL ON FUNCTION trust_cover_returned_payment(uuid, text, text, timestamptz) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION trust_cover_returned_payment(uuid, text, text, timestamptz) TO trust_app;

-- At commit: the advance's payment bounced in the same transaction, and its bounce took the
-- account to exactly zero at its lowest, so the advance was the shortfall, no more and no less.
CREATE FUNCTION trust_check_company_advance() RETURNS trigger
LANGUAGE plpgsql
SET search_path = public, pg_temp
AS $$
DECLARE
    v_low numeric;
BEGIN
    SELECT min(e.account_current_balance) INTO v_low
    FROM pgledger_transfers AS t
    JOIN pgledger_entries AS e ON e.transfer_id = t.id
    WHERE t.metadata ->> 'key' = 'bounce/' || NEW.payment_key
      AND e.account_id = NEW.ledger_account_id;
    IF v_low IS NULL THEN
        RAISE EXCEPTION 'trust: the company covers a returned payment only as it bounces: % '
            'did not bounce', NEW.payment_transfer_id
            USING ERRCODE = 'check_violation';
    END IF;
    IF v_low <> 0 THEN
        RAISE EXCEPTION 'trust: the company''s cover of % into % is not what its bounce needed '
            '(the account held % at its lowest)', NEW.amount, NEW.ledger_account_id, v_low
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NULL;
END;
$$;
REVOKE ALL ON FUNCTION trust_check_company_advance() FROM PUBLIC;
CREATE CONSTRAINT TRIGGER trust_check_company_advance
AFTER INSERT ON trust_company_advances
DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW EXECUTE FUNCTION trust_check_company_advance();

-- The company's advance is never a payment of a charge, a tenant's or a housing authority's.
CREATE FUNCTION trust_refuse_matching_an_advance() RETURNS trigger
LANGUAGE plpgsql
SET search_path = public, pg_temp
AS $$
BEGIN
    IF EXISTS (SELECT 1 FROM trust_company_advances AS v WHERE v.transfer_id = NEW.transfer_id)
    THEN
        RAISE EXCEPTION 'trust: transfer % is the company''s cover of a returned payment, not a '
            'payment', NEW.transfer_id
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    RETURN NEW;
END;
$$;
REVOKE ALL ON FUNCTION trust_refuse_matching_an_advance() FROM PUBLIC;
CREATE TRIGGER trust_not_an_advance BEFORE INSERT ON trust_charge_payments
FOR EACH ROW EXECUTE FUNCTION trust_refuse_matching_an_advance();
CREATE TRIGGER trust_not_an_advance BEFORE INSERT ON trust_assistance_payments
FOR EACH ROW EXECUTE FUNCTION trust_refuse_matching_an_advance();

CREATE FUNCTION trust_repay_company_advance(
    p_pmc_id uuid,
    p_account text,
    p_amount numeric,
    p_request_key text,
    p_event_at timestamptz
) RETURNS text
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
DECLARE
    v_staff uuid := nullif(current_setting('trust.staff_id', true), '')::uuid;
    v_account trust_ledger_accounts;
    v_taken trust_company_advance_repayments;
    v_owed numeric;
    v_income text;
    v_transfer text;
BEGIN
    IF v_staff IS NULL THEN
        RAISE EXCEPTION 'trust: name the staff member who takes the owner''s repayment'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    IF coalesce(p_request_key, '') = '' OR length(p_request_key) > 200 THEN
        RAISE EXCEPTION 'trust: a repayment needs a request key, so a retry can''t take it twice'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    v_account := trust_lock_owner_account(p_pmc_id, p_account);

    SELECT r.* INTO v_taken
    FROM trust_company_advance_repayments AS r
    WHERE r.pmc_id = p_pmc_id AND r.request_key = p_request_key;
    IF FOUND THEN
        IF (v_taken.ledger_account_id, v_taken.amount) IS DISTINCT FROM (p_account, p_amount) THEN
            RAISE EXCEPTION 'trust: request key % already repaid % from %',
                p_request_key, round(v_taken.amount, 2), v_taken.ledger_account_id
                USING ERRCODE = 'unique_violation',
                      HINT = 'Use a new key for a new repayment; resend a key only to retry.';
        END IF;
        RETURN v_taken.transfer_id;  -- a retry: the repayment already taken
    END IF;

    IF p_amount IS NULL OR p_amount <= 0 OR p_amount <> round(p_amount, 2) THEN
        RAISE EXCEPTION 'trust: a repayment is a positive amount in cents, not %', p_amount
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    v_owed := trust_company_advance_owed(p_account);
    IF p_amount > v_owed THEN
        RAISE EXCEPTION 'trust: % is owed back to the company from %; % is too much',
            round(v_owed, 2), p_account, round(p_amount, 2)
            USING ERRCODE = 'check_violation';
    END IF;

    SELECT t.ledger_account_id INTO v_income
    FROM trust_ledger_accounts AS t
    WHERE t.bank_account_id = v_account.bank_account_id AND t.kind = 'pmc_income';
    IF NOT FOUND THEN
        RAISE EXCEPTION 'trust: open the PMC''s fee account (pmc_income) in trust bank account % '
            'first', v_account.bank_account_id
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    SELECT p.id INTO v_transfer
    FROM trust_post_transfers(
        p_pmc_id,
        'advance repaid/' || p_request_key,
        ARRAY[(p_account, v_income, p_amount)::transfer_request],
        p_event_at,
        jsonb_build_object(
            'key', 'advance repaid/' || p_request_key,
            'kind', 'company advance repaid',
            'memo', 'Repaid to the company: its cover of a returned payment'
        )
    ) AS p;
    INSERT INTO trust_company_advance_repayments (
        pmc_id, request_key, ledger_account_id, amount, transfer_id, recorded_by
    ) VALUES (p_pmc_id, p_request_key, p_account, p_amount, v_transfer, v_staff);
    RETURN v_transfer;
END;
$$;
REVOKE ALL ON FUNCTION trust_repay_company_advance(uuid, text, numeric, text, timestamptz)
FROM PUBLIC;
GRANT EXECUTE ON FUNCTION trust_repay_company_advance(uuid, text, numeric, text, timestamptz)
TO trust_app;

-- migrate:down
SET LOCAL lock_timeout = '5s';

-- squawk-ignore ban-drop-function
DROP FUNCTION trust_repay_company_advance(uuid, text, numeric, text, timestamptz);
DROP TRIGGER trust_not_an_advance ON trust_assistance_payments;
DROP TRIGGER trust_not_an_advance ON trust_charge_payments;
-- squawk-ignore ban-drop-function
DROP FUNCTION trust_refuse_matching_an_advance();
-- squawk-ignore ban-drop-function
DROP FUNCTION trust_cover_returned_payment(uuid, text, text, timestamptz);
-- squawk-ignore ban-drop-function
DROP FUNCTION trust_company_advance_owed(text);
-- squawk-ignore ban-drop-function
DROP FUNCTION trust_return_shortfall(uuid, text);
-- squawk-ignore ban-drop-table
DROP TABLE trust_company_advance_repayments;
-- squawk-ignore ban-drop-table
DROP TABLE trust_company_advances;
-- squawk-ignore ban-drop-function
DROP FUNCTION trust_check_company_advance();
