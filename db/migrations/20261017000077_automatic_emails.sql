-- migrate:up
-- The emails the PMS writes by itself (docs/adr/0007-email.md, its second step), each one a
-- company turns on in Settings, Company setup (`trust_email_settings`, append-only, the latest row
-- holding, naming who changed it; none at all until a company turns one on):
--
--   rent_reminder     to each tenant of a lease, some days before its rent is due (once per
--                     tenant, lease and due day)
--   late_notice       to each tenant of a lease, when a late fee is charged (once per tenant and
--                     late fee)
--   owner_statement   to an owner, from a day of the month the company picks: last month's
--                     statement in brief, with a link to the portal (once per owner and month)
--   work_order        to a vendor, when a work order is assigned to them or their visit scheduled
--                     (once per step)
--   adverse_action    to each applicant of an application declined or approved on conditions
--                     on a screening: the notice the law asks for (once per applicant and
--                     application)
--
-- Each names what it is about (the lease and due day, the late fee's charge, the month, the
-- work order step, the application), and the indexes below keep each once. Emails hold people's
-- addresses: not audited and never read by the AI role, as before. The settings are only which
-- emails go and when: audited, and the AI role reads them. Records: kept for good.

-- On a live database, give up rather than queue behind a long transaction while holding locks.
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '60s';

CREATE TABLE trust_email_settings (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    pmc_id uuid NOT NULL REFERENCES trust_pmcs (pmc_id),
    -- Days before rent is due that its reminder goes; none sends no reminder. 1 to 15 fits.
    -- squawk-ignore prefer-bigint-over-int
    rent_reminder_days integer CHECK (rent_reminder_days BETWEEN 1 AND 15),
    late_notices boolean NOT NULL,
    -- The day of the month owners get last month's statement; none sends none. 1 to 28 fits.
    -- squawk-ignore prefer-bigint-over-int
    owner_statement_day integer CHECK (owner_statement_day BETWEEN 1 AND 28),
    work_order_notices boolean NOT NULL,
    adverse_action_notices boolean NOT NULL,
    recorded_by uuid,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    FOREIGN KEY (pmc_id, recorded_by) REFERENCES trust_staff (pmc_id, id)
);
CREATE INDEX trust_email_settings_pmc ON trust_email_settings (pmc_id, created_at);
CREATE INDEX trust_email_settings_recorded_by ON trust_email_settings (pmc_id, recorded_by);

CREATE TRIGGER trust_recorded_by BEFORE INSERT ON trust_email_settings
FOR EACH ROW EXECUTE FUNCTION trust_name_recorder();
CREATE TRIGGER trust_append_only
BEFORE UPDATE OR DELETE OR TRUNCATE ON trust_email_settings
FOR EACH STATEMENT EXECUTE FUNCTION trust_refuse_ledger_rewrite();
CREATE TRIGGER trust_audit AFTER INSERT OR UPDATE OR DELETE ON trust_email_settings
FOR EACH ROW EXECUTE FUNCTION trust_audit();

REVOKE ALL ON trust_email_settings FROM PUBLIC;
GRANT SELECT, INSERT ON trust_email_settings TO trust_app;
GRANT SELECT ON trust_email_settings TO trust_ai_agent;

-- What each automatic email is about.
ALTER TABLE trust_emails ADD COLUMN lease_id uuid;
ALTER TABLE trust_emails ADD COLUMN charge_id uuid;
ALTER TABLE trust_emails ADD COLUMN period date;
ALTER TABLE trust_emails ADD COLUMN work_order_step_id uuid;
ALTER TABLE trust_emails ADD COLUMN application_id uuid;
ALTER TABLE trust_emails ADD COLUMN applicant_id uuid;

-- Every email so far has none of the new columns set, so it holds the new checks; they are
-- added NOT VALID, checked for every new email. Replaced at once by checks that allow more
-- kinds; no guarantee is lost.
-- squawk-ignore ban-drop-constraint
ALTER TABLE trust_emails DROP CONSTRAINT trust_emails_kind_check;
ALTER TABLE trust_emails ADD CONSTRAINT trust_emails_kind_check CHECK (kind IN (
    'bill_approval', 'invite', 'password_reset', 'message', 'rent_reminder', 'late_notice',
    'owner_statement', 'work_order', 'adverse_action'
)) NOT VALID;
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
        ELSE tenant_id IS NULL AND vendor_id IS NULL AND staff_id IS NULL
    END
    AND (kind = 'message') = (sent_by IS NOT NULL)
    AND (kind IN ('invite', 'password_reset')) = needs_link
    AND (kind IN ('rent_reminder', 'late_notice')) = (lease_id IS NOT NULL)
    AND (kind = 'late_notice') = (charge_id IS NOT NULL)
    AND (kind IN ('rent_reminder', 'owner_statement')) = (period IS NOT NULL)
    AND (kind = 'owner_statement') <= (period = date_trunc('month', period)::date)
    AND (kind = 'work_order') = (work_order_step_id IS NOT NULL)
    AND (kind = 'adverse_action') = (application_id IS NOT NULL AND applicant_id IS NOT NULL)
) NOT VALID;
-- squawk-ignore adding-foreign-key-constraint
ALTER TABLE trust_emails ADD CONSTRAINT trust_emails_pmc_id_lease_id_fkey
FOREIGN KEY (pmc_id, lease_id) REFERENCES trust_leases (pmc_id, id) NOT VALID;
-- squawk-ignore adding-foreign-key-constraint
ALTER TABLE trust_emails ADD CONSTRAINT trust_emails_pmc_id_charge_id_fkey
FOREIGN KEY (pmc_id, charge_id) REFERENCES trust_charges (pmc_id, id) NOT VALID;
-- squawk-ignore adding-foreign-key-constraint
ALTER TABLE trust_emails ADD CONSTRAINT trust_emails_work_order_step_id_fkey
FOREIGN KEY (work_order_step_id) REFERENCES trust_work_order_steps (id) NOT VALID;
-- squawk-ignore adding-foreign-key-constraint
ALTER TABLE trust_emails ADD CONSTRAINT trust_emails_pmc_id_application_id_fkey
FOREIGN KEY (pmc_id, application_id) REFERENCES trust_applications (pmc_id, id) NOT VALID;
-- squawk-ignore adding-foreign-key-constraint
ALTER TABLE trust_emails ADD CONSTRAINT trust_emails_applicant_id_fkey
FOREIGN KEY (applicant_id) REFERENCES trust_applicants (id) NOT VALID;

-- Each automatic email once. The outbox holds no automatic email yet, so these build in moments;
-- CONCURRENTLY can't run inside the migration's transaction.
-- squawk-ignore require-concurrent-index-creation
CREATE UNIQUE INDEX trust_emails_one_rent_reminder ON trust_emails (tenant_id, lease_id, period)
WHERE kind = 'rent_reminder';
-- squawk-ignore require-concurrent-index-creation
CREATE UNIQUE INDEX trust_emails_one_late_notice ON trust_emails (tenant_id, charge_id)
WHERE kind = 'late_notice';
-- squawk-ignore require-concurrent-index-creation
CREATE UNIQUE INDEX trust_emails_one_owner_statement ON trust_emails (owner_id, period)
WHERE kind = 'owner_statement';
-- squawk-ignore require-concurrent-index-creation
CREATE UNIQUE INDEX trust_emails_one_work_order ON trust_emails (work_order_step_id)
WHERE kind = 'work_order';
-- squawk-ignore require-concurrent-index-creation
CREATE UNIQUE INDEX trust_emails_one_adverse_action ON trust_emails (applicant_id, application_id)
WHERE kind = 'adverse_action';

-- migrate:down
DO $$
BEGIN
    RAISE EXCEPTION 'automatic_emails cannot be rolled back: emails and settings are records';
END;
$$;
