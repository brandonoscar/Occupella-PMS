-- migrate:up
-- The day an autopay's bank account was saved is the company's (docs/edge-cases.md D-094, D-006).
-- trust_check_autopay_run read it as saved_at::date: the date in the database session's time
-- zone. Python (src/pms/autopay.py) read UTC's. A New York tenant who saved an account at 9 pm on
-- the due day (UTC's next day) paid from next month, while one who saved it at 6 pm paid this
-- month; and on a server whose session zone is east of UTC the check was stricter than Python's,
-- refused the payment and failed the company's whole nightly run. Both now read the company's
-- day (trust_pmc_day, migration 20261104000900). No money moves.

SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '60s';

CREATE OR REPLACE FUNCTION trust_check_autopay_run() RETURNS trigger
LANGUAGE plpgsql
SET search_path = public, pg_temp
AS $$
DECLARE
    v_autopay trust_autopay;
    v_payment trust_online_payments;
BEGIN
    PERFORM pg_advisory_xact_lock(hashtext('trust_autopay_stops'), hashtext(NEW.autopay_id::text));
    SELECT * INTO v_autopay FROM trust_autopay WHERE id = NEW.autopay_id;
    IF EXISTS (SELECT 1 FROM trust_autopay_stops AS s WHERE s.autopay_id = NEW.autopay_id) THEN
        RAISE EXCEPTION 'trust: autopay % is stopped', NEW.autopay_id
            USING ERRCODE = 'check_violation';
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM trust_autopay_accounts AS a
        WHERE a.autopay_id = NEW.autopay_id
            AND trust_pmc_day(NEW.pmc_id, a.saved_at) <= NEW.due_on
    ) THEN
        RAISE EXCEPTION 'trust: autopay % had no bank account saved by %', NEW.autopay_id,
            NEW.due_on
            USING ERRCODE = 'check_violation';
    END IF;
    IF NEW.payment_id IS NOT NULL THEN
        SELECT * INTO v_payment FROM trust_online_payments WHERE id = NEW.payment_id;
        IF v_payment.lease_id IS DISTINCT FROM v_autopay.lease_id
            OR v_payment.tenant_id IS DISTINCT FROM v_autopay.tenant_id
            OR v_payment.partner IS DISTINCT FROM v_autopay.partner
            OR v_payment.amount > v_autopay.amount
            OR (v_autopay.rule = 'fixed' AND v_payment.amount <> v_autopay.amount)
        THEN
            RAISE EXCEPTION 'trust: autopay % pays its own lease, by its own rule', NEW.autopay_id
                USING ERRCODE = 'check_violation';
        END IF;
    END IF;
    RETURN NEW;
END;
$$;
REVOKE ALL ON FUNCTION trust_check_autopay_run() FROM PUBLIC;

-- migrate:down
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '60s';

CREATE OR REPLACE FUNCTION trust_check_autopay_run() RETURNS trigger
LANGUAGE plpgsql
SET search_path = public, pg_temp
AS $$
DECLARE
    v_autopay trust_autopay;
    v_payment trust_online_payments;
BEGIN
    PERFORM pg_advisory_xact_lock(hashtext('trust_autopay_stops'), hashtext(NEW.autopay_id::text));
    SELECT * INTO v_autopay FROM trust_autopay WHERE id = NEW.autopay_id;
    IF EXISTS (SELECT 1 FROM trust_autopay_stops AS s WHERE s.autopay_id = NEW.autopay_id) THEN
        RAISE EXCEPTION 'trust: autopay % is stopped', NEW.autopay_id
            USING ERRCODE = 'check_violation';
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM trust_autopay_accounts AS a
        WHERE a.autopay_id = NEW.autopay_id AND a.saved_at::date <= NEW.due_on
    ) THEN
        RAISE EXCEPTION 'trust: autopay % had no bank account saved by %', NEW.autopay_id,
            NEW.due_on
            USING ERRCODE = 'check_violation';
    END IF;
    IF NEW.payment_id IS NOT NULL THEN
        SELECT * INTO v_payment FROM trust_online_payments WHERE id = NEW.payment_id;
        IF v_payment.lease_id IS DISTINCT FROM v_autopay.lease_id
            OR v_payment.tenant_id IS DISTINCT FROM v_autopay.tenant_id
            OR v_payment.partner IS DISTINCT FROM v_autopay.partner
            OR v_payment.amount > v_autopay.amount
            OR (v_autopay.rule = 'fixed' AND v_payment.amount <> v_autopay.amount)
        THEN
            RAISE EXCEPTION 'trust: autopay % pays its own lease, by its own rule', NEW.autopay_id
                USING ERRCODE = 'check_violation';
        END IF;
    END IF;
    RETURN NEW;
END;
$$;
REVOKE ALL ON FUNCTION trust_check_autopay_run() FROM PUBLIC;
