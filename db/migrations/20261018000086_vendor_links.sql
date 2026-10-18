-- migrate:up
-- A vendor's private link (the founder's review, q069; docs/law.md): a vendor has no login; staff
-- give them a link that shows the work orders assigned to them and lets them schedule a visit or
-- say the work is done.
--
--   trust_vendor_links             a vendor's link: its token kept only as its SHA-256, naming
--                                  who made it. Append-only; a newer link of the vendor replaces
--                                  the older (the API revokes it in the same transaction).
--   trust_vendor_link_revocations  a link taken back, once, naming who did it.
--   trust_work_order_steps         a step a vendor recorded through their link names it
--                                  (vendor_link_id): only a schedule or a completion, of a work
--                                  order whose latest assignment is that vendor.
--
-- Like logins and invites, the AI role never reads them.

-- On a live database, give up rather than queue behind a long transaction while holding locks.
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '60s';

CREATE TABLE trust_vendor_links (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    pmc_id uuid NOT NULL REFERENCES trust_pmcs (pmc_id),
    vendor_id uuid NOT NULL,
    token_sha256 bytea NOT NULL UNIQUE CHECK (length(token_sha256) = 32),
    recorded_by uuid,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    UNIQUE (pmc_id, id),
    FOREIGN KEY (pmc_id, vendor_id) REFERENCES trust_vendors (pmc_id, id),
    FOREIGN KEY (pmc_id, recorded_by) REFERENCES trust_staff (pmc_id, id)
);
CREATE INDEX trust_vendor_links_vendor ON trust_vendor_links (pmc_id, vendor_id);
CREATE INDEX trust_vendor_links_recorded_by ON trust_vendor_links (pmc_id, recorded_by);

CREATE TRIGGER trust_recorded_by BEFORE INSERT ON trust_vendor_links
FOR EACH ROW EXECUTE FUNCTION trust_name_recorder();
CREATE TRIGGER trust_append_only
BEFORE UPDATE OR DELETE OR TRUNCATE ON trust_vendor_links
FOR EACH STATEMENT EXECUTE FUNCTION trust_refuse_ledger_rewrite();
CREATE TRIGGER trust_audit AFTER INSERT OR UPDATE OR DELETE ON trust_vendor_links
FOR EACH ROW EXECUTE FUNCTION trust_audit();

REVOKE ALL ON trust_vendor_links FROM PUBLIC;
GRANT SELECT, INSERT ON trust_vendor_links TO trust_app;

CREATE TABLE trust_vendor_link_revocations (
    link_id uuid PRIMARY KEY,
    pmc_id uuid NOT NULL,
    recorded_by uuid,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    FOREIGN KEY (pmc_id, link_id) REFERENCES trust_vendor_links (pmc_id, id),
    FOREIGN KEY (pmc_id, recorded_by) REFERENCES trust_staff (pmc_id, id)
);
CREATE INDEX trust_vendor_link_revocations_recorded_by ON trust_vendor_link_revocations (
    pmc_id, recorded_by
);

CREATE TRIGGER trust_recorded_by BEFORE INSERT ON trust_vendor_link_revocations
FOR EACH ROW EXECUTE FUNCTION trust_name_recorder();
CREATE TRIGGER trust_append_only
BEFORE UPDATE OR DELETE OR TRUNCATE ON trust_vendor_link_revocations
FOR EACH STATEMENT EXECUTE FUNCTION trust_refuse_ledger_rewrite();
CREATE TRIGGER trust_audit AFTER INSERT OR UPDATE OR DELETE ON trust_vendor_link_revocations
FOR EACH ROW EXECUTE FUNCTION trust_audit();

REVOKE ALL ON trust_vendor_link_revocations FROM PUBLIC;
GRANT SELECT, INSERT ON trust_vendor_link_revocations TO trust_app;

-- --- a step through the link ------------------------------------------------------------------

ALTER TABLE trust_work_order_steps ADD COLUMN vendor_link_id uuid;
-- squawk-ignore adding-foreign-key-constraint
ALTER TABLE trust_work_order_steps ADD CONSTRAINT trust_work_order_steps_pmc_id_vendor_link_fkey
FOREIGN KEY (pmc_id, vendor_link_id) REFERENCES trust_vendor_links (pmc_id, id) NOT VALID;
-- squawk-ignore require-concurrent-index-creation
CREATE INDEX trust_work_order_steps_vendor_link ON trust_work_order_steps (vendor_link_id);

-- Through a link only a schedule or a completion, by the vendor last assigned the work order,
-- while the link stands.
CREATE FUNCTION trust_check_vendor_link_step() RETURNS trigger
LANGUAGE plpgsql
SET search_path = public, pg_temp
AS $$
DECLARE
    v_link record;
    v_assigned uuid;
BEGIN
    IF NEW.step NOT IN ('scheduled', 'completed') THEN
        RAISE EXCEPTION 'trust: a vendor schedules a visit or says the work is done, nothing else'
            USING ERRCODE = 'check_violation';
    END IF;
    SELECT l.vendor_id,
           EXISTS (SELECT 1 FROM trust_vendor_link_revocations AS r WHERE r.link_id = l.id)
               AS revoked
    INTO v_link
    FROM trust_vendor_links AS l
    WHERE l.id = NEW.vendor_link_id;
    IF v_link.revoked THEN
        RAISE EXCEPTION 'trust: this link was taken back' USING ERRCODE = 'check_violation';
    END IF;
    SELECT s.vendor_id INTO v_assigned
    FROM trust_work_order_steps AS s
    WHERE s.work_order_id = NEW.work_order_id AND s.step = 'assigned'
    ORDER BY s.taken_at DESC, s.created_at DESC
    LIMIT 1;
    IF v_assigned IS DISTINCT FROM v_link.vendor_id THEN
        RAISE EXCEPTION 'trust: this work order is not assigned to the link''s vendor'
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$;
REVOKE ALL ON FUNCTION trust_check_vendor_link_step() FROM PUBLIC;

CREATE TRIGGER trust_vendor_link_step BEFORE INSERT ON trust_work_order_steps
FOR EACH ROW WHEN (NEW.vendor_link_id IS NOT NULL)
EXECUTE FUNCTION trust_check_vendor_link_step();

-- migrate:down
DO $$
BEGIN
    RAISE EXCEPTION 'vendor_links cannot be rolled back: links, revocations and the steps vendors '
        'recorded are records';
END;
$$;
