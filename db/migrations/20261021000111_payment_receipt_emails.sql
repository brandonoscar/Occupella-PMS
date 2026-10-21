-- migrate:up
-- A tenant's payment receipt by email, and the owner's monthly statement with its PDF (the
-- founder's review q029, q045 and q055; docs/adr/0007-email.md):
--
--   payment_receipt   an email to one tenant of a lease about one payment recorded to it
--                     (trust_emails.tenant_id, lease_id and transfer_id, any transfer the
--                     payment posted), once each, written in the transaction that records the
--                     payment when the company turned receipts on and the server sends email.
--
-- A receipt's PDF and an owner statement's are made when the email is handed to the sender, from
-- what the database holds then (src/pms/attachments.py); the outbox keeps no file.

-- On a live database, give up rather than queue behind a long transaction while holding locks.
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '60s';

ALTER TABLE trust_emails ADD COLUMN transfer_id text;
-- Replaced at once by checks that allow one more kind; no guarantee is lost.
-- squawk-ignore ban-drop-constraint
ALTER TABLE trust_emails DROP CONSTRAINT trust_emails_kind_check;
ALTER TABLE trust_emails ADD CONSTRAINT trust_emails_kind_check CHECK (kind IN (
    'bill_approval', 'invite', 'password_reset', 'message', 'rent_reminder', 'late_notice',
    'owner_statement', 'work_order', 'adverse_action', 'emergency_request', 'signing_request',
    'payment_receipt'
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
        WHEN 'payment_receipt' THEN tenant_id IS NOT NULL AND lease_id IS NOT NULL
            AND transfer_id IS NOT NULL AND num_nonnulls(owner_id, vendor_id, staff_id) = 0
        ELSE tenant_id IS NULL AND vendor_id IS NULL AND staff_id IS NULL
    END
    AND (kind = 'message') = (sent_by IS NOT NULL)
    AND (kind IN ('invite', 'password_reset', 'signing_request')) = needs_link
    AND (kind IN ('rent_reminder', 'late_notice', 'payment_receipt')) = (lease_id IS NOT NULL)
    AND (kind = 'late_notice') = (charge_id IS NOT NULL)
    AND (kind IN ('rent_reminder', 'owner_statement')) = (period IS NOT NULL)
    AND (kind = 'owner_statement') <= (period = date_trunc('month', period)::date)
    AND (kind = 'work_order') = (work_order_step_id IS NOT NULL)
    AND (kind = 'adverse_action') = (application_id IS NOT NULL AND applicant_id IS NOT NULL)
    AND (kind = 'emergency_request') = (request_id IS NOT NULL)
    AND (kind = 'signing_request') = (offer_id IS NOT NULL OR signer_position IS NOT NULL)
    AND (kind = 'payment_receipt') = (transfer_id IS NOT NULL)
) NOT VALID;
-- squawk-ignore adding-foreign-key-constraint
ALTER TABLE trust_emails ADD CONSTRAINT trust_emails_transfer_id_fkey
FOREIGN KEY (transfer_id) REFERENCES pgledger_transfers (id) NOT VALID;
-- squawk-ignore require-concurrent-index-creation
CREATE UNIQUE INDEX trust_emails_one_payment_receipt ON trust_emails (tenant_id, transfer_id)
WHERE kind = 'payment_receipt';

-- Whether the company emails a receipt for each payment staff record; off until it is turned on.
ALTER TABLE trust_email_settings ADD COLUMN payment_receipts boolean NOT NULL DEFAULT false;

-- migrate:down
DO $$
BEGIN
    RAISE EXCEPTION 'payment_receipt_emails cannot be rolled back: emails are records';
END;
$$;
