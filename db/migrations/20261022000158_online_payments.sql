-- migrate:up
-- Rent paid online (the founder, 2026-10-23: tenants paying by card or bank, through Stripe once
-- there are keys, and a stand-in until then). A tenant starts a payment of what their lease owes
-- in the portal; the payment processor (src/pms/payments/processor.py) takes it on its own page;
-- once it says the money came, the PMS records the tenant's payment as staff would, into the
-- owners' accounts in trust (src/pms/api/rent.py). The processor bills its fees to the company,
-- never to the money in trust, and the tenant pays no fee (docs/law.md).
--
-- trust_online_payment_settings: whether a company takes cards and bank debits online,
-- append-only, the latest row holding, naming who. trust_online_payments: a payment a tenant on
-- a lease started, for an amount, by card or bank debit, through a processor. Its steps
-- (trust_online_payment_steps): started (the processor's reference), then once succeeded (with
-- the recorded payment's first transfer and the processor's own payment), failed or refused (the
-- money came but the lease takes no payment, so staff refund it at the processor), and after a
-- success once returned (a bank debit that came back, a dispute lost). Each processor event is
-- taken once. Append-only and audited; the AI role reads none of it.

-- On a live database, give up rather than queue behind a long transaction while holding locks.
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '60s';

CREATE TABLE trust_online_payment_settings (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    pmc_id uuid NOT NULL REFERENCES trust_pmcs (pmc_id),
    cards boolean NOT NULL,
    bank_debits boolean NOT NULL,
    recorded_by uuid,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    FOREIGN KEY (pmc_id, recorded_by) REFERENCES trust_staff (pmc_id, id)
);
CREATE INDEX trust_online_payment_settings_pmc
ON trust_online_payment_settings (pmc_id, created_at);
CREATE INDEX trust_online_payment_settings_recorded_by
ON trust_online_payment_settings (pmc_id, recorded_by);

CREATE TABLE trust_online_payments (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    pmc_id uuid NOT NULL REFERENCES trust_pmcs (pmc_id),
    lease_id uuid NOT NULL,
    tenant_id uuid NOT NULL,
    amount numeric NOT NULL CHECK (amount > 0 AND amount = round(amount, 2)),
    method text NOT NULL CHECK (method IN ('card', 'bank debit')),
    processor text NOT NULL CHECK (processor IN ('fake', 'stripe')),
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    UNIQUE (pmc_id, id),
    FOREIGN KEY (pmc_id, lease_id) REFERENCES trust_leases (pmc_id, id),
    FOREIGN KEY (lease_id, tenant_id) REFERENCES trust_lease_tenants (lease_id, tenant_id)
);
CREATE INDEX trust_online_payments_lease ON trust_online_payments (pmc_id, lease_id, created_at);
CREATE INDEX trust_online_payments_tenant ON trust_online_payments (lease_id, tenant_id);

CREATE TABLE trust_online_payment_steps (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    pmc_id uuid NOT NULL REFERENCES trust_pmcs (pmc_id),
    payment_id uuid NOT NULL,
    step text NOT NULL CHECK (step IN ('started', 'succeeded', 'failed', 'refused', 'returned')),
    -- The processor's: its checkout's reference (started), its own payment (succeeded), and the
    -- event that said so, taken once.
    reference text CHECK (char_length(reference) BETWEEN 1 AND 255),
    processor_event text UNIQUE CHECK (char_length(processor_event) BETWEEN 1 AND 255),
    -- The recorded payment's first transfer (succeeded), or the transfer that moved it back
    -- (returned).
    transfer_id text REFERENCES pgledger_transfers (id),
    -- Why a payment failed or was refused, in words.
    reason text CHECK (char_length(reason) BETWEEN 1 AND 500),
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    FOREIGN KEY (pmc_id, payment_id) REFERENCES trust_online_payments (pmc_id, id),
    CHECK (
        CASE step
            WHEN 'started' THEN reference IS NOT NULL AND transfer_id IS NULL AND reason IS NULL
            WHEN 'succeeded' THEN transfer_id IS NOT NULL AND reason IS NULL
            WHEN 'returned' THEN transfer_id IS NOT NULL AND reason IS NULL
            ELSE transfer_id IS NULL AND reason IS NOT NULL
        END
    )
);
CREATE UNIQUE INDEX trust_online_payment_steps_one ON trust_online_payment_steps (payment_id, step);
-- A checkout's reference names one payment of its processor.
CREATE UNIQUE INDEX trust_online_payment_steps_reference
ON trust_online_payment_steps (reference) WHERE step = 'started';
CREATE INDEX trust_online_payment_steps_transfer ON trust_online_payment_steps (transfer_id);

-- A payment's steps come in order: started first; then one of succeeded, failed or refused; a
-- return only after a success.
CREATE FUNCTION trust_check_online_payment_step() RETURNS trigger
LANGUAGE plpgsql
SET search_path = public, pg_temp
AS $$
DECLARE
    v_steps text[];
BEGIN
    PERFORM pg_advisory_xact_lock(hashtextextended('online payment ' || NEW.payment_id, 0));
    SELECT coalesce(array_agg(s.step), '{}') INTO v_steps
    FROM trust_online_payment_steps AS s WHERE s.payment_id = NEW.payment_id;
    IF NEW.step = 'started' AND v_steps <> '{}' THEN
        RAISE EXCEPTION 'trust: an online payment starts once' USING ERRCODE = 'check_violation';
    END IF;
    IF NEW.step IN ('succeeded', 'failed', 'refused')
       AND (NOT 'started' = ANY (v_steps) OR v_steps && ARRAY['succeeded', 'failed', 'refused']) THEN
        RAISE EXCEPTION 'trust: an online payment that started ends once'
            USING ERRCODE = 'check_violation';
    END IF;
    IF NEW.step = 'returned' AND NOT 'succeeded' = ANY (v_steps) THEN
        RAISE EXCEPTION 'trust: only an online payment that succeeded comes back'
            USING ERRCODE = 'check_violation';
    END IF;
    NEW.created_at := clock_timestamp();
    RETURN NEW;
END;
$$;
REVOKE ALL ON FUNCTION trust_check_online_payment_step() FROM PUBLIC;

CREATE TRIGGER trust_recorded_by BEFORE INSERT ON trust_online_payment_settings
FOR EACH ROW EXECUTE FUNCTION trust_name_recorder();
CREATE TRIGGER trust_append_only
BEFORE UPDATE OR DELETE OR TRUNCATE ON trust_online_payment_settings
FOR EACH STATEMENT EXECUTE FUNCTION trust_refuse_ledger_rewrite();
CREATE TRIGGER trust_audit AFTER INSERT OR UPDATE OR DELETE ON trust_online_payment_settings
FOR EACH ROW EXECUTE FUNCTION trust_audit();

CREATE TRIGGER trust_append_only BEFORE UPDATE OR DELETE OR TRUNCATE ON trust_online_payments
FOR EACH STATEMENT EXECUTE FUNCTION trust_refuse_ledger_rewrite();
CREATE TRIGGER trust_audit AFTER INSERT OR UPDATE OR DELETE ON trust_online_payments
FOR EACH ROW EXECUTE FUNCTION trust_audit();

CREATE TRIGGER trust_check_online_payment_step BEFORE INSERT ON trust_online_payment_steps
FOR EACH ROW EXECUTE FUNCTION trust_check_online_payment_step();
CREATE TRIGGER trust_append_only
BEFORE UPDATE OR DELETE OR TRUNCATE ON trust_online_payment_steps
FOR EACH STATEMENT EXECUTE FUNCTION trust_refuse_ledger_rewrite();
CREATE TRIGGER trust_audit AFTER INSERT OR UPDATE OR DELETE ON trust_online_payment_steps
FOR EACH ROW EXECUTE FUNCTION trust_audit();

REVOKE ALL ON trust_online_payment_settings, trust_online_payments, trust_online_payment_steps
FROM PUBLIC;
GRANT SELECT, INSERT ON trust_online_payment_settings, trust_online_payments,
    trust_online_payment_steps TO trust_app;


-- migrate:down
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '60s';

-- squawk-ignore ban-drop-table
DROP TABLE trust_online_payment_steps;
-- squawk-ignore ban-drop-table
DROP TABLE trust_online_payments;
-- squawk-ignore ban-drop-table
DROP TABLE trust_online_payment_settings;
-- squawk-ignore ban-drop-function
DROP FUNCTION trust_check_online_payment_step();
