-- migrate:up
-- Files on any record (the founder's punch list, ADR 0006, step 12): a document may be about a
-- property, a unit or a tenant too, and a lease or a vendor may hold documents besides their
-- move-out photos and W-9s. Each kind stays about its one kind of record of the PMC:
--   property_document: a property     unit_document: a unit        tenant_document: a tenant
--   lease_document: a lease (beside move_out_photo)
--   vendor_document: a vendor (beside w9)
-- The owner portal still serves an owner only their owner_document files. The file's bytes stay
-- apart, never audited and never read by the AI role, as before (migration 0036).

-- On a live database, give up rather than queue behind a long transaction while holding locks.
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '60s';

ALTER TABLE trust_documents ADD COLUMN property_id uuid;
ALTER TABLE trust_documents ADD COLUMN unit_id uuid;
ALTER TABLE trust_documents ADD COLUMN tenant_id uuid;

-- The kinds, and the record each one is about. Each check below replaces one that allowed less
-- (the old kinds keep their record); no guarantee is lost, and each is checked for every new row.
-- squawk-ignore ban-drop-constraint
ALTER TABLE trust_documents DROP CONSTRAINT trust_documents_kind_check;
ALTER TABLE trust_documents ADD CONSTRAINT trust_documents_kind_check CHECK (
    kind IN (
        'w9', 'receipt', 'move_out_photo', 'owner_document', 'work_order_photo',
        'property_document', 'unit_document', 'tenant_document', 'lease_document',
        'vendor_document'
    )
) NOT VALID;
-- squawk-ignore ban-drop-constraint
ALTER TABLE trust_documents DROP CONSTRAINT trust_documents_check;
ALTER TABLE trust_documents ADD CONSTRAINT trust_documents_vendor_check
CHECK ((kind IN ('w9', 'vendor_document')) = (vendor_id IS NOT NULL)) NOT VALID;
-- squawk-ignore ban-drop-constraint
ALTER TABLE trust_documents DROP CONSTRAINT trust_documents_check2;
ALTER TABLE trust_documents ADD CONSTRAINT trust_documents_lease_check
CHECK ((kind IN ('move_out_photo', 'lease_document')) = (lease_id IS NOT NULL)) NOT VALID;
ALTER TABLE trust_documents ADD CONSTRAINT trust_documents_property_check
CHECK ((kind = 'property_document') = (property_id IS NOT NULL)) NOT VALID;
ALTER TABLE trust_documents ADD CONSTRAINT trust_documents_unit_check
CHECK ((kind = 'unit_document') = (unit_id IS NOT NULL)) NOT VALID;
ALTER TABLE trust_documents ADD CONSTRAINT trust_documents_tenant_check
CHECK ((kind = 'tenant_document') = (tenant_id IS NOT NULL)) NOT VALID;

-- Each about a record of the document's own PMC.
-- squawk-ignore adding-foreign-key-constraint
ALTER TABLE trust_documents ADD CONSTRAINT trust_documents_pmc_id_property_id_fkey
FOREIGN KEY (pmc_id, property_id) REFERENCES trust_properties (pmc_id, id) NOT VALID;
-- squawk-ignore adding-foreign-key-constraint
ALTER TABLE trust_documents ADD CONSTRAINT trust_documents_pmc_id_unit_id_fkey
FOREIGN KEY (pmc_id, unit_id) REFERENCES trust_units (pmc_id, id) NOT VALID;
-- squawk-ignore adding-foreign-key-constraint
ALTER TABLE trust_documents ADD CONSTRAINT trust_documents_pmc_id_tenant_id_fkey
FOREIGN KEY (pmc_id, tenant_id) REFERENCES trust_tenants (pmc_id, id) NOT VALID;
-- squawk-ignore require-concurrent-index-creation
CREATE INDEX trust_documents_property_id ON trust_documents (property_id);
-- squawk-ignore require-concurrent-index-creation
CREATE INDEX trust_documents_unit_id ON trust_documents (unit_id);
-- squawk-ignore require-concurrent-index-creation
CREATE INDEX trust_documents_tenant_id ON trust_documents (tenant_id);

-- migrate:down
DO $$
BEGIN
    RAISE EXCEPTION 'document_attachments cannot be rolled back: documents are records';
END;
$$;
