-- migrate:up
-- A link taken back (docs/edge-cases.md U-099). Staff who marked the wrong person's record "the
-- same person" (migration 0105) had no way back: whoever signed in as the first record read and
-- acted on the other's lease, ledger, receipts and files, and might pay or file under it.
--
--   trust_tenant_unlinks   a record's link to a person's first record taken back, once, naming
--                          who. Append-only. The record is its own person again: it signs in on
--                          its own and others' records may be linked to it, but it is never
--                          linked again itself (a record is linked once, trust_tenant_links).
--
-- trust_tenant_records, the link check and the login check read only the links not taken back.
-- The API stops the autopays of the record (set up by whoever signed in as the first) in the same
-- transaction (src/pms/api/directory.py). Money stays each record's own, as before.

SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '60s';

CREATE TABLE trust_tenant_unlinks (
    tenant_id uuid PRIMARY KEY REFERENCES trust_tenant_links (tenant_id),
    pmc_id uuid NOT NULL REFERENCES trust_pmcs (pmc_id),
    recorded_by uuid,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    FOREIGN KEY (pmc_id, tenant_id) REFERENCES trust_tenants (pmc_id, id),
    FOREIGN KEY (pmc_id, recorded_by) REFERENCES trust_staff (pmc_id, id)
);
CREATE INDEX trust_tenant_unlinks_recorded_by ON trust_tenant_unlinks (pmc_id, recorded_by);

REVOKE ALL ON trust_tenant_unlinks FROM PUBLIC;
GRANT SELECT, INSERT ON trust_tenant_unlinks TO trust_app;
GRANT SELECT ON trust_tenant_unlinks TO trust_ai_agent;

CREATE TRIGGER trust_append_only BEFORE DELETE OR UPDATE OR TRUNCATE ON trust_tenant_unlinks
FOR EACH STATEMENT EXECUTE FUNCTION trust_refuse_ledger_rewrite();
CREATE TRIGGER trust_audit AFTER INSERT OR DELETE OR UPDATE ON trust_tenant_unlinks
FOR EACH ROW EXECUTE FUNCTION trust_audit();
CREATE TRIGGER trust_recorded_by BEFORE INSERT ON trust_tenant_unlinks
FOR EACH ROW EXECUTE FUNCTION trust_name_recorder();

-- A link taken back takes both records' locks, in order, as a link does, so a link, a login and a
-- taking back that touch the same records wait for each other.
CREATE FUNCTION trust_check_tenant_unlink() RETURNS trigger
LANGUAGE plpgsql
SET search_path = public, pg_temp
AS $$
BEGIN
    PERFORM 1 FROM trust_tenants AS t
    WHERE t.pmc_id = NEW.pmc_id
      AND t.id IN (SELECT k.tenant_id FROM trust_tenant_links AS k WHERE k.tenant_id = NEW.tenant_id
                   UNION ALL
                   SELECT k.same_as FROM trust_tenant_links AS k WHERE k.tenant_id = NEW.tenant_id)
    ORDER BY t.id
    FOR NO KEY UPDATE;
    RETURN NEW;
END;
$$;
REVOKE ALL ON FUNCTION trust_check_tenant_unlink() FROM PUBLIC;

CREATE TRIGGER trust_check_tenant_unlink BEFORE INSERT ON trust_tenant_unlinks
FOR EACH ROW EXECUTE FUNCTION trust_check_tenant_unlink();

-- The records of the person p_tenant_id belongs to, p_tenant_id among them, by the links not
-- taken back; nothing for a tenant not of the PMC.
CREATE OR REPLACE FUNCTION trust_tenant_records(p_pmc_id uuid, p_tenant_id uuid)
RETURNS SETOF uuid
LANGUAGE sql
STABLE
SET search_path = public, pg_temp
AS $$
    WITH standing AS (
        SELECT k.tenant_id, k.same_as FROM trust_tenant_links AS k
        WHERE k.pmc_id = p_pmc_id
          AND NOT EXISTS (SELECT 1 FROM trust_tenant_unlinks AS u WHERE u.tenant_id = k.tenant_id)
    ),
    person AS (
        SELECT coalesce(
            (SELECT k.same_as FROM standing AS k WHERE k.tenant_id = p_tenant_id), p_tenant_id
        ) AS id
    )
    SELECT t.id FROM trust_tenants AS t, person
    WHERE t.pmc_id = p_pmc_id AND t.id = person.id
    UNION
    SELECT k.tenant_id FROM standing AS k, person
    WHERE k.same_as = person.id
$$;

-- A link joins a record that signs in to nothing, was never linked, and is nobody's first (by
-- the links not taken back) to a person's first record, at a property where the person has no
-- record yet. Both records' locks are taken, in order, so two links (or a link and a login)
-- touching the same records wait for each other.
CREATE OR REPLACE FUNCTION trust_check_tenant_link() RETURNS trigger
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

    IF EXISTS (SELECT 1 FROM trust_tenant_unlinks AS u WHERE u.tenant_id = NEW.tenant_id) THEN
        RAISE EXCEPTION 'trust: % was linked to a record and its link was taken back: a record is '
            'linked once; link the other record to them instead', v_joining.display_name
            USING ERRCODE = 'check_violation';
    END IF;
    IF EXISTS (SELECT 1 FROM trust_tenant_links AS k WHERE k.tenant_id = NEW.tenant_id) THEN
        RAISE EXCEPTION 'trust: % is linked to their first record already', v_joining.display_name
            USING ERRCODE = 'check_violation';
    END IF;
    IF EXISTS (
        SELECT 1 FROM trust_tenant_links AS k
        WHERE k.tenant_id = NEW.same_as
          AND NOT EXISTS (SELECT 1 FROM trust_tenant_unlinks AS u WHERE u.tenant_id = k.tenant_id)
    ) THEN
        RAISE EXCEPTION 'trust: % is linked to another record already: link to their first',
            v_first.display_name
            USING ERRCODE = 'check_violation';
    END IF;
    IF EXISTS (
        SELECT 1 FROM trust_tenant_links AS k
        WHERE k.same_as = NEW.tenant_id
          AND NOT EXISTS (SELECT 1 FROM trust_tenant_unlinks AS u WHERE u.tenant_id = k.tenant_id)
    ) THEN
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

-- A tenant's portal login is their first record's: a record linked to another (and not taken
-- back) signs in through the person's first.
CREATE OR REPLACE FUNCTION trust_check_tenant_login() RETURNS trigger
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
    WHERE k.pmc_id = NEW.pmc_id AND k.tenant_id = NEW.tenant_id
      AND NOT EXISTS (SELECT 1 FROM trust_tenant_unlinks AS u WHERE u.tenant_id = k.tenant_id);
    IF FOUND THEN
        RAISE EXCEPTION 'trust: this tenant signs in as % at their first property: invite them there',
            v_first
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$;

-- migrate:down
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '60s';

CREATE OR REPLACE FUNCTION trust_tenant_records(p_pmc_id uuid, p_tenant_id uuid)
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

CREATE OR REPLACE FUNCTION trust_check_tenant_link() RETURNS trigger
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

CREATE OR REPLACE FUNCTION trust_check_tenant_login() RETURNS trigger
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

-- squawk-ignore ban-drop-table
DROP TABLE trust_tenant_unlinks;
-- squawk-ignore ban-drop-function
DROP FUNCTION trust_check_tenant_unlink();
