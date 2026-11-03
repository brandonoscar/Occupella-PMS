-- migrate:up
-- Owners pay in online (ADR 0009, lane 1; readiness q054): an owner adds money to one of their
-- property accounts from the owner portal, by bank (ACH), through the server's payments partner
-- (src/pms/payments.py), as a tenant pays rent online (migration 0150).
--
--   trust_owner_online_payments        each payment an owner started: the owner's property account
--                                      it goes into, the amount, the partner and its reference.
--   trust_owner_online_payment_events  what the partner reported, each kind once a payment: paid
--                                      (with the partner's fee), failed, returned after it was
--                                      paid, or expired (the owner left its page without paying).
--
-- The money moves through the existing write paths (src/pms/api/owner_payments.py): a paid payment
-- is the owner's contribution (trust_record_owner_contribution, under a key made from the payment),
-- and the partner's fee leaves trust from the company's fee account in the same trust bank account
-- once it holds enough: the company always pays it (the founder, 2026-11-02), never the owner and
-- never out of the owner's money (ADR 0009 rule 1). A return takes the contribution back out of
-- the owner's account through its cash. The AI role reads these tables: amounts, accounts and the
-- partner's references, no account number.

-- On a live database, give up rather than queue behind a long transaction while holding locks.
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '60s';

CREATE TABLE trust_owner_online_payments (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    pmc_id uuid NOT NULL REFERENCES trust_pmcs (pmc_id),
    -- The owner's property account it goes into (kind owner_property; a trigger checks it).
    ledger_account_id text NOT NULL,
    amount numeric NOT NULL CHECK (amount > 0 AND amount <= 100000 AND amount = round(amount, 2)),
    method text NOT NULL CHECK (method = 'ach'),
    partner text NOT NULL CHECK (partner IN ('fake', 'stripe')),
    partner_ref text NOT NULL CHECK (length(partner_ref) BETWEEN 1 AND 200),
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    UNIQUE (partner, partner_ref),
    UNIQUE (pmc_id, id),
    FOREIGN KEY (pmc_id, ledger_account_id)
        REFERENCES trust_ledger_accounts (pmc_id, ledger_account_id)
);
CREATE INDEX trust_owner_online_payments_account
ON trust_owner_online_payments (pmc_id, ledger_account_id, created_at);

CREATE TABLE trust_owner_online_payment_events (
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
    FOREIGN KEY (pmc_id, payment_id) REFERENCES trust_owner_online_payments (pmc_id, id)
);

-- An owner pays only into an owner's property account: money held for them for a property.
CREATE FUNCTION trust_check_owner_online_payment() RETURNS trigger
LANGUAGE plpgsql
SET search_path = public, pg_temp
AS $$
BEGIN
    IF NOT EXISTS (
        SELECT a.ledger_account_id FROM trust_ledger_accounts AS a
        WHERE a.ledger_account_id = NEW.ledger_account_id AND a.kind = 'owner_property'
    ) THEN
        RAISE EXCEPTION 'trust: % is not an owner''s property account; an owner pays only into one',
            NEW.ledger_account_id
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$;
REVOKE ALL ON FUNCTION trust_check_owner_online_payment() FROM PUBLIC;

CREATE TRIGGER trust_check_owner_online_payment
BEFORE INSERT ON trust_owner_online_payments
FOR EACH ROW EXECUTE FUNCTION trust_check_owner_online_payment();

-- As a tenant's payment online: paid or failed, never both, and only a paid one comes back. One
-- paid or failed doesn't expire; a report that the bank paid or failed counts even after one that
-- it expired, as the money did move. Takes a lock on the payment until commit, so two reports
-- about one payment (and the app's recording of it, which takes the same lock) wait for each
-- other.
CREATE FUNCTION trust_check_owner_online_payment_event() RETURNS trigger
LANGUAGE plpgsql
SET search_path = public, pg_temp
AS $$
BEGIN
    PERFORM pg_advisory_xact_lock(
        hashtext('trust_owner_online_payment_events'), hashtext(NEW.payment_id::text)
    );
    IF NEW.kind = 'paid' AND EXISTS (
        SELECT e.id FROM trust_owner_online_payment_events AS e
        WHERE e.payment_id = NEW.payment_id AND e.kind = 'failed'
    ) THEN
        RAISE EXCEPTION 'trust: owner payment % failed; it is not paid too', NEW.payment_id
            USING ERRCODE = 'check_violation';
    END IF;
    IF NEW.kind = 'failed' AND EXISTS (
        SELECT e.id FROM trust_owner_online_payment_events AS e
        WHERE e.payment_id = NEW.payment_id AND e.kind = 'paid'
    ) THEN
        RAISE EXCEPTION 'trust: owner payment % was paid; a payment paid comes back, it does not '
            'fail', NEW.payment_id
            USING ERRCODE = 'check_violation';
    END IF;
    IF NEW.kind = 'expired' AND EXISTS (
        SELECT e.id FROM trust_owner_online_payment_events AS e
        WHERE e.payment_id = NEW.payment_id AND e.kind IN ('paid', 'failed')
    ) THEN
        RAISE EXCEPTION 'trust: owner payment % reached the bank; it did not expire',
            NEW.payment_id
            USING ERRCODE = 'check_violation';
    END IF;
    IF NEW.kind = 'returned' AND NOT EXISTS (
        SELECT e.id FROM trust_owner_online_payment_events AS e
        WHERE e.payment_id = NEW.payment_id AND e.kind = 'paid'
    ) THEN
        RAISE EXCEPTION 'trust: owner payment % was not paid, so it can''t come back',
            NEW.payment_id
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$;
REVOKE ALL ON FUNCTION trust_check_owner_online_payment_event() FROM PUBLIC;

CREATE TRIGGER trust_check_owner_online_payment_event
BEFORE INSERT ON trust_owner_online_payment_events
FOR EACH ROW EXECUTE FUNCTION trust_check_owner_online_payment_event();

CREATE TRIGGER trust_append_only
BEFORE UPDATE OR DELETE OR TRUNCATE ON trust_owner_online_payments
FOR EACH STATEMENT EXECUTE FUNCTION trust_refuse_ledger_rewrite();
CREATE TRIGGER trust_append_only
BEFORE UPDATE OR DELETE OR TRUNCATE ON trust_owner_online_payment_events
FOR EACH STATEMENT EXECUTE FUNCTION trust_refuse_ledger_rewrite();

CREATE TRIGGER trust_audit AFTER INSERT OR UPDATE OR DELETE ON trust_owner_online_payments
FOR EACH ROW EXECUTE FUNCTION trust_audit();
CREATE TRIGGER trust_audit AFTER INSERT OR UPDATE OR DELETE ON trust_owner_online_payment_events
FOR EACH ROW EXECUTE FUNCTION trust_audit();

REVOKE ALL ON trust_owner_online_payments FROM PUBLIC;
REVOKE ALL ON trust_owner_online_payment_events FROM PUBLIC;
GRANT SELECT, INSERT ON trust_owner_online_payments TO trust_app;
GRANT SELECT, INSERT ON trust_owner_online_payment_events TO trust_app;
GRANT SELECT ON trust_owner_online_payments TO trust_ai_agent;
GRANT SELECT ON trust_owner_online_payment_events TO trust_ai_agent;

-- migrate:down
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '60s';

-- A payment online is a record of what an owner started and what the bank did with it; undoing
-- the tables would leave the money it moved without that record.
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM trust_owner_online_payments) THEN
        RAISE EXCEPTION 'trust: owners paid in online; this migration is not rolled back'
            USING ERRCODE = 'object_in_use';
    END IF;
END;
$$;
-- squawk-ignore ban-drop-table
DROP TABLE trust_owner_online_payment_events;
-- squawk-ignore ban-drop-function
DROP FUNCTION trust_check_owner_online_payment_event();
-- squawk-ignore ban-drop-table
DROP TABLE trust_owner_online_payments;
-- squawk-ignore ban-drop-function
DROP FUNCTION trust_check_owner_online_payment();
