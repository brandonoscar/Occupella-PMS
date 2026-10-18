-- migrate:up
-- Signing links by email (the founder's review, q081; docs/adr/0007-email.md): when staff send a
-- lease offer for signing, the PMS emails each signer their own signing link, while the server
-- sends email.
--
--   signing_request   an email to one signer of an offer (trust_emails.offer_id and
--                     signer_position), once each. Like an invite's, its link is never written
--                     here: it waits in the writing process's memory until the email is sent
--                     (needs_link), ten minutes at most.

-- On a live database, give up rather than queue behind a long transaction while holding locks.
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '60s';

ALTER TABLE trust_emails ADD COLUMN offer_id uuid;
ALTER TABLE trust_emails ADD COLUMN signer_position bigint;
-- Replaced at once by checks that allow one more kind; no guarantee is lost.
-- squawk-ignore ban-drop-constraint
ALTER TABLE trust_emails DROP CONSTRAINT trust_emails_kind_check;
ALTER TABLE trust_emails ADD CONSTRAINT trust_emails_kind_check CHECK (kind IN (
    'bill_approval', 'invite', 'password_reset', 'message', 'rent_reminder', 'late_notice',
    'owner_statement', 'work_order', 'adverse_action', 'emergency_request', 'signing_request'
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
        WHEN 'emergency_request' THEN staff_id IS NOT NULL AND request_id IS NOT NULL
            AND num_nonnulls(tenant_id, owner_id, vendor_id) = 0
        WHEN 'signing_request' THEN offer_id IS NOT NULL AND signer_position IS NOT NULL
            AND num_nonnulls(tenant_id, owner_id, vendor_id, staff_id) = 0
        ELSE tenant_id IS NULL AND vendor_id IS NULL AND staff_id IS NULL
    END
    AND (kind = 'message') = (sent_by IS NOT NULL)
    AND (kind IN ('invite', 'password_reset', 'signing_request')) = needs_link
    AND (kind IN ('rent_reminder', 'late_notice')) = (lease_id IS NOT NULL)
    AND (kind = 'late_notice') = (charge_id IS NOT NULL)
    AND (kind IN ('rent_reminder', 'owner_statement')) = (period IS NOT NULL)
    AND (kind = 'owner_statement') <= (period = date_trunc('month', period)::date)
    AND (kind = 'work_order') = (work_order_step_id IS NOT NULL)
    AND (kind = 'adverse_action') = (application_id IS NOT NULL AND applicant_id IS NOT NULL)
    AND (kind = 'emergency_request') = (request_id IS NOT NULL)
    AND (kind = 'signing_request') = (offer_id IS NOT NULL OR signer_position IS NOT NULL)
) NOT VALID;
-- squawk-ignore adding-foreign-key-constraint
ALTER TABLE trust_emails ADD CONSTRAINT trust_emails_pmc_id_offer_id_fkey
FOREIGN KEY (pmc_id, offer_id) REFERENCES trust_lease_offers (pmc_id, id) NOT VALID;
-- squawk-ignore adding-foreign-key-constraint
ALTER TABLE trust_emails ADD CONSTRAINT trust_emails_offer_id_signer_position_fkey
FOREIGN KEY (offer_id, signer_position) REFERENCES trust_lease_offer_signers (offer_id, position)
NOT VALID;
-- squawk-ignore require-concurrent-index-creation
CREATE UNIQUE INDEX trust_emails_one_signing_request ON trust_emails (offer_id, signer_position)
WHERE kind = 'signing_request';

-- migrate:down
DO $$
BEGIN
    RAISE EXCEPTION 'signing_request_emails cannot be rolled back: emails are records';
END;
$$;
