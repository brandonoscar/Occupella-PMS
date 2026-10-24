-- migrate:up
-- Rent paid online (ADR 0009, lane 1): a tenant pays from the portal through the server's payments
-- partner (src/pms/payments.py, PMS_PAYMENTS: "stripe" with the company's own keys, "fake" for
-- development and CI, none by default), by bank (ACH) for now.
--
--   trust_payment_settings        a company's choices, append-only, the latest holding, naming who
--                                 changed them: whether its tenants may pay online, and who pays the
--                                 partner's fee on a bank payment (the founder, 2026-10-23: the
--                                 company chooses): the company, from its fees held in trust, or the
--                                 tenant, a fee shown before paying.
--   trust_online_payments         each payment a tenant started: its lease, the tenant, what goes to
--                                 the lease, the tenant's fee, who paid the partner's fee then, the
--                                 partner and the partner's own reference.
--   trust_online_payment_events   what the partner reported, each kind once a payment: paid (with
--                                 the partner's fee), failed, returned after it was paid, or
--                                 expired (the tenant left its page without paying).
--
-- The money moves through the existing write paths (src/pms/api/online_payments.py): a paid
-- payment is recorded as staff record one (trust_post_transfers, trust_apply_payment), the tenant's
-- fee goes to the company's fee account, and the partner's fee leaves trust from that account once
-- it holds enough (never from an owner's money); a return is recorded as a bounce. The AI role reads
-- these tables: amounts, leases and the partner's references, no account number.

-- On a live database, give up rather than queue behind a long transaction while holding locks.
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '60s';

CREATE TABLE trust_payment_settings (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    pmc_id uuid NOT NULL REFERENCES trust_pmcs (pmc_id),
    online boolean NOT NULL,
    ach_fee_payer text NOT NULL CHECK (ach_fee_payer IN ('company', 'tenant')),
    recorded_by uuid,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    FOREIGN KEY (pmc_id, recorded_by) REFERENCES trust_staff (pmc_id, id)
);
CREATE INDEX trust_payment_settings_pmc ON trust_payment_settings (pmc_id, created_at);
CREATE INDEX trust_payment_settings_recorded_by ON trust_payment_settings (pmc_id, recorded_by);

CREATE TABLE trust_online_payments (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    pmc_id uuid NOT NULL REFERENCES trust_pmcs (pmc_id),
    lease_id uuid NOT NULL,
    tenant_id uuid NOT NULL,
    -- What goes to the lease, as a payment staff record would.
    amount numeric NOT NULL CHECK (amount > 0 AND amount <= 100000 AND amount = round(amount, 2)),
    -- What the tenant pays on top for the partner's fee: none unless the tenant pays it.
    tenant_fee numeric NOT NULL DEFAULT 0
        CHECK (tenant_fee >= 0 AND tenant_fee <= 1000 AND tenant_fee = round(tenant_fee, 2)),
    fee_payer text NOT NULL CHECK (fee_payer IN ('company', 'tenant')),
    method text NOT NULL CHECK (method = 'ach'),
    partner text NOT NULL CHECK (partner IN ('fake', 'stripe')),
    partner_ref text NOT NULL CHECK (length(partner_ref) BETWEEN 1 AND 200),
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    CHECK (fee_payer = 'tenant' OR tenant_fee = 0),
    UNIQUE (partner, partner_ref),
    UNIQUE (pmc_id, id),
    FOREIGN KEY (pmc_id, lease_id) REFERENCES trust_leases (pmc_id, id),
    FOREIGN KEY (lease_id, tenant_id) REFERENCES trust_lease_tenants (lease_id, tenant_id)
);
CREATE INDEX trust_online_payments_lease ON trust_online_payments (pmc_id, lease_id, created_at);
CREATE INDEX trust_online_payments_tenant ON trust_online_payments (lease_id, tenant_id);

CREATE TABLE trust_online_payment_events (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    pmc_id uuid NOT NULL,
    payment_id uuid NOT NULL,
    kind text NOT NULL CHECK (kind IN ('paid', 'failed', 'returned', 'expired')),
    -- The partner's own id of what it reported.
    partner_event text NOT NULL CHECK (length(partner_event) BETWEEN 1 AND 200),
    -- The partner's fee for a paid payment, which it keeps out of what it pays into trust.
    partner_fee numeric
        CHECK (partner_fee >= 0 AND partner_fee <= 1000 AND partner_fee = round(partner_fee, 2)),
    happened_at timestamptz NOT NULL,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    CHECK ((kind = 'paid') = (partner_fee IS NOT NULL)),
    UNIQUE (payment_id, kind),
    FOREIGN KEY (pmc_id, payment_id) REFERENCES trust_online_payments (pmc_id, id)
);

-- A payment is paid or it failed, never both, and only a paid one comes back. One paid or failed
-- doesn't expire; a report that the bank paid or failed counts even after one that it expired, as
-- the money did move. Takes a lock on the payment until commit, so two reports about one payment
-- wait for each other.
CREATE FUNCTION trust_check_online_payment_event() RETURNS trigger
LANGUAGE plpgsql
SET search_path = public, pg_temp
AS $$
BEGIN
    PERFORM pg_advisory_xact_lock(
        hashtext('trust_online_payment_events'), hashtext(NEW.payment_id::text)
    );
    IF NEW.kind = 'paid' AND EXISTS (
        SELECT e.id FROM trust_online_payment_events AS e
        WHERE e.payment_id = NEW.payment_id AND e.kind = 'failed'
    ) THEN
        RAISE EXCEPTION 'trust: online payment % failed; it is not paid too', NEW.payment_id
            USING ERRCODE = 'check_violation';
    END IF;
    IF NEW.kind = 'failed' AND EXISTS (
        SELECT e.id FROM trust_online_payment_events AS e
        WHERE e.payment_id = NEW.payment_id AND e.kind = 'paid'
    ) THEN
        RAISE EXCEPTION 'trust: online payment % was paid; a payment paid comes back, it does not '
            'fail', NEW.payment_id
            USING ERRCODE = 'check_violation';
    END IF;
    IF NEW.kind = 'expired' AND EXISTS (
        SELECT e.id FROM trust_online_payment_events AS e
        WHERE e.payment_id = NEW.payment_id AND e.kind IN ('paid', 'failed')
    ) THEN
        RAISE EXCEPTION 'trust: online payment % reached the bank; it did not expire',
            NEW.payment_id
            USING ERRCODE = 'check_violation';
    END IF;
    IF NEW.kind = 'returned' AND NOT EXISTS (
        SELECT e.id FROM trust_online_payment_events AS e
        WHERE e.payment_id = NEW.payment_id AND e.kind = 'paid'
    ) THEN
        RAISE EXCEPTION 'trust: online payment % was not paid, so it can''t come back',
            NEW.payment_id
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$;
REVOKE ALL ON FUNCTION trust_check_online_payment_event() FROM PUBLIC;

CREATE TRIGGER trust_check_online_payment_event
BEFORE INSERT ON trust_online_payment_events
FOR EACH ROW EXECUTE FUNCTION trust_check_online_payment_event();

CREATE TRIGGER trust_recorded_by BEFORE INSERT ON trust_payment_settings
FOR EACH ROW EXECUTE FUNCTION trust_name_recorder();

CREATE TRIGGER trust_append_only
BEFORE UPDATE OR DELETE OR TRUNCATE ON trust_payment_settings
FOR EACH STATEMENT EXECUTE FUNCTION trust_refuse_ledger_rewrite();
CREATE TRIGGER trust_append_only
BEFORE UPDATE OR DELETE OR TRUNCATE ON trust_online_payments
FOR EACH STATEMENT EXECUTE FUNCTION trust_refuse_ledger_rewrite();
CREATE TRIGGER trust_append_only
BEFORE UPDATE OR DELETE OR TRUNCATE ON trust_online_payment_events
FOR EACH STATEMENT EXECUTE FUNCTION trust_refuse_ledger_rewrite();

CREATE TRIGGER trust_audit AFTER INSERT OR UPDATE OR DELETE ON trust_payment_settings
FOR EACH ROW EXECUTE FUNCTION trust_audit();
CREATE TRIGGER trust_audit AFTER INSERT OR UPDATE OR DELETE ON trust_online_payments
FOR EACH ROW EXECUTE FUNCTION trust_audit();
CREATE TRIGGER trust_audit AFTER INSERT OR UPDATE OR DELETE ON trust_online_payment_events
FOR EACH ROW EXECUTE FUNCTION trust_audit();

REVOKE ALL ON trust_payment_settings FROM PUBLIC;
REVOKE ALL ON trust_online_payments FROM PUBLIC;
REVOKE ALL ON trust_online_payment_events FROM PUBLIC;
GRANT SELECT, INSERT ON trust_payment_settings TO trust_app;
GRANT SELECT, INSERT ON trust_online_payments TO trust_app;
GRANT SELECT, INSERT ON trust_online_payment_events TO trust_app;
GRANT SELECT ON trust_payment_settings TO trust_ai_agent;
GRANT SELECT ON trust_online_payments TO trust_ai_agent;
GRANT SELECT ON trust_online_payment_events TO trust_ai_agent;

-- Each posting's request key, which trust_post_transfers' callers write into its transfers'
-- metadata: the online payments find their postings by it (the app reads no
-- trust_idempotency_keys), as the rent work does when a request comes again (rent.earlier).
-- squawk-ignore require-concurrent-index-creation
CREATE INDEX trust_transfers_key ON pgledger_transfers ((metadata ->> 'key'));

-- migrate:down
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '60s';

-- A payment online is a record of what a tenant started and what the bank did with it; undoing
-- the tables would leave the money it moved without that record.
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM trust_online_payments) THEN
        RAISE EXCEPTION 'trust: payments were made online; this migration is not rolled back'
            USING ERRCODE = 'object_in_use';
    END IF;
END;
$$;
-- squawk-ignore require-concurrent-index-deletion
DROP INDEX trust_transfers_key;
-- squawk-ignore ban-drop-table
DROP TABLE trust_online_payment_events;
-- squawk-ignore ban-drop-function
DROP FUNCTION trust_check_online_payment_event();
-- squawk-ignore ban-drop-table
DROP TABLE trust_online_payments;
-- squawk-ignore ban-drop-table
DROP TABLE trust_payment_settings;
