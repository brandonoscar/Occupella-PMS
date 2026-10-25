-- migrate:up
-- A person's contact details found by the person (the enterprise test, 2026-10-25).
--
-- Every page that shows a tenant's, owner's or vendor's contact details reads the latest row of
-- trust_contacts for that person by their id alone (WHERE tenant_id = ..., ORDER BY created_at
-- DESC LIMIT 1): the Tenants list, Search, a person's page, the emails the PMS writes. Its
-- indexes (migration 0064) lead with pmc_id, which those reads don't name, so each read scanned
-- the table: on a company of 20,000 units, 1.3 ms a person, and Search and the Tenants list took
-- 35 s. These indexes find a person's rows by their id, newest last.

SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '60s';

-- squawk-ignore require-concurrent-index-creation
CREATE INDEX trust_contacts_by_tenant ON trust_contacts (tenant_id, created_at)
WHERE tenant_id IS NOT NULL;
-- squawk-ignore require-concurrent-index-creation
CREATE INDEX trust_contacts_by_owner ON trust_contacts (owner_id, created_at)
WHERE owner_id IS NOT NULL;
-- squawk-ignore require-concurrent-index-creation
CREATE INDEX trust_contacts_by_vendor ON trust_contacts (vendor_id, created_at)
WHERE vendor_id IS NOT NULL;

-- migrate:down
-- squawk-ignore require-concurrent-index-deletion
DROP INDEX trust_contacts_by_vendor;
-- squawk-ignore require-concurrent-index-deletion
DROP INDEX trust_contacts_by_owner;
-- squawk-ignore require-concurrent-index-deletion
DROP INDEX trust_contacts_by_tenant;
