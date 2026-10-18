-- migrate:up
-- Maintenance (the founder's review, q066, q070, q071 and q075; docs/law.md):
--
--   emergency            a tenant marks a request an emergency (no heat, a leak, no water...);
--                        its work order opens urgent, and each staff member on call is emailed.
--   request_photo        photos a tenant files with a request (trust_documents, about the
--                        request; PDF, JPEG or PNG of at most 5 MB, at most three a request as
--                        the portal takes them). Staff see them with the request and its work
--                        order; the AI role never reads a file.
--   trust_on_call_staff  who is on call for emergencies: append-only, the latest row for a staff
--                        member holding, naming who changed it.
--   emergency_request    an email to each staff member on call when a tenant files an
--                        emergency, once per request and staff member, while the server sends
--                        email (texts stay out of scope).
--   visit_window         when in the day a scheduled visit comes ("8-10 am"), on the schedule
--                        step; the vendor's email and the tenant's portal show it with the day.

-- On a live database, give up rather than queue behind a long transaction while holding locks.
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '60s';

ALTER TABLE trust_maintenance_requests ADD COLUMN emergency boolean NOT NULL DEFAULT false;

-- --- photos with a request ----------------------------------------------------------------

ALTER TABLE trust_documents ADD COLUMN request_id uuid;
-- Replaced at once by a check that allows one more kind; no guarantee is lost.
-- squawk-ignore ban-drop-constraint
ALTER TABLE trust_documents DROP CONSTRAINT trust_documents_kind_check;
ALTER TABLE trust_documents ADD CONSTRAINT trust_documents_kind_check CHECK (
    kind IN (
        'w9', 'receipt', 'move_out_photo', 'owner_document', 'work_order_photo',
        'property_document', 'unit_document', 'tenant_document', 'lease_document',
        'vendor_document', 'company_logo', 'request_photo'
    )
) NOT VALID;
ALTER TABLE trust_documents ADD CONSTRAINT trust_documents_request_check
CHECK ((kind = 'request_photo') = (request_id IS NOT NULL)) NOT VALID;
-- squawk-ignore adding-foreign-key-constraint
ALTER TABLE trust_documents ADD CONSTRAINT trust_documents_pmc_id_request_id_fkey
FOREIGN KEY (pmc_id, request_id) REFERENCES trust_maintenance_requests (pmc_id, id) NOT VALID;
-- squawk-ignore require-concurrent-index-creation
CREATE INDEX trust_documents_request_id ON trust_documents (request_id);

-- --- who is on call -------------------------------------------------------------------------

CREATE TABLE trust_on_call_staff (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    pmc_id uuid NOT NULL REFERENCES trust_pmcs (pmc_id),
    staff_id uuid NOT NULL,
    on_call boolean NOT NULL,
    recorded_by uuid,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    FOREIGN KEY (pmc_id, staff_id) REFERENCES trust_staff (pmc_id, id),
    FOREIGN KEY (pmc_id, recorded_by) REFERENCES trust_staff (pmc_id, id)
);
CREATE INDEX trust_on_call_staff_staff ON trust_on_call_staff (pmc_id, staff_id, created_at);
CREATE INDEX trust_on_call_staff_recorded_by ON trust_on_call_staff (pmc_id, recorded_by);

CREATE TRIGGER trust_recorded_by BEFORE INSERT ON trust_on_call_staff
FOR EACH ROW EXECUTE FUNCTION trust_name_recorder();
CREATE TRIGGER trust_append_only
BEFORE UPDATE OR DELETE OR TRUNCATE ON trust_on_call_staff
FOR EACH STATEMENT EXECUTE FUNCTION trust_refuse_ledger_rewrite();
CREATE TRIGGER trust_audit AFTER INSERT OR UPDATE OR DELETE ON trust_on_call_staff
FOR EACH ROW EXECUTE FUNCTION trust_audit();

REVOKE ALL ON trust_on_call_staff FROM PUBLIC;
GRANT SELECT, INSERT ON trust_on_call_staff TO trust_app;
GRANT SELECT ON trust_on_call_staff TO trust_ai_agent;

-- --- the emergency email ----------------------------------------------------------------------

ALTER TABLE trust_emails ADD COLUMN request_id uuid;
-- Replaced at once by checks that allow one more kind; no guarantee is lost.
-- squawk-ignore ban-drop-constraint
ALTER TABLE trust_emails DROP CONSTRAINT trust_emails_kind_check;
ALTER TABLE trust_emails ADD CONSTRAINT trust_emails_kind_check CHECK (kind IN (
    'bill_approval', 'invite', 'password_reset', 'message', 'rent_reminder', 'late_notice',
    'owner_statement', 'work_order', 'adverse_action', 'emergency_request'
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
    AND (kind = 'emergency_request') = (request_id IS NOT NULL)
) NOT VALID;
-- squawk-ignore adding-foreign-key-constraint
ALTER TABLE trust_emails ADD CONSTRAINT trust_emails_pmc_id_request_id_fkey
FOREIGN KEY (pmc_id, request_id) REFERENCES trust_maintenance_requests (pmc_id, id) NOT VALID;
-- squawk-ignore require-concurrent-index-creation
CREATE UNIQUE INDEX trust_emails_one_emergency ON trust_emails (staff_id, request_id)
WHERE kind = 'emergency_request';

-- --- a visit's window -------------------------------------------------------------------------

ALTER TABLE trust_work_order_steps ADD COLUMN visit_window text;
ALTER TABLE trust_work_order_steps ADD CONSTRAINT trust_work_order_steps_visit_window_check
CHECK (
    visit_window IS NULL
    OR (step = 'scheduled' AND visit_window ~ '\S' AND length(visit_window) <= 60)
) NOT VALID;

-- migrate:down
DO $$
BEGIN
    RAISE EXCEPTION 'maintenance_more cannot be rolled back: emergencies, photos, who was on '
        'call and the emails written are records';
END;
$$;
