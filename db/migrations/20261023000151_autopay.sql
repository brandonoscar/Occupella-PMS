-- migrate:up
-- Autopay (ADR 0009, lane 1): a tenant has their lease's rent paid from a bank account they saved
-- at the server's payments partner, each month on the lease's rent due day (src/pms/autopay.py).
--
--   trust_autopay              a tenant's autopay of a lease: a fixed amount each month, or the
--                              balance due, up to an amount they set; the partner it is saved at.
--   trust_autopay_accounts     the bank account it pays from, as the partner saved it: the
--                              partner's references and a label ("STRIPE TEST BANK ••••6789"),
--                              never an account number. Once per autopay.
--   trust_autopay_stops        when an autopay stopped, once: by the tenant, by staff (naming who),
--                              or by itself (the lease ended, the tenant left it, two payments in a
--                              row didn't go through, or the tenant set up a new one).
--   trust_autopay_runs         each due date an autopay came to, once: the payment it started (a row
--                              of trust_online_payments, which records it as any payment online),
--                              or why it started none.
--   trust_autopay_submissions  each autopay payment the partner accepted, with its reference, once.
--
-- No money moves here: a payment reaches the books only when the partner reports it paid, as a
-- payment the tenant started by hand does (migration 0150). The AI role reads what autopay did, never
-- the saved account's references (AI_NEVER_READS).

-- On a live database, give up rather than queue behind a long transaction while holding locks.
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '60s';

CREATE TABLE trust_autopay (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    pmc_id uuid NOT NULL REFERENCES trust_pmcs (pmc_id),
    lease_id uuid NOT NULL,
    tenant_id uuid NOT NULL,
    -- 'fixed': pays `amount` each month. 'balance': pays what the lease owes by its due date, up
    -- to `amount`, so the tenant agreed to the range their bank may be debited.
    rule text NOT NULL CHECK (rule IN ('fixed', 'balance')),
    amount numeric NOT NULL CHECK (amount > 0 AND amount <= 100000 AND amount = round(amount, 2)),
    partner text NOT NULL CHECK (partner IN ('fake', 'stripe')),
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    UNIQUE (pmc_id, id),
    FOREIGN KEY (pmc_id, lease_id) REFERENCES trust_leases (pmc_id, id),
    FOREIGN KEY (lease_id, tenant_id) REFERENCES trust_lease_tenants (lease_id, tenant_id)
);
CREATE INDEX trust_autopay_lease ON trust_autopay (pmc_id, lease_id, created_at);
CREATE INDEX trust_autopay_tenant ON trust_autopay (lease_id, tenant_id);

CREATE TABLE trust_autopay_accounts (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    pmc_id uuid NOT NULL,
    autopay_id uuid NOT NULL UNIQUE,
    -- The partner's customer, saved payment method and mandate (the tenant's authorization to
    -- debit the account), as references only.
    partner_customer text NOT NULL CHECK (length(partner_customer) BETWEEN 1 AND 200),
    partner_method text NOT NULL CHECK (length(partner_method) BETWEEN 1 AND 200),
    partner_mandate text CHECK (length(partner_mandate) BETWEEN 1 AND 200),
    label text NOT NULL CHECK (length(label) BETWEEN 1 AND 80),
    partner_event text NOT NULL CHECK (length(partner_event) BETWEEN 1 AND 200),
    saved_at timestamptz NOT NULL,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    FOREIGN KEY (pmc_id, autopay_id) REFERENCES trust_autopay (pmc_id, id)
);

CREATE TABLE trust_autopay_stops (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    pmc_id uuid NOT NULL,
    autopay_id uuid NOT NULL UNIQUE,
    why text NOT NULL CHECK (
        why IN ('tenant', 'staff', 'lease ended', 'tenant left', 'failed twice', 'replaced')
    ),
    recorded_by uuid,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    -- Staff stop it naming who; nothing else names a staff member.
    CHECK ((why = 'staff') = (recorded_by IS NOT NULL)),
    FOREIGN KEY (pmc_id, autopay_id) REFERENCES trust_autopay (pmc_id, id),
    FOREIGN KEY (pmc_id, recorded_by) REFERENCES trust_staff (pmc_id, id)
);
CREATE INDEX trust_autopay_stops_recorded_by ON trust_autopay_stops (pmc_id, recorded_by);

CREATE TABLE trust_autopay_runs (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    pmc_id uuid NOT NULL,
    autopay_id uuid NOT NULL,
    due_on date NOT NULL,
    payment_id uuid UNIQUE,
    skipped text CHECK (skipped IN ('payment hold', 'nothing owed', 'online payments off')),
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    CHECK (num_nonnulls(payment_id, skipped) = 1),
    UNIQUE (autopay_id, due_on),
    -- The submissions' key to their run.
    UNIQUE (pmc_id, payment_id),
    FOREIGN KEY (pmc_id, autopay_id) REFERENCES trust_autopay (pmc_id, id),
    FOREIGN KEY (pmc_id, payment_id) REFERENCES trust_online_payments (pmc_id, id)
);

CREATE TABLE trust_autopay_submissions (
    payment_id uuid PRIMARY KEY,
    pmc_id uuid NOT NULL,
    partner_ref text NOT NULL CHECK (length(partner_ref) BETWEEN 1 AND 200),
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    FOREIGN KEY (pmc_id, payment_id) REFERENCES trust_autopay_runs (pmc_id, payment_id)
);

-- At most one autopay of a lease and tenant not stopped. Takes a lock on the lease and tenant until
-- commit, so two set up at once wait for each other.
CREATE FUNCTION trust_check_autopay() RETURNS trigger
LANGUAGE plpgsql
SET search_path = public, pg_temp
AS $$
BEGIN
    PERFORM pg_advisory_xact_lock(
        hashtext('trust_autopay'), hashtext(NEW.lease_id::text || '/' || NEW.tenant_id::text)
    );
    IF EXISTS (
        SELECT a.id FROM trust_autopay AS a
        WHERE a.lease_id = NEW.lease_id AND a.tenant_id = NEW.tenant_id
          AND NOT EXISTS (SELECT 1 FROM trust_autopay_stops AS s WHERE s.autopay_id = a.id)
    ) THEN
        RAISE EXCEPTION 'trust: the tenant has an autopay of this lease already; stop it first'
            USING ERRCODE = 'unique_violation';
    END IF;
    RETURN NEW;
END;
$$;
REVOKE ALL ON FUNCTION trust_check_autopay() FROM PUBLIC;

CREATE TRIGGER trust_check_autopay
BEFORE INSERT ON trust_autopay
FOR EACH ROW EXECUTE FUNCTION trust_check_autopay();

-- An autopay starts a payment only while its account is saved and it is not stopped, and only for
-- its own lease and tenant, for no more than its rule allows. Takes the autopay's lock, which a stop
-- takes too, so a payment and a stop wait for each other.
CREATE FUNCTION trust_check_autopay_run() RETURNS trigger
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

CREATE TRIGGER trust_check_autopay_run
BEFORE INSERT ON trust_autopay_runs
FOR EACH ROW EXECUTE FUNCTION trust_check_autopay_run();

-- A stop takes the autopay's lock too (see above).
CREATE FUNCTION trust_lock_autopay_stop() RETURNS trigger
LANGUAGE plpgsql
SET search_path = public, pg_temp
AS $$
BEGIN
    PERFORM pg_advisory_xact_lock(hashtext('trust_autopay_stops'), hashtext(NEW.autopay_id::text));
    RETURN NEW;
END;
$$;
REVOKE ALL ON FUNCTION trust_lock_autopay_stop() FROM PUBLIC;

CREATE TRIGGER trust_lock_autopay_stop
BEFORE INSERT ON trust_autopay_stops
FOR EACH ROW EXECUTE FUNCTION trust_lock_autopay_stop();
CREATE TRIGGER trust_recorded_by BEFORE INSERT ON trust_autopay_stops
FOR EACH ROW EXECUTE FUNCTION trust_name_recorder();

CREATE TRIGGER trust_append_only
BEFORE UPDATE OR DELETE OR TRUNCATE ON trust_autopay
FOR EACH STATEMENT EXECUTE FUNCTION trust_refuse_ledger_rewrite();
CREATE TRIGGER trust_append_only
BEFORE UPDATE OR DELETE OR TRUNCATE ON trust_autopay_accounts
FOR EACH STATEMENT EXECUTE FUNCTION trust_refuse_ledger_rewrite();
CREATE TRIGGER trust_append_only
BEFORE UPDATE OR DELETE OR TRUNCATE ON trust_autopay_stops
FOR EACH STATEMENT EXECUTE FUNCTION trust_refuse_ledger_rewrite();
CREATE TRIGGER trust_append_only
BEFORE UPDATE OR DELETE OR TRUNCATE ON trust_autopay_runs
FOR EACH STATEMENT EXECUTE FUNCTION trust_refuse_ledger_rewrite();
CREATE TRIGGER trust_append_only
BEFORE UPDATE OR DELETE OR TRUNCATE ON trust_autopay_submissions
FOR EACH STATEMENT EXECUTE FUNCTION trust_refuse_ledger_rewrite();

CREATE TRIGGER trust_audit AFTER INSERT OR UPDATE OR DELETE ON trust_autopay
FOR EACH ROW EXECUTE FUNCTION trust_audit();
CREATE TRIGGER trust_audit AFTER INSERT OR UPDATE OR DELETE ON trust_autopay_accounts
FOR EACH ROW EXECUTE FUNCTION trust_audit();
CREATE TRIGGER trust_audit AFTER INSERT OR UPDATE OR DELETE ON trust_autopay_stops
FOR EACH ROW EXECUTE FUNCTION trust_audit();
CREATE TRIGGER trust_audit AFTER INSERT OR UPDATE OR DELETE ON trust_autopay_runs
FOR EACH ROW EXECUTE FUNCTION trust_audit();
CREATE TRIGGER trust_audit AFTER INSERT OR UPDATE OR DELETE ON trust_autopay_submissions
FOR EACH ROW EXECUTE FUNCTION trust_audit();

REVOKE ALL ON trust_autopay FROM PUBLIC;
REVOKE ALL ON trust_autopay_accounts FROM PUBLIC;
REVOKE ALL ON trust_autopay_stops FROM PUBLIC;
REVOKE ALL ON trust_autopay_runs FROM PUBLIC;
REVOKE ALL ON trust_autopay_submissions FROM PUBLIC;
GRANT SELECT, INSERT ON trust_autopay TO trust_app;
GRANT SELECT, INSERT ON trust_autopay_accounts TO trust_app;
GRANT SELECT, INSERT ON trust_autopay_stops TO trust_app;
GRANT SELECT, INSERT ON trust_autopay_runs TO trust_app;
GRANT SELECT, INSERT ON trust_autopay_submissions TO trust_app;
GRANT SELECT ON trust_autopay TO trust_ai_agent;
GRANT SELECT ON trust_autopay_stops TO trust_ai_agent;
GRANT SELECT ON trust_autopay_runs TO trust_ai_agent;
GRANT SELECT ON trust_autopay_submissions TO trust_ai_agent;

-- The tenant's emails about their autopay: each payment it starts, before the bank is debited, one
-- it gave up sending, and its stopping by itself. Replaced at once by checks that allow them; no guarantee is lost.
ALTER TABLE trust_emails ADD COLUMN autopay_id uuid;
ALTER TABLE trust_emails ADD CONSTRAINT trust_emails_autopay
FOREIGN KEY (pmc_id, autopay_id) REFERENCES trust_autopay (pmc_id, id) NOT VALID;
-- The kinds allowed so far are read from the check itself, not written out again here, so a
-- migration that added a kind before this one keeps it.
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
        SELECT kind, 1000 + n
        FROM unnest(ARRAY['autopay_payment', 'autopay_stopped', 'autopay_not_sent'])
            WITH ORDINALITY AS r (kind, n)
    ) AS k;
    ALTER TABLE trust_emails DROP CONSTRAINT trust_emails_kind_check;
    EXECUTE format(
        'ALTER TABLE trust_emails ADD CONSTRAINT trust_emails_kind_check'
        ' CHECK (kind IN (%s)) NOT VALID', v_kinds
    );
END;
$$;
-- squawk-ignore ban-drop-constraint
ALTER TABLE trust_emails DROP CONSTRAINT trust_emails_recipient_check;
-- Whom each kind goes to, and what an automatic one is about; nothing else is named.
ALTER TABLE trust_emails ADD CONSTRAINT trust_emails_recipient_check CHECK (
    CASE kind
        WHEN 'invite' THEN num_nonnulls(tenant_id, owner_id, staff_id) = 1 AND vendor_id IS NULL
        WHEN 'password_reset' THEN num_nonnulls(tenant_id, owner_id) = 1
            AND vendor_id IS NULL AND staff_id IS NULL
        WHEN 'message' THEN num_nonnulls(tenant_id, owner_id, vendor_id) = 1 AND staff_id IS NULL
        WHEN 'rent_reminder' THEN tenant_id IS NOT NULL AND lease_id IS NOT NULL
            AND period IS NOT NULL AND num_nonnulls(owner_id, vendor_id, staff_id) = 0
        WHEN 'late_notice' THEN tenant_id IS NOT NULL AND lease_id IS NOT NULL
            AND charge_id IS NOT NULL AND num_nonnulls(owner_id, vendor_id, staff_id) = 0
        WHEN 'owner_statement' THEN owner_id IS NOT NULL AND period IS NOT NULL
            AND num_nonnulls(tenant_id, vendor_id, staff_id) = 0
        WHEN 'work_order' THEN vendor_id IS NOT NULL AND work_order_step_id IS NOT NULL
            AND num_nonnulls(tenant_id, owner_id, staff_id) = 0
        WHEN 'adverse_action' THEN applicant_id IS NOT NULL AND application_id IS NOT NULL
            AND num_nonnulls(tenant_id, owner_id, vendor_id, staff_id) = 0
        WHEN 'emergency_request' THEN staff_id IS NOT NULL AND request_id IS NOT NULL
            AND num_nonnulls(tenant_id, owner_id, vendor_id) = 0
        WHEN 'signing_request' THEN offer_id IS NOT NULL AND signer_position IS NOT NULL
            AND num_nonnulls(tenant_id, owner_id, vendor_id, staff_id) = 0
        WHEN 'payment_receipt' THEN tenant_id IS NOT NULL AND lease_id IS NOT NULL
            AND transfer_id IS NOT NULL AND num_nonnulls(owner_id, vendor_id, staff_id) = 0
        WHEN 'deposit_statement' THEN tenant_id IS NOT NULL AND lease_id IS NOT NULL
            AND num_nonnulls(owner_id, vendor_id, staff_id) = 0
        WHEN 'autopay_payment' THEN tenant_id IS NOT NULL AND lease_id IS NOT NULL
            AND autopay_id IS NOT NULL AND period IS NOT NULL
            AND num_nonnulls(owner_id, vendor_id, staff_id) = 0
        WHEN 'autopay_not_sent' THEN tenant_id IS NOT NULL AND lease_id IS NOT NULL
            AND autopay_id IS NOT NULL AND period IS NOT NULL
            AND num_nonnulls(owner_id, vendor_id, staff_id) = 0
        WHEN 'autopay_stopped' THEN tenant_id IS NOT NULL AND lease_id IS NOT NULL
            AND autopay_id IS NOT NULL AND num_nonnulls(owner_id, vendor_id, staff_id) = 0
        ELSE tenant_id IS NULL AND vendor_id IS NULL AND staff_id IS NULL
    END
    AND (kind = 'message') = (sent_by IS NOT NULL)
    AND (kind IN ('invite', 'password_reset', 'signing_request')) = needs_link
    AND (kind IN (
        'rent_reminder', 'late_notice', 'payment_receipt', 'deposit_statement', 'autopay_payment',
        'autopay_stopped', 'autopay_not_sent'
    )) = (lease_id IS NOT NULL)
    AND (kind = 'late_notice') = (charge_id IS NOT NULL)
    AND (kind IN ('rent_reminder', 'owner_statement', 'autopay_payment', 'autopay_not_sent'))
        = (period IS NOT NULL)
    AND (kind = 'owner_statement') <= (period = date_trunc('month', period)::date)
    AND (kind = 'work_order') = (work_order_step_id IS NOT NULL)
    AND (kind = 'adverse_action') = (application_id IS NOT NULL AND applicant_id IS NOT NULL)
    AND (kind = 'emergency_request') = (request_id IS NOT NULL)
    AND (kind = 'signing_request') = (offer_id IS NOT NULL OR signer_position IS NOT NULL)
    AND (kind = 'payment_receipt') = (transfer_id IS NOT NULL)
    AND (kind IN ('autopay_payment', 'autopay_stopped', 'autopay_not_sent'))
        = (autopay_id IS NOT NULL)
) NOT VALID;
-- squawk-ignore require-concurrent-index-creation
CREATE UNIQUE INDEX trust_emails_one_autopay_payment ON trust_emails (autopay_id, period)
WHERE kind = 'autopay_payment';
-- squawk-ignore require-concurrent-index-creation
CREATE UNIQUE INDEX trust_emails_one_autopay_stopped ON trust_emails (autopay_id)
WHERE kind = 'autopay_stopped';
-- squawk-ignore require-concurrent-index-creation
CREATE UNIQUE INDEX trust_emails_one_autopay_not_sent ON trust_emails (autopay_id, period)
WHERE kind = 'autopay_not_sent';

-- migrate:down
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '60s';

-- An autopay is the tenant's authorization and the record of what it paid; undoing the tables
-- would leave its payments and emails without it.
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM trust_autopay) THEN
        RAISE EXCEPTION 'trust: tenants set up autopay; this migration is not rolled back'
            USING ERRCODE = 'object_in_use';
    END IF;
END;
$$;
-- squawk-ignore require-concurrent-index-deletion
DROP INDEX trust_emails_one_autopay_not_sent;
-- squawk-ignore require-concurrent-index-deletion
DROP INDEX trust_emails_one_autopay_stopped;
-- squawk-ignore require-concurrent-index-deletion
DROP INDEX trust_emails_one_autopay_payment;
-- squawk-ignore ban-drop-constraint
ALTER TABLE trust_emails DROP CONSTRAINT trust_emails_recipient_check;
-- squawk-ignore ban-drop-constraint
ALTER TABLE trust_emails DROP CONSTRAINT trust_emails_autopay;
-- squawk-ignore ban-drop-column
ALTER TABLE trust_emails DROP COLUMN autopay_id;
DO $$
DECLARE
    v_kinds text;
BEGIN
    SELECT string_agg(quote_literal(m[1]), ', ' ORDER BY n) INTO v_kinds
    FROM pg_constraint AS c,
         regexp_matches(pg_get_constraintdef(c.oid), '''([a-z_]+)''::text', 'g')
             WITH ORDINALITY AS r (m, n)
    WHERE c.conrelid = 'trust_emails'::regclass AND c.conname = 'trust_emails_kind_check'
      AND m[1] NOT IN ('autopay_payment', 'autopay_stopped', 'autopay_not_sent');
    ALTER TABLE trust_emails DROP CONSTRAINT trust_emails_kind_check;
    EXECUTE format(
        'ALTER TABLE trust_emails ADD CONSTRAINT trust_emails_kind_check'
        ' CHECK (kind IN (%s)) NOT VALID', v_kinds
    );
END;
$$;
ALTER TABLE trust_emails ADD CONSTRAINT trust_emails_recipient_check CHECK (
    CASE kind
        WHEN 'invite' THEN num_nonnulls(tenant_id, owner_id, staff_id) = 1 AND vendor_id IS NULL
        WHEN 'password_reset' THEN num_nonnulls(tenant_id, owner_id) = 1
            AND vendor_id IS NULL AND staff_id IS NULL
        WHEN 'message' THEN num_nonnulls(tenant_id, owner_id, vendor_id) = 1 AND staff_id IS NULL
        WHEN 'rent_reminder' THEN tenant_id IS NOT NULL AND lease_id IS NOT NULL
            AND period IS NOT NULL AND num_nonnulls(owner_id, vendor_id, staff_id) = 0
        WHEN 'late_notice' THEN tenant_id IS NOT NULL AND lease_id IS NOT NULL
            AND charge_id IS NOT NULL AND num_nonnulls(owner_id, vendor_id, staff_id) = 0
        WHEN 'owner_statement' THEN owner_id IS NOT NULL AND period IS NOT NULL
            AND num_nonnulls(tenant_id, vendor_id, staff_id) = 0
        WHEN 'work_order' THEN vendor_id IS NOT NULL AND work_order_step_id IS NOT NULL
            AND num_nonnulls(tenant_id, owner_id, staff_id) = 0
        WHEN 'adverse_action' THEN applicant_id IS NOT NULL AND application_id IS NOT NULL
            AND num_nonnulls(tenant_id, owner_id, vendor_id, staff_id) = 0
        WHEN 'emergency_request' THEN staff_id IS NOT NULL AND request_id IS NOT NULL
            AND num_nonnulls(tenant_id, owner_id, vendor_id) = 0
        WHEN 'signing_request' THEN offer_id IS NOT NULL AND signer_position IS NOT NULL
            AND num_nonnulls(tenant_id, owner_id, vendor_id, staff_id) = 0
        WHEN 'payment_receipt' THEN tenant_id IS NOT NULL AND lease_id IS NOT NULL
            AND transfer_id IS NOT NULL AND num_nonnulls(owner_id, vendor_id, staff_id) = 0
        WHEN 'deposit_statement' THEN tenant_id IS NOT NULL AND lease_id IS NOT NULL
            AND num_nonnulls(owner_id, vendor_id, staff_id) = 0
        ELSE tenant_id IS NULL AND vendor_id IS NULL AND staff_id IS NULL
    END
    AND (kind = 'message') = (sent_by IS NOT NULL)
    AND (kind IN ('invite', 'password_reset', 'signing_request')) = needs_link
    AND (kind IN ('rent_reminder', 'late_notice', 'payment_receipt', 'deposit_statement'))
        = (lease_id IS NOT NULL)
    AND (kind = 'late_notice') = (charge_id IS NOT NULL)
    AND (kind IN ('rent_reminder', 'owner_statement')) = (period IS NOT NULL)
    AND (kind = 'owner_statement') <= (period = date_trunc('month', period)::date)
    AND (kind = 'work_order') = (work_order_step_id IS NOT NULL)
    AND (kind = 'adverse_action') = (application_id IS NOT NULL AND applicant_id IS NOT NULL)
    AND (kind = 'emergency_request') = (request_id IS NOT NULL)
    AND (kind = 'signing_request') = (offer_id IS NOT NULL OR signer_position IS NOT NULL)
    AND (kind = 'payment_receipt') = (transfer_id IS NOT NULL)
) NOT VALID;
-- squawk-ignore ban-drop-table
DROP TABLE trust_autopay_submissions;
-- squawk-ignore ban-drop-table
DROP TABLE trust_autopay_runs;
-- squawk-ignore ban-drop-function
DROP FUNCTION trust_check_autopay_run();
-- squawk-ignore ban-drop-table
DROP TABLE trust_autopay_stops;
-- squawk-ignore ban-drop-function
DROP FUNCTION trust_lock_autopay_stop();
-- squawk-ignore ban-drop-table
DROP TABLE trust_autopay_accounts;
-- squawk-ignore ban-drop-table
DROP TABLE trust_autopay;
-- squawk-ignore ban-drop-function
DROP FUNCTION trust_check_autopay();
