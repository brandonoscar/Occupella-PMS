-- migrate:up
-- Archiving a property or a unit (ADR 0005: edits, never deletes). Nothing in a PMC is deleted;
-- archiving hides a record from lists and keeps all its history. An archival is a row
-- (`trust_archivals`, append-only, audited): the latest for a record holds, so bringing one back
-- is a new row. A property or unit is archived only while nothing is open on it: no lease in
-- effect or to come on it, and for a property, no money held in its owners' accounts and no
-- bill entered against them still unpaid (not paid and not declined). Bringing one back is
-- always allowed. Nothing here moves money.

-- On a live database, give up rather than queue behind a long transaction while holding locks.
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '60s';

CREATE TABLE trust_archivals (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    pmc_id uuid NOT NULL REFERENCES trust_pmcs (pmc_id),
    property_id uuid,
    unit_id uuid,
    archived boolean NOT NULL,
    recorded_by uuid,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    FOREIGN KEY (pmc_id, property_id) REFERENCES trust_properties (pmc_id, id),
    FOREIGN KEY (pmc_id, unit_id) REFERENCES trust_units (pmc_id, id),
    FOREIGN KEY (pmc_id, recorded_by) REFERENCES trust_staff (pmc_id, id),
    -- Each archival is of exactly one record.
    CHECK (num_nonnulls(property_id, unit_id) = 1)
);
CREATE INDEX trust_archivals_property ON trust_archivals (pmc_id, property_id, created_at);
CREATE INDEX trust_archivals_unit ON trust_archivals (pmc_id, unit_id, created_at);
CREATE INDEX trust_archivals_recorded_by ON trust_archivals (pmc_id, recorded_by);

-- Whether a property or unit is archived now: its latest archival says so.
CREATE FUNCTION trust_is_archived(p_property_id uuid, p_unit_id uuid)
RETURNS boolean
LANGUAGE sql
STABLE
SET search_path = public, pg_temp
AS $$
    SELECT coalesce((
        SELECT a.archived FROM trust_archivals AS a
        WHERE (p_property_id IS NOT NULL AND a.property_id = p_property_id)
           OR (p_unit_id IS NOT NULL AND a.unit_id = p_unit_id)
        ORDER BY a.created_at DESC, a.id DESC
        LIMIT 1
    ), false)
$$;

REVOKE ALL ON FUNCTION trust_is_archived(uuid, uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION trust_is_archived(uuid, uuid) TO trust_app;

-- Refuses archiving a property or unit while something is open on it, and archiving or bringing
-- back what already is so. The day is UTC's today, as the reports count it.
CREATE FUNCTION trust_check_archival() RETURNS trigger
LANGUAGE plpgsql
SET search_path = public, pg_temp
AS $$
DECLARE
    v_today date := (now() AT TIME ZONE 'UTC')::date;
    v_name text;
BEGIN
    IF trust_is_archived(NEW.property_id, NEW.unit_id) = NEW.archived THEN
        RAISE EXCEPTION 'trust: it is % already', CASE WHEN NEW.archived THEN 'archived' ELSE 'not archived' END
            USING ERRCODE = 'check_violation';
    END IF;
    IF NOT NEW.archived THEN
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
    SELECT p.display_name INTO v_name FROM trust_properties AS p WHERE p.id = NEW.property_id;
    IF EXISTS (SELECT 1 FROM trust_leases AS l JOIN trust_units AS u ON u.id = l.unit_id
               WHERE u.property_id = NEW.property_id
                 AND (l.ends_on IS NULL OR l.ends_on >= v_today))
    THEN
        RAISE EXCEPTION 'trust: % has a lease in effect or to come; end it before archiving',
            v_name USING ERRCODE = 'check_violation';
    END IF;
    IF EXISTS (SELECT 1 FROM trust_ledger_accounts AS a
               JOIN pgledger_accounts AS b ON b.id = a.ledger_account_id
               WHERE a.property_id = NEW.property_id AND a.kind = 'owner_property'
                 AND b.balance <> 0)
    THEN
        RAISE EXCEPTION 'trust: % holds its owners'' money; draw it before archiving', v_name
            USING ERRCODE = 'check_violation';
    END IF;
    IF EXISTS (SELECT 1 FROM trust_bills AS bill
               JOIN trust_ledger_accounts AS a ON a.ledger_account_id = bill.ledger_account_id
               WHERE a.property_id = NEW.property_id
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

CREATE TRIGGER trust_archival BEFORE INSERT ON trust_archivals
FOR EACH ROW EXECUTE FUNCTION trust_check_archival();
CREATE TRIGGER trust_recorded_by BEFORE INSERT ON trust_archivals
FOR EACH ROW EXECUTE FUNCTION trust_name_recorder();
CREATE TRIGGER trust_append_only
BEFORE UPDATE OR DELETE OR TRUNCATE ON trust_archivals
FOR EACH STATEMENT EXECUTE FUNCTION trust_refuse_ledger_rewrite();
CREATE TRIGGER trust_audit AFTER INSERT OR UPDATE OR DELETE ON trust_archivals
FOR EACH ROW EXECUTE FUNCTION trust_audit();

REVOKE ALL ON trust_archivals FROM PUBLIC;
GRANT SELECT, INSERT ON trust_archivals TO trust_app;
GRANT SELECT ON trust_archivals TO trust_ai_agent;

-- migrate:down
DO $$
BEGIN
    RAISE EXCEPTION 'archivals cannot be rolled back: they are records';
END;
$$;
