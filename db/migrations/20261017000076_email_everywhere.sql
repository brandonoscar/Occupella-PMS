-- migrate:up
-- Email beyond a bill's approval (founder decision, 2026-10-17; docs/adr/0007-email.md): the
-- outbox of migration 0042 now also holds portal invites, password resets an owner or a tenant
-- asks for, and what staff write to a tenant, an owner or a vendor. Each email names the person
-- it went to, so their page lists it, and a message names the staff member who wrote it.
--
--   trust_emails.tenant_id, owner_id, vendor_id, staff_id   whom it went to (one of them, but
--                            for a bill's approval email written before this, which names none)
--   trust_emails.sent_by     the staff member who wrote a message
--   trust_emails.needs_link  an invite or a reset: its link (a sign-in secret) is never kept
--                            here; the app holds it only until the email is handed to the sender
--
-- Staff need the new area 'email' to write a message (custom roles, migration 0057). Emails and
-- their deliveries hold people's addresses, so neither is audited and the AI role reads neither,
-- as before. Records: kept for good.

-- On a live database, give up rather than queue behind a long transaction while holding locks.
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '60s';

ALTER TABLE trust_emails ADD COLUMN tenant_id uuid;
ALTER TABLE trust_emails ADD COLUMN owner_id uuid;
ALTER TABLE trust_emails ADD COLUMN vendor_id uuid;
ALTER TABLE trust_emails ADD COLUMN staff_id uuid;
ALTER TABLE trust_emails ADD COLUMN sent_by uuid;
ALTER TABLE trust_emails ADD COLUMN needs_link boolean NOT NULL DEFAULT false;

-- Every email so far is a bill's approval with none of the new columns set, so it holds the new
-- checks; they are added NOT VALID, checked for every new email.
-- Replaced at once by a check that allows more kinds; no guarantee is lost.
-- squawk-ignore ban-drop-constraint
ALTER TABLE trust_emails DROP CONSTRAINT trust_emails_kind_check;
ALTER TABLE trust_emails ADD CONSTRAINT trust_emails_kind_check
CHECK (kind IN ('bill_approval', 'invite', 'password_reset', 'message')) NOT VALID;
-- Whom each kind goes to: an invite to one person with a login (staff, owner or tenant), a reset
-- to an owner or a tenant who asked, a message to a tenant, owner or vendor from a staff member,
-- a bill's approval to its owner (none named before this migration). Only an invite and a reset
-- carry a link, and only a message names who wrote it.
ALTER TABLE trust_emails ADD CONSTRAINT trust_emails_recipient_check CHECK (
    CASE kind
        WHEN 'invite' THEN num_nonnulls(tenant_id, owner_id, staff_id) = 1 AND vendor_id IS NULL
        WHEN 'password_reset' THEN num_nonnulls(tenant_id, owner_id) = 1
            AND vendor_id IS NULL AND staff_id IS NULL
        WHEN 'message' THEN num_nonnulls(tenant_id, owner_id, vendor_id) = 1 AND staff_id IS NULL
        ELSE tenant_id IS NULL AND vendor_id IS NULL AND staff_id IS NULL
    END
    AND (kind = 'message') = (sent_by IS NOT NULL)
    AND (kind IN ('invite', 'password_reset')) = needs_link
) NOT VALID;
-- squawk-ignore adding-foreign-key-constraint
ALTER TABLE trust_emails ADD CONSTRAINT trust_emails_pmc_id_tenant_id_fkey
FOREIGN KEY (pmc_id, tenant_id) REFERENCES trust_tenants (pmc_id, id) NOT VALID;
-- squawk-ignore adding-foreign-key-constraint
ALTER TABLE trust_emails ADD CONSTRAINT trust_emails_pmc_id_owner_id_fkey
FOREIGN KEY (pmc_id, owner_id) REFERENCES trust_owners (pmc_id, id) NOT VALID;
-- squawk-ignore adding-foreign-key-constraint
ALTER TABLE trust_emails ADD CONSTRAINT trust_emails_pmc_id_vendor_id_fkey
FOREIGN KEY (pmc_id, vendor_id) REFERENCES trust_vendors (pmc_id, id) NOT VALID;
-- squawk-ignore adding-foreign-key-constraint
ALTER TABLE trust_emails ADD CONSTRAINT trust_emails_pmc_id_staff_id_fkey
FOREIGN KEY (pmc_id, staff_id) REFERENCES trust_staff (pmc_id, id) NOT VALID;
-- squawk-ignore adding-foreign-key-constraint
ALTER TABLE trust_emails ADD CONSTRAINT trust_emails_pmc_id_sent_by_fkey
FOREIGN KEY (pmc_id, sent_by) REFERENCES trust_staff (pmc_id, id) NOT VALID;

-- A person's emails, for their page. The outbox holds a bill's approval emails only so far, at
-- most one a bill, so these build in moments; CONCURRENTLY can't run inside the migration's
-- transaction.
-- squawk-ignore require-concurrent-index-creation
CREATE INDEX trust_emails_tenant_id ON trust_emails (tenant_id) WHERE tenant_id IS NOT NULL;
-- squawk-ignore require-concurrent-index-creation
CREATE INDEX trust_emails_owner_id ON trust_emails (owner_id) WHERE owner_id IS NOT NULL;
-- squawk-ignore require-concurrent-index-creation
CREATE INDEX trust_emails_vendor_id ON trust_emails (vendor_id) WHERE vendor_id IS NOT NULL;

-- The new area for custom roles: emailing tenants, owners and vendors. Replaced at once by a
-- check that allows one area more; every row holds it, so it is added NOT VALID.
-- squawk-ignore ban-drop-constraint
ALTER TABLE trust_custom_role_areas DROP CONSTRAINT trust_custom_role_areas_area_check;
ALTER TABLE trust_custom_role_areas ADD CONSTRAINT trust_custom_role_areas_area_check
CHECK (area IN (
    'rent', 'bills', 'owners', 'budgets', 'journal', 'month-end', 'switching', 'setup',
    'move-out', 'documents', 'leasing', 'maintenance', 'people', 'email'
)) NOT VALID;

-- migrate:down
DO $$
BEGIN
    RAISE EXCEPTION 'email_everywhere cannot be rolled back: emails are records';
END;
$$;
