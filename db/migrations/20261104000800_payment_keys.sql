-- migrate:up
-- A company's own Stripe keys (the founder's review of 11/03, item 4, and his call of 11/04: the
-- company enters them in Settings, the server's keys from the environment still working when it
-- has none): its secret key and its webhook's signing secret, each sealed by the app under the
-- server's key (src/pms/sealed.py, as tax IDs are), so the database never holds them in the clear
-- and no answer, log or audit row carries them. Only the secret key's last four and whether it is
-- a test or a live key are kept in the clear, to show. Append-only, the latest row holding; a row
-- with no keys takes them away, and the company goes back to the server's. Only an admin changes
-- them, named on the row. The AI role never reads them.

-- On a live database, give up rather than queue behind a long transaction while holding locks.
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '60s';

CREATE TABLE trust_payment_keys (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    pmc_id uuid NOT NULL REFERENCES trust_pmcs (pmc_id),
    -- Sealed (a version byte, a nonce and AES-GCM's ciphertext, the row's id bound in).
    secret_key_sealed bytea CHECK (octet_length(secret_key_sealed) BETWEEN 30 AND 600),
    webhook_secret_sealed bytea CHECK (octet_length(webhook_secret_sealed) BETWEEN 30 AND 600),
    secret_key_last4 text CHECK (secret_key_last4 ~ '^[A-Za-z0-9]{4}$'),
    mode text CHECK (mode IN ('test', 'live')),
    -- The admin who changed them: always named (the command line names nobody, and sets none).
    recorded_by uuid NOT NULL,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    -- Keys, all of them, or none (taken away).
    CHECK (
        num_nulls(secret_key_sealed, webhook_secret_sealed, secret_key_last4, mode) IN (0, 4)
    ),
    FOREIGN KEY (pmc_id, recorded_by) REFERENCES trust_staff (pmc_id, id)
);
CREATE INDEX trust_payment_keys_pmc ON trust_payment_keys (pmc_id, created_at);
CREATE INDEX trust_payment_keys_recorded_by ON trust_payment_keys (pmc_id, recorded_by);

-- Only an admin changes a company's payments keys.
CREATE FUNCTION trust_check_payment_keys() RETURNS trigger
LANGUAGE plpgsql
SET search_path = public, pg_temp
AS $$
BEGIN
    PERFORM trust_require_role(NEW.pmc_id, NEW.recorded_by, 'admin', 'change the payments keys');
    RETURN NEW;
END;
$$;
REVOKE ALL ON FUNCTION trust_check_payment_keys() FROM PUBLIC;

-- Named after trust_recorded_by, so it fires once the row names who changed them.
CREATE TRIGGER trust_recorded_by BEFORE INSERT ON trust_payment_keys
FOR EACH ROW EXECUTE FUNCTION trust_name_recorder();
CREATE TRIGGER trust_set_by_admin BEFORE INSERT ON trust_payment_keys
FOR EACH ROW EXECUTE FUNCTION trust_check_payment_keys();
CREATE TRIGGER trust_append_only
BEFORE UPDATE OR DELETE OR TRUNCATE ON trust_payment_keys
FOR EACH STATEMENT EXECUTE FUNCTION trust_refuse_ledger_rewrite();
CREATE TRIGGER trust_audit AFTER INSERT OR UPDATE OR DELETE ON trust_payment_keys
FOR EACH ROW EXECUTE FUNCTION trust_audit();

REVOKE ALL ON trust_payment_keys FROM PUBLIC;
GRANT SELECT, INSERT ON trust_payment_keys TO trust_app;

-- migrate:down
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '60s';
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM trust_payment_keys) THEN
        RAISE EXCEPTION 'refusing to undo payments keys: a company has entered its own';
    END IF;
END;
$$;
-- squawk-ignore ban-drop-table
DROP TABLE trust_payment_keys;
-- squawk-ignore ban-drop-function
DROP FUNCTION trust_check_payment_keys();
