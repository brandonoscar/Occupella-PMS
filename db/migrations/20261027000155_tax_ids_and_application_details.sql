-- migrate:up
-- The founder's design pass, phase 2 (ADR 0010, parts F and G): an owner's or vendor's tax ID,
-- and what an application collects, carried to the tenant and the lease.
--
--   trust_tax_ids                an owner's or vendor's tax ID (an EIN or an SSN), sealed by the
--                                app under the server's key (src/pms/sealed.py); only its last
--                                four are kept in the clear. Append-only, the latest row holding.
--   trust_tax_id_reveals         each time a staff member saw a whole tax ID (Reveal, or a 1099
--                                export), naming them; only an admin may.
--   trust_applicant_details      what an applicant says of themselves: phone, current address,
--                                previous landlord (name, phone, dates) and employer.
--   trust_application_occupants  others who would live there (names).
--   trust_application_pets       pets, each maybe an assistance animal.
--   trust_application_vehicles   vehicles.
--   trust_emergency_contacts     a tenant's or an applicant's emergency contact (a name and a
--                                phone), the latest holding. None existed.
--   trust_documents              an applicant's proof of income: the kind 'income_proof', about
--                                the applicant.
--   trust_tenant_applicants      the applicant a tenant was, written once when their
--                                application's offer became their lease: the application's
--                                details belong to the tenant from then on.
--
-- Nothing here asks about anything fair housing protects, nor a tenant's or applicant's Social
-- Security number or date of birth: the screening partner keeps those. The AI role reads none of
-- it (tests/test_privileges.py, AI_NEVER_READS).

-- On a live database, give up rather than queue behind a long transaction while holding locks.
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '60s';

-- --- tax IDs -----------------------------------------------------------------------------------

CREATE TABLE trust_tax_ids (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    pmc_id uuid NOT NULL REFERENCES trust_pmcs (pmc_id),
    owner_id uuid,
    vendor_id uuid,
    kind text NOT NULL CHECK (kind IN ('EIN', 'SSN')),
    -- The nine digits, sealed by the app (a version byte, a nonce and AES-GCM's ciphertext, the
    -- row's id bound in); only the last four are kept in the clear, to show.
    tax_id_sealed bytea NOT NULL CHECK (octet_length(tax_id_sealed) BETWEEN 30 AND 200),
    last4 text NOT NULL CHECK (last4 ~ '^[0-9]{4}$'),
    recorded_by uuid,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    UNIQUE (pmc_id, id),
    CHECK (num_nonnulls(owner_id, vendor_id) = 1),
    FOREIGN KEY (pmc_id, owner_id) REFERENCES trust_owners (pmc_id, id),
    FOREIGN KEY (pmc_id, vendor_id) REFERENCES trust_vendors (pmc_id, id),
    FOREIGN KEY (pmc_id, recorded_by) REFERENCES trust_staff (pmc_id, id)
);
CREATE INDEX trust_tax_ids_owner ON trust_tax_ids (pmc_id, owner_id, created_at);
CREATE INDEX trust_tax_ids_vendor ON trust_tax_ids (pmc_id, vendor_id, created_at);
CREATE INDEX trust_tax_ids_recorded_by ON trust_tax_ids (pmc_id, recorded_by);

CREATE TRIGGER trust_recorded_by BEFORE INSERT ON trust_tax_ids
FOR EACH ROW EXECUTE FUNCTION trust_name_recorder();
CREATE TRIGGER trust_append_only
BEFORE UPDATE OR DELETE OR TRUNCATE ON trust_tax_ids
FOR EACH STATEMENT EXECUTE FUNCTION trust_refuse_ledger_rewrite();
CREATE TRIGGER trust_audit AFTER INSERT OR UPDATE OR DELETE ON trust_tax_ids
FOR EACH ROW EXECUTE FUNCTION trust_audit();

REVOKE ALL ON trust_tax_ids FROM PUBLIC;
GRANT SELECT, INSERT ON trust_tax_ids TO trust_app;

CREATE TABLE trust_tax_id_reveals (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    pmc_id uuid NOT NULL REFERENCES trust_pmcs (pmc_id),
    tax_id uuid NOT NULL,
    -- Shown on screen (an admin's Reveal), or written into a 1099 export.
    purpose text NOT NULL CHECK (purpose IN ('reveal', '1099 export')),
    -- Who saw it: the staff member the request names (the command line names nobody, and sees none).
    recorded_by uuid NOT NULL,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    FOREIGN KEY (pmc_id, tax_id) REFERENCES trust_tax_ids (pmc_id, id),
    FOREIGN KEY (pmc_id, recorded_by) REFERENCES trust_staff (pmc_id, id)
);
CREATE INDEX trust_tax_id_reveals_tax_id ON trust_tax_id_reveals (pmc_id, tax_id, created_at);
CREATE INDEX trust_tax_id_reveals_recorded_by ON trust_tax_id_reveals (pmc_id, recorded_by);

-- Only an admin sees a whole tax ID.
CREATE FUNCTION trust_check_tax_id_reveal() RETURNS trigger
LANGUAGE plpgsql
SET search_path = public, pg_temp
AS $$
BEGIN
    PERFORM trust_require_role(NEW.pmc_id, NEW.recorded_by, 'admin', 'see a whole tax ID');
    RETURN NEW;
END;
$$;
REVOKE ALL ON FUNCTION trust_check_tax_id_reveal() FROM PUBLIC;

-- Named after trust_recorded_by, so it fires once the row names who saw it.
CREATE TRIGGER trust_recorded_by BEFORE INSERT ON trust_tax_id_reveals
FOR EACH ROW EXECUTE FUNCTION trust_name_recorder();
CREATE TRIGGER trust_reveal_by_admin BEFORE INSERT ON trust_tax_id_reveals
FOR EACH ROW EXECUTE FUNCTION trust_check_tax_id_reveal();
CREATE TRIGGER trust_append_only
BEFORE UPDATE OR DELETE OR TRUNCATE ON trust_tax_id_reveals
FOR EACH STATEMENT EXECUTE FUNCTION trust_refuse_ledger_rewrite();
CREATE TRIGGER trust_audit AFTER INSERT OR UPDATE OR DELETE ON trust_tax_id_reveals
FOR EACH ROW EXECUTE FUNCTION trust_audit();

REVOKE ALL ON trust_tax_id_reveals FROM PUBLIC;
GRANT SELECT, INSERT ON trust_tax_id_reveals TO trust_app;

-- --- what an application collects --------------------------------------------------------------

-- A small table (people who applied); its key on both columns lets their details stay in their
-- company.
-- squawk-ignore disallowed-unique-constraint, constraint-missing-not-valid
ALTER TABLE trust_applicants ADD CONSTRAINT trust_applicants_pmc_id_id_key UNIQUE (pmc_id, id);

CREATE TABLE trust_applicant_details (
    applicant_id uuid PRIMARY KEY,
    pmc_id uuid NOT NULL REFERENCES trust_pmcs (pmc_id),
    phone text NOT NULL CHECK (phone ~ '^[+]?[0-9 ().-]{7,30}$'),
    -- Where they live now.
    address_line_1 text NOT NULL CHECK (char_length(address_line_1) BETWEEN 1 AND 200),
    address_line_2 text CHECK (char_length(address_line_2) BETWEEN 1 AND 200),
    city text NOT NULL CHECK (char_length(city) BETWEEN 1 AND 100),
    state text NOT NULL CHECK (char_length(state) BETWEEN 1 AND 50),
    postal_code text NOT NULL CHECK (char_length(postal_code) BETWEEN 1 AND 20),
    -- Their previous landlord, if they had one: a name, maybe a phone, and the days they rented.
    previous_landlord text CHECK (previous_landlord ~ '\S' AND length(previous_landlord) <= 200),
    previous_landlord_phone text CHECK (previous_landlord_phone ~ '^[+]?[0-9 ().-]{7,30}$'),
    previous_from date,
    previous_to date,
    employer text CHECK (employer ~ '\S' AND length(employer) <= 200),
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    CHECK (previous_landlord IS NOT NULL OR num_nulls(previous_landlord_phone, previous_from,
                                                      previous_to) = 3),
    CHECK (previous_from IS NULL OR previous_to IS NULL OR previous_from <= previous_to),
    FOREIGN KEY (pmc_id, applicant_id) REFERENCES trust_applicants (pmc_id, id)
);

CREATE TABLE trust_application_occupants (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    pmc_id uuid NOT NULL REFERENCES trust_pmcs (pmc_id),
    application_id uuid NOT NULL,
    display_name text NOT NULL CHECK (display_name ~ '\S' AND length(display_name) <= 200),
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    FOREIGN KEY (pmc_id, application_id) REFERENCES trust_applications (pmc_id, id)
);
CREATE INDEX trust_application_occupants_application
ON trust_application_occupants (pmc_id, application_id);

-- As a lease's pets (migration 0080): an assistance animal is never charged pet rent or a fee.
CREATE TABLE trust_application_pets (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    pmc_id uuid NOT NULL REFERENCES trust_pmcs (pmc_id),
    application_id uuid NOT NULL,
    display_name text NOT NULL CHECK (display_name ~ '\S' AND length(display_name) <= 100),
    kind text NOT NULL CHECK (kind ~ '\S' AND length(kind) <= 50),
    breed text CHECK (breed ~ '\S' AND length(breed) <= 100),
    weight_lbs numeric CHECK (weight_lbs > 0 AND weight_lbs <= 500 AND weight_lbs = round(weight_lbs, 1)),
    assistance_animal boolean NOT NULL DEFAULT false,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    FOREIGN KEY (pmc_id, application_id) REFERENCES trust_applications (pmc_id, id)
);
CREATE INDEX trust_application_pets_application ON trust_application_pets (pmc_id, application_id);

CREATE TABLE trust_application_vehicles (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    pmc_id uuid NOT NULL REFERENCES trust_pmcs (pmc_id),
    application_id uuid NOT NULL,
    make text NOT NULL CHECK (make ~ '\S' AND length(make) <= 50),
    model text CHECK (model ~ '\S' AND length(model) <= 50),
    color text CHECK (color ~ '\S' AND length(color) <= 30),
    plate text NOT NULL CHECK (plate ~ '\S' AND length(plate) <= 15),
    plate_state text CHECK (plate_state ~ '\S' AND length(plate_state) <= 30),
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    FOREIGN KEY (pmc_id, application_id) REFERENCES trust_applications (pmc_id, id)
);
CREATE INDEX trust_application_vehicles_application
ON trust_application_vehicles (pmc_id, application_id);

CREATE TABLE trust_emergency_contacts (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    pmc_id uuid NOT NULL REFERENCES trust_pmcs (pmc_id),
    tenant_id uuid,
    applicant_id uuid,
    display_name text NOT NULL CHECK (display_name ~ '\S' AND length(display_name) <= 200),
    phone text NOT NULL CHECK (phone ~ '^[+]?[0-9 ().-]{7,30}$'),
    recorded_by uuid,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    CHECK (num_nonnulls(tenant_id, applicant_id) = 1),
    FOREIGN KEY (pmc_id, tenant_id) REFERENCES trust_tenants (pmc_id, id),
    FOREIGN KEY (pmc_id, applicant_id) REFERENCES trust_applicants (pmc_id, id),
    FOREIGN KEY (pmc_id, recorded_by) REFERENCES trust_staff (pmc_id, id)
);
CREATE INDEX trust_emergency_contacts_tenant
ON trust_emergency_contacts (pmc_id, tenant_id, created_at);
CREATE INDEX trust_emergency_contacts_applicant ON trust_emergency_contacts (pmc_id, applicant_id);
CREATE INDEX trust_emergency_contacts_recorded_by ON trust_emergency_contacts (pmc_id, recorded_by);

CREATE TRIGGER trust_recorded_by BEFORE INSERT ON trust_emergency_contacts
FOR EACH ROW EXECUTE FUNCTION trust_name_recorder();

-- An applicant's proof of income, a document about them.
ALTER TABLE trust_documents ADD COLUMN applicant_id uuid;
-- Replaced at once by a check that allows one more kind; no guarantee is lost.
-- squawk-ignore ban-drop-constraint
ALTER TABLE trust_documents DROP CONSTRAINT trust_documents_kind_check;
ALTER TABLE trust_documents ADD CONSTRAINT trust_documents_kind_check CHECK (
    kind IN (
        'w9', 'receipt', 'move_out_photo', 'owner_document', 'work_order_photo',
        'property_document', 'unit_document', 'tenant_document', 'lease_document',
        'vendor_document', 'company_logo', 'request_photo', 'listing_photo',
        'payment_attachment', 'income_proof'
    )
) NOT VALID;
ALTER TABLE trust_documents ADD CONSTRAINT trust_documents_applicant_check
CHECK ((kind = 'income_proof') = (applicant_id IS NOT NULL)) NOT VALID;
-- squawk-ignore adding-foreign-key-constraint
ALTER TABLE trust_documents ADD CONSTRAINT trust_documents_applicant_id_fkey
FOREIGN KEY (pmc_id, applicant_id) REFERENCES trust_applicants (pmc_id, id) NOT VALID;
-- squawk-ignore require-concurrent-index-creation
CREATE INDEX trust_documents_applicant_id ON trust_documents (applicant_id);

-- --- carried to the tenant ---------------------------------------------------------------------

CREATE TABLE trust_tenant_applicants (
    tenant_id uuid PRIMARY KEY,
    pmc_id uuid NOT NULL REFERENCES trust_pmcs (pmc_id),
    applicant_id uuid NOT NULL UNIQUE,
    lease_id uuid NOT NULL,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    FOREIGN KEY (pmc_id, tenant_id) REFERENCES trust_tenants (pmc_id, id),
    FOREIGN KEY (pmc_id, applicant_id) REFERENCES trust_applicants (pmc_id, id),
    FOREIGN KEY (pmc_id, lease_id) REFERENCES trust_leases (pmc_id, id)
);
CREATE INDEX trust_tenant_applicants_lease ON trust_tenant_applicants (pmc_id, lease_id);

-- A tenant was the applicant only when the lease is the one their application's offer made, and
-- the tenant is on it.
CREATE FUNCTION trust_check_tenant_applicant() RETURNS trigger
LANGUAGE plpgsql
SET search_path = public, pg_temp
AS $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM trust_applicants AS a
        JOIN trust_lease_offers AS o ON o.application_id = a.application_id
        JOIN trust_lease_offer_completions AS c ON c.offer_id = o.id
        JOIN trust_lease_tenants AS t ON t.lease_id = c.lease_id
        WHERE a.id = NEW.applicant_id AND c.lease_id = NEW.lease_id
          AND t.tenant_id = NEW.tenant_id
    ) THEN
        RAISE EXCEPTION 'trust: tenant % is not on the lease applicant % applied for',
            NEW.tenant_id, NEW.applicant_id
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$;
REVOKE ALL ON FUNCTION trust_check_tenant_applicant() FROM PUBLIC;
CREATE TRIGGER trust_check_tenant_applicant BEFORE INSERT ON trust_tenant_applicants
FOR EACH ROW EXECUTE FUNCTION trust_check_tenant_applicant();

-- Everything above is written once and kept: corrections are new rows.
DO $$
DECLARE
    v_table text;
BEGIN
    FOREACH v_table IN ARRAY ARRAY[
        'trust_applicant_details', 'trust_application_occupants', 'trust_application_pets',
        'trust_application_vehicles', 'trust_emergency_contacts', 'trust_tenant_applicants'
    ] LOOP
        EXECUTE format(
            'CREATE TRIGGER trust_append_only BEFORE UPDATE OR DELETE OR TRUNCATE ON %I'
            ' FOR EACH STATEMENT EXECUTE FUNCTION trust_refuse_ledger_rewrite()', v_table);
        EXECUTE format(
            'CREATE TRIGGER trust_audit AFTER INSERT OR UPDATE OR DELETE ON %I'
            ' FOR EACH ROW EXECUTE FUNCTION trust_audit()', v_table);
        EXECUTE format('REVOKE ALL ON %I FROM PUBLIC', v_table);
        EXECUTE format('GRANT SELECT, INSERT ON %I TO trust_app', v_table);
    END LOOP;
END;
$$;

-- migrate:down
DO $$
BEGIN
    RAISE EXCEPTION 'tax_ids_and_application_details cannot be rolled back: tax IDs, reveals and '
        'what applicants gave are records';
END;
$$;
