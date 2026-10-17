-- migrate:up
-- The company itself (the founder's punch list, ADR 0006, step 12: "Company setup: company name,
-- address, logo, and default late fee / NSF rules"). Its name stays trust_pmcs.display_name,
-- which the app may already rename. The rest:
--
--   trust_company_profiles  the company's mailing address (whole or none) and the late fee and
--                           NSF fee terms a property added from then on starts with, from its
--                           first day (none: a new property has none, as before). Append-only,
--                           the latest row holding, naming who recorded it. The terms in force
--                           at a property stay its own (trust_late_fee_policies,
--                           trust_nsf_fee_policies): a default changes no property already there.
--                           The owner and tenant portals show the address, where a notice to the
--                           manager goes. The AI role never reads it (contact details).
--   company_logo            a new kind of document: the company's logo, a PNG or JPEG about no
--                           one record; the latest not removed is the logo, which the company
--                           setup page and the owner and tenant portals show. Its bytes sit
--                           apart, never audited and never read by the AI role, as every
--                           document's do (migration 0036).
--
-- Nothing here moves money.

-- On a live database, give up rather than queue behind a long transaction while holding locks.
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '60s';

CREATE TABLE trust_company_profiles (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    pmc_id uuid NOT NULL REFERENCES trust_pmcs (pmc_id),
    address_line_1 text CHECK (btrim(address_line_1) <> '' AND char_length(address_line_1) <= 200),
    address_line_2 text CHECK (btrim(address_line_2) <> '' AND char_length(address_line_2) <= 200),
    city text CHECK (btrim(city) <> '' AND char_length(city) <= 100),
    state text CHECK (btrim(state) <> '' AND char_length(state) <= 50),
    postal_code text CHECK (btrim(postal_code) <> '' AND char_length(postal_code) <= 20),
    -- A new property's late fee terms: as trust_late_fee_policies keeps them.
    -- squawk-ignore prefer-bigint-over-int
    late_grace_days integer CHECK (late_grace_days BETWEEN 0 AND 365),
    late_flat_fee numeric CHECK (late_flat_fee >= 0 AND late_flat_fee = round(late_flat_fee, 2)),
    late_percent numeric CHECK (late_percent BETWEEN 0 AND 100),
    late_maximum numeric CHECK (late_maximum >= 0 AND late_maximum = round(late_maximum, 2)),
    -- A new property's NSF fee: as trust_nsf_fee_policies keeps it.
    nsf_fee numeric CHECK (nsf_fee >= 0 AND nsf_fee = round(nsf_fee, 2)),
    recorded_by uuid,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    FOREIGN KEY (pmc_id, recorded_by) REFERENCES trust_staff (pmc_id, id),
    -- A mailing address is whole (a second line is optional) or not there.
    CHECK (
        num_nonnulls(address_line_1, city, state, postal_code) IN (0, 4)
        AND (address_line_2 IS NULL OR address_line_1 IS NOT NULL)
    ),
    -- Late fee terms are whole (a cap is optional) or not there.
    CHECK (
        num_nonnulls(late_grace_days, late_flat_fee, late_percent) IN (0, 3)
        AND (late_maximum IS NULL OR late_grace_days IS NOT NULL)
    )
);
CREATE INDEX trust_company_profiles_pmc ON trust_company_profiles (pmc_id, created_at);
CREATE INDEX trust_company_profiles_recorded_by ON trust_company_profiles (pmc_id, recorded_by);

CREATE TRIGGER trust_recorded_by BEFORE INSERT ON trust_company_profiles
FOR EACH ROW EXECUTE FUNCTION trust_name_recorder();
CREATE TRIGGER trust_append_only
BEFORE UPDATE OR DELETE OR TRUNCATE ON trust_company_profiles
FOR EACH STATEMENT EXECUTE FUNCTION trust_refuse_ledger_rewrite();
CREATE TRIGGER trust_audit AFTER INSERT OR UPDATE OR DELETE ON trust_company_profiles
FOR EACH ROW EXECUTE FUNCTION trust_audit();

-- Staff's own: the AI role gets nothing (an address is a contact detail).
REVOKE ALL ON trust_company_profiles FROM PUBLIC;
GRANT SELECT, INSERT ON trust_company_profiles TO trust_app;

-- The logo, a document about no one record: each other kind's check already holds its record's
-- column empty for it. The kind check below replaces 0074's, which allowed less.
-- squawk-ignore ban-drop-constraint
ALTER TABLE trust_documents DROP CONSTRAINT trust_documents_kind_check;
ALTER TABLE trust_documents ADD CONSTRAINT trust_documents_kind_check CHECK (
    kind IN (
        'w9', 'receipt', 'move_out_photo', 'owner_document', 'work_order_photo',
        'property_document', 'unit_document', 'tenant_document', 'lease_document',
        'vendor_document', 'company_logo'
    )
) NOT VALID;
ALTER TABLE trust_documents ADD CONSTRAINT trust_documents_logo_check
CHECK (kind <> 'company_logo' OR content_type IN ('image/png', 'image/jpeg')) NOT VALID;

-- migrate:down
DO $$
BEGIN
    RAISE EXCEPTION 'company_profiles cannot be rolled back: profiles are records';
END;
$$;
