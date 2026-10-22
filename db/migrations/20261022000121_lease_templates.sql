-- migrate:up
-- A company's own lease, filled in (brought in scope by the founder on 2026-10-22): its lease
-- text kept as a template with blanks, such as {{tenant_names}} or {{monthly_rent}}, which the
-- PMS fills from a draft lease's terms and makes into the PDF the draft lease sends for signing
-- (src/pms/api/offers.py). The words are the company's, as the PDF it uploads otherwise is.
--
--   trust_notice_templates.used_for   what a template is for, from when it is made: a notice on a
--                                     lease (migration 0119) or a lease to sign.
--   trust_lease_offer_templates       the version a lease offer's document was filled from, once
--                                     per offer, naming who made it. Append-only and audited; the
--                                     AI role reads it (it reads neither offers nor their files).
--
-- A notice is made only from a notice template, an offer only from a lease template.

-- On a live database, give up rather than queue behind a long transaction while holding locks.
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '60s';

ALTER TABLE trust_notice_templates ADD COLUMN used_for text NOT NULL DEFAULT 'notice';
ALTER TABLE trust_notice_templates ADD CONSTRAINT trust_notice_templates_used_for_check
CHECK (used_for IN ('notice', 'lease')) NOT VALID;

CREATE TABLE trust_lease_offer_templates (
    offer_id uuid PRIMARY KEY,
    pmc_id uuid NOT NULL REFERENCES trust_pmcs (pmc_id),
    template_version_id uuid NOT NULL,
    recorded_by uuid,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    FOREIGN KEY (pmc_id, offer_id) REFERENCES trust_lease_offers (pmc_id, id),
    FOREIGN KEY (pmc_id, template_version_id) REFERENCES trust_notice_template_versions (pmc_id, id),
    FOREIGN KEY (pmc_id, recorded_by) REFERENCES trust_staff (pmc_id, id)
);
CREATE INDEX trust_lease_offer_templates_version
ON trust_lease_offer_templates (pmc_id, template_version_id);
CREATE INDEX trust_lease_offer_templates_recorded_by
ON trust_lease_offer_templates (pmc_id, recorded_by);

-- What a notice or an offer is made from is a template for that use.
CREATE FUNCTION trust_check_template_use() RETURNS trigger
LANGUAGE plpgsql
SET search_path = public, pg_temp
AS $$
DECLARE
    v_use text;
BEGIN
    SELECT t.used_for INTO v_use
    FROM trust_notice_template_versions AS v
    JOIN trust_notice_templates AS t ON t.id = v.template_id
    WHERE v.id = NEW.template_version_id;
    IF v_use <> TG_ARGV[0] THEN
        RAISE EXCEPTION 'trust: a % is made from a % template, not a % one',
            TG_ARGV[0], TG_ARGV[0], v_use
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER trust_template_use BEFORE INSERT ON trust_notices
FOR EACH ROW EXECUTE FUNCTION trust_check_template_use('notice');
CREATE TRIGGER trust_template_use BEFORE INSERT ON trust_lease_offer_templates
FOR EACH ROW EXECUTE FUNCTION trust_check_template_use('lease');
CREATE TRIGGER trust_recorded_by BEFORE INSERT ON trust_lease_offer_templates
FOR EACH ROW EXECUTE FUNCTION trust_name_recorder();
CREATE TRIGGER trust_append_only
BEFORE UPDATE OR DELETE OR TRUNCATE ON trust_lease_offer_templates
FOR EACH STATEMENT EXECUTE FUNCTION trust_refuse_ledger_rewrite();
CREATE TRIGGER trust_audit AFTER INSERT OR UPDATE OR DELETE ON trust_lease_offer_templates
FOR EACH ROW EXECUTE FUNCTION trust_audit();

REVOKE ALL ON trust_lease_offer_templates FROM PUBLIC;
REVOKE ALL ON FUNCTION trust_check_template_use() FROM PUBLIC;
GRANT SELECT, INSERT ON trust_lease_offer_templates TO trust_app;
GRANT SELECT ON trust_lease_offer_templates TO trust_ai_agent;


-- migrate:down
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '60s';

-- squawk-ignore ban-drop-table
DROP TABLE trust_lease_offer_templates;
DROP TRIGGER trust_template_use ON trust_notices;
-- squawk-ignore ban-drop-function
DROP FUNCTION trust_check_template_use();
-- squawk-ignore ban-drop-column
ALTER TABLE trust_notice_templates DROP COLUMN used_for;
