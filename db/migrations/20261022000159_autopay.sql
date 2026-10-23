-- migrate:up
-- Autopay (the founder, 2026-10-23: tenants paying online, autopay among it). A tenant on a lease
-- saves a card or a bank account with the payment processor for it, and from then on each month,
-- once rent is due, what the lease owes is charged to it by itself: an online payment
-- (migration 0158) the nightly run, or an admin's Run rent now, starts, recorded in trust once
-- the processor says the money came. The tenant or staff stop it at any time.
--
-- trust_autopay: a tenant's autopay of a lease, by card or bank debit, through a processor. Its
-- steps (trust_autopay_steps): started (the processor's reference for saving it), then once saved
-- (what the processor keeps to charge, and how staff and the tenant see it, as "Card ••••4242")
-- or failed; stopped once, after it started, with why and who. A lease has at most one autopay
-- saved and not stopped. trust_autopay_charges: the online payment an autopay started for a due
-- date, once per autopay and date, of its lease, tenant and method. Append-only and audited; the
-- AI role reads none of it.

-- On a live database, give up rather than queue behind a long transaction while holding locks.
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '60s';

CREATE TABLE trust_autopay (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    pmc_id uuid NOT NULL REFERENCES trust_pmcs (pmc_id),
    lease_id uuid NOT NULL,
    tenant_id uuid NOT NULL,
    method text NOT NULL CHECK (method IN ('card', 'bank debit')),
    processor text NOT NULL CHECK (processor IN ('fake', 'stripe')),
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    UNIQUE (pmc_id, id),
    FOREIGN KEY (pmc_id, lease_id) REFERENCES trust_leases (pmc_id, id),
    FOREIGN KEY (lease_id, tenant_id) REFERENCES trust_lease_tenants (lease_id, tenant_id)
);
CREATE INDEX trust_autopay_lease ON trust_autopay (pmc_id, lease_id, created_at);
CREATE INDEX trust_autopay_tenant ON trust_autopay (lease_id, tenant_id);

CREATE TABLE trust_autopay_steps (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    pmc_id uuid NOT NULL REFERENCES trust_pmcs (pmc_id),
    autopay_id uuid NOT NULL,
    step text NOT NULL CHECK (step IN ('started', 'saved', 'failed', 'stopped')),
    -- The processor's: its checkout's reference (started), what it keeps to charge (saved).
    reference text CHECK (char_length(reference) BETWEEN 1 AND 255),
    -- How a saved card or account is shown: "Card ••••4242", "Bank account ••••6789".
    label text CHECK (char_length(label) BETWEEN 1 AND 60),
    processor_event text UNIQUE CHECK (char_length(processor_event) BETWEEN 1 AND 255),
    reason text CHECK (char_length(reason) BETWEEN 1 AND 500),
    recorded_by uuid,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    FOREIGN KEY (pmc_id, autopay_id) REFERENCES trust_autopay (pmc_id, id),
    FOREIGN KEY (pmc_id, recorded_by) REFERENCES trust_staff (pmc_id, id),
    CHECK (
        CASE step
            WHEN 'started' THEN reference IS NOT NULL AND label IS NULL AND reason IS NULL
            WHEN 'saved' THEN reference IS NOT NULL AND label IS NOT NULL AND reason IS NULL
            ELSE reference IS NULL AND label IS NULL AND reason IS NOT NULL
        END
    )
);
CREATE UNIQUE INDEX trust_autopay_steps_one ON trust_autopay_steps (autopay_id, step);
CREATE UNIQUE INDEX trust_autopay_steps_reference
ON trust_autopay_steps (reference) WHERE step = 'started';
CREATE INDEX trust_autopay_steps_recorded_by ON trust_autopay_steps (pmc_id, recorded_by);

CREATE TABLE trust_autopay_charges (
    pmc_id uuid NOT NULL REFERENCES trust_pmcs (pmc_id),
    autopay_id uuid NOT NULL,
    due_on date NOT NULL,
    payment_id uuid NOT NULL UNIQUE,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (autopay_id, due_on),
    FOREIGN KEY (pmc_id, autopay_id) REFERENCES trust_autopay (pmc_id, id),
    FOREIGN KEY (pmc_id, payment_id) REFERENCES trust_online_payments (pmc_id, id)
);
CREATE INDEX trust_autopay_charges_pmc ON trust_autopay_charges (pmc_id, autopay_id);

-- An autopay's steps come in order: started first; then saved or failed, once; stopped once,
-- after it started. Saving waits for the lease and refuses a second autopay saved and not stopped.
CREATE FUNCTION trust_check_autopay_step() RETURNS trigger
LANGUAGE plpgsql
SET search_path = public, pg_temp
AS $$
DECLARE
    v_steps text[];
    v_lease uuid;
BEGIN
    SELECT a.lease_id INTO v_lease FROM trust_autopay AS a
    WHERE a.pmc_id = NEW.pmc_id AND a.id = NEW.autopay_id;
    PERFORM pg_advisory_xact_lock(hashtextextended('autopay of lease ' || v_lease, 0));
    SELECT coalesce(array_agg(s.step), '{}') INTO v_steps
    FROM trust_autopay_steps AS s WHERE s.autopay_id = NEW.autopay_id;
    IF NEW.step = 'started' AND v_steps <> '{}' THEN
        RAISE EXCEPTION 'trust: an autopay starts once' USING ERRCODE = 'check_violation';
    END IF;
    IF NEW.step IN ('saved', 'failed')
       AND (NOT 'started' = ANY (v_steps) OR v_steps && ARRAY['saved', 'failed', 'stopped']) THEN
        RAISE EXCEPTION 'trust: an autopay that started is saved or fails once, before it stops'
            USING ERRCODE = 'check_violation';
    END IF;
    IF NEW.step = 'stopped' AND NOT 'started' = ANY (v_steps) THEN
        RAISE EXCEPTION 'trust: only an autopay that started stops'
            USING ERRCODE = 'check_violation';
    END IF;
    IF NEW.step = 'saved' AND EXISTS (
        SELECT 1 FROM trust_autopay AS a
        WHERE a.lease_id = v_lease AND a.id <> NEW.autopay_id
          AND EXISTS (SELECT 1 FROM trust_autopay_steps AS s
                      WHERE s.autopay_id = a.id AND s.step = 'saved')
          AND NOT EXISTS (SELECT 1 FROM trust_autopay_steps AS s
                          WHERE s.autopay_id = a.id AND s.step = 'stopped')
    ) THEN
        RAISE EXCEPTION 'trust: this lease is on autopay already: stop it first'
            USING ERRCODE = 'check_violation';
    END IF;
    NEW.created_at := clock_timestamp();
    NEW.recorded_by := nullif(current_setting('trust.staff_id', true), '')::uuid;
    RETURN NEW;
END;
$$;
REVOKE ALL ON FUNCTION trust_check_autopay_step() FROM PUBLIC;

-- A charge is an online payment of its autopay's lease, tenant and method, by a saved autopay
-- not stopped.
CREATE FUNCTION trust_check_autopay_charge() RETURNS trigger
LANGUAGE plpgsql
SET search_path = public, pg_temp
AS $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM trust_autopay AS a
        JOIN trust_online_payments AS p
            ON p.pmc_id = a.pmc_id AND p.lease_id = a.lease_id AND p.tenant_id = a.tenant_id
           AND p.method = a.method AND p.processor = a.processor
        WHERE a.pmc_id = NEW.pmc_id AND a.id = NEW.autopay_id AND p.id = NEW.payment_id
          AND EXISTS (SELECT 1 FROM trust_autopay_steps AS s
                      WHERE s.autopay_id = a.id AND s.step = 'saved')
          AND NOT EXISTS (SELECT 1 FROM trust_autopay_steps AS s
                          WHERE s.autopay_id = a.id AND s.step = 'stopped')
    ) THEN
        RAISE EXCEPTION 'trust: an autopay charges its own lease, saved and not stopped'
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$;
REVOKE ALL ON FUNCTION trust_check_autopay_charge() FROM PUBLIC;

CREATE TRIGGER trust_append_only BEFORE UPDATE OR DELETE OR TRUNCATE ON trust_autopay
FOR EACH STATEMENT EXECUTE FUNCTION trust_refuse_ledger_rewrite();
CREATE TRIGGER trust_audit AFTER INSERT OR UPDATE OR DELETE ON trust_autopay
FOR EACH ROW EXECUTE FUNCTION trust_audit();

CREATE TRIGGER trust_check_autopay_step BEFORE INSERT ON trust_autopay_steps
FOR EACH ROW EXECUTE FUNCTION trust_check_autopay_step();
CREATE TRIGGER trust_append_only BEFORE UPDATE OR DELETE OR TRUNCATE ON trust_autopay_steps
FOR EACH STATEMENT EXECUTE FUNCTION trust_refuse_ledger_rewrite();
CREATE TRIGGER trust_audit AFTER INSERT OR UPDATE OR DELETE ON trust_autopay_steps
FOR EACH ROW EXECUTE FUNCTION trust_audit();

CREATE TRIGGER trust_check_autopay_charge BEFORE INSERT ON trust_autopay_charges
FOR EACH ROW EXECUTE FUNCTION trust_check_autopay_charge();
CREATE TRIGGER trust_append_only BEFORE UPDATE OR DELETE OR TRUNCATE ON trust_autopay_charges
FOR EACH STATEMENT EXECUTE FUNCTION trust_refuse_ledger_rewrite();
CREATE TRIGGER trust_audit AFTER INSERT OR UPDATE OR DELETE ON trust_autopay_charges
FOR EACH ROW EXECUTE FUNCTION trust_audit();

REVOKE ALL ON trust_autopay, trust_autopay_steps, trust_autopay_charges FROM PUBLIC;
GRANT SELECT, INSERT ON trust_autopay, trust_autopay_steps, trust_autopay_charges TO trust_app;


-- migrate:down
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '60s';

-- squawk-ignore ban-drop-table
DROP TABLE trust_autopay_charges;
-- squawk-ignore ban-drop-table
DROP TABLE trust_autopay_steps;
-- squawk-ignore ban-drop-table
DROP TABLE trust_autopay;
-- squawk-ignore ban-drop-function
DROP FUNCTION trust_check_autopay_charge();
-- squawk-ignore ban-drop-function
DROP FUNCTION trust_check_autopay_step();
