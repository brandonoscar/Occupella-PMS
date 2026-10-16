-- migrate:up
-- Notes (the founder's punch list, ADR 0006: "Detail pages use tabs. Summary | Ledger | Files |
-- Notes"): what staff write down about a property, unit, lease, tenant, owner or vendor of the
-- PMC. A note is a row (`trust_notes`, append-only, audited, naming who wrote it); a correction
-- is a new note. Notes are about people and their homes, so the AI role never reads them, nor
-- their rows in the audit log. Nothing here moves money.

-- On a live database, give up rather than queue behind a long transaction while holding locks.
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '60s';

CREATE TABLE trust_notes (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    pmc_id uuid NOT NULL REFERENCES trust_pmcs (pmc_id),
    property_id uuid,
    unit_id uuid,
    lease_id uuid,
    tenant_id uuid,
    owner_id uuid,
    vendor_id uuid,
    body text NOT NULL CHECK (btrim(body) <> '' AND char_length(body) <= 5000),
    recorded_by uuid,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    FOREIGN KEY (pmc_id, property_id) REFERENCES trust_properties (pmc_id, id),
    FOREIGN KEY (pmc_id, unit_id) REFERENCES trust_units (pmc_id, id),
    FOREIGN KEY (pmc_id, lease_id) REFERENCES trust_leases (pmc_id, id),
    FOREIGN KEY (pmc_id, tenant_id) REFERENCES trust_tenants (pmc_id, id),
    FOREIGN KEY (pmc_id, owner_id) REFERENCES trust_owners (pmc_id, id),
    FOREIGN KEY (pmc_id, vendor_id) REFERENCES trust_vendors (pmc_id, id),
    FOREIGN KEY (pmc_id, recorded_by) REFERENCES trust_staff (pmc_id, id),
    -- Each note is about exactly one record.
    CHECK (num_nonnulls(property_id, unit_id, lease_id, tenant_id, owner_id, vendor_id) = 1)
);
CREATE INDEX trust_notes_property ON trust_notes (pmc_id, property_id, created_at);
CREATE INDEX trust_notes_unit ON trust_notes (pmc_id, unit_id, created_at);
CREATE INDEX trust_notes_lease ON trust_notes (pmc_id, lease_id, created_at);
CREATE INDEX trust_notes_tenant ON trust_notes (pmc_id, tenant_id, created_at);
CREATE INDEX trust_notes_owner ON trust_notes (pmc_id, owner_id, created_at);
CREATE INDEX trust_notes_vendor ON trust_notes (pmc_id, vendor_id, created_at);
CREATE INDEX trust_notes_recorded_by ON trust_notes (pmc_id, recorded_by);

CREATE TRIGGER trust_recorded_by BEFORE INSERT ON trust_notes
FOR EACH ROW EXECUTE FUNCTION trust_name_recorder();
CREATE TRIGGER trust_append_only
BEFORE UPDATE OR DELETE OR TRUNCATE ON trust_notes
FOR EACH STATEMENT EXECUTE FUNCTION trust_refuse_ledger_rewrite();
CREATE TRIGGER trust_audit AFTER INSERT OR UPDATE OR DELETE ON trust_notes
FOR EACH ROW EXECUTE FUNCTION trust_audit();

REVOKE ALL ON trust_notes FROM PUBLIC;
GRANT SELECT, INSERT ON trust_notes TO trust_app;

-- migrate:down
DO $$
BEGIN
    RAISE EXCEPTION 'notes cannot be rolled back: they are records';
END;
$$;
