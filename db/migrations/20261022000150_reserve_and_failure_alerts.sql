-- migrate:up
-- Two more emails the PMS writes by itself (the founder, 2026-10-22: q054 and q149;
-- docs/adr/0007-email.md), each only while the server sends email:
--
--   reserve_low   to an owner, when one of their property accounts falls below the reserve its
--                 management agreement keeps (trust_email_settings.reserve_alerts, which a
--                 company turns on in Company setup). Once per drop: the nightly run records
--                 each account's drops below its reserve and its returns to it
--                 (trust_reserve_events), and a drop's email names the drop.
--   admin_alert   to each of the company's admins, at their login's email, when its nightly run
--                 failed, or when an email was given up on after its last attempt failed
--                 (trust_admin_alerts: once per admin, kind and day). No setting: admins hear of
--                 both whenever the server sends email.
--
-- The reserve events say how much an account held against its reserve on a day: audited, and the
-- AI role reads them as it reads the ledger. An alert names a staff member and says what failed,
-- maybe in the database's own words: audited, and never read by the AI role. Emails, as before,
-- are neither. Records: kept for good. Nothing here moves money.

-- On a live database, give up rather than queue behind a long transaction while holding locks.
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '60s';

ALTER TABLE trust_email_settings ADD COLUMN reserve_alerts boolean NOT NULL DEFAULT false;

-- Each owner's property account's drops below its reserve and returns to it, one after the
-- other: the first a drop, then a return, a drop and so on (number 1, 2, 3...).
CREATE TABLE trust_reserve_events (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    pmc_id uuid NOT NULL REFERENCES trust_pmcs (pmc_id),
    ledger_account_id text NOT NULL,
    number bigint NOT NULL CHECK (number > 0),
    kind text NOT NULL CHECK (kind IN ('below', 'back')),
    found_on date NOT NULL,
    -- What the account held and the reserve in force that day (0 with no agreement).
    balance numeric NOT NULL CHECK (balance >= 0 AND balance = round(balance, 2)),
    reserve numeric NOT NULL CHECK (reserve >= 0 AND reserve = round(reserve, 2)),
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    CHECK ((kind = 'below') = (number % 2 = 1)),
    CHECK ((kind = 'below') = (balance < reserve)),
    UNIQUE (ledger_account_id, number),
    UNIQUE (pmc_id, id),
    FOREIGN KEY (pmc_id, ledger_account_id)
    REFERENCES trust_ledger_accounts (pmc_id, ledger_account_id)
);

CREATE FUNCTION trust_check_reserve_event() RETURNS trigger
LANGUAGE plpgsql
SET search_path = public, pg_temp
AS $$
DECLARE
    v_before date;
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM trust_ledger_accounts AS a
        WHERE a.ledger_account_id = NEW.ledger_account_id AND a.kind = 'owner_property'
    ) THEN
        RAISE EXCEPTION 'trust: % is not an owner''s property account', NEW.ledger_account_id
            USING ERRCODE = 'check_violation';
    END IF;
    IF NEW.number > 1 THEN
        SELECT e.found_on INTO v_before FROM trust_reserve_events AS e
        WHERE e.ledger_account_id = NEW.ledger_account_id AND e.number = NEW.number - 1;
        IF v_before IS NULL OR v_before > NEW.found_on THEN
            RAISE EXCEPTION 'trust: event % of % follows no event % on or before %',
                NEW.number, NEW.ledger_account_id, NEW.number - 1,
                to_char(NEW.found_on, 'MM/DD/YYYY')
                USING ERRCODE = 'check_violation';
        END IF;
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER trust_check_reserve_event BEFORE INSERT ON trust_reserve_events
FOR EACH ROW EXECUTE FUNCTION trust_check_reserve_event();
CREATE TRIGGER trust_append_only
BEFORE UPDATE OR DELETE OR TRUNCATE ON trust_reserve_events
FOR EACH STATEMENT EXECUTE FUNCTION trust_refuse_ledger_rewrite();
CREATE TRIGGER trust_audit AFTER INSERT OR UPDATE OR DELETE ON trust_reserve_events
FOR EACH ROW EXECUTE FUNCTION trust_audit();

-- What failed, told to one admin: once per admin, kind and day.
CREATE TABLE trust_admin_alerts (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    pmc_id uuid NOT NULL REFERENCES trust_pmcs (pmc_id),
    staff_id uuid NOT NULL,
    kind text NOT NULL CHECK (kind IN ('nightly_failed', 'deliveries_failing')),
    day date NOT NULL,
    what text NOT NULL CHECK (what ~ '\S' AND length(what) <= 1000),
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    UNIQUE (pmc_id, staff_id, kind, day),
    UNIQUE (pmc_id, id),
    FOREIGN KEY (pmc_id, staff_id) REFERENCES trust_staff (pmc_id, id)
);

CREATE TRIGGER trust_append_only
BEFORE UPDATE OR DELETE OR TRUNCATE ON trust_admin_alerts
FOR EACH STATEMENT EXECUTE FUNCTION trust_refuse_ledger_rewrite();
CREATE TRIGGER trust_audit AFTER INSERT OR UPDATE OR DELETE ON trust_admin_alerts
FOR EACH ROW EXECUTE FUNCTION trust_audit();

REVOKE ALL ON trust_reserve_events FROM PUBLIC;
REVOKE ALL ON trust_admin_alerts FROM PUBLIC;
GRANT SELECT, INSERT ON trust_reserve_events TO trust_app;
GRANT SELECT, INSERT ON trust_admin_alerts TO trust_app;
GRANT SELECT ON trust_reserve_events TO trust_ai_agent;
REVOKE ALL ON FUNCTION trust_check_reserve_event() FROM PUBLIC;

-- The emails. The kinds allowed so far are read from the check itself, not written out again
-- here, so a migration that added a kind before this one keeps it. Each new kind holds the
-- recipient check's last branch (no tenant, vendor or staff member named on the email): an
-- owner's names the owner and the drop, an admin's names the alert, which names the admin.
ALTER TABLE trust_emails ADD COLUMN reserve_event_id uuid;
ALTER TABLE trust_emails ADD COLUMN alert_id uuid;
DO $$
DECLARE
    v_kinds text;
BEGIN
    SELECT string_agg(quote_literal(k.kind), ', ' ORDER BY k.n) INTO v_kinds
    FROM (
        SELECT m[1] AS kind, n
        FROM pg_constraint AS c,
             regexp_matches(pg_get_constraintdef(c.oid), '''([a-z_]+)''::text', 'g')
                 WITH ORDINALITY AS r (m, n)
        WHERE c.conrelid = 'trust_emails'::regclass AND c.conname = 'trust_emails_kind_check'
        UNION ALL
        SELECT kind, 1000 + n FROM unnest(ARRAY['reserve_low', 'admin_alert'])
            WITH ORDINALITY AS r (kind, n)
    ) AS k;
    -- Replaced at once by a check that allows two more kinds; no guarantee is lost.
    ALTER TABLE trust_emails DROP CONSTRAINT trust_emails_kind_check;
    EXECUTE format(
        'ALTER TABLE trust_emails ADD CONSTRAINT trust_emails_kind_check'
        ' CHECK (kind IN (%s)) NOT VALID', v_kinds
    );
END;
$$;
-- Every email so far names neither, so it holds this check; it is added NOT VALID, checked for
-- every new email.
ALTER TABLE trust_emails ADD CONSTRAINT trust_emails_alert_check CHECK (
    (kind = 'reserve_low') = (reserve_event_id IS NOT NULL)
    AND (kind = 'reserve_low') <= (owner_id IS NOT NULL)
    AND (kind = 'admin_alert') = (alert_id IS NOT NULL)
    AND (kind = 'admin_alert') <= (owner_id IS NULL)
) NOT VALID;
-- squawk-ignore adding-foreign-key-constraint
ALTER TABLE trust_emails ADD CONSTRAINT trust_emails_pmc_id_reserve_event_id_fkey
FOREIGN KEY (pmc_id, reserve_event_id) REFERENCES trust_reserve_events (pmc_id, id) NOT VALID;
-- squawk-ignore adding-foreign-key-constraint
ALTER TABLE trust_emails ADD CONSTRAINT trust_emails_pmc_id_alert_id_fkey
FOREIGN KEY (pmc_id, alert_id) REFERENCES trust_admin_alerts (pmc_id, id) NOT VALID;
-- Each once. The outbox holds neither kind yet, so these build in moments; CONCURRENTLY can't
-- run inside the migration's transaction.
-- squawk-ignore require-concurrent-index-creation
CREATE UNIQUE INDEX trust_emails_one_reserve_low ON trust_emails (reserve_event_id)
WHERE kind = 'reserve_low';
-- squawk-ignore require-concurrent-index-creation
CREATE UNIQUE INDEX trust_emails_one_admin_alert ON trust_emails (alert_id)
WHERE kind = 'admin_alert';

-- migrate:down
DO $$
BEGIN
    RAISE EXCEPTION 'reserve_and_failure_alerts cannot be rolled back: emails and alerts are '
        'records';
END;
$$;
