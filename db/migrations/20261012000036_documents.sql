-- migrate:up
-- Documents (founder, 2026-10-12: docs/adr/0003-staff-back-office.md, item 6): a vendor's W-9,
-- a bill's receipt, a lease's move-out photos and an owner's documents, as staff upload them.
--
--   trust_documents          what a document is: its kind and what it belongs to (a vendor, a
--                            bill, a lease or an owner), its title, its type, size and SHA-256,
--                            and who uploaded it
--   trust_document_files     its bytes, kept apart: they are never copied into the audit log
--                            (a W-9 carries a tax ID); the document's row, audited, says which
--                            file it was by its SHA-256
--   trust_document_removals  a document staff took away: no list shows it again, and no owner
--                            reads it; its record and bytes are kept, like every trust record
--
-- A document is a PDF, a JPEG or a PNG of at most 5 MB, and its bytes are what it says: their
-- first bytes are its type's, and their size and SHA-256 are its. Each document has its file at
-- commit. The PMS reads nothing out of a file: a W-9's tax ID stays in the file, and 1099s are
-- still not filed. trust_ai_agent never reads the files.

-- On a live database, give up rather than queue behind a long transaction while holding locks.
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '60s';

CREATE TABLE trust_documents (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    pmc_id uuid NOT NULL REFERENCES trust_pmcs (pmc_id),
    kind text NOT NULL CHECK (kind IN ('w9', 'receipt', 'move_out_photo', 'owner_document')),
    vendor_id uuid,
    bill_id uuid,
    lease_id uuid,
    owner_id uuid,
    title text NOT NULL CHECK (title ~ '\S' AND length(title) <= 200),
    content_type text NOT NULL CHECK (content_type IN ('application/pdf', 'image/jpeg', 'image/png')),
    size bigint NOT NULL CHECK (size BETWEEN 1 AND 5242880),
    sha256 bytea NOT NULL CHECK (length(sha256) = 32),
    -- The staff member who uploaded it (set by the trigger; NULL: the server's command line).
    uploaded_by uuid,
    created_at timestamptz NOT NULL DEFAULT now(),
    -- Each kind belongs to one thing, and only that.
    CHECK ((kind = 'w9') = (vendor_id IS NOT NULL)),
    CHECK ((kind = 'receipt') = (bill_id IS NOT NULL)),
    CHECK ((kind = 'move_out_photo') = (lease_id IS NOT NULL)),
    CHECK ((kind = 'owner_document') = (owner_id IS NOT NULL)),
    UNIQUE (pmc_id, id),
    FOREIGN KEY (pmc_id, vendor_id) REFERENCES trust_vendors (pmc_id, id),
    FOREIGN KEY (pmc_id, bill_id) REFERENCES trust_bills (pmc_id, id),
    FOREIGN KEY (pmc_id, lease_id) REFERENCES trust_leases (pmc_id, id),
    FOREIGN KEY (pmc_id, owner_id) REFERENCES trust_owners (pmc_id, id),
    FOREIGN KEY (pmc_id, uploaded_by) REFERENCES trust_staff (pmc_id, id)
);
CREATE INDEX trust_documents_pmc_id_kind ON trust_documents (pmc_id, kind);

CREATE TABLE trust_document_files (
    document_id uuid PRIMARY KEY,
    pmc_id uuid NOT NULL REFERENCES trust_pmcs (pmc_id),
    content bytea NOT NULL,
    FOREIGN KEY (pmc_id, document_id) REFERENCES trust_documents (pmc_id, id)
);

CREATE TABLE trust_document_removals (
    document_id uuid PRIMARY KEY,
    pmc_id uuid NOT NULL REFERENCES trust_pmcs (pmc_id),
    removed_by uuid,
    created_at timestamptz NOT NULL DEFAULT now(),
    FOREIGN KEY (pmc_id, document_id) REFERENCES trust_documents (pmc_id, id),
    FOREIGN KEY (pmc_id, removed_by) REFERENCES trust_staff (pmc_id, id)
);

CREATE TRIGGER trust_append_only
BEFORE UPDATE OR DELETE OR TRUNCATE ON trust_documents
FOR EACH STATEMENT EXECUTE FUNCTION trust_refuse_ledger_rewrite();
CREATE TRIGGER trust_append_only
BEFORE UPDATE OR DELETE OR TRUNCATE ON trust_document_files
FOR EACH STATEMENT EXECUTE FUNCTION trust_refuse_ledger_rewrite();
CREATE TRIGGER trust_append_only
BEFORE UPDATE OR DELETE OR TRUNCATE ON trust_document_removals
FOR EACH STATEMENT EXECUTE FUNCTION trust_refuse_ledger_rewrite();

-- The file's bytes are never copied into the log; the document's row says which they were.
CREATE TRIGGER trust_audit AFTER INSERT OR UPDATE OR DELETE ON trust_documents
FOR EACH ROW EXECUTE FUNCTION trust_audit();
CREATE TRIGGER trust_audit AFTER INSERT OR UPDATE OR DELETE ON trust_document_removals
FOR EACH ROW EXECUTE FUNCTION trust_audit();

-- Who uploaded or removed it: the staff member the app names, never one the row names itself.
CREATE FUNCTION trust_name_document_uploader() RETURNS trigger
LANGUAGE plpgsql
SET search_path = public, pg_temp
AS $$
BEGIN
    NEW.uploaded_by := nullif(current_setting('trust.staff_id', true), '')::uuid;
    RETURN NEW;
END;
$$;

CREATE FUNCTION trust_name_document_remover() RETURNS trigger
LANGUAGE plpgsql
SET search_path = public, pg_temp
AS $$
BEGIN
    NEW.removed_by := nullif(current_setting('trust.staff_id', true), '')::uuid;
    RETURN NEW;
END;
$$;

CREATE TRIGGER trust_document_staff
BEFORE INSERT ON trust_documents
FOR EACH ROW EXECUTE FUNCTION trust_name_document_uploader();
CREATE TRIGGER trust_document_staff
BEFORE INSERT ON trust_document_removals
FOR EACH ROW EXECUTE FUNCTION trust_name_document_remover();

-- A file is its document's: the size and SHA-256 it says, starting with its type's bytes.
CREATE FUNCTION trust_check_document_file() RETURNS trigger
LANGUAGE plpgsql
SET search_path = public, pg_temp
AS $$
DECLARE
    v_document trust_documents;
    v_typed boolean;
BEGIN
    SELECT d.* INTO v_document FROM trust_documents AS d
    WHERE d.id = NEW.document_id AND d.pmc_id = NEW.pmc_id;
    IF NOT FOUND THEN
        -- No such document of this PMC: the foreign key refuses the file.
        RETURN NEW;
    END IF;
    IF length(NEW.content) <> v_document.size OR sha256(NEW.content) <> v_document.sha256 THEN
        RAISE EXCEPTION 'trust: the file of document % is not the % bytes it says',
            NEW.document_id, v_document.size
            USING ERRCODE = 'check_violation';
    END IF;
    -- The first bytes of a PDF ("%PDF-"), a PNG and a JPEG.
    v_typed := CASE v_document.content_type
        WHEN 'application/pdf' THEN substring(NEW.content FROM 1 FOR 5) = '\x255044462d'::bytea
        WHEN 'image/png' THEN substring(NEW.content FROM 1 FOR 8) = '\x89504e470d0a1a0a'::bytea
        ELSE substring(NEW.content FROM 1 FOR 3) = '\xffd8ff'::bytea
    END;
    IF NOT v_typed THEN
        RAISE EXCEPTION 'trust: the file of document % is not a %', NEW.document_id,
            v_document.content_type
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER trust_document_file
BEFORE INSERT ON trust_document_files
FOR EACH ROW EXECUTE FUNCTION trust_check_document_file();

-- At commit, a document has its file.
CREATE FUNCTION trust_check_document_has_file() RETURNS trigger
LANGUAGE plpgsql
SET search_path = public, pg_temp
AS $$
BEGIN
    IF NOT EXISTS (SELECT f.document_id FROM trust_document_files AS f
                   WHERE f.document_id = NEW.id) THEN
        RAISE EXCEPTION 'trust: document % has no file', NEW.id
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NULL;
END;
$$;

CREATE CONSTRAINT TRIGGER trust_document_has_file
AFTER INSERT ON trust_documents
DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW EXECUTE FUNCTION trust_check_document_has_file();

REVOKE ALL ON trust_documents, trust_document_files, trust_document_removals FROM PUBLIC;
-- Staff upload and remove documents with plain inserts; the triggers keep them whole.
GRANT SELECT, INSERT ON trust_documents, trust_document_files, trust_document_removals
TO trust_app;
-- The AI may know a document exists; it never reads one.
GRANT SELECT ON trust_documents, trust_document_removals TO trust_ai_agent;

-- migrate:down
DO $$
BEGIN
    RAISE EXCEPTION 'documents cannot be rolled back: documents are records';
END;
$$;
