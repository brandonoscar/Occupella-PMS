-- migrate:up
-- A listing's photos (the founder's review, q076): staff keep photos of a unit to show on the
-- company's public vacancy page while the unit is listed.
--
--   listing_photo   a document about a unit (trust_documents.unit_id, as a unit's documents are),
--                   a JPEG or PNG. The public page shows those not removed for each listed unit;
--                   removing one (trust_document_removals) takes it off. Like every document, its
--                   bytes stay in trust_document_files, which the AI role never reads.

-- On a live database, give up rather than queue behind a long transaction while holding locks.
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '60s';

-- Each is replaced at once by a check that allows one more kind; no guarantee is lost.
-- squawk-ignore ban-drop-constraint
ALTER TABLE trust_documents DROP CONSTRAINT trust_documents_kind_check;
ALTER TABLE trust_documents ADD CONSTRAINT trust_documents_kind_check CHECK (
    kind IN (
        'w9', 'receipt', 'move_out_photo', 'owner_document', 'work_order_photo',
        'property_document', 'unit_document', 'tenant_document', 'lease_document',
        'vendor_document', 'company_logo', 'request_photo', 'listing_photo'
    )
) NOT VALID;
-- squawk-ignore ban-drop-constraint
ALTER TABLE trust_documents DROP CONSTRAINT trust_documents_unit_check;
ALTER TABLE trust_documents ADD CONSTRAINT trust_documents_unit_check
CHECK ((kind IN ('unit_document', 'listing_photo')) = (unit_id IS NOT NULL)) NOT VALID;
ALTER TABLE trust_documents ADD CONSTRAINT trust_documents_listing_photo_check
CHECK (kind <> 'listing_photo' OR content_type IN ('image/png', 'image/jpeg')) NOT VALID;

-- migrate:down
DO $$
BEGIN
    RAISE EXCEPTION 'listing_photos cannot be rolled back: listing photos are documents';
END;
$$;
