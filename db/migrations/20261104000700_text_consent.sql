-- migrate:up
-- A phone number at sign-up (the founder's review of 11/03, item 14): a tenant or an owner setting
-- the password of their portal login gives their phone, stored as E.164, and says whether the
-- company may text them, the box unchecked unless they check it. Each answer is kept with the
-- exact words they were shown and when, the record a texting provider's registration (A2P 10DLC)
-- asks for. The PMS sends no text. Append-only, the latest row of a person holding; the phone
-- also becomes their contact phone. The AI role never reads it, as it never reads contact details.

-- On a live database, give up rather than queue behind a long transaction while holding locks.
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '60s';

CREATE TABLE trust_text_consents (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    pmc_id uuid NOT NULL REFERENCES trust_pmcs (pmc_id),
    -- The portal login whose password was set as they answered.
    login_id uuid NOT NULL REFERENCES trust_logins (id),
    tenant_id uuid,
    owner_id uuid,
    phone text NOT NULL CHECK (phone ~ '^\+[1-9][0-9]{7,14}$'),
    texts_ok boolean NOT NULL,
    -- The words beside the box, as shown.
    wording text NOT NULL CHECK (wording ~ '\S' AND length(wording) <= 1000),
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    CHECK (num_nonnulls(tenant_id, owner_id) = 1),
    FOREIGN KEY (pmc_id, tenant_id) REFERENCES trust_tenants (pmc_id, id),
    FOREIGN KEY (pmc_id, owner_id) REFERENCES trust_owners (pmc_id, id)
);
CREATE INDEX trust_text_consents_by_tenant ON trust_text_consents (tenant_id, created_at);
CREATE INDEX trust_text_consents_by_owner ON trust_text_consents (owner_id, created_at);
CREATE INDEX trust_text_consents_by_login ON trust_text_consents (login_id);
CREATE INDEX trust_text_consents_by_pmc ON trust_text_consents (pmc_id, created_at);

-- An answer is the login's own person's: a tenant login's tenant, an owner login's owner.
CREATE FUNCTION trust_check_text_consent() RETURNS trigger
LANGUAGE plpgsql
SET search_path = public, pg_temp
AS $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM trust_logins AS l
        WHERE l.id = NEW.login_id AND l.pmc_id = NEW.pmc_id
          AND l.tenant_id IS NOT DISTINCT FROM NEW.tenant_id
          AND l.owner_id IS NOT DISTINCT FROM NEW.owner_id
    ) THEN
        RAISE EXCEPTION 'trust: login % is not this person''s', NEW.login_id
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$;
REVOKE ALL ON FUNCTION trust_check_text_consent() FROM PUBLIC;
CREATE TRIGGER trust_check_text_consent BEFORE INSERT ON trust_text_consents
FOR EACH ROW EXECUTE FUNCTION trust_check_text_consent();

CREATE TRIGGER trust_append_only
BEFORE UPDATE OR DELETE OR TRUNCATE ON trust_text_consents
FOR EACH STATEMENT EXECUTE FUNCTION trust_refuse_ledger_rewrite();
CREATE TRIGGER trust_audit AFTER INSERT OR UPDATE OR DELETE ON trust_text_consents
FOR EACH ROW EXECUTE FUNCTION trust_audit();

REVOKE ALL ON trust_text_consents FROM PUBLIC;
GRANT SELECT, INSERT ON trust_text_consents TO trust_app;

-- migrate:down
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '60s';
-- What people agreed to, and when, is their record: undoing it would lose it.
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM trust_text_consents) THEN
        RAISE EXCEPTION 'refusing to undo text consent: people have answered';
    END IF;
END;
$$;
-- squawk-ignore ban-drop-table
DROP TABLE trust_text_consents;
-- squawk-ignore ban-drop-function
DROP FUNCTION trust_check_text_consent();
