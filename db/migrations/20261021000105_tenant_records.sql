-- migrate:up
-- A tenant renting at two properties at once (the founder's review q009).
--
-- A tenant is a record at one property: a lease's tenants are its property's, and the deposits
-- and prepaid rent a record holds are its own there. Someone who rents at another property while
-- still renting at the first gets a record there too, linked to their first:
--
--   trust_tenant_links     a record and the first record of the same person, once per record,
--                          naming who linked them. Append-only. The first record is linked to
--                          nothing, and the person has at most one record at a property when
--                          one is linked.
--   trust_tenant_records   the records of the person a record belongs to: their first and every
--                          record linked to it.
--
-- Money stays each record's own, so each lease's deposit and each property's owner are as they
-- were, and no rule of the ledger changes. The person signs in once: only a record linked to
-- nothing takes a portal login, and the portal shows the leases of every record of theirs
-- (src/pms/api/tenant.py). Their emails go to each record's contact email, else to the person's
-- login's.

-- On a live database, give up rather than queue behind a long transaction while holding locks.
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '60s';

CREATE TABLE trust_tenant_links (
    tenant_id uuid PRIMARY KEY,
    pmc_id uuid NOT NULL REFERENCES trust_pmcs (pmc_id),
    -- The person's first record.
    same_as uuid NOT NULL,
    recorded_by uuid,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    CHECK (tenant_id <> same_as),
    FOREIGN KEY (pmc_id, tenant_id) REFERENCES trust_tenants (pmc_id, id),
    FOREIGN KEY (pmc_id, same_as) REFERENCES trust_tenants (pmc_id, id),
    FOREIGN KEY (pmc_id, recorded_by) REFERENCES trust_staff (pmc_id, id)
);
CREATE INDEX trust_tenant_links_same_as ON trust_tenant_links (pmc_id, same_as);
CREATE INDEX trust_tenant_links_recorded_by ON trust_tenant_links (pmc_id, recorded_by);

-- The records of the person p_tenant_id belongs to, p_tenant_id among them; nothing for a
-- tenant not of the PMC.
CREATE FUNCTION trust_tenant_records(p_pmc_id uuid, p_tenant_id uuid)
RETURNS SETOF uuid
LANGUAGE sql
STABLE
SET search_path = public, pg_temp
AS $$
    WITH person AS (
        SELECT coalesce(
            (SELECT k.same_as FROM trust_tenant_links AS k
             WHERE k.pmc_id = p_pmc_id AND k.tenant_id = p_tenant_id),
            p_tenant_id
        ) AS id
    )
    SELECT t.id FROM trust_tenants AS t, person
    WHERE t.pmc_id = p_pmc_id AND t.id = person.id
    UNION
    SELECT k.tenant_id FROM trust_tenant_links AS k, person
    WHERE k.pmc_id = p_pmc_id AND k.same_as = person.id
$$;

-- A link joins a record that signs in to nothing to a person's first record, at a property
-- where the person has no record yet. Both records' locks are taken, in order, so two links (or
-- a link and a login) touching the same records wait for each other.
CREATE FUNCTION trust_check_tenant_link() RETURNS trigger
LANGUAGE plpgsql
SET search_path = public, pg_temp
AS $$
DECLARE
    v_joining record;
    v_first record;
    v_where text;
BEGIN
    PERFORM 1 FROM trust_tenants AS t
    WHERE t.pmc_id = NEW.pmc_id AND t.id IN (NEW.tenant_id, NEW.same_as)
    ORDER BY t.id
    FOR NO KEY UPDATE;
    SELECT t.display_name, t.property_id INTO v_joining
    FROM trust_tenants AS t WHERE t.pmc_id = NEW.pmc_id AND t.id = NEW.tenant_id;
    SELECT t.display_name, t.property_id INTO v_first
    FROM trust_tenants AS t WHERE t.pmc_id = NEW.pmc_id AND t.id = NEW.same_as;

    IF EXISTS (SELECT 1 FROM trust_tenant_links AS k WHERE k.tenant_id = NEW.tenant_id) THEN
        RAISE EXCEPTION 'trust: % is linked to their first record already', v_joining.display_name
            USING ERRCODE = 'check_violation';
    END IF;
    IF EXISTS (SELECT 1 FROM trust_tenant_links AS k WHERE k.tenant_id = NEW.same_as) THEN
        RAISE EXCEPTION 'trust: % is linked to another record already: link to their first',
            v_first.display_name
            USING ERRCODE = 'check_violation';
    END IF;
    IF EXISTS (SELECT 1 FROM trust_tenant_links AS k WHERE k.same_as = NEW.tenant_id) THEN
        RAISE EXCEPTION 'trust: % has records at other properties linked to them: link % to them instead',
            v_joining.display_name, v_first.display_name
            USING ERRCODE = 'check_violation';
    END IF;
    IF EXISTS (
        SELECT 1 FROM trust_logins AS g
        WHERE g.pmc_id = NEW.pmc_id AND g.tenant_id = NEW.tenant_id AND g.disabled_at IS NULL
    ) THEN
        RAISE EXCEPTION 'trust: % signs in to the portal: link % to them instead',
            v_joining.display_name, v_first.display_name
            USING ERRCODE = 'check_violation';
    END IF;
    SELECT p.display_name INTO v_where
    FROM trust_tenants AS t
    JOIN trust_properties AS p ON p.id = t.property_id
    WHERE t.property_id = v_joining.property_id
      AND t.id IN (SELECT trust_tenant_records(NEW.pmc_id, NEW.same_as));
    IF FOUND THEN
        RAISE EXCEPTION 'trust: % is a tenant at % already: one record a property',
            v_first.display_name, v_where
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER trust_check_tenant_link BEFORE INSERT ON trust_tenant_links
FOR EACH ROW EXECUTE FUNCTION trust_check_tenant_link();

-- A tenant's portal login is their first record's: a record linked to another signs in through
-- the person's first.
CREATE FUNCTION trust_check_tenant_login() RETURNS trigger
LANGUAGE plpgsql
SET search_path = public, pg_temp
AS $$
DECLARE
    v_first text;
BEGIN
    PERFORM 1 FROM trust_tenants AS t
    WHERE t.pmc_id = NEW.pmc_id AND t.id = NEW.tenant_id
    FOR SHARE;
    SELECT f.display_name INTO v_first
    FROM trust_tenant_links AS k
    JOIN trust_tenants AS f ON f.id = k.same_as
    WHERE k.pmc_id = NEW.pmc_id AND k.tenant_id = NEW.tenant_id;
    IF FOUND THEN
        RAISE EXCEPTION 'trust: this tenant signs in as % at their first property: invite them there',
            v_first
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER trust_check_tenant_login BEFORE INSERT ON trust_logins
FOR EACH ROW WHEN (NEW.tenant_id IS NOT NULL)
EXECUTE FUNCTION trust_check_tenant_login();

CREATE TRIGGER trust_recorded_by BEFORE INSERT ON trust_tenant_links
FOR EACH ROW EXECUTE FUNCTION trust_name_recorder();
CREATE TRIGGER trust_append_only
BEFORE UPDATE OR DELETE OR TRUNCATE ON trust_tenant_links
FOR EACH STATEMENT EXECUTE FUNCTION trust_refuse_ledger_rewrite();
CREATE TRIGGER trust_audit AFTER INSERT OR UPDATE OR DELETE ON trust_tenant_links
FOR EACH ROW EXECUTE FUNCTION trust_audit();

REVOKE ALL ON trust_tenant_links FROM PUBLIC;
GRANT SELECT, INSERT ON trust_tenant_links TO trust_app;
GRANT SELECT ON trust_tenant_links TO trust_ai_agent;
REVOKE ALL ON FUNCTION trust_tenant_records(uuid, uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION trust_tenant_records(uuid, uuid) TO trust_app;
REVOKE ALL ON FUNCTION trust_check_tenant_link() FROM PUBLIC;
REVOKE ALL ON FUNCTION trust_check_tenant_login() FROM PUBLIC;

-- migrate:down
DO $$
BEGIN
    RAISE EXCEPTION 'tenant_records cannot be rolled back: which records are the same person is a record';
END;
$$;
