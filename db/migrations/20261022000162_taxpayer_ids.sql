-- migrate:up
-- 1099 forms (the founder, 2026-10-23: filing and printing 1099s, taxpayer IDs kept encrypted).
-- The PMS prints each recipient's copy of the year's 1099-NEC (vendors marked for one, paid the
-- year's threshold) and 1099-MISC (owners' rents, box 1), and writes the list a company uploads
-- to file them; it reads what was paid and moves no money.
--
-- trust_taxpayer_ids: the name and taxpayer ID on a vendor's or an owner's W-9, or the company's
-- own as the payer (neither named), with its kind (EIN, SSN, ITIN). The number is sealed by the
-- app (AES-GCM under the server's PMS_FIELD_KEY, which never reaches the database;
-- src/pms/sealed.py) beside its last four digits, which staff see. An owner the company files no
-- 1099 for (a corporation, say) is marked exempt; a vendor's profile says so for vendors
-- (migration 0073). Append-only and audited, the latest row for a person holding, naming who
-- recorded it. The AI role never reads it.

-- On a live database, give up rather than queue behind a long transaction while holding locks.
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '60s';

CREATE TABLE trust_taxpayer_ids (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    pmc_id uuid NOT NULL REFERENCES trust_pmcs (pmc_id),
    vendor_id uuid,
    owner_id uuid,
    tin_type text NOT NULL CHECK (tin_type IN ('EIN', 'SSN', 'ITIN')),
    -- The nine digits, sealed by the app: version, nonce, ciphertext.
    tin_sealed bytea NOT NULL CHECK (octet_length(tin_sealed) BETWEEN 30 AND 200),
    tin_last4 text NOT NULL CHECK (tin_last4 ~ '^[0-9]{4}$'),
    -- The name the W-9 gives (line 1), as the IRS matches it with the number.
    legal_name text NOT NULL
        CHECK (legal_name = btrim(legal_name) AND char_length(legal_name) BETWEEN 1 AND 80),
    exempt boolean NOT NULL DEFAULT false,
    recorded_by uuid,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    FOREIGN KEY (pmc_id, vendor_id) REFERENCES trust_vendors (pmc_id, id),
    FOREIGN KEY (pmc_id, owner_id) REFERENCES trust_owners (pmc_id, id),
    FOREIGN KEY (pmc_id, recorded_by) REFERENCES trust_staff (pmc_id, id),
    -- A vendor's, an owner's, or neither: the company's own.
    CHECK (num_nonnulls(vendor_id, owner_id) <= 1),
    CHECK (NOT exempt OR owner_id IS NOT NULL),
    -- An SSN or an ITIN is a person's; the company's own is an EIN.
    CHECK (vendor_id IS NOT NULL OR owner_id IS NOT NULL OR tin_type = 'EIN')
);
CREATE INDEX trust_taxpayer_ids_vendor ON trust_taxpayer_ids (pmc_id, vendor_id, created_at);
CREATE INDEX trust_taxpayer_ids_owner ON trust_taxpayer_ids (pmc_id, owner_id, created_at);
CREATE INDEX trust_taxpayer_ids_company ON trust_taxpayer_ids (pmc_id, created_at)
WHERE vendor_id IS NULL AND owner_id IS NULL;
CREATE INDEX trust_taxpayer_ids_recorded_by ON trust_taxpayer_ids (pmc_id, recorded_by);

CREATE TRIGGER trust_recorded_by BEFORE INSERT ON trust_taxpayer_ids
FOR EACH ROW EXECUTE FUNCTION trust_name_recorder();
CREATE TRIGGER trust_append_only
BEFORE UPDATE OR DELETE OR TRUNCATE ON trust_taxpayer_ids
FOR EACH STATEMENT EXECUTE FUNCTION trust_refuse_ledger_rewrite();
CREATE TRIGGER trust_audit AFTER INSERT OR UPDATE OR DELETE ON trust_taxpayer_ids
FOR EACH ROW EXECUTE FUNCTION trust_audit();

REVOKE ALL ON trust_taxpayer_ids FROM PUBLIC;
GRANT SELECT, INSERT ON trust_taxpayer_ids TO trust_app;


-- migrate:down
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '60s';

-- squawk-ignore ban-drop-table
DROP TABLE trust_taxpayer_ids;
