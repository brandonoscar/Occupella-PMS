-- migrate:up
-- Contact details, and archiving tenants and owners (ADR 0005).
--
-- A tenant's, owner's or vendor's email, phone numbers and mailing address (`trust_contacts`):
-- append-only and audited, the latest row for a person holding, so a change is a new row and
-- the old one stays. Staff see and change them; the AI role never reads them (no grant, so the
-- audit log's row-level security hides their rows too, as migration 0043 does for logins).
-- Nothing here moves money, and the reports keep to names.
--
-- Archiving (migration 0063) reaches tenants and owners: a tenant only while on no lease in
-- effect or to come and holding no prepaid rent or deposit; an owner only while none of their
-- property accounts holds money or has a bill neither paid nor declined.

-- On a live database, give up rather than queue behind a long transaction while holding locks.
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '60s';

CREATE TABLE trust_contacts (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    pmc_id uuid NOT NULL REFERENCES trust_pmcs (pmc_id),
    tenant_id uuid,
    owner_id uuid,
    vendor_id uuid,
    email text CHECK (email ~ '^[^@[:space:]]+@[^@[:space:]]+[.][^@[:space:]]+$' AND char_length(email) <= 254),
    phone text CHECK (phone ~ '^[+]?[0-9 ().-]{7,30}$'),
    alternate_phone text CHECK (alternate_phone ~ '^[+]?[0-9 ().-]{7,30}$'),
    address_line_1 text CHECK (char_length(address_line_1) BETWEEN 1 AND 200),
    address_line_2 text CHECK (char_length(address_line_2) BETWEEN 1 AND 200),
    city text CHECK (char_length(city) BETWEEN 1 AND 100),
    state text CHECK (char_length(state) BETWEEN 1 AND 50),
    postal_code text CHECK (char_length(postal_code) BETWEEN 1 AND 20),
    recorded_by uuid,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    FOREIGN KEY (pmc_id, tenant_id) REFERENCES trust_tenants (pmc_id, id),
    FOREIGN KEY (pmc_id, owner_id) REFERENCES trust_owners (pmc_id, id),
    FOREIGN KEY (pmc_id, vendor_id) REFERENCES trust_vendors (pmc_id, id),
    FOREIGN KEY (pmc_id, recorded_by) REFERENCES trust_staff (pmc_id, id),
    -- Each row is one person's.
    CHECK (num_nonnulls(tenant_id, owner_id, vendor_id) = 1),
    -- A mailing address is whole (a second line is optional) or not there.
    CHECK (
        num_nonnulls(address_line_1, city, state, postal_code) IN (0, 4)
        AND (address_line_2 IS NULL OR address_line_1 IS NOT NULL)
    )
);
CREATE INDEX trust_contacts_tenant ON trust_contacts (pmc_id, tenant_id, created_at);
CREATE INDEX trust_contacts_owner ON trust_contacts (pmc_id, owner_id, created_at);
CREATE INDEX trust_contacts_vendor ON trust_contacts (pmc_id, vendor_id, created_at);
CREATE INDEX trust_contacts_recorded_by ON trust_contacts (pmc_id, recorded_by);

CREATE TRIGGER trust_recorded_by BEFORE INSERT ON trust_contacts
FOR EACH ROW EXECUTE FUNCTION trust_name_recorder();
CREATE TRIGGER trust_append_only
BEFORE UPDATE OR DELETE OR TRUNCATE ON trust_contacts
FOR EACH STATEMENT EXECUTE FUNCTION trust_refuse_ledger_rewrite();
CREATE TRIGGER trust_audit AFTER INSERT OR UPDATE OR DELETE ON trust_contacts
FOR EACH ROW EXECUTE FUNCTION trust_audit();

-- Staff's own: the AI role gets nothing.
REVOKE ALL ON trust_contacts FROM PUBLIC;
GRANT SELECT, INSERT ON trust_contacts TO trust_app;

-- Archiving a tenant or an owner. Every row so far is a property's or a unit's, so the new
-- columns are NULL in all of them and their keys check nothing; the table is small, so
-- validating the keys and the new check in the same step scans little.
ALTER TABLE trust_archivals ADD COLUMN tenant_id uuid;
ALTER TABLE trust_archivals ADD COLUMN owner_id uuid;
-- squawk-ignore adding-foreign-key-constraint, constraint-missing-not-valid
ALTER TABLE trust_archivals ADD CONSTRAINT trust_archivals_tenant_fkey
FOREIGN KEY (pmc_id, tenant_id) REFERENCES trust_tenants (pmc_id, id);
-- squawk-ignore adding-foreign-key-constraint, constraint-missing-not-valid
ALTER TABLE trust_archivals ADD CONSTRAINT trust_archivals_owner_fkey
FOREIGN KEY (pmc_id, owner_id) REFERENCES trust_owners (pmc_id, id);
-- squawk-ignore constraint-missing-not-valid
ALTER TABLE trust_archivals ADD CONSTRAINT trust_archivals_one_record
CHECK (num_nonnulls(property_id, unit_id, tenant_id, owner_id) = 1);
-- The one-record check above takes over from 0063's, which knew only properties and units.
-- squawk-ignore ban-drop-constraint
ALTER TABLE trust_archivals DROP CONSTRAINT trust_archivals_check;
-- trust_archivals is small (a row each time staff archive or bring back a record), so these
-- indexes build in moments; CONCURRENTLY can't run inside the migration's transaction.
-- squawk-ignore require-concurrent-index-creation
CREATE INDEX trust_archivals_tenant ON trust_archivals (pmc_id, tenant_id, created_at);
-- squawk-ignore require-concurrent-index-creation
CREATE INDEX trust_archivals_owner ON trust_archivals (pmc_id, owner_id, created_at);

-- Whether a property, unit, tenant or owner is archived now: its latest archival says so. The
-- two-argument form 0063 made gives way to this one, which a call with two arguments still
-- reaches.
-- squawk-ignore ban-drop-function
DROP FUNCTION trust_is_archived(uuid, uuid);
CREATE FUNCTION trust_is_archived(
    p_property_id uuid, p_unit_id uuid, p_tenant_id uuid DEFAULT NULL, p_owner_id uuid DEFAULT NULL
)
RETURNS boolean
LANGUAGE sql
STABLE
SET search_path = public, pg_temp
AS $$
    SELECT coalesce((
        SELECT a.archived FROM trust_archivals AS a
        WHERE (p_property_id IS NOT NULL AND a.property_id = p_property_id)
           OR (p_unit_id IS NOT NULL AND a.unit_id = p_unit_id)
           OR (p_tenant_id IS NOT NULL AND a.tenant_id = p_tenant_id)
           OR (p_owner_id IS NOT NULL AND a.owner_id = p_owner_id)
        ORDER BY a.created_at DESC, a.id DESC
        LIMIT 1
    ), false)
$$;

REVOKE ALL ON FUNCTION trust_is_archived(uuid, uuid, uuid, uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION trust_is_archived(uuid, uuid, uuid, uuid) TO trust_app;

-- 0063's checks, and a tenant's and an owner's. The day is UTC's today, as the reports count it.
CREATE OR REPLACE FUNCTION trust_check_archival() RETURNS trigger
LANGUAGE plpgsql
SET search_path = public, pg_temp
AS $$
DECLARE
    v_today date := (now() AT TIME ZONE 'UTC')::date;
    v_name text;
BEGIN
    IF trust_is_archived(NEW.property_id, NEW.unit_id, NEW.tenant_id, NEW.owner_id) = NEW.archived THEN
        RAISE EXCEPTION 'trust: it is % already', CASE WHEN NEW.archived THEN 'archived' ELSE 'not archived' END
            USING ERRCODE = 'check_violation';
    END IF;
    IF NOT NEW.archived THEN
        RETURN NEW;
    END IF;
    IF NEW.tenant_id IS NOT NULL THEN
        SELECT t.display_name INTO v_name FROM trust_tenants AS t WHERE t.id = NEW.tenant_id;
        IF EXISTS (SELECT 1 FROM trust_lease_tenants AS lt JOIN trust_leases AS l ON l.id = lt.lease_id
                   WHERE lt.tenant_id = NEW.tenant_id AND (l.ends_on IS NULL OR l.ends_on >= v_today))
        THEN
            RAISE EXCEPTION 'trust: % is on a lease in effect or to come; end it before archiving',
                v_name USING ERRCODE = 'check_violation';
        END IF;
        IF EXISTS (SELECT 1 FROM trust_ledger_accounts AS a
                   JOIN pgledger_accounts AS b ON b.id = a.ledger_account_id
                   WHERE a.tenant_id = NEW.tenant_id AND b.balance <> 0)
        THEN
            RAISE EXCEPTION 'trust: % has prepaid rent or a deposit held; return it before archiving',
                v_name USING ERRCODE = 'check_violation';
        END IF;
        RETURN NEW;
    END IF;
    IF NEW.unit_id IS NOT NULL THEN
        SELECT u.display_name INTO v_name FROM trust_units AS u WHERE u.id = NEW.unit_id;
        IF EXISTS (SELECT 1 FROM trust_leases AS l
                   WHERE l.unit_id = NEW.unit_id AND (l.ends_on IS NULL OR l.ends_on >= v_today))
        THEN
            RAISE EXCEPTION 'trust: % has a lease in effect or to come; end it before archiving',
                v_name USING ERRCODE = 'check_violation';
        END IF;
        RETURN NEW;
    END IF;
    IF NEW.property_id IS NOT NULL THEN
        SELECT p.display_name INTO v_name FROM trust_properties AS p WHERE p.id = NEW.property_id;
        IF EXISTS (SELECT 1 FROM trust_leases AS l JOIN trust_units AS u ON u.id = l.unit_id
                   WHERE u.property_id = NEW.property_id
                     AND (l.ends_on IS NULL OR l.ends_on >= v_today))
        THEN
            RAISE EXCEPTION 'trust: % has a lease in effect or to come; end it before archiving',
                v_name USING ERRCODE = 'check_violation';
        END IF;
    ELSE
        SELECT o.display_name INTO v_name FROM trust_owners AS o WHERE o.id = NEW.owner_id;
    END IF;
    -- A property's owners' accounts, or an owner's accounts: no money held, no bill left open.
    IF EXISTS (SELECT 1 FROM trust_ledger_accounts AS a
               JOIN pgledger_accounts AS b ON b.id = a.ledger_account_id
               WHERE a.kind = 'owner_property' AND b.balance <> 0
                 AND (a.property_id = NEW.property_id OR a.owner_id = NEW.owner_id))
    THEN
        RAISE EXCEPTION 'trust: % holds % money; draw it before archiving', v_name,
            CASE WHEN NEW.owner_id IS NULL THEN 'its owners''' ELSE 'their' END
            USING ERRCODE = 'check_violation';
    END IF;
    IF EXISTS (SELECT 1 FROM trust_bills AS bill
               JOIN trust_ledger_accounts AS a ON a.ledger_account_id = bill.ledger_account_id
               WHERE (a.property_id = NEW.property_id OR a.owner_id = NEW.owner_id)
                 AND NOT EXISTS (SELECT 1 FROM trust_bill_payments AS p
                                 WHERE p.bill_id = bill.id AND p.step = 'paid')
                 AND NOT EXISTS (SELECT 1 FROM trust_bill_declines AS d
                                 WHERE d.bill_id = bill.id))
    THEN
        RAISE EXCEPTION 'trust: % has a bill not paid; pay or decline it before archiving', v_name
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$;

-- migrate:down
DO $$
BEGIN
    RAISE EXCEPTION 'contacts cannot be rolled back: they are records';
END;
$$;
