-- migrate:up
-- A tenant's own contact details (the founder's review q121): a tenant changes their email,
-- phones and mailing address in the portal (`POST /api/tenant/contact`, src/pms/api/tenant.py).
-- The change is a new row of `trust_contacts`, as a staff member's is, marked as the person's own
-- (`by_person`) and naming no staff member; the latest row still holds. Staff see on the tenant's
-- page that the tenant changed them, and when. The AI role still reads none of it.

-- On a live database, give up rather than queue behind a long transaction while holding locks.
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '60s';

-- A constant default: Postgres 11 and later add it without rewriting the table.
ALTER TABLE trust_contacts ADD COLUMN by_person boolean NOT NULL DEFAULT false;
-- Every row so far is staff's (false), so the check holds; the table is small, so validating it
-- in the same step scans little.
-- squawk-ignore constraint-missing-not-valid
ALTER TABLE trust_contacts ADD CONSTRAINT trust_contacts_by_person
CHECK (NOT by_person OR (tenant_id IS NOT NULL AND recorded_by IS NULL));

-- migrate:down
DO $$
BEGIN
    RAISE EXCEPTION 'own_contact cannot be rolled back: which contact details a tenant gave '
        'themselves is a record';
END;
$$;
