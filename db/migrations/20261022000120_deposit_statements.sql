-- migrate:up
-- A deposit's return deadline and its statement (the founder's review q040 and q039, brought in
-- scope on 2026-10-22; docs/law.md):
--
--   trust_deduction_receipts  a receipt or invoice for what is kept of a deposit: one of the
--                             lease's documents (kind lease_document) linked to one of its fees,
--                             once each, naming who linked it. Append-only. The deposit statement
--                             lists each under its fee and carries the file.
--   deposit_statement         an email to one tenant of a lease whose deposit was returned
--                             (trust_emails.tenant_id and lease_id), written when staff send the
--                             statement. Its PDF is made when the email is handed to the sender
--                             (src/pms/attachments.py): the tenant's forwarding address, what was
--                             kept and why, the receipts and the move-out photos. Staff may send
--                             it again, after a receipt comes in.
--
-- The return deadline is worked out, not stored (src/pms/api/moveout.py): 14 days after the
-- lease's last day, 10 while it owes nothing, the strictest found.

-- On a live database, give up rather than queue behind a long transaction while holding locks.
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '60s';

CREATE TABLE trust_deduction_receipts (
    charge_id uuid NOT NULL,
    document_id uuid NOT NULL,
    pmc_id uuid NOT NULL REFERENCES trust_pmcs (pmc_id),
    recorded_by uuid,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (charge_id, document_id),
    FOREIGN KEY (pmc_id, charge_id) REFERENCES trust_charges (pmc_id, id),
    FOREIGN KEY (pmc_id, document_id) REFERENCES trust_documents (pmc_id, id),
    FOREIGN KEY (pmc_id, recorded_by) REFERENCES trust_staff (pmc_id, id)
);
CREATE INDEX trust_deduction_receipts_charge ON trust_deduction_receipts (pmc_id, charge_id);
CREATE INDEX trust_deduction_receipts_document ON trust_deduction_receipts (pmc_id, document_id);
CREATE INDEX trust_deduction_receipts_recorded_by
ON trust_deduction_receipts (pmc_id, recorded_by);

-- A receipt is a document of the fee's own lease.
CREATE FUNCTION trust_check_deduction_receipt() RETURNS trigger
LANGUAGE plpgsql
SET search_path = public, pg_temp
AS $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM trust_charges AS c
        JOIN trust_documents AS d ON d.lease_id = c.lease_id
        WHERE c.id = NEW.charge_id AND d.id = NEW.document_id
          AND c.kind = 'fee' AND d.kind = 'lease_document'
    ) THEN
        RAISE EXCEPTION 'trust: a receipt is a document of the lease of the fee it is for'
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER trust_deduction_receipt
BEFORE INSERT ON trust_deduction_receipts
FOR EACH ROW EXECUTE FUNCTION trust_check_deduction_receipt();
CREATE TRIGGER trust_recorded_by BEFORE INSERT ON trust_deduction_receipts
FOR EACH ROW EXECUTE FUNCTION trust_name_recorder();
CREATE TRIGGER trust_append_only
BEFORE UPDATE OR DELETE OR TRUNCATE ON trust_deduction_receipts
FOR EACH STATEMENT EXECUTE FUNCTION trust_refuse_ledger_rewrite();
CREATE TRIGGER trust_audit AFTER INSERT OR UPDATE OR DELETE ON trust_deduction_receipts
FOR EACH ROW EXECUTE FUNCTION trust_audit();

REVOKE ALL ON trust_deduction_receipts FROM PUBLIC;
REVOKE ALL ON FUNCTION trust_check_deduction_receipt() FROM PUBLIC;
GRANT SELECT, INSERT ON trust_deduction_receipts TO trust_app;
GRANT SELECT ON trust_deduction_receipts TO trust_ai_agent;

-- Replaced at once by checks that allow one more kind; no guarantee is lost.
-- squawk-ignore ban-drop-constraint
ALTER TABLE trust_emails DROP CONSTRAINT trust_emails_kind_check;
ALTER TABLE trust_emails ADD CONSTRAINT trust_emails_kind_check CHECK (kind IN (
    'bill_approval', 'invite', 'password_reset', 'message', 'rent_reminder', 'late_notice',
    'owner_statement', 'work_order', 'adverse_action', 'emergency_request', 'signing_request',
    'payment_receipt', 'deposit_statement'
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

-- migrate:down
DO $$
BEGIN
    RAISE EXCEPTION 'deposit_statements cannot be rolled back: emails and receipts are records';
END;
$$;
